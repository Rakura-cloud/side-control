# SPDX-License-Identifier: GPL-3.0-or-later
"""python -m side_control.backend [--adapter hciX], as root"""
from .main import main

try:
    main()
except KeyboardInterrupt:
    pass
