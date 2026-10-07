"""Entry point: `python3 -m agent [--frontend-fd N] [ai args...]` or `python3 <dir>/agent [...]`.

Run as a path (the C front-end does this so cwd never matters), the package's parent directory is
put first on sys.path: the repo checkout, or ~/.local/share/ai/lib when installed — the same
aitools package the ai_mcp.py shim next to it would import."""
import os
import sys

if not __package__:
    _here = os.path.dirname(os.path.realpath(__file__))
    # `python3 <dir>/agent` puts the package dir itself on sys.path[0]; drop it so agent's
    # module names (cli, tools, session, ...) can never shadow anything.
    sys.path[:] = [p for p in sys.path if os.path.realpath(p or ".") != _here]
    sys.path.insert(0, os.path.dirname(_here))

from agent.loop import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
