"""The recorded corpus, the replay seams, and the golden-file helper.

WHY A CHARACTERIZATION SUITE AND NOT MORE UNIT TESTS. The 110 unit modules pin
the PARTS. Nothing pins the WHOLE. The failure this suite exists to catch is not
a crash -- a crash is loud and gets fixed. It is the restructure that deletes or
rewires a module and leaves a pipeline that still runs, still writes records,
still reports ``accounting_ok``, and routes citations differently than it did
yesterday. A green unit suite is entirely compatible with that.

So: run the real system on fixed inputs, freeze exactly what it produces today,
and afterwards assert equality. This suite does not care whether today's
behaviour is CORRECT. It cares that tomorrow's is IDENTICAL.

THE SEAMS REPLAY, THEY DO NOT IMPROVISE. Every stub below answers from a
recorded table and RAISES on a lookup miss. A stub that invented an answer for
an input it had not seen would quietly absorb exactly the change this suite is
here to catch: a refactor that starts asking a different question would get a
plausible reply and the routes would not move. A miss has to be a red test.

NO NETWORK, NO PAID CALL, NO WALL CLOCK, NO FILESYSTEM OUTSIDE A TMPDIR.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "characterization"
GOLDEN = Path(__file__).resolve().parent / "golden"

#: Set to regenerate a golden file after a DELIBERATE, reviewed behaviour change.
#: It is never used to make a red test green during a refactor: a golden diff
#: means behaviour moved, and the answer to that is to find the change, not to
#: rewrite the record of what the change was measured against.
UPDATE_GOLDEN = os.environ.get("CRE_UPDATE_GOLDEN") == "1"


def assert_golden(name: str, lines) -> None:
    """Compare sorted plain-text records against a committed golden file.

    Plain text, one record per line, sorted -- never a pickle and never an
    unordered dict dump -- so that ``git diff`` on a failure is the readable
    answer to "what did the restructure change?".
    """
    produced = "\n".join(sorted(lines)) + "\n"
    path = GOLDEN / name
    if UPDATE_GOLDEN or not path.exists():
        path.write_text(produced, encoding="utf-8")
        if not UPDATE_GOLDEN:
            pytest.fail(f"{name} did not exist and was created; commit it and "
                        f"re-run so the comparison is against a reviewed record")
        return
    expected = path.read_text(encoding="utf-8")
    if produced != expected:
        # The diff itself is the message: a route or a label that moved is named
        # on the line that changed.
        import difflib
        diff = "\n".join(difflib.unified_diff(
            expected.splitlines(), produced.splitlines(),
            fromfile=f"golden/{name}", tofile="produced", lineterm=""))
        pytest.fail(
            f"{name} differs from the committed golden record. A diff here is a "
            f"BEHAVIOUR CHANGE until proven otherwise -- find it, explain it, "
            f"revert it. Do NOT regenerate the golden file to go green.\n{diff}")


# ---------------------------------------------------------------------------
# the recorded Band 2 corpus
# ---------------------------------------------------------------------------
def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def band2_corpus():
    return load("band2_corpus.json")


class Recorded:
    """A seam that answers from a table and raises on a miss.

    ``label`` names the seam in the failure, because "the extractor was asked
    something it had never been asked" and "the coverage judge was" are
    different findings about a refactor.
    """

    def __init__(self, label: str, table: dict):
        self.label = label
        self.table = table
        self.asked: list = []

    def answer(self, key):
        self.asked.append(key)
        if key not in self.table:
            raise AssertionError(
                f"the {self.label} seam was asked {key!r}, which is not in the "
                f"recorded table. The pipeline is asking a question it did not "
                f"ask when these fixtures were captured -- that is a behaviour "
                f"change, not a missing fixture. Known keys: "
                f"{sorted(self.table)[:8]}")
        return self.table[key]
