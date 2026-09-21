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


def test_one_key_covering_both_is_a_configuration_and_not_a_fallback(monkeypatch):
    """The decision model may borrow OPENROUTER_API_KEY; the text helper must not.

    `_turbo_backend` falls back to `OPENROUTER_API_KEY` because every decisions route speaks the
    same contract. The text helper does not, and borrowing would be worse than refusing: it posts
    to `TEXT_MODEL_BASE_URL`, which is DeepSeek by default, and an OpenRouter key sent there earns a
    401 whose message is about DeepSeek. A run diagnosed from the wrong provider's error is a run
    nobody diagnoses. So the loader leaves it unset and the helper refuses by name.

    Covering both with one key is therefore a configuration -- point `TEXT_MODEL_BASE_URL` and
    `TEXT_MODEL` at that provider too -- not something the loader does for you. This is pinned
    because adding the fallback looks like a kindness and is not one.
    """
    for var in ("TEXT_MODEL_API_KEY", "TEXT_MODEL_BASE_URL", "TEXT_MODEL", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://openrouter.ai/api/alpha/decisions")

    cfg = Config.from_env()
    _, element, observation = _fixtures()

    assert cfg.typesafe_key == "openrouter-key", "the decision model does borrow it"
    assert cfg.text_model_key is None, "the text helper must not borrow a key for a different API"
    assert cfg.text_model_base == "https://api.deepseek.com/v1", (
        "the default base is DeepSeek, which is exactly why the key cannot be borrowed")

    with pytest.raises(policy.TurboUnavailable) as caught:
        policy.text_for(cfg, "open the Python article", element, observation, [])

    assert "TEXT_MODEL_API_KEY" in str(caught.value), (
        "the refusal has to name the variable to set, or it is a dead end")
