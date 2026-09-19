#!/usr/bin/env python3
"""Register jev-ultrafast-mcp with the MCP clients already on this machine.

Every client wants the same server described in a slightly different dialect:
a JSON object under `mcpServers`, the same object under `servers` with an
explicit transport (VS Code), or a TOML table (Codex). People copy a snippet
between them, the key is wrong, the client loads nothing, and nothing is
logged. So this script detects what is installed and writes the right dialect
for each, merging into existing config instead of overwriting it.

Before writing, it checks that the interpreter it is about to name can actually
import the package from outside this checkout: a config that looks right but
that the client silently fails to start is the same problem one level down.

    python scripts/install.py               # detect and install everywhere
    python scripts/install.py --list        # show what was detected, write nothing
    python scripts/install.py --print       # show the exact bytes, write nothing
    python scripts/install.py -c cursor,codex
    python scripts/install.py --uninstall

Stdlib only, on purpose: it must run before the project's dependencies exist.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SERVER_NAME = "jev-ultrafast-mcp"
REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- paths


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _appdata() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) if appdata else _home() / "AppData" / "Roaming"


def _config_home() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else _home() / ".config"


def _interpreter() -> str:
    """The interpreter the client should spawn.

    A repo-local venv is what `pip install -e .` was run into, so prefer it;
    fall back to whatever interpreter is running this script.
    """
    for candidate in (
        REPO_ROOT / ".venv" / "bin" / "python",
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _interpreter_ok(interp: str) -> bool:
    """Can that interpreter import this package from somewhere other than the repo?

    The client spawns it with a working directory of its own choosing, so the
    repo root will not be on `sys.path` unless the package is really installed.
    An entry that only works from one directory is the same class of failure
    this script exists to remove -- the client loads nothing and logs nothing --
    except harder to see, because the config looks correct.
    """
    try:
        probe = subprocess.run(
            [interp, "-I", "-c", "import jev_ultrafast_mcp"],
            cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _check_interpreter(interp: str, *, writing: bool) -> None:
    """Refuse to write a config the client cannot start. Warn when only printing."""
    if _interpreter_ok(interp):
        return
    message = "\n".join([
        f"  ! {interp} cannot import jev_ultrafast_mcp.",
        "    A client runs it from its own directory, so the package has to be",
        "    installed, not merely present in this checkout. Run:",
        "",
        f"      {interp} -m pip install -e {REPO_ROOT}",
        "",
        "    or pass --python with an interpreter that already has it.",
    ])
    if writing:
        raise SystemExit(message + "\n    (nothing written)")
    print(message)


def _clients() -> list[dict[str, Any]]:
    home, appdata, cfg = _home(), _appdata(), _config_home()
    is_mac = sys.platform == "darwin"
    is_win = os.name == "nt"

    if is_win:
        claude_desktop = appdata / "Claude" / "claude_desktop_config.json"
    elif is_mac:
        claude_desktop = home / "Library/Application Support/Claude/claude_desktop_config.json"
    else:
        claude_desktop = cfg / "Claude" / "claude_desktop_config.json"

    if is_win:
        vscode_user = appdata / "Code" / "User"
    elif is_mac:
        vscode_user = home / "Library/Application Support/Code/User"
    else:
        vscode_user = cfg / "Code" / "User"

    return [
        {
            "key": "workbuddy",
            "label": "WorkBuddy",
            "kind": "json",
            "root": "mcpServers",
            "paths": [home / ".workbuddy" / "mcp.json"],
            "probe": [home / ".workbuddy"],
            "note": "then open Connectors → Custom connectors and click Trust",
        },
        {
            "key": "claude",
            "label": "Claude Code",
            "kind": "json",
            "root": "mcpServers",
            "paths": [home / ".claude.json"],
            "probe": [home / ".claude", home / ".claude.json"],
            "note": "user scope; or run: claude mcp add --scope user "
                    f"{SERVER_NAME} -- <python> -m jev_ultrafast_mcp",
        },
        {
            "key": "claude-desktop",
            "label": "Claude Desktop",
            "kind": "json",
            "root": "mcpServers",
            "paths": [claude_desktop],
            "probe": [claude_desktop.parent],
            "note": "restart the app completely (quit from the tray, not just the window)",
        },
        {
            "key": "codex",
            "label": "Codex CLI",
            "kind": "toml",
            "root": None,
            "paths": [home / ".codex" / "config.toml"],
            "probe": [home / ".codex"],
            "note": "verify with: codex mcp list",
        },
        {
            "key": "cursor",
            "label": "Cursor",
            "kind": "json",
            "root": "mcpServers",
            "paths": [home / ".cursor" / "mcp.json"],
            "probe": [home / ".cursor"],
            "note": "reload the window, then check Settings → MCP",
        },
        {
            "key": "vscode",
            "label": "VS Code (Copilot)",
            "kind": "json",
            "root": "servers",
            "paths": [vscode_user / "mcp.json"],
            "probe": [vscode_user],
            "note": "needs the 'servers' key and type=stdio; tools appear in Agent mode only",
        },
        {
            "key": "cline",
            "label": "Cline (VS Code)",
            "kind": "json",
            "root": "mcpServers",
            "paths": [
                vscode_user
                / "globalStorage"
                / "saoudrizwan.claude-dev"
                / "settings"
                / "cline_mcp_settings.json"
            ],
            "probe": [vscode_user / "globalStorage" / "saoudrizwan.claude-dev"],
            "note": "reload the window",
        },
        {
            "key": "windsurf",
            "label": "Windsurf",
            "kind": "json",
            "root": "mcpServers",
            "paths": [home / ".codeium" / "windsurf" / "mcp_config.json"],
            "probe": [home / ".codeium" / "windsurf"],
            "note": "reload the window",
        },
        {
            "key": "gemini",
            "label": "Gemini CLI",
            "kind": "json",
            "root": "mcpServers",
            "paths": [home / ".gemini" / "settings.json"],
            "probe": [home / ".gemini"],
            "note": "verify with: gemini mcp list",
        },
    ]


def _detected(client: dict[str, Any]) -> bool:
    return any(probe.exists() for probe in client["probe"])


def _target(client: dict[str, Any]) -> Path:
    """The file to write: the first that exists, else the first candidate."""
    for path in client["paths"]:
        if path.exists():
            return path
    return client["paths"][0]


# --------------------------------------------------------------------------- files


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"  ! {path} is not valid JSON ({exc})\n"
                         f"    refusing to touch it — fix the file, or run with --print")
    if not isinstance(data, dict):
        raise SystemExit(f"  ! {path} is not a JSON object\n"
                         f"    refusing to touch it — fix the file, or run with --print")
    return data


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _backup(path: Path) -> Path | None:
    """Copy the pre-install file to `<name>.bak` once, so the original survives."""
    if not path.exists():
        return None
    dest = path.with_name(path.name + ".bak")
    if not dest.exists():
        shutil.copy2(path, dest)
    return dest


# --------------------------------------------------------------------------- entries


def _entry(client: dict[str, Any], interp: str, args: list[str],
           env: dict[str, str]) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if client["root"] == "servers":  # VS Code wants the transport spelled out
        entry["type"] = "stdio"
    entry["command"] = interp
    entry["args"] = args
    if env:
        entry["env"] = env
    return entry


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_block(name: str, interp: str, args: list[str], env: dict[str, str]) -> str:
    lines = [
        f"[mcp_servers.{name}]",
        f"command = {_toml_string(interp)}",
        "args = [" + ", ".join(_toml_string(arg) for arg in args) + "]",
        "startup_timeout_sec = 20",
    ]
    if env:
        lines.append("")
        lines.append(f"[mcp_servers.{name}.env]")
        lines.extend(f"{key} = {_toml_string(value)}" for key, value in env.items())
    return "\n".join(lines) + "\n"


_MCP_TABLE = re.compile(r"^\[mcp_servers\.")


def _toml_drop_section(text: str, name: str) -> str:
    """Remove `[mcp_servers.NAME]` and any `[mcp_servers.NAME.*]` sub-tables."""
    wanted = re.compile(rf"^\[mcp_servers\.{re.escape(name)}(?:\.[^\]]*)?\]$")
    kept: list[str] = []
    dropping = False
    for line in text.splitlines():
        stripped = line.strip()
        if wanted.match(stripped):
            dropping = True
            continue
        if dropping and stripped.startswith("["):
            # A new table ends the block we are removing -- but the new table
            # may itself be another sub-table of the same server.
            dropping = bool(wanted.match(stripped))
            if dropping:
                continue
        if not dropping:
            kept.append(line)
    return "\n".join(kept).rstrip("\n")


def _toml_upsert(text: str, name: str, block: str) -> str:
    stripped = _toml_drop_section(text, name)
    if stripped:
        return stripped + "\n\n" + block
    return block


# --------------------------------------------------------------------------- actions


def _plan(client: dict[str, Any], interp: str, args: list[str], env: dict[str, str],
          remove: bool) -> tuple[Path, str | None, str]:
    """Return (path, new_text_or_None, human summary)."""
    path = _target(client)
    if client["kind"] == "toml":
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if remove:
            dropped = _toml_drop_section(text, SERVER_NAME)
            new_text = dropped + "\n" if dropped else ""
            return path, (new_text if new_text != text else None), \
                "remove [mcp_servers.%s]" % SERVER_NAME
        new_text = _toml_upsert(text, SERVER_NAME, _toml_block(SERVER_NAME, interp, args, env))
        table = f"[mcp_servers.{SERVER_NAME}]"
        verb = "replace" if table in text else "add"
        return path, new_text, f"{verb} {table}"

    data = _read_json(path)
    present = SERVER_NAME in (data.get(client["root"]) or {})
    if remove:
        if not present:
            return path, None, f"no {SERVER_NAME} entry"
        del data[client["root"]][SERVER_NAME]
        return path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", "remove entry"
    data.setdefault(client["root"], {})[SERVER_NAME] = _entry(client, interp, args, env)
    verb = "replace" if present else "add"
    return (path, json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            f"{verb} entry under \"{client['root']}\"")


# --------------------------------------------------------------------------- main


def _argv() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Register jev-ultrafast-mcp with the MCP clients on this machine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join([
            "examples:",
            "  python scripts/install.py                 install into every client found",
            "  python scripts/install.py --print -c all  show the config, write nothing",
            "  python scripts/install.py --headed        keep the browser window visible",
            "  python scripts/install.py --uninstall     take the server back out",
        ]),
    )
    parser.add_argument("-c", "--client", default="auto",
                        help="comma-separated client keys, or 'auto' (detected) / 'all'")
    parser.add_argument("--list", action="store_true", help="show detection and exit")
    parser.add_argument("--print", dest="dry_run", action="store_true",
                        help="print the config that would be written, change nothing")
    parser.add_argument("--uninstall", action="store_true", help="remove the entry again")
    parser.add_argument("--headed", action="store_true",
                        help="set JEVMCP_HEADLESS=0 so the browser is visible")
    parser.add_argument("--python", dest="interp", default=None,
                        help="interpreter for the client to spawn (default: this repo's .venv)")
    parser.add_argument("--allow-domains", default="",
                        help="comma-separated JEVMCP_ALLOW_DOMAINS to pin the url envelope")
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE",
                        help="extra environment variable (repeatable)")
    parser.add_argument("--no-backup", action="store_true",
                        help="skip the .bak copy of files being rewritten")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    return parser.parse_args()


def _env_for(args: argparse.Namespace) -> dict[str, str]:
    env = {"JEVMCP_HEADLESS": "0" if args.headed else "1"}
    if args.allow_domains:
        env["JEVMCP_ALLOW_DOMAINS"] = args.allow_domains
    for item in args.env:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--env expects KEY=VALUE, got {item!r}")
        env[key.strip()] = value
    return env


def main() -> int:
    args = _argv()
    clients = _clients()
    by_key = {client["key"]: client for client in clients}

    detected = [client for client in clients if _detected(client)]

    if args.list:
        width = max(len(client["label"]) for client in clients)
        print(f"{'client':<{width}}  {'':<9} config file")
        print("-" * (width + 60))
        for client in clients:
            mark = "detected" if _detected(client) else "—"
            print(f"{client['label']:<{width}}  {mark:<9} {_target(client)}")
        print()
        print("Add --client <key> to force one that was not detected.")
        return 0

    if args.client in ("auto", ""):
        chosen = detected
    elif args.client == "all":
        chosen = clients
    else:
        wanted = [key.strip() for key in args.client.split(",") if key.strip()]
        unknown = [key for key in wanted if key not in by_key]
        if unknown:
            raise SystemExit(f"unknown client(s): {', '.join(unknown)}\n"
                             f"known: {', '.join(by_key)}")
        chosen = [by_key[key] for key in wanted]

    if not chosen:
        print("No MCP client detected on this machine.\n")
        print("Registering is still just one JSON object. Put this in the client's")
        print("config file by hand, or force a client with --client:\n")
        interp = args.interp or _interpreter()
        _check_interpreter(interp, writing=False)
        args_list = ["-m", "jev_ultrafast_mcp"]
        print(json.dumps({"mcpServers": {SERVER_NAME: _entry(
            {"root": "mcpServers"}, interp, args_list, _env_for(args))}},
            indent=2, ensure_ascii=False))
        print("\nrun with --list to see the config file each client expects.")
        return 0

    interp = args.interp or _interpreter()
    # Uninstalling must always be possible, including after the venv it names has
    # been deleted -- which is a reason to remove the entry, not a reason to be
    # stuck with it.
    if not args.uninstall:
        _check_interpreter(interp, writing=not args.dry_run)
    module_args = ["-m", "jev_ultrafast_mcp"]
    env = _env_for(args)
    verb = "Uninstalling from" if args.uninstall else "Installing into"

    print(f"{verb} {len(chosen)} client(s)\n")
    print(f"  command : {interp} {' '.join(module_args)}")
    if env:
        print("  env     : " + "  ".join(f"{key}={value}" for key, value in env.items()))
    if args.interp is None:
        print("  (interpreter: this repo's .venv, else the one running this script)")
    print()

    plans: list[tuple[dict[str, Any], Path, str | None, str]] = []
    for client in chosen:
        path, new_text, summary = _plan(client, interp, module_args, env, args.uninstall)
        plans.append((client, path, new_text, summary))

    width = max(len(client["label"]) for client, *_ in plans)
    for client, path, new_text, summary in plans:
        state = "no change" if new_text is None else summary
        print(f"  {client['label']:<{width}}  {state}")
        print(f"  {' ' * width}  {path}")

    if args.dry_run:
        print("\n--print: nothing written. Config that would be written:\n")
        for client, path, new_text, summary in plans:
            if new_text is None:
                continue
            print(f"----- {client['label']}  ({path})")
            print(new_text)
        return 0

    writable = [(client, path, new_text, summary) for client, path, new_text, summary in plans
                if new_text is not None]
    if not writable:
        print("\nNothing to do.")
        return 0

    if not args.yes and sys.stdin.isatty():
        answer = input(f"\nWrite {len(writable)} file(s)? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted; nothing written")
            return 1

    for client, path, new_text, _summary in writable:
        assert new_text is not None
        backup = None if args.no_backup else _backup(path)
        _write(path, new_text)
        tail = f"  (backup: {backup.name})" if backup else ""
        print(f"  wrote {path}{tail}")

    print()
    if args.uninstall:
        print("Done. Restart the client(s) to drop the tools.")
    else:
        for client, path, new_text, _summary in writable:
            print(f"  {client['label']}: {client['note']}")
        print("\nRestart the client, then ask: \"open example.com and tell me the headline\".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
