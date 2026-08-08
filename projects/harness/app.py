"""Native entrypoint for the PCC DeepSeek Harness port."""

import sys

from harness_core import run_cli


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--gui-self-check":
        from gui_app import gui_self_check

        return gui_self_check()
    if len(sys.argv) == 1:
        from gui_app import run_gui

        return run_gui()
    return run_cli(sys.argv)


main()
