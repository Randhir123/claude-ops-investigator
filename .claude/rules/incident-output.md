---
paths:
  - "src/claude_ops/schemas/**/*"
  - "src/claude_ops/agent/**/*"
  - "src/claude_ops/main.py"
---

# Incident Output Rules

Incident reports must include:

- service
- namespace
- severity
- symptoms
- evidence
- likely_causes
- ruled_out
- recommended_next_steps
- requires_human
- confidence
- unknowns

Every likely cause should be supported by at least one evidence item.
