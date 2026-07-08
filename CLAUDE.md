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

## Commands

```bash
# setup
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"        # core + pytest
pip install -e ".[dev,mcp]"    # also installs the `mcp` package for the server/smoke test

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
- `src/claude_ops/mcp/server.py` — a `FastMCP` server (`claude-ops-investigator`) that wraps the same tool functions as MCP tools/resources/prompt for Claude Code, configured via `.mcp.json`.

**Layering (bottom to top):**
1. `tools/k8s_tools.py` — all Kubernetes access funnels through `_run_kubectl()`, which checks every kubectl verb against `hooks.py::validate_kubectl_verb` *before* invoking `subprocess.run`. This is the single choke point enforcing the read-only boundary — new k8s tool functions must go through `_run_kubectl`, never call `subprocess` directly.
2. `hooks.py` — defines `ALLOWED_KUBECTL_VERBS` (get/describe/logs/top/auth/config) vs `DESTRUCTIVE_KUBECTL_VERBS` (delete/apply/patch/scale/rollout/exec/etc.) and `require_human_approval()` for a future approval gate. This is the file to touch when changing what's permitted.
3. `errors.py` — every tool returns either `ok(data)` (`{"isError": False, "data": ...}`) or a `ToolError(...).to_dict()` (`{"isError": True, "errorCategory", "isRetryable", "message", "attempted", "partialResults", "alternatives"}`). `errorCategory` is one of `transient|validation|permission|business|unknown`. All downstream code checks `result.get("isError")` rather than raising.
4. `tools/runbook_tools.py` — loads `data/runbook_index.json` and does keyword search over the markdown files in `data/runbooks/`.
5. `evidence/` — an evidence-store layer sitting between raw kubectl output and what the model sees, to bound context growth:
   - `raw_store.py` writes full tool output as JSON to `artifacts/ev_<timestamp>_<hash>.json` and returns an `EvidenceRecord` (models.py) with a truncated `summary` (max 1000 chars) plus `evidence_ref`.
   - `summarizers.py` turns raw kubectl text into compact summaries — `summarize_kubectl_result` is the generic fallback; `summarize_k8s_events` is a purpose-built summarizer for `kubectl get events` table output (parses columns by splitting on 2+ spaces since messages contain single spaces) that surfaces Warning/Unhealthy counts, top reasons, and matching objects.
   - `k8s_evidence.py::store_k8s_tool_result` is the glue: pass it a tool result plus an optional `summarize` callable; errors pass through unchanged (the agent needs to see them directly), successes get archived and replaced with the compact record.
   - The MCP server's `evidence_get_detail` tool is the only way to pull full raw evidence back out via `evidence_ref`; agents are instructed to reason from summaries first.
6. `schemas/incident_report_schema.py` — `INCIDENT_REPORT_SCHEMA` is a JSON Schema (validated with `jsonschema`) that the final incident report must conform to: `service`, `namespace`, `severity`, `symptoms`, `evidence` (each item needs `source`+`detail`), `likely_causes`, `ruled_out`, `recommended_next_steps`, `requires_human`, `confidence`, `unknowns`. `additionalProperties: False` throughout — reports must match this shape exactly.

**MCP server surface** (`src/claude_ops/mcp/server.py`):
- Resources: `ops://runbook-catalog`, `ops://service-catalog` (reads `data/service_catalog.json` directly).
- Tools: `k8s_list_pods`, `k8s_describe_pod`, `k8s_get_pod_logs`, `k8s_get_recent_namespace_events`, `k8s_top_pods`, `runbook_search`, `evidence_get_detail`. All k8s tools except `k8s_list_pods`/`k8s_top_pods` route their result through `store_k8s_tool_result` for evidence archiving.
- Prompt: `investigate_incident(namespace, service, since_minutes)` — encodes the read-only investigation workflow as a reusable prompt template.

**Claude Code project config** (drives agent behavior, not application code):
- `.claude/commands/investigate-incident.md` — the `/investigate-incident` slash command; requires namespace/service/symptom/since_minutes and maps symptom patterns (OOM, probe failures, Kafka lag, latency) to the narrowest relevant tool subset rather than always calling everything.
- `.claude/skills/incident-analysis/` and `.claude/skills/runbook-summarizer/` — forked-context skills (`context: fork`) for producing evidence-grounded reports and summarizing runbooks respectively.
- `.claude/rules/incident-output.md` and `.claude/rules/testing.md` — scoped rules mirroring the required-fields and testing conventions above.

**Data files**: `data/service_catalog.json` (known services/namespaces/labels/production risk), `data/runbook_index.json` (runbook catalog metadata), `data/runbooks/*.md` (runbook bodies — currently `oom-restart`, `liveness-probe-failure`, `kafka-commit-rate-low`).

**Generated/output directories** (not source): `artifacts/` (evidence store JSON), `runs/` (saved investigation snapshots), `reports/` (generated incident reports).
