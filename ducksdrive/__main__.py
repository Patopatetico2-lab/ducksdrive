"""
__main__.py - Package entrypoint for `python3 -m ducksdrive`.
"""

import os
import sys

if __package__ in (None, ""):
    _parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _parent not in sys.path:
        sys.path.insert(0, _parent)

from ducksdrive.main import main

if __name__ == "__main__":
    main()
