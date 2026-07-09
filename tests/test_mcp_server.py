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
        "prom_get_pod_restart_increase",
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


def test_investigate_incident_prompt_is_symptom_driven():
    import asyncio

    import claude_ops.mcp.server as server

    prompts = asyncio.run(server.mcp.list_prompts())
    prompt = next(p for p in prompts if p.name == "investigate_incident")

    args_by_name = {a.name: a for a in (prompt.arguments or [])}
    assert set(args_by_name) == {"namespace", "service", "symptom", "since_minutes"}
    assert args_by_name["symptom"].required is True
    assert args_by_name["since_minutes"].required is False

    rendered = asyncio.run(
        server.mcp.get_prompt(
            "investigate_incident",
            {"namespace": "si", "service": "event-data", "symptom": "OOMKilled restarts"},
        )
    )
    text = rendered.messages[0].content.text
    assert "OOMKilled restarts" in text
    assert "incident-coordinator" in text


def test_evidence_get_detail_not_found_is_structured_validation_error():
    import json

    import claude_ops.mcp.server as server

    result = json.loads(server.evidence_get_detail("ev_does_not_exist"))

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert result["isRetryable"] is False
    assert any("evidence_ref" in alt for alt in result["alternatives"])
