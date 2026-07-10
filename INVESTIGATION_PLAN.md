# Investigation Plan: event-data Pod Restarts in si Namespace

## Executive Summary

**Investigation Mode**: Bob Shell (Claude Code subagents NOT available)  
**Approach**: Direct workflow execution using orchestrator mode with specialist mode delegation

**Incident Parameters**:
- **Namespace**: si
- **Service**: event-data  
- **Symptom**: Pod restarts
- **Time Window**: Last 60 minutes
- **Investigation ID**: Will be generated as `si-event-data-{YYYYMMDDTHHMMSSZ}`

---

## Investigation Strategy

Since Claude Code subagents are unavailable, we'll use Bob Shell's custom mode system:

1. **Current Mode**: `plan` (creating this investigation plan)
2. **Execution Mode**: `orchestrator` (coordinates specialist modes)
3. **Specialist Modes Available**:
   - `k8s-evidence-collector`: Kubernetes current-state evidence
   - `prometheus-analyst`: Metrics analysis (requires Prometheus preflight)
   - `log-analyst`: Historical IBM Cloud Logs
   - `runbook-analyst`: Known incident pattern matching
   - `incident-reporter`: Final report synthesis

---

## 8-Step Investigation Workflow

### Step 1: Context Gathering (Orchestrator)

**Objective**: Load service and runbook catalogs for context

**Actions**:
- [ ] Read `ops://service-catalog` MCP resource
  - Expected: Service configuration, dependencies, known issues
- [ ] Read `ops://runbook-catalog` MCP resource  
  - Expected: Index of available runbooks with titles and IDs

**Delegation**: Orchestrator reads these directly (has read access)

**Success Criteria**: Both catalogs loaded and summarized

---

### Step 2: Pod Discovery (k8s-evidence-collector)

**Objective**: Identify event-data pods only, scoped to the service

**Delegation to**: `k8s-evidence-collector`

**Task Brief**:
```
Namespace: si
Service: event-data
Symptom: Pod restarts
Time Window: Last 60 minutes

Action: List pods for event-data service only using label_selector="app=event-data"
```

**Tool**: `k8s_list_pods(namespace="si", label_selector="app=event-data")`

**Expected Output**:
- Pod names (exact, for use in subsequent calls)
- Pod phases (Running/Pending/Failed/CrashLoopBackOff)
- Restart counts per pod
- Container names

**Success Criteria**: 
- Pod list retrieved with evidence_ref
- No unrelated services enumerated
- Restart counts visible for each pod

---

### Step 3: Namespace Events Analysis (k8s-evidence-collector)

**Objective**: Get recent Kubernetes events, filter to event-data pods

**Delegation to**: `k8s-evidence-collector`

**Task Brief**:
```
Namespace: si
Service: event-data
Symptom: Pod restarts
Time Window: Last 60 minutes

Action: Get recent namespace events and filter to event-data pods only
```

**Tool**: `k8s_get_recent_namespace_events(namespace="si")`

**Expected Output**:
- Event counts by reason (Unhealthy, OOMKilled, FailedScheduling, BackOff, etc.)
- Matching objects (pods, deployments)
- Timestamps

**Post-Processing**: Filter out events for unrelated services

**Success Criteria**:
- Events retrieved with evidence_ref
- Event-data-specific events identified
- Unrelated service events noted but not reported as incident evidence

---

### Step 4: Pod-Level Deep Dive (k8s-evidence-collector)

**Objective**: Inspect configuration and state of affected pods

**Delegation to**: `k8s-evidence-collector`

**For Each Pod with Restarts**:

#### 4a. Pod Description
**Tool**: `k8s_describe_pod(namespace="si", pod_name="{exact_pod_name}")`

**Focus Areas**:
- Readiness probe config (path, timeout, failure threshold)
- Liveness probe config (path, timeout, failure threshold)
- Last termination reason (OOMKilled, Error, exit code)
- Container restart count
- Recent pod-specific events

**Expected Output**: Compact summary + evidence_ref

#### 4b. Current Pod Logs
**Tool**: `k8s_get_pod_logs(namespace="si", pod_name="{exact_pod_name}", since_minutes=60, tail=200)`

**Purpose**: Check for errors/exceptions in current pod incarnation

**Limitation**: Only shows current pod - does NOT span restarts

**Expected Output**: Log excerpt + evidence_ref

#### 4c. Current Resource Usage
**Tool**: `k8s_top_pods(namespace="si")`

**Focus**: CPU (cores) and memory (bytes) for event-data pods

**Purpose**: Identify current resource pressure

**Expected Output**: Per-pod usage + evidence_ref

**Success Criteria**:
- All affected pods inspected
- Termination reasons identified
- Probe configurations documented
- Current logs and resource usage captured

---

### Step 5: Metrics Analysis (prometheus-analyst)

**Objective**: Quantify restart frequency and resource trends

**Delegation to**: `prometheus-analyst`

**MANDATORY WAVE 0 PREFLIGHT** (orchestrator delegates this first):
```
Task: Call prom_ensure_connection to verify Prometheus reachability.
Report: reachable/unreachable and stop.
Do NOT fetch metrics in this delegation.
```

**Tool**: `prom_ensure_connection()`

**If Unreachable**: STOP investigation, report gap to user

**If Reachable**: Continue with metrics gathering

#### 5a. Restart Increase (Incident Window)
**Tool**: `prom_get_pod_restart_increase(namespace="si", service="event-data", since_minutes=60)`

**Purpose**: How many restarts happened in the last 60 minutes?

**Preferred Over**: `prom_get_pod_restart_counts` (cumulative all-time)

#### 5b. Memory Usage Trends
**Tool**: `prom_get_pod_memory_usage(namespace="si", service="event-data")`

**Purpose**: Correlate with OOMKilled terminations

#### 5c. CPU Usage Trends
**Tool**: `prom_get_pod_cpu_usage(namespace="si", service="event-data")`

**Purpose**: Identify CPU pressure

#### 5d. HTTP Error Rate (if applicable)
**Tool**: `prom_get_http_error_rate(namespace="si", service="event-data", since_minutes=60)`

**Purpose**: Measure availability impact

#### 5e. Latency p95 (if applicable)
**Tool**: `prom_get_latency_p95(namespace="si", service="event-data", since_minutes=60)`

**Purpose**: Measure performance impact

**Gap Handling**: If any metric is unavailable (no coverage), document as `unknown`, NOT zero/normal

**Success Criteria**:
- Prometheus connectivity confirmed
- Restart increase quantified
- Resource trends captured
- Availability/performance impact measured (or gaps documented)

---

### Step 6: Historical Log Analysis (log-analyst)

**Objective**: Search persistent logs spanning pod restarts

**Delegation to**: `log-analyst`

**Prerequisites**: IBM_CLOUD_API_KEY and IBM_LOGS_ENDPOINT configured

**Task Brief**:
```
Namespace: si
Service: event-data (use app="event-data" parameter)
Symptom: Pod restarts
Time Window: Last 60 minutes

Action: Search IBM Cloud Logs for errors, probe failures, and specific patterns
```

#### 6a. ERROR-Level Logs
**Tool**: `ibm_logs_search_errors(namespace="si", app="event-data", since_minutes=60, limit=200)`

**Purpose**: Find errors spanning pod restarts

**Preferred Over**: `k8s_get_pod_logs` for historical analysis

#### 6b. Probe Failure Logs
**Tool**: `ibm_logs_search_probe_failures(namespace="si", app="event-data", since_minutes=60, limit=200)`

**Purpose**: Correlate with Unhealthy events from Step 3

#### 6c. Specific Error Patterns (if identified)
**Tool**: `ibm_logs_search_text(namespace="si", app="event-data", text="{pattern}", since_minutes=60, limit=200)`

**Examples**: 
- Exception class names (NullPointerException, OutOfMemoryError)
- Endpoint paths (/health, /ready)
- Alert strings from monitoring

**Gap Handling**: If IBM Logs unavailable, document as `unknown`

**Success Criteria**:
- Historical errors identified (or gap documented)
- Probe failures correlated with events
- Specific error signatures extracted

---

### Step 7: Runbook Correlation (runbook-analyst)

**Objective**: Match symptom to known incident patterns

**Delegation to**: `runbook-analyst`

**Task Brief**:
```
Symptom: Pod restarts in event-data service
Time Window: Last 60 minutes

Action: Search runbooks for "pod restarts" and any specific termination reasons found
```

#### 7a. Generic Symptom Search
**Tool**: `runbook_search(query="pod restarts")`

**Purpose**: Check for known incident patterns

#### 7b. Specific Symptom Search (if applicable)
**Tool**: `runbook_search(query="{specific_symptom}")`

**Examples**:
- "OOMKilled"
- "liveness probe failure"
- "readiness probe failure"
- "CrashLoopBackOff"

**Expected Output**:
- Runbook ID, title, excerpt
- Diagnosis steps
- Likely causes
- Safety warnings

**No Match Handling**: If no runbook matches, state explicitly (do not fabricate)

**Success Criteria**:
- Runbook search completed
- Relevant diagnosis steps extracted (or no-match documented)
- Safety warnings noted

---

### Step 8: Evidence Synthesis & Reporting (incident-reporter)

**Objective**: Produce schema-valid incident report

**Delegation to**: `incident-reporter`

**Input**: Structured Finding Brief from orchestrator containing:
- All evidence_refs from Steps 2-7
- Compact summaries from each specialist mode
- Identified gaps/unknowns

**Task Brief**:
```
Synthesize findings from k8s-evidence-collector, prometheus-analyst, log-analyst, 
and runbook-analyst into a schema-valid incident report.

Schema: src/claude_ops/schemas/incident_report_schema.py

Required fields:
- service: "event-data"
- namespace: "si"
- severity: low/medium/high/critical/unclear
- symptoms: [observed symptoms]
- evidence: [{source, detail, timestamp, evidence_ref}]
- likely_causes: [evidence-grounded causes]
- ruled_out: [what evidence excluded]
- recommended_next_steps: [actionable steps]
- requires_human: true if remediation risky/unclear
- confidence: low/medium/high/unclear
- unknowns: [missing data/gaps]
```

**Evidence Discipline**:
- ✅ Every claim cites evidence_ref
- ✅ Preserve evidence source and detail
- ✅ State unknowns explicitly
- ✅ List ruled-out causes with evidence
- ✅ Set requires_human=true if needed

**Output Location**: `runs/si-event-data-{timestamp}/report.md`

**Success Criteria**:
- Report conforms to incident_report_schema.py
- All evidence cited with evidence_ref
- Unknowns documented
- Ruled-out causes listed
- No destructive commands suggested

---

## Safety Rules (Non-Negotiable)

### Blocked Operations
❌ **NEVER suggest without human approval**:
- `kubectl delete`
- `kubectl apply`
- `kubectl patch`
- `kubectl scale`
- `kubectl rollout restart`
- `kubectl exec`
- `helm upgrade`

### Evidence Discipline
✅ **ALWAYS**:
- Cite evidence_ref for every claim
- Preserve evidence source and detail
- State unknowns explicitly
- List what evidence ruled out
- Set requires_human=true if remediation risky

❌ **NEVER**:
- Summarize away evidence_ref
- Treat missing config as zero/normal
- Claim root cause without evidence
- Enumerate unrelated services

---

## Configuration Requirements

### Required for Full Investigation
- ✅ Kubernetes access (kubectl configured)
- ⚠️ PROMETHEUS_URL (optional but recommended)
- ⚠️ PROMETHEUS_AUTO_PORT_FORWARD (optional, default false)
- ⚠️ IBM_CLOUD_API_KEY (optional but recommended)
- ⚠️ IBM_LOGS_ENDPOINT (optional but recommended)

### Gap Handling
If any config is missing:
1. Document as `unknown` in report
2. Do NOT treat as zero/normal value
3. List in `unknowns` section
4. Note as limitation in confidence assessment

---

## Expected Artifacts

### Investigation Directory
```
runs/si-event-data-{YYYYMMDDTHHMMSSZ}/
├── scratchpad/
│   ├── coordinator-brief.md      # Orchestrator's running findings
│   └── workflow-audit.md         # Tool call log
└── report.md                     # Final incident report
```

### Evidence Store
```
artifacts/
└── {evidence_ref}.json           # Raw tool outputs (gitignored)
```

### Optional Report Copy
```
reports/si-event-data-{YYYYMMDDTHHMMSSZ}.md
```

---

## Execution Checklist

### Pre-Investigation
- [ ] Verify kubectl access: `kubectl config current-context`
- [ ] Check RBAC: `kubectl auth can-i get pods -n si`
- [ ] Verify .env configuration (optional services)

### Investigation Phases
- [ ] Phase 1: Context gathering (service + runbook catalogs)
- [ ] Phase 2: Pod discovery (k8s-evidence-collector)
- [ ] Phase 3: Namespace events (k8s-evidence-collector)
- [ ] Phase 4: Pod deep dive (k8s-evidence-collector)
- [ ] Phase 5: Metrics analysis (prometheus-analyst with preflight)
- [ ] Phase 6: Historical logs (log-analyst)
- [ ] Phase 7: Runbook correlation (runbook-analyst)
- [ ] Phase 8: Report synthesis (incident-reporter)

### Post-Investigation
- [ ] Verify report schema compliance
- [ ] Confirm all evidence_refs cited
- [ ] Review unknowns and ruled-out causes
- [ ] Check requires_human flag
- [ ] Validate no destructive commands suggested

---

## Mode Transition Instructions

**Current Mode**: `plan` (markdown-only editing)

**Next Action**: Switch to `orchestrator` mode

**Command**:
```
Switch to orchestrator mode to execute this investigation plan
```

**Orchestrator Responsibilities**:
1. Create investigation directory: `runs/si-event-data-{timestamp}/`
2. Initialize coordinator brief: `scratchpad/coordinator-brief.md`
3. Execute mandatory Prometheus preflight (wave 0)
4. Delegate to specialist modes based on symptom
5. Aggregate findings in Structured Finding Brief format
6. Hand off to incident-reporter for final synthesis

**Specialist Mode Delegation Pattern**:
```
Delegate to {mode_slug}:
Task: {specific action}
Context: namespace={ns}, service={svc}, symptom={symptom}, since_minutes={window}
Output: Write findings to runs/{investigation_id}/scratchpad/{mode_slug}-findings.md
```

---

## Success Criteria

### Investigation Complete When:
✅ All 8 workflow steps executed  
✅ Every claim backed by evidence_ref  
✅ Unknowns explicitly documented  
✅ Ruled-out causes listed with evidence  
✅ Report conforms to incident_report_schema.py  
✅ No destructive commands suggested without approval  
✅ requires_human flag set appropriately  

### Quality Gates:
- Evidence refs preserved through all delegations
- Unrelated services filtered out
- Gaps reported as unknowns, not zeros
- Safety warnings from runbooks noted
- Confidence level justified by evidence quality

---

## Investigation Ready

This plan is complete and ready for execution. Switch to `orchestrator` mode to begin the investigation.
