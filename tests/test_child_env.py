"""The environment the check scripts hand their child.

`mcp_check.py` and `live_check.py` each spawn the server as a subprocess, and each inherits the real
environment so that a browser in an unusual place -- or a proxy -- still reaches the child.
Everything configuring the *model path* is stripped on the way in, because those checks drive the
surface that needs no key, and a key the developer happens to have exported must not change what
they are testing.

That strip was two hand-written lists, and adding a second provider left `OPENROUTER_API_KEY` off
both of them -- so the documented way to configure the OpenRouter route also leaked a decision key
into a child that was supposed to be keyless. These tests pin the guarantee rather than the lists.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from jev_ultrafast_mcp import config as config_mod
from jev_ultrafast_mcp.config import model_env_vars

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scripts():
    return {"mcp_check": _load("mcp_check"), "live_check": _load("live_check")}


def _child_env(scripts, which: str) -> dict[str, str]:
    state = Path("/tmp/jev-child-env-test")
    if which == "mcp_check":
        return scripts["mcp_check"]._child_env(state)
    return scripts["live_check"].child_env(state, True)


@pytest.mark.parametrize("which", ["mcp_check", "live_check"])
def test_the_child_cannot_reach_a_decision_model(scripts, monkeypatch, which):
    for name in model_env_vars():
        monkeypatch.setenv(name, "leaked")

    env = _child_env(scripts, which)

    survivors = [name for name in model_env_vars() if name in env]
    assert not survivors, f"{which} hands its child {survivors}"


@pytest.mark.parametrize("which", ["mcp_check", "live_check"])
def test_the_child_still_inherits_the_environment_it_needs(scripts, monkeypatch, which):
    """Stripping the model path must not quietly become "strip everything".

    Both docstrings promise the real environment reaches the child, and the reason is concrete: a
    Chrome in a non-default location, or a proxy, is a working setup this check would otherwise
    report as a failure of the server.
    """
    monkeypatch.setenv("JEVMCP_CHROME", "/opt/chrome")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")

    env = _child_env(scripts, which)

    assert env["JEVMCP_CHROME"] == "/opt/chrome"
    assert env["HTTPS_PROXY"] == "http://proxy.internal:3128"


def test_a_provider_added_later_is_stripped_without_being_named(monkeypatch):
    """The point of deriving the list, simulated rather than described.

    "It is derived" is exactly the kind of claim that stops being true in a later edit -- replace
    the comprehension with a literal and every other test in this file still passes. So the third
    provider is added here and its key is looked for, which fails the moment the derivation goes.
    """
    monkeypatch.setitem(config_mod.PROVIDERS, "third", {
        "endpoint": "https://third.example/decisions",
        "key_vars": ("THIRD_API_KEY",),
        "chat_base": None,
    })

    assert "THIRD_API_KEY" in model_env_vars()
