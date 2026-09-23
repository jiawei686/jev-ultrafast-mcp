"""MCP annotations decide whether a host stops to ask before every call.

Leaving them off is not neutral. An unannotated tool reads as "could do
anything", so a careful client confirms *every* call — including the reads that
make up most of an agent loop. `browser_observe` is the highest-frequency call
in the whole surface and cannot change anything, so having it prompt is the
single most annoying thing this server can do to a user.

These tests pin the read/write split so a new tool cannot quietly land
unannotated, which would silently reintroduce a prompt per call.
"""

import asyncio

from jev_ultrafast_mcp import server as S

READS = {"browser_observe", "browser_assert", "browser_sessions", "browser_doctor"}
WRITES = {
    "browser_open",
    "browser_act",
    "browser_goal",
    "browser_macro",
    "browser_tabs",
    "browser_close",
}


def _tools():
    return {tool.name: tool for tool in asyncio.run(S.SERVER.list_tools())}


def test_the_surface_is_exactly_what_the_tests_cover():
    """A new tool has to be classified here, not just added to the server."""
    assert set(_tools()) == READS | WRITES


def test_every_tool_declares_what_it_does():
    for name, tool in _tools().items():
        assert tool.annotations is not None, f"{name} carries no annotations"


def test_the_reads_are_declared_reads():
    """These can never change the page, so interrupting them buys no safety."""
    tools = _tools()
    for name in sorted(READS):
        annotations = tools[name].annotations
        assert annotations.read_only_hint is True, name
        assert annotations.idempotent_hint is True, name
        assert annotations.destructive_hint is not True, name


def test_the_writes_do_not_pretend_to_be_reads():
    tools = _tools()
    for name in sorted(WRITES):
        annotations = tools[name].annotations
        assert annotations.read_only_hint is False, name
        # None would read as "might destroy something" — more prompting, not
        # less. The confirmation envelope is enforced in safety.py instead.
        assert annotations.destructive_hint is False, name


def test_open_world_marks_what_leaves_the_page_you_are_on():
    """A tool with one action that reaches a new host is an open-world tool.

    `browser_tabs` is in the first group for `action="new"` alone: it creates a
    target at a caller-supplied URL, which is what `browser_open` does. It was
    annotated local until the envelope fix, and the claim was false -- the same
    URL was refused through `browser_act` and accepted through this tool.
    """
    tools = _tools()
    for name in ("browser_open", "browser_act", "browser_goal", "browser_macro",
                 "browser_tabs"):
        assert tools[name].annotations.open_world_hint is True, name
    for name in ("browser_observe", "browser_assert", "browser_sessions",
                 "browser_doctor", "browser_close"):
        assert tools[name].annotations.open_world_hint is False, name
