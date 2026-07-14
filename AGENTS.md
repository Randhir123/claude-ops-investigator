# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project Overview

**Claude Ops Investigator** is a context-aware Kubernetes incident investigation assistant. At its core is an MCP (Model Context Protocol) server exposing narrow, read-only investigation tools, resources, and a reusable prompt. It helps engineers investigate production incidents safely by combining live cluster signals, Prometheus metrics, IBM Cloud Logs, runbook knowledge, and structured incident reporting.

The MCP server is usable through multiple, independent agent harnesses layered on top of it — currently `.claude/` and `.bob/`. Neither is "the" default; both talk to the same tool layer and are documented in parallel throughout this file.

### Core Philosophy

- **Read-only by default**: No destructive operations without explicit human approval
- **Evidence-grounded**: Every claim must cite an `evidence_ref` from tool results
- **Symptom-driven**: Investigations start with a specific reported symptom, not generic health checks
- **Narrow, typed tools**: Prefer specific tools over generic shell commands
- **Safety gates**: Multiple layers prevent accidental production mutations

### Technology Stack

- **Language**: Python 3.10+
- **MCP Server**: FastMCP for exposing tools, resources, and prompts
- **Kubernetes**: Read-only kubectl operations via typed Python wrappers
- **Observability**: Prometheus (metrics), IBM Cloud Logs (historical logs)
- **Testing**: pytest
- **Environment**: python-dotenv for local configuration

## Architecture

### Component Structure

```
claude-ops-investigator/
├── src/claude_ops/           # Core application code
│   ├── mcp/                  # MCP server implementation
│   │   └── server.py         # Tool/resource/prompt definitions
│   ├── tools/                # Read-only investigation tools
│   │   ├── k8s_tools.py      # Kubernetes operations
│   │   ├── prometheus_tools.py
│   │   ├── ibm_logs_tools.py
│   │   └── runbook_tools.py
│   ├── evidence/             # Evidence storage and summarization
│   │   ├── k8s_evidence.py   # Evidence archival
│   │   ├── raw_store.py      # Raw data persistence
│   │   └── summarizers.py    # Compact summaries
│   ├── schemas/              # Structured output schemas
│   │   └── incident_report_schema.py
│   └── hooks.py              # Application-level safety gates
├── .claude/                  # Claude Code harness (subagent-based)
│   ├── agents/               # Subagent definitions
│   │   ├── incident-coordinator.md
│   │   ├── k8s-evidence-collector.md
│   │   ├── prometheus-analyst.md
│   │   ├── log-analyst.md
│   │   ├── runbook-analyst.md
│   │   └── incident-reporter.md
│   ├── commands/             # Slash commands
│   │   └── investigate-incident.md
│   ├── hooks/                # Harness-level safety hooks
│   │   ├── block_unsafe_shell.py
│   │   ├── audit_mcp_tool_call.py
│   │   ├── audit_subagent_lifecycle.py
│   │   └── validate_final_report.py
│   ├── rules/                # Project-specific rules
│   └── skills/               # Reusable investigation patterns
├── .bob/                     # Bob Shell harness (orchestrator/custom-mode-based)
│   ├── mcp.json              # MCP server configuration
│   ├── custom_modes.yaml     # Mode definitions: orchestrator + 5 specialist modes + fix-proposer
│   ├── commands/              # Slash commands
│   │   ├── investigate-incident.md
│   │   └── propose-fix.md    # Read-only investigation + autonomous fix proposal
│   ├── rules-orchestrator/    # Orchestrator mode rules (workflow, brief, routing)
│   │   └── 03-fix-proposal-gate.md  # Gate applied only by /propose-fix
│   ├── rules-k8s-evidence-collector/
│   ├── rules-prometheus-analyst/
│   ├── rules-log-analyst/
│   ├── rules-runbook-analyst/
│   ├── rules-incident-reporter/
│   ├── rules-fix-proposer/    # Fix-proposer mode rules (used only by /propose-fix)
│   │   └── 00-rules.md
│   ├── rules-ops-investigator/  # Shared operational rules
│   │   ├── read-only-safety.md
│   │   ├── evidence-ref-discipline.md
│   │   ├── symptom-driven-tool-choice.md
│   │   ├── prometheus-connectivity.md
│   │   ├── unrelated-log-noise.md
│   │   └── scratchpad-and-briefs.md
│   └── skills/                # Advanced-mode fallback (single-agent, no delegation)
│       └── investigate-incident/
│           └── SKILL.md
├── runs/                     # Investigation runs (Bob harness)
│   └── <investigation_id>/   # Format: namespace-service-YYYYMMDDTHHMMSSZ
│       ├── scratchpad/
│       │   ├── coordinator-brief.md  # Investigation progress
│       │   └── workflow-audit.md     # Tool call log
│       └── report.md         # Final incident report
├── reports/                  # Optional report copies
│   └── <investigation_id>.md # Copy of final report
├── data/                     # Static reference data
│   ├── runbooks/             # Incident runbooks
│   ├── runbook_index.json    # Runbook catalog
│   └── service_catalog.json  # Known services
├── docs/                     # Documentation
│   └── bob-harness.md        # Bob Shell harness guide
├── tests/                    # Test suite
└── artifacts/                # Evidence storage (gitignored)
```

### Investigation Workflows

Both harnesses implement the same coordinator/specialist shape — a top-level
role decomposes the incident and delegates to narrowly-scoped specialists,
then hands off to a final reporting role — using each harness's own
delegation mechanism.

#### Claude Code harness (`.claude/`)

1. **incident-coordinator**: Top-level coordinator subagent
   - Maintains investigation state in `runs/<investigation_id>/scratchpad/`
   - Delegates to specialist subagents based on symptom
   - Aggregates findings into structured brief
   - Never gathers evidence directly
   - Is the only subagent allowed to call `prom_ensure_connection`, and only
     when the user explicitly asked for Prometheus-backed investigation or
     metrics are necessary to answer the symptom

2. **Specialist subagents**:
   - **k8s-evidence-collector**: Pod status, logs, events, resource usage
   - **prometheus-analyst**: Metrics (restarts, CPU, memory, errors, latency)
   - **log-analyst**: Historical IBM Cloud Logs across pod restarts
   - **runbook-analyst**: Match symptoms to known incident patterns

3. **incident-reporter**: Final synthesis
   - Produces schema-valid incident report
   - Cites all evidence refs
   - Lists ruled-out causes and unknowns

See `.claude/agents/incident-coordinator.md` for the full policy.

#### Bob harness (`.bob/`)

1. **orchestrator** (override of Bob's built-in Orchestrator mode): Top-level
   coordinator
   - Maintains a Structured Finding Brief on disk at
     `runs/<investigation_id>/scratchpad/coordinator-brief.md`
   - Delegates to specialist modes as subtasks based on symptom
   - Never gathers evidence directly (no `mcp` tool access on purpose)
   - Runs a **mandatory wave 0 Prometheus preflight** before any other
     delegation: it delegates to `prometheus-analyst` to call
     `prom_ensure_connection` and confirm reachability. If Prometheus is
     unreachable, the investigation stops immediately — no further
     delegation, no partial report; see
     `.bob/rules-orchestrator/00-workflow.md` and
     `.bob/rules-ops-investigator/prometheus-connectivity.md` for the full
     hard-gate policy

2. **Specialist modes**:
   - **k8s-evidence-collector**: Pod status, logs, events, resource usage
   - **prometheus-analyst**: Metrics (restarts, CPU, memory, errors,
     latency); also owns the wave 0 preflight call
   - **log-analyst**: Historical IBM Cloud Logs across pod restarts
   - **runbook-analyst**: Match symptoms to known incident patterns

3. **incident-reporter**: Final synthesis
   - Produces schema-valid incident report
   - Cites all evidence refs
   - Lists ruled-out causes and unknowns

4. **fix-proposer**: Autonomous fix-proposal mode, invoked only via
   `/propose-fix` after the fix-proposal gate passes — never part of the
   default `/investigate-incident` flow. Works only in the target service's
   own existing local git checkout (never clones), requires a clean working
   tree, and opens a draft-only PR. See `.bob/rules-fix-proposer/00-rules.md`
   for the full policy. No Claude Code equivalent exists yet.

Entry point: `.bob/commands/investigate-incident.md`. A single-agent
Advanced-mode fallback (`.bob/skills/investigate-incident/`) exists for Bob
installations without custom-mode support.

#### The `/propose-fix` flow

`.bob/commands/propose-fix.md` runs the same investigation as
`/investigate-incident` (or loads an existing `investigation_id`'s report),
then applies `.bob/rules-orchestrator/03-fix-proposal-gate.md`: orchestrator
checks whether at least one `likely_causes` entry cites a named
application-code location, as opposed to an infra/operational finding. If
the gate isn't met, the flow stops there — no fix, no PR. If it is met,
orchestrator delegates once to `fix-proposer`, which locates and verifies
the service's local checkout (never cloning), confirms a clean working tree,
locates the implicated code, proposes the smallest defensible fix, then
branches, commits, pushes, and opens a draft-only PR with an AI-disclosure
line and human-review checklist. `dry_run=true` stops fix-proposer after it
narrates the proposed fix to a scratchpad file, with no git/GitHub actions
taken.

### Evidence Model

Tools return **compact summaries + evidence_ref**:
- Summary: Enough to reason about findings
- `evidence_ref`: Pointer to full raw data in `artifacts/`
- Use `evidence_get_detail(evidence_ref)` only when summary is insufficient

## Building and Running

### Initial Setup

```bash
# Clone and enter project
cd claude-ops-investigator

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install with dev dependencies
pip install -e ".[dev]"

# Optional: Install MCP dependencies for smoke testing
pip install -e ".[dev,mcp]"
```

### Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
# Edit .env with your values:
# - PROMETHEUS_URL (optional)
# - PROMETHEUS_AUTO_PORT_FORWARD (optional, default false)
# - IBM_LOGS_ENDPOINT (optional)
# - IBM_CLOUD_API_KEY (optional)
```

**Important**: `.env` is gitignored. Never commit secrets to `.mcp.json` or version control.

### Verify Kubernetes Access

```bash
kubectl config current-context
kubectl auth can-i get pods -n <namespace>
kubectl auth can-i get pods/log -n <namespace>
```

### Running Investigations

**CLI Mode** (standalone snapshot):
```bash
python -m claude_ops.main investigate \
  --namespace si \
  --service event-data \
  --since-minutes 60
```

**Claude Code Mode** (interactive with subagents):
```
/investigate-incident namespace=si service=event-data symptom="readiness probe failures during recent rollout" since_minutes=60
```

**Bob Shell Mode** (interactive with orchestrator/custom modes):
```
/investigate-incident namespace=si service=event-data symptom="readiness probe failures during recent rollout" since_minutes=60
```
(This is `.bob/commands/investigate-incident.md` — distinct from the MCP
server's `investigate_incident` prompt, which any MCP client can also call
directly.)

### Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_k8s_tools.py

# Run with verbose output
pytest -v

# MCP server smoke test (requires mcp extra)
python scripts/mcp_smoke_client.py
```

### MCP Server

**Manual start** (for debugging):
```bash
python -m claude_ops.mcp.server
```

**Harness integration**: Each harness has its own MCP server config that launches the server automatically over STDIO — the project-root `.mcp.json` for the Claude Code harness, `.bob/mcp.json` for the Bob harness.

## Development Conventions

### Safety Rules (Non-Negotiable)

1. **Read-only by default**: All tools must be read-only unless explicitly approved
2. **Blocked operations**:
   - `kubectl delete`, `apply`, `patch`, `scale`, `rollout restart`, `exec`
   - `helm upgrade`
   - Any production mutation without human approval

3. **Safety gates**:
   - `src/claude_ops/hooks.py::validate_kubectl_verb`: Application-level gate
   - `.claude/hooks/block_unsafe_shell.py`: Harness-level gate for raw Bash
   - Both must pass for any kubectl/helm command

### Tool Design Patterns

**Preferred**: Narrow, typed tools
```python
# Good: Specific, bounded, typed
prom_get_pod_restart_increase(namespace, service, since_minutes)

# Avoid: Generic, unbounded
prom_query_instant("rate(kube_pod_container_status_restarts_total[5m])")
```

**Evidence handling**:
```python
# Tools return compact summary + evidence_ref
# Full raw data is stored externally in artifacts/
result = {
    "isError": False,
    "summary": "3 pods restarted in last 60m",
    "evidence_ref": "k8s.pod_describe.20260709T054200Z.abc123",
    "artifact_path": "artifacts/k8s.pod_describe.20260709T054200Z.abc123.json"
}

# Archive raw data to artifacts/ (not returned in conversation)
store_k8s_tool_result(
    content_type="k8s.pod_describe",
    result=result,
    label="describe pod si/event-data-xyz",
    metadata={"namespace": "si", "pod_name": "event-data-xyz"}
)
```

**Error handling**:
```python
# Structured errors, never silent failures
# Valid errorCategory values: "transient", "validation", "permission", "business", "unknown"
{
    "isError": True,
    "errorCategory": "validation",  # or "transient", "permission", "business", "unknown"
    "isRetryable": False,
    "message": "PROMETHEUS_URL not configured",
    "attempted": {"namespace": "si", "service": "event-data"},
    "partialResults": None,
    "alternatives": ["Set PROMETHEUS_URL in .env"]
}
```

### Code Style

- **Type hints**: Use for all function signatures
- **Docstrings**: Required for all public functions and tools
- **Error messages**: Be specific and actionable
- **Logging**: Use structured logging (not implemented yet, but planned)
- **Testing**: Write tests for new tools and safety gates

### Adding New Tools

1. **Implement in appropriate module** (`tools/k8s_tools.py`, etc.)
2. **Add safety validation** if it touches Kubernetes/Prometheus
3. **Return structured result** with `isError`, `summary`, `evidence_ref`
4. **Register in MCP server** (`src/claude_ops/mcp/server.py`)
5. **Write tests** (`tests/test_<module>.py`)
6. **Update documentation** (README.md, relevant agent files)

Example:
```python
# In tools/k8s_tools.py
def get_pod_status(namespace: str, pod_name: str) -> dict:
    """Get current status of a specific pod.
    
    Args:
        namespace: Kubernetes namespace
        pod_name: Exact pod name from k8s_list_pods
        
    Returns:
        Structured result with isError, summary, evidence_ref
    """
    # Implementation with safety checks
    pass

# In mcp/server.py
@mcp.tool()
def k8s_get_pod_status(namespace: str, pod_name: str) -> str:
    """Get current status of a specific pod."""
    result = get_pod_status(namespace, pod_name)
    return _json(store_k8s_tool_result(...))
```

### Subagent Development (Claude Code harness)

When creating or modifying subagents in `.claude/agents/`:

1. **Scope clearly**: Each subagent has a narrow, well-defined responsibility
2. **No context inheritance**: Pass all needed context explicitly via task prompt
3. **Structured Finding Brief**: Include in every delegation
4. **Scratchpad discipline**: Write findings to assigned path, never raw logs
5. **Evidence preservation**: Always cite `evidence_ref` in findings

### Custom Mode Development (Bob harness)

When creating or modifying modes in `.bob/custom_modes.yaml` and their
matching `.bob/rules-{slug}/` directories:

1. **Scope clearly**: Each mode has a narrow, well-defined responsibility;
   `groups` in `custom_modes.yaml` sets its coarse read/edit/mcp access, and
   `rules-{slug}/00-rules.md` narrows it further by instruction (Bob's `mcp`
   group has no per-tool allowlist, so the narrower list is a soft rule you
   must still enforce for yourself)
2. **No context inheritance**: A Bob subtask doesn't inherit the parent
   conversation — pass all needed context explicitly via the subtask prompt
3. **Structured Finding Brief**: Include in every delegation (see
   `.bob/rules-orchestrator/01-structured-finding-brief.md`)
4. **Scratchpad discipline**: Write findings to the assigned
   `runs/<investigation_id>/scratchpad/wave<N>-<mode-slug>.md` path, never
   raw logs
5. **Evidence preservation**: Always cite `evidence_ref` in findings

### Hook Development

Hooks in `.claude/hooks/` are **read-only audit/gate scripts**:

- **No external calls**: Never call Kubernetes, Prometheus, IBM Cloud, or Claude API
- **Stdin only**: Read JSON payload from stdin
- **Stdout for decisions**: Write JSON decision to stdout
- **Fail open**: On malformed input, return 0 (allow)
- **Disable flag**: Respect `CLAUDE_OPS_HOOKS_DISABLED=1`

## Key Workflows

### Investigation Flow

1. **Setup**: Mint `investigation_id`, create scratchpad directory
2. **Context**: Read service catalog and runbook catalog
3. **Discovery**: List pods for target service only
4. **Evidence gathering**: Use symptom-appropriate tools
5. **Analysis**: Reason from summaries, fetch detail only when needed
6. **Reporting**: Produce structured, evidence-grounded report

### Symptom-to-Tool Mapping

- **OOM/restarts/crash-loop**: `k8s_list_pods`, `k8s_describe_pod`, `k8s_get_recent_namespace_events`, `prom_get_pod_restart_increase`, `prom_get_pod_memory_usage`
- **Probe failures**: `k8s_get_recent_namespace_events`, `k8s_describe_pod`, `ibm_logs_search_probe_failures`, `runbook_search`
- **Latency/errors**: `prom_get_latency_p95`, `prom_get_http_error_rate`, `ibm_logs_search_errors`
- **Kafka lag**: `runbook_search`, `ibm_logs_search_text`, `k8s_list_pods`

### Prometheus Connectivity

The shared principle both harnesses agree on: **gaps are gaps, never zeros,
never silently skipped.** Unreachable or unconfigured Prometheus must be
reported as a gap/unknown, never treated as "no metrics" or a healthy
service. `prom_ensure_connection` may start a `kubectl port-forward` only if
`PROMETHEUS_AUTO_PORT_FORWARD=true`.

Each harness enforces *who* may call `prom_ensure_connection`, and *when*,
its own way:

- **Claude Code harness**: soft/instructional — see
  `.claude/agents/incident-coordinator.md` for the exact policy.
- **Bob harness**: a hard, non-bypassable gate — see
  `.bob/rules-orchestrator/00-workflow.md` and
  `.bob/rules-ops-investigator/prometheus-connectivity.md` for the wave-0
  preflight policy.

## Common Pitfalls

### ❌ Don't Do This

```python
# Don't: Generic shell commands
execute_command("kubectl get pods -n si | grep event-data")

# Don't: Assume tool success
pods = k8s_list_pods(namespace, service)
for pod in pods["data"]:  # Crashes if isError=True
    ...

# Don't: Lose evidence refs
summary = "3 pods restarted"  # Where's the evidence_ref?

# Don't: Treat missing config as zero
if not prometheus_url:
    return {"restarts": 0}  # Wrong! This is an unknown, not zero
```

### ✅ Do This Instead

```python
# Do: Use typed tools
pods = k8s_list_pods(namespace=namespace, label_selector=f"app={service}")

# Do: Check for errors
if pods.get("isError"):
    return handle_error(pods)

# Do: Preserve evidence refs
finding = {
    "summary": "3 pods restarted in last 60m",
    "evidence_ref": pods["evidence_ref"]
}

# Do: Report gaps explicitly
if not prometheus_url:
    return {
        "isError": True,
        "errorCategory": "config",
        "message": "PROMETHEUS_URL not configured",
        "alternatives": ["Set PROMETHEUS_URL in .env"]
    }
```

## Incident Report Schema

All investigations must produce a report matching `INCIDENT_REPORT_SCHEMA`:

```python
{
    "service": str,
    "namespace": str,
    "severity": "low" | "medium" | "high" | "critical" | "unclear",
    "symptoms": [str],
    "evidence": [
        {
            "source": str,
            "detail": str,
            "timestamp": str | None
        }
    ],
    "likely_causes": [str],
    "ruled_out": [str],  # Required: What evidence excluded
    "recommended_next_steps": [str],
    "requires_human": bool,
    "confidence": "low" | "medium" | "high" | "unclear",
    "unknowns": [str]  # Required: What couldn't be confirmed
}
```

## Resources

- **README.md**: User-facing documentation, quick start, safety rules
- **CLAUDE.md**: Claude Code project instructions (if exists)
- **.claude/agents/**: Subagent definitions and responsibilities
- **.claude/commands/**: Slash command specifications
- **.bob/custom_modes.yaml**: Orchestrator and specialist mode definitions
- **.bob/rules-{slug}/**: Per-mode rules, plus shared rules in
  `.bob/rules-ops-investigator/`
- **.bob/commands/**: Slash command specifications
- **data/runbooks/**: Known incident patterns and remediation steps
- **tests/**: Test suite with examples of tool usage

## Getting Help

- **Tool documentation**: See docstrings in `src/claude_ops/mcp/server.py`
- **Subagent specs**: Read `.claude/agents/<subagent-name>.md`
- **Custom mode specs**: Read `.bob/custom_modes.yaml` and
  `.bob/rules-<slug>/`
- **Safety rules**: Review `.claude/hooks/`, `.bob/rules-ops-investigator/`,
  and `src/claude_ops/hooks.py`
- **Examples**: Check `tests/` for tool usage patterns
