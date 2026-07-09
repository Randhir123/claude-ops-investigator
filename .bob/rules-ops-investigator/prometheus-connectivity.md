# Prometheus Connectivity

## Core Principle

Unreachable Prometheus is a gap/unknown, not "no metrics" or "healthy".

## Connectivity Rules

1. **Missing configuration is a gap**
   - Missing `PROMETHEUS_URL` → gap, not zero metrics
   - Report in unknowns section
   - Do not treat as "service is healthy"

2. **Unreachable Prometheus is a gap**
   - Connection timeout → gap, not zero metrics
   - Network error → gap, not zero metrics
   - Report in unknowns section
   - Do not treat as "no restarts" or "no errors"

3. **No metric coverage is a gap**
   - Service has no HTTP error rate metric → gap, not "0% errors"
   - Service has no latency metric → gap, not "latency is fine"
   - Report in unknowns section

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
  "errorCategory": "config|connectivity|validation|timeout",
  "isRetryable": true|false,
  "message": "descriptive error message",
  "attempted": {...},
  "partialResults": null,
  "alternatives": ["suggestion 1", "suggestion 2"]
}
```

## Coordinator Responsibility

In Claude Code subagent architecture:
- Only `incident-coordinator` calls `prom_ensure_connection`
- Only when user explicitly requested Prometheus investigation
- Only when metrics are necessary to answer the symptom
- Never automatically by `prom_get_*` tools

`prom_ensure_connection` behavior:
- Checks Prometheus reachability first
- May start `kubectl port-forward` only if `PROMETHEUS_AUTO_PORT_FORWARD=true`
- Returns instructions if auto-port-forward disabled
- After success, retry the metric query via relevant `prom_get_*` tool

## Bob Usage

Bob does not have a coordinator subagent, so:
- Bob may call `prom_ensure_connection` directly when needed
- Follow same rules: only when metrics are necessary
- Handle unreachable Prometheus as a gap
- Do not retry indefinitely

### Prometheus Connectivity Recovery (Bob-Specific)

When any Prometheus MCP tool (`prom_get_*`, `prom_query_instant`) returns a transient connectivity error:

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
   - Document as gap in unknowns section
   - Do NOT treat as zero metrics or healthy service
   - Include error details in unknowns

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
        "errorCategory": "config",
        "message": "PROMETHEUS_URL not configured",
        "alternatives": ["Set PROMETHEUS_URL in .env"]
    }
```

✅ **Do this:**
```
"Unknown: Restart count could not be determined (Prometheus unreachable, evidence_ref: prom.error.20260709T054300Z.abc123)"
```

## Reporting

In final incident report:

**Unknowns section must include:**
- "Prometheus metrics unavailable (PROMETHEUS_URL not configured)"
- "Prometheus unreachable (connection timeout)"
- "HTTP error rate metric not available for this service"
- "Latency metric not available for this service"

**Do not include in evidence or likely causes:**
- Prometheus errors are not evidence of healthy service
- Missing metrics are not evidence of no problems
