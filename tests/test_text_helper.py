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


def _page():
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
    return element, observation


def _fixtures():
    cfg = dataclasses.replace(Config.from_env(), text_model_key="test-key", text_model="test-model")
    element, observation = _page()
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


def test_a_borrowed_key_always_comes_with_its_own_provider_s_base_url(monkeypatch):
    """The helper may borrow the decision model's key -- but never the key alone.

    An earlier version of this test pinned the opposite: that the helper must never borrow a
    key at all. The reasoning was sound, but it was not about borrowing -- it was about the
    *mismatch*. The helper posts to `TEXT_MODEL_BASE_URL`, which defaults to DeepSeek, so an
    OpenRouter key sent there earned a 401 naming the wrong company, and a run diagnosed from
    the wrong provider's error is a run nobody diagnoses.

    The invariant worth pinning is therefore not "never borrow" but "the key and the base URL
    come from the same provider". That is what makes the borrow safe, and it is what this
    holds to: a borrowed key always arrives with the base URL of the API that issued it.
    """
    for var in ("TEXT_MODEL_API_KEY", "TEXT_MODEL_BASE_URL", "TEXT_MODEL", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("JEV_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")

    cfg = Config.from_env()

    assert cfg.typesafe_key == "openrouter-key"
    assert cfg.text_model_key == "openrouter-key", "the helper may reuse the same key"
    assert cfg.text_model_base == "https://openrouter.ai/api/v1", (
        "and it must be OpenRouter's own base URL, or the key is being sent to a stranger")
    assert cfg.text_model is None, (
        "the slug is not inherited: `deepseek-chat` is not an OpenRouter slug, and guessing "
        "one would turn a clear refusal into a confusing 400")


def test_jev_s_own_api_has_no_chat_route_to_inherit(monkeypatch):
    """Jev chooses; it does not write prose, so there is nothing here for the helper to borrow.

    That is a fact about the provider, and it lives in `PROVIDERS[provider]["chat_base"]`
    rather than in a docstring -- which is what lets the refusal say *why* instead of naming a
    key that would not have helped.
    """
    for var in ("TEXT_MODEL_API_KEY", "TEXT_MODEL_BASE_URL", "TEXT_MODEL", "OPENROUTER_API_KEY",
                "TYPESAFE_BASE_URL", "JEV_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-key")

    cfg = Config.from_env()
    element, observation = _page()

    assert cfg.provider == "typesafe"
    assert cfg.text_model_key is None, "Jev's own API has no chat route to borrow from"

    with pytest.raises(policy.TurboUnavailable) as caught:
        policy.text_for(cfg, "open the Python article", element, observation, [])

    message = str(caught.value)
    assert "JEV_PROVIDER=openrouter" in message, (
        "the refusal must offer the route that would work, not only the key that would not")
    assert "generate" in message, (
        "and it must say why this provider cannot serve it, or a provider that does not do "
        "this reads as a variable somebody forgot to set")


def test_inheriting_a_route_still_requires_naming_a_model(monkeypatch):
    """A borrowed base URL is not a borrowed model slug, and guessing one is worse than asking."""
    for var in ("TEXT_MODEL_API_KEY", "TEXT_MODEL_BASE_URL", "TEXT_MODEL", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("JEV_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")

    cfg = Config.from_env()
    element, observation = _page()

    with pytest.raises(policy.TurboUnavailable) as caught:
        policy.text_for(cfg, "open the Python article", element, observation, [])

    assert "TEXT_MODEL" in str(caught.value), (
        "the refusal has to name the variable that is actually missing")


def test_an_explicit_text_configuration_is_untouched(monkeypatch):
    """The long-standing route must not move under anyone already relying on it."""
    for var in ("TEXT_MODEL_BASE_URL", "TEXT_MODEL", "JEV_PROVIDER", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "deepseek-key")

    cfg = Config.from_env()

    assert cfg.text_model_base == "https://api.deepseek.com/v1", (
        "setting only a key keeps the DeepSeek base it has always had")
    assert cfg.text_model == "deepseek-chat"
    assert cfg.text_model_key == "deepseek-key"
