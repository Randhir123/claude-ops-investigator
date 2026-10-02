# Grafana dashboards

## JVM troubleshooting (OpenJ9)

`jvm-troubleshooting.json` is a Grafana dashboard for OpenJ9/IBM Semeru JVMs on
Kubernetes. It shows the same signals the `jvm-troubleshooter` MCP tools read,
so what you see on the board matches what the agent reports.

### Import

1. In Grafana: **Dashboards → New → Import → Upload dashboard JSON file**, and
   choose `jvm-troubleshooting.json`.
2. At the top of the board, pick the **Data source** (the Prometheus or Thanos
   datasource that scrapes your JVMs), then **Namespace**, **Service** and
   **Pods**.
3. **Service label** must match the label that names the service on your JMX
   Exporter series (the MCP server's `JVM_LABEL_KEY`). The default is `job`;
   `app` and `service` are also offered. If **Service** stays empty, this is
   the setting to change.

Importing creates a copy you own; nothing here writes to Prometheus.

### What's on it

| Section | Panels |
|---|---|
| **At a glance** | JVM pods, lowest memory headroom, highest heap used, highest GC overhead, threads started per second, deadlocked threads |
| **Memory** | Container memory vs limit, memory headroom, heap used vs max, **tenured after GC (the leak signal)**, memory pools, non-heap and direct buffers |
| **Garbage collection** | GC overhead per pod, collections per minute (scavenge/global), average pause by collector, heap vs GC frequency (leak or load?) |
| **Threads** | Live threads, threads started per second (churn), daemon threads |
| **CPU, file descriptors, class loading** | Process CPU (with CPU limit when set), CPU vs GC overhead, open file descriptors (% of max), classes loaded and unloaded |
| **Runtime** | JVM version and uptime per pod |

Every panel has a description (the ⓘ next to its title) that says how to read it.

### Requirements

- OpenJ9 JVMs scraped by the Prometheus **JMX Exporter java agent**. The board
  uses its `java_lang_*` MBean metrics, plus the `jvm_*` and `process_*`
  metrics the agent also exports.
- For the memory-limit panels: **cAdvisor** (`container_memory_working_set_bytes`)
  and **kube-state-metrics** (`kube_pod_container_resource_limits`) in the same
  Prometheus. These are matched to the JVM pods by `namespace`, `pod` and
  `container`, so other workloads in the namespace don't appear.

### Changing it

Edit `generate_jvm_troubleshooting.py`, not the JSON, then regenerate:

```bash
python dashboards/generate_jvm_troubleshooting.py
```

`tests/test_dashboard.py` fails if the committed JSON is out of date with the
generator. It also checks that panels don't overlap and that every query is
scoped to the selected service and pods.
