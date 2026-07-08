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

## Claude Code slash command

The main interactive workflow is the `/investigate-incident` slash command:

```
/investigate-incident namespace=<namespace> service=<service> symptom="<specific symptom>" since_minutes=<minutes>
```

`symptom` is required — this workflow is symptom-driven, not a generic
service health check. If no symptom is given, Claude asks for one before
investigating anything.

Examples:

```
/investigate-incident namespace=si service=multi-system-processor symptom="readiness probe failures during recent rollout" since_minutes=60
/investigate-incident namespace=si service=event-data symptom="KafkaConsumerCommitRateLow alert fired" since_minutes=120
/investigate-incident namespace=si service=multi-system-processor symptom="OOMKilled restarts observed" since_minutes=180
```

What it does:

1. Reads the service catalog and runbook catalog
2. Lists target pods
3. Checks namespace events
4. Uses describe/log/top tools as needed
5. Uses Prometheus tools for metrics when configured
6. Uses IBM Cloud Logs tools for historical logs when configured
7. Stores raw evidence as `evidence_ref`s
8. Produces an evidence-grounded incident report

What it does not do:

- Does not mutate Kubernetes resources
- Does not restart pods
- Does not apply fixes
- Does not run destructive commands
- Does not fetch raw evidence detail unless needed

### Environment for optional tools

Prometheus:
- `PROMETHEUS_URL`
- `PROMETHEUS_AUTO_PORT_FORWARD`
- `PROMETHEUS_PF_SERVICE`
- `PROMETHEUS_PF_NAMESPACE`

IBM Cloud Logs:
- `IBM_LOGS_ENDPOINT`
- `IBM_CLOUD_API_KEY`

Copy `.env.example` to `.env` and fill in local values. Never commit `.env`.
The MCP server loads it automatically at startup so these tools have access
without any secrets going into `.mcp.json`.

### No-token local tests

These exercise the tools and structured error paths without any real
Prometheus, IBM Cloud, or Kubernetes credentials:

```bash
python -m pytest
python scripts/mcp_smoke_client.py
```

Direct tool checks:

```bash
python - <<'PY'
from claude_ops.tools.prometheus_preflight import ensure_prometheus
import json
print(json.dumps(ensure_prometheus(), indent=2))
PY

python - <<'PY'
from claude_ops.tools.ibm_logs_tools import ibm_logs_search_errors
import json
print(json.dumps(ibm_logs_search_errors("si", "multi-system-processor", limit=1), indent=2))
PY
```

## Local environment

The MCP server needs environment variables for the optional Prometheus and
IBM Cloud Logs tools (`PROMETHEUS_URL`, `IBM_LOGS_ENDPOINT`,
`IBM_CLOUD_API_KEY`, etc.). Configure them locally with a `.env` file — it is
gitignored and loaded automatically, no secrets ever need to go in
`.mcp.json`.

```bash
cp .env.example .env
# edit .env with your local values
source .venv/bin/activate
claude
```

`src/claude_ops/mcp/server.py` calls `load_dotenv()` at startup, so the MCP
server picks up `.env` automatically when Claude Code launches it — no
manual `export` needed. Missing `.env` is fine; tools that need a variable
that still isn't set return a structured config error instead of failing
silently.

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
