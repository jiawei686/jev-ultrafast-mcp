"""Prove a packed bundle is a working MCP server, not just a well-formed zip.

`scripts/build_mcpb.py` checks that the archive is complete; this checks that what is inside it
runs. It extracts the bundle, executes the entry point the manifest names, and drives a real stdio
handshake against it.

What it does not cover
----------------------
The `uv` runtime itself. A bundle is launched by the host's uv, and uv is not necessarily installed
where this runs, so dependency resolution stays untested. Everything the bundle is responsible for
is tested: that the entry point exists at the declared path, that it can be executed as a script,
that the package it imports is complete, and that the server answers with the ten tools the docs
promise.

The handshake deliberately does not use `subprocess.run(input=...)`. That writes the payload and
closes stdin at once, and on stdio transport an EOF can shut the server down before it has read the
last request -- the first version of this check did that and reported "0 tools" for a server that
answers fine, which reads as a broken bundle rather than a broken check.

Usage:
    python scripts/check_bundle.py [path/to/bundle.mcpb]
"""

from __future__ import annotations

import json
import pathlib
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


def handshake(command: list[str]) -> list[str]:
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
    )
    try:
        for request in requests:
            proc.stdin.write(json.dumps(request) + chr(10))
        proc.stdin.flush()

        answers: dict[int, dict] = {}
        deadline = time.time() + 60
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
            "the entry point never answered initialize; stderr was: " + proc.stderr.read()[-1500:]
        )
    return sorted(
        tool["name"] for tool in answers[2].get("result", {}).get("tools", [])
    )


def main() -> int:
    bundle = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else newest_bundle()
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
            raise SystemExit("the manifest names " + entry_point + ", which is not in the archive")

        names = handshake([sys.executable, str(entry)])

    print("bundle     : " + bundle.name)
    print("entry point: " + entry_point)
    print("tools      : " + str(len(names)) + " " + str(names))

    if len(names) != EXPECTED_TOOLS:
        raise SystemExit("expected " + str(EXPECTED_TOOLS) + " tools, got " + str(len(names)))
    if "browser_goal" not in names:
        raise SystemExit("the handoff tool is missing from the bundle")

    print("OK: the packed server answers a stdio handshake with " + str(EXPECTED_TOOLS) + " tools")
    return 0


if __name__ == "__main__":
    sys.exit(main())
