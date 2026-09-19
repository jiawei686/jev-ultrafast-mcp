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


_WORKBUDDY_DIR_NAMES = (".workbuddy", ".workbuddy-ai")
# Files the app rewrites while it runs. `workbuddy.db-wal` is the decisive one:
# a live app holds that write-ahead log open, so it is touched every few seconds.
_WORKBUDDY_SENTINELS = ("workbuddy.db-wal", "workbuddy.db", "mcp.json", "logs", "app")


def _touched_at(directory: Path) -> float:
    """When the app last wrote here, judged by the newest thing it leaves behind."""
    newest = 0.0
    for name in _WORKBUDDY_SENTINELS:
        try:
            newest = max(newest, (directory / name).stat().st_mtime)
        except OSError:
            continue
    return newest


def _workbuddy_config_dirs() -> list[Path]:
    """WorkBuddy config dirs on this machine, the one in use first.

    WorkBuddy resolves its config dir from WORKBUDDY_CONFIG_DIR and otherwise
    falls back to `~/.workbuddy`, but a machine can carry more than one install:
    an older app keeps `~/.workbuddy` while the current one is pointed at
    `~/.workbuddy-ai`. Only one of them belongs to the app the user is actually
    looking at, and writing the other registers nothing and logs nothing -- the
    same silent failure this script exists to remove, one directory over.

    An explicit env var is authoritative rather than merely a candidate: it is
    the same thing the app itself obeys, so it must not lose a freshness vote to
    a directory that happens to have been touched more recently.
    """
    explicit = (os.environ.get("WORKBUDDY_CONFIG_DIR")
                or os.environ.get("CODEBUDDY_CONFIG_DIR") or "").strip()
    pinned = [Path(explicit).expanduser()] if explicit else []
    fallbacks: list[Path] = []
    for name in _WORKBUDDY_DIR_NAMES:
        candidate = _home() / name
        if candidate not in pinned and candidate not in fallbacks:
            fallbacks.append(candidate)
    # `sorted` is stable, so dirs that were never touched keep the declared order.
    return pinned + sorted(fallbacks, key=_touched_at, reverse=True)


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

    workbuddy_dirs = _workbuddy_config_dirs()
    workbuddy_note = "restart the app, then Connectors → Custom connectors → Trust"
    if sum(1 for directory in workbuddy_dirs if directory.exists()) > 1:
        workbuddy_note += ("  (more than one WorkBuddy config dir on this machine"
                           " — the one in use was chosen; --list shows which)")

    return [
        {
            "key": "workbuddy",
            "label": "WorkBuddy",
            "kind": "json",
            "root": "mcpServers",
            "paths": [directory / "mcp.json" for directory in workbuddy_dirs],
            # Chosen by directory, not by file. The config dir in use usually has
            # no mcp.json yet, so "the first file that exists" would skip past it
            # and land in a stale install -- which is exactly how a server gets
            # registered somewhere nobody is looking.
            "target": lambda: workbuddy_dirs[0] / "mcp.json",
            "probe": workbuddy_dirs,
            "note": workbuddy_note,
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
    """The file to write: the first that exists, else the first candidate.

    A client may override this with `target`, for the cases where "which file
    exists" is the wrong question -- see the WorkBuddy entry.
    """
    chooser = client.get("target")
    if chooser is not None:
        return chooser()
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


# The keys this script writes are command, args, env and (for VS Code) type.
# Everything else already in an entry -- `cwd`, `disabled`, a variable set by
# hand -- belongs to the user, and an install is not allowed to be the thing
# that deletes it.
def _entry(client: dict[str, Any], interp: str, args: list[str], env: dict[str, str],
           existing: Any = None) -> dict[str, Any]:
    """The entry to write, merged over whatever is already there.

    Replacing an entry wholesale dropped every key this script does not write,
    so a hand-added `cwd` -- and an environment allowlist, which is a safety
    setting -- disappeared on the first install. That is the opposite of what
    this module promises: merging into a config, not overwriting it.
    """
    entry: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    if client["root"] == "servers":  # VS Code wants the transport spelled out
        entry["type"] = "stdio"
    entry["command"] = interp
    entry["args"] = args
    # Variables are merged, not replaced: `--env` sets what it names and leaves
    # the rest alone, so an allowlist added by hand survives a plain install.
    merged = dict(entry.get("env") or {})
    merged.update(env)
    if merged:
        entry["env"] = merged
    else:
        entry.pop("env", None)
    return entry


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


_TOML_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*)\s*=")


def _toml_key(line: str) -> str:
    match = _TOML_KEY.match(line)
    return match.group(1) if match else ""


def _toml_section_lines(text: str, header: str) -> list[str]:
    """The non-blank body lines of `[header]`, up to the next table header."""
    wanted = re.compile(rf"^\[{re.escape(header)}\]$")
    body: list[str] = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = bool(wanted.match(stripped))
            continue
        if inside and stripped:
            body.append(stripped)
    return body


def _toml_carry_over(text: str, name: str,
                     env: dict[str, str]) -> tuple[list[str], list[str]]:
    """Lines already in Codex's tables that this script does not write.

    The same contract as the JSON path: a hand-added `cwd`, a timeout someone
    tuned, a variable set outside this script -- all of it is the user's, and an
    install must not be the thing that deletes it. Lines are carried across
    verbatim, so a value the script has no opinion about keeps its own spelling.
    """
    main = [line for line in _toml_section_lines(text, f"mcp_servers.{name}")
            if _toml_key(line) not in ("command", "args")]
    sub = [line for line in _toml_section_lines(text, f"mcp_servers.{name}.env")
           if _toml_key(line) not in env]
    return main, sub


def _toml_block(name: str, interp: str, args: list[str], env: dict[str, str],
                keep: list[str] = (), keep_env: list[str] = ()) -> str:
    lines = [
        f"[mcp_servers.{name}]",
        f"command = {_toml_string(interp)}",
        "args = [" + ", ".join(_toml_string(arg) for arg in args) + "]",
    ]
    # Seeded for a fresh entry only: a timeout already in the file comes back
    # through `keep`, so re-installing does not quietly reset it to the default.
    if not any(_toml_key(line) == "startup_timeout_sec" for line in keep):
        lines.append("startup_timeout_sec = 20")
    lines.extend(keep)
    env_lines = [f"{key} = {_toml_string(value)}" for key, value in env.items()]
    env_lines.extend(keep_env)
    if env_lines:
        lines.append("")
        lines.append(f"[mcp_servers.{name}.env]")
        lines.extend(env_lines)
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
        keep, keep_env = _toml_carry_over(text, SERVER_NAME, env)
        block = _toml_block(SERVER_NAME, interp, args, env, keep, keep_env)
        new_text = _toml_upsert(text, SERVER_NAME, block)
        table = f"[mcp_servers.{SERVER_NAME}]"
        verb = "replace" if table in text else "add"
        return path, new_text, f"{verb} {table}"

    data = _read_json(path)
    existing = data.get(client["root"])
    if existing is not None and not isinstance(existing, dict):
        raise SystemExit(f"  ! {path}: \"{client['root']}\" is not a JSON object\n"
                         f"    refusing to touch it — fix the file, or run with --print")
    present = SERVER_NAME in (existing or {})
    if remove:
        if not present:
            return path, None, f"no {SERVER_NAME} entry"
        del existing[SERVER_NAME]
        return path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", "remove entry"
    data.setdefault(client["root"], {})[SERVER_NAME] = _entry(
        client, interp, args, env, (existing or {}).get(SERVER_NAME))
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
