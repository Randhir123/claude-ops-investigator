from __future__ import annotations

from jvm_troubleshooter.tools import thread_tools


def test_get_thread_status_queries_thread_and_class_counts(prom_env, capture_queries):
    result = thread_tools.get_thread_status("si-dev-001a", "time-series-writer")

    assert result["isError"] is False
    assert len(capture_queries) == 2
    assert "java_lang_Threading_ThreadCount" in capture_queries[0]["params"]["query"]
    assert "java_lang_ClassLoading_LoadedClassCount" in capture_queries[1]["params"]["query"]
    assert "caveat" in result["data"]
    assert "TMDA" in result["data"]["caveat"]
