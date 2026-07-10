---
name: investigate-incident
description: Fallback single-agent Kubernetes incident investigation, for use only when custom modes are unavailable. Prefer the /investigate-incident command (.bob/commands/), which runs the same workflow with scoped, per-mode tool access instead of full Advanced-mode access.
---

## Why this skill is now a fallback, not the primary path

Bob **skills only activate in Advanced mode**
(<https://bob.ibm.com/docs/ide/features/skills#requirements>), and Advanced
mode has no tool restrictions at all — full `read`, `edit`, `command`, and
`mcp` access, including raw shell. That directly conflicts with this
project's read-only, narrowly-scoped safety design (see
`.bob/rules-orchestrator/00-workflow.md`): a skill-based investigation runs
with strictly more privilege than the `orchestrator` + specialist custom
modes provide, even though it never *needs* that privilege.

**Use `.bob/commands/investigate-incident.md` instead.** It switches into
the scoped `orchestrator` custom mode and delegates to specialist modes,
none of which have shell access and each of which is limited (by
`customInstructions`, not a hard permission boundary — see the limitation
noted at the top of `.bob/custom_modes.yaml`) to its own narrow MCP tool
subset.

Only fall back to this skill if your Bob installation doesn't support custom
modes, or you've deliberately decided the single-agent, single-context
tradeoff is acceptable for your use case.

## Fallback workflow (Advanced mode, single agent)

1. Mint `investigation_id` = `<namespace>-<service>-<UTC timestamp,
   YYYYMMDDTHHMMSSZ>` and create `runs/<investigation_id>/scratchpad/`.
2. **Mandatory Prometheus preflight, before any evidence gathering.** Call
   `prom_ensure_connection` and confirm Prometheus is reachable. If it is
   not, stop here — do not gather any evidence and do not produce a report.
   Tell the user plainly that Prometheus connectivity is required before an
   investigation can run. See
   `.bob/rules-ops-investigator/prometheus-connectivity.md` for the full
   gate rules; this fallback path enforces the same hard stop as the
   orchestrator's wave 0.
3. Maintain one running scratchpad at
   `runs/<investigation_id>/scratchpad.md` (no coordinator/specialist split
   — see `.bob/rules-ops-investigator/scratchpad-and-briefs.md` for the
   single-scratchpad format).
4. Work through the same symptom-driven tool selection as
   `.bob/rules-orchestrator/02-symptom-routing.md`, calling MCP tools
   directly yourself instead of delegating.
5. Apply every safety rule in `.bob/rules-orchestrator/00-workflow.md` and
   the individual specialist rule files manually — nothing enforces them for
   you in Advanced mode.
6. Write the final report to `runs/<investigation_id>/report.md`, following
   the same schema as `.bob/rules-incident-reporter/00-rules.md`, and note
   in the report: "Execution mode: single-agent skill (Advanced mode
   fallback, no custom-mode delegation)."
