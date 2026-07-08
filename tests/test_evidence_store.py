from claude_ops.evidence.raw_store import load_raw_evidence, store_raw_evidence


def test_store_and_load_raw_evidence(tmp_path):
    raw = {"message": "hello", "items": [1, 2, 3]}

    record = store_raw_evidence(
        content_type="test.raw",
        raw=raw,
        summary="test summary",
        metadata={"service": "event-data"},
        artifact_dir=tmp_path,
    )

    assert record.evidence_ref.startswith("ev_")
    assert record.content_type == "test.raw"
    assert record.summary == "test summary"
    assert record.size_bytes > 0

    loaded = load_raw_evidence(record.evidence_ref, artifact_dir=tmp_path)
    assert loaded["raw"] == raw
    assert loaded["metadata"]["service"] == "event-data"


def test_distinct_metadata_yields_distinct_evidence_refs_for_identical_raw(tmp_path):
    """Two Prometheus tool calls with identical (empty) result bodies but
    different metric metadata must not collide on the same evidence_ref."""
    empty_result = {"resultType": "vector", "result": []}

    error_rate_record = store_raw_evidence(
        content_type="prometheus.query_result",
        raw=empty_result,
        summary="HTTP 5xx error rate for si/multi-system-processor over last 60m",
        metadata={
            "namespace": "si",
            "service": "multi-system-processor",
            "metric": "http_error_rate",
            "since_minutes": 60,
            "promql": 'sum(rate(http_requests_total{namespace="si", service="multi-system-processor", status=~"5.."}[60m]))',
        },
        artifact_dir=tmp_path,
    )
    latency_record = store_raw_evidence(
        content_type="prometheus.query_result",
        raw=empty_result,
        summary="p95 latency for si/multi-system-processor over last 60m",
        metadata={
            "namespace": "si",
            "service": "multi-system-processor",
            "metric": "latency_p95",
            "since_minutes": 60,
            "promql": 'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{namespace="si", service="multi-system-processor"}[60m])) by (le))',
        },
        artifact_dir=tmp_path,
    )

    assert error_rate_record.evidence_ref != latency_record.evidence_ref

    loaded_error_rate = load_raw_evidence(error_rate_record.evidence_ref, artifact_dir=tmp_path)
    loaded_latency = load_raw_evidence(latency_record.evidence_ref, artifact_dir=tmp_path)
    assert loaded_error_rate["metadata"]["metric"] == "http_error_rate"
    assert loaded_latency["metadata"]["metric"] == "latency_p95"


def test_store_k8s_tool_result_preserves_errors():
    from claude_ops.evidence.k8s_evidence import store_k8s_tool_result

    error = {
        "isError": True,
        "errorCategory": "permission",
        "message": "blocked",
    }

    result = store_k8s_tool_result(
        content_type="k8s.test",
        result=error,
        label="test",
        metadata={"namespace": "si"},
    )

    assert result is error


def test_store_k8s_tool_result_returns_evidence_record(monkeypatch, tmp_path):
    from claude_ops.evidence import k8s_evidence
    from claude_ops.evidence.raw_store import store_raw_evidence as real_store_raw_evidence

    def fake_store_raw_evidence(*, content_type, raw, summary, metadata):
        return real_store_raw_evidence(
            content_type=content_type,
            raw=raw,
            summary=summary,
            metadata=metadata,
            artifact_dir=tmp_path,
        )

    monkeypatch.setattr(k8s_evidence, "store_raw_evidence", fake_store_raw_evidence)

    result = k8s_evidence.store_k8s_tool_result(
        content_type="k8s.namespace_events",
        result={"isError": False, "data": "line1\nline2"},
        label="recent events",
        metadata={"namespace": "si"},
    )

    assert result["isError"] is False
    assert result["data"]["evidence_ref"].startswith("ev_")
    assert result["data"]["content_type"] == "k8s.namespace_events"
    assert result["data"]["metadata"]["namespace"] == "si"
    assert result["data"]["size_bytes"] > 0


def test_store_k8s_tool_result_uses_custom_summarizer(monkeypatch, tmp_path):
    from claude_ops.evidence import k8s_evidence
    from claude_ops.evidence.raw_store import store_raw_evidence as real_store_raw_evidence
    from claude_ops.evidence.summarizers import summarize_k8s_events

    def fake_store_raw_evidence(*, content_type, raw, summary, metadata):
        return real_store_raw_evidence(
            content_type=content_type,
            raw=raw,
            summary=summary,
            metadata=metadata,
            artifact_dir=tmp_path,
        )

    monkeypatch.setattr(k8s_evidence, "store_raw_evidence", fake_store_raw_evidence)

    events_text = (
        "LAST SEEN   TYPE      REASON      OBJECT              MESSAGE\n"
        "1m          Warning   Unhealthy   pod/event-data-abc  Liveness probe failed\n"
    )

    result = k8s_evidence.store_k8s_tool_result(
        content_type="k8s.namespace_events",
        result={"isError": False, "data": events_text},
        label="recent events",
        metadata={"namespace": "si"},
        summarize=summarize_k8s_events,
    )

    summary = result["data"]["summary"]
    assert "1 Warning" in summary
    assert "1 Unhealthy" in summary
    assert "Liveness probe failed" in summary
