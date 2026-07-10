# Read-Only Safety

## Allowed Operations

- `kubectl get`
- `kubectl describe`
- `kubectl logs`
- `kubectl top`

## Blocked Operations

Never run or suggest:

- `kubectl delete`
- `kubectl apply`
- `kubectl patch`
- `kubectl scale`
- `kubectl rollout restart`
- `kubectl exec`
- `helm upgrade`
- Any production mutation without explicit human approval

## Tool Usage

- Use claude-ops-investigator MCP tools exclusively
- Do not use raw `kubectl`, `helm`, or shell commands
- All investigation tools are read-only by design
- Application-level gate: `src/claude_ops/hooks.py::validate_kubectl_verb`

## Remediation

- Mark any production-impacting remediation as `requires_human: true`
- Never execute remediation automatically
- Provide evidence-grounded recommendations only
- Let humans decide and execute fixes
