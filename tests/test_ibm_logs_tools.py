from __future__ import annotations

import json

import httpx
import pytest

from claude_ops.tools import ibm_logs_tools
from claude_ops.evidence.raw_store import store_raw_evidence as real_store_raw_evidence


class FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data


class FakeStreamResponse:
    def __init__(self, status_code: int, lines: list[str], text: str = ""):
        self.status_code = status_code
        self._lines = lines
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def iter_lines(self):
        return iter(self._lines)

    def read(self):
        return None


@pytest.fixture(autouse=True)
def _reset_token_cache():
    ibm_logs_tools._token_cache["token"] = None
    ibm_logs_tools._token_cache["expires_at"] = 0.0
    yield
    ibm_logs_tools._token_cache["token"] = None
    ibm_logs_tools._token_cache["expires_at"] = 0.0


def _redirect_store_to_tmp(monkeypatch, tmp_path):
    def fake_store_raw_evidence(*, content_type, raw, summary, metadata):
        return real_store_raw_evidence(
            content_type=content_type,
            raw=raw,
            summary=summary,
            metadata=metadata,
            artifact_dir=tmp_path,
        )

    monkeypatch.setattr(ibm_logs_tools, "store_raw_evidence", fake_store_raw_evidence)


def _fake_iam_request(expected_key: str = "fake-key"):
    def fake_request(method, url, **kwargs):
        assert method == "POST"
        assert url == ibm_logs_tools._IAM_TOKEN_URL
        assert kwargs["data"]["apikey"] == expected_key
        return FakeResponse(200, json_data={"access_token": "fake-token", "expires_in": 3600})

    return fake_request


def test_missing_ibm_logs_endpoint_returns_config_error(monkeypatch):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
    monkeypatch.delenv("IBM_LOGS_ENDPOINT", raising=False)

    def unexpected(*args, **kwargs):
        raise AssertionError("should not make a network call without IBM_LOGS_ENDPOINT")

    monkeypatch.setattr(httpx, "request", unexpected)
    monkeypatch.setattr(httpx, "stream", unexpected)

    result = ibm_logs_tools.ibm_logs_search(namespace="si", query="ERROR")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert "IBM_LOGS_ENDPOINT" in result["message"]


def test_missing_ibm_cloud_api_key_returns_auth_config_error(monkeypatch):
    monkeypatch.setenv("IBM_LOGS_ENDPOINT", "https://guid.api.us-south.logs.cloud.ibm.com")
    monkeypatch.delenv("IBM_CLOUD_API_KEY", raising=False)

    def unexpected(*args, **kwargs):
        raise AssertionError("should not reach the network without IBM_CLOUD_API_KEY")

    monkeypatch.setattr(httpx, "request", unexpected)
    monkeypatch.setattr(httpx, "stream", unexpected)

    result = ibm_logs_tools.ibm_logs_search(namespace="si", query="ERROR")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert "IBM_CLOUD_API_KEY" in result["message"]


def test_build_dataprime_query_escapes_and_scopes_by_namespace_and_app():
    query = ibm_logs_tools._build_dataprime_query(
        text_query="UNKNOWN_TOPIC_OR_PARTITION",
        namespace="si",
        app="multi-system-processor",
        limit=10,
    )

    assert "source logs" in query
    assert "filter $l.applicationname == 'si'" in query
    assert "filter $l.subsystemname == 'multi-system-processor'" in query
    assert "wildfind 'UNKNOWN_TOPIC_OR_PARTITION'" in query
    assert "limit 10" in query


def test_build_dataprime_query_escapes_single_quotes():
    query = ibm_logs_tools._build_dataprime_query(
        text_query="can't parse",
        namespace="si",
        app=None,
        limit=5,
    )

    assert "wildfind 'can\\'t parse'" in query
    assert "subsystemname" not in query


def test_ibm_logs_search_success_is_summarized_and_stored(monkeypatch, tmp_path):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
    monkeypatch.setenv("IBM_LOGS_ENDPOINT", "https://guid.ingress.us-south.logs.cloud.ibm.com")
    _redirect_store_to_tmp(monkeypatch, tmp_path)

    monkeypatch.setattr(httpx, "request", _fake_iam_request())

    result_entry = {
        "metadata": [{"key": "timestamp", "value": "2024-01-01T00:00:00.000Z"}],
        "userData": json.dumps({"message": "ERROR UNKNOWN_TOPIC_OR_PARTITION"}),
    }
    sse_lines = [
        ": success",
        json.dumps({"query_id": {"queryId": "abc"}}),
        "data: " + json.dumps({"result": {"results": [result_entry]}}),
    ]

    captured = {}

    def fake_stream(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        return FakeStreamResponse(200, lines=sse_lines)

    monkeypatch.setattr(httpx, "stream", fake_stream)

    result = ibm_logs_tools.ibm_logs_search(
        namespace="si", query="UNKNOWN_TOPIC_OR_PARTITION", app="multi-system-processor", since_minutes=60, limit=10
    )

    # ingress endpoint rewritten to api endpoint
    assert captured["url"] == "https://guid.api.us-south.logs.cloud.ibm.com/v1/query"
    assert captured["headers"]["Authorization"] == "Bearer fake-token"
    assert "wildfind 'UNKNOWN_TOPIC_OR_PARTITION'" in captured["json"]["query"]

    assert result["isError"] is False
    assert result["data"]["evidence_ref"].startswith("ev_")
    assert result["data"]["content_type"] == "ibm_logs.search_result"
    assert "1 matching log lines" in result["data"]["summary"]
    assert "ERROR UNKNOWN_TOPIC_OR_PARTITION" in result["data"]["summary"]

    # secrets must never leak into the tool result
    dumped = json.dumps(result)
    assert "fake-key" not in dumped
    assert "fake-token" not in dumped


def test_ibm_logs_search_errors_uses_error_keyword(monkeypatch, tmp_path):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
    monkeypatch.setenv("IBM_LOGS_ENDPOINT", "https://guid.api.us-south.logs.cloud.ibm.com")
    _redirect_store_to_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(httpx, "request", _fake_iam_request())

    captured = {}

    def fake_stream(method, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return FakeStreamResponse(200, lines=[])

    monkeypatch.setattr(httpx, "stream", fake_stream)

    result = ibm_logs_tools.ibm_logs_search_errors(namespace="si", app="event-data")

    assert "wildfind 'ERROR'" in captured["json"]["query"]
    assert result["isError"] is False
    assert "no matching log lines" in result["data"]["summary"]


def test_ibm_logs_search_probe_failures_uses_probe_keyword(monkeypatch, tmp_path):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
    monkeypatch.setenv("IBM_LOGS_ENDPOINT", "https://guid.api.us-south.logs.cloud.ibm.com")
    _redirect_store_to_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(httpx, "request", _fake_iam_request())

    captured = {}

    def fake_stream(method, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return FakeStreamResponse(200, lines=[])

    monkeypatch.setattr(httpx, "stream", fake_stream)

    ibm_logs_tools.ibm_logs_search_probe_failures(namespace="si", app="event-data")

    assert "wildfind 'probe failed'" in captured["json"]["query"]


def test_ibm_logs_query_api_error_is_structured(monkeypatch, tmp_path):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
    monkeypatch.setenv("IBM_LOGS_ENDPOINT", "https://guid.api.us-south.logs.cloud.ibm.com")
    _redirect_store_to_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(httpx, "request", _fake_iam_request())

    sse_lines = ["data: " + json.dumps({"error": {"message": "invalid query"}})]

    def fake_stream(method, url, **kwargs):
        return FakeStreamResponse(200, lines=sse_lines)

    monkeypatch.setattr(httpx, "stream", fake_stream)

    result = ibm_logs_tools.ibm_logs_search(namespace="si", query="ERROR")

    assert result["isError"] is True
    assert result["errorCategory"] == "unknown"
    assert "invalid query" in result["message"]


def test_ibm_logs_query_permission_error_on_401(monkeypatch):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
    monkeypatch.setenv("IBM_LOGS_ENDPOINT", "https://guid.api.us-south.logs.cloud.ibm.com")
    monkeypatch.setattr(httpx, "request", _fake_iam_request())

    def fake_stream(method, url, **kwargs):
        return FakeStreamResponse(401, lines=[], text="unauthorized")

    monkeypatch.setattr(httpx, "stream", fake_stream)

    result = ibm_logs_tools.ibm_logs_search(namespace="si", query="ERROR")

    assert result["isError"] is True
    assert result["errorCategory"] == "permission"
    assert result["isRetryable"] is False
    assert any("IBM_CLOUD_API_KEY" in alt or "IBM_LOGS_ENDPOINT" in alt for alt in result["alternatives"])
    joined = " ".join(result["alternatives"]).lower()
    assert "delete" not in joined and "patch" not in joined and "exec" not in joined


def test_ibm_logs_query_permission_error_on_403(monkeypatch):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
    monkeypatch.setenv("IBM_LOGS_ENDPOINT", "https://guid.api.us-south.logs.cloud.ibm.com")
    monkeypatch.setattr(httpx, "request", _fake_iam_request())

    def fake_stream(method, url, **kwargs):
        return FakeStreamResponse(403, lines=[], text="forbidden")

    monkeypatch.setattr(httpx, "stream", fake_stream)

    result = ibm_logs_tools.ibm_logs_search(namespace="si", query="ERROR")

    assert result["isError"] is True
    assert result["errorCategory"] == "permission"
    assert result["isRetryable"] is False


def test_ibm_logs_query_rate_limited_on_429_is_transient_and_retryable(monkeypatch):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
    monkeypatch.setenv("IBM_LOGS_ENDPOINT", "https://guid.api.us-south.logs.cloud.ibm.com")
    monkeypatch.setattr(httpx, "request", _fake_iam_request())

    def fake_stream(method, url, **kwargs):
        return FakeStreamResponse(429, lines=[], text="rate limit exceeded")

    monkeypatch.setattr(httpx, "stream", fake_stream)

    result = ibm_logs_tools.ibm_logs_search(namespace="si", query="ERROR")

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"
    assert result["isRetryable"] is True
    assert any("backoff" in alt.lower() or "retry" in alt.lower() for alt in result["alternatives"])


def test_ibm_logs_query_5xx_is_transient_and_retryable(monkeypatch):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "fake-key")
    monkeypatch.setenv("IBM_LOGS_ENDPOINT", "https://guid.api.us-south.logs.cloud.ibm.com")
    monkeypatch.setattr(httpx, "request", _fake_iam_request())

    def fake_stream(method, url, **kwargs):
        return FakeStreamResponse(503, lines=[], text="service unavailable")

    monkeypatch.setattr(httpx, "stream", fake_stream)

    result = ibm_logs_tools.ibm_logs_search(namespace="si", query="ERROR")

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"
    assert result["isRetryable"] is True
    assert any("gap" in alt.lower() for alt in result["alternatives"])


def test_ibm_logs_missing_endpoint_does_not_expose_secrets(monkeypatch):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "super-secret-key-value")
    monkeypatch.delenv("IBM_LOGS_ENDPOINT", raising=False)

    def unexpected(*args, **kwargs):
        raise AssertionError("should not make a network call without IBM_LOGS_ENDPOINT")

    monkeypatch.setattr(httpx, "request", unexpected)
    monkeypatch.setattr(httpx, "stream", unexpected)

    result = ibm_logs_tools.ibm_logs_search(namespace="si", query="ERROR")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert "IBM_LOGS_ENDPOINT" in result["message"]

    dumped = json.dumps(result)
    assert "super-secret-key-value" not in dumped


def test_ibm_logs_missing_api_key_does_not_expose_secrets(monkeypatch):
    monkeypatch.setenv("IBM_LOGS_ENDPOINT", "https://guid.api.us-south.logs.cloud.ibm.com")
    monkeypatch.delenv("IBM_CLOUD_API_KEY", raising=False)

    def unexpected(*args, **kwargs):
        raise AssertionError("should not reach the network without IBM_CLOUD_API_KEY")

    monkeypatch.setattr(httpx, "request", unexpected)
    monkeypatch.setattr(httpx, "stream", unexpected)

    result = ibm_logs_tools.ibm_logs_search(namespace="si", query="ERROR")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert "IBM_CLOUD_API_KEY" in result["message"]
    assert result["alternatives"]


def test_ibm_logs_iam_token_error_redacts_api_key_from_response_body(monkeypatch):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "my-leaked-looking-key")
    monkeypatch.setenv("IBM_LOGS_ENDPOINT", "https://guid.api.us-south.logs.cloud.ibm.com")

    def fake_iam_error(method, url, **kwargs):
        # Simulate an IAM error body that echoes the bad apikey back.
        return FakeResponse(400, text="invalid apikey: my-leaked-looking-key")

    monkeypatch.setattr(httpx, "request", fake_iam_error)

    def unexpected_stream(*args, **kwargs):
        raise AssertionError("should not query logs without a token")

    monkeypatch.setattr(httpx, "stream", unexpected_stream)

    result = ibm_logs_tools.ibm_logs_search(namespace="si", query="ERROR")

    assert result["isError"] is True
    dumped = json.dumps(result)
    assert "my-leaked-looking-key" not in dumped
    assert "REDACTED" in dumped
