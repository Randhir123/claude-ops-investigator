from claude_ops.evidence.summarizers import summarize_k8s_events

FAKE_EVENTS_TABLE = """LAST SEEN   TYPE      REASON              OBJECT                       MESSAGE
5m          Normal    Scheduled           pod/event-data-abc123        Successfully assigned si/event-data-abc123 to node-1
5m          Normal    Pulled              pod/event-data-abc123        Container image already present on machine
4m          Normal    Created             pod/event-data-abc123        Created container event-data
3m          Warning   Unhealthy           pod/event-data-abc123        Liveness probe failed: Get "http://10.0.0.5:8080/healthz": dial tcp 10.0.0.5:8080: connect: connection refused
2m          Warning   Unhealthy           pod/event-data-abc123        Readiness probe failed: HTTP probe failed with statuscode: 503
1m          Normal    Killing             pod/event-data-abc123        Stopping container event-data
1m          Normal    SuccessfulCreate    replicaset/event-data-7c9    Created pod: event-data-def456
"""


def test_summarize_k8s_events_counts_lines_excluding_header():
    summary = summarize_k8s_events(FAKE_EVENTS_TABLE)

    assert "7 event lines" in summary
    assert "2 Warning" in summary
    assert "2 Unhealthy" in summary


def test_summarize_k8s_events_surfaces_top_reasons():
    summary = summarize_k8s_events(FAKE_EVENTS_TABLE)

    assert "Unhealthy x2" in summary


def test_summarize_k8s_events_surfaces_warning_objects():
    summary = summarize_k8s_events(FAKE_EVENTS_TABLE)

    assert "pod/event-data-abc123" in summary


def test_summarize_k8s_events_includes_first_warning_lines_not_just_first_lines():
    summary = summarize_k8s_events(FAKE_EVENTS_TABLE)

    assert "Liveness probe failed" in summary
    assert "Readiness probe failed" in summary
    # The generic first-lines behavior would have surfaced "Scheduled" instead.
    assert "First Warning lines" in summary


def test_summarize_k8s_events_filters_by_target_service():
    summary = summarize_k8s_events(FAKE_EVENTS_TABLE, target_service="event-data-7c9")

    assert "Lines mentioning 'event-data-7c9'" in summary
    assert "SuccessfulCreate" in summary


def test_summarize_k8s_events_handles_no_events():
    summary = summarize_k8s_events("LAST SEEN   TYPE   REASON   OBJECT   MESSAGE\n")

    assert summary == "Kubernetes events: no non-empty event lines captured."


def test_summarize_k8s_events_handles_empty_text():
    summary = summarize_k8s_events("")

    assert summary == "Kubernetes events: no non-empty event lines captured."
