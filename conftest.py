import os
import pytest

@pytest.fixture(autouse=True, scope="session")
def suppress_browser_and_remote_auth():
    """Ensure no test or subprocess triggers external auth websites or browser popups."""
    os.environ["BROWSER"] = ":"
    os.environ["NO_BROWSER"] = "1"
    os.environ.setdefault("AI_REAL_CI", os.environ.get("CI", ""))  # before forcing CI=1 (timing tests)
    os.environ["CI"] = "1"
    os.environ["DISPLAY"] = ""
    os.environ["INFER_TEST_MODE"] = "1"
    # Never reach/spawn the persistent tool server from a test (module/session-scoped fixtures run
    # before tests/conftest.py's per-test setting); tests/test_tool_server.py opts in explicitly.
    os.environ["INFER_TOOL_SERVER"] = "0"
    os.environ.pop("WSL_DISTRO_NAME", None)
    # Engine: tests that don't use the `engine` fixture (tests/conftest.py, which runs a test once
    # per engine) run the binary's default engine (Python, agent/) on purpose, never a developer's
    # sourced INFER_ENGINE, and never silently fall back to the legacy C loop when agent/ is not
    # found. AI_TEST_ENGINE=c runs them on the legacy C loop instead (CI runs the suite both ways).
    os.environ.pop("INFER_ENGINE", None)
    if os.environ.get("AI_TEST_ENGINE") in ("c", "py"):
        os.environ["INFER_ENGINE"] = os.environ["AI_TEST_ENGINE"]
    os.environ["INFER_ENGINE_STRICT"] = "1"
