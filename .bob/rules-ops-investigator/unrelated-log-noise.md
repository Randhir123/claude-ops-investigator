# Unrelated Log Noise

## Core Principle

Keep unrelated ERROR logs separate from the reported symptom root cause.

## What Counts as Related

Log errors are related to the symptom if they mention:
- **Probe keywords**: readiness, liveness, probes, startup
- **Health endpoints**: port 9493, health endpoints, `/health`, `/ready`, `/live`
- **Container lifecycle**: container termination, OOM, restart, killed
- **Symptom-specific terms**: exact error strings from the reported symptom

## What Counts as Unrelated

Log errors are unrelated/background if they:
- Do not mention probe/health/termination keywords
- Are generic application errors (e.g., license validation, fulfillment timeouts)
- Occur in unrelated services in the same namespace
- Predate the incident window significantly
- Are known/expected errors documented in runbooks as non-critical

## Handling Unrelated Errors

1. **Do not conflate with symptom**
   - Do not claim unrelated errors caused the symptom
   - Do not include in likely causes without clear connection

2. **Report as background signals**
   - Note in a separate "Background/Unrelated Signals" section
   - Explain why they're considered unrelated
   - Preserve evidence_ref for completeness

3. **Example structure**
   ```
   ## Likely Causes
   - Readiness probe failing due to port 9493 not responding (evidence_ref: ibm_logs.probe_failures.xyz)
   
   ## Background/Unrelated Signals
   - License validation errors observed (evidence_ref: ibm_logs.errors.abc)
     - Not related to readiness probe failures
     - No mention of health endpoints or container termination
     - Known non-critical error per runbook
   ```

## Symptom-Specific Examples

### Readiness Probe Failures

**Related:**
- "Readiness probe failed: Get http://10.0.0.1:9493/ready: dial tcp 10.0.0.1:9493: connect: connection refused"
- "Health check endpoint /ready returned 503"
- "Port 9493 not responding"

**Unrelated:**
- "License validation failed for tenant XYZ"
- "Fulfillment timeout after 30s"
- "Database connection pool exhausted"

### OOM / Restarts

**Related:**
- "OOMKilled"
- "Container terminated: Error"
- "Memory limit exceeded"
- "java.lang.OutOfMemoryError"

**Unrelated:**
- "HTTP 404 on /api/v1/resource"
- "Kafka consumer lag increased"
- "Redis connection timeout"

### Latency / Errors

**Related:**
- "HTTP 500 Internal Server Error"
- "Request timeout after 30s"
- "Circuit breaker opened"

**Unrelated:**
- "Scheduled job failed"
- "Cache miss for key ABC"
- "Audit log write failed"

## Anti-Patterns

❌ **Don't do this:**
```
"Root cause: License validation errors causing readiness probe failures"
# (No evidence linking license errors to probe failures)
```

❌ **Don't do this:**
```
"Service is healthy, only license errors observed"
# (Ignoring actual probe failures)
```

✅ **Do this:**
```
"Likely cause: Readiness probe failing due to port 9493 not responding (evidence_ref: ibm_logs.probe_failures.xyz)

Background signals:
- License validation errors observed (evidence_ref: ibm_logs.errors.abc)
  - Not related to readiness probe failures
  - No mention of health endpoints
  - Known non-critical error per runbook"
```

## Investigation Strategy

1. **Start with symptom-specific search**
   - Use `ibm_logs_search_probe_failures` for probe symptoms
   - Use `ibm_logs_search_text` with specific error strings

2. **Broaden only if needed**
   - Use `ibm_logs_search_errors` if specific search returns nothing
   - Filter results to symptom-related errors

3. **Separate signal from noise**
   - Identify errors that match symptom keywords
   - Note unrelated errors separately
   - Do not mix in final root cause analysis
