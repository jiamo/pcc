from __future__ import annotations

import sys

from .build_exec import main as build_exec_main
from .build_plan import main as build_plan_main
from .array_core import main as array_core_main
from .campaign import main as campaign_main
from .extension_abi import main as extension_abi_main
from .inspect import main as inspect_main
from .install import main as install_main
from .linkage import main as linkage_main
from .toolchain import main as toolchain_main
from .wheel_repo import main as wheel_repo_main

if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "wheel-repo":
        raise SystemExit(wheel_repo_main(argv[1:]))
    if argv and argv[0] == "ext-abi":
        raise SystemExit(extension_abi_main(argv[1:]))
    if argv and argv[0] == "campaign":
        raise SystemExit(campaign_main(argv[1:]))
    if argv and argv[0] == "array-core":
        raise SystemExit(array_core_main(argv[1:]))
    if argv and argv[0] == "toolchain":
        raise SystemExit(toolchain_main(argv[1:]))
    if argv and argv[0] == "linkage":
        raise SystemExit(linkage_main(argv[1:]))
    if argv and argv[0] == "build-exec":
        raise SystemExit(build_exec_main(argv[1:]))
    if argv and argv[0] == "build-plan":
        raise SystemExit(build_plan_main(argv[1:]))
    if argv and argv[0] == "inspect":
        raise SystemExit(inspect_main(argv[1:]))
    if argv and argv[0] == "install":
        raise SystemExit(install_main(argv[1:]))
    raise SystemExit(inspect_main(argv))
