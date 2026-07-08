---
name: runbook-summarizer
description: Summarize operational runbooks into concise diagnosis steps and safety warnings.
argument-hint: "Provide runbook file path or text."
allowed-tools:
  - Read
context: fork
---

# Runbook Summarizer Skill

Summarize a runbook into:

- trigger symptoms
- diagnosis commands
- likely causes
- safe read-only checks
- risky actions requiring approval
- escalation criteria
