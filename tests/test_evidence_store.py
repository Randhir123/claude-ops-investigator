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
