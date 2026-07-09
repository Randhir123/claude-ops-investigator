# Investigate Incident Skill

## Description

Read-only Kubernetes incident investigation workflow using the claude-ops-investigator MCP tools. Anchored to a specific reported symptom, not a generic health check.

**Bob Harness Note:** This skill executes as a single-agent workflow (Bob does not support Claude Code's named subagent delegation). All investigation steps are performed sequentially by Bob, with findings tracked in a scratchpad directory.

## Inputs

- **namespace** (required): Kubernetes namespace
- **service** (required): Service name (matches `app` label)
- **symptom** (required): Specific reported symptom or alert (e.g., "readiness probe failures during recent rollout", "KafkaConsumerCommitRateLow alert fired", "pods restarting with OOMKilled")
- **since_minutes** (optional, default: 60): Time window to investigate

## Investigation Structure

### Investigation ID Format
```
<namespace>-<service>-<UTC timestamp YYYYMMDDTHHMMSSZ>
```
Example: `si-multi-system-processor-20260709T080958Z`

### Directory Structure
```
runs/<investigation_id>/
├── scratchpad/
│   ├── coordinator-brief.md      # Investigation progress and findings
│   └── workflow-audit.md          # Tools called, decisions made
└── report.md                      # Final incident report

reports/
└── <investigation_id>.md          # Copy of final report (optional)
```

**Important:** Do NOT write investigation files to the repository root. All investigation artifacts must be under `runs/<investigation_id>/`.

## Rules

### Investigation Scope
- Investigation must be anchored to a concrete symptom
- Do not perform generic service health checks
- Scope all evidence gathering to the target service only
- Filter out unrelated services from namespace-wide results

### Tool Usage
- Use claude-ops-investigator MCP tools exclusively
- Do not use raw `kubectl`, `helm`, or generic shell commands
- Do not use generic command execution for investigation
- Prefer typed tools over free-form queries:
  - `prom_get_pod_restart_increase` for incident-window restart checks
  - `prom_get_pod_restart_counts` only as supporting context for cumulative totals
  - `prom_get_pod_cpu_usage`, `prom_get_pod_memory_usage` for resource metrics
  - `prom_get_http_error_rate`, `prom_get_latency_p95` for availability/latency
  - `ibm_logs_search_errors`, `ibm_logs_search_probe_failures`, `ibm_logs_search_text` for historical logs

### Evidence Discipline
- Start with current Kubernetes evidence (`k8s_list_pods`, `k8s_describe_pod`, `k8s_get_recent_namespace_events`)
- Use Prometheus metrics when needed to size/corroborate the incident
- Use IBM Cloud Logs for historical/cross-restart log analysis
- Use runbook search for known incident patterns
- Preserve `evidence_ref` in every finding
- Store/mention only summaries and `evidence_ref`s, never raw logs or secrets
- Call `evidence_get_detail` only when summary is insufficient, and explain why detail was needed
- Example: "Summary shows 59 ERROR logs but doesn't indicate if they're probe-related. Fetching detail to check for readiness/liveness mentions."

### Gaps and Unknowns
- Treat missing metrics/no metric coverage as a gap, not as healthy or zero
- Treat unreachable Prometheus as an unknown/gap, not as "no metrics"
- Missing `PROMETHEUS_URL`, `IBM_CLOUD_API_KEY`, or `IBM_LOGS_ENDPOINT` is a gap
- Report all gaps explicitly in the unknowns section

### Log Analysis
- Keep unrelated ERROR logs separate/background unless they mention:
  - readiness, liveness, probes, startup
  - port 9493, health endpoints
  - container termination, OOM, restart
- Do not conflate unrelated errors with the reported symptom

### Root Cause
- Never claim root cause without `evidence_ref` support
- Explicitly list what evidence ruled out
- State confidence level based on evidence quality

### Safety
- Never run or suggest destructive commands:
  - `kubectl delete`, `apply`, `patch`, `scale`, `rollout restart`, `exec`
  - `helm upgrade`
- Mark any production-impacting remediation as `requires_human: true`

## Workflow

1. **Mint investigation ID and setup**
   - Generate investigation_id: `<namespace>-<service>-<UTC timestamp YYYYMMDDTHHMMSSZ>`
   - Create directory: `runs/<investigation_id>/scratchpad/`
   - Initialize `coordinator-brief.md` with investigation scope
   - Initialize `workflow-audit.md` for tracking tool calls

2. **Restate the investigation scope**
   - Echo namespace, service, symptom, time window in coordinator-brief.md

3. **Read context**
   - `ops://service-catalog` resource (read from data/service_catalog.json)
   - `ops://runbook-catalog` resource (read from data/runbook_index.json)
   - Document in coordinator-brief.md

4. **Discover pods**
   - `k8s_list_pods` with `label_selector="app={service}"`
   - Get exact pod names for further investigation
   - Update coordinator-brief.md with pod list
   - Log tool call in workflow-audit.md

5. **Gather Kubernetes evidence**
   - `k8s_get_recent_namespace_events` (namespace-wide, filter to service in analysis)
   - `k8s_describe_pod` for affected pods (use summaries first)
   - `k8s_get_pod_logs` for current pod logs (if needed)
   - `k8s_top_pods` for current resource usage (if needed)
   - Update coordinator-brief.md with findings
   - Log each tool call in workflow-audit.md

6. **Check runbooks**
   - `runbook_search` with symptom text
   - Note any matching patterns and safety warnings
   - Update coordinator-brief.md with runbook findings
   - Log tool call in workflow-audit.md

7. **Gather metrics (if needed)**
   - `prom_get_pod_restart_increase` for incident-window restarts
   - `prom_get_pod_memory_usage` for OOM investigation
   - `prom_get_http_error_rate`, `prom_get_latency_p95` for availability/latency
   - **Prometheus connectivity recovery**: If any Prometheus tool returns:
     - `isError=true`
     - `errorCategory="transient"`
     - Message contains: connection refused, timeout, unreachable, network error, OR alternatives mention `prom_ensure_connection`
     - Then: Call `prom_ensure_connection` to check/establish connectivity
     - If `prom_ensure_connection` succeeds: Retry the original Prometheus tool once with same arguments
     - If `prom_ensure_connection` fails or Prometheus remains unreachable: Document as gap in unknowns
     - **Important**: Bob must NOT start raw `kubectl port-forward` itself. Only `prom_ensure_connection` may do that, and only if `PROMETHEUS_AUTO_PORT_FORWARD=true`
   - Handle Prometheus unreachable as a gap (document in unknowns)
   - Update coordinator-brief.md with metrics findings
   - Log each tool call in workflow-audit.md

8. **Gather historical logs (if needed)**
   - `ibm_logs_search_errors` for ERROR-level logs
   - `ibm_logs_search_probe_failures` for probe-related logs
   - `ibm_logs_search_text` for specific error patterns
   - Handle missing IBM Cloud Logs config as a gap
   - Update coordinator-brief.md with log findings
   - Log each tool call in workflow-audit.md

9. **Analyze and synthesize**
   - Reason from summaries first
   - Call `evidence_get_detail` only when summary is insufficient (explain why in coordinator-brief.md)
   - Correlate evidence across tools
   - Identify likely causes with evidence support
   - List ruled-out causes with evidence
   - List unknowns/gaps
   - Update coordinator-brief.md with analysis

10. **Generate final report**
    - Write structured incident report to `runs/<investigation_id>/report.md`
    - Optionally copy to `reports/<investigation_id>.md`
    - Include all required sections (see Output section below)

## Output

### File Structure

All investigation artifacts are written to `runs/<investigation_id>/`:

1. **scratchpad/coordinator-brief.md** - Investigation progress and findings
2. **scratchpad/workflow-audit.md** - Complete tool call log with timestamps
3. **report.md** - Final structured incident report

Optionally, copy final report to `reports/<investigation_id>.md` for easier access.

### Final Report Contents

The final report (`runs/<investigation_id>/report.md`) must include:

1. **Investigation Metadata**
   - Investigation ID
   - Timestamp
   - Investigator: "claude-ops-investigator (Bob Shell harness)"
   - Note: "Bob subagent parity: single-agent skill execution"
   - Investigation duration

2. **Incident Scope**
   - Namespace, service, symptom, time window, service risk level

3. **Executive Summary**
   - Clear verdict (symptom confirmed or not confirmed)
   - Key findings (3-5 bullet points)
   - Confidence level
   - Requires human action flag

4. **Detailed Findings**
   - Current pod state
   - Kubernetes events (filtered to target service)
   - Pod configuration analysis
   - Historical log analysis (probe failures, errors, symptom-specific searches)
   - Metrics analysis (or gap documentation)
   - Runbook correlation

5. **Evidence Summary Table**
   - Source (tool name)
   - Evidence ref
   - Summary
   - Timestamp

6. **Likely Causes**
   - Evidence-grounded causes only
   - Each cause must cite evidence_ref

7. **Ruled Out**
   - Hypotheses excluded by evidence
   - Evidence that excluded each (with evidence_ref)

8. **Unknowns and Gaps**
   - Missing configuration (Prometheus, IBM Cloud Logs)
   - Unreachable services
   - Time window limitations
   - Insufficient evidence
   - External dependencies

9. **Recommended Next Steps**
   - Immediate actions (no human approval required)
   - Conditional actions (if symptom recurs)
   - No risky actions (mark as requires_human if needed)

10. **Workflow Audit**
    - Tools called (in order, with parameters)
    - Evidence refs collected
    - Key decisions made
    - Note: "Bob subagent parity: single-agent skill execution (no named subagent delegation)"

11. **Clear Verdict**
    - Explicit statement: "Symptom confirmed for {service} in {namespace} over the last {since_minutes} minutes" OR "Symptom not confirmed for {service} in {namespace} over the last {since_minutes} minutes"
    - Supporting evidence for verdict

12. **Incident Report Schema Compliance**
    - JSON representation matching INCIDENT_REPORT_SCHEMA
    - All required fields populated

## Example Usage

```
Use the investigate-incident skill.

namespace=si
service=multi-system-processor
symptom="readiness probe failures during recent rollout"
since_minutes=60
```

## Acceptance Criteria

### Tool Usage
- Uses claude-ops-investigator MCP tools exclusively
- Does not use raw kubectl, helm, or shell commands
- Preserves `evidence_ref` in all findings
- Uses `prom_get_pod_restart_increase` for incident-window restart checks
- Treats no metric coverage as gap, not zero

### File Structure
- Mints investigation_id: `<namespace>-<service>-<UTC timestamp YYYYMMDDTHHMMSSZ>`
- Creates `runs/<investigation_id>/scratchpad/` directory
- Writes `coordinator-brief.md` and `workflow-audit.md` to scratchpad
- Writes final report to `runs/<investigation_id>/report.md`
- Does NOT write investigation files to repository root
- Optionally copies final report to `reports/<investigation_id>.md`

### Evidence Discipline
- Uses summaries first, calls `evidence_get_detail` only when insufficient
- Explains why detail was needed when calling `evidence_get_detail`
- Keeps unrelated log errors separate from symptom root cause unless they mention:
  - readiness, liveness, probes, startup
  - port 9493, health endpoints
  - container termination, OOM, restart

### Report Quality
- States whether symptom is confirmed for the requested service/window
- Produces schema-valid incident report
- Lists ruled-out causes with evidence refs
- Lists unknowns/gaps explicitly
- Includes workflow audit with note: "Bob subagent parity: single-agent skill execution"
- Includes clear verdict in executive summary and conclusion
