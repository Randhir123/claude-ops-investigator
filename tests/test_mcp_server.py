def test_mcp_server_imports():
    import claude_ops.mcp.server as server

    assert server.mcp is not None
