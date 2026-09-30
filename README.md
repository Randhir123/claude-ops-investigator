# Claude Ops Investigator

An MCP-based Kubernetes incident investigation tool with two supported agent harnesses — Claude Code and IBM Bob Shell — combining live cluster signals, Prometheus, log search, runbooks, evidence memory, and structured incident reports.

Claude Ops Investigator helps engineers investigate Kubernetes incidents safely by combining read-only operational tools, external evidence storage, compact investigation memory, and human-controlled remediation boundaries.

The goal is not to give an AI unrestricted production access. The goal is to expose narrow, auditable, read-only interfaces that help engineers gather evidence, form hypotheses, rule out causes, and produce reliable incident reports faster.

## What this project provides

- Narrow MCP-style tools instead of generic `kubectl`
- Read-only live Kubernetes investigation
- Per-harness project instructions (CLAUDE.md, AGENTS.md, Bob mode rules)
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

## Slash commands

Both harnesses expose the same three commands: a read-only investigation
command, a standalone JVM health-check command, and a separate, autonomous
fix-proposal command. `/investigate-incident` and `/investigate-jvm` are
both strictly read-only; `/propose-fix` is the only one that can ever open a
(draft) PR, and none of the read-only commands trigger it on their own.

### Claude Code slash commands

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

1. Mints an `investigation_id` and creates `runs/<investigation_id>/scratchpad/`,
   then delegates to the `incident-coordinator` subagent (falls back to a
   single agent, with that fallback stated explicitly, only if subagents
   aren't available)
2. The coordinator reads the service catalog and runbook catalog, then routes
   the symptom to the narrowest relevant specialist subagents:
   - `k8s-evidence-collector` — pod listing, describe, live logs, namespace
     events, current resource usage
   - `prometheus-analyst` — restart counts/increase, CPU, memory, HTTP error
     rate, latency p95
   - `log-analyst` — historical IBM Cloud Logs search (errors, probe
     failures, arbitrary text) spanning restarts/deployments
   - `runbook-analyst` — matches the symptom against local runbooks
   - `jvm-analyst` *(conditional)* — GC pause/throughput, heap, memory-pool/
     native-memory, and thread signals via the separate `jvm-troubleshooter`
     MCP server; only routed to when the symptom is GC-, heap-,
     memory-pressure-, or OOM-flavored on a known OpenJ9/IBM Semeru service
3. Every specialist stores raw evidence as an `evidence_ref` and hands back
   only summaries/findings — the coordinator never gathers evidence directly.
   Each also writes a concise markdown scratchpad (scope, tools called, key
   findings, evidence_refs, unknowns/gaps, decisions, handoff summary) to its
   assigned `runs/<investigation_id>/scratchpad/wave<N>-<subagent-name>.md`
   file — never raw log/metric bodies, only summaries and evidence_refs. The
   coordinator maintains its own running Structured Finding Brief at
   `coordinator-brief.md` in the same directory, and passes each subagent
   that brief plus any relevant prior scratchpad paths in its task prompt.
4. `incident-reporter` runs last, synthesizing all subagents' findings (never
   its own) into a single evidence-grounded, schema-valid report
5. The final output includes a "Subagent usage audit" table: which subagent
   ran, what it did, which tools/evidence_refs/scratchpad path it used, and
   its result

What it does not do:

- Does not mutate Kubernetes resources
- Does not restart pods
- Does not apply fixes
- Does not run destructive commands
- Does not fetch raw evidence detail unless needed

#### `/investigate-jvm`

A lightweight, standalone JVM health check against one OpenJ9/IBM Semeru
service — GC behavior, heap, memory pools, threads, allocation rate, leak
trend — using only `jvm-troubleshooter`'s own tools, no coordinator/subagent
delegation or evidence store required:

```
/investigate-jvm namespace=<namespace> service=<service> lookback_minutes=<minutes>
```

Use this for a quick point check; use `/investigate-incident` (which routes
to `jvm-analyst` automatically for a GC/heap/OOM-flavored symptom) when the
finding needs to sit alongside other specialists' evidence in one incident
report. See `.claude/commands/investigate-jvm.md`
for the full workflow this command follows.

### Bob Shell slash commands

Bob Shell (`.bob/`) is a parallel harness that talks to the same MCP tool
layer and exposes the same three commands.

#### `/investigate-incident`

Read-only, same behavior and args as the Claude Code version above:

```
/investigate-incident namespace=<namespace> service=<service> symptom="<specific symptom>" since_minutes=<minutes>
```

An orchestrator mode decomposes the incident and delegates to the same
specialist roles (`k8s-evidence-collector`, `prometheus-analyst`,
`log-analyst`, `runbook-analyst`, plus `jvm-analyst` when the symptom is
GC-, heap-, memory-pressure-, or OOM-flavored on a known JVM workload), then
hands off to `incident-reporter` for a schema-valid, evidence-grounded report — see `.bob/commands/investigate-incident.md`
and `AGENTS.md` for the full workflow.

#### `/investigate-jvm`

Same lightweight, standalone JVM health check as the Claude Code version
above, no mode switch required:

```
/investigate-jvm namespace=<namespace> service=<service> lookback_minutes=<minutes>
```

See `.bob/commands/investigate-jvm.md` for the full workflow.

#### `/propose-fix`

A separate, autonomous command. It only fires when an incident report traces
the cause to a named application-code location (an exception class, stack
trace, or file/function reference — not an infra/operational finding); if
that gate isn't met, no fix is proposed. When it does fire, it:

- Works only in the target service's own existing local git checkout (looked
  up from `data/service_catalog.json`) — it never clones a repo.
- Requires a clean working tree first — any uncommitted changes to tracked
  files stop it immediately, nothing is stashed or discarded.
- Opens a **draft-only** PR, with an AI-disclosure line and a human-review
  checklist in the description. It never opens a non-draft PR.

Args:

```
/propose-fix namespace=<namespace> service=<service> symptom="<symptom>" since_minutes=<minutes>
/propose-fix investigation_id=<id>|latest
```

Optional: `dry_run=true` (locates the code and narrates the proposed fix to
a scratchpad file without branching, committing, pushing, or opening a PR)
and `base_branch=<branch>` (defaults to whatever branch is already checked
out in the local checkout if omitted).

`/investigate-incident` never triggers `/propose-fix` — they are separate
commands, and a code change only ever happens when `/propose-fix` is run
explicitly.

## Environment for optional tools

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

## No-token local tests

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

## Harness hooks (safety gate + audit trail)

`.claude/settings.json` wires four read-only Claude Code hooks under
`.claude/hooks/`. They're a harness-level safety net and audit trail that sit
alongside the application-level guardrails (`src/claude_ops/hooks.py`,
`schemas/incident_report_schema.py`) — none of them call Kubernetes,
Prometheus, IBM Cloud Logs, or the Claude API; they only inspect the JSON
Claude Code already passes them on stdin, and the only files they write are
JSONL audit logs under `runs/` (gitignored, like the rest of that directory).

| Hook | Event | What it does |
|---|---|---|
| `block_unsafe_shell.py` | `PreToolUse` on `Bash` | Denies raw shell `kubectl delete/apply/patch/scale/rollout restart/exec` and `helm upgrade` — the second gate for a destructive command reaching `Bash` directly, bypassing the typed MCP tools that `hooks.py::validate_kubectl_verb` already gates. |
| `audit_mcp_tool_call.py` | `PostToolUse` on `mcp__claude-ops-investigator__.*` | Appends `{tool_name, timestamp, status, evidence_ref, session_id}` to `runs/mcp-tool-audit.jsonl` for every completed MCP tool call. |
| `audit_subagent_lifecycle.py` | `SubagentStart` / `SubagentStop` | Appends `{event, timestamp, session_id, subagent_type, description}` to `runs/subagent-audit.jsonl`. |
| `validate_final_report.py` | `Stop` | If the last assistant message looks like an incident report (mentions "Subagent usage audit", "incident report", or `requires_human`), checks it contains `evidence_ref`, a "Subagent usage audit" table, `ruled_out`, `unknowns`, and an explicit confirmed/not-confirmed statement — and blocks the stop with a reason if any are missing. Ordinary conversational turns are left alone. |

### Disabling hooks locally

Two ways, from least to most surgical:

- **Disable everything**: add `"disableAllHooks": true` to
  `.claude/settings.local.json` (gitignored, personal — never commit this to
  the project's shared `.claude/settings.json`).
- **Disable just these four**: set `CLAUDE_OPS_HOOKS_DISABLED=1` in your
  shell environment before launching `claude`. Each script checks this at
  the top and no-ops immediately — no audit lines written, no shell command
  blocked, no report validated.

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

### Second MCP server: `jvm-troubleshooter`

A dedicated, independently-testable MCP server for OpenJ9/IBM Semeru JVM
internals (GC, heap, memory pools, threads) lives alongside this project at
`mcp-servers/jvm-troubleshooter/` — its own `pyproject.toml`, `src/`,
`tests/`, and `README.md`, but committed in this same repo/PR rather than as
a separate GitHub project, so the whole `jvm-analyst` feature (specialist +
its server + routing) reviews and ships as one change.

```text
mcp-servers/jvm-troubleshooter/src/jvm_troubleshooter/mcp/server.py = second local MCP server
.mcp.json / .bob/mcp.json = both declare it as "jvm-troubleshooter", PYTHONPATH pointed at
                            ${PWD}/mcp-servers/jvm-troubleshooter/src (same pattern as the
                            main server's own ${PWD}/src)
```

It's backed by Prometheus (or Thanos Query) scraping OpenJ9 JVMs via the
standard Prometheus JMX Exporter — a different metric-naming convention than
this project's own `prom_*` tools assume, so it ships its own PromQL and its
own `PROMETHEUS_URL`/`JVM_LABEL_KEY`. Because `.mcp.json`'s `env` block for
this entry sets those directly (mirroring how the entry itself is
configured) rather than relying on `.env`, set real values there rather than
in `.env` for this specific server.

Its 18 tools (GC activity/pause/throughput/behavior-over-time, heap
status/trend, memory-pool breakdown/native-memory/fragmentation, allocation
rate, leak indicator, thread status, GC-memory correlation, before/after
deploy comparison, one-call incident snapshot, and three PNG chart renderers)
are documented in full — including caveats on what each one can't see — in
[`mcp-servers/jvm-troubleshooter/README.md`](mcp-servers/jvm-troubleshooter/README.md).
Run its own test suite (independent of this project's `pytest` invocation,
which only looks at the root `tests/`) with:

```bash
cd mcp-servers/jvm-troubleshooter
pytest
```

For a quick, standalone JVM health check outside the full incident-
investigation flow, see `/investigate-jvm` under Slash commands above.

### GC logs and thread dumps

The Prometheus-based JVM tools give counts and 5-minute averages. For the
real thing, `claude-ops-investigator` adds three read-only tools, and two
scripts that only a human runs:

- **`jvm_get_gc_log_events(namespace, pod_name, since_minutes, previous)`**
  reads the pod's logs (`kubectl logs`, the same read-only verb as
  `k8s_get_pod_logs`) and parses OpenJ9 verbose GC XML: true per-pause
  p50/p95/p99/max, scavenge vs global, the longest pauses with timestamps and
  triggers (allocation failure, `System.gc()`, concurrent kickoff),
  percolate/copy-failed events and GC warnings.
  **Prerequisite:** verbose GC has to be switched on. For a Liberty service,
  add `-verbose:gc` to its `jvm.options` (e.g. the
  `jvmoptions-<service>-config` ConfigMap) and roll the pods. OpenJ9 writes
  it natively to stderr, so it lands in `kubectl logs` and IBM Cloud Logs
  without Liberty's logging in the way. Expect roughly a few MB of extra
  log volume per pod per hour. Until it's on, the tool returns a `business`
  error saying so.
- **`scripts/capture-gclog.sh [-m max_files] <namespace> <pod> [container]`**
  — **human-run only**, for JVMs that write their GC log to a *file*
  (`-Xverbosegclog`). It reads the JVM's real setting from its command line
  and `OPENJ9_JAVA_OPTIONS`/`IBM_JAVA_OPTIONS`/`JAVA_TOOL_OPTIONS`/
  `JDK_JAVA_OPTIONS` (including `%pid`/`%seq` patterns and rotated files),
  also checks `/tmp/verbosegc.*.txt` and `/opt/ibm/*verbosegc.*.txt`, and
  streams the newest files (default 10) into `runs/gclogs/<pod>-<utc>/`.
  It changes nothing in the pod. If the JVM only has `-verbose:gc`, it tells
  you to use `jvm_get_gc_log_events` instead. If no verbose GC is
  configured at all, it says so: OpenJ9 can't switch it on at runtime, so
  that needs a `jvm.options` change and a restart. Same safeguards as the
  javacore script.
- **`jvm_analyze_gc_log(path)`** gives the same analysis as
  `jvm_get_gc_log_events` for a file or a whole capture directory under
  `runs/gclogs/` (rotated files are analyzed together). IBM GCMV remains the
  tool for very large logs.
- **`scripts/capture-javacore.sh [-n count] [-i seconds] <namespace> <pod> [container]`**
  — **human-run only.** Taking a thread dump needs `kubectl exec`
  (`jcmd <pid> Dump.java`, falling back to SIGQUIT if the image has no
  `jcmd`, then reading the javacore file back), which agents are never
  allowed to do. The script refuses to run without an interactive terminal,
  shows the kubectl context, asks you to type the pod name to confirm, and
  saves each javacore to `runs/javacores/<pod>-<utc>[-n].txt`
  (gitignored). `.claude/hooks/block_unsafe_shell.py` also denies it from
  the agent's Bash tool. The JVM keeps running; application threads pause
  briefly while each dump is written. Use `-n 3 -i 10` to take a series:
  threads sitting in the same frame in every dump are stuck, not just busy.
  Run it with `bash scripts/capture-javacore.sh …`; it also prints the IBM
  TMDA command for the saved files.
- **`jvm_analyze_javacore(path)`** analyzes a captured javacore (only files
  under `runs/javacores/`): threads by state, largest thread pools (digits
  collapsed, e.g. `Default Executor-thread-#`), deadlocks, most-contended
  lock owners, blocked/parked threads, common stacks, and hot frames among
  runnable threads. IBM TMDA remains the tool for deeper analysis.

All three tools archive their result as evidence and return an
`evidence_ref`; `jvm-analyst` is allowed to call them in both harnesses.
