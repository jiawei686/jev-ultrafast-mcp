"""Configuration. Everything is environment-driven so an MCP client can set it.

No key is needed for the browser path: open, observe, act, assert and macro all
run against a local Chrome and never call out. A decision model is opt-in, and it
is reachable through two APIs that are both supported here:

    JEV_PROVIDER=typesafe     Jev's own API, paid for with TYPESAFE_API_KEY (the default)
    JEV_PROVIDER=openrouter   the same model through OpenRouter, paid for with
                              OPENROUTER_API_KEY and no TypeSafe account

`TYPESAFE_BASE_URL` still overrides the decisions URL for either, and is what the
provider is inferred from when `JEV_PROVIDER` is unset, so every existing
configuration keeps working. Without a key, `browser_goal` reports
`turbo_unavailable` and executes nothing, while the rest of the surface is
unaffected.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

CHROME_CANDIDATES = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ],
    "linux": [
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
        "microsoft-edge", "brave-browser",
    ],
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ],
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def chrome_data_dirs() -> list[Path]:
    """Where a Chromium-family browser keeps `DevToolsActivePort`.

    Chrome 144+ exposes remote debugging as a WebSocket-only server: the port it
    prints on `chrome://inspect/#remote-debugging` answers 404 to every `/json/*`
    path, so the port number alone is not enough to attach. The same port and the
    browser-level WebSocket path are written to `DevToolsActivePort` inside the
    browser's data directory, which is what this list is for. Most likely first.
    """
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
        names = ["Google/Chrome", "Chromium", "Google/Chrome Beta",
                 "Microsoft Edge", "BraveSoftware/Brave-Browser"]
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        names = ["Google/Chrome/User Data", "Chromium/User Data",
                 "Microsoft/Edge/User Data", "BraveSoftware/Brave-Browser/User Data"]
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
        names = ["google-chrome", "chromium", "microsoft-edge",
                 "BraveSoftware/Brave-Browser"]
    return [base / name for name in names]


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"

DEFAULT_PROVIDER = "typesafe"
DEFAULT_TEXT_BASE = "https://api.deepseek.com/v1"
DEFAULT_TEXT_MODEL = "deepseek-chat"

# Jev is reachable through more than one API, and both are first-class here rather than one
# being the default and the other being a variable named after its competitor. Every route
# speaks the same `{model, state, questions}` contract, so a provider is three facts: where
# decisions are posted, which variable holds the key, and whether that provider *also* serves
# an OpenAI-compatible chat route the text helper can borrow.
#
# That third fact is a separate question and is asked separately, because these are different
# APIs rather than two URLs for one API. Jev's own API answers typed questions; it does not
# write prose, so a text helper has nothing to inherit from it. Recording that as `None` in the
# table is what keeps it a fact about the provider instead of a claim in a docstring: giving
# Jev a chat route later is one line here, and until then the helper refuses by name and says
# which provider cannot serve it.
PROVIDERS: dict[str, dict] = {
    "typesafe": {
        "endpoint": TYPESAFE_ENDPOINT,
        "key_vars": ("TYPESAFE_API_KEY",),
        "chat_base": None,
    },
    "openrouter": {
        "endpoint": OPENROUTER_ENDPOINT,
        # Its own key, and only its own. An earlier revision accepted TYPESAFE_API_KEY here as a
        # fallback; no working configuration ever relied on it, because a key issued by one API
        # does not authenticate against another's host -- it 401s, and the message names the
        # company that rejected it rather than the variable that is wrong. The fallback made the
        # table's second column mean "some key" instead of "this provider's key", which is the
        # one thing the table exists to keep straight.
        "key_vars": ("OPENROUTER_API_KEY",),
        "chat_base": "https://openrouter.ai/api/v1",
    },
}


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def resolve_provider() -> str:
    """Which API pays for the decision model.

    `JEV_PROVIDER` names it outright. With that unset the answer is inferred from
    `TYPESAFE_BASE_URL`, which is how this was configured before the provider had a name:
    pointing that variable at OpenRouter is the documented route, so a URL is as good as a
    word and every existing configuration keeps working unchanged.

    An unrecognised name is ignored rather than raised, and the inference below still runs.
    `Config.from_env()` runs at import, so a typo in one optional variable must not take down
    the whole server -- including the browser surface that needs no key at all. It is not
    silent either: `provider_note()` reports it, which is where a typo belongs.
    """
    explicit = _env("JEV_PROVIDER").lower()
    if explicit in PROVIDERS:
        return explicit
    if "openrouter.ai" in _env("TYPESAFE_BASE_URL"):
        return "openrouter"
    return DEFAULT_PROVIDER


def provider_endpoint(provider: str) -> str:
    """Where decisions are posted, honouring `TYPESAFE_BASE_URL` as a custom-endpoint override.

    The override outlives the provider it was named after -- a proxy, a staging host and
    OpenRouter itself are all just a different URL -- so it is still honoured when it is set.

    One case is not honoured: a URL naming a *different* provider than the one selected. That
    is a contradiction, and the explicitly named provider wins, because the alternative is
    sending one API's key to another API's host and reporting the 401 as if the key were bad.
    A URL that names no provider is a proxy or a staging host and is taken at face value.
    """
    url = _env("TYPESAFE_BASE_URL").rstrip("/")
    if not url:
        return PROVIDERS[provider]["endpoint"]
    for name in PROVIDERS:
        if name != provider and name in url:
            return PROVIDERS[provider]["endpoint"]
    if provider == "openrouter" and not url.endswith("/decisions"):
        return url + "/api/alpha/decisions"
    return url


def provider_note() -> str | None:
    """A sentence for `browser_doctor` when `JEV_PROVIDER` names something unknown.

    Unrecognised is deliberately not fatal -- see `resolve_provider` -- but it must not be
    silent either, or a typo turns into a mystery 401 against a provider the caller never
    chose and has no reason to suspect.
    """
    raw = _env("JEV_PROVIDER")
    if raw and raw.lower() not in PROVIDERS:
        return (f"JEV_PROVIDER={raw!r} is not a provider this build knows "
                f"({', '.join(sorted(PROVIDERS))}); the default ({DEFAULT_PROVIDER}) is in use.")
    return None


def _provider_key(provider: str) -> str | None:
    for name in PROVIDERS[provider]["key_vars"]:
        value = _env(name)
        if value:
            return value
    return None


def _text_backend(provider: str, decision_key: str | None) -> tuple[str, str | None, str | None]:
    """Resolve (base, key, model) for the text helper.

    An explicit `TEXT_MODEL_*` configuration always wins and keeps its DeepSeek default -- that
    is the long-standing route and it must not change under anyone who already relies on it.

    With nothing set, the helper inherits *the provider the decision model is already using*,
    and inherits both halves together: the key and the base URL come from the same provider, so
    the request is authenticated by the API it is actually sent to. Borrowing the key alone is
    the mistake this shape exists to prevent -- it would post to DeepSeek's URL with an
    OpenRouter key and earn a 401 naming the wrong company, and a run diagnosed from the wrong
    provider's error is a run nobody diagnoses.

    The model slug is deliberately *not* inherited. `deepseek-chat` is not an OpenRouter slug,
    and guessing one would turn a clear refusal into a confusing 400. Inheriting therefore
    requires the caller to name the model, and the helper refuses by name when they have not.
    """
    explicit_key = _env("TEXT_MODEL_API_KEY")
    explicit_base = _env("TEXT_MODEL_BASE_URL")
    if explicit_key or explicit_base:
        return (explicit_base or DEFAULT_TEXT_BASE, explicit_key,
                _env("TEXT_MODEL") or DEFAULT_TEXT_MODEL)
    chat_base = PROVIDERS[provider]["chat_base"]
    if chat_base and decision_key:
        return chat_base, decision_key, _env("TEXT_MODEL") or None
    return DEFAULT_TEXT_BASE, None, _env("TEXT_MODEL") or DEFAULT_TEXT_MODEL


def find_chrome(explicit: str | None = None) -> str:
    """Locate a Chromium-family browser binary."""
    if explicit:
        return explicit
    for candidate in CHROME_CANDIDATES.get(sys.platform, CHROME_CANDIDATES["linux"]):
        if candidate.startswith("/") or candidate.startswith("C:"):
            if Path(candidate).exists():
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    raise RuntimeError(
        "No Chrome/Chromium binary found. Set JEVMCP_CHROME to the executable path."
    )


DEFAULT_DENY_PATTERNS = [
    r"\bdelete\s+(account|workspace|repository|project)\b",
    r"\bpay\s+now\b", r"\bplace\s+order\b", r"\bcomplete\s+purchase\b", r"\bbuy\s+now\b",
    r"\bcancel\s+(order|subscription|booking)\b", r"\bunsubscribe\b",
    r"\b(close|delete)\s+permanently\b", r"\bconfirm\s+(payment|order|transfer)\b",
    r"\bsend\s+(money|payment)\b", r"\bwithdraw\b", r"\btransfer\s+funds\b",
]

DEFAULT_SECRET_PATTERNS = [
    r"\bpass(word|wd|code|phrase)\b", r"\bpassphrase\b", r"\botp\b",
    r"\bone[- ]?time\b", r"\bverification code\b", r"\bsecurity code\b",
    r"\bcvv\b", r"\bcvc\b", r"\bcard\s*number\b", r"\bsecret\b",
    r"\bapi\s*key\b", r"\baccess\s*token\b", r"\bssn\b", r"\bsocial security\b",
    r"\biban\b", r"\brouting\s*number\b", r"\bsecurity\s*answer\b", r"\bpin\b",
]


@dataclass
class Config:
    chrome: str | None = None
    mode: str = "launch"                  # launch | attach
    cdp_url: str | None = None            # required for mode=attach
    headless: bool = True
    foreground: bool = False              # True = activate the owned tab (watch it work)
    sandbox: str = "auto"                 # auto | on | off
    profile_dir: Path | None = None
    attach_profile_dir: Path | None = None  # data dir of the browser we attach to
    window: tuple[int, int] = (1280, 860)
    max_actions: int = 250
    max_text: int = 6000
    state_dir: Path = field(default_factory=lambda: Path.home() / ".jev-ultrafast-mcp")
    allow_domains: list[str] = field(default_factory=list)
    deny_domains: list[str] = field(default_factory=list)
    allow_js: bool = False
    allow_uploads: bool = True
    confirm_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_PATTERNS))
    secret_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_SECRET_PATTERNS))
    typesafe_key: str | None = None
    typesafe_model: str = "jev-latest"
    # Which API pays for the decision model: "typesafe" (Jev's own) or "openrouter".
    # Named by JEV_PROVIDER, or inferred from TYPESAFE_BASE_URL. See PROVIDERS.
    provider: str = DEFAULT_PROVIDER
    # Where the decision model lives. Same wire contract at every route, so the
    # only thing that changes is the URL and whose credits pay for it.
    #   TypeSafe direct : https://api.typesafe.ai/v1/systemone   (TYPESAFE_API_KEY)
    #   OpenRouter      : https://openrouter.ai/api/alpha/decisions (OPENROUTER key;
    #                     note the path is outside /api/v1)
    typesafe_endpoint: str = TYPESAFE_ENDPOINT
    # The text helper. An explicit TEXT_MODEL_* configuration wins; otherwise both halves
    # are inherited from `provider` together, so key and base URL never disagree.
    # `text_model` is None only in the inherited case, where the caller has not named a
    # model the provider actually serves -- and the helper refuses by name rather than
    # guessing a slug.
    text_model_key: str | None = None
    text_model_base: str = DEFAULT_TEXT_BASE
    text_model: str | None = DEFAULT_TEXT_MODEL
    nav_timeout: float = 20.0
    call_timeout: float = 30.0
    settle_timeout: float = 4.0            # max wait for a client-rendered page to show elements
    settle_poll_ms: int = 120              # re-read cadence while waiting

    @classmethod
    def from_env(cls) -> "Config":
        profile = os.environ.get("JEVMCP_PROFILE_DIR")
        attach_profile = os.environ.get("JEVMCP_ATTACH_PROFILE_DIR")
        provider = resolve_provider()
        turbo_endpoint = provider_endpoint(provider)
        turbo_key = _provider_key(provider)
        text_base, text_key, text_model = _text_backend(provider, turbo_key)
        window = os.environ.get("JEVMCP_WINDOW", "1280x860")
        try:
            w, h = (int(part) for part in window.lower().split("x", 1))
        except Exception:  # noqa: BLE001 - a malformed JEVMCP_WINDOW falls back, it does not refuse
            w, h = 1280, 860
        return cls(
            chrome=os.environ.get("JEVMCP_CHROME"),
            mode=os.environ.get("JEVMCP_MODE", "launch"),
            cdp_url=os.environ.get("JEVMCP_CDP_URL"),
            headless=_env_bool("JEVMCP_HEADLESS", True),
            foreground=_env_bool("JEVMCP_FOREGROUND", False),
            sandbox=os.environ.get("JEVMCP_SANDBOX", "auto"),
            profile_dir=Path(profile).expanduser() if profile else None,
            attach_profile_dir=(
                Path(attach_profile).expanduser() if attach_profile else None
            ),
            window=(w, h),
            max_actions=int(os.environ.get("JEVMCP_MAX_ACTIONS", "250")),
            max_text=int(os.environ.get("JEVMCP_MAX_TEXT", "6000")),
            state_dir=Path(
                os.environ.get("JEVMCP_STATE_DIR", str(Path.home() / ".jev-ultrafast-mcp"))
            ).expanduser(),
            allow_domains=_env_list("JEVMCP_ALLOW_DOMAINS"),
            deny_domains=_env_list("JEVMCP_DENY_DOMAINS"),
            allow_js=_env_bool("JEVMCP_ALLOW_JS", False),
            allow_uploads=_env_bool("JEVMCP_ALLOW_UPLOADS", True),
            confirm_patterns=_env_list("JEVMCP_CONFIRM_PATTERNS") or list(DEFAULT_DENY_PATTERNS),
            typesafe_key=turbo_key,
            typesafe_model=os.environ.get("TYPESAFE_MODEL", "jev-latest"),
            provider=provider,
            typesafe_endpoint=turbo_endpoint,
            text_model_key=text_key,
            text_model_base=text_base,
            text_model=text_model,
            settle_timeout=float(os.environ.get("JEVMCP_SETTLE_TIMEOUT", "4.0")),
            settle_poll_ms=int(os.environ.get("JEVMCP_SETTLE_POLL_MS", "120")),
        )

    def resolved_profile(self) -> Path:
        path = self.profile_dir or (self.state_dir / "chrome-profile")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def attach_data_dirs(self) -> list[Path]:
        """Data dirs to look for `DevToolsActivePort` in, most likely first."""
        dirs = list(chrome_data_dirs())
        if self.attach_profile_dir:
            dirs.insert(0, self.attach_profile_dir)
        return dirs

    def macros_dir(self) -> Path:
        path = self.state_dir / "macros"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def turbo_ready(self) -> bool:
        return bool(self.typesafe_key)
