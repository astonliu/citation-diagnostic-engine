"""Make ``cde`` importable when pytest is run from anywhere in the tree.

The test modules import ``cde.<subpackage>.<module>`` absolutely rather than
relatively, even though they sit in a package of their own. That is deliberate:
a test should reach the code the way a caller does. A relative import would tie
the suite to the package's internal layout, which is the thing most likely to
move next.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
