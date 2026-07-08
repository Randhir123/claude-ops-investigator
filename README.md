# Claude Ops Investigator

A context-aware Kubernetes incident investigation assistant built with Claude Code, MCP, live cluster signals, Prometheus, log search, runbooks, evidence memory, and structured incident reports.

Claude Ops Investigator helps engineers investigate Kubernetes incidents safely by combining read-only operational tools, external evidence storage, compact investigation memory, and human-controlled remediation boundaries.

The goal is not to give an AI unrestricted production access. The goal is to expose narrow, auditable, read-only interfaces that help engineers gather evidence, form hypotheses, rule out causes, and produce reliable incident reports faster.

## What this project provides

- Narrow MCP-style tools instead of generic `kubectl`
- Read-only live Kubernetes investigation
- Claude Code project instructions
- MCP resources, tools, and prompts
- Skills, slash commands, and scoped project rules
- Coordinator/subagent-style investigation workflows
- Structured tool errors
- Hooks and gates for destructive actions
- Structured incident-report output
- Human escalation for risky or ambiguous actions

## Safety rule

Start read-only. Do not give Claude unrestricted shell, `kubectl`, Helm, or production mutation permissions.

Allowed operations in this scaffold:

- `kubectl get`
- `kubectl describe`
- `kubectl logs`
- `kubectl top`

Blocked operations include:

- `kubectl delete`
- `kubectl apply`
- `kubectl patch`
- `kubectl scale`
- `kubectl rollout restart`
- `helm upgrade`
- `kubectl exec` by default

## Quick start

```bash
cd claude-ops-investigator
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Check Kubernetes access:

```bash
kubectl config current-context
kubectl auth can-i get pods -n si
kubectl auth can-i get pods/log -n si
```

Run a read-only snapshot:

```bash
python -m claude_ops.main investigate --namespace si --service event-data --since-minutes 60
```

Run tests:

```bash
pytest
```

## Recommended first live use

Use a non-production namespace first.

```bash
python -m claude_ops.main investigate --namespace si --service multi-system-processor --since-minutes 120
```

Then paste the generated JSON snapshot into Claude/Claude Code and ask it to produce an incident report using the schema in `src/claude_ops/schemas/incident_report_schema.py`.

## Roadmap

1. Run the current MCP/Kubernetes tools against a live read-only cluster context.
2. Add evidence references and raw artifact storage to avoid context bloat.
3. Add durable investigation memory using task, evidence, hypothesis, and decision records.
4. Add Prometheus tools for bounded metric evidence.
5. Add log-search tools for persistent historical log evidence.
6. Add Claude API and Batch API paths for structured reports, evals, and offline analysis.
7. Add Goose as an operator-facing UI over the same MCP server.

## MCP client/server map

In this project:

```text
Claude Code = MCP client
src/claude_ops/mcp/server.py = local MCP server
.mcp.json = project-level MCP client configuration for Claude Code
```

Start the MCP server manually for a quick syntax check:

```bash
python -m claude_ops.mcp.server
```

For Claude Code, keep `.mcp.json` in the project root. Claude Code reads the config and launches the server over STDIO.

Optional smoke test:

```bash
pip install -e ".[dev,mcp]"
python scripts/mcp_smoke_client.py
```

The MCP server exposes:

Resources:
- `ops://runbook-catalog`
- `ops://service-catalog`

Tools:
- `k8s_list_pods`
- `k8s_describe_pod`
- `k8s_get_pod_logs`
- `k8s_get_recent_namespace_events`
- `k8s_top_pods`
- `runbook_search`

Prompt:
- `investigate_incident`
