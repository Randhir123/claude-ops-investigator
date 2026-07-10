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
4. **Mandatory Prometheus preflight (wave 0) — before any evidence-gathering
   delegation begins.** Delegate a subtask to `prometheus-analyst` to call
   `prom_ensure_connection` and confirm Prometheus is reachable, writing its
   result to `runs/<investigation_id>/scratchpad/wave0-prometheus-preflight.md`.
   If Prometheus is unreachable, **stop the investigation here**: do not
   delegate to any other specialist mode, do not produce a partial report,
   and do not record this as an `unknowns` gap to work around — tell the
   user plainly that Prometheus connectivity is required before an
   investigation can run. See
   `.bob/rules-ops-investigator/prometheus-connectivity.md` for the full gate
   rules. Only continue to the remaining steps once preflight has passed.
5. **Read context.** Read the `ops://service-catalog` and
   `ops://runbook-catalog` MCP resources.
6. **Delegate to specialist modes as subtasks**, following the routing in
   `.bob/rules-orchestrator/02-symptom-routing.md`:
   `k8s-evidence-collector`, `prometheus-analyst`, `log-analyst`,
   `runbook-analyst` as the symptom warrants, then `incident-reporter` last.
   Maintain and pass the Structured Finding Brief per
   `.bob/rules-orchestrator/01-structured-finding-brief.md` with every
   delegation.
7. **Do not gather evidence directly from `orchestrator` mode.** This mode
   has no `mcp` tool access on purpose — evidence gathering only happens
   inside a delegated specialist subtask.
8. **Produce a structured incident report grounded in evidence refs.**
   `incident-reporter` writes `runs/<investigation_id>/report.md`; every
   claim must cite an `evidence_ref`. Do not claim a root cause without
   evidence.
9. **Explicitly list what was ruled out and what remains unknown**,
   including any missing `IBM_CLOUD_API_KEY` or `IBM_LOGS_ENDPOINT` config,
   or an individual metric a later wave couldn't retrieve after preflight
   already passed. Total Prometheus unreachability is never an `unknowns`
   entry — per step 4, it's a hard stop before the report stage is ever
   reached.
10. **Read-only only, end to end.** No mode in this workflow may run or
    suggest `kubectl delete/apply/patch/scale/rollout restart/exec` or
    `helm upgrade`. Mark any production-impacting remediation as
    `requires_human: true`.
11. **Include a "Specialist mode usage audit"** in the final report: which
    mode ran, its task, tools/evidence_refs/scratchpad path used, and its
    result — including the wave 0 preflight delegation.

## Fallback: single-agent execution

If custom modes are unavailable in your Bob version (see
`.bob/custom_modes.yaml`'s schema note), fall back to running this entire
workflow from Advanced mode as a single agent, using the `investigate-incident`
skill under `.bob/skills/` — see that skill's `SKILL.md` for the fallback
procedure and its tradeoffs (full, unrestricted tool access rather than the
scoped per-mode access this command provides).
