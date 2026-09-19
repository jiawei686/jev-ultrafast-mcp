"""Tests for the files that describe the project rather than run it.

The README is the product page, `server.json` is what the MCP registry reads, and `pyproject.toml`
is what the packaging indexes read. All three are hand-written prose about behaviour that lives in
code, which is exactly the kind of thing that goes stale without anyone noticing -- the READMEs
described this project as needing no key at all for as long as they did because nothing checked.

So these tests assert the claims that carry weight, not the wording: that the registry entry agrees
with the package, that the files the READMEs link to exist, that every tool the server registers is
named where a reader looks for it, and that the two sentences about keys keep saying the true thing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"

TOOL_RE = re.compile(r"@SERVER\.tool\(\)\s*\ndef\s+([a-z_][a-z0-9_]*)\(", re.MULTILINE)


def _registered_tools() -> list[str]:
    source = (ROOT / "jev_ultrafast_mcp" / "server.py").read_text(encoding="utf-8")
    return TOOL_RE.findall(source)


def _pyproject() -> dict:
    tomllib = pytest.importorskip("tomllib", reason="stdlib tomllib is 3.11+")
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


# --- the registry entry, which has to agree with the package -------------------------------

def test_server_json_is_a_valid_registry_entry():
    entry = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

    assert entry["$schema"].startswith("https://static.modelcontextprotocol.io/schemas/")
    # The registry requires a reverse-DNS name it can tie to an owner.
    assert re.fullmatch(r"io\.github\.[a-z0-9-]+/[a-z0-9-]+", entry["name"]), entry["name"]
    assert entry["name"].startswith("io.github.jiawei686/")
    assert entry["description"] and entry["version"]
    assert entry["repository"]["url"].endswith("jev-ultrafast-mcp")
    assert entry["repository"]["source"] == "github"


def test_server_json_points_at_the_real_package():
    entry = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    project = _pyproject()["project"]

    packages = entry["packages"]
    assert len(packages) == 1
    package = packages[0]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == project["name"], "the registry entry must name this package"
    assert package["transport"] == {"type": "stdio"}


def test_every_version_in_the_tree_agrees():
    """A release that bumps one of these and not the others is a release that lies."""
    entry = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    init = (ROOT / "jev_ultrafast_mcp" / "__init__.py").read_text(encoding="utf-8")

    versions = {
        "pyproject.toml": _pyproject()["project"]["version"],
        "server.json": entry["version"],
        "server.json package": entry["packages"][0]["version"],
        "__init__.py": re.search(r'__version__ = "([^"]+)"', init).group(1),
    }

    assert len(set(versions.values())) == 1, versions


# --- discoverability metadata --------------------------------------------------------------

def test_the_package_metadata_carries_search_words():
    project = _pyproject()["project"]

    assert len(project["description"]) > 60, "the description is what a search result shows"
    assert {"mcp", "model-context-protocol", "browser-automation"} <= set(project["keywords"])
    assert "Topic :: Scientific/Engineering :: Artificial Intelligence" in project["classifiers"]
    assert {"Homepage", "Repository", "Issues"} <= set(project["urls"])


def test_the_publish_workflow_cannot_fire_on_an_ordinary_push():
    """Publishing is a deliberate act. A tag-triggered workflow is the only kind allowed here."""
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    trigger = workflow.split("jobs:", 1)[0]

    assert "tags:" in trigger
    assert "branches:" not in trigger
    assert "id-token: write" in workflow, "trusted publishing needs the OIDC token"


# --- the prose has to match the code it describes ------------------------------------------

def test_every_registered_tool_is_named_where_a_reader_looks():
    tools = _registered_tools()
    assert len(tools) == 10, f"the READMEs say ten tools; the server registers {len(tools)}"

    for path in (README, README_ZH, ROOT / "llms.txt"):
        text = path.read_text(encoding="utf-8")
        missing = [name for name in tools if name not in text]
        assert not missing, f"{path.name} never mentions {missing}"


def test_the_readmes_do_not_claim_the_project_needs_no_key():
    """True of the browser tools, false of the project: `browser_goal` sends a goal to a model."""
    for path in (README, README_ZH):
        text = path.read_text(encoding="utf-8")
        assert "No API keys." not in text, path.name
        assert "不需要任何 API key**，没什么要注册的。" not in text, path.name

    # And the accurate version is the one that is there.
    assert "`browser_goal` is the one opt-in exception" in README.read_text(encoding="utf-8")
    assert "`browser_goal` 是唯一的" in README_ZH.read_text(encoding="utf-8")


def test_the_readmes_say_the_decision_model_is_the_only_thing_that_calls_out():
    for path in (README, README_ZH):
        text = path.read_text(encoding="utf-8")
        assert "Nothing leaves your machine." not in text, path.name
        assert "TYPESAFE_BASE_URL" in text, f"{path.name} must document the OpenRouter route"
        assert "TYPESAFE_MODEL" in text, f"{path.name} must document the model slug"


def test_local_links_in_the_readmes_resolve():
    """A README whose screenshots and design notes are 404s reads as an abandoned project."""
    pattern = re.compile(r"\]\((?!https?://|#)([^)]+)\)")
    for path in (README, README_ZH):
        for target in pattern.findall(path.read_text(encoding="utf-8")):
            # An anchor addresses a heading, so only the file part has to exist.
            file_part = target.split("#", 1)[0]
            assert (ROOT / file_part).exists(), f"{path.name} links to a missing {target}"
