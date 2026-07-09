# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Claude Ops Support Agent — Project Instructions

You are working on a read-only Kubernetes incident-investigation assistant.

## Non-negotiable safety rules

- Never suggest or run destructive Kubernetes actions by default.
- Do not use `kubectl delete`, `kubectl apply`, `kubectl patch`, `kubectl scale`, `kubectl rollout restart`, `helm upgrade`, or `kubectl exec` unless a human explicitly approves and a gate allows it.
- Prefer narrow typed tools over generic shell commands.
- Treat production actions as human-approved only.

## Architecture rules

Use the coordinator/subagent pattern — this is implemented as real Claude Code
subagents in `.claude/agents/`, not just a convention:

- `incident-coordinator` decomposes the incident, decides which subagents to
  delegate to based on the symptom, aggregates findings, and hands off to
  `incident-reporter` last. It is the only subagent allowed to call
  `prom_ensure_connection`, and only when the user explicitly asked for
  Prometheus-backed investigation or metrics are necessary to answer the
  symptom.
- `k8s-evidence-collector`, `prometheus-analyst`, `log-analyst`, and
  `runbook-analyst` are the specialist subagents — each scoped to a narrow
  tool subset (see each agent's frontmatter `tools:` list). None of them
  draws incident-level conclusions; that's the coordinator/reporter's job.
- `incident-reporter` always runs last, never gathers evidence itself, and
  only synthesizes the findings it's handed into the schema-valid report.
- Subagents do not automatically inherit parent context — the coordinator
  must restate namespace/service/symptom/window explicitly in each delegation.
- All evidence must preserve provenance: source type, namespace, pod/service,
  timestamp if available, evidence_ref, and raw supporting detail.

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

## Commands

```bash
# setup
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"        # core + pytest
pip install -e ".[dev,mcp]"    # also installs the `mcp` package for the server/smoke test

# optional local secrets for Prometheus/IBM Cloud Logs tools (gitignored, loaded via python-dotenv)
cp .env.example .env            # then edit PROMETHEUS_URL / IBM_LOGS_ENDPOINT / IBM_CLOUD_API_KEY

# tests
pytest                          # whole suite
pytest tests/test_hooks.py      # single file
pytest tests/test_hooks.py::test_destructive_verbs_blocked  # single test

# run a read-only investigation snapshot (CLI, no MCP)
python -m claude_ops.main investigate --namespace si --service event-data --since-minutes 60

# run the MCP server standalone (syntax/import check; Claude Code normally launches this via .mcp.json over STDIO)
python -m claude_ops.mcp.server

# MCP smoke test (requires the `mcp` extra)
python scripts/mcp_smoke_client.py
```

There is no lint/format tooling configured in `pyproject.toml`.

## Architecture

**Two parallel entry points share the same tool layer:**
- `src/claude_ops/main.py` — a CLI (`investigate` subcommand) that calls the k8s/runbook tool functions directly and dumps a JSON snapshot for a human to paste into Claude.
- `src/claude_ops/mcp/server.py` — a `FastMCP` server (`claude-ops-investigator`) that wraps the same tool functions as MCP tools/resources/prompt for Claude Code, configured via `.mcp.json`. It calls `load_dotenv()` before importing the Prometheus/IBM Cloud Logs tool modules, so a local `.env` (copied from `.env.example`, gitignored) is picked up automatically with no secrets ever going into `.mcp.json`. Missing `.env`/env vars are not fatal — the affected tool returns a structured `validation` error instead.

**Layering (bottom to top):**
1. `tools/k8s_tools.py` — all Kubernetes access funnels through `_run_kubectl()`, which checks every kubectl verb against `hooks.py::validate_kubectl_verb` *before* invoking `subprocess.run`. This is the single choke point enforcing the read-only boundary — new k8s tool functions must go through `_run_kubectl`, never call `subprocess` directly.
2. `tools/prometheus_tools.py` — typed `prom_get_*` tools (restart counts/increase, CPU, memory, HTTP error rate, latency p95) each build a bounded PromQL string internally and call `_instant_query`/`_range_query`, which reject missing `PROMETHEUS_URL`, oversized queries, and range durations over `_MAX_RANGE_DAYS`. `prom_query_instant` is the free-form escape hatch, guarded by the same validation. None of these functions can start a port-forward or otherwise mutate anything — they only issue `GET /api/v1/query`.
3. `tools/prometheus_preflight.py` — the *only* module allowed to start a `kubectl port-forward` subprocess. `ensure_prometheus()` checks reachability first; it only spawns a port-forward if `PROMETHEUS_AUTO_PORT_FORWARD=true`, and registers the process for `atexit` cleanup. It is never called automatically by `prometheus_tools.py` or the investigation workflow — only via the explicit `prom_ensure_connection` MCP tool (owned by `incident-coordinator`, per its agent rules) or direct CLI use.
4. `tools/ibm_logs_tools.py` — read-only DataPrime (`source logs`) search against IBM Cloud Logs. Exchanges `IBM_CLOUD_API_KEY` for a short-lived IAM bearer token (in-process cache), streams `text/event-stream` query results, and never includes the API key/token in returned results or errors. `ibm_logs_search_errors`/`_probe_failures`/`_text` are typed wrappers around the generic `ibm_logs_search`/`_search` with different `text_query`/`search_kind`. Preferred over live pod logs because entries survive restarts/deployments and span all pod incarnations.
5. `tools/http_client.py` — `request_json()` is the shared HTTP layer for both `prometheus_tools.py` and `ibm_logs_tools.py`; maps timeouts/network errors/4xx/5xx onto the same `errorCategory` conventions used everywhere else instead of each tool module reimplementing it.
6. `hooks.py` — defines `ALLOWED_KUBECTL_VERBS` (get/describe/logs/top/auth/config) vs `DESTRUCTIVE_KUBECTL_VERBS` (delete/apply/patch/scale/rollout/exec/etc.) and `require_human_approval()` for a future approval gate. This is the file to touch when changing what's permitted.
7. `errors.py` — every tool returns either `ok(data)` (`{"isError": False, "data": ...}`) or a `ToolError(...).to_dict()` (`{"isError": True, "errorCategory", "isRetryable", "message", "attempted", "partialResults", "alternatives"}`). `errorCategory` is one of `transient|validation|permission|business|unknown`. All downstream code checks `result.get("isError")` rather than raising.
8. `tools/runbook_tools.py` — loads `data/runbook_index.json` and does keyword search over the markdown files in `data/runbooks/`.
9. `evidence/` — an evidence-store layer sitting between raw tool output and what the model sees, to bound context growth:
   - `raw_store.py::store_raw_evidence` writes full tool output as JSON to `artifacts/ev_<timestamp>_<hash>.json` and returns an `EvidenceRecord` (models.py) with a truncated `summary` (max 1000 chars) plus `evidence_ref`. The hash is computed over `content_type` + `metadata` + `raw` together (not raw alone) so that two different tool calls returning an identical/empty raw payload in the same second (e.g. `prom_get_http_error_rate` vs `prom_get_latency_p95` both returning an empty vector) still get distinct evidence_refs instead of silently colliding/overwriting each other's artifact.
   - `summarizers.py` turns raw tool output into compact summaries — `summarize_kubectl_result` is the generic kubectl fallback; `summarize_k8s_events` is a purpose-built summarizer for `kubectl get events` table output (parses columns by splitting on 2+ spaces since messages contain single spaces) that surfaces Warning/Unhealthy counts, top reasons, and matching objects; `summarize_prometheus_result` and `summarize_log_matches` do the equivalent for Prometheus query results and IBM Cloud Logs entries.
   - `k8s_evidence.py::store_k8s_tool_result` is the glue for k8s tools: pass it a tool result plus an optional `summarize` callable; errors pass through unchanged (the agent needs to see them directly), successes get archived and replaced with the compact record. `prometheus_tools.py` and `ibm_logs_tools.py` call `store_raw_evidence` directly instead, since their result shape/summarizer differ per tool.
   - The MCP server's `evidence_get_detail` tool is the only way to pull full raw evidence back out via `evidence_ref`; agents are instructed to reason from summaries first.
10. `schemas/incident_report_schema.py` — `INCIDENT_REPORT_SCHEMA` is a JSON Schema (validated with `jsonschema`) that the final incident report must conform to: `service`, `namespace`, `severity`, `symptoms`, `evidence` (each item needs `source`+`detail`), `likely_causes`, `ruled_out`, `recommended_next_steps`, `requires_human`, `confidence`, `unknowns`. `additionalProperties: False` throughout — reports must match this shape exactly.

**MCP server surface** (`src/claude_ops/mcp/server.py`):
- Resources: `ops://runbook-catalog`, `ops://service-catalog` (reads `data/service_catalog.json` directly).
- Tools: `k8s_list_pods`, `k8s_describe_pod`, `k8s_get_pod_logs`, `k8s_get_recent_namespace_events`, `k8s_top_pods`, `runbook_search`, `prom_query_instant`, `prom_get_pod_restart_counts`, `prom_get_pod_restart_increase`, `prom_get_pod_cpu_usage`, `prom_get_pod_memory_usage`, `prom_get_http_error_rate`, `prom_get_latency_p95`, `prom_ensure_connection`, `ibm_logs_search`, `ibm_logs_search_errors`, `ibm_logs_search_probe_failures`, `ibm_logs_search_text`, `evidence_get_detail`. All k8s tools except `k8s_list_pods`/`k8s_top_pods` route their result through `store_k8s_tool_result` for evidence archiving; all Prometheus/IBM Cloud Logs tools except `prom_ensure_connection` archive via `store_raw_evidence` directly.
- Prompt: `investigate_incident(namespace, service, since_minutes)` — encodes the read-only investigation workflow as a reusable prompt template.

**Claude Code project config** (drives agent behavior, not application code):
- `.claude/commands/investigate-incident.md` — the `/investigate-incident` slash command; requires namespace/service/symptom/since_minutes, mints an `investigation_id` and creates `runs/<investigation_id>/scratchpad/` before delegating to `incident-coordinator` (single-agent fallback only if subagents are unavailable), and maps symptom patterns (OOM, probe failures, Kafka lag, latency) to the narrowest relevant tool subset rather than always calling everything. Also documents that Prometheus connectivity is coordinator-owned (see `prom_ensure_connection` rules above) and requires a "Subagent usage audit" table in the final output.
- `.claude/agents/*.md` — the six subagents described under Architecture rules above; each file's frontmatter `tools:` list is the actual enforced allowlist for that subagent (e.g. `prometheus-analyst` has no `prom_ensure_connection`, by design — connectivity setup is coordinator-only). `incident-coordinator` and the four specialist subagents also have `Read`/`Write` to maintain per-investigation scratchpads under `runs/<investigation_id>/scratchpad/` — the coordinator persists its Structured Finding Brief to `coordinator-brief.md` (overwritten each wave); each specialist writes an immutable `wave<N>-<subagent-name>.md` per delegation with scope/tools called/key findings/evidence_refs/unknowns/decisions/handoff summary. Scratchpads hold summaries and `evidence_ref`s only, never raw data — that stays in `artifacts/`.
- `.claude/skills/incident-analysis/` and `.claude/skills/runbook-summarizer/` — forked-context skills (`context: fork`) for producing evidence-grounded reports and summarizing runbooks respectively.
- `.claude/rules/incident-output.md` and `.claude/rules/testing.md` — scoped rules mirroring the required-fields and testing conventions above.
- `.claude/settings.json` wires four read-only hooks in `.claude/hooks/*.py`: `block_unsafe_shell.py` (`PreToolUse`/`Bash`, a harness-level companion to `hooks.py::validate_kubectl_verb` for raw shell use), `audit_mcp_tool_call.py` and `audit_subagent_lifecycle.py` (`PostToolUse`/`SubagentStart`/`SubagentStop`, append JSONL to `runs/`), and `validate_final_report.py` (`Stop`, checks a final incident report has evidence_refs/Subagent usage audit/ruled_out/unknowns/a confirmed verdict). See the README's "Harness hooks" section, including how to disable them locally (`CLAUDE_OPS_HOOKS_DISABLED=1` or `disableAllHooks` in `.claude/settings.local.json`).

**Data files**: `data/service_catalog.json` (known services/namespaces/labels/production risk), `data/runbook_index.json` (runbook catalog metadata), `data/runbooks/*.md` (runbook bodies — currently `oom-restart`, `liveness-probe-failure`, `kafka-commit-rate-low`).

**Other non-Python config**: `k8s/readonly-rbac.yaml` — a reference `ServiceAccount`/`Role`/`RoleBinding` manifest scoping a cluster identity to `get/list/watch` on pods/logs/events/services/configmaps/deployments/replicasets/statefulsets/daemonsets/HPAs plus `metrics.k8s.io` pods, in namespace `si` — the RBAC-level mirror of the `ALLOWED_KUBECTL_VERBS` boundary enforced in `hooks.py`.

**Generated/output directories** (not source): `artifacts/` (evidence store JSON), `runs/` (saved investigation snapshots, MCP/subagent audit JSONL, and per-investigation `runs/<investigation_id>/scratchpad/` coordinator/subagent scratchpads), `reports/` (generated incident reports).
