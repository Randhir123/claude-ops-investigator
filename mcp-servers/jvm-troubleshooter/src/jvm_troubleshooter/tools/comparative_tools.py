"""Before/after deploy-anchored snapshot comparison.

Answers "did this deploy change JVM behavior" by comparing windowed averages
of heap, GC, and thread signals immediately before vs. immediately after a
given anchor timestamp -- the moment a rollout, config change, or restart
happened -- rather than requiring the caller to run two separate tool calls
and eyeball the difference themselves.

METHOD: uses PromQL's `@ <unix_timestamp>` modifier to evaluate an
`avg_over_time(...[window]m] @ <timestamp>)` range vector as of a specific
past instant, computed twice per signal: once anchored at `deploy_timestamp`
(covering the `window_minutes` immediately *before* it) and once anchored at
`deploy_timestamp + window_minutes*60` (covering the `window_minutes`
immediately *after* it). This needs no new query primitives beyond what
`instant_query` already sends -- the anchor lives inside the PromQL string
itself.

REQUIREMENTS: the `@` modifier must be enabled on the target Prometheus (the
default since Prometheus 2.33; older versions, or one explicitly started
without `--enable-feature=promql-at-modifier` on an older release line, will
make every query in this module fail). `deploy_timestamp` must be a Unix
epoch (seconds) as an int or a string of digits -- this project doesn't
parse ISO-8601 dates itself, to avoid a timezone-ambiguity bug; convert on
the caller's side.

CAVEATS:
- This is a correlation in time, not a causal claim -- a change straddling
  `deploy_timestamp` is consistent with the deploy causing it, but so is
  any other coincident event (a traffic pattern change, an unrelated
  restart, a dependency incident). Always corroborate against
  `k8s`/deployment-history evidence before attributing a change to "the
  deploy" in an incident report.
- Both windows still need real traffic in them to mean anything -- comparing
  two `window_minutes` windows that are each mostly idle (e.g. right after a
  rollout, before load ramps back up) produces a noisy, low-confidence
  comparison even though the numbers come out clean.
- `window_minutes` after the deploy will be clamped down if it would reach
  into the future relative to "now" -- a deploy from a few minutes ago won't
  have a full post-window yet, and the tool will return whatever partial
  window is available rather than erroring, but the comparison is weaker
  with less data.
"""

from __future__ import annotations

import time as _time
from typing import Any

from jvm_troubleshooter.errors import ToolError, ok
from jvm_troubleshooter.tools.prometheus_client import instant_query, namespace_service_selector

_COMPARISON_NOTE = (
    "Correlation in time, not a causal claim -- corroborate against deployment/restart history "
    "before attributing a change to 'the deploy' in an incident report. Each window needs real "
    "traffic in it to mean anything; a mostly-idle window (e.g. right after a rollout, before "
    "load ramps back up) produces a clean-looking but low-confidence number. Requires the "
    "Prometheus @ modifier (default-enabled since Prometheus 2.33) -- every field below will "
    "come back as a query error on an older or explicitly-disabled Prometheus."
)


def _percent_change(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    if before == 0:
        return None  # avoid a divide-by-zero producing a misleading +inf/"huge spike" reading
    return 100 * (after - before) / before


def _extract_vector_by_instance(prom_response: dict[str, Any]) -> dict[str, float]:
    result = (prom_response or {}).get("data", {}).get("result", [])
    out: dict[str, float] = {}
    for entry in result:
        instance = entry.get("metric", {}).get("instance", "unknown-pod")
        try:
            out[instance] = float(entry.get("value", [None, None])[1])
        except (TypeError, ValueError):
            continue
    return out


def _compare_metric(
    *, metric_expr_template: str, deploy_ts: int, window_minutes: int
) -> dict[str, Any]:
    """Runs one `avg_over_time(<metric_expr_template>[window]m] @ ts)` query anchored before and
    after `deploy_ts`, using `window_minutes` on both sides, and returns `{before, after,
    percent_change}` per pod instance. `metric_expr_template` must be a complete PromQL vector
    selector (no aggregation, no `@` modifier, no range) -- this wraps it in
    `avg_over_time(...[Nm] @ ts)` itself."""
    after_ts = deploy_ts + window_minutes * 60

    before_expr = f"avg_over_time(({metric_expr_template})[{window_minutes}m] @ {deploy_ts})"
    after_expr = f"avg_over_time(({metric_expr_template})[{window_minutes}m] @ {after_ts})"

    before_result = instant_query(before_expr)
    if before_result.get("isError"):
        return before_result
    after_result = instant_query(after_expr)
    if after_result.get("isError"):
        return after_result

    before_by_pod = _extract_vector_by_instance(before_result["data"])
    after_by_pod = _extract_vector_by_instance(after_result["data"])

    per_pod: dict[str, Any] = {}
    for instance in set(before_by_pod) | set(after_by_pod):
        before_val = before_by_pod.get(instance)
        after_val = after_by_pod.get(instance)
        per_pod[instance] = {
            "before": before_val,
            "after": after_val,
            "percent_change": _percent_change(before_val, after_val),
        }

    return ok(per_pod)


def get_before_after_deploy_comparison(
    namespace: str, service: str, deploy_timestamp: int | str, window_minutes: int = 15
) -> dict[str, Any]:
    """Heap, GC frequency/overhead, and thread-count averages in the window immediately before
    vs. immediately after `deploy_timestamp` (a Unix epoch in seconds), per pod -- use this to
    answer "did this deploy/restart/config-change change JVM behavior," not as a causal proof
    on its own. See the module docstring's caveats."""
    try:
        deploy_ts = int(deploy_timestamp)
    except (TypeError, ValueError):
        return ToolError(
            "validation",
            False,
            "deploy_timestamp must be a Unix epoch in seconds (int or digit string).",
            attempted={"deploy_timestamp": deploy_timestamp},
        ).to_dict()

    window_m = max(1, min(int(window_minutes), 180))
    selector = namespace_service_selector(namespace, service)

    signals = {
        "heap_used_bytes": f"java_lang_Memory_HeapMemoryUsage_used{{{selector}}}",
        "gc_frequency_per_min": f"rate(java_lang_GarbageCollector_CollectionCount{{{selector}}}[5m]) * 60",
        "gc_overhead_percent": f"rate(java_lang_GarbageCollector_CollectionTime{{{selector}}}[5m]) / 10",
        "thread_count": f"java_lang_Threading_ThreadCount{{{selector}}}",
    }

    comparison: dict[str, Any] = {}
    for key, expr in signals.items():
        result = _compare_metric(metric_expr_template=expr, deploy_ts=deploy_ts, window_minutes=window_m)
        if result.get("isError"):
            return result
        comparison[key] = result["data"]

    now = int(_time.time())
    after_window_end = deploy_ts + window_m * 60
    truncated_post_window = after_window_end > now

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "deploy_timestamp": deploy_ts,
            "window_minutes": window_m,
            "signals": comparison,
            "post_window_truncated": truncated_post_window,
            "caveat": _COMPARISON_NOTE
            + (
                " post_window_truncated is true here -- deploy_timestamp is recent enough that the "
                "full post-deploy window hasn't elapsed yet, so this comparison used whatever "
                "partial window Prometheus had."
                if truncated_post_window
                else ""
            ),
        }
    )
