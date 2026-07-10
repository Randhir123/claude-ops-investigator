---
description: Read-only Kubernetes incident investigation, delegated across specialist modes via the Orchestrator
argument-hint: namespace=<namespace> service=<service> symptom="<symptom>" since_minutes=<minutes>
---

Use this command when a developer wants a read-only Kubernetes investigation
of a **specific reported symptom or alert** — not a general service health
check. Every invocation must be anchored to a concrete symptom; if no symptom
is given, ask for one before doing any investigation.

Arguments (all required): namespace, service, symptom, since_minutes.

$ARGUMENTS

## Workflow

1. **Switch to `orchestrator` mode** (this project's override of Bob's
   built-in Orchestrator — see `.bob/custom_modes.yaml` — not the generic
   Advanced-mode skill). If already in `orchestrator` mode, continue in it.
2. **Mint an investigation workspace.** As `orchestrator`, create
   `investigation_id` = `<namespace>-<service>-<UTC timestamp,
   YYYYMMDDTHHMMSSZ>` and the directory `runs/<investigation_id>/scratchpad/`.
3. **Restate the symptom.** Echo back namespace, service, symptom, and time
   window before doing anything else.
4. **Read context first.** Read the `ops://service-catalog` and
   `ops://runbook-catalog` MCP resources.
5. **Delegate to specialist modes as subtasks**, following the routing in
   `.bob/rules-orchestrator/02-symptom-routing.md`:
   `k8s-evidence-collector`, `prometheus-analyst`, `log-analyst`,
   `runbook-analyst` as the symptom warrants, then `incident-reporter` last.
   Maintain and pass the Structured Finding Brief per
   `.bob/rules-orchestrator/01-structured-finding-brief.md` with every
   delegation.
6. **Do not gather evidence directly from `orchestrator` mode.** This mode
   has no `mcp` tool access on purpose — evidence gathering only happens
   inside a delegated specialist subtask.
7. **Produce a structured incident report grounded in evidence refs.**
   `incident-reporter` writes `runs/<investigation_id>/report.md`; every
   claim must cite an `evidence_ref`. Do not claim a root cause without
   evidence.
8. **Explicitly list what was ruled out and what remains unknown**,
   including any missing `PROMETHEUS_URL`, `IBM_CLOUD_API_KEY`, or
   `IBM_LOGS_ENDPOINT` config, or an unreachable Prometheus.
9. **Read-only only, end to end.** No mode in this workflow may run or
   suggest `kubectl delete/apply/patch/scale/rollout restart/exec` or
   `helm upgrade`. Mark any production-impacting remediation as
   `requires_human: true`.
10. **Include a "Specialist mode usage audit"** in the final report: which
    mode ran, its task, tools/evidence_refs/scratchpad path used, and its
    result — the Bob-native equivalent of Claude Code's "Subagent usage
    audit" table.

## Fallback: single-agent execution

If custom modes are unavailable in your Bob version (see
`.bob/custom_modes.yaml`'s schema note), fall back to running this entire
workflow from Advanced mode as a single agent, using the `investigate-incident`
skill under `.bob/skills/` — see that skill's `SKILL.md` for the fallback
procedure and its tradeoffs (full, unrestricted tool access rather than the
scoped per-mode access this command provides).
