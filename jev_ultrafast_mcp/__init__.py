"""jev-ultrafast-mcp — fast, guarded browser control for agents.

The MCP server exposes an indexed element table and executes refs. The calling
agent is the policy: no second model, no API keys, no screenshots in the loop.
"""

__version__ = "0.1.0"

from .browser import BrowserManager, Session
from .config import Config

__all__ = ["Config", "BrowserManager", "Session", "__version__"]
