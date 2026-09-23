"""Build the Claude Desktop bundle (.mcpb) for this project.

An MCPB is a zip holding a local MCP server plus a `manifest.json`. The point of the format is a
double-click install, so the bundle has to be self-describing: everything the host needs is either
inside the archive or declared in the manifest.

Why `server.type` is `uv` and not `python`
------------------------------------------
The MCPB spec documents a real limitation for `type: "python"`: a bundle cannot portably carry
compiled dependencies. This project depends on the `mcp` SDK, which depends on pydantic, which is
compiled -- so a `python` bundle would be broken on any machine whose wheel does not match the
build host. `type: "uv"` (manifest version 0.4) moves dependency resolution to the host's uv, which
is the documented answer to exactly this problem, and it is why the bundle ships source and a
`pyproject.toml` rather than a vendored virtualenv.

Why the release check exists
----------------------------
The manifest states a version, and that version is a promise about which code is inside. This repo
publishes to PyPI from tags, and `main` normally carries unreleased commits -- so a bundle built
from a working tree is a bundle whose declared version is a guess. The manifest version is therefore
required to equal `pyproject.toml`, and a build whose `HEAD` is not the matching release tag warns
loudly (or fails, with `--require-release`, which is what CI passes).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "jev_ultrafast_mcp"

# Copied to the bundle root. `README.md` is not decoration: `pyproject.toml` declares
# `readme = "README.md"`, and a build backend that cannot find it refuses to build the project at
# all -- so leaving it out produces a bundle that fails on first launch, not on first read.
FILES = ("pyproject.toml", "README.md", "LICENSE")
FROM_MANIFEST_DIR = ("manifest.json", "icon.png", "server.py")

# Checked after packing, because "the zip exists" is not "the zip is complete".
REQUIRED_IN_ZIP = (
    "manifest.json",
    "icon.png",
    "server.py",
    "pyproject.toml",
    "README.md",
    PACKAGE + "/server.py",
    PACKAGE + "/js/observer.js",
)


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if not found:
        raise SystemExit("could not read the version out of pyproject.toml")
    return found.group(1)


def head_tag() -> str:
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def package_files() -> list[pathlib.Path]:
    root = ROOT / PACKAGE
    if not root.is_dir():
        raise SystemExit("missing package directory: " + PACKAGE)
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )


def build(output_dir: pathlib.Path, require_release: bool = False) -> pathlib.Path:
    version = project_version()
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))

    if manifest["version"] != version:
        raise SystemExit(
            "mcpb/manifest.json says "
            + manifest["version"]
            + " but pyproject.toml says "
            + version
            + " -- the bundle would describe code it does not contain"
        )

    tag = head_tag()
    wanted = "v" + version
    if tag != wanted:
        message = (
            "HEAD is not at tag "
            + wanted
            + ((" (it is at " + tag + ")") if tag else " (it is not on a tag)")
            + ": this bundle declares "
            + version
            + " but contains whatever is in the working tree"
        )
        if require_release:
            raise SystemExit(message)
        print("WARNING: " + message)
        print("WARNING: do not distribute this file until that version is actually released.")

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / ("jev-ultrafast-mcp-" + version + ".mcpb")

    with tempfile.TemporaryDirectory() as staging_name:
        staging = pathlib.Path(staging_name)
        for name in FROM_MANIFEST_DIR:
            source = ROOT / "mcpb" / name
            if not source.is_file():
                raise SystemExit("missing bundle file: mcpb/" + name)
            (staging / name).write_bytes(source.read_bytes())
        for name in FILES:
            source = ROOT / name
            if not source.is_file():
                raise SystemExit("missing bundle file: " + name)
            (staging / name).write_bytes(source.read_bytes())
        for source in package_files():
            destination = staging / source.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())

        if target.exists():
            target.unlink()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staging).as_posix())

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        bad = archive.testzip()
        if bad is not None:
            raise SystemExit("the archive is corrupt at " + bad)
    missing = [name for name in REQUIRED_IN_ZIP if name not in names]
    if missing:
        raise SystemExit("the bundle is missing: " + ", ".join(missing))

    print(
        "wrote "
        + str(target)
        + " ("
        + str(len(names))
        + " files, "
        + str(round(target.stat().st_size / 1024))
        + " KB)"
    )
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-release",
        action="store_true",
        help="fail unless HEAD is exactly the tag matching the declared version",
    )
    parser.add_argument(
        "--out",
        default="dist",
        help="output directory, relative to the repository root (default: dist)",
    )
    args = parser.parse_args()
    build(ROOT / args.out, require_release=args.require_release)
    sys.exit(0)
