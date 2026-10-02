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
        ("render_thread_trend_chart", "thread-trend"),
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


# --- Mermaid text charts (for clients that render Mermaid but not image results) -----------


def test_mermaid_text_is_appended_with_paste_instruction(monkeypatch, tmp_path):
    monkeypatch.setenv("JVM_CHART_DIR", str(tmp_path))
    block = '```mermaid\nxychart-beta\n    title "Heap used"\n    x-axis ["10:00"]\n    y-axis "GB" 0 --> 1\n    line [0.5]\n```'
    monkeypatch.setattr(
        chart_tools, "render_heap_trend_chart",
        lambda *a, **k: {"isError": False, "png_bytes": PNG, "caption": "c", "mermaid": block},
    )

    text = _call("render_heap_trend_chart")[1].text

    assert "paste the block(s) below into your reply exactly as given" in text
    assert text.endswith(block)


def test_across_pods_and_downsample():
    a = [(0, 1.0), (60, 5.0)]
    b = [(0, 3.0), (60, 1.0)]
    assert chart_tools._across_pods([a, b], "max") == [(0, 3.0), (60, 5.0)]
    assert chart_tools._across_pods([a, b], "mean") == [(0, 2.0), (60, 3.0)]

    long = [(i * 60, float(i)) for i in range(95)]
    down = chart_tools._downsample(long, "max")
    assert len(down) <= chart_tools._MERMAID_MAX_POINTS
    assert down[0] == (0, 3.0) and down[-1][1] == 94.0  # buckets of 4, max of each


def test_mermaid_line_format():
    text = chart_tools._mermaid_line('Heap "used" (GB)', "GB", [(1790000000, 1.234), (1790000060, 2.0)], y_max=4.0)

    assert text.startswith("```mermaid\nxychart-beta\n") and text.endswith("```")
    assert "title \"Heap 'used' (GB)\"" in text
    assert 'x-axis ["14:13", "14:14"]' in text  # UTC HH:MM
    assert 'y-axis "GB" 0 --> 4.20' in text  # 5% headroom over the ceiling
    assert "line [1.23, 2]" in text
    assert chart_tools._mermaid_line("x", "y", []) == ""


def test_per_pod_sum_adds_generations():
    entries = [
        ({"instance": "p1", "name": "scavenge"}, [(0, 4.0), (60, 6.0)]),
        ({"instance": "p1", "name": "global"}, [(0, 1.0)]),
        ({"instance": "p2", "name": "scavenge"}, [(0, 2.0)]),
    ]
    assert sorted(chart_tools._per_pod_sum(entries)) == [[(0, 2.0)], [(0, 5.0), (60, 6.0)]]
