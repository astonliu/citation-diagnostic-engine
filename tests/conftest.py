"""Put the package on the path for tests that live outside it.

The test modules import ``cre.f1.X`` absolutely rather than relatively, because
they sit beside the package rather than inside it. That is deliberate: a
characterization test must reach the code the way a caller does, and a relative
import would tie the suite to the package's internal layout -- which is
precisely the thing being restructured underneath it.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent / "citation_repair_F1_handoff"
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))
