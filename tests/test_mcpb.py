"""Tests for the Claude Desktop bundle.

A `.mcpb` is a zip plus a manifest, and the manifest is a set of promises about what is inside:
which version, which tools, and which file to run. Nothing executes a manifest, so every one of
those promises is the kind that goes stale without anyone noticing.

That is not hypothetical here. The first version of this manifest pointed `entry_point` at
`jev_ultrafast_mcp/server.py`, which cannot be an entry point at all -- it uses relative imports,
so running it as a script dies with "attempted relative import with no known parent package". The
manifest validated against its own schema regardless, because the schema only asks whether the
field is a string. The bundle was unpacked and the entry point was actually run to find it, and
these tests exist so that finding does not have to be repeated by hand.
"""

from __future__ import annotations

import importlib.util
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "mcpb" / "manifest.json"
LAUNCHER = ROOT / "mcpb" / "server.py"
BUILD = ROOT / "scripts" / "build_mcpb.py"

TOOL_RE = re.compile(
    r"@SERVER\.tool\(.*?\)\s*\ndef\s+([a-z_][a-z0-9_]*)\(", re.MULTILINE,
)


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _registered_tools() -> list[str]:
    source = (ROOT / "jev_ultrafast_mcp" / "server.py").read_text(encoding="utf-8")
    return TOOL_RE.findall(source)


def _build_module():
    spec = importlib.util.spec_from_file_location("build_mcpb", BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_manifest_advertises_exactly_the_tools_the_server_registers():
    """A manifest that lists a tool the server does not have is an install dialog that lies.

    The bundle's tool list is what a directory or an install dialog shows *before* anything runs,
    so it is the one place a missing or invented tool cannot be corrected by the server itself.
    """
    advertised = sorted(tool["name"] for tool in _manifest()["tools"])
    registered = sorted(_registered_tools())

    assert advertised == registered, (
        "mcpb/manifest.json advertises " + str(advertised) + " but the server registers " + str(registered)
    )
    assert len(advertised) == 10, "the docs say ten tools"


def test_the_entry_point_is_a_launcher_and_not_a_package_module():
    """The entry point must be runnable as a script, which a module with relative imports is not.

    `from . import x` only resolves inside a package, and an entry point is executed as a file. So
    the launcher has to import absolutely -- and that works because a `uv` bundle is installed as a
    project before it runs.
    """
    assert LAUNCHER.is_file(), "mcpb/server.py is the declared entry point and must exist"

    body = LAUNCHER.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )
    assert not re.search(r"^\s*from\s+\.", code, re.MULTILINE), (
        "the entry point uses a relative import, which cannot resolve when run as a script"
    )
    assert "from jev_ultrafast_mcp.server import main" in code, (
        "the entry point must import the server by its absolute path"
    )


def test_the_manifest_entry_point_is_actually_inside_the_archive(tmp_path):
    """Build the bundle and check the promise, rather than reading the build script and hoping.

    The failure this pins is a manifest naming a file the packer never copies: the manifest is
    valid, the archive is valid, and the bundle is dead on arrival.
    """
    module = _build_module()
    archive_path = module.build(tmp_path, require_release=False)

    manifest = json.loads(
        zipfile.ZipFile(archive_path).read("manifest.json").decode("utf-8"))
    entry_point = manifest["server"]["entry_point"]

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())

    assert entry_point in names, (
        "the manifest names entry_point " + entry_point + " but the archive holds " + str(sorted(names))
    )

    # A `uv` bundle ships source and a pyproject for the host to resolve, so both must be present.
    assert manifest["server"]["type"] == "uv"
    assert "pyproject.toml" in names
    assert "icon.png" in names


def test_the_manifest_does_not_claim_a_runtime_the_bundle_cannot_carry():
    """`type: python` cannot portably bundle this project's dependencies, so it must not be used.

    The MCPB spec says a `python` bundle cannot carry compiled dependencies; this project depends on
    the `mcp` SDK, which depends on pydantic, which is compiled. `uv` is the documented answer, and
    it is the difference between a bundle that installs and one that fails on the user's machine.
    """
    assert _manifest()["server"]["type"] == "uv", (
        "this project's dependency tree is not pure Python, so a bundled python server would not work"
    )
    assert _manifest()["manifest_version"] >= "0.4", (
        "server.type 'uv' requires manifest version 0.4 or later"
    )
