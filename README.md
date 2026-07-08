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

1. Delegates to the `incident-coordinator` subagent (falls back to a single
   agent, with that fallback stated explicitly, only if subagents aren't
   available)
2. The coordinator reads the service catalog and runbook catalog, then routes
   the symptom to the narrowest relevant specialist subagents:
   - `k8s-evidence-collector` — pod listing, describe, live logs, namespace
     events, current resource usage
   - `prometheus-analyst` — restart counts/increase, CPU, memory, HTTP error
     rate, latency p95
   - `log-analyst` — historical IBM Cloud Logs search (errors, probe
     failures, arbitrary text) spanning restarts/deployments
   - `runbook-analyst` — matches the symptom against local runbooks
3. Every specialist stores raw evidence as an `evidence_ref` and hands back
   only summaries/findings — the coordinator never gathers evidence directly
4. `incident-reporter` runs last, synthesizing all subagents' findings (never
   its own) into a single evidence-grounded, schema-valid report
5. The final output includes a "Subagent usage audit" table: which subagent
   ran, what it did, which tools/evidence_refs it used, and its result

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
- `prom_query_instant`
- `prom_get_pod_restart_counts`
- `prom_get_pod_restart_increase`
- `prom_get_pod_cpu_usage`
- `prom_get_pod_memory_usage`
- `prom_get_http_error_rate`
- `prom_get_latency_p95`
- `prom_ensure_connection`
- `ibm_logs_search`
- `ibm_logs_search_errors`
- `ibm_logs_search_probe_failures`
- `ibm_logs_search_text`
- `evidence_get_detail`

Prompt:
- `investigate_incident`
