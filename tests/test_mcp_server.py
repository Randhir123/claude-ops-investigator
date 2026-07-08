def test_mcp_server_imports():
    import claude_ops.mcp.server as server

    assert server.mcp is not None


def test_mcp_server_lists_all_tools():
    import asyncio

    import claude_ops.mcp.server as server

    tools = asyncio.run(server.mcp.list_tools())
    tool_names = {tool.name for tool in tools}

    expected = {
        # original k8s + runbook + evidence tools
        "k8s_list_pods",
        "k8s_describe_pod",
        "k8s_get_pod_logs",
        "k8s_get_recent_namespace_events",
        "k8s_top_pods",
        "runbook_search",
        "evidence_get_detail",
        # Prometheus tools
        "prom_query_instant",
        "prom_get_pod_restart_counts",
        "prom_get_pod_cpu_usage",
        "prom_get_pod_memory_usage",
        "prom_get_http_error_rate",
        "prom_get_latency_p95",
        "prom_ensure_connection",
        # IBM Cloud Logs tools
        "ibm_logs_search",
        "ibm_logs_search_errors",
        "ibm_logs_search_probe_failures",
        "ibm_logs_search_text",
    }

    assert expected <= tool_names
