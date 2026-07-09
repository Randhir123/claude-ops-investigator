# Evidence Ref Discipline

## Core Principle

Every claim must cite an `evidence_ref` from tool results.

## Tool Response Structure

Tools return:
- **summary**: Compact, human-readable finding
- **evidence_ref**: Pointer to full raw data in `artifacts/`
- **data**: Full raw payload (archived automatically)

Example:
```json
{
  "isError": false,
  "summary": "3 pods restarted in last 60m",
  "evidence_ref": "k8s.pod_describe.20260709T054200Z.abc123",
  "data": {...}
}
```

## Usage Rules

1. **Reason from summaries first**
   - Summaries contain enough information for most decisions
   - Do not fetch raw detail speculatively

2. **Call `evidence_get_detail` only when needed**
   - Summary is truncated at a critical point
   - Need full stack trace/log body to verify hypothesis
   - Need exact configuration values

3. **Preserve evidence_ref in findings**
   - Never summarize away the `evidence_ref`
   - Include `evidence_ref` in every finding
   - Pass `evidence_ref` through to final report

4. **Never store raw data in scratchpads**
   - Scratchpads hold summaries and `evidence_ref`s only
   - Full raw data lives in `artifacts/`
   - Retrievable via `evidence_get_detail(evidence_ref)`

## Evidence Table Format

In final report, include evidence table:

| Source | Summary | Evidence Ref | Timestamp |
|--------|---------|--------------|-----------|
| k8s_describe_pod | Pod event-data-xyz terminated: OOMKilled | k8s.pod_describe.20260709T054200Z.abc123 | 2026-07-09T05:42:00Z |
| prom_get_pod_restart_increase | 3 restarts in last 60m | prom.restart_increase.20260709T054300Z.def456 | 2026-07-09T05:43:00Z |

## Anti-Patterns

❌ **Don't do this:**
```
"3 pods restarted"  # Where's the evidence_ref?
```

✅ **Do this:**
```
"3 pods restarted in last 60m (evidence_ref: prom.restart_increase.20260709T054300Z.def456)"
```

## Root Cause Claims

Never claim root cause without `evidence_ref` support:

❌ **Don't do this:**
```
"Root cause: OOM due to memory leak"
```

✅ **Do this:**
```
"Likely cause: OOM (evidence: k8s.pod_describe.20260709T054200Z.abc123 shows OOMKilled termination, prom.memory_usage.20260709T054300Z.ghi789 shows 95% memory usage)"
```
