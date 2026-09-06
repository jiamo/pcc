"""One executable semantic-version contract for host pcc and fresh pcc1."""

PYTHON_TARGET_SOURCE = """import sys
import sysconfig
import platform
def describe():
    if sys.version_info >= (3, 15):
        print('current-target')
    else:
        print('old-target')
    print(sys.version_info)
    print(sys.version_info.major, sys.version_info.minor, sys.version_info.micro)
    print(sys.version)
    print(sysconfig.get_config_var('VERSION'))
    print(platform.python_version())
    print(platform.python_version_tuple())
describe()
"""

PYTHON_TARGET_STDOUT = """current-target
(3, 15, 0)
3 15 0
3.15.0 (pcc self-host)
3.15
3.15.0
('3', '15', '0')
"""
