"""Render PNG charts from the time-series tools, instead of leaving the caller to reason
about a lookback window from avg/max numbers alone.

`heap_tools.get_heap_trend_over_time`, `gc_tools.get_gc_behavior_over_time`, and
`correlation_tools.get_gc_memory_correlation` already return real time series (Prometheus
range-query results) -- these functions are a thin rendering layer on top of them, producing
an actual PNG so a lookback-window question gets a picture of the shape over time, not just
a single number.

Each function returns either the same structured `ToolError` shape as every other tool in
this project (`{"isError": True, ...}`), or `{"isError": False, "png_bytes": <bytes>,
"caption": <str>}` on success -- `png_bytes` is not JSON-serializable, so the MCP server layer
(`jvm_troubleshooter.mcp.server`) is responsible for wrapping it in an `mcp.server.fastmcp.Image`
before returning it from a `@mcp.tool()`-decorated function; that's what makes the chart render
inline in an MCP client that supports image content, rather than arriving as a wall of numbers.
"""

from __future__ import annotations

import io
import math
from datetime import datetime, timezone
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless rendering -- no display server in an MCP server process

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from jvm_troubleshooter.errors import ToolError
from jvm_troubleshooter.tools.correlation_tools import get_gc_memory_correlation
from jvm_troubleshooter.tools.gc_tools import get_gc_behavior_over_time
from jvm_troubleshooter.tools.heap_tools import get_heap_trend_over_time

Series = dict[str, list[tuple[float, float]]]


def _iter_matrix_series(prom_response: dict[str, Any]) -> list[tuple[dict[str, str], list[tuple[float, float]]]]:
    """Extract (metric_labels, [(timestamp, value), ...]) pairs from a raw Prometheus
    `/api/v1/query_range` JSON response. Tolerant of an empty/missing result list, and of
    Prometheus emitting non-numeric samples ("NaN"/"+Inf") -- those points are dropped rather
    than crashing the chart."""
    result = (prom_response or {}).get("data", {}).get("result", [])
    series: list[tuple[dict[str, str], list[tuple[float, float]]]] = []
    for entry in result:
        metric = entry.get("metric", {})
        points: list[tuple[float, float]] = []
        for ts, val in entry.get("values", []):
            try:
                ts_f, val_f = float(ts), float(val)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(val_f):
                # Prometheus emits "NaN"/"+Inf"/"-Inf" as literal strings for some queries
                # (e.g. a division by a stale/absent series) -- float() parses them without
                # raising, so this check is what actually excludes them from the chart.
                continue
            points.append((ts_f, val_f))
        series.append((metric, points))
    return series


def _series_label(metric: dict[str, str], *, extra_keys: tuple[str, ...] = ()) -> str:
    base = metric.get("instance") or metric.get("pod") or metric.get("kubernetes_pod_name") or "unknown-pod"
    parts = [base]
    for key in extra_keys:
        if key in metric:
            parts.append(metric[key])
    return " / ".join(parts)


def _no_data_error(namespace: str, service: str, lookback_minutes: int) -> dict[str, Any]:
    return ToolError(
        "business",
        False,
        "No time-series data points were returned for this namespace/service/window -- nothing to chart.",
        attempted={"namespace": namespace, "service": service, "lookback_minutes": lookback_minutes},
        alternatives=[
            "Widen lookback_minutes -- the window may be shorter than the scrape interval",
            "Confirm JVM_LABEL_KEY and the service name actually match running pods "
            "(see get_gc_activity/get_heap_status for an instant-query sanity check)",
        ],
    ).to_dict()


# --- Mermaid text charts --------------------------------------------------------------
#
# Some MCP clients (IBM Bob) never display image tool results, but do render Mermaid code
# blocks in the assistant's reply. So each chart also comes as small Mermaid `xychart-beta`
# blocks the model can paste into its answer. xychart has no legend, so every block is ONE
# line, aggregated across pods, with what it shows spelled out in its title.

_MERMAID_MAX_POINTS = 30


def _across_pods(series_list: list[list[tuple[float, float]]], how: str) -> list[tuple[float, float]]:
    """Combine per-pod series into one: max or mean of the pods' values at each timestamp."""
    by_ts: dict[float, list[float]] = {}
    for points in series_list:
        for ts, value in points:
            by_ts.setdefault(ts, []).append(value)
    pick = max if how == "max" else (lambda vs: sum(vs) / len(vs))
    return [(ts, pick(values)) for ts, values in sorted(by_ts.items())]


def _downsample(points: list[tuple[float, float]], how: str) -> list[tuple[float, float]]:
    """At most _MERMAID_MAX_POINTS buckets, keeping each bucket's max or mean (and its first time)."""
    if len(points) <= _MERMAID_MAX_POINTS:
        return points
    size = math.ceil(len(points) / _MERMAID_MAX_POINTS)
    out = []
    for i in range(0, len(points), size):
        chunk = points[i:i + size]
        values = [v for _, v in chunk]
        out.append((chunk[0][0], max(values) if how == "max" else sum(values) / len(values)))
    return out


def _mermaid_line(title: str, y_label: str, points: list[tuple[float, float]], *, y_max: float | None = None) -> str:
    """One Mermaid xychart-beta line chart. Times are HH:MM in UTC."""
    if not points:
        return ""
    labels = ", ".join(f'"{datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M")}"' for ts, _ in points)
    values = ", ".join(f"{v:.2f}".rstrip("0").rstrip(".") for _, v in points)
    top = y_max if y_max is not None else max(v for _, v in points)
    top = top * 1.05 if top > 0 else 1
    safe_title = title.replace('"', "'")
    return (
        "```mermaid\nxychart-beta\n"
        f'    title "{safe_title}"\n'
        f"    x-axis [{labels}]\n"
        f'    y-axis "{y_label}" 0 --> {top:.2f}\n'
        f"    line [{values}]\n```"
    )


def _per_pod_sum(entries: list[tuple[dict[str, str], list[tuple[float, float]]]]) -> list[list[tuple[float, float]]]:
    """Sum series that belong to the same pod (e.g. scavenge + global) into one series per pod."""
    pods: dict[str, dict[float, float]] = {}
    for metric, points in entries:
        pod = pods.setdefault(_series_label(metric), {})
        for ts, value in points:
            pod[ts] = pod.get(ts, 0.0) + value
    return [sorted(by_ts.items()) for by_ts in pods.values()]


_GB = 1e9


def _scaled(points: list[tuple[float, float]], factor: float) -> list[tuple[float, float]]:
    return [(ts, v / factor) for ts, v in points]


def _mermaid_block(charts: list[str]) -> str:
    return "\n\n".join(c for c in charts if c)


def _render_panels_png(*, title: str, panels: list[dict[str, Any]]) -> bytes:
    """Render one or more stacked line-chart panels sharing a time axis.

    Each panel: {"y_label": str, "series": Series}.
    """
    fig, axes = plt.subplots(len(panels), 1, figsize=(10, 4 * len(panels)), squeeze=False)
    for ax, panel in zip(axes[:, 0], panels):
        any_points = False
        for label, points in panel["series"].items():
            if not points:
                continue
            any_points = True
            times = [datetime.fromtimestamp(t, tz=timezone.utc) for t, _ in points]
            values = [v for _, v in points]
            ax.plot(times, values, label=label, linewidth=1.4)
        ax.set_ylabel(panel["y_label"])
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone.utc))
        if any_points:
            ax.legend(fontsize=7, loc="upper left")
        else:
            ax.text(
                0.5, 0.5, "no data points in this window", ha="center", va="center",
                transform=ax.transAxes, fontsize=9, color="gray",
            )
    axes[0, 0].set_title(title)
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return buf.getvalue()


def _render_dual_axis_png(
    *, title: str, left_label: str, left_series: Series, right_label: str, right_series: Series
) -> bytes:
    """Render one chart with two y-axes sharing a time axis -- for overlaying two differently
    -scaled signals (e.g. heap bytes vs. GC collections/min) to compare their shapes directly."""
    fig, ax_left = plt.subplots(figsize=(10, 5))
    ax_right = ax_left.twinx()

    lines = []
    for label, points in left_series.items():
        if not points:
            continue
        times = [datetime.fromtimestamp(t, tz=timezone.utc) for t, _ in points]
        values = [v for _, v in points]
        (line,) = ax_left.plot(times, values, linewidth=1.6, linestyle="-", label=f"{label} (left)")
        lines.append(line)
    for label, points in right_series.items():
        if not points:
            continue
        times = [datetime.fromtimestamp(t, tz=timezone.utc) for t, _ in points]
        values = [v for _, v in points]
        (line,) = ax_right.plot(times, values, linewidth=1.2, linestyle="--", label=f"{label} (right)")
        lines.append(line)

    ax_left.set_ylabel(left_label)
    ax_right.set_ylabel(right_label)
    ax_left.set_title(title)
    ax_left.grid(True, alpha=0.3)
    ax_left.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone.utc))
    if lines:
        ax_left.legend(lines, [line.get_label() for line in lines], fontsize=7, loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return buf.getvalue()


def render_heap_trend_chart(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
) -> dict[str, Any]:
    """Heap used/max as a line chart over the lookback window, one pair of lines per pod."""
    result = get_heap_trend_over_time(namespace, service, lookback_minutes, step)
    if result.get("isError"):
        return result
    data = result["data"]

    series: Series = {}
    for metric, points in _iter_matrix_series(data["heap_used_bytes_over_time"]):
        series[f"{_series_label(metric)} used"] = points
    for metric, points in _iter_matrix_series(data["heap_max_bytes_over_time"]):
        series[f"{_series_label(metric)} max"] = points

    if not series:
        return _no_data_error(namespace, service, data["lookback_minutes"])

    png_bytes = _render_panels_png(
        title=f"Heap used/max -- {service} ({namespace}), last {data['lookback_minutes']}m",
        panels=[{"y_label": "bytes", "series": series}],
    )
    used = [points for _, points in _iter_matrix_series(data["heap_used_bytes_over_time"])]
    ceiling = max((v for _, points in _iter_matrix_series(data["heap_max_bytes_over_time"]) for _, v in points), default=None)
    ceiling_gb = ceiling / _GB if ceiling else None
    ceiling_note = f", heap max {ceiling_gb:.1f} GB" if ceiling_gb else ""
    mermaid = _mermaid_block([
        _mermaid_line(f"Heap used, highest pod (GB{ceiling_note})", "GB",
                      _scaled(_downsample(_across_pods(used, "max"), "max"), _GB), y_max=ceiling_gb),
        _mermaid_line("Heap used, average across pods (GB)", "GB",
                      _scaled(_downsample(_across_pods(used, "mean"), "mean"), _GB), y_max=ceiling_gb),
    ])
    return {
        "isError": False,
        "png_bytes": png_bytes,
        "mermaid": mermaid,
        "caption": f"Heap used/max per pod over the last {data['lookback_minutes']}m for {service}/{namespace}.",
    }


def render_gc_behavior_chart(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
) -> dict[str, Any]:
    """GC frequency (collections/min) and overhead (%) as stacked line charts over the lookback
    window, broken out by pod and generation (scavenge/global)."""
    result = get_gc_behavior_over_time(namespace, service, lookback_minutes, step)
    if result.get("isError"):
        return result
    data = result["data"]

    freq_series: Series = {}
    for metric, points in _iter_matrix_series(data["gc_frequency_per_min"]):
        freq_series[_series_label(metric, extra_keys=("name",))] = points
    overhead_series: Series = {}
    for metric, points in _iter_matrix_series(data["gc_overhead_percent"]):
        overhead_series[_series_label(metric, extra_keys=("name",))] = points

    if not freq_series and not overhead_series:
        return _no_data_error(namespace, service, data["lookback_minutes"])

    freq_by_pod = _per_pod_sum(_iter_matrix_series(data["gc_frequency_per_min"]))
    overhead_by_pod = _per_pod_sum(_iter_matrix_series(data["gc_overhead_percent"]))
    mermaid = _mermaid_block([
        _mermaid_line("GC collections per minute, busiest pod (all collectors)", "per min",
                      _downsample(_across_pods(freq_by_pod, "max"), "max")),
        _mermaid_line("GC overhead %, busiest pod (all collectors)", "%",
                      _downsample(_across_pods(overhead_by_pod, "max"), "max")),
    ])
    png_bytes = _render_panels_png(
        title=f"GC frequency & overhead -- {service} ({namespace}), last {data['lookback_minutes']}m",
        panels=[
            {"y_label": "collections/min", "series": freq_series},
            {"y_label": "GC overhead (%)", "series": overhead_series},
        ],
    )
    return {
        "isError": False,
        "png_bytes": png_bytes,
        "mermaid": mermaid,
        "caption": (
            f"GC frequency (top) and overhead % (bottom) per pod+generation over the last "
            f"{data['lookback_minutes']}m for {service}/{namespace}."
        ),
    }


def render_gc_memory_correlation_chart(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
) -> dict[str, Any]:
    """Heap used and GC frequency overlaid on one dual-axis chart over the lookback window --
    the leak-vs-load-spike visual: a rising heap floor alongside climbing GC frequency points
    toward a leak/undersized heap; heap returning to baseline each cycle points toward load."""
    result = get_gc_memory_correlation(namespace, service, lookback_minutes, step)
    if result.get("isError"):
        return result
    data = result["data"]

    heap_series: Series = {}
    for metric, points in _iter_matrix_series(data["heap_used_bytes_over_time"]):
        heap_series[_series_label(metric)] = points
    freq_series: Series = {}
    for metric, points in _iter_matrix_series(data["gc_frequency_per_min_over_time"]):
        freq_series[_series_label(metric, extra_keys=("name",))] = points

    if not heap_series and not freq_series:
        return _no_data_error(namespace, service, data["lookback_minutes"])

    heap_lists = [points for _, points in _iter_matrix_series(data["heap_used_bytes_over_time"])]
    freq_by_pod = _per_pod_sum(_iter_matrix_series(data["gc_frequency_per_min_over_time"]))
    mermaid = _mermaid_block([
        _mermaid_line("Heap used, average across pods (GB) -- does the floor rise?", "GB",
                      _scaled(_downsample(_across_pods(heap_lists, "mean"), "mean"), _GB)),
        _mermaid_line("GC collections per minute, average per pod -- same time axis", "per min",
                      _downsample(_across_pods(freq_by_pod, "mean"), "mean")),
    ])
    png_bytes = _render_dual_axis_png(
        title=f"Heap vs GC frequency -- {service} ({namespace}), last {data['lookback_minutes']}m",
        left_label="heap used (bytes)",
        left_series=heap_series,
        right_label="GC collections/min",
        right_series=freq_series,
    )
    return {
        "isError": False,
        "png_bytes": png_bytes,
        "mermaid": mermaid,
        "caption": (
            f"Heap used (solid) vs GC frequency (dashed) over the last {data['lookback_minutes']}m. "
            f"{data['how_to_read']}"
        ),
    }
