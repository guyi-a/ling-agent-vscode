"""PyInstaller entry point for the Ling sidecar."""

import sys

if "--computer-helper" in sys.argv[1:]:
    from ling_agent.computer.helper.main import run_helper

    raise SystemExit(run_helper(sys.argv[1:]))

from ling_rpc.server import main

if __name__ == "__main__":
    main()
