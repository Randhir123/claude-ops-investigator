from __future__ import annotations

import asyncio

import pytest

from jvm_troubleshooter.mcp import server
from jvm_troubleshooter.tools import chart_tools

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
ARGS = {"namespace": "si", "service": "time-series-query", "lookback_minutes": 60}


def _call(tool: str):
    result = asyncio.run(server.mcp.call_tool(tool, ARGS))
    # newer FastMCP returns (content, structured); older returns the content list
    return result[0] if isinstance(result, tuple) else result


@pytest.mark.parametrize(
    "tool, kind",
    [
        ("render_heap_trend_chart", "heap-trend"),
        ("render_gc_behavior_chart", "gc-behavior"),
        ("render_gc_memory_correlation_chart", "gc-memory-correlation"),
    ],
)
def test_chart_tools_return_image_and_saved_file_path(monkeypatch, tmp_path, tool, kind):
    monkeypatch.setenv("JVM_CHART_DIR", str(tmp_path))
    monkeypatch.setattr(
        chart_tools, tool, lambda *a, **k: {"isError": False, "png_bytes": PNG, "caption": "Heap used/max per pod"}
    )

    content = _call(tool)

    assert [block.type for block in content] == ["image", "text"]
    assert content[0].mimeType == "image/png"
    saved = list(tmp_path.glob(f"time-series-query-{kind}-*.png"))
    assert len(saved) == 1 and saved[0].read_bytes() == PNG
    assert f"Chart saved to {saved[0]}" in content[1].text
    assert content[1].text.startswith("Heap used/max per pod")


def test_chart_error_is_returned_as_json_without_saving(monkeypatch, tmp_path):
    monkeypatch.setenv("JVM_CHART_DIR", str(tmp_path))
    monkeypatch.setattr(
        chart_tools,
        "render_heap_trend_chart",
        lambda *a, **k: {"isError": True, "errorCategory": "business", "message": "nothing to chart"},
    )

    content = _call("render_heap_trend_chart")

    assert [block.type for block in content] == ["text"]
    assert "nothing to chart" in content[0].text
    assert list(tmp_path.iterdir()) == []


def test_chart_still_returned_when_file_cannot_be_saved(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("JVM_CHART_DIR", str(blocker))
    monkeypatch.setattr(chart_tools, "render_heap_trend_chart", lambda *a, **k: {"isError": False, "png_bytes": PNG})

    content = _call("render_heap_trend_chart")

    assert [block.type for block in content] == ["image", "text"]
    assert "could not be saved" in content[1].text


def test_default_chart_dir_is_the_repo_runs_folder(monkeypatch):
    monkeypatch.delenv("JVM_CHART_DIR", raising=False)

    directory = server._chart_dir()

    assert directory.parts[-2:] == ("runs", "charts")
    assert (directory.parent.parent / "mcp-servers").is_dir()
