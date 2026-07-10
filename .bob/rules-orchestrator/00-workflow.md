# Orchestrator Workflow

Adapted from `.claude/agents/incident-coordinator.md`. The mechanism differs
(Bob delegates via visible, interactive **subtasks**; Claude Code delegates
via silent, isolated **subagents** — see PARITY_NOTES.md, item 4) but the
investigation shape is the same.

## Subtask routing

Delegate to specialist modes as subtasks, choosing based on the reported
symptom — do not invoke every mode for every incident:

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
  state, not a per-wave log. This mode is deliberately granted `edit`
  scoped to `runs/**/*.md` for exactly this purpose (see PARITY_NOTES.md,
  item 3 — the built-in Orchestrator mode has zero tool access by default).
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

## Prometheus connectivity — known gap vs. Claude Code

The Claude Code coordinator has a scoped `prom_ensure_connection` tool and
can retry a metric query after re-establishing connectivity. This
Orchestrator mode intentionally has **no MCP tool access at all** (see
PARITY_NOTES.md, item 3), so it cannot call `prom_ensure_connection` itself.
If `prometheus-analyst` reports Prometheus unreachable, record it as an
`unknowns` gap and surface it to the user — do not attempt connectivity
recovery from this mode, and do not treat missing metrics as zero.
