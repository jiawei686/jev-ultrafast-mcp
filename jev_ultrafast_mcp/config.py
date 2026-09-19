"""Configuration. Everything is environment-driven so an MCP client can set it.

No API keys are required for the default path. TypeSafe is opt-in: without
TYPESAFE_API_KEY the server still exposes the full observe/act/assert surface.
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


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


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
    text_model_key: str | None = None
    text_model_base: str = "https://api.deepseek.com/v1"
    text_model: str = "deepseek-chat"
    nav_timeout: float = 20.0
    call_timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "Config":
        profile = os.environ.get("JEVMCP_PROFILE_DIR")
        window = os.environ.get("JEVMCP_WINDOW", "1280x860")
        try:
            w, h = (int(part) for part in window.lower().split("x", 1))
        except Exception:
            w, h = 1280, 860
        return cls(
            chrome=os.environ.get("JEVMCP_CHROME"),
            mode=os.environ.get("JEVMCP_MODE", "launch"),
            cdp_url=os.environ.get("JEVMCP_CDP_URL"),
            headless=_env_bool("JEVMCP_HEADLESS", True),
            foreground=_env_bool("JEVMCP_FOREGROUND", False),
            sandbox=os.environ.get("JEVMCP_SANDBOX", "auto"),
            profile_dir=Path(profile).expanduser() if profile else None,
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
            typesafe_key=os.environ.get("TYPESAFE_API_KEY"),
            typesafe_model=os.environ.get("TYPESAFE_MODEL", "jev-latest"),
            text_model_key=os.environ.get("TEXT_MODEL_API_KEY"),
            text_model_base=os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1"),
            text_model=os.environ.get("TEXT_MODEL", "deepseek-chat"),
        )

    def resolved_profile(self) -> Path:
        path = self.profile_dir or (self.state_dir / "chrome-profile")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def macros_dir(self) -> Path:
        path = self.state_dir / "macros"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def turbo_ready(self) -> bool:
        return bool(self.typesafe_key)
