from __future__ import annotations

from jvm_troubleshooter.tools import incident_tools


def _ok(data):
    return {"isError": False, "data": data}


def _err(message):
    return {"isError": True, "errorCategory": "transient", "isRetryable": True, "message": message}


def test_get_jvm_incident_snapshot_aggregates_all_signals_on_success(prom_env, monkeypatch):
    monkeypatch.setattr(incident_tools, "get_heap_status", lambda *a, **k: _ok({"heap": "ok"}))
    monkeypatch.setattr(incident_tools, "get_gc_pause_stats", lambda *a, **k: _ok({"pause": "ok"}))
    monkeypatch.setattr(incident_tools, "get_gc_throughput", lambda *a, **k: _ok({"throughput": "ok"}))
    monkeypatch.setattr(incident_tools, "get_native_memory_summary", lambda *a, **k: _ok({"native": "ok"}))
    monkeypatch.setattr(incident_tools, "get_thread_status", lambda *a, **k: _ok({"threads": "ok"}))

    result = incident_tools.get_jvm_incident_snapshot("si-dev-001a", "event-data")

    assert result["isError"] is False
    signals = result["data"]["signals"]
    assert signals == {
        "heap_status": {"heap": "ok"},
        "gc_pause_stats": {"pause": "ok"},
        "gc_throughput": {"throughput": "ok"},
        "native_memory_summary": {"native": "ok"},
        "thread_status": {"threads": "ok"},
    }
    assert result["data"]["failed_signals"] == []


def test_get_jvm_incident_snapshot_tolerates_partial_failure(prom_env, monkeypatch):
    monkeypatch.setattr(incident_tools, "get_heap_status", lambda *a, **k: _ok({"heap": "ok"}))
    monkeypatch.setattr(incident_tools, "get_gc_pause_stats", lambda *a, **k: _ok({"pause": "ok"}))
    monkeypatch.setattr(incident_tools, "get_gc_throughput", lambda *a, **k: _ok({"throughput": "ok"}))
    monkeypatch.setattr(
        incident_tools, "get_native_memory_summary", lambda *a, **k: _err("native memory query failed")
    )
    monkeypatch.setattr(incident_tools, "get_thread_status", lambda *a, **k: _ok({"threads": "ok"}))

    result = incident_tools.get_jvm_incident_snapshot("si-dev-001a", "event-data")

    # the whole snapshot still succeeds -- one bad sub-query doesn't fail the triage
    assert result["isError"] is False
    signals = result["data"]["signals"]
    assert "native_memory_summary" not in signals
    assert set(signals) == {"heap_status", "gc_pause_stats", "gc_throughput", "thread_status"}

    failures = result["data"]["failed_signals"]
    assert len(failures) == 1
    assert failures[0]["signal"] == "native_memory_summary"
    assert failures[0]["message"] == "native memory query failed"


def test_get_jvm_incident_snapshot_clamps_lookback(prom_env, monkeypatch):
    captured = {}

    def fake_get_gc_pause_stats(namespace, service, lookback_minutes=60):
        captured["lookback_minutes"] = lookback_minutes
        return _ok({})

    monkeypatch.setattr(incident_tools, "get_heap_status", lambda *a, **k: _ok({}))
    monkeypatch.setattr(incident_tools, "get_gc_pause_stats", fake_get_gc_pause_stats)
    monkeypatch.setattr(incident_tools, "get_gc_throughput", lambda *a, **k: _ok({}))
    monkeypatch.setattr(incident_tools, "get_native_memory_summary", lambda *a, **k: _ok({}))
    monkeypatch.setattr(incident_tools, "get_thread_status", lambda *a, **k: _ok({}))

    incident_tools.get_jvm_incident_snapshot("si-dev-001a", "event-data", lookback_minutes=999_999)

    assert captured["lookback_minutes"] == 1440
