# Structured Finding Brief

Before launching each new investigation wave (one or more subtask delegations
run for the same purpose), create or update the Structured Finding Brief,
persist it to `coordinator-brief.md`, and include it verbatim in every
subtask instruction for that wave. This is what lets a specialist mode
reason without inheriting hidden context — the brief, plus the task-specific
question, is the entirety of what it knows about the investigation so far.

The brief must include these fields, every time:

- **Investigation scope** — namespace, service, symptom, time window.
- **Current working status** — one line on where the investigation stands
  (e.g. "symptom not yet confirmed against this service").
- **Confirmed evidence** — findings established so far, each with its
  `evidence_ref`.
- **Ruled out** — hypotheses the evidence so far excludes, with the
  evidence_ref(s) that excluded them.
- **Unknowns/gaps** — anything not yet confirmed, missing config, or
  evidence that couldn't be gathered.
- **Unrelated/background signals** — findings observed that don't (yet)
  connect to the reported symptom, so specialists don't mistake them for
  confirmed causes or re-discover/re-report them as new.
- **Next wave plan** — which mode(s) you're about to delegate to next and
  why.
- **The specific question for this subtask** — what this particular
  delegation needs to answer, scoped narrowly to that mode's tools. Per-
  delegation only; it doesn't need to be persisted to `coordinator-brief.md`,
  only included in that subtask's instructions.

Before the first wave, most fields will be empty (e.g. "none yet") — still
include the brief in full so the shape is consistent from the start.

**Update the brief after each wave, before delegating again.** Fold that
wave's findings into confirmed evidence / ruled_out / unknowns / background
signals as appropriate, so the next wave's specialists see the current
state, not the initial one.
