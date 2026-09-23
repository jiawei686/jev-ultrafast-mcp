"""Prove a packed bundle is a working MCP server, not just a well-formed zip.

`scripts/build_mcpb.py` checks that the archive is complete; this checks that what is inside it
runs. It extracts the bundle, executes the entry point the manifest names, and drives a real stdio
handshake against it.

Two launch routes, because a host can take either
-------------------------------------------------
* **The entry point file**, run directly (`python <entry_point>`). Nothing but a Python
  interpreter is needed, so this route always runs.
* **The `mcp_config` command**, run exactly as the manifest declares it
  (`uv run --directory <dir> jev-ultrafast-mcp`). This is the route the docs advertise and the only
  one that exercises the host's dependency resolution, but it needs `uv`, so it runs under `--uv`
  -- which is what the CI job passes after installing uv.

The second route is not a hand-written copy of the first: it is built by reading
`manifest.server.mcp_config` and substituting `${__dirname}` and every `${user_config.*}`. So a
manifest whose declared command is wrong fails here, and so does a bundle that cannot survive the
blank `user_config` values a host passes when the user leaves the optional keys empty -- which is
the configuration most users will actually run.

`--uv` refuses to pass when `uv` is not on PATH. A check that quietly skips itself is decoration;
if you asked for the uv route and uv is missing, that is a failure, not a pass.

The handshake deliberately does not use `subprocess.run(input=...)`. That writes the payload and
closes stdin at once, and on stdio transport an EOF can shut the server down before it has read the
last request -- the first version of this check did that and reported "0 tools" for a server that
answers fine, which reads as a broken bundle rather than a broken check.

Usage:
    python scripts/check_bundle.py [path/to/bundle.mcpb] [--uv]
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = 10


def newest_bundle() -> pathlib.Path:
    candidates = sorted(
        (ROOT / "dist").glob("*.mcpb"), key=lambda path: path.stat().st_mtime
    )
    if not candidates:
        raise SystemExit("no bundle in dist/ -- run scripts/build_mcpb.py first")
    return candidates[-1]


def manifest_command(
    manifest: dict, directory: pathlib.Path
) -> tuple[list[str], dict[str, str]]:
    """Turn the manifest's declared `mcp_config` into something runnable.

    `${__dirname}` becomes the directory the bundle was extracted into, and every
    `${user_config.X}` becomes the empty string. Both keys the manifest declares are optional, so
    "" is what a host passes when the user leaves them blank -- and that is worth exercising rather
    than assuming, because an empty value must fall through to the server's own defaults instead of
    selecting something bogus.
    """
    config = manifest["server"]["mcp_config"]

    substitutions = {"${__dirname}": str(directory)}
    for key in manifest.get("user_config", {}):
        substitutions["${user_config." + key + "}"] = ""

    def expand(text: str) -> str:
        for token, value in substitutions.items():
            text = text.replace(token, value)
        if "${" in text:
            # A token nobody expanded must not be handed to the server as if it were a value. This
            # is not hypothetical: an unexpanded `${user_config.typesafe_api_key}` in `env` would
            # arrive as that literal string, and the handshake would still pass, because
            # `initialize` and `tools/list` never need an API key.
            raise SystemExit(
                "the manifest's mcp_config uses a token this check cannot expand: " + text
            )
        return text

    command = [expand(str(config["command"]))]
    command += [expand(str(argument)) for argument in config.get("args", [])]
    env = {key: expand(str(value)) for key, value in config.get("env", {}).items()}
    return command, env


def handshake(command: list[str], env: dict[str, str] | None = None) -> list[str]:
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "check-bundle", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]

    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        for request in requests:
            proc.stdin.write(json.dumps(request) + chr(10))
        proc.stdin.flush()

        answers: dict[int, dict] = {}
        deadline = time.time() + 120
        while time.time() < deadline and (1 not in answers or 2 not in answers):
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in message:
                answers[message["id"]] = message
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if 1 not in answers:
        raise SystemExit(
            " ".join(command)
            + " never answered initialize; stderr was: "
            + proc.stderr.read()[-1500:]
        )
    return sorted(
        tool["name"] for tool in answers[2].get("result", {}).get("tools", [])
    )


def verify(label: str, names: list[str]) -> None:
    print(label.ljust(30) + ": " + str(len(names)) + " " + str(names))
    if len(names) != EXPECTED_TOOLS:
        raise SystemExit(
            label + ": expected " + str(EXPECTED_TOOLS) + " tools, got " + str(len(names))
        )
    if "browser_goal" not in names:
        raise SystemExit(label + ": the handoff tool is missing from the bundle")


def main() -> int:
    arguments = sys.argv[1:]
    use_uv = "--uv" in arguments
    positional = [item for item in arguments if not item.startswith("--")]
    bundle = (
        pathlib.Path(positional[0]).resolve() if positional else newest_bundle()
    )
    if not bundle.is_file():
        raise SystemExit("no such bundle: " + str(bundle))

    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    entry_point = manifest["server"]["entry_point"]

    with tempfile.TemporaryDirectory() as name:
        extracted = pathlib.Path(name)
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(extracted)

        entry = extracted / entry_point
        if not entry.is_file():
            raise SystemExit(
                "the manifest names " + entry_point + ", which is not in the archive"
            )

        print("bundle     : " + bundle.name)
        print("entry point: " + entry_point)

        verify("entry point (python)", handshake([sys.executable, str(entry)]))

        if use_uv:
            command, extra_env = manifest_command(manifest, extracted)
            if shutil.which(command[0]) is None:
                raise SystemExit(
                    "--uv was passed but " + command[0] + " is not on PATH"
                )
            verify(
                "mcp_config (" + command[0] + ")",
                handshake(command, env={**os.environ, **extra_env}),
            )

    print(
        "OK: the packed server answers a stdio handshake with "
        + str(EXPECTED_TOOLS)
        + " tools"
        + (" on both launch routes" if use_uv else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
