"""Runs the end-to-end scenario under pytest.

The scenario needs a real browser and takes ~10s, so it is skipped when no
Chromium-family binary is present. It is the same code path the MCP tools use.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jev_ultrafast_mcp.config import find_chrome  # noqa: E402


def _chrome_available() -> bool:
    try:
        find_chrome(None)
        return True
    except RuntimeError:
        return False


@pytest.mark.skipif(not _chrome_available(), reason="no Chromium-family browser found")
def test_end_to_end_scenario() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "smoke.py")],
        capture_output=True, text=True, timeout=600, cwd=str(ROOT),
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        failures = [line.strip() for line in output.splitlines() if "[FAIL]" in line]
        pytest.fail("scenario failed:\n" + "\n".join(failures[-20:]) + "\n\n" + output[-3000:])
    assert "checks passed" in output


def test_secret_patterns_do_not_over_match() -> None:
    from jev_ultrafast_mcp.config import Config
    from jev_ultrafast_mcp.safety import is_secret

    cfg = Config.from_env()
    assert is_secret(cfg, "Password", "textbox")
    assert is_secret(cfg, "One-time code", "textbox")
    assert not is_secret(cfg, "Passengers", "combobox")
    assert not is_secret(cfg, "Promo code", "textbox")
    assert not is_secret(cfg, "Where from?", "textbox")


def test_confirmation_rules() -> None:
    from jev_ultrafast_mcp.config import Config
    from jev_ultrafast_mcp.safety import confirm_reason

    cfg = Config.from_env()
    assert confirm_reason(cfg, "Delete account", "button")
    assert confirm_reason(cfg, "Pay now", "button")
    assert confirm_reason(cfg, "Search", "button") is None
    assert confirm_reason(cfg, "Where to?", "textbox") is None


def test_domain_envelope() -> None:
    from jev_ultrafast_mcp.config import Config
    from jev_ultrafast_mcp.safety import SafetyError, check_url

    cfg = Config.from_env()
    cfg.allow_domains = ["example.com", "*.corp.example.com"]
    check_url(cfg, "https://example.com/a")
    check_url(cfg, "https://api.corp.example.com/b")
    with pytest.raises(SafetyError):
        check_url(cfg, "https://evil.test/a")

    cfg2 = Config.from_env()
    cfg2.deny_domains = ["admin.example.com"]
    with pytest.raises(SafetyError):
        check_url(cfg2, "https://admin.example.com/")


def test_macro_scoring_and_ambiguity() -> None:
    from jev_ultrafast_mcp.macros import MacroError, resolve
    from jev_ultrafast_mcp.observe import Element, Observation

    def observation(*elements: Element) -> Observation:
        return Observation(url="https://x.test", title="t", text="", elements=list(elements),
                           digest="d", text_digest="d", page_key="k", scroll={}, reachable=0,
                           omitted=0, overlays=[], cross_frames=0, cross_frame_srcs=[])

    page = observation(
        Element(ref="e1", role="textbox", name="Where from?"),
        Element(ref="e2", role="button", name="Search"),
    )
    ops, report = resolve([{"op": "type", "target": {"role": "textbox", "name": "Where from?"},
                            "text": "{{city}}"}], page, {"city": "Zurich"})
    assert ops == [{"op": "type", "ref": "e1", "text": "Zurich"}]
    assert report[0]["score"] == 1.0

    with pytest.raises(MacroError):
        resolve([{"op": "click", "target": {"role": "button", "name": "Checkout"}}], page)

    ambiguous = observation(
        Element(ref="e1", role="button", name="Select", context="Zurich -> London"),
        Element(ref="e2", role="button", name="Select", context="Zurich -> Paris"),
    )
    with pytest.raises(MacroError):
        resolve([{"op": "click", "target": {"role": "button", "name": "Select"}}], ambiguous)
