"""Tests for the Semantic Context Budget Manager (SCBM)."""
from __future__ import annotations

import pytest

from contextclipper.engine.scbm import (
    Segment,
    ScoredLine,
    score_lines,
    select_budget,
    semantic_compress,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lines(*texts: str) -> list[str]:
    return list(texts)


# ---------------------------------------------------------------------------
# score_lines
# ---------------------------------------------------------------------------


class TestScoreLines:
    def test_empty_returns_empty(self) -> None:
        assert score_lines([], "ls", 0) == []

    def test_error_line_scores_highest(self) -> None:
        lines = ["some body line", "ERROR: something went wrong", "another body line"]
        scored = score_lines(lines, "pytest", 1)
        error_scored = [s for s in scored if s.segment == Segment.ERROR]
        assert len(error_scored) == 1
        assert error_scored[0].score >= 0.90

    def test_error_score_boosted_on_failure(self) -> None:
        lines = ["Error: oops"]
        score_success = score_lines(lines, "cmd", 0)[0].score
        score_failure = score_lines(lines, "cmd", 1)[0].score
        assert score_failure > score_success

    def test_stack_frame_classified(self) -> None:
        lines = [
            "Traceback (most recent call last):",
            "  File \"app.py\", line 42, in main",
            "  File \"lib.py\", line 7, in helper",
        ]
        scored = score_lines(lines, "python", 1)
        stack_frames = [s for s in scored if s.segment == Segment.STACK]
        # Line 1 and 2 are stack frames (line 0 is positional header/error)
        assert len(stack_frames) >= 1

    def test_stack_frame_score_decays_with_depth(self) -> None:
        # Pad with body lines so stack frames don't land in the first/last 3
        # (which would get a higher positional score overriding the stack score).
        prefix = ["INFO: starting" for _ in range(5)]
        stack_lines = [f"  File \"f.py\", line {i}" for i in range(8)]
        suffix = ["INFO: done" for _ in range(5)]
        all_lines = prefix + stack_lines + suffix
        scored = score_lines(all_lines, "python", 1)
        # Collect only the scored entries that correspond to stack frames
        stack_scores = [s.score for s in scored if s.segment == Segment.STACK]
        # Scores should be non-increasing (allowing small floating-point slack)
        for i in range(len(stack_scores) - 1):
            assert stack_scores[i] >= stack_scores[i + 1] - 0.01

    def test_footer_classified(self) -> None:
        lines = ["Some output", "10 passed, 2 failed in 0.5s"]
        scored = score_lines(lines, "pytest", 1)
        footer = [s for s in scored if s.segment == Segment.FOOTER]
        assert len(footer) == 1
        assert footer[0].score >= 0.75

    def test_first_three_lines_are_headers(self) -> None:
        lines = ["line 0", "line 1", "line 2", "line 3 body", "line 4 body"]
        scored = score_lines(lines, "cmd", 0)
        for s in scored[:3]:
            assert s.segment in (Segment.HEADER, Segment.ERROR, Segment.FOOTER)

    def test_last_three_lines_are_headers(self) -> None:
        lines = [f"body {i}" for i in range(10)]
        scored = score_lines(lines, "cmd", 0)
        for s in scored[-3:]:
            # Should have elevated score
            assert s.score >= 0.65

    def test_body_line_has_low_score(self) -> None:
        lines = ["This is just some verbose informational log line"]
        # Pad so it's not in the first/last 3
        padded = ["header"] * 3 + lines + ["tail"] * 3
        scored = score_lines(padded, "cmd", 0)
        body = scored[3]
        assert body.segment == Segment.BODY
        assert body.score < 0.30

    def test_scores_capped_at_one(self) -> None:
        lines = ["FATAL ERROR: critical failure"] * 5
        scored = score_lines(lines, "cmd", 1)
        for s in scored:
            assert s.score <= 1.0

    def test_index_matches_position(self) -> None:
        lines = ["a", "b", "c"]
        scored = score_lines(lines, "cmd", 0)
        for i, s in enumerate(scored):
            assert s.index == i
            assert s.content == lines[i]


# ---------------------------------------------------------------------------
# select_budget
# ---------------------------------------------------------------------------


class TestSelectBudget:
    def _make_scored(self, lines: list[str], scores: list[float]) -> list[ScoredLine]:
        assert len(lines) == len(scores)
        return [
            ScoredLine(i, ln, sc, Segment.BODY)
            for i, (ln, sc) in enumerate(zip(lines, scores))
        ]

    def test_all_fit_returns_all(self) -> None:
        lines = ["a", "b", "c"]
        scored = self._make_scored(lines, [0.5, 0.5, 0.5])
        result = select_budget(scored, max_tokens=10_000)
        assert result == lines

    def test_high_score_kept_over_low(self) -> None:
        lines = ["important error line", "verbose body line"]
        scored = self._make_scored(lines, [0.9, 0.1])
        # Budget: ~6 tokens (24 chars). "important error line" is 20 chars (fits);
        # "verbose body line" is 17 chars — total would be 39 chars > 24 → only one fits.
        result = select_budget(scored, max_tokens=6)
        assert "important error line" in result
        assert "verbose body line" not in result

    def test_omission_notice_inserted(self) -> None:
        lines = [f"line {i}" for i in range(20)]
        scores = [0.9 if i == 5 else 0.1 for i in range(20)]
        scored = self._make_scored(lines, scores)
        result = select_budget(scored, max_tokens=5)
        notice_lines = [ln for ln in result if "omitted by budget" in ln]
        assert len(notice_lines) >= 1

    def test_budget_never_exceeded(self) -> None:
        import random
        random.seed(42)
        lines = [f"line content {i} " * 5 for i in range(100)]
        scores = [random.random() for _ in range(100)]
        scored = self._make_scored(lines, scores)
        for max_tokens in [10, 50, 100, 500]:
            result = select_budget(scored, max_tokens)
            # Count chars of non-notice lines
            total = sum(len(ln) + 1 for ln in result if "omitted by budget" not in ln)
            assert total <= max_tokens * 4 + 50  # small tolerance for notice lines

    def test_original_order_preserved(self) -> None:
        lines = [f"line {i}" for i in range(10)]
        # All same score — order should be preserved
        scored = self._make_scored(lines, [0.5] * 10)
        result = select_budget(scored, max_tokens=10_000)
        content = [ln for ln in result if "omitted" not in ln]
        assert content == lines

    def test_empty_scored_returns_empty(self) -> None:
        assert select_budget([], max_tokens=100) == []


# ---------------------------------------------------------------------------
# semantic_compress (integration)
# ---------------------------------------------------------------------------


class TestSemanticCompress:
    def test_no_truncation_when_under_budget(self) -> None:
        lines = ["a", "b", "c"]
        result = semantic_compress(lines, "ls", 0, max_tokens=10_000)
        assert result == lines

    def test_empty_input(self) -> None:
        assert semantic_compress([], "ls", 0, max_tokens=100) == []

    def test_zero_budget_returns_input(self) -> None:
        lines = ["a", "b", "c"]
        # Edge case: max_tokens=0 should return input unchanged
        result = semantic_compress(lines, "ls", 0, max_tokens=0)
        assert result == lines

    def test_errors_preserved_over_body(self) -> None:
        body = [f"verbose info line {i}" for i in range(50)]
        error = ["ERROR: database connection refused"]
        lines = body[:25] + error + body[25:]
        # Tight budget: ~2 lines
        result = semantic_compress(lines, "app", exit_code=1, max_tokens=20)
        assert any("ERROR" in ln for ln in result)

    def test_stack_trace_preserved(self) -> None:
        body = [f"INFO: processing item {i}" for i in range(30)]
        stack = [
            "Traceback (most recent call last):",
            '  File "main.py", line 10, in run',
            "  ValueError: invalid value",
        ]
        lines = body + stack
        result = semantic_compress(lines, "python", exit_code=1, max_tokens=30)
        assert any("Traceback" in ln or "ValueError" in ln for ln in result)

    def test_footer_preserved(self) -> None:
        body = [f"running test {i}..." for i in range(40)]
        footer = ["5 passed, 2 failed in 1.23s"]
        lines = body + footer
        result = semantic_compress(lines, "pytest", exit_code=1, max_tokens=20)
        assert any("passed" in ln or "failed" in ln for ln in result)

    def test_result_has_omission_notices_when_truncated(self) -> None:
        lines = [f"body line {i}" * 3 for i in range(100)]
        result = semantic_compress(lines, "cmd", 0, max_tokens=50)
        assert any("omitted by budget" in ln for ln in result)

    @pytest.mark.parametrize("max_tokens", [5, 20, 100, 500])
    def test_fuzz_random_lines_no_crash(self, max_tokens: int) -> None:
        import random
        random.seed(max_tokens)
        lines = [
            "".join(random.choices("abcdefghijklmnopqrstuvwxyz ABCDEF\n", k=50))
            for _ in range(200)
        ]
        # Must not raise
        result = semantic_compress(lines, "randcmd", exit_code=random.randint(0, 1), max_tokens=max_tokens)
        assert isinstance(result, list)

    def test_compress_output_integration(self) -> None:
        """Verify that the filter engine uses SCBM when max_tokens is set."""
        from contextclipper.engine.filters import compress_output

        body = [f"verbose info line {i}" for i in range(100)]
        error_line = "ERROR: something critical happened"
        raw = "\n".join(body[:50] + [error_line] + body[50:])

        result = compress_output("myapp", raw, exit_code=1, max_tokens=30)
        assert any("ERROR" in ln for ln in result.compressed.splitlines())
