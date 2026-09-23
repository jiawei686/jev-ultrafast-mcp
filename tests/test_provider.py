"""Which API pays for the decision model, and how the two routes are kept apart.

Jev is reachable through its own API and through OpenRouter. Both are supported here, and the
point of these tests is that neither is "the real one" with the other bolted on: the provider is
named, the key follows the name, and a configuration that mixes the two is not allowed to send
one API's key to another API's host -- because the 401 that produces reads as a bad key rather
than as a bad configuration, and a run diagnosed from the wrong provider's error is a run
nobody diagnoses.
"""

from __future__ import annotations

import pytest

from jev_ultrafast_mcp import config as config_mod
from jev_ultrafast_mcp import policy
from jev_ultrafast_mcp.config import Config

PROVIDER_VARS = (
    "JEV_PROVIDER",
    "TYPESAFE_BASE_URL",
    "TYPESAFE_API_KEY",
    "OPENROUTER_API_KEY",
    "TEXT_MODEL_API_KEY",
    "TEXT_MODEL_BASE_URL",
    "TEXT_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every case from "nothing configured", so a host's real key cannot decide the test."""
    for name in PROVIDER_VARS:
        monkeypatch.delenv(name, raising=False)


def test_jev_s_own_api_is_the_default():
    cfg = Config.from_env()

    assert cfg.provider == "typesafe"
    assert cfg.typesafe_endpoint == "https://api.typesafe.ai/v1/systemone"


def test_the_provider_can_be_named_outright(monkeypatch):
    monkeypatch.setenv("JEV_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    cfg = Config.from_env()

    assert cfg.provider == "openrouter"
    assert cfg.typesafe_endpoint == "https://openrouter.ai/api/alpha/decisions"
    assert cfg.typesafe_key == "or-key"


def test_the_old_variable_still_selects_openrouter(monkeypatch):
    """Every configuration written before the provider had a name keeps working unchanged."""
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://openrouter.ai/api/alpha/decisions")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    cfg = Config.from_env()

    assert cfg.provider == "openrouter", "the URL is as good as the word"
    assert cfg.typesafe_endpoint == "https://openrouter.ai/api/alpha/decisions"
    assert cfg.typesafe_key == "or-key"


def test_a_url_naming_another_provider_does_not_win(monkeypatch):
    """The named provider decides, because the alternative is a key sent to a stranger.

    Setting `JEV_PROVIDER=typesafe` while a stale `TYPESAFE_BASE_URL` still points at OpenRouter
    is a contradiction the loader can see. Honouring the URL would authenticate TypeSafe's key
    against OpenRouter's host and report the resulting 401 as though the key were bad.
    """
    monkeypatch.setenv("JEV_PROVIDER", "typesafe")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://openrouter.ai/api/alpha/decisions")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")

    cfg = Config.from_env()

    assert cfg.provider == "typesafe"
    assert cfg.typesafe_endpoint == "https://api.typesafe.ai/v1/systemone", (
        "the explicit provider wins over a URL naming a different one")


def test_a_custom_endpoint_is_still_honoured(monkeypatch):
    """A URL naming no provider is a proxy or a staging host, not a contradiction."""
    monkeypatch.setenv("JEV_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://gateway.internal/decisions")

    cfg = Config.from_env()

    assert cfg.provider == "openrouter"
    assert cfg.typesafe_endpoint == "https://gateway.internal/decisions"


def test_an_unknown_provider_is_reported_and_not_fatal(monkeypatch):
    """A typo must not take the browser surface down with it -- and must not be silent either.

    `Config.from_env()` runs at import, so raising here would make one misspelled optional
    variable fatal to `browser_open` as well, which needs no key at all. It is reported through
    `provider_note()` instead, where `browser_doctor` picks it up.
    """
    monkeypatch.setenv("JEV_PROVIDER", "openroutr")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    cfg = Config.from_env()
    note = config_mod.provider_note()

    assert cfg.provider == "typesafe", "an unrecognised name leaves the inference in charge"
    assert note is not None, "and it is reported rather than swallowed"
    assert "openroutr" in note, "the note quotes what was actually written"
    assert "typesafe" in note and "openrouter" in note, "and lists what is accepted"


def test_a_provider_is_paid_for_with_its_own_key_and_no_other(monkeypatch):
    """The second column of the table means "this provider's key", not "some key".

    An earlier revision let the OpenRouter route fall back to `TYPESAFE_API_KEY`, which reads as
    a kindness and is not one: a key issued by one API does not authenticate against another's
    host, so the fallback could only ever convert a clear "no key for this provider" into a 401
    naming a company the caller never chose. Nothing working depended on it, because it never
    worked. Naming the provider and holding the other one's key is now a missing key, and the
    refusal says which variable to set.
    """
    monkeypatch.setenv("JEV_PROVIDER", "openrouter")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")

    cfg = Config.from_env()

    assert cfg.provider == "openrouter"
    assert cfg.typesafe_key is None, "the other provider's key is not this provider's key"
    with pytest.raises(policy.TurboUnavailable) as caught:
        policy.choose(cfg, None, "goal", [])
    assert "OPENROUTER_API_KEY" in str(caught.value), "and the refusal names the one to set"


def test_a_recognised_provider_raises_no_note(monkeypatch):
    monkeypatch.setenv("JEV_PROVIDER", "openrouter")

    assert config_mod.provider_note() is None
