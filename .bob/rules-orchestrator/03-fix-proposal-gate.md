# Fix-Proposal Gate (invoked only by /propose-fix)

This file is not part of the default investigation workflow in
00-workflow.md. /investigate-incident never reads it, and orchestrator
never applies it on its own. It only runs when /propose-fix explicitly
instructs orchestrator to apply it, after a report exists — either freshly
produced or loaded from an earlier investigation_id.

## The gate

Read runs/<investigation_id>/report.md. Check: does at least one
likely_causes entry cite a specific, named application-code location — an
exception class, stack trace, or file/function reference — as opposed to an
infrastructure/operational finding (resource limits, probe configuration,
replica/scaling settings, missing config)?

- If yes: delegate to fix-proposer once, passing the full report and
  investigation_id (and "DRY RUN — do not branch, commit, push, or open a
  PR" if /propose-fix was invoked with dry_run=true). Append fix-proposer's
  outcome (PR opened + URL, or no PR + reason) to the report as a new
  "Proposed fix" section.
- If no: report that the gate wasn't met and stop — no PR, no fix-proposer
  delegation. This is not a failure; most incidents are operational, not
  code bugs, and should end here.

Be conservative: a vague or generic error message alone (no class, no file)
is not enough to trigger this — you need something a human could actually
locate and read in source. This gate is a judgment call, not a hard schema
field (INCIDENT_REPORT_SCHEMA doesn't have one).
