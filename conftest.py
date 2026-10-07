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
    os.environ.pop("WSL_DISTRO_NAME", None)
