"""Entry point for the MCP bundle.

Why this file exists instead of pointing `entry_point` at the package
--------------------------------------------------------------------------
`jev_ultrafast_mcp/server.py` cannot be an entry point: it uses relative imports (`from . import
assertions`), so running it as a script dies with "attempted relative import with no known parent
package". That was the first version of the manifest, and it was wrong -- verified by running the
packed bundle, not by reading the spec.

A `uv` bundle is installed as a project before it runs, so by the time this file is executed
`jev_ultrafast_mcp` is importable from the environment and an absolute import is the correct one.
"""

from jev_ultrafast_mcp.server import main

if __name__ == "__main__":
    main()
