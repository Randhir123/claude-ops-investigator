# Orchestrator Workflow

Bob delegates via visible, interactive **subtasks**: each specialist mode
runs as its own conversation thread that the orchestrator seeds with an
explicit brief and reads back a final result from.

## Wave 0: mandatory Prometheus preflight

Before any other delegation, delegate a subtask to **prometheus-analyst**
instructed to call only `prom_ensure_connection` and report back
reachable/unreachable — no metrics, no other tool calls. Write its result to
`runs/<investigation_id>/scratchpad/wave0-prometheus-preflight.md`.

- If reachable: proceed to the rest of the workflow below.
- If unreachable: **stop the investigation immediately.** Do not delegate to
  any other specialist mode, do not produce a report (partial or otherwise),
  and do not record this as an `unknowns` gap to work around. Tell the user
  plainly that Prometheus connectivity is required before an investigation
  can run, and that the investigation was aborted at the preflight step. See
  `.bob/rules-ops-investigator/prometheus-connectivity.md` for the full gate
  rules.

## Subtask routing

Once preflight has passed, delegate to specialist modes as subtasks,
choosing based on the reported symptom — do not invoke every mode for every
incident:

- **k8s-evidence-collector** — pod listing, pod describe, live pod logs,
  namespace events, current resource usage. Start here for any symptom.
- **prometheus-analyst** — metrics and impact measurement: restart counts/
  increase, CPU/memory usage, HTTP error rate, latency. Use when you need
  numbers to size the incident or corroborate a hypothesis. For restart/OOM/
  crash-loop symptoms, ask for restart increase over the incident window,
  not only cumulative counts.
- **log-analyst** — historical logs across pod restarts and deployments via
  IBM Cloud Logs. Use for anything that predates the current pod
  incarnation, or when live pod logs are insufficient/unavailable.
- **runbook-analyst** — match the symptom against known runbooks for
  diagnosis steps and safety warnings.
- **incident-reporter** — always the last subtask. Hand it the final
  Structured Finding Brief plus every selected specialist's findings (not
  your own conclusions) to produce the final structured report.

## Investigation workspace

You are given `investigation_id` (and its scratchpad directory,
`runs/<investigation_id>/scratchpad/`) by the `/investigate-incident`
workflow. If it's missing, mint one yourself as
`<namespace>-<service>-<UTC timestamp, YYYYMMDDTHHMMSSZ>` and create the
directory before your first delegation.

- **Your own brief lives on disk.** Maintain
  `runs/<investigation_id>/scratchpad/coordinator-brief.md` with the
  Structured Finding Brief (see `01-structured-finding-brief.md`). Write it
  with the `edit` tool after folding in each subtask's findings, before
  starting the next one — overwrite the file each time; it reflects current
  state, not a per-wave log. This mode is deliberately granted `edit` scoped
  to `runs/**/*.md` for exactly this purpose, since it has no other way to
  persist state between delegations.
- **Give each subtask an explicit scratchpad path to write to**, following
  the convention `runs/<investigation_id>/scratchpad/wave<N>-<mode-slug>.md`
  (e.g. `wave1-k8s-evidence-collector.md`, `wave2-prometheus-analyst.md`).
  State this exact path in the subtask instructions; the specialist mode
  writes there itself. Treat these as immutable snapshots — a later wave
  gets a new file, never an edit to an earlier one.
- **Never let raw logs or payloads land in any scratchpad** — yours or a
  specialist's. Scratchpads hold summaries and `evidence_ref`s only; the
  full raw data lives in `artifacts/` and is retrievable via
  `evidence_get_detail`.

## Rules

- Restate namespace, service, symptom, and time window before delegating, so
  every subtask gets an explicit, scoped brief. Specialist modes do not
  inherit this conversation — a Bob subtask starts its own context, seeded
  only by the instructions you give it. Pass the Structured Finding Brief
  plus the specific question for that delegation directly in the subtask
  prompt.
- Every subtask prompt must include, in this order: (1) the current
  Structured Finding Brief, verbatim; (2) the paths of any prior scratchpads
  relevant to this delegation; and (3) the precise question this delegation
  needs answered, plus the exact scratchpad path this mode must write its
  findings to.
- Choose modes based on the symptom; see `02-symptom-routing.md` for the
  symptom-to-mode mapping this project uses.
- Preserve each specialist's evidence provenance (source, evidence_ref) when
  folding findings into the brief and when passing findings to
  incident-reporter.
- Never issue or suggest destructive Kubernetes/Helm actions yourself, and
  never instruct a specialist mode to. Treat production remediation as
  human-approved only.
- If specialists disagree or evidence is inconsistent, say so explicitly in
  the brief's unknowns/gaps rather than picking one account — let
  incident-reporter capture it under `unknowns`.
- If the Structured Finding Brief carries a "Code context brief" path
  (only possible via `/propose-fix`'s fresh-investigation Step 2 — see
  `01-structured-finding-brief.md`), specialists — especially log-analyst
  and prometheus-analyst — may read it for vocabulary: specific exception
  names, log strings, metric names to search for. It is never a citable
  `evidence_ref`. Every `likely_causes` claim must still trace back to a
  real `k8s_*`/`prom_*`/`ibm_logs_*`/`runbook_search` result; a lead the
  brief suggested but that couldn't be confirmed against real evidence must
  not appear in the report as a finding.

## Prometheus connectivity

This mode has **no MCP tool access at all**, so it never calls
`prom_ensure_connection` itself — the mandatory wave 0 preflight (above)
delegates that call to prometheus-analyst instead. A wave 0 failure is a
hard stop for the whole investigation, not an `unknowns` gap to route
around; see `.bob/rules-ops-investigator/prometheus-connectivity.md`. Once
preflight has passed, an isolated transient failure on a later metrics call
is handled by prometheus-analyst's own one-shot retry and, if still failing,
recorded as an `unknowns` gap for that specific metric only.
