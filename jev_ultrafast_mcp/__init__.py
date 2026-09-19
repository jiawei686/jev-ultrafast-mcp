"""jev-ultrafast-mcp — fast, guarded browser control for agents.

The MCP server exposes an indexed element table and executes refs. The calling
agent is the policy: no second model, no key, no screenshots in the loop. The
one opt-in exception is `browser_goal`, which drives the loop server-side with a
decision model once you give it a key.
"""

__version__ = "0.1.2"

from .browser import BrowserManager, Session
from .config import Config

__all__ = ["Config", "BrowserManager", "Session", "__version__"]
