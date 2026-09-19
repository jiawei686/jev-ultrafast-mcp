"""Tests for the files that describe the project rather than run it.

The README is the product page, `server.json` is what the MCP registry reads, and `pyproject.toml`
is what the packaging indexes read. All three are hand-written prose about behaviour that lives in
code, which is exactly the kind of thing that goes stale without anyone noticing -- the READMEs
described this project as needing no key at all for as long as they did because nothing checked.

So these tests assert the claims that carry weight, not the wording: that the registry entry agrees
with the package, that the files the READMEs link to exist, that every tool the server registers is
named where a reader looks for it, that the sentences about keys keep saying the true thing, and
that the one-line summaries still say what the project is actually for.
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


def test_the_readmes_still_separate_the_keyless_surface_from_the_model():
    """True of the browser tools, false of the project: `browser_goal` sends a goal to a model."""
    for path in (README, README_ZH):
        text = path.read_text(encoding="utf-8")
        assert "No API keys." not in text, path.name
        assert "不需要任何 API key**，没什么要注册的。" not in text, path.name
        # And the accurate version is still there, on both sides of the line.
        assert "browser_goal" in text, path.name
        assert "TYPESAFE_API_KEY" in text, path.name


def test_the_readmes_say_the_decision_model_is_the_only_thing_that_calls_out():
    for path in (README, README_ZH):
        text = path.read_text(encoding="utf-8")
        assert "Nothing leaves your machine." not in text, path.name
        assert "TYPESAFE_BASE_URL" in text, f"{path.name} must document the OpenRouter route"
        assert "TYPESAFE_MODEL" in text, f"{path.name} must document the model slug"


def test_the_readmes_promise_attach_mode_leaves_the_users_browser_alone():
    """The one claim in here that, if wrong, costs a user every tab they had open.

    `attach` mode drives the browser the user is already using, so "close the session" has to mean
    detach rather than quit. The code now separates the two (see test_attach_mode.py); this keeps the
    README from quietly dropping the promise that makes attach mode reasonable to enable.
    """
    for path, word in ((README, "detach"), (README_ZH, "断开")):
        text = path.read_text(encoding="utf-8")
        assert "JEVMCP_CDP_URL" in text, path.name
        assert word in text, f"{path.name} documents attach mode without saying it detaches"


def test_local_links_in_the_readmes_resolve():
    """A README whose screenshots and design notes are 404s reads as an abandoned project."""
    pattern = re.compile(r"\]\((?!https?://|#)([^)]+)\)")
    for path in (README, README_ZH):
        for target in pattern.findall(path.read_text(encoding="utf-8")):
            # An anchor addresses a heading, so only the file part has to exist.
            file_part = target.split("#", 1)[0]
            assert (ROOT / file_part).exists(), f"{path.name} links to a missing {target}"


# --- what the project says it is for ---------------------------------------------------------

# Ways of saying "the browser work can be handed off" that an honest rewrite would reach for.
HANDOFF = ("hand ", "hands off", "server-side", "delegate", "交出去", "服务端")


def test_the_handoff_is_the_pitch_and_not_a_buried_option():
    """`browser_goal` is the reason to run this, so a reader meets it before scrolling.

    This project was described for a while as a keyless server with no second model: true of the
    browser tools, and the reason it read as a smaller thing than it is. What it actually sells is
    that the agent can decline to drive, so that has to be up front where a reader decides rather
    than only in the tool reference.
    """
    for path in (README, README_ZH, ROOT / "llms.txt"):
        text = path.read_text(encoding="utf-8")
        assert "No second model" not in text, f"{path.name} still denies the model it ships"
        assert "no second model" not in text, path.name
        assert "没有第二个模型" not in text, path.name

    for path in (README, README_ZH):
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:40])
        assert "browser_goal" in head, f"{path.name} buries the handoff below the fold"


def test_the_on_ramp_comes_before_the_argument_for_it():
    """Install first, argument second.

    The commands and the first thing to say are what a reader came for; the case for the project is
    what convinces them to stay. Putting the second above the first costs the reader the thing they
    were looking for, so the on-ramp is asserted to be at the top rather than left to drift.
    """
    for path, quick, story in (
        (README, "## Quick start", "## Why another browser MCP?"),
        (README_ZH, "## 快速开始", "## 为什么还要再造一个浏览器 MCP"),
    ):
        text = path.read_text(encoding="utf-8")
        head = "\n".join(text.splitlines()[:40])

        assert "git clone" in head, f"{path.name} hides the install below the fold"
        assert "install.py" in head, f"{path.name} never says how to write a client config"
        assert text.index(quick) < text.index(story), f"{path.name} argues before it onboards"


def test_the_one_line_summaries_sell_the_handoff():
    """A registry listing and a package description are a search result: they get one sentence."""
    entry = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

    for label, text in (("server.json", entry["description"]),
                        ("pyproject.toml", _pyproject()["project"]["description"])):
        assert any(word in text for word in HANDOFF), f"{label} describes the project without it"
        assert "server" in text.lower(), label
