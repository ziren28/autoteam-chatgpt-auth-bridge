"""AutoTeam - ChatGPT Team 账号自动轮转管理工具"""

__version__ = "0.1.0"

import logging
import os
import sys

from rich.logging import RichHandler

if os.environ.get("AUTOTEAM_PROBE_MODE") == "1":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        stream=sys.stderr,
    )
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%H:%M:%S]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False, markup=True)],
    )
