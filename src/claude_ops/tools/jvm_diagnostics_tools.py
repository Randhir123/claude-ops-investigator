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
from datetime import datetime
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
            elif tag == "concurrent-global-final":
                # Stop-the-world end of a concurrent global mark (gencon); the
                # reason follows in <concurrent-trace-info reason="...">.
                if current is not None and current["trigger"] is None:
                    current["trigger"] = "concurrent-global-final"
            elif tag == "concurrent-trace-info":
                if current is not None and current["trigger"] == "concurrent-global-final" and attrs.get("reason"):
                    current["trigger"] = f"concurrent-global-final:{attrs['reason']}"
            elif tag == "concurrent-end" and attrs.get("terminationReason"):
                # e.g. a concurrent scavenge cut short ("termination requested by GC")
                events[f"concurrent-end:{attrs.get('type', 'unknown')}:{attrs['terminationReason']}"] += 1
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
                if (block["trigger"] or "").startswith("concurrent-global-final"):
                    triggers[block["trigger"]] += 1
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


_JAVA_THREAD_ID_RE = re.compile(r"^3XMJAVALTHREAD\s+\(java/lang/Thread getId:(?P<id>0x[0-9A-Fa-f]+)")


def _parse_javacore_threads(text: str) -> tuple[dict[str, str], list[dict[str, Any]], list[str]]:
    """Return (header, threads, deadlocked thread names) from an OpenJ9 javacore."""
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
            current = {"name": match["name"], "id": None, "state": match["state"], "block": None, "frames": []}
            threads.append(current)
        elif line.split(maxsplit=1)[:1] == ["3XMTHREADINFO"]:
            current = None  # e.g. "Anonymous native thread": don't attach its frames to the previous thread
        elif current is not None and (match := _JAVA_THREAD_ID_RE.match(line)):
            current["id"] = match["id"]
        elif current is not None and (match := _BLOCK_RE.match(line)):
            current["block"] = {"kind": match["kind"], "object": match["obj"], "owner": match["owner"]}
        elif current is not None and (match := _FRAME_RE.match(line)):
            current["frames"].append(match["frame"])
        elif match := _DEADLOCK_THREAD_RE.match(line):
            if match["name"] not in deadlocked:
                deadlocked.append(match["name"])

    return header, threads, deadlocked


def parse_javacore(text: str) -> dict[str, Any]:
    """Summarize an OpenJ9 javacore: thread states, pools, lock contention, deadlocks, hot stacks."""
    header, threads, deadlocked = _parse_javacore_threads(text)

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


# --- javacore series comparison ---------------------------------------------------

_MAX_COMPARE_DUMPS = 10
_STACK_DEPTH = 5
# Top frames where a RUNNABLE thread is really idle, waiting for I/O or events.
_IDLE_TOP_FRAMES = (
    "sun/nio/ch/EPoll.wait",
    "sun/nio/ch/Net.accept",
    "sun/nio/ch/Net.poll",
    "java/net/PlainSocketImpl.socketAccept",
    "sun/nio/fs/LinuxWatchService.poll",
    "sun/nio/ch/KQueue.poll",
)
_DUMP_TIME_RE = re.compile(r"(\d{4}/\d{2}/\d{2}) at (\d{2}:\d{2}:\d{2})(?::(\d{1,3}))?")
_LAST_NUMBER_RE = re.compile(r"(\d+)(?!.*\d)")


def _dump_time(header: dict[str, str]) -> datetime | None:
    match = _DUMP_TIME_RE.search(header.get("dump_time", ""))
    if not match:
        return None
    stamp = datetime.strptime(f"{match[1]} {match[2]}", "%Y/%m/%d %H:%M:%S")
    return stamp.replace(microsecond=int(match[3] or 0) * 1000)


def _is_idle(frames: list[str]) -> bool:
    return bool(frames) and frames[0].startswith(_IDLE_TOP_FRAMES)


def compare_javacores(texts: list[tuple[str, str]]) -> dict[str, Any]:
    """Compare a series of javacores from one JVM, taken some seconds apart.

    `texts` is a list of (name, javacore text). Dumps are ordered by their own
    dump time. A thread is matched across dumps by its Java thread ID (falling
    back to its name).
    """
    dumps = []
    for name, text in texts:
        header, threads, deadlocked = _parse_javacore_threads(text)
        dumps.append({
            "file": name,
            "time": _dump_time(header),
            "threads": {(t["id"] or t["name"]): t for t in threads},
            "deadlocked": deadlocked,
        })
    dumps.sort(key=lambda d: (d["time"] is None, d["time"] or datetime.min))

    keys_in_all = set.intersection(*(set(d["threads"]) for d in dumps))
    stuck: dict[tuple, dict[str, Any]] = {}
    blocked_throughout = []
    idle_io = waiting_unchanged = 0
    for key in keys_in_all:
        seen = [d["threads"][key] for d in dumps]
        stacks = {tuple(t["frames"][:_STACK_DEPTH]) for t in seen}
        states = [t["state"] for t in seen]
        if len(stacks) != 1:
            continue  # moved between dumps: busy, not stuck
        frames = next(iter(stacks))
        if _is_idle(list(frames)):
            idle_io += 1
        elif all(s == "B" for s in states):
            last = seen[-1]
            blocked_throughout.append({
                "name": last["name"],
                "waiting_on": (last["block"] or {}).get("object"),
                "owner": (last["block"] or {}).get("owner"),
                "top_frame": frames[0] if frames else None,
            })
        elif all(s == "R" for s in states) and frames:
            group = stuck.setdefault(frames, {"threads": 0, "top_frames": list(frames), "examples": []})
            group["threads"] += 1
            if len(group["examples"]) < 5:
                group["examples"].append(seen[-1]["name"])
        else:
            waiting_unchanged += 1  # waiting/parked on the same thing: normal for idle pool threads

    times = [d["time"] for d in dumps]
    span = (times[-1] - times[0]).total_seconds() if all(times) and len(times) > 1 else None

    pools: dict[str, dict[str, Any]] = {}
    for i, d in enumerate(dumps):
        for t in d["threads"].values():
            match = _LAST_NUMBER_RE.search(t["name"])
            if not match:
                continue
            key = t["name"][: match.start()] + "#" + t["name"][match.end():]
            pool = pools.setdefault(key, {"pool": key, "counts": [0] * len(dumps), "max_number": [None] * len(dumps)})
            pool["counts"][i] += 1
            number = int(match[1])
            if pool["max_number"][i] is None or number > pool["max_number"][i]:
                pool["max_number"][i] = number
    churn = []
    for pool in pools.values():
        first, last = pool["max_number"][0], pool["max_number"][-1]
        if first is None or last is None or last <= first:
            continue
        created = last - first
        churn.append({
            **pool,
            "threads_created": created,
            "threads_created_per_second": round(created / span, 2) if span else None,
        })
    churn.sort(key=lambda p: -p["threads_created"])

    first_keys, last_keys = set(dumps[0]["threads"]), set(dumps[-1]["threads"])
    return {
        "dumps": [
            {
                "file": d["file"],
                "dump_time": d["time"].isoformat(timespec="milliseconds") if d["time"] else None,
                "thread_count": len(d["threads"]),
                "threads_by_state": dict(Counter(
                    _STATE_NAMES.get(t["state"], t["state"]) for t in d["threads"].values()
                ).most_common()),
                "deadlocked_threads": d["deadlocked"],
            }
            for d in dumps
        ],
        "span_seconds": span,
        "threads_in_every_dump": len(keys_in_all),
        "stuck_runnable": sorted(stuck.values(), key=lambda g: -g["threads"])[:_TOP_N * 2],
        "blocked_throughout": blocked_throughout[: _TOP_N * 5],
        "idle_io_threads": idle_io,
        "waiting_unchanged_threads": waiting_unchanged,
        "thread_churn": churn[:_TOP_N],
        "threads_appeared": len(last_keys - first_keys),
        "threads_disappeared": len(first_keys - last_keys),
    }


def _compare_summary(parsed: dict[str, Any]) -> str:
    n = len(parsed["dumps"])
    span = f" over {parsed['span_seconds']:.0f}s" if parsed["span_seconds"] else ""
    stuck = sum(g["threads"] for g in parsed["stuck_runnable"])
    parts = [
        f"{n} javacores{span}: {parsed['threads_in_every_dump']} threads in every dump; "
        f"{stuck} RUNNABLE with an unchanged non-idle stack (possibly stuck or spinning), "
        f"{len(parsed['blocked_throughout'])} BLOCKED throughout, "
        f"{parsed['idle_io_threads']} idle I/O, {parsed['waiting_unchanged_threads']} waiting unchanged"
    ]
    if parsed["stuck_runnable"]:
        top = parsed["stuck_runnable"][0]
        parts.append(f"largest stuck group: {top['threads']} threads at {top['top_frames'][0]}")
    if any(d["deadlocked_threads"] for d in parsed["dumps"]):
        parts.append("DEADLOCK reported in at least one dump")
    if parsed["thread_churn"]:
        top = parsed["thread_churn"][0]
        rate = f" (~{top['threads_created_per_second']}/s)" if top["threads_created_per_second"] else ""
        parts.append(f"most churn: '{top['pool']}' created {top['threads_created']} threads{rate}")
    parts.append(f"{parsed['threads_appeared']} threads appeared, {parsed['threads_disappeared']} disappeared")
    return "; ".join(parts)


def jvm_compare_javacores(paths: list[str]) -> dict[str, Any]:
    """Compare 2-10 javacores from the same JVM that a human captured into runs/javacores/."""
    attempted = {"paths": paths}
    hint = "Run scripts/capture-javacore.sh -n 3 -i 10 <namespace> <pod>; it saves under runs/javacores/."

    expanded: list[str] = []
    for path in paths or []:
        if any(ch in path for ch in "*?["):
            base = Path(path) if Path(path).is_absolute() else PROJECT_ROOT / path
            matches = sorted(str(p) for p in base.parent.glob(base.name))
            if not matches:
                return ToolError("validation", False, f"No files match {path}.", attempted=attempted,
                                 alternatives=[hint]).to_dict()
            expanded.extend(matches)
        else:
            expanded.append(path)

    if not 2 <= len(expanded) <= _MAX_COMPARE_DUMPS:
        return ToolError(
            "validation",
            False,
            f"Compare needs 2 to {_MAX_COMPARE_DUMPS} javacores; got {len(expanded)}.",
            attempted=attempted,
            alternatives=[hint, "For a single dump, use jvm_analyze_javacore"],
        ).to_dict()

    texts: list[tuple[str, str]] = []
    for path in expanded:
        resolved = _resolve_confined(path, JAVACORE_DIR, hint)
        if isinstance(resolved, dict):
            return resolved
        if not resolved.is_file() or resolved.stat().st_size > _MAX_JAVACORE_BYTES:
            return ToolError("validation", False, f"{resolved.name} is not a file under the size limit.",
                             attempted=attempted).to_dict()
        texts.append((resolved.name, resolved.read_text(errors="replace")))

    parsed = compare_javacores(texts)
    if any(d["thread_count"] == 0 for d in parsed["dumps"]):
        return ToolError(
            "validation",
            False,
            "At least one file has no 3XMTHREADINFO thread entries — not an OpenJ9 javacore.",
            attempted=attempted,
            partialResults={"dumps": parsed["dumps"]},
        ).to_dict()

    record = store_raw_evidence(
        content_type="jvm.javacore_comparison",
        raw=parsed,
        summary=_compare_summary(parsed),
        metadata={"files": [name for name, _ in texts], "source": "javacore series"},
    )
    return ok(record.to_dict())
