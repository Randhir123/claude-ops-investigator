# Bob Shell Harness for Claude Ops Investigator

## Overview

The Bob Shell harness is a parallel investigation interface to the existing Claude Code harness. Both harnesses share the same core Python application and MCP server, but use different configuration and workflow patterns.

## Architecture

```
claude-ops-investigator/
├── .claude/              # Claude Code harness (subagent-based)
├── .bob/                 # Bob Shell harness (skill-based)
├── src/claude_ops/       # Shared core application
├── data/                 # Shared runbooks and catalogs
├── tests/                # Shared test suite
└── docs/                 # Documentation
```

### Key Differences

| Aspect | Claude Code | Bob Shell |
|--------|-------------|-----------|
| Configuration | `.claude/` | `.bob/` |
| Workflow | Coordinator + specialist subagents | Single agent + skill |
| State management | Scratchpad per subagent | Single scratchpad |
| MCP server | Same (`src/claude_ops/mcp/server.py`) | Same |
| Tools | Same MCP tools | Same MCP tools |
| Safety gates | Harness hooks + app hooks | App hooks only |

## Setup

### Prerequisites

- Python 3.10+
- Kubernetes cluster access (read-only)
- Bob Shell installed and configured

### Installation

1. **Clone and enter the repository**
   ```bash
   cd claude-ops-investigator
   ```

2. **Create virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -e ".[dev]"
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your values:
   # - PROMETHEUS_URL (optional)
   # - PROMETHEUS_AUTO_PORT_FORWARD (optional, default false)
   # - IBM_LOGS_ENDPOINT (optional)
   # - IBM_CLOUD_API_KEY (optional)
   ```

   **Important**: `.env` is gitignored. Never commit secrets to `.bob/mcp.json` or version control.

5. **Verify Kubernetes access**
   ```bash
   kubectl config current-context
   kubectl auth can-i get pods -n <namespace>
   kubectl auth can-i get pods/log -n <namespace>
   ```

### Bob Configuration

Bob Shell reads `.bob/mcp.json` to launch the MCP server. The configuration is already set up to use the shared MCP server at `src/claude_ops/mcp/server.py`.

No additional Bob-specific configuration is needed beyond the standard `.env` file.

## Usage

### Starting Bob

From the repository root:

```bash
source .venv/bin/activate  # Activate virtual environment
bob  # Start Bob Shell
```

Bob will automatically:
1. Load `.bob/mcp.json` configuration
2. Launch the MCP server via STDIO
3. Connect to the claude-ops-investigator MCP tools
4. Load skills from `.bob/skills/`
5. Apply rules from `.bob/rules-ops-investigator/`

### Using the Investigate Incident Skill

The primary workflow is the `investigate-incident` skill:

```
Use the investigate-incident skill.

namespace=si
service=multi-system-processor
symptom="readiness probe failures during recent rollout"
since_minutes=60
```

### Custom Mode (Optional)

If your Bob Shell version supports custom modes, you can activate the `ops-investigator` mode:

```
/mode ops-investigator
```

This mode:
- Focuses on read-only incident investigation
- Prefers MCP tools over raw shell commands
- Requires evidence_refs in all findings
- Enforces structured incident reports

## Test Prompt

Use this prompt to verify the Bob harness is working correctly:

```
Use the investigate-incident skill.

namespace=si
service=multi-system-processor
symptom="readiness probe failures during recent rollout"
since_minutes=60

Return:
1. Incident report
2. Evidence table with evidence_refs
3. Ruled out
4. Unknowns
5. Workflow audit
6. Clear statement whether the symptom is confirmed for the requested service/window
```

### Acceptance Criteria

The investigation should:

✅ **Use claude-ops-investigator MCP tools exclusively**
- `k8s_list_pods`, `k8s_describe_pod`, `k8s_get_recent_namespace_events`
- `prom_get_pod_restart_increase`, `prom_get_pod_memory_usage`
- `ibm_logs_search_probe_failures`, `ibm_logs_search_errors`
- `runbook_search`

✅ **Not use raw kubectl, helm, or shell commands**
- No `kubectl get pods -n si | grep multi-system-processor`
- No `kubectl describe pod ...`
- No generic `Bash` tool calls for investigation

✅ **Preserve evidence_refs in all findings**
- Every claim cites an `evidence_ref`
- Evidence table includes all refs
- Refs are retrievable via `evidence_get_detail`

✅ **Use `prom_get_pod_restart_increase` for incident-window restart checks**
- Not just `prom_get_pod_restart_counts` (cumulative)
- Sized to the `since_minutes` window

✅ **Treat no metric coverage as gap, not zero**
- Missing `PROMETHEUS_URL` → unknown, not "no restarts"
- Unreachable Prometheus → gap, not "healthy"
- No HTTP error rate metric → gap, not "0% errors"

✅ **Keep unrelated log errors separate from readiness root cause**
- License validation errors → background/unrelated
- Fulfillment timeouts → background/unrelated
- Only probe/health/termination errors are related

✅ **State whether symptom is confirmed**
- Clear verdict: "Symptom confirmed for multi-system-processor in si over the last 60 minutes" OR "Symptom not confirmed..."
- Supporting evidence for verdict

✅ **Produce schema-valid incident report**
- Matches `INCIDENT_REPORT_SCHEMA` in `src/claude_ops/schemas/incident_report_schema.py`
- Includes all required fields: service, namespace, severity, symptoms, evidence, likely_causes, ruled_out, recommended_next_steps, requires_human, confidence, unknowns

✅ **List ruled-out causes with evidence**
- Not just "OOM ruled out"
- "OOM ruled out (evidence: prom.memory_usage.xyz shows 45% usage, well below limit)"

✅ **List unknowns/gaps explicitly**
- Missing configuration
- Unreachable services
- Insufficient evidence
- Gaps in metric coverage

## Workflow Patterns

### Single-Agent Investigation

Bob uses a single-agent pattern (no subagents), so the workflow is:

1. **Setup**
   - Mint `investigation_id`
   - Create `runs/<investigation_id>/scratchpad.md`

2. **Context**
   - Read `ops://service-catalog` resource
   - Read `ops://runbook-catalog` resource

3. **Discovery**
   - `k8s_list_pods` with `label_selector="app={service}"`

4. **Evidence Gathering**
   - Choose tools based on symptom (see `.bob/rules-ops-investigator/symptom-driven-tool-choice.md`)
   - Preserve `evidence_ref` from each tool
   - Update scratchpad after each tool call

5. **Analysis**
   - Reason from summaries first
   - Call `evidence_get_detail` only when needed
   - Correlate evidence across tools

6. **Reporting**
   - Produce structured incident report
   - Include evidence table with refs
   - List ruled-out causes
   - List unknowns/gaps
   - State clear verdict

### Scratchpad Management

Bob maintains investigation state in `runs/<investigation_id>/scratchpad/`:

```
runs/<investigation_id>/
├── scratchpad/
│   ├── coordinator-brief.md      # Investigation progress and findings
│   └── workflow-audit.md          # Tools called, decisions made
└── report.md                      # Final incident report
```

**coordinator-brief.md** tracks investigation progress:
```markdown
# Investigation Brief: si-multi-system-processor-20260709T084017Z

## Investigation Scope
- Namespace: si
- Service: multi-system-processor
- Symptom: readiness probe failures during recent rollout
- Time window: last 60 minutes

## Context
[Service catalog, runbook catalog]

## Findings
[Pod discovery, Kubernetes evidence, metrics, logs, runbooks]

## Analysis
[Likely causes, ruled out, unknowns]
```

**workflow-audit.md** logs all tool calls:
```markdown
# Workflow Audit: si-multi-system-processor-20260709T084017Z

## Tool Calls (Detailed)
- Tool name, parameters, status, evidence_ref
- Key findings from each tool
- Decisions made based on evidence
```

See `.bob/rules-ops-investigator/scratchpad-and-briefs.md` for detailed guidance.

## Troubleshooting

### MCP Server Not Starting

**Symptom**: Bob reports "MCP server failed to start"

**Solutions**:
1. Verify virtual environment is activated: `source .venv/bin/activate`
2. Check Python path: `which python` should point to `.venv/bin/python`
3. Verify dependencies: `pip install -e ".[dev]"`
4. Test server manually: `python -m claude_ops.mcp.server`

### Missing Environment Variables

**Symptom**: Tools return "PROMETHEUS_URL not configured" or "IBM_CLOUD_API_KEY not configured"

**Solutions**:
1. Create `.env` file: `cp .env.example .env`
2. Edit `.env` with your values
3. Restart Bob to pick up new environment
4. Note: Missing config is reported as a gap/unknown, not an error

### Kubernetes Access Denied

**Symptom**: Tools return "permission denied" or "forbidden"

**Solutions**:
1. Verify cluster context: `kubectl config current-context`
2. Check permissions: `kubectl auth can-i get pods -n <namespace>`
3. Ensure read-only RBAC is configured (see `k8s/readonly-rbac.yaml`)
4. Contact cluster admin if permissions are insufficient

### Evidence Refs Not Found

**Symptom**: `evidence_get_detail` returns "evidence_ref not found"

**Solutions**:
1. Verify `evidence_ref` is from a tool call in this investigation
2. Check `artifacts/` directory exists and is writable
3. Ensure investigation_id matches the current investigation
4. Evidence refs are session-specific and don't persist across Bob restarts

## Comparison with Claude Code Harness

### When to Use Bob

- Single-agent investigations
- Simpler workflow without subagent coordination
- Direct tool usage without delegation
- Skill-based investigation patterns

### When to Use Claude Code

- Complex investigations requiring specialist subagents
- Coordinator/specialist delegation pattern
- Multiple waves of evidence gathering
- Structured Finding Brief maintenance across subagents

### Shared Components

Both harnesses use:
- Same MCP server (`src/claude_ops/mcp/server.py`)
- Same MCP tools (k8s, Prometheus, IBM Cloud Logs, runbooks)
- Same evidence storage (`artifacts/`)
- Same incident report schema
- Same safety rules (read-only, evidence-grounded)

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_k8s_tools.py

# Run with verbose output
pytest -v

# MCP server smoke test
python scripts/mcp_smoke_client.py
```

### Adding New Rules

1. Create rule file in `.bob/rules-ops-investigator/`
2. Add rule to `.bob/custom_modes.yaml` (if using custom modes)
3. Document rule in this file
4. Test with investigation workflow

### Adding New Skills

1. Create skill directory in `.bob/skills/`
2. Add `SKILL.md` with inputs, rules, workflow, output
3. Test skill with sample investigation
4. Document in this file

## Resources

- **README.md**: User-facing documentation, quick start, safety rules
- **AGENTS.md**: Repo-wide guidance for agents
- **.bob/skills/investigate-incident/SKILL.md**: Investigation skill specification
- **.bob/rules-ops-investigator/**: Operational rules for investigations
- **src/claude_ops/mcp/server.py**: MCP tool documentation
- **tests/**: Test suite with tool usage examples

## Support

For issues or questions:
1. Check this documentation first
2. Review `.bob/rules-ops-investigator/` for operational guidance
3. Check `AGENTS.md` for architecture and development conventions
4. Review test suite in `tests/` for tool usage examples
5. Consult MCP tool docstrings in `src/claude_ops/mcp/server.py`
