"""Generate dashboards/jvm-troubleshooting.json, a Grafana dashboard for OpenJ9 JVM troubleshooting.

It charts the same signals the jvm-troubleshooter MCP tools read, from JMX Exporter-scraped
OpenJ9 JVMs (plus cAdvisor and kube-state-metrics for container memory):

    python dashboards/generate_jvm_troubleshooting.py   # rewrites the JSON next to this file

Edit the panels here, not in the JSON. tests/test_dashboard.py checks that the committed JSON
matches this generator.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).with_name("jvm-troubleshooting.json")

DS = {"type": "prometheus", "uid": "${datasource}"}

# Selector for the JVM series of the chosen service and pods.
S = 'namespace="$namespace", $label_key=~"$service", pod=~"$pod"'
# Keeps container-level series (cAdvisor, kube-state-metrics) to the JVM containers selected above,
# so other workloads in the namespace never show up.
JVM_CONTAINERS = f"max by (namespace, pod, container) (java_lang_Threading_ThreadCount{{{S}}})"
WORKING_SET = (
    'max by (namespace, pod, container) (container_memory_working_set_bytes{namespace="$namespace", '
    f'container!="", container!="POD"}}) and on (namespace, pod, container) {JVM_CONTAINERS}'
)
MEMORY_LIMIT = (
    'max by (namespace, pod, container) (kube_pod_container_resource_limits{namespace="$namespace", '
    f'resource="memory"}}) and on (namespace, pod, container) {JVM_CONTAINERS}'
)
CPU_LIMIT = (
    'max by (namespace, pod, container) (kube_pod_container_resource_limits{namespace="$namespace", '
    f'resource="cpu"}}) and on (namespace, pod, container) {JVM_CONTAINERS}'
)
HEADROOM = f"100 * (1 - ({WORKING_SET}) / ({MEMORY_LIMIT}))"
HEAP_PERCENT = (
    f"100 * max by (pod) (java_lang_Memory_HeapMemoryUsage_used{{{S}}}) / "
    f"max by (pod) (java_lang_Memory_HeapMemoryUsage_max{{{S}}})"
)
GC_OVERHEAD = f"sum by (pod) (rate(java_lang_GarbageCollector_CollectionTime{{{S}}}[5m])) / 10"
CHURN = f"sum by (pod) (rate(java_lang_Threading_TotalStartedThreadCount{{{S}}}[5m]))"

_next_id = 0


def _id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def target(expr: str, legend: str = "", ref: str = "A", instant: bool = False, fmt: str = "time_series") -> dict:
    t = {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref, "format": fmt}
    if instant:
        t["instant"] = True
        t["range"] = False
    return t


def row(title: str, y: int) -> dict:
    return {"type": "row", "id": _id(), "title": title, "collapsed": False, "panels": [],
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}}


def stat(title: str, expr: str, x: int, y: int, unit: str, description: str, steps: list[tuple[str, float | None]],
         decimals: int = 1) -> dict:
    return {
        "type": "stat", "id": _id(), "title": title, "description": description, "datasource": DS,
        "gridPos": {"h": 4, "w": 4, "x": x, "y": y},
        "targets": [target(expr, instant=True)],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "background", "graphMode": "none", "textMode": "value"},
        "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals,
                                     "thresholds": {"mode": "absolute",
                                                    "steps": [{"color": c, "value": v} for c, v in steps]}},
                        "overrides": []},
    }


def timeseries(title: str, targets: list[dict], x: int, y: int, w: int, unit: str, description: str,
               overrides: list[dict] | None = None, h: int = 8, thresholds: list[tuple[str, float | None]] | None = None,
               fill: int = 0) -> dict:
    defaults = {"unit": unit, "custom": {"lineWidth": 1, "fillOpacity": fill, "showPoints": "never",
                                         "spanNulls": True}}
    if thresholds:
        defaults["thresholds"] = {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in thresholds]}
        defaults["custom"]["thresholdsStyle"] = {"mode": "dashed"}
    return {
        "type": "timeseries", "id": _id(), "title": title, "description": description, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": targets,
        "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
    }


def right_axis(name_regex: str, unit: str) -> dict:
    return {"matcher": {"id": "byRegexp", "options": name_regex},
            "properties": [{"id": "custom.axisPlacement", "value": "right"}, {"id": "unit", "value": unit}]}


def dashed(name_regex: str) -> dict:
    return {"matcher": {"id": "byRegexp", "options": name_regex},
            "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                           {"id": "custom.lineWidth", "value": 2}]}


def variable(name: str, label: str, query: str, multi: bool = False, include_all: bool = False) -> dict:
    v = {"name": name, "label": label, "type": "query", "datasource": DS, "refresh": 2, "sort": 1,
         "query": {"query": query, "refId": f"{name}-query"}, "definition": query,
         "multi": multi, "includeAll": include_all, "current": {}, "options": []}
    if include_all:
        v["allValue"] = ".*"
    return v


def build() -> dict:
    global _next_id
    _next_id = 0
    good, warn, bad = "green", "orange", "red"
    panels: list[dict] = []
    y = 0

    # --- At a glance ---------------------------------------------------------------
    panels.append(row("At a glance", y)); y += 1
    panels += [
        stat("JVM pods", f"count(java_lang_Threading_ThreadCount{{{S}}})", 0, y, "short",
             "Pods of this service with JVM metrics right now.", [("blue", None)], decimals=0),
        stat("Lowest memory headroom", f"min({HEADROOM})", 4, y, "percent",
             "Smallest gap between a pod's container working set and its memory limit. Near 0 = OOMKilled risk, "
             "whatever the heap looks like.", [(bad, None), (warn, 10), (good, 20)]),
        stat("Highest heap used", f"max({HEAP_PERCENT})", 8, y, "percent",
             "Highest heap used / heap max across pods. Heap max is -Xmx, not the container limit.",
             [(good, None), (warn, 80), (bad, 90)]),
        stat("Highest GC overhead (5m)", f"max({GC_OVERHEAD})", 12, y, "percent",
             "Share of time in GC on the busiest pod, over 5 minutes.", [(good, None), (warn, 5), (bad, 10)], decimals=2),
        stat("Threads started / s (avg pod)", f"avg({CHURN})", 16, y, "short",
             "Thread creation rate per pod. A high rate with a flat thread count is churn: pool threads expiring "
             "and being recreated.", [("blue", None)]),
        stat("Deadlocked threads", f"sum(jvm_threads_deadlocked{{{S}}})", 20, y, "short",
             "From the JVM's own deadlock detection at scrape time. 0 does not rule out livelock or threads stuck "
             "on I/O.", [(good, None), (bad, 1)], decimals=0),
    ]
    y += 4

    # --- Memory ---------------------------------------------------------------------
    panels.append(row("Memory: container limit, heap, pools", y)); y += 1
    panels += [
        timeseries("Container memory vs limit", [
            target(WORKING_SET, "{{pod}} working set", "A"),
            target(MEMORY_LIMIT, "{{pod}} limit", "B"),
        ], 0, y, 12, "bytes",
            "Working set is what the kubelet and the OOM killer act on. Dashed = the container memory limit.",
            overrides=[dashed(".* limit")]),
        timeseries("Memory headroom below the limit", [target(HEADROOM, "{{pod}}")], 12, y, 12, "percent",
                   "100% minus working set / limit. Under 10% means the pod is close to being OOMKilled.",
                   thresholds=[(bad, None), (warn, 10), (good, 20)]),
    ]
    y += 8
    panels += [
        timeseries("Heap used vs max", [
            target(f"max by (pod) (java_lang_Memory_HeapMemoryUsage_used{{{S}}})", "{{pod}} used", "A"),
            target(f"max(java_lang_Memory_HeapMemoryUsage_max{{{S}}})", "heap max", "B"),
        ], 0, y, 12, "bytes",
            "A sawtooth that returns to the same floor after GC is normal; a floor that keeps rising suggests a "
            "leak. Heap max is -Xmx, not the container limit.", overrides=[dashed("heap max")]),
        timeseries("Tenured (old gen) after GC: the leak signal", [
            target(f'sum by (pod) (java_lang_MemoryPool_CollectionUsage_used{{{S}, name=~"tenured-SOA|tenured-LOA"}})',
                   "{{pod}}"),
        ], 12, y, 12, "bytes",
            "Old-gen usage right after the last collection, i.e. what survived GC. Flat or falling = healthy; "
            "steadily rising over hours = objects are being retained (leak candidate). Use a long time range."),
    ]
    y += 8
    panels += [
        timeseries("Memory pools (average per pod)", [
            target(f"avg by (name) (java_lang_MemoryPool_Usage_used{{{S}}})", "{{name}}"),
        ], 0, y, 12, "bytes",
            "OpenJ9 pools: nursery (young gen), tenured SOA/LOA (old gen), JIT code/data cache, class storage and "
            "other non-heap storage.", fill=10),
        timeseries("Non-heap and direct buffers", [
            target(f"max by (pod) (java_lang_Memory_NonHeapMemoryUsage_used{{{S}}})", "{{pod}} non-heap", "A"),
            target(f'max by (pod) (jvm_buffer_pool_used_bytes{{{S}, pool="direct"}})', "{{pod}} direct buffers", "B"),
        ], 12, y, 12, "bytes",
            "Memory outside the heap that still counts against the container limit. Growing direct buffers often "
            "come from network I/O (Netty, NIO)."),
    ]
    y += 8

    # --- GC -------------------------------------------------------------------------
    panels.append(row("Garbage collection", y)); y += 1
    panels += [
        timeseries("GC overhead per pod", [target(GC_OVERHEAD, "{{pod}}")], 0, y, 12, "percent",
                   "Share of wall time spent in GC (all collectors), 5-minute rate. Sustained above ~5-10% means "
                   "GC is costing throughput.", thresholds=[(good, None), (warn, 5), (bad, 10)]),
        timeseries("GC collections per minute", [
            target(f"sum by (pod, name) (rate(java_lang_GarbageCollector_CollectionCount{{{S}}}[5m])) * 60",
                   "{{pod}} {{name}}"),
        ], 12, y, 12, "short",
            "scavenge = young-gen collections (frequent, cheap); global = old-gen collections (rare, expensive)."),
    ]
    y += 8
    panels += [
        timeseries("Average GC pause by collector", [
            target(f"sum by (name) (rate(java_lang_GarbageCollector_CollectionTime{{{S}}}[5m])) / "
                   f"(sum by (name) (rate(java_lang_GarbageCollector_CollectionCount{{{S}}}[5m])) > 0)", "{{name}}"),
        ], 0, y, 12, "ms",
            "Mean pause per collection over 5 minutes. Averages hide the worst pauses: for real per-pause "
            "max/p99 use the GC log (jvm_get_gc_log_events / jvm_analyze_gc_log). A gap = no collections of "
            "that type in the 5-minute window."),
        timeseries("Heap used vs GC frequency (leak or load?)", [
            target(f"avg(java_lang_Memory_HeapMemoryUsage_used{{{S}}})", "heap used (avg pod)", "A"),
            target(f"avg(sum by (pod) (rate(java_lang_GarbageCollector_CollectionCount{{{S}}}[5m]))) * 60",
                   "GC per minute (avg pod)", "B"),
        ], 12, y, 12, "bytes",
            "Heap that returns to baseline while GC rises and falls = load. A rising heap floor together with "
            "climbing GC frequency = leak or undersized heap.", overrides=[right_axis("GC per minute.*", "short")]),
    ]
    y += 8

    # --- Threads --------------------------------------------------------------------
    panels.append(row("Threads", y)); y += 1
    panels += [
        timeseries("Live threads", [target(f"max by (pod) (java_lang_Threading_ThreadCount{{{S}}})", "{{pod}}")],
                   0, y, 8, "short", "Thread COUNT, not state. A steady climb means threads are piling up."),
        timeseries("Threads started per second (churn)", [target(CHURN, "{{pod}}")], 8, y, 8, "short",
                   "Thread creation rate. High with a flat live count = churn (short keep-alive, pools created per "
                   "task). Which pool churns needs a javacore series (jvm_compare_javacores)."),
        timeseries("Daemon threads", [
            target(f"max by (pod) (java_lang_Threading_DaemonThreadCount{{{S}}})", "{{pod}}"),
        ], 16, y, 8, "short", "Daemon threads per pod; peak count is in the MCP tool get_thread_trend."),
    ]
    y += 8

    # --- CPU & process --------------------------------------------------------------
    panels.append(row("CPU, file descriptors, class loading", y)); y += 1
    panels += [
        timeseries("Process CPU", [
            target(f"max by (pod) (rate(process_cpu_seconds_total{{{S}}}[5m]))", "{{pod}}", "A"),
            target(CPU_LIMIT, "{{pod}} CPU limit", "B"),
        ], 0, y, 12, "short",
            "CPU in cores (1 = one full core). Dashed = CPU limit, when the container has one; sustained use at "
            "the limit means throttling.", overrides=[dashed(".* CPU limit")]),
        timeseries("CPU vs GC overhead", [
            target(f"avg(rate(process_cpu_seconds_total{{{S}}}[5m]))", "CPU cores (avg pod)", "A"),
            target(f"avg({GC_OVERHEAD})", "GC overhead % (avg pod)", "B"),
        ], 12, y, 12, "short",
            "Lines moving together with low GC overhead = load drives both. High GC overhead rising with CPU = "
            "GC is driving the CPU.", overrides=[right_axis("GC overhead.*", "percent")]),
    ]
    y += 8
    panels += [
        timeseries("Open file descriptors (% of max)", [
            target(f"100 * max by (pod) (process_open_fds{{{S}}}) / max by (pod) (process_max_fds{{{S}}})", "{{pod}}"),
        ], 0, y, 12, "percent",
            "Near 100% causes 'Too many open files'. A steady climb suggests leaked sockets or files.",
            thresholds=[(good, None), (warn, 70), (bad, 90)]),
        timeseries("Classes loaded and unloaded", [
            target(f"max by (pod) (java_lang_ClassLoading_LoadedClassCount{{{S}}})", "{{pod}} loaded", "A"),
            target(f"sum by (pod) (rate(java_lang_ClassLoading_UnloadedClassCount{{{S}}}[1h])) * 3600",
                   "{{pod}} unloaded / hour", "B"),
        ], 12, y, 12, "short",
            "Loaded classes should flatten after warm-up. A steady climb hours after start, with few unloads, "
            "suggests a classloader leak.", overrides=[right_axis(".* unloaded / hour", "short")]),
    ]
    y += 8

    # --- Runtime --------------------------------------------------------------------
    panels.append(row("Runtime", y)); y += 1
    runtime = timeseries("JVM version and uptime per pod", [
        target(f"max by (pod, version, vendor, runtime) (jvm_info{{{S}}}) * on (pod) group_left() "
               f"(max by (pod) (java_lang_Runtime_Uptime{{{S}}}) / 3600000)", "", instant=True, fmt="table"),
    ], 0, y, 24, "h",
        "Uptime in hours. A pod much younger than the others restarted or was rescheduled; different versions "
        "mean pods run different JVM builds.", h=7)
    runtime["type"] = "table"
    runtime["options"] = {"showHeader": True, "sortBy": [{"displayName": "Value", "desc": False}]}
    runtime["transformations"] = [{"id": "organize", "options": {
        "excludeByName": {"Time": True},
        "renameByName": {"Value": "uptime (hours)", "pod": "pod", "version": "JVM version", "vendor": "vendor",
                         "runtime": "runtime"}}}]
    panels.append(runtime)

    return {
        "uid": "jvm-troubleshooting",
        "title": "JVM troubleshooting (OpenJ9)",
        "description": "Memory vs container limit, heap and pools, GC, threads and churn, CPU, file descriptors, "
                       "class loading and JVM versions for OpenJ9 JVMs scraped by the Prometheus JMX Exporter. "
                       "Generated by dashboards/generate_jvm_troubleshooting.py in claude-ops-investigator.",
        "tags": ["jvm", "openj9", "troubleshooting"],
        "timezone": "utc",
        "editable": True,
        "graphTooltip": 1,
        "refresh": "1m",
        "time": {"from": "now-3h", "to": "now"},
        "schemaVersion": 39,
        "version": 1,
        "templating": {"list": [
            {"name": "datasource", "label": "Data source", "type": "datasource", "query": "prometheus",
             "current": {}, "options": [], "refresh": 1},
            variable("namespace", "Namespace", "label_values(java_lang_Threading_ThreadCount, namespace)"),
            {"name": "label_key", "label": "Service label", "type": "custom", "query": "job,app,service",
             "current": {"text": "job", "value": "job"},
             "options": [{"text": k, "value": k, "selected": k == "job"} for k in ("job", "app", "service")],
             "description": "Prometheus label that names the service on JMX Exporter series (JVM_LABEL_KEY)."},
            variable("service", "Service",
                     'label_values(java_lang_Threading_ThreadCount{namespace="$namespace"}, $label_key)'),
            variable("pod", "Pods",
                     'label_values(java_lang_Threading_ThreadCount{namespace="$namespace", $label_key=~"$service"}, pod)',
                     multi=True, include_all=True),
        ]},
        "annotations": {"list": []},
        "panels": panels,
    }


def main() -> None:
    OUT.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
