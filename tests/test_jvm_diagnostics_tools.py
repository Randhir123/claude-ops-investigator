from __future__ import annotations

import pytest

from claude_ops.evidence.raw_store import store_raw_evidence as real_store_raw_evidence
from claude_ops.tools import jvm_diagnostics_tools, k8s_tools

# Trimmed OpenJ9 (gencon) verbose GC output, interleaved with application log lines
# the way it appears in container stderr/stdout.
VERBOSE_GC_LOG = """\
21:19:59.900 [s1-io-17] INFO  c.d.o.d.i.core.tracker.RequestLogger - Slow (607 ms) Select ...
<exclusive-start id="100" timestamp="2026-10-01T21:20:00.000" intervalms="5000.1">
  <response-info timems="0.05" idlems="0.02" threads="0" lastid="0000000000AB1200" lastname="Default Executor-thread-3" />
</exclusive-start>
<af-start id="101" threadId="0000000000AB1200" totalBytesRequested="48" timestamp="2026-10-01T21:20:00.000" intervalms="5000.2" type="nursery" />
<cycle-start id="102" type="scavenge" contextid="0" timestamp="2026-10-01T21:20:00.000" intervalms="5000.3" />
<gc-start id="103" type="scavenge" contextid="102" timestamp="2026-10-01T21:20:00.000">
  <mem-info id="104" free="100" total="1000" percent="10">
</gc-start>
<gc-end id="105" type="scavenge" contextid="102" durationms="12.5" usertimems="90" systemtimems="1" timestamp="2026-10-01T21:20:00.013" activeThreads="8">
  <mem-info id="106" free="600" total="1000" percent="60">
</gc-end>
<cycle-end id="107" type="scavenge" contextid="102" timestamp="2026-10-01T21:20:00.013" />
<af-end id="108" timestamp="2026-10-01T21:20:00.013" success="true" from="nursery"/>
<exclusive-end id="109" timestamp="2026-10-01T21:20:00.013" durationms="12.9" />
21:20:05.000 [main] INFO app - unrelated line with <angle> text
<exclusive-start id="200" timestamp="2026-10-01T21:25:00.000" intervalms="300000.0">
</exclusive-start>
<sys-start id="201" reason="explicit" timestamp="2026-10-01T21:25:00.000" intervalms="300000.0" />
<cycle-start id="202" type="global" contextid="0" timestamp="2026-10-01T21:25:00.000" intervalms="300000.0" />
<gc-start id="203" type="global mark" contextid="202" timestamp="2026-10-01T21:25:00.000" />
<gc-end id="204" type="global mark" contextid="202" durationms="200.1" timestamp="2026-10-01T21:25:00.200" />
<cycle-end id="205" type="global" contextid="202" timestamp="2026-10-01T21:25:00.250" />
<exclusive-end id="206" timestamp="2026-10-01T21:25:00.250" durationms="250.7" />
<concurrent-kickoff id="300" timestamp="2026-10-01T21:26:00.000">
</concurrent-kickoff>
{"message": "<exclusive-start id=\\"400\\" timestamp=\\"2026-10-01T21:30:00.000\\" intervalms=\\"1.0\\">"}
{"message": "<af-start id=\\"401\\" type=\\"nursery\\" timestamp=\\"2026-10-01T21:30:00.000\\" />"}
{"message": "<cycle-start id=\\"402\\" type=\\"scavenge\\" contextid=\\"0\\" timestamp=\\"2026-10-01T21:30:00.000\\" />"}
{"message": "<copy-failed type=\\"nursery\\" objectcount=\\"12\\" bytes=\\"4096\\" />"}
{"message": "<percolate-collect id=\\"403\\" from=\\"nursery\\" to=\\"global\\" reason=\\"failed tenure threshold reached\\" />"}
{"message": "<warning details=\\"tenure objects are being copied\\" />"}
{"message": "<exclusive-end id=\\"404\\" timestamp=\\"2026-10-01T21:30:00.090\\" durationms=\\"90.0\\" />"}
"""

JAVACORE = """\
0SECTION       TITLE subcomponent dump routine
NULL           ===============================
1TISIGINFO     Dump Event "user" (00004000) received
1TIDATETIME    Date: 2026/10/01 at 21:52:03:123
1CIJAVAVERSION JRE 17.0.12 Linux amd64-64 (build 17.0.12+7)
1LKDEADLOCK    Deadlock detected !!!
2LKDEADLOCKTHR  Thread "worker-1" (0x00000000001A2B00)
3LKDEADLOCKWTR    is waiting for:
4LKDEADLOCKOBJ      java/lang/Object@0x00000000F0002000
3LKDEADLOCKOWN    which is owned by:
2LKDEADLOCKTHR  Thread "worker-2" (0x00000000001A2C00)
3LKDEADLOCKWTR    is waiting for:
3LKDEADLOCKOWN    which is owned by:
2LKDEADLOCKTHR  Thread "worker-1" (0x00000000001A2B00)
1XMTHDINFO     Thread Details
3XMTHREADINFO      "Default Executor-thread-1" J9VMThread:0x0000000000011100, omrthread_t:0x1, java/lang/Thread:0x2, state:R, prio=5
3XMJAVALTHREAD            (java/lang/Thread getId:0x21, isDaemon:true)
3XMTHREADINFO1            (native thread ID:0x1F, native priority:0x5, native policy:UNKNOWN, vmstate:CW, vm thread flags:0x00000001)
3XMTHREADINFO3           Java callstack:
4XESTACKTRACE                at java/net/SocketInputStream.socketRead0(Native Method)
4XESTACKTRACE                at com/example/Dao.query(Dao.java:42)
3XMTHREADINFO      "Default Executor-thread-2" J9VMThread:0x0000000000011200, omrthread_t:0x3, java/lang/Thread:0x4, state:B, prio=5
3XMTHREADBLOCK     Blocked on: java/lang/Object@0x00000000F0001000 Owned by: "worker-2" (J9VMThread:0x0000000000012200, java/lang/Thread:0x5)
4XESTACKTRACE                at com/example/Cache.get(Cache.java:10)
4XESTACKTRACE                at com/example/Service.handle(Service.java:20)
3XMTHREADINFO      "Default Executor-thread-3" J9VMThread:0x0000000000011300, omrthread_t:0x6, java/lang/Thread:0x7, state:B, prio=5
3XMTHREADBLOCK     Blocked on: java/lang/Object@0x00000000F0001000 Owned by: "worker-2" (J9VMThread:0x0000000000012200, java/lang/Thread:0x5)
4XESTACKTRACE                at com/example/Cache.get(Cache.java:10)
4XESTACKTRACE                at com/example/Service.handle(Service.java:20)
3XMTHREADINFO      "worker-2" J9VMThread:0x0000000000012200, omrthread_t:0x8, java/lang/Thread:0x5, state:P, prio=5
3XMTHREADBLOCK     Parked on: java/util/concurrent/locks/ReentrantLock$NonfairSync@0x00000000F0003000 Owned by: "worker-1" (J9VMThread:0x0000000000012100, java/lang/Thread:0x9)
4XESTACKTRACE                at jdk/internal/misc/Unsafe.park(Native Method)
3XMTHREADINFO      "Kafka consumer-17" J9VMThread:0x0000000000013300, omrthread_t:0xa, java/lang/Thread:0xb, state:CW, prio=5
4XESTACKTRACE                at java/lang/Object.wait(Native Method)
3XMTHREADINFO      Anonymous native thread
3XMTHREADINFO1            (native thread ID:0x99, native priority:0x0, native policy:UNKNOWN)
4XESTACKTRACE                at should/not/be/attributed.anywhere(Unknown Source)
"""


class FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def evidence_to_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(
        jvm_diagnostics_tools,
        "store_raw_evidence",
        lambda **kwargs: real_store_raw_evidence(**kwargs, artifact_dir=tmp_path / "artifacts"),
    )


@pytest.fixture
def javacore_dir(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    jc_dir = root / "runs" / "javacores"
    jc_dir.mkdir(parents=True)
    monkeypatch.setattr(jvm_diagnostics_tools, "PROJECT_ROOT", root)
    monkeypatch.setattr(jvm_diagnostics_tools, "JAVACORE_DIR", jc_dir)
    return jc_dir


# --- verbose GC parsing ------------------------------------------------------------


def test_parse_verbose_gc_attributes_types_and_triggers_to_each_pause():
    parsed = jvm_diagnostics_tools.parse_verbose_gc(VERBOSE_GC_LOG)

    assert parsed["pauses"]["count"] == 3
    assert parsed["pauses"]["max_ms"] == 250.7
    assert parsed["pauses"]["p50_ms"] == 90.0
    assert parsed["longest_pauses"][0] == {
        "timestamp": "2026-10-01T21:25:00.250",
        "duration_ms": 250.7,
        "gc_types": ["global", "global mark"],
        "trigger": "system-gc:explicit",
    }
    assert parsed["pauses_by_gc_type"]["scavenge"]["count"] == 2
    assert parsed["pauses_by_gc_type"]["scavenge"]["max_ms"] == 90.0
    assert parsed["cycles_by_type"] == {"scavenge": 2, "global": 1}
    assert parsed["triggers"] == {"allocation-failure:nursery": 2, "system-gc:explicit": 1, "concurrent-kickoff": 1}
    assert parsed["notable_events"] == {"copy-failed:nursery": 1, "percolate-collect:failed tenure threshold reached": 1}
    assert parsed["warnings"] == {"tenure objects are being copied": 1}
    assert parsed["first_pause_at"] == "2026-10-01T21:20:00.013"
    assert parsed["last_pause_at"] == "2026-10-01T21:30:00.090"


def test_parse_verbose_gc_with_no_gc_output():
    parsed = jvm_diagnostics_tools.parse_verbose_gc("plain app log\nanother <b>line</b>\n")
    assert parsed["pauses"]["count"] == 0
    assert parsed["longest_pauses"] == []


def test_gc_log_events_reads_pod_logs_read_only_and_stores_evidence(monkeypatch, evidence_to_tmp):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return FakeCompleted(0, stdout=VERBOSE_GC_LOG)

    monkeypatch.setattr(k8s_tools.subprocess, "run", fake_run)

    result = jvm_diagnostics_tools.jvm_get_gc_log_events("si", "tsq-abc", since_minutes=5000, previous=True)

    assert result["isError"] is False
    assert captured["args"][:3] == ["kubectl", "logs", "tsq-abc"]
    assert "--since=1440m" in captured["args"]  # clamped
    assert "--previous" in captured["args"]
    assert not {"exec", "cp", "debug"} & set(captured["args"])
    assert result["data"]["content_type"] == "jvm.gc_log_events"
    assert "3 pauses (2026-10-01T21:20:00.013 to 2026-10-01T21:30:00.090), max 250.7ms" in result["data"]["summary"]
    assert result["data"]["evidence_ref"].startswith("ev_")


def test_gc_log_events_without_verbose_gc_is_business_gap(monkeypatch):
    monkeypatch.setattr(k8s_tools.subprocess, "run", lambda args, **kwargs: FakeCompleted(0, stdout="app line\n"))

    result = jvm_diagnostics_tools.jvm_get_gc_log_events("si", "tsq-abc")

    assert result["isError"] is True
    assert result["errorCategory"] == "business"
    assert any("-verbose:gc" in alt for alt in result["alternatives"])
    assert any("not as 'no GC activity'" in alt for alt in result["alternatives"])


def test_gc_log_events_passes_kubectl_errors_through(monkeypatch):
    monkeypatch.setattr(
        k8s_tools.subprocess, "run", lambda args, **kwargs: FakeCompleted(1, stderr='pods "nope" not found')
    )

    result = jvm_diagnostics_tools.jvm_get_gc_log_events("si", "nope")

    assert result["isError"] is True
    assert "not found" in result["message"]


def test_gc_log_events_gap_points_at_file_based_capture(monkeypatch):
    monkeypatch.setattr(k8s_tools.subprocess, "run", lambda args, **kwargs: FakeCompleted(0, stdout="app line\n"))

    result = jvm_diagnostics_tools.jvm_get_gc_log_events("si", "tsq-abc")

    assert any("capture-gclog.sh" in alt and "jvm_analyze_gc_log" in alt for alt in result["alternatives"])


# --- verbose GC log files ------------------------------------------------------------

_SCAVENGE_BLOCK = """\
<exclusive-start id="1" timestamp="{ts}" intervalms="1.0">
</exclusive-start>
<af-start id="2" type="nursery" timestamp="{ts}" />
<cycle-start id="3" type="scavenge" contextid="0" timestamp="{ts}" />
<exclusive-end id="4" timestamp="{ts}" durationms="{ms}" />
"""


@pytest.fixture
def gclog_dir(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    gc_dir = root / "runs" / "gclogs"
    gc_dir.mkdir(parents=True)
    monkeypatch.setattr(jvm_diagnostics_tools, "PROJECT_ROOT", root)
    monkeypatch.setattr(jvm_diagnostics_tools, "GCLOG_DIR", gc_dir)
    return gc_dir


def test_analyze_gc_log_single_file(gclog_dir, evidence_to_tmp):
    (gclog_dir / "verbosegc.20261001.212000.1.txt").write_text(VERBOSE_GC_LOG)

    result = jvm_diagnostics_tools.jvm_analyze_gc_log("runs/gclogs/verbosegc.20261001.212000.1.txt")

    assert result["isError"] is False
    assert result["data"]["content_type"] == "jvm.gc_log_file_analysis"
    assert "3 pauses" in result["data"]["summary"]
    assert "max 250.7ms" in result["data"]["summary"]


def test_analyze_gc_log_directory_of_rotated_files_uses_true_time_range(gclog_dir, evidence_to_tmp):
    capture = gclog_dir / "tsq-abc-20261001T220000Z"
    capture.mkdir()
    # Circular rotation: the lexically-first file holds the *newest* events.
    (capture / "gc.log.001").write_text(_SCAVENGE_BLOCK.format(ts="2026-10-01T21:59:00.000", ms="40.0"))
    (capture / "gc.log.002").write_text(_SCAVENGE_BLOCK.format(ts="2026-10-01T21:00:00.000", ms="10.0"))

    result = jvm_diagnostics_tools.jvm_analyze_gc_log("runs/gclogs/tsq-abc-20261001T220000Z")

    assert result["isError"] is False
    summary = result["data"]["summary"]
    assert "(2 file(s))" in summary
    assert "2 pauses (2026-10-01T21:00:00.000 to 2026-10-01T21:59:00.000)" in summary
    assert "max 40.0ms" in summary


@pytest.mark.parametrize("path", ["/etc/hosts", "runs/gclogs/../../.env", "runs/javacores"])
def test_analyze_gc_log_rejects_paths_outside_gclog_dir(gclog_dir, path):
    result = jvm_diagnostics_tools.jvm_analyze_gc_log(path)

    assert result["errorCategory"] == "validation"
    assert "Only files under" in result["message"]


def test_analyze_gc_log_missing_points_at_human_capture(gclog_dir):
    result = jvm_diagnostics_tools.jvm_analyze_gc_log("runs/gclogs/nothing-here")

    assert result["errorCategory"] == "validation"
    assert any("capture-gclog.sh" in alt for alt in result["alternatives"])


def test_analyze_gc_log_without_gc_events(gclog_dir):
    (gclog_dir / "app.log").write_text("INFO just application output\n")

    result = jvm_diagnostics_tools.jvm_analyze_gc_log("runs/gclogs/app.log")

    assert result["errorCategory"] == "validation"
    assert "not an OpenJ9 verbose GC log" in result["message"]


def test_analyze_gc_log_size_limit(gclog_dir, monkeypatch):
    monkeypatch.setattr(jvm_diagnostics_tools, "_MAX_GCLOG_BYTES", 100)
    (gclog_dir / "big.txt").write_text(VERBOSE_GC_LOG)

    result = jvm_diagnostics_tools.jvm_analyze_gc_log("runs/gclogs/big.txt")

    assert result["errorCategory"] == "validation"
    assert "over the" in result["message"]


# --- javacore ----------------------------------------------------------------------


def test_parse_javacore_threads_pools_locks_and_deadlock():
    parsed = jvm_diagnostics_tools.parse_javacore(JAVACORE)

    assert parsed["dump_event"] == 'Dump Event "user" (00004000) received'
    assert parsed["java_version"].startswith("JRE 17.0.12")
    assert parsed["thread_count"] == 5  # the anonymous native thread is not counted
    assert parsed["threads_by_state"] == {
        "BLOCKED": 2,
        "RUNNABLE": 1,
        "PARKED": 1,
        "WAITING (condition wait)": 1,
    }
    assert parsed["thread_pools"][0] == {"pool": "Default Executor-thread-#", "threads": 3}
    assert parsed["deadlocked_threads"] == ["worker-1", "worker-2"]
    assert parsed["most_contended_lock_owners"][0] == {"owner": "worker-2", "waiters": 2}
    assert parsed["common_stacks"] == [
        {"threads": 2, "top_frames": ["com/example/Cache.get(Cache.java:10)", "com/example/Service.handle(Service.java:20)"]}
    ]
    assert parsed["hot_runnable_frames"] == [{"frame": "java/net/SocketInputStream.socketRead0(Native Method)", "threads": 1}]
    all_frames = str(parsed)
    assert "should/not/be/attributed" not in all_frames


def test_analyze_javacore_success_stores_evidence(javacore_dir, evidence_to_tmp):
    (javacore_dir / "tsq-abc-20261001T215203Z.txt").write_text(JAVACORE)

    result = jvm_diagnostics_tools.jvm_analyze_javacore("runs/javacores/tsq-abc-20261001T215203Z.txt")

    assert result["isError"] is False
    assert result["data"]["content_type"] == "jvm.javacore_analysis"
    assert "5 threads" in result["data"]["summary"]
    assert "DEADLOCK among 2 threads" in result["data"]["summary"]


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "runs/javacores/../../.env", "runs/other.txt", "src/claude_ops/hooks.py"],
)
def test_analyze_javacore_rejects_paths_outside_javacore_dir(javacore_dir, path):
    result = jvm_diagnostics_tools.jvm_analyze_javacore(path)

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert "Only files under" in result["message"]


def test_analyze_javacore_missing_file_lists_available_and_points_at_human_script(javacore_dir):
    (javacore_dir / "older.txt").write_text(JAVACORE)

    result = jvm_diagnostics_tools.jvm_analyze_javacore("runs/javacores/missing.txt")

    assert result["errorCategory"] == "validation"
    assert result["partialResults"] == {"available": ["older.txt"]}
    assert any("A human needs to capture it first" in alt for alt in result["alternatives"])
    assert any("capture-javacore.sh" in alt for alt in result["alternatives"])


def test_analyze_javacore_rejects_non_javacore_file(javacore_dir):
    (javacore_dir / "notes.txt").write_text("just some text\n")

    result = jvm_diagnostics_tools.jvm_analyze_javacore("runs/javacores/notes.txt")

    assert result["errorCategory"] == "validation"
    assert "doesn't look like an OpenJ9 javacore" in result["message"]
