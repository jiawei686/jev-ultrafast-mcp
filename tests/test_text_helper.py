"""The text helper answers a fixed shape, and a wrong shape must say so.

Jev only chooses; it never writes a field value. Filling one is delegated to a
chat model, which must answer exactly `{"text": "..."}`. When it answers
something else the run stops with nothing typed -- correct, since typing a
guess would be worse -- but "no usable value" alone cannot be acted on: a helper
that answered in the wrong shape and a helper that answered nothing at all need
different fixes, and only the answer tells them apart.

Live, that error was the only signal available for a run that failed
intermittently, which is why the answer is now carried inside it.
"""

from __future__ import annotations

import dataclasses

import pytest

from jev_ultrafast_mcp import policy
from jev_ultrafast_mcp.config import Config
from jev_ultrafast_mcp.observe import Element, Observation


def _fixtures():
    element = Element(ref="e1", role="searchbox", name="Search Wikipedia", editable=True)
    observation = Observation(
        url="https://example.test/",
        title="Example",
        text="Example page",
        elements=[element],
        digest="d",
        text_digest="t",
        page_key="k",
        scroll={"y": 0},
        reachable=1,
        omitted=0,
        overlays=[],
        cross_frames=0,
        cross_frame_srcs=[],
    )
    cfg = dataclasses.replace(Config.from_env(), text_model_key="test-key", text_model="test-model")
    return cfg, element, observation


def _answer(content: str):
    return {"choices": [{"message": {"content": content}}]}


@pytest.mark.parametrize(
    "content",
    [
        '{"value": "Python"}',          # right idea, wrong key
        '{"text": "a", "why": "b"}',    # right key, extra company
        '{"text": "   "}',              # right shape, nothing in it
        "not json at all",                          # a refusal, a preamble, markdown
        '{"text": "' + "x" * 2001 + '"}',           # right shape, an essay
    ],
)
def test_a_rejected_answer_is_reported_back(monkeypatch, content):
    cfg, element, observation = _fixtures()
    monkeypatch.setattr(policy, "_post", lambda url, key, body: _answer(content))

    with pytest.raises(policy.TurboUnavailable) as caught:
        policy.text_for(cfg, "open the Python article", element, observation, [])

    message = str(caught.value)
    assert "nothing typed" in message
    assert content[:60] in message, "the error must carry the answer, not just the verdict"


def test_a_well_formed_answer_is_used_verbatim(monkeypatch):
    cfg, element, observation = _fixtures()
    monkeypatch.setattr(policy, "_post", lambda url, key, body: _answer('{"text": "Python"}'))

    assert policy.text_for(cfg, "open the Python article", element, observation, []) == "Python"


def test_an_answer_with_no_message_at_all_does_not_crash(monkeypatch):
    """A provider that returns a shape we did not anticipate is a failed fill, not a traceback."""
    cfg, element, observation = _fixtures()
    monkeypatch.setattr(policy, "_post", lambda url, key, body: {"id": "gen-1", "choices": []})

    with pytest.raises(policy.TurboUnavailable):
        policy.text_for(cfg, "open the Python article", element, observation, [])
