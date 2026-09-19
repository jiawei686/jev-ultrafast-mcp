"""Tests for scripts/install.py -- the config-merging logic, without a browser.

The installer is the first thing a new user runs, and it writes into files it
does not own (an IDE's settings, Codex's config.toml, ~/.claude.json). So the
contract under test is not "it adds an entry" but "it adds an entry and leaves
everything else byte-identical".
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _installer():
    spec = importlib.util.spec_from_file_location("jev_install", ROOT / "scripts" / "install.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = _installer()


CODEX_CONFIG = """\
model = "gpt-5"

[projects."/tmp/x"]
trust_level = "trusted"

[mcp_servers.other]
command = "node"
args = ["/opt/repl.js"]

[mcp_servers.other.env]
FOO = "bar"

[features]
js_repl = true
"""


# --------------------------------------------------------------------------- toml


def test_toml_block_is_valid_and_preserves_neighbours():
    tomllib = pytest.importorskip("tomllib")
    block = install._toml_block("mine", "/usr/bin/python3", ["-m", "thing"], {"A": "1"})
    merged = install._toml_upsert(CODEX_CONFIG, "mine", block)

    parsed = tomllib.loads(merged)
    assert parsed["model"] == "gpt-5"
    assert parsed["features"]["js_repl"] is True
    assert parsed["mcp_servers"]["other"]["env"]["FOO"] == "bar"
    assert parsed["mcp_servers"]["mine"]["args"] == ["-m", "thing"]
    assert parsed["mcp_servers"]["mine"]["env"] == {"A": "1"}


def test_toml_upsert_is_idempotent():
    block = install._toml_block("mine", "/usr/bin/python3", ["-m", "thing"], {})
    once = install._toml_upsert(CODEX_CONFIG, "mine", block)
    twice = install._toml_upsert(once, "mine", block)
    assert once == twice
    assert once.count("[mcp_servers.mine]") == 1


def test_toml_upsert_replaces_a_stale_entry():
    tomllib = pytest.importorskip("tomllib")
    first = install._toml_upsert(
        CODEX_CONFIG, "mine", install._toml_block("mine", "/old/python", [], {})
    )
    second = install._toml_upsert(
        first, "mine", install._toml_block("mine", "/new/python", [], {})
    )
    assert "/old/python" not in second
    assert tomllib.loads(second)["mcp_servers"]["mine"]["command"] == "/new/python"


def _toml_client(path: Path) -> dict:
    return {"key": "x", "label": "X", "kind": "toml", "root": None,
            "paths": [path], "probe": [path.parent]}


def test_toml_plan_keeps_keys_it_does_not_write(tmp_path: Path):
    """Codex's dialect gets the same contract as the JSON one, one table down.

    A hand-added `cwd` and a `startup_timeout_sec` someone raised are the user's
    lines; regenerating the whole table threw both away and reset the timeout to
    this script's default, which is a silent downgrade rather than a merge.
    """
    tomllib = pytest.importorskip("tomllib")
    path = tmp_path / "config.toml"
    path.write_text(
        '[mcp_servers.other]\ncommand = "node"\nargs = ["/opt/repl.js"]\n\n'
        f'[mcp_servers.{install.SERVER_NAME}]\n'
        'command = "/old/python"\n'
        'args = ["-m", "old"]\n'
        'startup_timeout_sec = 60\n'
        'cwd = "/home/me/jev-ultrafast-mcp"\n'
    )

    _p, text, summary = install._plan(
        _toml_client(path), "/new/python", ["-m", "jev_ultrafast_mcp"], {}, False)
    assert "replace" in summary

    parsed = tomllib.loads(text)
    entry = parsed["mcp_servers"][install.SERVER_NAME]
    assert entry["cwd"] == "/home/me/jev-ultrafast-mcp"
    assert entry["startup_timeout_sec"] == 60, "a tuned timeout is not reset to the default"
    assert entry["command"] == "/new/python"
    assert entry["args"] == ["-m", "jev_ultrafast_mcp"]
    assert parsed["mcp_servers"]["other"]["command"] == "node"


def test_toml_plan_keeps_hand_added_env_vars(tmp_path: Path):
    tomllib = pytest.importorskip("tomllib")
    path = tmp_path / "config.toml"
    path.write_text(
        f'[mcp_servers.{install.SERVER_NAME}]\ncommand = "/old"\nargs = []\n\n'
        f'[mcp_servers.{install.SERVER_NAME}.env]\n'
        'JEVMCP_ALLOW_DOMAINS = "example.com"\n'
        'JEVMCP_HEADLESS = "0"\n'
    )

    _p, text, _s = install._plan(_toml_client(path), "/new", [],
                                 {"JEVMCP_HEADLESS": "1"}, False)

    env = tomllib.loads(text)["mcp_servers"][install.SERVER_NAME]["env"]
    assert env["JEVMCP_ALLOW_DOMAINS"] == "example.com"
    assert env["JEVMCP_HEADLESS"] == "1"


def test_toml_plan_stays_idempotent_with_keys_it_carries_over(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        f'[mcp_servers.{install.SERVER_NAME}]\n'
        'command = "/old"\nargs = []\nstartup_timeout_sec = 60\ncwd = "/somewhere"\n'
    )
    client = _toml_client(path)

    _p, first, _s = install._plan(client, "/new", ["-m", "m"], {"A": "1"}, False)
    assert first is not None
    path.write_text(first)
    _p, second, _s = install._plan(client, "/new", ["-m", "m"], {"A": "1"}, False)

    assert second == first
    assert first.count(f"[mcp_servers.{install.SERVER_NAME}]") == 1


def test_toml_drop_removes_subtables_and_stops_at_the_next_table():
    tomllib = pytest.importorskip("tomllib")
    block = install._toml_block("mine", "/usr/bin/python3", [], {"A": "1"})
    merged = install._toml_upsert(CODEX_CONFIG, "mine", block)
    dropped = install._toml_drop_section(merged, "mine")

    assert "mine" not in dropped
    parsed = tomllib.loads(dropped)
    assert sorted(parsed["mcp_servers"]) == ["other"]
    assert parsed["mcp_servers"]["other"]["env"]["FOO"] == "bar"
    assert parsed["features"]["js_repl"] is True


def test_toml_drop_does_not_match_a_prefix_sibling():
    text = '[mcp_servers.mine]\ncommand = "a"\n\n[mcp_servers.mine-other]\ncommand = "b"\n'
    dropped = install._toml_drop_section(text, "mine")
    assert "mine-other" in dropped
    assert '[mcp_servers.mine]' not in dropped


def test_toml_string_escapes_windows_paths():
    assert install._toml_string(r"C:\Program Files\py.exe") == r'"C:\\Program Files\\py.exe"'


# --------------------------------------------------------------------------- json


def _json_client(root: str = "mcpServers") -> dict:
    return {"key": "x", "label": "X", "kind": "json", "root": root, "paths": [], "probe": []}


def test_json_plan_merges_and_preserves_other_servers(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"keep": {"command": "npx"}}, "otherKey": [1]}))

    client = _json_client()
    client["paths"] = [path]
    _path, text, summary = install._plan(client, "/usr/bin/python3", ["-m", "m"], {"A": "1"}, False)

    assert text is not None and "add entry" in summary
    data = json.loads(text)
    assert data["mcpServers"]["keep"] == {"command": "npx"}
    assert data["otherKey"] == [1]
    assert data["mcpServers"][install.SERVER_NAME]["args"] == ["-m", "m"]


def test_json_plan_is_idempotent(tmp_path: Path):
    path = tmp_path / "mcp.json"
    client = _json_client()
    client["paths"] = [path]

    _p, first, _s = install._plan(client, "/usr/bin/python3", ["-m", "m"], {}, False)
    assert first is not None
    path.write_text(first)
    _p, second, summary = install._plan(client, "/usr/bin/python3", ["-m", "m"], {}, False)
    assert second == first
    assert "replace" in summary


def test_json_plan_keeps_the_keys_it_does_not_write(tmp_path: Path):
    """An install must not be the thing that deletes a hand-added `cwd`.

    Replacing an entry wholesale did exactly that, and took `disabled` with it:
    the config looked freshly installed and the settings the user had put there
    were simply gone. The module's own promise is that it merges into a config
    rather than overwriting it, and that has to hold one level below the file.
    """
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {install.SERVER_NAME: {
        "command": "/old/python", "args": ["-m", "old"],
        "cwd": "/home/me/jev-ultrafast-mcp", "disabled": False,
    }}}))
    client = _json_client()
    client["paths"] = [path]

    _p, text, _s = install._plan(client, "/new/python", ["-m", "jev_ultrafast_mcp"], {}, False)

    entry = json.loads(text)["mcpServers"][install.SERVER_NAME]
    assert entry["cwd"] == "/home/me/jev-ultrafast-mcp"
    assert entry["disabled"] is False
    # ...while the keys the script does own are refreshed rather than left stale.
    assert entry["command"] == "/new/python"
    assert entry["args"] == ["-m", "jev_ultrafast_mcp"]


def test_json_plan_merges_env_instead_of_replacing_it(tmp_path: Path):
    """A domain allowlist is a safety setting, and a plain install used to wipe it."""
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {install.SERVER_NAME: {
        "command": "/old/python", "args": [],
        "env": {"JEVMCP_ALLOW_DOMAINS": "example.com", "JEVMCP_HEADLESS": "0"},
    }}}))
    client = _json_client()
    client["paths"] = [path]

    _p, text, _s = install._plan(client, "/new/python", ["-m", "m"],
                                 {"JEVMCP_HEADLESS": "1"}, False)

    env = json.loads(text)["mcpServers"][install.SERVER_NAME]["env"]
    assert env["JEVMCP_ALLOW_DOMAINS"] == "example.com", "the allowlist survived"
    assert env["JEVMCP_HEADLESS"] == "1", "but a variable this run sets is refreshed"


def test_json_plan_keeps_an_entry_with_unowned_keys_idempotent(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {install.SERVER_NAME: {
        "command": "/old/python", "args": [], "cwd": "/somewhere"}}}))
    client = _json_client()
    client["paths"] = [path]

    _p, first, _s = install._plan(client, "/new/python", ["-m", "m"], {}, False)
    assert first is not None
    path.write_text(first)
    _p, second, summary = install._plan(client, "/new/python", ["-m", "m"], {}, False)

    assert second == first
    assert "replace" in summary


def test_a_root_key_that_is_not_an_object_is_refused(tmp_path: Path):
    """A clear refusal beats a traceback in the script a new user runs first."""
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": ["not", "an", "object"]}))
    client = _json_client()
    client["paths"] = [path]

    with pytest.raises(SystemExit, match="not a JSON object"):
        install._plan(client, "/usr/bin/python3", ["-m", "m"], {}, False)
    assert json.loads(path.read_text()) == {"mcpServers": ["not", "an", "object"]}


def test_vscode_entries_declare_their_transport(tmp_path: Path):
    path = tmp_path / "mcp.json"
    client = _json_client(root="servers")
    client["paths"] = [path]

    _p, text, _s = install._plan(client, "/usr/bin/python3", ["-m", "m"], {}, False)
    entry = json.loads(text)["servers"][install.SERVER_NAME]
    # VS Code silently ignores stdio entries without an explicit type.
    assert entry["type"] == "stdio"
    assert "mcpServers" not in json.loads(text)


def test_json_plan_removes_only_its_own_entry(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {
        "keep": {"command": "npx"}, install.SERVER_NAME: {"command": "old"}}}))
    client = _json_client()
    client["paths"] = [path]

    _p, text, _s = install._plan(client, "/usr/bin/python3", ["-m", "m"], {}, True)
    assert text is not None
    assert list(json.loads(text)["mcpServers"]) == ["keep"]


def test_json_plan_reports_no_change_when_absent_on_uninstall(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"keep": {"command": "npx"}}}))
    client = _json_client()
    client["paths"] = [path]

    _p, text, _s = install._plan(client, "/usr/bin/python3", ["-m", "m"], {}, True)
    assert text is None


def test_broken_json_is_refused_rather_than_clobbered(tmp_path: Path):
    path = tmp_path / "mcp.json"
    broken = '{ "mcpServers": { broken }'
    path.write_text(broken)
    client = _json_client()
    client["paths"] = [path]

    with pytest.raises(SystemExit, match="not valid JSON"):
        install._plan(client, "/usr/bin/python3", ["-m", "m"], {}, False)
    assert path.read_text() == broken


# --------------------------------------------------------------------------- env


def test_env_defaults_to_headless_and_honours_headed():
    import argparse

    ns = argparse.Namespace(headed=False, allow_domains="", env=[])
    assert install._env_for(ns) == {"JEVMCP_HEADLESS": "1"}
    ns.headed = True
    assert install._env_for(ns)["JEVMCP_HEADLESS"] == "0"


def test_env_rejects_a_bare_key():
    import argparse

    ns = argparse.Namespace(headed=False, allow_domains="", env=["NOPE"])
    with pytest.raises(SystemExit, match="KEY=VALUE"):
        install._env_for(ns)


def test_clients_have_unique_keys_and_all_documented_fields():
    keys = [client["key"] for client in install._clients()]
    assert len(keys) == len(set(keys))
    for client in install._clients():
        assert client["kind"] in ("json", "toml")
        assert client["paths"] and client["probe"] and client["note"]
        assert client["root"] in ("mcpServers", "servers", None)
        assert (client["root"] is None) == (client["kind"] == "toml")


# ------------------------------------------------------------------ interpreter


def test_an_interpreter_that_cannot_import_the_package_is_refused():
    """Writing a config the client cannot start is the failure this script exists to prevent.

    The client spawns the interpreter from its own directory, so an interpreter
    that only finds the package because the repo happens to be the working
    directory is not good enough — and a machine without the package installed
    at all is the ordinary way to get there.
    """
    assert install._interpreter_ok(sys.executable), "this test suite runs with the package installed"
    assert not install._interpreter_ok(str(Path(sys.executable).parent / "definitely-not-python"))
    assert not install._interpreter_ok("/bin/sh")


def test_the_refusal_names_the_command_that_fixes_it(monkeypatch):
    monkeypatch.setattr(install, "_interpreter_ok", lambda _interp: False)

    with pytest.raises(SystemExit) as caught:
        install._check_interpreter("/usr/bin/python3", writing=True)

    message = str(caught.value)
    assert "pip install -e" in message
    assert str(install.REPO_ROOT) in message
    assert "nothing written" in message


def test_printing_is_allowed_but_warned(monkeypatch, capsys):
    """`--print` writes nothing, so it may still show the bytes — with the warning."""
    monkeypatch.setattr(install, "_interpreter_ok", lambda _interp: False)

    install._check_interpreter("/usr/bin/python3", writing=False)

    assert "cannot import" in capsys.readouterr().out


def test_uninstall_does_not_require_a_working_interpreter(monkeypatch, tmp_path):
    """Removing an entry must work even when the interpreter it named is gone.

    That is the ordinary reason to remove it: delete the venv, and now the
    config points at nothing. Refusing to uninstall until the package is
    importable would trap the user in exactly the state they are escaping.
    """
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {install.SERVER_NAME: {"command": "gone"}}}))
    client = _json_client()
    client["paths"] = [path]
    client["probe"] = [tmp_path]

    monkeypatch.setattr(install, "_clients", lambda: [client])
    monkeypatch.setattr(install, "_interpreter_ok", lambda _interp: False)
    monkeypatch.setattr(sys, "argv", ["install.py", "--uninstall", "-c", "x", "-y"])

    assert install.main() == 0
    assert install.SERVER_NAME not in json.loads(path.read_text())["mcpServers"]


def test_the_package_check_does_not_inherit_our_working_directory(monkeypatch):
    """`python -c` puts the cwd on sys.path, so a probe run from the repo proves nothing.

    It has to run from somewhere neutral and in isolated mode, or the check
    passes in every checkout — including ones where nothing was installed — and
    catches exactly the case it was written for.
    """
    seen: dict = {}

    class Probe:
        returncode = 0

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["cwd"] = kwargs.get("cwd")
        return Probe()

    monkeypatch.setattr(install.subprocess, "run", fake_run)

    assert install._interpreter_ok("/usr/bin/python3")
    assert seen["cwd"] == tempfile.gettempdir() != os.getcwd()
    assert "-I" in seen["argv"], "isolated mode, or PYTHONPATH can fake the answer"
