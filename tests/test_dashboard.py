from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "dashboards" / "generate_jvm_troubleshooting.py"
COMMITTED = ROOT / "dashboards" / "jvm-troubleshooting.json"


def _build() -> dict:
    spec = importlib.util.spec_from_file_location("generate_jvm_troubleshooting", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build()


def _panels(dashboard: dict) -> list[dict]:
    return [p for p in dashboard["panels"] if p["type"] != "row"]


def test_committed_json_matches_generator():
    assert json.loads(COMMITTED.read_text()) == _build(), (
        "dashboards/jvm-troubleshooting.json is stale: run python dashboards/generate_jvm_troubleshooting.py"
    )


def test_panel_ids_are_unique_and_titles_set():
    dashboard = _build()
    ids = [p["id"] for p in dashboard["panels"]]
    assert len(ids) == len(set(ids))
    assert all(p["title"] for p in dashboard["panels"])


def test_panels_do_not_overlap_and_fit_the_grid():
    cells = set()
    for p in _build()["panels"]:
        g = p["gridPos"]
        assert 0 <= g["x"] and g["x"] + g["w"] <= 24
        for x in range(g["x"], g["x"] + g["w"]):
            for y in range(g["y"], g["y"] + g["h"]):
                assert (x, y) not in cells, f"{p['title']} overlaps another panel"
                cells.add((x, y))


def test_every_query_is_scoped_to_the_selected_service():
    for p in _panels(_build()):
        assert p["targets"], p["title"]
        for t in p["targets"]:
            expr = t["expr"]
            # JVM series select the service directly; container series are joined to those JVM series
            assert '$label_key=~"$service"' in expr and 'pod=~"$pod"' in expr, (p["title"], expr)
            assert t["datasource"]["uid"] == "${datasource}"


def test_container_queries_are_limited_to_jvm_containers():
    for p in _panels(_build()):
        for t in p["targets"]:
            if "container_memory" in t["expr"] or "kube_pod_container_resource_limits" in t["expr"]:
                assert "and on (namespace, pod, container)" in t["expr"], p["title"]


def test_variables():
    names = [v["name"] for v in _build()["templating"]["list"]]
    assert names == ["datasource", "namespace", "label_key", "service", "pod"]


def test_key_troubleshooting_panels_exist():
    titles = {p["title"] for p in _panels(_build())}
    for expected in (
        "Lowest memory headroom",
        "Container memory vs limit",
        "Tenured (old gen) after GC: the leak signal",
        "GC overhead per pod",
        "Threads started per second (churn)",
        "Open file descriptors (% of max)",
        "JVM version and uptime per pod",
    ):
        assert expected in titles
