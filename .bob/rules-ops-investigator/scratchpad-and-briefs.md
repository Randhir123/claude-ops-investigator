# Scratchpad and Briefs

## Core Principle

Maintain investigation state in structured scratchpads. Never store raw logs or secrets.

## Scratchpad Structure

For orchestrator-driven investigations (custom modes, the primary path):

```
runs/<investigation_id>/scratchpad/
├── coordinator-brief.md                   # Orchestrator's structured finding brief
├── wave0-prometheus-preflight.md          # Mandatory Prometheus reachability check
├── wave1-k8s-evidence-collector.md        # K8s evidence gathering
├── wave2-prometheus-analyst.md            # Metrics analysis
├── wave3-log-analyst.md                   # Historical log analysis
├── wave4-runbook-analyst.md               # Runbook matching
└── wave5-incident-reporter.md             # Final report synthesis
```

For the single-agent Advanced-mode fallback (`.bob/skills/investigate-incident/`,
used only when custom modes are unavailable — no wave split):

```
runs/<investigation_id>/
├── scratchpad.md          # Main investigation scratchpad
└── evidence-refs.md       # Evidence reference index
```

## Scratchpad Content Rules

**Must include:**
- Investigation scope (namespace, service, symptom, time window)
- Tools called with parameters
- Key findings with `evidence_ref`s
- Decisions made and reasoning
- Unknowns/gaps identified
- Next steps or handoff summary

**Must NOT include:**
- Raw log bodies
- Full kubectl/Prometheus output
- Secrets or credentials
- Personal data
- Unfiltered error dumps

## Structured Finding Brief

For coordinator-based investigations, maintain a Structured Finding Brief with:

1. **Investigation scope**
   - Namespace, service, symptom, time window

2. **Current working status**
   - One-line summary of investigation state

3. **Confirmed evidence**
   - Findings established so far
   - Each with `evidence_ref`

4. **Ruled out**
   - Hypotheses excluded by evidence
   - Evidence that excluded them

5. **Unknowns/gaps**
   - Missing configuration
   - Unreachable services
   - Insufficient evidence

6. **Unrelated/background signals**
   - Findings that don't connect to symptom
   - Prevents re-discovery/re-reporting

7. **Next wave plan**
   - Which specialist modes to delegate to next
   - Why they're needed

## Single-Agent Fallback Pattern

In the Advanced-mode fallback (no custom-mode delegation, see
`.bob/skills/investigate-incident/`), maintain a single scratchpad instead
of per-wave files:

```markdown
# Investigation: si/multi-system-processor - Readiness Probe Failures

## Scope
- Namespace: si
- Service: multi-system-processor
- Symptom: readiness probe failures during recent rollout
- Time window: last 60 minutes
- Investigation ID: si-multi-system-processor-20260709T060000Z

## Tools Called

### k8s_list_pods
- Parameters: namespace=si, label_selector="app=multi-system-processor"
- Result: 3 pods found, 2 in Running state, 1 in CrashLoopBackOff
- Evidence ref: k8s.list_pods.20260709T060100Z.abc123

### k8s_describe_pod
- Parameters: namespace=si, pod_name=multi-system-processor-xyz
- Result: Readiness probe failed: connection refused on port 9493
- Evidence ref: k8s.pod_describe.20260709T060200Z.def456

## Key Findings

1. Readiness probe failures confirmed
   - Evidence: k8s.pod_describe.20260709T060200Z.def456
   - 2 of 3 pods failing readiness checks
   - Port 9493 not responding

2. Recent restarts observed
   - Evidence: prom.restart_increase.20260709T060300Z.ghi789
   - 3 restarts in last 60 minutes
   - Correlates with probe failures

## Ruled Out

1. OOM as root cause
   - Evidence: prom.memory_usage.20260709T060400Z.jkl012
   - Memory usage at 45%, well below limit
   - No OOMKilled terminations in pod describe

## Unknowns

1. Historical probe failure logs
   - IBM Cloud Logs unavailable (IBM_CLOUD_API_KEY not configured)
   - Cannot determine if this is a recurring pattern

## Next Steps

1. Check runbooks for known probe failure patterns
2. Examine current pod logs for port 9493 errors
3. Synthesize findings into incident report
```

## Evidence Reference Index

Maintain a separate evidence reference index:

```markdown
# Evidence References

## k8s.list_pods.20260709T060100Z.abc123
- Tool: k8s_list_pods
- Timestamp: 2026-07-09T06:01:00Z
- Summary: 3 pods found, 2 Running, 1 CrashLoopBackOff
- Location: artifacts/k8s.list_pods.20260709T060100Z.abc123.json

## k8s.pod_describe.20260709T060200Z.def456
- Tool: k8s_describe_pod
- Timestamp: 2026-07-09T06:02:00Z
- Summary: Readiness probe failed on port 9493
- Location: artifacts/k8s.pod_describe.20260709T060200Z.def456.json
```

## Anti-Patterns

❌ **Don't do this:**
```markdown
## Pod Logs
<paste 1000 lines of raw logs>
```

❌ **Don't do this:**
```markdown
## Findings
- Some errors observed
- Pods restarting
- Might be a problem
```

✅ **Do this:**
```markdown
## Key Findings

1. Readiness probe failures confirmed
   - Evidence: k8s.pod_describe.20260709T060200Z.def456
   - 2 of 3 pods failing readiness checks
   - Port 9493 not responding
   - Correlates with recent rollout (deployment event at 05:55:00Z)
```

## Update Frequency

- Update scratchpad after each tool call
- Update evidence index when new evidence is collected
- Update Structured Finding Brief (coordinator) after each wave
- Never let scratchpad get stale or out of sync with actual investigation state
