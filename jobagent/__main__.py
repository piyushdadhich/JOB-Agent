"""Allow `python -m jobagent <command>` invocations.

Background services (Spec 16 TASK 3) call this entry point rather
than the console-script shim because `sys.executable -m jobagent`
is portable across Windows / macOS / Linux without needing to know
the platform-specific path to the `job-agent` executable.
"""
import sys

from jobagent.cli import main

if __name__ == "__main__":
    sys.exit(main())
