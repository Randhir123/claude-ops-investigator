"""Read-only OpenJ9 diagnostics: verbose GC log events and javacore analysis.

Neither tool can trigger anything inside a pod:

- `jvm_get_gc_log_events` reads pod logs through `_run_kubectl(["logs", ...])`
  (an allowed read-only verb) and parses the OpenJ9 verbose GC XML the JVM
  writes to stderr when `-verbose:gc` is in its jvm.options. Enabling that
  option is a deploy-config change a human makes; this tool only reads.
- `jvm_analyze_gc_log` parses verbose GC log *files* (`-Xverbosegclog`)
  that a human pulled with `scripts/capture-gclog.sh`. It only reads files
  under `runs/gclogs/`.
- `jvm_analyze_javacore` parses a javacore file that a human already
  captured with `scripts/capture-javacore.sh` (which needs `kubectl exec`
  and is deliberately never run by an agent). It only reads files under
  `runs/javacores/`.

Unlike the Prometheus-based JVM metrics (5-minute-bucket averages), verbose
GC events give true per-collection pause times.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from claude_ops.errors import ToolError, ok
from claude_ops.evidence.raw_store import store_raw_evidence
from claude_ops.tools.k8s_tools import _run_kubectl

PROJECT_ROOT = Path(__file__).resolve().parents[3]
JAVACORE_DIR = PROJECT_ROOT / "runs" / "javacores"
GCLOG_DIR = PROJECT_ROOT / "runs" / "gclogs"

_MAX_SINCE_MINUTES = 1440
_LOG_LIMIT_BYTES = 64 * 1024 * 1024
_MAX_JAVACORE_BYTES = 50 * 1024 * 1024
_MAX_GCLOG_BYTES = 200 * 1024 * 1024
_TOP_N = 10


def _resolve_confined(path: str, root: Path, capture_hint: str) -> Path | dict[str, Any]:
    """Resolve `path` (repo-relative or absolute) and require it to stay under `root`."""
    attempted = {"path": path}
    try:
        candidate = Path(path)
        resolved = (candidate if candidate.is_absolute() else PROJECT_ROOT / candidate).resolve()
    except (OSError, RuntimeError) as exc:
        return ToolError("validation", False, f"Invalid path: {exc}", attempted=attempted).to_dict()

    resolved_root = root.resolve()
    if not resolved.is_relative_to(resolved_root):
        return ToolError(
            "validation",
            False,
            f"Only files under {resolved_root} can be analyzed.",
            attempted=attempted,
            alternatives=[capture_hint],
        ).to_dict()

    if not resolved.exists():
        available = sorted(p.name for p in resolved_root.iterdir()) if resolved_root.is_dir() else []
        return ToolError(
            "validation",
            False,
            f"Nothing at {resolved}.",
            attempted=attempted,
            partialResults={"available": available[-20:]},
            alternatives=[f"A human needs to capture it first — agents never exec into pods. {capture_hint}"],
        ).to_dict()

    return resolved

# --- verbose GC ---------------------------------------------------------------

_TAG_RE = re.compile(r"<(?P<tag>[a-z-]+)\b(?P<attrs>[^<>]*?)/?>")
_ATTR_RE = re.compile(r'([A-Za-z]+)="([^"]*)"')


def _attrs(raw: str) -> dict[str, str]:
    return dict(_ATTR_RE.findall(raw))


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, round(pct / 100 * (len(sorted_values) - 1))))
    return sorted_values[index]


def _pause_stats(durations: list[float]) -> dict[str, Any]:
    values = sorted(durations)
    return {
        "count": len(values),
        "total_ms": round(sum(values), 3),
        "mean_ms": round(sum(values) / len(values), 3) if values else 0.0,
        "p50_ms": _percentile(values, 50),
        "p95_ms": _percentile(values, 95),
        "p99_ms": _percentile(values, 99),
        "max_ms": values[-1] if values else 0.0,
    }


def parse_verbose_gc(text: str) -> dict[str, Any]:
    """Parse OpenJ9 verbose GC XML (as it appears in container stderr) into pause statistics.

    Each stop-the-world pause is an `<exclusive-start>`..`<exclusive-end durationms=..>`
    block; the collection type and trigger inside that block are attributed to it.
    Non-GC log lines are ignored, so interleaved application output is fine.
    """
    pauses: list[dict[str, Any]] = []
    cycles: Counter[str] = Counter()
    triggers: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    events: Counter[str] = Counter()
    current: dict[str, Any] | None = None
    gc_lines = 0
    last_heap_free_percent: float | None = None

    for line in text.splitlines():
        if "<" not in line:
            continue
        line = line.replace('\\"', '"')
        for match in _TAG_RE.finditer(line):
            tag = match.group("tag")
            attrs = _attrs(match.group("attrs"))
            gc_lines += 1

            if tag == "exclusive-start":
                current = {"start": attrs.get("timestamp"), "types": [], "trigger": None}
            elif tag in ("cycle-start", "gc-start"):
                gc_type = attrs.get("type", "unknown")
                if tag == "cycle-start":
                    cycles[gc_type] += 1
                if current is not None and gc_type not in current["types"]:
                    current["types"].append(gc_type)
            elif tag == "af-start":
                trigger = f"allocation-failure:{attrs.get('type', 'unknown')}"
                triggers[trigger] += 1
                if current is not None:
                    current["trigger"] = trigger
            elif tag == "sys-start":
                trigger = f"system-gc:{attrs.get('reason', 'unknown')}"
                triggers[trigger] += 1
                if current is not None:
                    current["trigger"] = trigger
            elif tag == "concurrent-kickoff":
                triggers["concurrent-kickoff"] += 1
            elif tag in ("percolate-collect", "copy-failed"):
                events[f"{tag}:{attrs.get('reason') or attrs.get('type') or 'unknown'}"] += 1
            elif tag == "warning":
                warnings[attrs.get("details", "unknown")] += 1
            elif tag == "mem-info" and "percent" in attrs:
                try:
                    last_heap_free_percent = float(attrs["percent"])
                except ValueError:
                    pass
            elif tag == "exclusive-end":
                try:
                    duration = float(attrs.get("durationms", ""))
                except ValueError:
                    current = None
                    continue
                block = current or {"start": None, "types": [], "trigger": None}
                pauses.append({
                    "timestamp": attrs.get("timestamp") or block["start"],
                    "duration_ms": duration,
                    "gc_types": block["types"] or ["unknown"],
                    "trigger": block["trigger"],
                })
                current = None

    by_type: dict[str, list[float]] = {}
    for pause in pauses:
        by_type.setdefault("+".join(pause["gc_types"]), []).append(pause["duration_ms"])

    # min/max rather than first/last: rotated GC log files may be concatenated out of order.
    timestamps = [p["timestamp"] for p in pauses if p["timestamp"]]
    return {
        "gc_lines_found": gc_lines,
        "first_pause_at": min(timestamps) if timestamps else None,
        "last_pause_at": max(timestamps) if timestamps else None,
        "pauses": _pause_stats([p["duration_ms"] for p in pauses]),
        "pauses_by_gc_type": {k: _pause_stats(v) for k, v in sorted(by_type.items())},
        "longest_pauses": sorted(pauses, key=lambda p: -p["duration_ms"])[:_TOP_N],
        "cycles_by_type": dict(cycles.most_common()),
        "triggers": dict(triggers.most_common()),
        "notable_events": dict(events.most_common()),
        "warnings": dict(warnings.most_common(_TOP_N)),
        "last_heap_free_percent": last_heap_free_percent,
    }


def _gc_summary(label: str, parsed: dict[str, Any]) -> str:
    p = parsed["pauses"]
    parts = [
        f"{label}: {p['count']} pauses ({parsed['first_pause_at']} to {parsed['last_pause_at']}), "
        f"max {p['max_ms']}ms, p99 {p['p99_ms']}ms, p50 {p['p50_ms']}ms, total {p['total_ms']}ms"
    ]
    for gc_type, stats in parsed["pauses_by_gc_type"].items():
        parts.append(f"{gc_type}: {stats['count']}x max {stats['max_ms']}ms")
    if parsed["triggers"]:
        parts.append("triggers " + ", ".join(f"{k}={v}" for k, v in parsed["triggers"].items()))
    if parsed["notable_events"]:
        parts.append("events " + ", ".join(f"{k}={v}" for k, v in parsed["notable_events"].items()))
    if parsed["warnings"]:
        parts.append(f"{sum(parsed['warnings'].values())} GC warnings")
    return "; ".join(parts)


def jvm_get_gc_log_events(
    namespace: str,
    pod_name: str,
    container: str | None = None,
    since_minutes: int = 60,
    previous: bool = False,
) -> dict[str, Any]:
    """Read one pod's logs and summarize its OpenJ9 verbose GC events (true per-pause times)."""
    since = max(1, min(int(since_minutes), _MAX_SINCE_MINUTES))
    args = ["logs", pod_name, "-n", namespace, f"--since={since}m", f"--limit-bytes={_LOG_LIMIT_BYTES}"]
    if container:
        args.extend(["-c", container])
    if previous:
        args.append("--previous")

    result = _run_kubectl(args, timeout_seconds=90)
    if result.get("isError"):
        return result

    text = result["data"] or ""
    parsed = parse_verbose_gc(text)
    attempted = {"namespace": namespace, "pod": pod_name, "since_minutes": since, "previous": previous}

    if parsed["pauses"]["count"] == 0:
        return ToolError(
            "business",
            False,
            f"No OpenJ9 verbose GC events found in {pod_name}'s logs for the last {since}m.",
            attempted=attempted,
            alternatives=[
                "If the JVM writes its GC log to a file (-Xverbosegclog or -Xloggc, which wins over a bare "
                "-verbose:gc) rather than stderr, ask a human to run "
                "scripts/capture-gclog.sh <namespace> <pod> and analyze the result with jvm_analyze_gc_log",
                "Otherwise verbose GC is probably not enabled: a human must add -verbose:gc (or "
                "-Xverbosegclog:<file>) to the service's jvm.options (e.g. its jvmoptions-*-config ConfigMap) "
                "and roll the pods — this tool never changes config",
                "If the pod restarted, retry with previous=true to read the prior container's logs",
                "Use jvm-troubleshooter's Prometheus-based GC tools meanwhile (bucket-averaged, not per-pause)",
                "Record this as an unknowns/gap — not as 'no GC activity'",
            ],
        ).to_dict()

    parsed["possibly_truncated"] = len(text.encode()) >= _LOG_LIMIT_BYTES
    record = store_raw_evidence(
        content_type="jvm.gc_log_events",
        raw=parsed,
        summary=_gc_summary(f"{pod_name} verbose GC (stderr), last {since}m", parsed),
        metadata={**attempted, "container": container, "source": "kubectl logs"},
    )
    return ok(record.to_dict())


def jvm_analyze_gc_log(path: str) -> dict[str, Any]:
    """Analyze verbose GC log file(s) a human pulled into runs/gclogs/ (read-only, local files).

    `path` may be one file or a directory of rotated files (as saved by
    scripts/capture-gclog.sh); a directory's files are read in name order and
    analyzed together.
    """
    attempted = {"path": path}
    resolved = _resolve_confined(
        path, GCLOG_DIR, "Run scripts/capture-gclog.sh <namespace> <pod>; it saves under runs/gclogs/."
    )
    if isinstance(resolved, dict):
        return resolved

    files = sorted(p for p in resolved.iterdir() if p.is_file()) if resolved.is_dir() else [resolved]
    if not files:
        return ToolError("validation", False, f"{resolved} contains no files.", attempted=attempted).to_dict()

    total_bytes = sum(p.stat().st_size for p in files)
    if total_bytes > _MAX_GCLOG_BYTES:
        return ToolError(
            "validation",
            False,
            f"GC logs total {total_bytes // (1024 * 1024)}MB, over the {_MAX_GCLOG_BYTES // (1024 * 1024)}MB limit.",
            attempted=attempted,
            partialResults={"files": [p.name for p in files]},
            alternatives=["Analyze one rotated file at a time", "Use IBM GCMV for very large GC logs"],
        ).to_dict()

    parsed = parse_verbose_gc("\n".join(p.read_text(errors="replace") for p in files))
    if parsed["pauses"]["count"] == 0:
        return ToolError(
            "validation",
            False,
            "No OpenJ9 verbose GC pauses (<exclusive-end durationms=...>) found — not an OpenJ9 verbose GC log?",
            attempted=attempted,
            partialResults={"files": [p.name for p in files]},
        ).to_dict()

    rel = resolved.relative_to(PROJECT_ROOT)
    parsed["files"] = [p.name for p in files]
    record = store_raw_evidence(
        content_type="jvm.gc_log_file_analysis",
        raw=parsed,
        summary=_gc_summary(f"{rel} ({len(files)} file(s))", parsed),
        metadata={"path": str(rel), "files": parsed["files"], "source": "verbose GC log file"},
    )
    return ok(record.to_dict())


# --- javacore -----------------------------------------------------------------

_THREAD_RE = re.compile(r'^3XMTHREADINFO\s+"(?P<name>[^"]*)".*?\bstate:(?P<state>[A-Z]+)')
_BLOCK_RE = re.compile(
    r'^3XMTHREADBLOCK\s+(?P<kind>Blocked on|Waiting on|Parked on):\s*(?P<obj>\S+)'
    r'(?:.*?Owned by:\s*"(?P<owner>[^"]*)")?'
)
_FRAME_RE = re.compile(r"^4XESTACKTRACE\s+at\s+(?P<frame>.+?)\s*$")
_DEADLOCK_THREAD_RE = re.compile(r'^2LKDEADLOCKTHR\s+Thread\s+"(?P<name>[^"]*)"')
_DIGITS_RE = re.compile(r"\d+")

_STATE_NAMES = {
    "R": "RUNNABLE",
    "CW": "WAITING (condition wait)",
    "B": "BLOCKED",
    "P": "PARKED",
    "S": "SUSPENDED",
    "Z": "ZOMBIE",
}


def parse_javacore(text: str) -> dict[str, Any]:
    """Summarize an OpenJ9 javacore: thread states, pools, lock contention, deadlocks, hot stacks."""
    threads: list[dict[str, Any]] = []
    header: dict[str, str] = {}
    deadlocked: list[str] = []
    current: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("1TISIGINFO"):
            header["dump_event"] = line[len("1TISIGINFO"):].strip()
        elif line.startswith("1TIDATETIME"):
            header["dump_time"] = line[len("1TIDATETIME"):].strip()
        elif line.startswith("1CIJAVAVERSION"):
            header["java_version"] = line[len("1CIJAVAVERSION"):].strip()
        elif line.startswith("1LKDEADLOCK"):
            header["deadlock"] = "detected"

        if match := _THREAD_RE.match(line):
            current = {"name": match["name"], "state": match["state"], "block": None, "frames": []}
            threads.append(current)
        elif line.split(maxsplit=1)[:1] == ["3XMTHREADINFO"]:
            current = None  # e.g. "Anonymous native thread": don't attach its frames to the previous thread
        elif current is not None and (match := _BLOCK_RE.match(line)):
            current["block"] = {"kind": match["kind"], "object": match["obj"], "owner": match["owner"]}
        elif current is not None and (match := _FRAME_RE.match(line)):
            current["frames"].append(match["frame"])
        elif match := _DEADLOCK_THREAD_RE.match(line):
            if match["name"] not in deadlocked:
                deadlocked.append(match["name"])

    states = Counter(_STATE_NAMES.get(t["state"], t["state"]) for t in threads)
    pools = Counter(_DIGITS_RE.sub("#", t["name"]) for t in threads)
    lock_owners = Counter(t["block"]["owner"] for t in threads if t["block"] and t["block"]["owner"])
    stacks = Counter(tuple(t["frames"][:5]) for t in threads if t["frames"])
    runnable_frames = Counter(t["frames"][0] for t in threads if t["state"] == "R" and t["frames"])

    return {
        **header,
        "thread_count": len(threads),
        "threads_by_state": dict(states.most_common()),
        "thread_pools": [{"pool": name, "threads": n} for name, n in pools.most_common(_TOP_N)],
        "deadlocked_threads": deadlocked,
        "most_contended_lock_owners": [{"owner": name, "waiters": n} for name, n in lock_owners.most_common(_TOP_N)],
        "blocked_threads": [
            {"name": t["name"], "state": t["state"], **t["block"], "top_frame": t["frames"][0] if t["frames"] else None}
            for t in threads
            if t["state"] in ("B", "P") and t["block"]
        ][: _TOP_N * 5],
        "common_stacks": [
            {"threads": n, "top_frames": list(frames)} for frames, n in stacks.most_common(_TOP_N) if n > 1
        ],
        "hot_runnable_frames": [{"frame": f, "threads": n} for f, n in runnable_frames.most_common(_TOP_N)],
    }


def _javacore_summary(name: str, parsed: dict[str, Any]) -> str:
    parts = [f"{name}: {parsed['thread_count']} threads"]
    if parsed["threads_by_state"]:
        parts.append(", ".join(f"{k}={v}" for k, v in parsed["threads_by_state"].items()))
    if parsed["thread_pools"]:
        top = parsed["thread_pools"][:3]
        parts.append("largest pools " + ", ".join(f"'{p['pool']}'={p['threads']}" for p in top))
    if parsed["deadlocked_threads"]:
        parts.append(f"DEADLOCK among {len(parsed['deadlocked_threads'])} threads")
    if parsed["most_contended_lock_owners"]:
        top = parsed["most_contended_lock_owners"][0]
        parts.append(f"most contended lock owner '{top['owner']}' with {top['waiters']} waiters")
    return "; ".join(parts)


def jvm_analyze_javacore(path: str) -> dict[str, Any]:
    """Analyze a javacore that a human captured into runs/javacores/ (read-only, local file)."""
    attempted = {"path": path}
    resolved = _resolve_confined(
        path, JAVACORE_DIR, "Run scripts/capture-javacore.sh <namespace> <pod>; it saves under runs/javacores/."
    )
    if isinstance(resolved, dict):
        return resolved
    if not resolved.is_file():
        return ToolError("validation", False, f"{resolved} is not a file.", attempted=attempted).to_dict()

    if resolved.stat().st_size > _MAX_JAVACORE_BYTES:
        return ToolError(
            "validation",
            False,
            f"Javacore is larger than {_MAX_JAVACORE_BYTES // (1024 * 1024)}MB; analyze it with IBM TMDA instead.",
            attempted=attempted,
        ).to_dict()

    parsed = parse_javacore(resolved.read_text(errors="replace"))
    if parsed["thread_count"] == 0:
        return ToolError(
            "validation",
            False,
            "No 3XMTHREADINFO thread entries found — this doesn't look like an OpenJ9 javacore.",
            attempted=attempted,
        ).to_dict()

    record = store_raw_evidence(
        content_type="jvm.javacore_analysis",
        raw=parsed,
        summary=_javacore_summary(resolved.name, parsed),
        metadata={"file": str(resolved.relative_to(PROJECT_ROOT)), "source": "javacore"},
    )
    return ok(record.to_dict())
