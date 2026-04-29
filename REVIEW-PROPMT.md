# Prompt for Sonnet (Antigravity)

You are an expert software architect and AI systems engineer. Your task is to perform a deep, holistic analysis of the **`ctxclp`** tool — an AI context optimization utility.

## Context to analyze

Use the following three references to understand the current state of the `ctxclp` tool:

1.  `@contextScopeItemMention` (first instance)
2.  `@contextScopeItemMention` (second instance)
3.  `@contextScopeItemMention` (third instance)

*Interpret these as three distinct sources or views of the project: codebase, documentation, runtime behavior, user feedback, or performance metrics — whichever is most relevant based on the actual content they contain.*

## Analysis objectives

### 1. Requirement coverage audit
Determine whether the `ctxclp` tool fully satisfies all stated and implied requirements for an AI context optimization tool. Explicitly list any missing or partially met requirements.

### 2. Performance evaluation
- Measure or reason about processing speed (tokens/sec, compression ratio)
- Memory footprint for large context windows (100k+ tokens)
- Latency overhead for real-time or near-real-time optimization
- Parallelization and batching capabilities

### 3. Security assessment
- Data privacy during context processing (local vs. remote processing)
- Handling of sensitive information (PII, secrets, proprietary code)
- Input sanitization and injection risks
- Secure defaults and configuration hardening

### 4. Reliability analysis
- Error handling and graceful degradation
- Idempotency and consistency of output
- Crash recovery and state persistence
- Test coverage and fuzzing results (inferred or actual)

## Deliverable improvements

Propose **actionable enhancements** to make `ctxclp` the **market leader in AI context optimization**. Focus on:

- **Novel compression techniques** (semantic, structural, or learned)
- **Integration with LLM APIs** (prefill optimization, KV cache reuse)
- **Developer experience** (CLI ergonomics, plugins for Cursor/Continue/OpenWebUI)
- **Observability** (telemetry, cost estimation, context Quality-of-Service metrics)
- **Differentiators** (multi-modal support, agentic context pruning, version-aware context)

## Output format

Return your response as a structured **Markdown document** with the following sections:

```markdown
# ctxclp — Strategic Analysis & Roadmap to Market Leadership

## 1. Current State Summary
[Brief synthesis from the three @contextScopeItemMention sources]

## 2. Requirements Coverage
| Requirement | Status (Met/Partial/Missing) | Evidence | Gap Action |
|-------------|------------------------------|----------|-------------|

## 3. Performance, Security & Reliability Gaps
- **Performance**: [findings + benchmarks targets]
- **Security**: [findings + hardening steps]
- **Reliability**: [findings + SLO recommendations]

## 4. Improvements for Market Leadership
### Short-term (weeks)
### Medium-term (quarters)
### Long-term (year+)

## 5. Implementation Blueprint for Top-1 Feature
[Choose the single highest-ROI feature and provide a technical design]

## 6. Success Metrics (KPIs)
- Compression quality (e.g., retention@80% compression)
- p99 latency for 1M tokens
- Adoption in ≥3 major AI frameworks
