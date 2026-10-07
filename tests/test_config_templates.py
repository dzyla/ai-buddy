"""Config templates live in config/*.example* (personal config stays out of the repo).

Checks that every template parses and carries no personal data, that the MCP config search
order prefers the user's ~/.config/ai over the cwd, that `"disabled": true` entries are
skipped, that install.sh seeds ~/.config/ai from the templates without overwriting anything,
and that the CI workflow has the expected jobs.
"""
import configparser
import glob
import json
import os
import re
import stat
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO, "config")
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts"))

import privacy_check  # noqa: E402
from aitools import mcp_client  # noqa: E402

EXPECTED = ["mcp.example.json", "trader.example.json", "private_patterns.example.txt",
            "zuliprc.example"]


def _templates():
    return sorted(glob.glob(os.path.join(CONFIG, "*.example*")))


def test_expected_templates_exist():
    names = [os.path.basename(p) for p in _templates()]
    for n in EXPECTED:
        assert n in names, n


@pytest.mark.parametrize("path", _templates(), ids=os.path.basename)
def test_template_parses(path):
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            json.load(f)
    elif os.path.basename(path).startswith("zuliprc"):
        cp = configparser.ConfigParser()
        cp.read(path, encoding="utf-8")
        assert cp.has_section("api")
        assert {"email", "key", "site"} <= set(cp["api"])
    elif path.endswith(".txt"):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    re.compile(line)
    else:  # pragma: no cover - a new template type needs a parser here
        pytest.fail(f"no parser for {path}")


@pytest.mark.parametrize("path", _templates(), ids=os.path.basename)
def test_template_has_no_personal_data(path):
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            assert not re.search(r"(?<!\d)\d{9}(?!\d)", line), f"{path}:{i}: 9-digit number"
            assert privacy_check.scan_line(line, []) == [], f"{path}:{i}: {line!r}"
            assert "privacy-ok" not in line, f"{path}:{i}: templates need no exemptions"


def test_private_patterns_template_has_no_active_patterns():
    """Installed verbatim as ~/.config/ai/private_patterns.txt: it must not block anything."""
    with open(os.path.join(CONFIG, "private_patterns.example.txt"), encoding="utf-8") as f:
        active = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    assert active == []


def test_mcp_template_entries_are_disabled_and_secret_free():
    with open(os.path.join(CONFIG, "mcp.example.json"), encoding="utf-8") as f:
        servers = json.load(f)["mcpServers"]
    assert {"robinhood", "zulip", "pubmed"} <= set(servers)
    for name, cfg in servers.items():
        assert cfg.get("disabled") is True, name
        for k in cfg.get("env", {}):
            assert not re.search(r"(?i)key|token|secret|password", k), (name, k)
    blob = json.dumps(servers)
    assert "PUBMED_API_KEY" not in blob and "PUBMED_SEARCH_URL" not in blob


def test_mcp_json_is_not_tracked():
    r = subprocess.run(["git", "ls-files", "--error-unmatch", "mcp.json"], cwd=REPO,
                       capture_output=True, text=True)
    if r.returncode != 0 and "not a git repository" in r.stderr:
        pytest.skip("not a git checkout")
    assert r.returncode != 0, "mcp.json is still tracked; it belongs in ~/.config/ai/"
    with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
        assert "/mcp.json" in f.read().split("\n")


# ── load_config search order ────────────────────────────────────────────────
def _write_cfg(path, server):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {server: {"command": "true"}}}, f)


@pytest.fixture
def cfg_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("INFER_MCP_CONFIG", raising=False)
    monkeypatch.chdir(work)
    return home, work


def test_load_config_prefers_user_config_over_cwd(cfg_env):
    home, work = cfg_env
    _write_cfg(str(work / "mcp.json"), "from_cwd")
    _write_cfg(str(home / ".config" / "ai" / "mcp.json"), "from_home")
    assert list(mcp_client.load_config()) == ["from_home"]


def test_load_config_env_override_wins(cfg_env, tmp_path, monkeypatch):
    home, work = cfg_env
    _write_cfg(str(home / ".config" / "ai" / "mcp.json"), "from_home")
    override = tmp_path / "custom.json"
    _write_cfg(str(override), "from_env")
    monkeypatch.setenv("INFER_MCP_CONFIG", str(override))
    assert list(mcp_client.load_config()) == ["from_env"]


def test_load_config_falls_back_to_cwd_for_dev(cfg_env):
    home, work = cfg_env
    _write_cfg(str(work / "mcp.json"), "from_cwd")
    assert list(mcp_client.load_config()) == ["from_cwd"]


def test_load_config_mcp_config_json_in_home_beats_cwd(cfg_env):
    home, work = cfg_env
    _write_cfg(str(work / "mcp.json"), "from_cwd")
    _write_cfg(str(home / ".config" / "ai" / "mcp_config.json"), "from_home_alt")
    assert list(mcp_client.load_config()) == ["from_home_alt"]


def test_load_config_skips_disabled_servers(cfg_env):
    home, _ = cfg_env
    p = home / ".config" / "ai" / "mcp.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"mcpServers": {
        "on": {"command": "true"},
        "off": {"command": "true", "disabled": True},
        "explicit_on": {"command": "true", "disabled": False},
    }}))
    assert sorted(mcp_client.load_config()) == ["explicit_on", "on"]


def test_load_config_template_yields_no_servers(cfg_env, monkeypatch):
    monkeypatch.setenv("INFER_MCP_CONFIG", os.path.join(CONFIG, "mcp.example.json"))
    assert mcp_client.load_config() == {}


# ── install.sh seeds ~/.config/ai from the templates ────────────────────────
def _fake_bin(dirpath, names):
    dirpath.mkdir()
    for n in names:
        p = dirpath / n
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _install(tmp_path, home):
    if not os.path.exists(os.path.join(REPO, "ai")):
        pytest.skip("ai binary not built")
    fake = tmp_path / "fakebin"
    if not fake.exists():
        # No rebuild, no pip, no apt, no systemd, no git config: only the copy steps run.
        _fake_bin(fake, ["make", "pip", "sudo", "systemctl", "git", "apt-get"])
    env = {k: v for k, v in os.environ.items() if not k.startswith("INFER_") or k == "INFER_TOOL_SERVER"}
    env.update(HOME=str(home), PATH=f"{fake}:{os.environ.get('PATH', '')}")
    r = subprocess.run(["bash", os.path.join(REPO, "install.sh")], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return r


def test_install_seeds_config_without_overwriting(tmp_path):
    home = tmp_path / "home"
    cfg = home / ".config" / "ai"
    cfg.mkdir(parents=True)
    sentinel = '{"mcpServers": {"mine": {"command": "SENTINEL"}}}\n'
    (cfg / "mcp.json").write_text(sentinel)
    r = _install(tmp_path, home)
    assert (cfg / "mcp.json").read_text() == sentinel
    assert "ship disabled" not in r.stdout  # nothing seeded, no notice
    assert not (cfg / "zuliprc").exists(), "a placeholder zuliprc must not be seeded"
    for tpl, target in [("trader.example.json", "trader.json"),
                        ("private_patterns.example.txt", "private_patterns.txt")]:
        dst = cfg / target
        assert dst.exists(), target
        with open(os.path.join(CONFIG, tpl), encoding="utf-8") as f:
            assert dst.read_text() == f.read(), target
        assert stat.S_IMODE(dst.stat().st_mode) == 0o600, target
    # A second install keeps whatever the user edited.
    (cfg / "trader.json").write_text("{}\n")
    _install(tmp_path, home)
    assert (cfg / "trader.json").read_text() == "{}\n"
    assert (cfg / "mcp.json").read_text() == sentinel


def test_install_creates_mcp_json_from_template(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = _install(tmp_path, home)
    dst = home / ".config" / "ai" / "mcp.json"
    assert "MCP servers ship disabled" in r.stdout
    assert '"disabled": false' in r.stdout and str(dst) in r.stdout
    with open(os.path.join(CONFIG, "mcp.example.json"), encoding="utf-8") as f:
        assert dst.read_text() == f.read()


# ── CI workflow ──────────────────────────────────────────────────────────────
CI = os.path.join(REPO, ".github", "workflows", "ci.yml")


def test_ci_workflow_jobs():
    with open(CI, encoding="utf-8") as f:
        text = f.read()
    assert "\t" not in text, "YAML must not contain tabs"
    for needle in ("ubuntu-latest", "libcurl4-openssl-dev", "make", "pytest tests/ -q",
                   "scripts/privacy_check.py --all", "AI_PRIVATE_PATTERNS",
                   "evals/run_evals.py --mock"):
        assert needle in text, needle
    try:
        import yaml
    except ImportError:
        jobs = re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", text, re.M)
    else:
        doc = yaml.safe_load(text)
        jobs = list(doc["jobs"])
        for name, job in doc["jobs"].items():
            assert job["runs-on"] == "ubuntu-latest", name
            assert job["steps"], name
    assert {"test", "privacy", "evals-mock"} <= set(jobs), jobs


def test_zulip_mcp_server_expands_config_path(tmp_path, monkeypatch):
    """ZULIP_CONFIG_PATH may be given as ~/.config/ai/zuliprc (the zuliprc template's location);
    MCP `env` values are not expanded by the client, so the server must expand ~ itself."""
    import types
    import zulip_mcp_server as zms
    seen = {}
    stub = types.ModuleType("zulip")
    stub.Client = lambda **kw: seen.update(kw) or "client"
    monkeypatch.setitem(sys.modules, "zulip", stub)
    monkeypatch.setattr(zms, "zulip", stub, raising=False)
    for k in ("ZULIP_SITE", "ZULIP_EMAIL", "ZULIP_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ZULIP_CONFIG_PATH", "~/.config/ai/zuliprc")
    assert zms.get_zulip_client() == "client"
    assert seen["config_file"] == str(tmp_path / ".config" / "ai" / "zuliprc")


def test_zulip_mcp_server_rejects_placeholder_zuliprc(tmp_path, monkeypatch):
    import types
    import zulip_mcp_server as zms
    stub = types.ModuleType("zulip")
    stub.Client = lambda **kw: "client"
    monkeypatch.setitem(sys.modules, "zulip", stub)
    monkeypatch.setattr(zms, "zulip", stub, raising=False)
    for k in ("ZULIP_SITE", "ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_CONFIG_PATH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    with open(os.path.join(CONFIG, "zuliprc.example"), encoding="utf-8") as f:
        (tmp_path / ".zuliprc").write_text(f.read())
    with pytest.raises(Exception, match="not configured"):
        zms.get_zulip_client()
    rc = tmp_path / "rc"
    rc.write_text("[api]\nemail=bot@example.com\nkey=REPLACE_WITH_KEY\nsite=https://x.example.com\n")
    monkeypatch.setenv("ZULIP_CONFIG_PATH", str(rc))
    with pytest.raises(Exception, match="not configured"):
        zms.get_zulip_client()
    rc.write_text("[api]\nemail=bot@example.com\nkey=realkey123\nsite=https://x.example.com\n")
    assert zms.get_zulip_client() == "client"
