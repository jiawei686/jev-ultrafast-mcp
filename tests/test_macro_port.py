"""Tests for the macro resolver the extension replays with.

Replay is the one part of this project where being wrong is silent. A live agent that
picks the wrong element usually gets corrected by the next observation, and a human
reads the transcript. A replay that picks the wrong element has already submitted the
form, and there is nothing in the transcript to read because no model was ever
consulted.

That is why the resolver is not reimplemented in JavaScript from a description of what
it does: `lib/macro.js` is a port of `macros.py`, and `test/macro-parity.mjs` holds it
to fixtures the real Python resolver wrote -- including the exact sentence each refusal
carried. This file drives that check and holds the fixture file itself to its
generator, so the two cannot drift.

Three things are asserted here that the parity run alone would not catch:

- the fixtures still contain *both* kinds of refusal, so a future edit cannot quietly
  turn the suite into a comparison of successful resolutions only;
- the CJK limitation is recorded as a fixture rather than described in a comment, so
  a port that "fixes" it by accident fails rather than silently disagreeing with the
  server it is supposed to be a copy of;
- the three constants match Python, because a margin or a threshold that drifted would
  agree with the server on every fixture whose scores are far apart.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jev_ultrafast_mcp import macros  # noqa: E402

EXT = ROOT / "chrome-extension"
TEST = EXT / "test"
FIXTURES = TEST / "macro-fixtures.json"

WEAK_MATCH = "did not match anything above"
AMBIGUOUS = "is ambiguous between"


def test_the_resolver_port_agrees_with_python():
    """Run the Node parity check: the port against fixtures the real resolver wrote.

    Node is not a dependency of this package and is not on PATH in every sandbox, so
    this skips with a message that says what to set rather than passing quietly. A
    test that becomes a no-op when its tool is missing is worse than no test -- the
    whole point is that someone notices the day it stops running.
    """
    node = _node()
    if node is None:
        pytest.skip(
            "node was not found; set JEVMCP_NODE=/path/to/node to run the resolver "
            "parity test (the extension itself does not need node, only this check does)")

    result = subprocess.run(
        [node, str(TEST / "macro-parity.mjs")],
        capture_output=True, text=True, cwd=str(EXT), check=False,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_the_committed_macro_fixtures_are_what_the_generator_produces():
    """The fixtures are ground truth, so they have to be regenerated, not hand-edited.

    This one earned its place during development: a case was added to the generator
    and the fixtures were not regenerated, which made a mutation survive that should
    have been caught. "The suite is green" meant "the suite is comparing against last
    week's file" until this assertion existed.
    """
    generator = _load(TEST / "make_macro_fixtures.py", "_jev_macro_fixtures")
    committed = FIXTURES.read_text(encoding="utf-8")
    produced = json.dumps(generator.build(), indent=2, ensure_ascii=False) + "\n"

    assert committed == produced, (
        "chrome-extension/test/macro-fixtures.json is stale; regenerate it with "
        "`.venv/bin/python chrome-extension/test/make_macro_fixtures.py`")


def test_the_two_languages_agree_on_the_three_limits():
    """A drifted threshold is the least visible way for a port to be wrong.

    Every fixture whose scores are far apart agrees whatever the margin is, so the
    cases cannot be the only guard: the numbers themselves are compared against the
    server's, in Python here and in JavaScript over there.
    """
    limits = json.loads(FIXTURES.read_text(encoding="utf-8"))["limits"]

    assert limits["threshold"] == macros.DEFAULT_THRESHOLD
    assert limits["ambiguity_margin"] == macros.AMBIGUITY_MARGIN
    assert limits["context_weight"] == macros.CONTEXT_WEIGHT
    # The relationship the comment in `macros.py` insists on: a tie can only be broken
    # by context if context is worth more than the margin that would refuse it.
    assert macros.CONTEXT_WEIGHT > macros.AMBIGUITY_MARGIN


def test_the_fixtures_still_contain_both_kinds_of_refusal():
    """A resolver that never refuses is a resolver that guesses.

    Both refusals are features, and the fixtures are the only place their wording is
    pinned. Trim the refusals out to make a change pass, and the suite keeps reporting
    success while the guarantee it stands for is gone.
    """
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]
    errors = [case["expected"]["error"] for case in cases if "error" in case["expected"]]

    assert sum(WEAK_MATCH in message for message in errors) >= 3, (
        "the weak-match refusal has lost its fixtures")
    assert sum(AMBIGUOUS in message for message in errors) >= 3, (
        "the ambiguity refusal has lost its fixtures")
    assert any("best=" in message and "best=none" not in message for message in errors), (
        "no fixture exercises the refusal that reports candidates, so the five-entry "
        "best list is unverified")


def test_the_cjk_limitation_is_a_fixture_rather_than_a_comment():
    """The port has to be as blind as the original, and the blindness has to be recorded.

    `_tokens` splits on `[^a-z0-9]+`, which matches every CJK character, so a Chinese
    label tokenises to nothing and both the name-overlap branch and the context bonus
    stop working for it. An exact or substring label still resolves, which is why this
    is easy to miss; anything reworded does not, and a repeated Chinese label cannot be
    disambiguated at all.

    The fixtures say so, in cases rather than prose, because the fix belongs in both
    languages at once -- and a port that quietly improves on the original would be a
    second resolver, which is the thing this whole file exists to prevent.
    """
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]
    cjk = [case for case in cases if case["name"].startswith("cjk-")]

    assert len(cjk) >= 3, "the CJK fixtures have been removed rather than fixed"
    assert any("error" in case["expected"] for case in cjk), (
        "no CJK fixture refuses, so the limitation is no longer pinned")
    assert any("error" not in case["expected"] for case in cjk), (
        "no CJK fixture resolves, so the cases no longer separate what works from "
        "what does not")
    # The control that makes the point: the same markup with English labels works.
    assert any(case["name"] == "the-same-tie-in-english-resolves" for case in cases)


def test_the_resolver_ships_inside_the_extension_it_belongs_to():
    """A replay runs in the browser, so the resolver has to be in the bundle.

    Cheap, but it is the difference between a port that is wired up and a port that is
    a well-tested file nobody calls.
    """
    source = (EXT / "lib" / "macro.js").read_text(encoding="utf-8")

    assert "export function resolve(" in source
    assert "'../lib/macro.js'" in (TEST / "macro-parity.mjs").read_text(encoding="utf-8"), (
        "the parity check must import the shipped resolver, not a copy of it")
    assert not (EXT / "lib" / "macros.js").exists(), (
        "there is one resolver; a second file with a similar name is how this drifts")


# --- helpers -----------------------------------------------------------------------------------


def _node() -> str | None:
    """Reuse the lookup `test_extension.py` already needed.

    Which node a machine has, and where a managed one hides, is a fact about the
    environment rather than about macros, and the render port had to answer it first.
    """
    return _load(Path(__file__).with_name("test_extension.py"), "_jev_extension_tests")._node()


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
