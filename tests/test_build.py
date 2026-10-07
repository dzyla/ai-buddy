"""The C sources build warning-free: `make ci` (and an explicit -Werror build) must succeed.

Builds into a temporary copy of the sources so the repo's own `ai` binary and libremote_harness.so
are never touched (a failed -Werror build must not leave the checkout without a binary)."""
import glob
import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WERROR_FLAGS = "-O2 -Wall -Wextra -fPIC -Werror"


def _copy_sources(dst):
    for pattern in ("*.c", "*.h", "Makefile"):
        for path in glob.glob(os.path.join(REPO, pattern)):
            shutil.copy2(path, dst)
    os.makedirs(os.path.join(dst, "tests"), exist_ok=True)
    shutil.copy2(os.path.join(REPO, "tests", "test_remote_harness.c"), os.path.join(dst, "tests"))


def _make(cwd, *args):
    return subprocess.run(["make", *args], cwd=cwd, capture_output=True, text=True, timeout=600)


@pytest.fixture(scope="module")
def src_copy(tmp_path_factory):
    if not shutil.which("make") or not shutil.which("gcc"):
        pytest.skip("make/gcc not available")
    d = tmp_path_factory.mktemp("build")
    _copy_sources(str(d))
    return str(d)


def test_no_warnings(src_copy):
    """make clean && make CFLAGS='-O2 -Wall -Wextra -fPIC -Werror' ai succeeds."""
    r = _make(src_copy, "clean")
    assert r.returncode == 0, r.stderr
    r = _make(src_copy, f"CFLAGS={WERROR_FLAGS}", "ai")
    warnings = [line for line in (r.stdout + r.stderr).splitlines() if "warning:" in line or "error:" in line]
    assert r.returncode == 0 and not warnings, "\n".join(warnings[:50]) or r.stderr[-4000:]
    assert os.access(os.path.join(src_copy, "ai"), os.X_OK)


def test_make_ci_target_uses_werror(src_copy):
    """`make ci` is the CI build: a clean rebuild of the library and binary with -Werror."""
    r = _make(src_copy, "-n", "ci")
    assert r.returncode == 0, r.stderr
    r = _make(src_copy, "ci")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-4000:]
    assert "-Werror" in out
    assert "warning:" not in out
    assert os.path.exists(os.path.join(src_copy, "libremote_harness.so"))
    assert os.access(os.path.join(src_copy, "ai"), os.X_OK)
