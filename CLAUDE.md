# Claude Ops Support Agent — Project Instructions

You are working on a read-only Kubernetes incident-investigation assistant.

## Non-negotiable safety rules

- Never suggest or run destructive Kubernetes actions by default.
- Do not use `kubectl delete`, `kubectl apply`, `kubectl patch`, `kubectl scale`, `kubectl rollout restart`, `helm upgrade`, or `kubectl exec` unless a human explicitly approves and a gate allows it.
- Prefer narrow typed tools over generic shell commands.
- Treat production actions as human-approved only.

## Architecture rules

Use the coordinator/subagent pattern:

- Coordinator decomposes the incident, decides which tools/subagents to use, aggregates findings, and produces the final report.
- Subagents are specialized and receive explicit context.
- Subagents do not automatically inherit parent context.
- All evidence must preserve provenance: source type, namespace, pod/service, timestamp if available, and raw supporting detail.

## Output rules

Final incident reports must be structured and evidence-grounded.

Do not claim a root cause without evidence.
Use `unknowns` when data is missing.
Use `requires_human: true` when remediation is risky, policy is unclear, or evidence is insufficient.

## Testing rules

- Add tests for tool allowlists and destructive command blocking.
- Add tests for structured error responses.
- Add tests for incident-report schema validation.

## Style

- Keep tools narrow and typed.
- Return structured errors with `errorCategory`, `isRetryable`, and `message`.
- Prefer explicit, auditable behavior over clever generic abstractions.
