# Prometheus Connectivity

## Core Principle

Prometheus connectivity is a **mandatory, non-bypassable prerequisite** for
running an investigation — not a soft gap to note and route around. It is
checked once, up front, via a hard preflight gate. A specific metric being
unavailable *after* that gate has passed is a narrower, softer gap and is
still handled as an `unknowns` entry.

## The preflight gate (hard stop)

Before any evidence-gathering delegation begins, the orchestrator delegates
a wave 0 subtask to prometheus-analyst that does nothing but call
`prom_ensure_connection` and report reachable/unreachable (see
`.bob/rules-orchestrator/00-workflow.md` and
`.bob/commands/investigate-incident.md`).

- **If Prometheus is unreachable at this gate** — whether because
  `PROMETHEUS_URL` is missing/misconfigured, the connection times out, or
  any other network error — the investigation **stops immediately**:
  - Do not delegate to any other specialist mode.
  - Do not produce a report, partial or otherwise.
  - Do not record it as an `unknowns` entry to work around — the
    investigation never reaches the report stage.
  - Tell the user plainly that Prometheus connectivity is required before an
    investigation can run, including whatever detail
    `prom_ensure_connection` returned (e.g. missing config vs. unreachable
    endpoint).
- **If Prometheus is reachable**, the investigation proceeds normally and
  this gate is not revisited.

## After the gate has passed: per-metric gaps are still soft

Once wave 0 preflight has confirmed connectivity, a later wave may still hit
narrower issues. These remain `unknowns`, not investigation-ending:

1. **A single metric call fails transiently** — network blip on one
   `prom_get_*`/`prom_query_instant` call. Handled by prometheus-analyst's
   one-shot recovery retry (below); if the retry also fails, report that
   specific metric as an `unknowns` gap for that wave only. Do not treat as
   zero metrics or as "no restarts"/"no errors".
2. **No metric coverage for this service** — e.g. no HTTP error rate metric
   or no latency metric exists for the service being investigated. Report as
   an `unknowns` gap, not "0% errors" or "latency is fine".

Neither of these reopens or bypasses the wave 0 gate; they're scoped to the
one metric or one wave they occurred in, with overall connectivity already
confirmed.

## Tool Behavior

All `prom_get_*` and `prom_query_instant` tools return structured errors for:
- Missing `PROMETHEUS_URL`
- Unreachable Prometheus
- Query timeout
- Invalid query

Error response structure:
```json
{
  "isError": true,
  "errorCategory": "transient|validation|permission|business|unknown",
  "isRetryable": true|false,
  "message": "descriptive error message",
  "attempted": {...},
  "partialResults": null,
  "alternatives": ["suggestion 1", "suggestion 2"]
}
```

## Who calls `prom_ensure_connection`

Only `prometheus-analyst` has `prom_ensure_connection` in its tool list, and
only in the two situations described above: the mandatory wave 0 preflight
(delegated by the orchestrator, always run, regardless of symptom), and a
one-shot recovery retry after a later transient failure. It is never called
automatically by any `prom_get_*` tool, and never called speculatively
outside those two cases.

`prom_ensure_connection` behavior:
- Checks Prometheus reachability first
- May start `kubectl port-forward` only if `PROMETHEUS_AUTO_PORT_FORWARD=true`
- Returns instructions if auto-port-forward disabled
- After success, retry the metric query via relevant `prom_get_*` tool

### Prometheus Connectivity Recovery (post-gate, per-metric)

Applies only after wave 0 preflight has already confirmed Prometheus is
reachable. When any Prometheus MCP tool (`prom_get_*`, `prom_query_instant`)
subsequently returns a transient connectivity error on one call:

**Detection criteria** (any of):
- `isError=true` AND `errorCategory="transient"`
- Message contains: "connection refused", "timeout", "unreachable", "network error"
- `alternatives` array mentions `prom_ensure_connection`

**Recovery workflow**:
1. Call `prom_ensure_connection` to check/establish connectivity
   - This tool checks Prometheus reachability
   - May start `kubectl port-forward` only if `PROMETHEUS_AUTO_PORT_FORWARD=true`
   - Returns success/failure status

2. If `prom_ensure_connection` succeeds (Prometheus reachable):
   - Retry the original Prometheus tool **once** with same arguments
   - If retry succeeds: Use the metrics data
   - If retry fails: Document as gap in unknowns

3. If `prom_ensure_connection` fails or Prometheus remains unreachable:
   - Document the specific metric as a gap in unknowns for this wave
   - Do NOT treat as zero metrics or healthy service
   - Include error details in unknowns
   - This does not reopen the wave 0 gate retroactively — surface it to the
     orchestrator as a notable finding, since Prometheus becoming
     unreachable mid-investigation after passing preflight is unusual and
     worth flagging distinctly from an ordinary single-metric gap

**Important constraints**:
- Bob must NOT start raw `kubectl port-forward` itself
- Only `prom_ensure_connection` may start port-forward
- Port-forward only happens if `PROMETHEUS_AUTO_PORT_FORWARD=true`
- Do not retry more than once after `prom_ensure_connection`
- Do not retry indefinitely on repeated failures

**Example workflow**:
```
1. Call prom_get_pod_restart_increase(namespace=si, service=event-data, since_minutes=60)
2. Result: isError=true, errorCategory="transient", message="Connection refused"
3. Call prom_ensure_connection()
4. Result: isError=false, message="Prometheus reachable at http://localhost:9090"
5. Retry prom_get_pod_restart_increase(namespace=si, service=event-data, since_minutes=60)
6. Result: isError=false, data={...restart metrics...}
7. Use metrics in investigation
```

**Gap documentation example**:
```
If prom_ensure_connection fails:
"Unknown: Prometheus metrics unavailable (connection refused to http://localhost:9090, prom_ensure_connection failed, evidence_ref: prom.error.20260709T083143Z.abc123)"
```

## Anti-Patterns

❌ **Don't do this:**
```python
if not prometheus_url:
    return {"restarts": 0}  # Wrong! This is a gap, not zero
```

❌ **Don't do this:**
```
"No restarts detected (Prometheus unreachable)"  # Contradictory
```

✅ **Do this:**
```python
if not prometheus_url:
    return {
        "isError": True,
        "errorCategory": "validation",
        "message": "PROMETHEUS_URL not configured",
        "alternatives": ["Set PROMETHEUS_URL in .env"]
    }
```

✅ **Do this:**
```
"Unknown: Restart count could not be determined (Prometheus unreachable, evidence_ref: prom.error.20260709T054300Z.abc123)"
```

## Reporting

A final incident report only exists if wave 0 preflight passed — total
Prometheus unreachability (missing `PROMETHEUS_URL`, connection timeout,
etc.) is a hard stop at the preflight step and is reported to the user
directly, not folded into a report's `unknowns` section, because no report
is produced in that case.

**Unknowns section in the final report may still include** (post-gate,
per-metric gaps only):
- "HTTP error rate metric not available for this service"
- "Latency metric not available for this service"
- "Restart increase metric temporarily unavailable for one wave (transient
  error, recovery retry also failed)"

**Do not include in evidence or likely causes:**
- Prometheus errors are not evidence of healthy service
- Missing metrics are not evidence of no problems
