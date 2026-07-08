from claude_ops.tools.runbook_tools import get_runbook_catalog, search_runbooks


def test_runbook_catalog_loads():
    result = get_runbook_catalog()
    assert not result["isError"]
    assert result["data"]["runbooks"]


def test_search_runbooks():
    result = search_runbooks("OOM")
    assert not result["isError"]
    assert result["data"]
