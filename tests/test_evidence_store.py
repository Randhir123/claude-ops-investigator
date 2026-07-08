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
