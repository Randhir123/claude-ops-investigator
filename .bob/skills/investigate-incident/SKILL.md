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
`.bob/rules-orchestrator/00-workflow.md` and PARITY_NOTES.md, item 5): a
skill-based investigation runs with strictly more privilege than the
`orchestrator` + specialist custom modes provide, even though it never
*needs* that privilege.

**Use `.bob/commands/investigate-incident.md` instead.** It switches into
the scoped `orchestrator` custom mode and delegates to specialist modes,
none of which have shell access and each of which is limited (by
`customInstructions`, not a hard boundary — see PARITY_NOTES.md, item 2) to
its own narrow MCP tool subset.

Only fall back to this skill if your Bob installation doesn't support custom
modes, or you've deliberately decided the single-agent, single-context
tradeoff is acceptable for your use case.

## Fallback workflow (Advanced mode, single agent)

1. Mint `investigation_id` = `<namespace>-<service>-<UTC timestamp,
   YYYYMMDDTHHMMSSZ>` and create `runs/<investigation_id>/scratchpad/`.
2. Maintain one running scratchpad at
   `runs/<investigation_id>/scratchpad.md` (no coordinator/specialist split
   — see `.bob/rules-orchestrator/../rules-ops-investigator/scratchpad-and-briefs.md`
   for the single-scratchpad format this project used previously).
3. Work through the same symptom-driven tool selection as
   `.bob/rules-orchestrator/02-symptom-routing.md`, calling MCP tools
   directly yourself instead of delegating.
4. Apply every safety rule in `.bob/rules-orchestrator/00-workflow.md` and
   the individual specialist rule files manually — nothing enforces them for
   you in Advanced mode.
5. Write the final report to `runs/<investigation_id>/report.md`, following
   the same schema as `.bob/rules-incident-reporter/00-rules.md`, and note
   in the report: "Bob subagent parity: single-agent skill execution
   (Advanced mode fallback, no custom-mode delegation)."
