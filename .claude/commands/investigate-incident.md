# Investigate Incident

Use this command when a developer wants a read-only Kubernetes investigation.

Arguments:

- namespace
- service
- since_minutes

Workflow:

1. Confirm the namespace and service.
2. Use only read-only tools.
3. Gather pods, events, logs, and resource usage.
4. Summarize symptoms.
5. Preserve evidence.
6. Produce a structured incident report.
7. Mark production-impacting actions as requiring human approval.
