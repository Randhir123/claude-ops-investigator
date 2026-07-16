---
description: Investigate an incident and, if it traces to application code, propose a fix as a draft PR — or apply the same fix-proposal step to a report that already exists
argument-hint: (namespace=<namespace> service=<service> symptom="<symptom>" since_minutes=<minutes>) | investigation_id=<id>|latest [dry_run=true] [base_branch=<branch>]
---

This command does everything /investigate-incident does, plus one more
step: if the resulting report traces a cause to application code, it
proposes a fix and opens a draft PR automatically, with no separate human
step in between. Use /investigate-incident instead if you only want a
report and don't want fix-proposer to ever run.

$ARGUMENTS

## Workflow

1. Determine mode from the arguments given:
   - If namespace/service/symptom/since_minutes are given: this is
     fresh-investigation mode. Continue to Step 2 below.
   - If investigation_id (or "latest") is given instead: skip straight to
     loading runs/<investigation_id>/report.md (resolving "latest" as the
     most recently modified runs/*/report.md). Do not re-investigate, and
     skip Step 2 — go straight to Step 3. This mode never runs the Code
     Context Brief pre-step; it only applies to a fresh investigation.
2. **Delegate to orchestrator for the investigation, with a Code Context
   Brief pre-step.** Switch to orchestrator and instruct it explicitly:
   immediately after minting `investigation_id` and creating its
   scratchpad directory, and before wave 0 or any specialist delegation,
   orchestrator must first:
   - Look up `service` in `data/service_catalog.json`. If the entry has no
     `source_repo_path`: skip the rest of this step entirely and proceed
     straight to wave 0 — no behavior change, no error, no note in the
     report.
   - If the entry does have a `source_repo_path`: delegate a narrow,
     read-only subtask to `fix-proposer` mode (the only mode with a path to
     a source checkout) to produce a Code Context Brief — reconnaissance
     only; no branch, commit, push, or write to the source repo of any
     kind. See `.bob/rules-fix-proposer/00-rules.md`'s "Code Context Brief
     mode" section for the full procedure. The subtask writes
     `runs/<investigation_id>/scratchpad/code-context-brief.md`.
   - If that file was created, fold its path into the Structured Finding
     Brief (see `.bob/rules-orchestrator/01-structured-finding-brief.md`)
     before wave 0 begins, so it reaches every subsequent specialist
     delegation.
   This pre-step is specific to this /propose-fix delegation —
   /investigate-incident.md's own switch to orchestrator never includes
   it, so orchestrator stays code-blind there. Everything after this
   pre-step (wave 0 preflight, specialist delegation per the routing
   table, incident-reporter writing report.md) runs exactly as
   /investigate-incident.md defines.
3. Once a report.md exists (freshly written per Step 2, or loaded per Step
   1), apply .bob/rules-orchestrator/03-fix-proposal-gate.md. If
   dry_run=true was given, tell fix-proposer explicitly "DRY RUN — do not
   branch, commit, push, or open a PR." If base_branch=<branch> was given,
   tell fix-proposer explicitly to use that as the base branch instead of
   whatever is currently checked out. If a code-context-brief.md exists
   from Step 2, note its path to fix-proposer here too (see
   `.bob/rules-fix-proposer/00-rules.md`'s note on reusing it).
4. Report back: the investigation findings (if freshly run), whether the
   gate was met, and if so, whether a PR was opened and its URL.
