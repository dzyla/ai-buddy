"""Layout + behaviour-preservation guards for the `zulip_bridge/` package split of zulip_ai_bridge.py.

tests/golden/zulip_check.txt holds `python3 zulip_ai_bridge.py --check` output captured from the
single-file bridge *before* the split (stdout, stderr, exit code; paths normalised). A missing
golden file fails the test; regenerate only on purpose: ZULIP_REGEN_GOLDEN=1 pytest tests/test_zulip_layout.py
Every subprocess runs with an isolated HOME and a cwd outside the repo.
"""
import os
import stat
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIM = os.path.join(REPO, "zulip_ai_bridge.py")
GOLDEN = os.path.join(REPO, "tests", "golden", "zulip_check.txt")
REGEN = os.environ.get("ZULIP_REGEN_GOLDEN") == "1"
sys.path.insert(0, REPO)


def _env(home, **extra):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(extra)
    return env


def _fake_ai(tmp_path):
    p = tmp_path / "bin" / "ai"
    p.parent.mkdir()
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _normalise(text, tmp_path):
    ver = "%d.%d.%d" % sys.version_info[:3]
    return (text.replace(str(tmp_path), "<TMP>").replace(REPO, "<REPO>")
            .replace(sys.executable, "<PY>").replace(ver, "<PYVER>"))


def _run_check(tmp_path, script=SHIM, argv=("--check",), cwd="/", home=None):
    home = home or (tmp_path / "home")
    home.mkdir(exist_ok=True)
    env = _env(home, INFER_BIN_PATH=str(_fake_ai(tmp_path)))
    return subprocess.run([sys.executable, script, *argv], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120)


def _blob(r, tmp_path):
    return _normalise(f"rc={r.returncode}\n--stdout--\n{r.stdout}--stderr--\n{r.stderr}", tmp_path)


def test_check_output_matches_golden(tmp_path):
    got = _blob(_run_check(tmp_path), tmp_path)
    if REGEN:
        with open(GOLDEN, "w", encoding="utf-8") as f:
            f.write(got)
    assert os.path.exists(GOLDEN), "golden missing: capture with ZULIP_REGEN_GOLDEN=1"
    with open(GOLDEN, encoding="utf-8") as f:
        assert got == f.read()


def test_shim_is_small():
    with open(SHIM, encoding="utf-8") as f:
        assert len(f.read().splitlines()) < 60


def test_no_module_too_big():
    pkg = os.path.join(REPO, "zulip_bridge")
    assert os.path.isdir(pkg), "zulip_bridge/ package missing"
    sizes = {}
    for fn in os.listdir(pkg):
        if fn.endswith(".py"):
            with open(os.path.join(pkg, fn), encoding="utf-8") as f:
                sizes[fn] = len(f.read().splitlines())
    too_big = {k: v for k, v in sizes.items() if v > 800}
    assert sizes and not too_big, too_big


def test_check_from_other_cwd_and_symlink(tmp_path):
    """The shim finds the package via its realpath (a symlink elsewhere still works)."""
    link = tmp_path / "linkdir" / "zulip_ai_bridge.py"
    link.parent.mkdir()
    link.symlink_to(SHIM)
    r = _run_check(tmp_path, script=str(link), cwd=str(tmp_path))
    assert r.returncode == 1
    assert "[FAIL] zulip package not importable" in r.stdout


def test_monkeypatch_reaches_moved_code(monkeypatch):
    """Patching a name on the shim module changes what the moved code calls."""
    import zulip_ai_bridge as zab
    from zulip_bridge import cli, bridge, files
    monkeypatch.setattr(zab, "resolve_ai_bin", lambda: "PATCHED")
    assert cli.resolve_ai_bin() == "PATCHED" and bridge.resolve_ai_bin() == "PATCHED"
    monkeypatch.setattr(zab, "run_ai_subprocess", lambda *a, **k: "RAN")
    from zulip_bridge import handler
    assert handler.run_ai_subprocess() == "RAN"
    monkeypatch.setattr(zab, "MAX_FILE_SIZE", 7)
    assert files.MAX_FILE_SIZE == 7


def test_resolve_ai_bin_next_to_entry_script(tmp_path, monkeypatch):
    import zulip_bridge
    from zulip_bridge import config
    ai = _fake_ai(tmp_path)
    monkeypatch.delenv("INFER_BIN_PATH", raising=False)
    monkeypatch.setattr(zulip_bridge, "ENTRY_SCRIPT", str(ai.parent / "zulip_ai_bridge.py"))
    assert config.resolve_ai_bin() == str(ai)


def test_install_layout(tmp_path):
    """install.sh into an isolated HOME copies the shim and the package; the installed shim runs
    --check from cwd=/ using the package in ~/.local/share/ai/lib (not this checkout)."""
    if not os.path.exists(os.path.join(REPO, "ai")):
        import pytest
        pytest.skip("ai binary not built")
    home = tmp_path / "home"
    home.mkdir()
    fake = tmp_path / "fakebin"
    fake.mkdir()
    for n in ["make", "pip", "sudo", "systemctl", "git", "apt-get"]:
        p = fake / n
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    env = _env(home)
    env["PATH"] = f"{fake}:{env['PATH']}"
    r = subprocess.run(["bash", os.path.join(REPO, "install.sh")], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    lib = home / ".local" / "share" / "ai" / "lib" / "zulip_bridge"
    assert (lib / "__init__.py").exists() and (lib / "bridge.py").exists()
    assert not list(lib.glob("__pycache__"))
    installed = home / ".local" / "bin" / "zulip_ai_bridge.py"
    assert installed.exists()
    r = subprocess.run([sys.executable, str(installed), "--check"], cwd="/",
                       env=_env(home, INFER_BIN_PATH=str(_fake_ai(tmp_path))),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 1 and "[FAIL] zulip package not importable" in r.stdout, r.stdout + r.stderr
    probe = "import sys; sys.argv=['x']; import zulip_ai_bridge, zulip_bridge; print(zulip_bridge.__file__)"
    r = subprocess.run([sys.executable, "-c", probe], cwd="/",
                       env=_env(home, PYTHONPATH=str(installed.parent)),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().startswith(str(lib)), r.stdout
