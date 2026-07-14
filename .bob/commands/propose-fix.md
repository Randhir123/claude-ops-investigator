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
   - If namespace/service/symptom/since_minutes are given: run the full
     investigation workflow exactly as /investigate-incident.md defines it
     (switch to orchestrator, mint investigation_id, delegate through
     specialists per the routing table, incident-reporter writes
     report.md).
   - If investigation_id (or "latest") is given instead: skip straight to
     loading runs/<investigation_id>/report.md (resolving "latest" as the
     most recently modified runs/*/report.md). Do not re-investigate.
2. Once a report.md exists (freshly written or loaded), apply
   .bob/rules-orchestrator/03-fix-proposal-gate.md. If dry_run=true was
   given, tell fix-proposer explicitly "DRY RUN — do not branch, commit,
   push, or open a PR." If base_branch=<branch> was given, tell
   fix-proposer explicitly to use that as the base branch instead of
   whatever is currently checked out.
3. Report back: the investigation findings (if freshly run), whether the
   gate was met, and if so, whether a PR was opened and its URL.
