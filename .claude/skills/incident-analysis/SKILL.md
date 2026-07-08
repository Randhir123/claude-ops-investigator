---
name: incident-analysis
description: Analyze Kubernetes incident snapshots and produce evidence-grounded incident reports.
argument-hint: "Provide namespace, service, time window, and incident snapshot JSON."
allowed-tools:
  - Read
  - Grep
  - Glob
context: fork
---

# Incident Analysis Skill

Use this skill to analyze a Kubernetes incident snapshot.

## Required output

Produce:

- symptoms
- evidence
- likely causes
- ruled out causes
- recommended next steps
- human escalation decision
- unknowns

## Rules

- Do not invent facts.
- Do not recommend destructive remediation without human approval.
- Preserve evidence provenance.
- If the snapshot is incomplete, say what data is missing.
