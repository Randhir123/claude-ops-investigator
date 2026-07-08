from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_env_example_exists_with_placeholder_values_only():
    env_example = PROJECT_ROOT / ".env.example"
    assert env_example.exists()

    content = env_example.read_text()

    for var in (
        "PROMETHEUS_URL",
        "PROMETHEUS_AUTO_PORT_FORWARD",
        "PROMETHEUS_PF_SERVICE",
        "PROMETHEUS_PF_NAMESPACE",
        "PROMETHEUS_PF_LOCAL_PORT",
        "PROMETHEUS_PF_REMOTE_PORT",
        "IBM_LOGS_ENDPOINT",
        "IBM_CLOUD_API_KEY",
    ):
        assert var in content

    # Never a real-looking credential — only the documented placeholders.
    assert "<do-not-commit-real-key>" in content
    assert "<guid>" in content


def test_gitignore_ignores_env_but_keeps_env_example():
    lines = (PROJECT_ROOT / ".gitignore").read_text().splitlines()

    assert ".env" in lines
    assert ".env.*" in lines
    assert "!.env.example" in lines


def test_mcp_server_imports_without_a_env_file():
    # .env.example (not .env) is the only env file tracked in this repo, so
    # importing the server here already exercises load_dotenv() with no .env
    # present — it must not raise.
    import claude_ops.mcp.server as server

    assert server.mcp is not None
