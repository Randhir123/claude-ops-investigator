from __future__ import annotations

import pytest

_GRAFANA_ENV_VARS = ("GRAFANA_URL", "GRAFANA_DATASOURCE_UID", "GRAFANA_API_TOKEN", "GRAFANA_SESSION_COOKIE")


@pytest.fixture(autouse=True)
def _no_grafana_env(monkeypatch):
    # The MCP server's load_dotenv() may pull GRAFANA_* from a developer's
    # local .env; GRAFANA_URL switches every Prometheus query to Grafana mode,
    # so clear them and let Grafana tests opt in explicitly.
    for var in _GRAFANA_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
