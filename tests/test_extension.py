"""Tests for the Chrome extension that shows the element table.

The extension is a second implementation of something the server already does, and the only reason
it is allowed to exist is that it is not a second implementation at all. It ships the server's own
observer byte for byte and renders with a port of `observe.py` that is held to the real renderer by
fixtures generated from Python. Both of those are claims. A claim nothing checks is how this project
ended up with a broken image on its own PyPI page, so they get checked here.

Two of these tests paid for themselves before the extension was even finished. The observer emits
`offscreen`, the header counts it and prints "(offscreen N, » = will scroll on act)", and
`Observation.from_raw` never read it -- so the note was unreachable text. The same was true of
`inViewport`, which made the `»` flag on an element dead code. Both surfaced as parity failures
between the port and Python rather than as a bug report, which is the entire argument for having a
port and a parity harness at all.
"""

from __future__ import annotations

import codecs
import glob
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jev_ultrafast_mcp.observe import Element, Observation  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome-extension"
LIB = EXT / "lib"
TEST = EXT / "test"
MANIFEST = EXT / "manifest.json"
OBSERVER_SOURCE = ROOT / "jev_ultrafast_mcp" / "js" / "observer.js"

# A *call* on the debugger API -- `chrome.debugger.attach(`, `chrome.debugger.sendCommand(` -- as
# opposed to a mention of it. The distinction is the whole point: prose about the API cannot match
# this, so `lib/session.js` is free to explain in its header why the driver it is handed happens to
# be the debugger, while a second thing that can actually attach still fails the test below.
DEBUGGER_CALL = re.compile(r"chrome\.debugger\.\w+\s*\(")


# --- the extension is made of the server's parts --------------------------------------------

def test_the_vendored_observer_is_the_servers_observer():
    """Byte for byte, because a copy that has been edited is a second observer.

    The whole promise of the table is that a ref means the same thing everywhere. The moment the
    extension's copy of the observer can drift, `e7` in the popup and `e7` in the server are two
    different elements that happen to share a name, and nothing would say so.
    """
    vendored = LIB / "observer.js"

    assert vendored.exists(), "chrome-extension/lib/observer.js is missing"
    assert vendored.read_bytes() == OBSERVER_SOURCE.read_bytes(), (
        "chrome-extension/lib/observer.js has drifted from "
        "jev_ultrafast_mcp/js/observer.js -- copy the file again instead of editing the copy")


def test_the_renderer_port_agrees_with_python():
    """Run the Node parity test, which compares the port against fixtures the real renderer wrote.

    Node is not always present -- it is not part of this package's dependencies and it is not on
    PATH in every sandbox -- so this skips with a message that says what to set rather than passing
    quietly. A test that turns into a no-op when its tool is missing is worse than no test.
    """
    node = _node()
    if node is None:
        pytest.skip(
            "node was not found; set JEVMCP_NODE=/path/to/node to run the renderer parity test "
            "(the extension itself does not need node, only this check does)")

    result = subprocess.run(
        [node, str(TEST / "render-parity.mjs")],
        capture_output=True, text=True, cwd=str(EXT), check=False,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_the_committed_fixtures_are_what_the_generator_produces():
    """The fixtures are the ground truth, so they have to be regenerated, not hand-edited.

    Without this, someone changes `observe.py`, the fixtures keep describing the old renderer, and
    the parity test cheerfully certifies that the port matches a version of Python that no longer
    exists.
    """
    generator = _load_fixture_generator()
    committed = (TEST / "fixtures.json").read_text(encoding="utf-8")
    produced = json.dumps(generator.build(), indent=2, ensure_ascii=False) + "\n"

    assert committed == produced, (
        "chrome-extension/test/fixtures.json is stale; regenerate it with "
        "`.venv/bin/python chrome-extension/test/make_fixtures.py`")


def test_every_flag_the_renderer_can_emit_is_exercised_by_a_fixture():
    """A generated fixture set still only covers the shapes someone thought of.

    Regenerating the fixtures removes the "author's belief" problem for each *case*, but which cases
    exist is still a choice, and a flag no case contains is a flag the parity harness never compares
    -- while staying green the whole time. That is exactly how the port came to have no `⋮` and no
    "(N ⋮ = menu trigger, hover before choosing)" header for months: `hoverable` was the one flag the
    fixture set had never contained, so there was nothing for the two sides to disagree about.

    So the required coverage is derived from the renderer rather than from memory: every flag
    `Element.render` can append has to appear in the committed fixtures. Adding a flag without a case
    for it now fails here, next to the fixtures it is about, instead of surviving until an unrelated
    change happens to compare the line it lives on.
    """
    source = (ROOT / "jev_ultrafast_mcp" / "observe.py").read_text(encoding="utf-8")
    # The renderer writes these as "\u2298" and friends, so the escape has to be decoded before it
    # can be looked for: comparing the raw escape against rendered output finds nothing, and a test
    # that finds nothing passes for the wrong reason.
    vocabulary = {
        codecs.decode(escape, "unicode_escape")
        for escape in re.findall(r'flags \+= "([^"]+)"', source)
    }
    assert len(vocabulary) >= 5, (
        "the renderer's flag vocabulary moved; this guard is looking in the wrong place")

    fixtures = (TEST / "fixtures.json").read_text(encoding="utf-8")
    uncovered = sorted(glyph for glyph in vocabulary if glyph not in fixtures)

    assert not uncovered, (
        f"no fixture exercises {uncovered}; add a case to make_fixtures.py that renders "
        f"them, or the port can drop them without the parity test noticing")


def test_the_popup_renders_with_the_port_rather_than_its_own_formatter():
    """The popup has to stay a window onto the table, not a second opinion about it.

    Its whole reason for existing is to show what the server would send. The moment it starts
    formatting rows itself -- a flag glyph here, a role code there -- it is showing something else,
    and the parity test cannot see that because it only exercises `lib/render.js`.
    """
    source = (EXT / "popup.js").read_text(encoding="utf-8")

    assert "from './lib/render.js'" in source, "the popup must render through the port"
    assert "renderObservation(" in source
    assert "'lib/observer.js'" in source, "the popup must inject the vendored observer"
    assert "readState" in source

    # Checked in both spellings: a glyph written as `'\u00bb'` in JavaScript is the same glyph and
    # would slip past a test that only looked for the character itself. `*` is left out on purpose --
    # it is an ordinary character and would match prose.
    for glyph, escape, what in (("\u2298", "\\u2298", "occluded"),
                                ("\u00bb", "\\u00bb", "off-screen"),
                                ("\u2297", "\\u2297", "disabled"),
                                ("\u22ee", "\\u22ee", "menu trigger"),
                                ("\u25be", "\\u25be", "expanded"),
                                ("\u2713", "\\u2713", "checked"),
                                ("\u00b7", "\\u00b7", "unchecked")):
        assert glyph not in source and escape not in source, (
            f"popup.js contains the {what} flag glyph; formatting belongs in lib/render.js")
    assert "ROLE_CODE" not in source


# --- the manifest ----------------------------------------------------------------------------

def test_the_manifest_is_a_loadable_mv3_extension():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 3
    assert manifest["name"] and manifest["description"]
    assert re.fullmatch(r"\d+(\.\d+){1,3}", manifest["version"]), manifest["version"]

    popup = EXT / manifest["action"]["default_popup"]
    assert popup.exists(), f"{popup} is the popup the manifest points at and it is not there"
    assert (EXT / "popup.html").read_text(encoding="utf-8").count("popup.js") == 1

    # Every icon the manifest names has to exist, and be the size the manifest claims, or Chrome
    # silently falls back and the toolbar shows a generic puzzle piece.
    for field in ("icons", "action.default_icon"):
        table = manifest["icons"] if field == "icons" else manifest["action"]["default_icon"]
        for size, relative in table.items():
            icon = EXT / relative
            assert icon.exists(), f"{relative} is referenced by {field} and is missing"
            assert _png_size(icon) == (int(size), int(size)), (
                f"{relative} is {_png_size(icon)}, not the {size}x{size} the manifest claims")


def test_the_manifest_asks_for_no_more_than_it_uses():
    """Four permissions, each one doing a job, and no host permissions at all.

    `activeTab` is the interesting one: it grants access to the tab you clicked the extension on and
    nothing else, which is why the extension can read a page without being able to read your
    browsing history. Requesting `<all_urls>` or `tabs` would be the easy way to build this and
    would make the extension something a reader has to trust rather than something they can check.

    `debugger` is the fourth and it was added on purpose, when replay became a thing this does. It is
    the one permission here that asks for something rather than limiting it: it is what buys real
    `Input.dispatchMouseEvent` instead of `isTrusted: false` synthetic events, and the price is the
    banner Chrome shows on the tab while a run is in progress. That is a trade worth making and
    worth saying out loud, which is why it is asserted here rather than left to the diff — this list
    is where a permission gets decided, so a change to it has to fail here first.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    source = (EXT / "popup.js").read_text(encoding="utf-8")
    worker = (EXT / "background.js").read_text(encoding="utf-8")

    assert set(manifest["permissions"]) == {"activeTab", "scripting", "storage", "debugger"}
    assert "host_permissions" not in manifest, (
        "the extension works on the clicked tab via activeTab and needs no host permissions")

    # Each permission has to be earning its place in the install prompt.
    assert "chrome.scripting.executeScript" in source, "scripting permission is unused"
    assert "chrome.storage.local" in source, "storage permission is unused"
    assert "chrome.tabs.query" in source, (
        "activeTab is what makes the active tab readable; the popup should be reading it")
    assert "chrome.debugger" in worker, "debugger permission is unused"


def test_the_debugger_is_held_in_exactly_one_place():
    """The service worker owns it, and nothing else calls it.

    Two holders of one attachment is how a run ends up detaching a debugger another run is using.
    It is also what keeps the browser surface small enough to audit: `lib/session.js` talks to an
    injected driver and never calls the API, which is what lets it be exercised without a browser at
    all.

    Naming it is not the same as calling it, and this test has to tell those apart rather than ban
    the word. `lib/session.js` opened with a paragraph explaining that the debugger is the only
    in-extension source of trusted input, and a substring search read that explanation as a second
    holder -- the kind of test that gets deleted rather than fixed. So it searches for the call.
    """
    callers = sorted(
        path.relative_to(EXT).as_posix()
        for path in list(EXT.glob("*.js")) + list(LIB.glob("*.js"))
        if DEBUGGER_CALL.search(path.read_text(encoding="utf-8")))

    assert callers == ["background.js"], (
        f"the debugger API is called from {callers}; it belongs only in background.js")


def test_the_worker_that_owns_the_debugger_is_the_one_the_manifest_loads():
    """A service worker the manifest does not point at is a file, not a component."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    background = manifest["background"]

    assert background["type"] == "module", (
        "background.js uses import, so the worker has to be declared as a module")
    assert (EXT / background["service_worker"]).exists(), (
        f"{background['service_worker']} is the worker the manifest points at and it is not there")


def test_the_extension_documents_itself_where_the_manifests_points():
    """`homepage_url` is a promise that a reader can find out what they installed."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["homepage_url"] == "https://github.com/jiawei686/jev-ultrafast-mcp"
    assert (EXT / "README.md").exists(), "chrome-extension/README.md is missing"


def test_the_pages_a_reader_lands_on_point_at_the_extension():
    """An extension nobody is told about is an extension nobody installs.

    The link has to be absolute, because these two READMEs are also the PyPI project page and PyPI
    resolves nothing relative -- a relative link there becomes a 404 on pypi.org rather than on
    GitHub, which is the failure mode `test_docs.py` exists to catch and this keeps it from coming
    back through a new file.
    """
    link = "https://github.com/jiawei686/jev-ultrafast-mcp/blob/main/chrome-extension/README.md"

    for name in ("README.md", "README.zh-CN.md", "llms.txt"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert link in text, f"{name} does not link to the extension's README"

    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "extension_check.py" in contributing, (
        "CONTRIBUTING.md's check list does not mention scripts/extension_check.py")


# --- the observer/renderer contract -----------------------------------------------------------

# What `readState()` puts on the wire. Frozen deliberately: adding a field to the observer is a
# decision about what the server knows, and this list is where that decision gets made.
OBSERVER_KEYS = {
    "url", "title", "w", "h", "text", "scroll", "reachable", "actions", "omitted",
    "offscreen", "overlays", "cross_frames", "cross_frame_srcs", "digest", "page_key",
}

# The two the server genuinely has no use for. `scroll.height` carries the same number as `h`, and
# nothing about what a ref points at depends on the viewport size.
UNUSED_BY_SERVER = {"w", "h"}

# A value for each field that has to change what the agent sees. `offscreen` and `overlays` are in
# here because they were once dropped between the observer and the table.
MUTATIONS: dict[str, object] = {
    "url": "https://example.com/elsewhere",
    "title": "A different page",
    "text": "Entirely different words on the page.",
    "scroll": {"y": 400, "height": 1240},
    "reachable": 9,
    "actions": [{"ref": "e1", "role": "button", "name": "Replaced"}],
    "omitted": 3,
    "offscreen": 2,
    "overlays": [{"role": "dialog", "name": "Sign in", "modal": True}],
    "cross_frames": 3,
    "cross_frame_srcs": ["https://b.example/frame"],
}

BASE_ACTION = {
    "ref": "e1", "role": "button", "name": "Search", "label": "Search", "value": "",
    "editable": False, "occluded": False, "inViewport": True, "checked": None, "expanded": None,
    "current": None, "options": [], "opts_total": 0, "secret": False, "context": "",
    "accept": None, "multiple": False, "hoverable": False, "disabled": False,
}

# The observer's per-action contract, and the `Element` field each key has to reach. This exists
# because the top-level contract is not the one that broke: `inViewport` lives here, and it was the
# field `Element.from_raw` never read.
ACTION_TO_ELEMENT = {
    "ref": "ref", "role": "role", "name": "name", "label": "label", "value": "value",
    "editable": "editable", "occluded": "occluded", "inViewport": "in_viewport",
    "checked": "checked", "expanded": "expanded", "current": "current", "options": "options",
    "opts_total": "opts_total", "secret": "secret", "accept": "accept", "multiple": "multiple",
    "hoverable": "hoverable", "disabled": "disabled",
}

# The observer's own bookkeeping. `node`, `rank` and `order` are how it builds and sorts the list
# and never reach the table; `scope` becomes `context`, and only where a label repeats.
ACTION_BOOKKEEPING = {"node", "rank", "order", "scope"}

# A value for each key that differs from `BASE_ACTION`, so a field that is read into nothing shows
# up as "no change" rather than as a passing test.
ACTION_MUTATIONS: dict[str, object] = {
    "ref": "e99", "role": "textbox", "name": "A different name", "label": "A different label",
    "value": "a value", "editable": True, "occluded": True, "inViewport": False,
    "checked": True, "expanded": "true", "current": "2 adults",
    "options": [{"ref": "e1:1", "label": "One", "value": "1", "selected": True}],
    "opts_total": 5, "secret": True, "accept": ".pdf", "multiple": True, "hoverable": True,
    "disabled": True,
}


def _raw() -> dict:
    return {
        "url": "https://example.com/flights", "title": "Flights",
        "text": "Search flights -- one way or round trip.",
        "scroll": {"y": 0, "height": 1240},
        "reachable": 2,
        "actions": [dict(BASE_ACTION), {**BASE_ACTION, "ref": "e2", "name": "Reset"}],
        "omitted": 0, "offscreen": 0,
        "overlays": [],
        "cross_frames": 1, "cross_frame_srcs": ["https://a.example/frame"],
        "digest": "d", "page_key": "k",
    }


def _render(raw: dict) -> str:
    """The table for a raw payload, at the sequence number the server would use."""
    observation = Observation.from_raw(raw)
    observation.sequence = 1
    return observation.render()


def test_the_observer_contract_is_frozen():
    """Both contracts: the payload `readState()` returns, and one action inside it.

    Parsed out of the observer rather than restated here, because a restated contract is just a
    comment with syntax. Adding a field to either literal has to be a decision about what the
    server knows, and these two lists are where that decision gets written down.
    """
    assert _object_keys("return JSON.stringify({") == OBSERVER_KEYS
    assert _object_keys("built.push({") == set(ACTION_TO_ELEMENT) | ACTION_BOOKKEEPING


def test_every_action_field_the_observer_emits_reaches_the_element():
    """The guard for the field that was actually dropped.

    `Element.from_raw` never read `inViewport`, so `in_viewport` was permanently `True`. The header
    still counted those elements in `offscreen` and still printed "» = will scroll on act", but no
    row ever carried a `»` -- the flag was unreachable code, and a reader had no way to tell which
    of the thirteen controls the note was about.
    """
    baseline = Element.from_raw(BASE_ACTION)

    for key, attribute in ACTION_TO_ELEMENT.items():
        mutated = Element.from_raw({**BASE_ACTION, key: ACTION_MUTATIONS[key]})
        assert getattr(mutated, attribute) != getattr(baseline, attribute), (
            f"the observer emits {key!r} on every action and `Element.{attribute}` never changes; "
            "the field is being dropped on the way into the table")


def test_the_flags_on_an_element_come_from_the_observer():
    """Every glyph the header explains, rendered from the field that means it."""
    flags = {
        "*": {"editable": True},
        "\u2298": {"occluded": True},
        "\u00bb": {"inViewport": False},
        "\u25be": {"expanded": "true"},
        "\u2713": {"checked": True},
        "\u00b7": {"checked": False},
        "\u22ee": {"hoverable": True},
        "\u2297": {"disabled": True},
    }

    for glyph, override in flags.items():
        line = Element.from_raw({**BASE_ACTION, **override}).render()
        assert glyph in line, f"{glyph!r} is documented in the header and {override} does not draw it"

    # `⊘` wins over `»`: an element that is both covered and off-screen is reported as covered,
    # because that is the thing the agent has to deal with first.
    both = Element.from_raw({**BASE_ACTION, "occluded": True, "inViewport": False}).render()
    assert "\u2298" in both and "\u00bb" not in both


def test_every_field_the_observer_emits_changes_what_the_agent_sees():
    """The regression guard for the bug this extension found twice.

    `offscreen` was emitted by the observer, counted in the header, and never read by
    `Observation.from_raw`. The header still promised "(offscreen N, » = will scroll on act)" and
    printed a zero. Nothing failed, because nothing was checking that a field the observer sends
    survives the trip into the table.
    """
    baseline = _render(_raw())

    for key, value in MUTATIONS.items():
        raw = _raw()
        raw[key] = value
        assert _render(raw) != baseline, (
            f"the observer emits {key!r} and changing it changes nothing in the table")


def test_the_two_fields_the_server_does_not_need_are_deliberately_unused():
    """`w` and `h` are read into nothing on purpose, so that stays a decision rather than a bug."""
    baseline = _render(_raw())

    for key in sorted(UNUSED_BY_SERVER):
        raw = _raw()
        raw[key] = 4242
        assert _render(raw) == baseline, (
            f"{key!r} is in the unused set and something now renders it; "
            "move it into MUTATIONS and out of UNUSED_BY_SERVER")


def test_the_fields_that_are_read_but_never_rendered_stay_readable():
    """`digest` and `page_key` never appear in the table, but a client can ask for them."""
    observation = Observation.from_raw({**_raw(), "digest": "sentinel", "page_key": "sentinel-key"})

    assert observation.digest == str(hash("sentinel"))
    assert observation.page_key == "sentinel-key"
    assert "sentinel-key" not in observation.render()


def test_the_end_to_end_check_invokes_the_extension_rather_than_patching_it():
    """The grant is the interesting half, so the check must not quietly stop exercising it.

    `activeTab` is only granted when the extension is *invoked*, and `Extensions.triggerAction` is
    the browser-level way to invoke it. The check used to run its comparison against a copy of the
    extension carrying one added `host_permissions`, because a popup opened as a background tab has
    no grant and nothing available at the time could produce one -- which meant the shipped manifest
    was never the thing that read a page. That is a silent downgrade if it comes back, so the
    *decision* is driven here with a stub rather than grepped for: the first call must be the
    invocation, and the copy must only be reached when the browser cannot do it.
    """
    check = _load_script("extension_check")
    invoked, why = check.invoke_action(_StubCdp(), "an-id", "a-tab-target")

    assert invoked and why == ""
    assert _StubCdp.last_method == "Extensions.triggerAction", (
        "the check must invoke the extension; without it the activeTab grant goes untested")
    assert _StubCdp.last_params["targetId"] == "a-tab-target", (
        "the invocation must name the tab, which is the only kind of target it accepts")

    # A browser without the command is the one case that may fall back.
    refused, reason = check.invoke_action(_StubCdp(fail=True), "an-id", "a-tab-target")
    assert not refused and "unavailable" in reason, reason


def test_the_fallback_announces_that_it_skips_the_grant():
    """A run that lost the `activeTab` coverage must not look like one that had it."""
    script = (ROOT / "scripts" / "extension_check.py").read_text(encoding="utf-8")

    assert "no grant tested" in script, "the fallback must announce that it skips the grant"
    assert "invoke_action(" in script, "the primary path must go through the invocation"


# --- what the run left attached ---------------------------------------------------------------


def test_the_debugger_list_is_polled_rather_than_read_once():
    """The browser's list is not stable at the moment the run ends.

    It used to be compared as a *count*, read once. The count reads 2 or 3 depending on whether the
    extension's service worker happens to be alive, so a single read measured the worker's lifecycle
    and reported it as a leak. Polling is the repair, and comparing names is what makes a failure
    actionable -- the earlier repair polled the count and the same failure came back reading
    "2 before the run, 3 after", which is a number with no next step in it.
    """
    check = _load_script("extension_check")
    reads = iter([{"a", "b", "c"}, {"a", "b", "c"}, {"a", "b"}])

    assert check.wait_until_nothing_new(lambda: next(reads), {"a", "b"}, timeout=1.0) == {"a", "b"}


def test_something_that_stays_attached_still_fails_and_can_be_named():
    """The poll must not soften the assertion -- a genuine leak never clears.

    This is the test that says waiting is safe: the timeout only decides how long to wait, and a
    target still attached at the end is still a failure. The last value is returned rather than
    `None` so the caller can name what was still there; `None` would read as a broken check rather
    than as a leak.
    """
    check = _load_script("extension_check")
    seen = []
    leaked = {"a", "b", "page http://example.test/ EXTRA"}

    def read():
        seen.append(1)
        return leaked

    assert check.wait_until_nothing_new(read, {"a", "b"}, timeout=0.2, interval=0.01) == leaked
    assert len(seen) > 1, "it gave up without looking a second time"


def test_nothing_extra_is_not_a_reason_to_keep_polling():
    """Why this cannot be `wait_until`: the value being waited for is the empty set.

    `wait_until` returns the first *truthy* value, and `set()` is falsy -- so it would poll straight
    past the one answer that means "this run attached nothing new", which is the answer the check
    exists to see.
    """
    check = _load_script("extension_check")
    calls = []

    def read():
        calls.append(1)
        return {"a"}

    assert check.wait_until_nothing_new(read, {"a"}, timeout=1.0) == {"a"}
    assert len(calls) == 1, "it kept polling past the answer it was waiting for"


def test_a_target_going_away_is_not_a_failure():
    """A subset test, not an equality test.

    A target that disappears during the poll -- the worker going dormant, most likely -- is not this
    check's business. Waiting for exact equality with the earlier list would time out and then fail
    on something that is not a leak, which is how a check starts being ignored.
    """
    check = _load_script("extension_check")

    assert check.wait_until_nothing_new(lambda: {"a"}, {"a", "b"}, timeout=1.0) == {"a"}


# --- helpers -----------------------------------------------------------------------------------


class _StubCdp:
    """Records what it was asked to do; raises on the command a Chrome without it would lack."""

    last_method = ""
    last_params: dict = {}

    def __init__(self, *, fail: bool = False):
        self.fail = fail

    def call(self, method, **params):
        type(self).last_method = method
        type(self).last_params = params
        if method == "Extensions.triggerAction" and self.fail:
            raise RuntimeError("'Extensions.triggerAction' wasn't found")
        return {}


def _node() -> str | None:
    override = os.environ.get("JEVMCP_NODE")
    if override:
        return override if Path(override).exists() else None
    for name in ("node", "nodejs"):
        found = shutil.which(name)
        if found:
            return found
    # A managed runtime outside PATH, which is how this sandbox is laid out.
    for candidate in sorted(glob.glob(os.path.expanduser(
            "~/.workbuddy-ai/binaries/node/versions/*/bin/node")), reverse=True):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _load_fixture_generator():
    path = TEST / "make_fixtures.py"
    spec = importlib.util.spec_from_file_location("_jev_extension_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_script(name: str):
    """`scripts/` is not a package, so load a check the way a runner would rather than importing it."""
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_jev_script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _object_keys(anchor: str) -> set[str]:
    """The keys of the object literal that follows `anchor`, with comments removed.

    Walked rather than regexed because both literals contain a nested object (`scroll`, `options`)
    and calls with commas inside their arguments, so splitting on commas alone would invent keys
    that are not there. Comments are stripped first because one of them contains a colon, and a
    colon is what this looks for.
    """
    source = re.sub(r"//[^\n]*", "", OBSERVER_SOURCE.read_text(encoding="utf-8"))
    index = source.index(anchor) + len(anchor)

    keys: set[str] = set()
    chunk = ""
    depth = 1
    while index < len(source) and depth:
        character = source[index]
        index += 1
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                break
        elif character == "," and depth == 1:
            _add_key(keys, chunk)
            chunk = ""
            continue
        chunk += character

    _add_key(keys, chunk)
    return keys


def _add_key(keys: set[str], chunk: str) -> None:
    chunk = chunk.strip()
    if not chunk:
        return
    key = chunk.split(":", 1)[0].strip() if ":" in chunk else chunk
    if re.fullmatch(r"[A-Za-z_$][\w$]*", key):
        keys.add(key)


def _png_size(path: Path) -> tuple[int, int]:
    """Read a PNG's dimensions from its IHDR, so this test needs no image library."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    assert data[12:16] == b"IHDR", f"{path.name} has no IHDR chunk"
    return struct.unpack(">II", data[16:24])
