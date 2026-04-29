"""Tests for the secret-redaction helpers used by stats and tee."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from contextclipper.engine.redact import REDACTED, redact_command, redact_text  # type: ignore[import-not-found]


class TestRedactCommand:
    def test_token_flag_redacted(self) -> None:
        out = redact_command("curl --token=abcdef12345 https://x")
        assert "abcdef12345" not in out
        assert REDACTED in out

    def test_password_flag_redacted(self) -> None:
        out = redact_command("mysql --password supersecret123 -h db")
        assert "supersecret123" not in out

    def test_dash_p_flag(self) -> None:
        # The plain `-p value` form is too generic to redact safely, but `--password=...`
        # and `--auth=...` must be covered.
        out = redact_command("svc --auth=topsecret")
        assert "topsecret" not in out

    def test_no_match_left_alone(self) -> None:
        out = redact_command("ls -la /tmp")
        assert out == "ls -la /tmp"


class TestRedactText:
    def test_env_var_secret(self) -> None:
        out = redact_text("API_TOKEN=abc123xyz\nUSER=alice\n")
        assert "abc123xyz" not in out
        assert "alice" in out

    def test_authorization_header(self) -> None:
        out = redact_text("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
        assert "eyJhbGciOiJIUzI1NiJ9.payload.sig" not in out
        assert REDACTED in out

    def test_aws_access_key(self) -> None:
        out = redact_text("Using AKIAIOSFODNN7EXAMPLE for s3")
        assert "AKIAIOSFODNN7EXAMPLE" not in out

    def test_github_pat(self) -> None:
        out = redact_text("token=ghp_abcdefghijklmnopqrstuvwx12345678")
        assert "ghp_abcdefghijklmnopqrstuvwx12345678" not in out

    def test_json_secret_field(self) -> None:
        out = redact_text('{"token": "live-secret-xyz", "user": "bob"}')
        assert "live-secret-xyz" not in out
        assert "bob" in out

    def test_empty(self) -> None:
        assert redact_text("") == ""
