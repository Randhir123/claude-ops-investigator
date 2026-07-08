---
paths:
  - "tests/**/*"
  - "src/**/*.py"
---

# Testing Rules

- Use pytest.
- Test both success and failure paths.
- For Kubernetes tools, prefer monkeypatching subprocess calls.
- Always include tests proving destructive commands are blocked.
