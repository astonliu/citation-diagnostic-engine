"""Offline tests for cre/f1/fulltext_reader.fetch_fulltext.

Fully offline: the ``resolve_pmcid`` / ``fetch_xml`` seams are injected, and the
tests that exercise the LIVE defaults inject a stub ``session`` instead, so no
test touches the network. ``time.sleep`` is neutralized so retry/limiter backoff
costs no wall-clock. Each test maps to one row of the acceptance matrix.

Every fixture here is a CODE-PATH fixture: hand-written JATS that exists only to
drive a branch. None of it is evaluation data, a gold label, or a reported
number.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import pytest

from cre.f1 import f7_entity as f7
from cre.f1 import fulltext_reader as fr
from cre.f1.ncbi_meta import EFETCH, ELINK


# --------------------------------------------------------------------------
# Fixtures. Written with real JATS whitespace (EFetch pretty-prints), so the
# whitespace-collapse path is exercised rather than bypassed.
# --------------------------------------------------------------------------
FULL_JATS = """<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <front>
      <article-meta>
        <abstract><p>An abstract that is not body text.</p></abstract>
      </article-meta>
    </front>
    <body>
      <sec sec-type="intro">
        <title>Introduction</title>
        <p>Sepsis remains common.</p>
      </sec>
      <sec sec-type="materials|methods">
        <title>2. Materials and Methods</title>
        <p>We enrolled 40 patients.</p>
        <sec>
          <title>Statistical analysis</title>
          <p>We used a two-sided t-test.</p>
        </sec>
      </sec>
      <sec sec-type="results">
        <title>3. Results</title>
        <p>Mortality fell by 12%.</p>
        <table-wrap id="t1">
          <label>Table 1</label>
          <caption><title>Baseline characteristics</title></caption>
          <table>
            <thead><tr><th>Arm</th><th>N</th></tr></thead>
            <tbody>
              <tr><td>Drug</td><td>20</td></tr>
              <tr><td>Placebo</td><td>20</td></tr>
            </tbody>
          </table>
        </table-wrap>
        <sec>
          <title>Secondary outcomes</title>
          <p>No difference in length of stay.</p>
          <table-wrap id="t2">
            <label>Table 2</label>
            <caption><p>Secondary endpoints.</p></caption>
            <table><tbody><tr><td>LOS</td><td>0.42</td></tr></tbody></table>
          </table-wrap>
        </sec>
        <fig id="f1">
          <label>Figure 1</label>
          <caption><title>Survival curve.</title><p>Kaplan-Meier estimate.</p></caption>
          <graphic xlink:href="f1.jpg" xmlns:xlink="http://www.w3.org/1999/xlink"/>
        </fig>
      </sec>
      <sec>
        <title>Discussion</title>
        <p>Interpretation follows.</p>
      </sec>
    </body>
  </article>
</pmc-articleset>
"""

# The SAME document under a default namespace. Only the root <article> gains an
# xmlns, which namespaces every descendant -- exactly the shape parser.py's
# namespace-naive find() paths cannot read.
FULL_JATS_NS = FULL_JATS.replace(
    "<article>", '<article xmlns="http://jats.nlm.nih.gov">')

NO_BODY_JATS = """<?xml version="1.0"?>
<pmc-articleset><article>
  <front><article-meta>
    <abstract><p>Abstract only; this record carries no body.</p></abstract>
  </article-meta></front>
</article></pmc-articleset>
"""

DISCUSSION_ONLY_JATS = """<?xml version="1.0"?>
<pmc-articleset><article><body>
  <sec sec-type="intro"><title>Background</title><p>Prior work.</p></sec>
  <sec><title>Discussion</title><p>We speculate.</p></sec>
</body></article></pmc-articleset>
"""

UNLABELLED_SEC_JATS = """<?xml version="1.0"?>
<pmc-articleset><article><body>
  <sec><title>A heading naming no canonical section</title>
    <p>Text that must survive.</p></sec>
</body></article></pmc-articleset>
"""

MALFORMED_JATS = '<?xml version="1.0"?><pmc-articleset><article><body><sec>'

SUB_ARTICLE_JATS = """<?xml version="1.0"?>
<pmc-articleset><article>
  <body><sec sec-type="results"><title>Results</title>
    <p>The article's own results.</p></sec></body>
  <sub-article article-type="peer-review">
    <body><sec sec-type="results"><title>Results</title>
      <p>A reviewer's text that is not this paper.</p></sec></body>
  </sub-article>
</article></pmc-articleset>
"""

MINIMAL_RESULTS_JATS = """<?xml version="1.0"?>
<pmc-articleset><article><body>
  <sec sec-type="results"><title>Results</title><p>{body}</p></sec>
</body></article></pmc-articleset>
"""

# Enough prose to clear NONTRIVIAL_BODY_CHARS, for the rows that must face the
# REAL floor rather than opt out of it. Deliberately boring: it is padding, and
# nothing asserts anything about its content beyond its presence.
# Stripped: the reader collapses whitespace, so a trailing space would not
# survive extraction and would break the exact-text assertions that use it.
FILLER = ("This paragraph exists only to give the body a realistic length. "
          * 20).strip()

# A non-IMRAD body -- intro + discussion, zero results, zero methods -- at a
# realistic size, so "an essay-shaped paper is a complete retrieval" is proved
# against the real floor instead of a neutralized one. The intro text is kept
# short and exact so the section-text assertion stays precise.
DISCUSSION_ONLY_BIG_JATS = """<?xml version="1.0"?>
<pmc-articleset><article><body>
  <sec sec-type="intro"><title>Background</title><p>Prior work.</p></sec>
  <sec><title>Discussion</title><p>{filler}</p></sec>
</body></article></pmc-articleset>
""".format(filler=FILLER)

# A full IMRAD body at realistic size: the ordinary complete case, under the real
# floor. FULL_JATS itself stays small so its exact label/text/hash assertions
# remain readable.
FULL_JATS_BIG = FULL_JATS.replace(
    "<p>Mortality fell by 12%.</p>", f"<p>Mortality fell by 12%. {FILLER}</p>")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seams(xml_text, pmcid="PMC7654321"):
    """(resolve_pmcid, fetch_xml) stubs plus a call log."""
    calls = {"resolve": 0, "fetch": 0}

    def resolve(pmid):
        calls["resolve"] += 1
        return pmcid

    def fetch(cid):
        calls["fetch"] += 1
        return xml_text

    return resolve, fetch, calls


def _boom(*_args, **_kwargs):
    raise AssertionError("this seam must not be called")


class _Resp:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self.headers = {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _RoutedSession:
    """Serves a fixed response per URL and records every call."""

    def __init__(self, by_url):
        self._by_url = by_url
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self._by_url[url]


class _BoomSession:
    def get(self, url, params=None, timeout=None):
        raise AssertionError("no HTTP request expected")


def _elink_ok(pmcid_digits="7654321"):
    return _Resp(payload={"linksets": [{"linksetdbs": [
        {"linkname": "pubmed_pmc", "links": [pmcid_digits]}]}]})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)


@pytest.fixture
def no_size_floor(monkeypatch):
    """Neutralize NONTRIVIAL_BODY_CHARS for rows that are not about it.

    These fixtures run a few dozen to a few hundred characters: they exist to pin
    labelling, namespaces, hashing, sanitization and cache behaviour, and padding
    each past a 1000-character floor would bury what they actually test. Opt-in,
    never autouse.

    RULE -- a test that asserts ``retrieval_complete`` or ``incomplete_reasons``
    MUST NOT use this fixture. Those two fields ARE the floor's output, so
    asserting them under a neutralized floor proves nothing while reading exactly
    like proof. A row that needs to make a completeness claim uses a realistic
    fixture (``FULL_JATS_BIG``, ``DISCUSSION_ONLY_BIG_JATS``, ``_essay_jats()``)
    and faces the real constant. ``test_no_completeness_claim_hides_behind_a_
    lowered_floor`` enforces this mechanically, so it cannot decay into a comment.
    """
    monkeypatch.setattr(fr, "NONTRIVIAL_BODY_CHARS", 1)


# --------------------------------------------------------------------------
# Row 1 -- namespaced JATS with results + methods + 2 tables
# --------------------------------------------------------------------------
def test_namespaced_jats_extracts_labels_tables_and_figures(no_size_floor):
    resolve, fetch, _ = _seams(FULL_JATS_NS)
    out = fr.fetch_fulltext("111", resolve_pmcid=resolve, fetch_xml=fetch)

    assert out["resolved"] is True
    assert out["pmcid"] == "PMC7654321"
    assert out["source"] == "live"

    labelled = [(s["label"], s["title"]) for s in out["sections"]]
    assert labelled == [
        ("intro", "Introduction"),
        ("methods", "2. Materials and Methods"),
        ("methods", "Statistical analysis"),
        ("results", "3. Results"),
        ("table", "Table 1"),
        ("results", "Secondary outcomes"),
        ("table", "Table 2"),
        ("figure", "Figure 1"),
        ("discussion", "Discussion"),
    ]
    by_title = {s["title"]: s for s in out["sections"]}
    assert by_title["3. Results"]["text"] == "Mortality fell by 12%."
    # A nested subsection with no sec-type inherits its parent's label, so a
    # complete retrieval is not reported incomplete just because JATS nests.
    assert by_title["Statistical analysis"]["label"] == "methods"
    assert by_title["Secondary outcomes"]["label"] == "results"
    # Table cell boundaries survive; a flat itertext would lose them.
    assert by_title["Table 1"]["text"] == (
        "Baseline characteristics\n\n"
        "Arm | N\nDrug | 20\nPlacebo | 20"
    )
    assert by_title["Table 2"]["text"] == "Secondary endpoints.\n\nLOS | 0.42"
    # A figure's caption title is content, not a heading, so it stays in text --
    # as its own block, since EFetch writes <title><p> with nothing between them.
    assert by_title["Figure 1"]["text"] == (
        "Survival curve.\n\nKaplan-Meier estimate.")
    for section in out["sections"]:
        assert section["content_sha256"] == _sha(section["text"])


# --------------------------------------------------------------------------
# Row 2 -- same content, no namespace: identical output
# --------------------------------------------------------------------------
def test_unnamespaced_jats_output_is_identical_to_namespaced(no_size_floor):
    """The parser.py defect, not reproduced: a default namespace must change
    nothing about what is extracted."""
    resolve_a, fetch_a, _ = _seams(FULL_JATS_NS)
    resolve_b, fetch_b, _ = _seams(FULL_JATS)
    namespaced = fr.fetch_fulltext("111", resolve_pmcid=resolve_a,
                                   fetch_xml=fetch_a)
    plain = fr.fetch_fulltext("111", resolve_pmcid=resolve_b, fetch_xml=fetch_b)
    # Whole-dict equality already covers every field, completeness included.
    assert namespaced == plain


# --------------------------------------------------------------------------
# Row 3 -- no PMCID
# --------------------------------------------------------------------------
def test_no_pmcid_is_unresolved_and_never_cached(tmp_path):
    cache_dir = str(tmp_path / "fulltext")
    out = fr.fetch_fulltext("222", cache_dir=cache_dir,
                            resolve_pmcid=lambda p: "", fetch_xml=_boom)
    assert out["resolved"] is False
    assert out["pmcid"] is None
    assert out["retrieval_complete"] is False
    assert out["incomplete_reasons"] == ["no_pmcid"]
    assert out["sections"] == []
    assert not os.path.exists(os.path.join(cache_dir, "fulltext_pmid_222.json"))


# --------------------------------------------------------------------------
# Row 4 -- a document, but no <body>
# --------------------------------------------------------------------------
def test_record_with_abstract_but_no_body_is_resolved_but_incomplete():
    resolve, fetch, _ = _seams(NO_BODY_JATS)
    out = fr.fetch_fulltext("333", resolve_pmcid=resolve, fetch_xml=fetch)
    assert out["resolved"] is True
    assert out["retrieval_complete"] is False
    assert out["incomplete_reasons"] == ["no_body"]
    assert out["sections"] == []


def test_no_body_is_cached_because_it_is_a_property_of_the_document(tmp_path):
    """resolved=True is the cache predicate: no_body is stable, not transient."""
    cache_dir = str(tmp_path / "fulltext")
    resolve, fetch, _ = _seams(NO_BODY_JATS)
    fr.fetch_fulltext("333", cache_dir=cache_dir, resolve_pmcid=resolve,
                      fetch_xml=fetch)
    second = fr.fetch_fulltext("333", cache_dir=cache_dir,
                               resolve_pmcid=_boom, fetch_xml=_boom)
    assert second["source"] == "cache"
    assert second["incomplete_reasons"] == ["no_body"]


# --------------------------------------------------------------------------
# Row 5 -- body present, no results/methods. AMENDED (DEC-041): completeness
# asks whether the body was retrieved, not whether the paper is IMRAD, so a
# discussion-and-intro body is COMPLETE. The old expectation --
# False / no_results_or_methods -- made completeness structurally unreachable
# for every non-IMRAD paper, holding reviews forever under DEC-032.
# --------------------------------------------------------------------------
def test_discussion_and_intro_only_is_complete_and_records_its_sections():
    """Faces the REAL floor: this row's whole claim is a completeness claim, so
    proving it under a neutralized floor would prove nothing."""
    resolve, fetch, _ = _seams(DISCUSSION_ONLY_BIG_JATS)
    out = fr.fetch_fulltext("444", resolve_pmcid=resolve, fetch_xml=fetch)
    assert out["resolved"] is True
    assert out["retrieval_complete"] is True
    assert out["incomplete_reasons"] == []
    assert [s["label"] for s in out["sections"]] == ["intro", "discussion"]
    assert out["sections"][0]["text"] == "Prior work."
    # What was present is REPORTED, and gates nothing.
    assert out["sections_present"] == ["discussion", "intro"]


# --------------------------------------------------------------------------
# Row 6 -- unlabelled <sec>
# --------------------------------------------------------------------------
def test_unlabelled_sec_is_other_and_its_text_is_preserved():
    resolve, fetch, _ = _seams(UNLABELLED_SEC_JATS)
    out = fr.fetch_fulltext("555", resolve_pmcid=resolve, fetch_xml=fetch)
    assert [s["label"] for s in out["sections"]] == ["other"]
    assert out["sections"][0]["text"] == "Text that must survive."
    assert out["sections"][0]["title"] == (
        "A heading naming no canonical section")
    assert out["retrieval_complete"] is False


# --------------------------------------------------------------------------
# Row 7 -- malformed XML
# --------------------------------------------------------------------------
def test_malformed_xml_is_unparseable_never_resolved_and_not_cached(tmp_path):
    cache_dir = str(tmp_path / "fulltext")
    resolve, fetch, _ = _seams(MALFORMED_JATS)
    out = fr.fetch_fulltext("666", cache_dir=cache_dir, resolve_pmcid=resolve,
                            fetch_xml=fetch)
    assert out["resolved"] is False
    assert out["retrieval_complete"] is False
    assert out["incomplete_reasons"] == ["body_unparseable"]
    assert out["sections"] == []
    assert not os.path.exists(os.path.join(cache_dir, "fulltext_pmid_666.json"))


def test_empty_efetch_body_is_unparseable_not_a_silent_empty_result():
    resolve, fetch, _ = _seams("   ")
    out = fr.fetch_fulltext("667", resolve_pmcid=resolve, fetch_xml=fetch)
    assert out["resolved"] is False
    assert out["incomplete_reasons"] == ["body_unparseable"]


def test_undefined_entity_parse_error_yields_no_sections():
    """The DOCTYPE-level failure JATS's external DTD provokes must not be
    swallowed into a truncated section list."""
    resolve, fetch, _ = _seams(
        '<?xml version="1.0"?><!DOCTYPE article><article><body>'
        '<sec sec-type="results"><title>Results</title>'
        '<p>Effect was &alpha; large.</p></sec></body></article>')
    out = fr.fetch_fulltext("668", resolve_pmcid=resolve, fetch_xml=fetch)
    assert out["retrieval_complete"] is False
    assert out["incomplete_reasons"] == ["body_unparseable"]
    assert out["sections"] == []


# --------------------------------------------------------------------------
# Row 8 -- lone surrogate
# --------------------------------------------------------------------------
def test_lone_surrogate_is_escaped_at_the_boundary_and_recorded():
    """A lone surrogate makes ET.fromstring raise UnicodeEncodeError, and would
    kill a later JSONL write. It is escaped before parsing, and recorded."""
    resolve, fetch, _ = _seams(
        MINIMAL_RESULTS_JATS.format(
            body="Effect size " + chr(0xD800) + " here. " + FILLER))
    out = fr.fetch_fulltext("777", resolve_pmcid=resolve, fetch_xml=fetch)

    assert out["sanitized_paths"] == ["xml_text"]
    # Real floor, not a lowered one: an escaped code point is a sanitization
    # event, never a retrieval failure.
    assert out["retrieval_complete"] is True
    assert out["incomplete_reasons"] == []
    text = out["sections"][0]["text"]
    assert text == "Effect size \\ud800 here. " + FILLER
    # The whole record survives the JSONL round trip that motivated the guard.
    json.dumps(out, ensure_ascii=False).encode("utf-8")
    assert out["sections"][0]["content_sha256"] == _sha(text)


def test_valid_non_ascii_is_preserved_verbatim_beside_a_surrogate():
    """Only the offending code points are rewritten: a blanket ASCII fold would
    silently narrow the corpus."""
    resolve, fetch, _ = _seams(
        MINIMAL_RESULTS_JATS.format(
            body="α-synuclein rose ≥12% " + chr(0xDC00) + " overall."))
    out = fr.fetch_fulltext("778", resolve_pmcid=resolve, fetch_xml=fetch)
    assert out["sections"][0]["text"] == "α-synuclein rose ≥12% \\udc00 overall."
    assert out["sanitized_paths"] == ["xml_text"]


def test_no_surrogate_means_no_sanitized_paths():
    resolve, fetch, _ = _seams(MINIMAL_RESULTS_JATS.format(body="Clean text."))
    out = fr.fetch_fulltext("779", resolve_pmcid=resolve, fetch_xml=fetch)
    assert out["sanitized_paths"] == []


def test_emit_sections_reescapes_a_surrogate_that_reached_a_section():
    """Defense in depth: the per-field pass is what guarantees the emitted text
    is encodable, independent of how a section was built."""
    touched: list = []
    out = fr._emit_sections(
        [{"label": "results", "title": "T", "text": "bad " + chr(0xD800)}],
        touched)
    assert touched == ["sections[0].text"]
    assert out[0]["text"] == "bad \\ud800"
    assert out[0]["content_sha256"] == _sha(out[0]["text"])


# --------------------------------------------------------------------------
# Row 9 -- cache round trip
# --------------------------------------------------------------------------
def test_cache_round_trip_is_identical_and_makes_no_request(tmp_path):
    cache_dir = str(tmp_path / "fulltext")
    resolve, fetch, calls = _seams(FULL_JATS)
    first = fr.fetch_fulltext("888", cache_dir=cache_dir,
                              resolve_pmcid=resolve, fetch_xml=fetch)
    assert first["source"] == "live"
    assert calls == {"resolve": 1, "fetch": 1}

    second = fr.fetch_fulltext("888", cache_dir=cache_dir,
                               session=_BoomSession(),
                               resolve_pmcid=_boom, fetch_xml=_boom)
    assert second["source"] == "cache"
    assert second == {**first, "source": "cache"}


# --------------------------------------------------------------------------
# Row 10 -- corrupt cache
# --------------------------------------------------------------------------
def test_corrupt_cache_is_ignored_refetched_and_rewritten(tmp_path):
    cache_dir = str(tmp_path / "fulltext")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "fulltext_pmid_999.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"pmid": "999", "sections": [truncated')

    resolve, fetch, calls = _seams(FULL_JATS)
    out = fr.fetch_fulltext("999", cache_dir=cache_dir, resolve_pmcid=resolve,
                            fetch_xml=fetch)
    assert out["source"] == "live"
    assert calls == {"resolve": 1, "fetch": 1}

    with open(path, encoding="utf-8") as f:
        rewritten = json.load(f)
    assert rewritten["pmid"] == "999"
    assert "source" not in rewritten
    again = fr.fetch_fulltext("999", cache_dir=cache_dir,
                              resolve_pmcid=_boom, fetch_xml=_boom)
    assert again["source"] == "cache"


def test_cache_entry_whose_hash_no_longer_matches_is_refetched(tmp_path):
    """F7 re-checks content_sha256 at construction, so a cache that violates the
    invariant must never be handed downstream."""
    cache_dir = str(tmp_path / "fulltext")
    resolve, fetch, _ = _seams(FULL_JATS)
    fr.fetch_fulltext("1000", cache_dir=cache_dir, resolve_pmcid=resolve,
                      fetch_xml=fetch)
    path = os.path.join(cache_dir, "fulltext_pmid_1000.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["sections"][0]["text"] = "tampered text"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    resolve2, fetch2, calls2 = _seams(FULL_JATS)
    out = fr.fetch_fulltext("1000", cache_dir=cache_dir, resolve_pmcid=resolve2,
                            fetch_xml=fetch2)
    assert out["source"] == "live"
    assert calls2 == {"resolve": 1, "fetch": 1}


def test_cache_entry_for_a_different_pmid_is_not_served(tmp_path):
    cache_dir = str(tmp_path / "fulltext")
    os.makedirs(cache_dir, exist_ok=True)
    resolve, fetch, _ = _seams(FULL_JATS)
    good = fr.fetch_fulltext("1001", cache_dir=cache_dir,
                             resolve_pmcid=resolve, fetch_xml=fetch)
    with open(os.path.join(cache_dir, "fulltext_pmid_1002.json"), "w",
              encoding="utf-8") as f:
        json.dump({k: v for k, v in good.items() if k != "source"}, f)

    resolve2, fetch2, calls2 = _seams(FULL_JATS)
    out = fr.fetch_fulltext("1002", cache_dir=cache_dir,
                            resolve_pmcid=resolve2, fetch_xml=fetch2)
    assert out["source"] == "live"
    assert out["pmid"] == "1002"
    assert calls2 == {"resolve": 1, "fetch": 1}


# --------------------------------------------------------------------------
# Row 11 -- the F7 SectionText convention, verbatim
# --------------------------------------------------------------------------
def test_every_section_is_accepted_by_f7_sectiontext_verbatim():
    resolve, fetch, _ = _seams(FULL_JATS)
    out = fr.fetch_fulltext("1111", resolve_pmcid=resolve, fetch_xml=fetch)
    accepted = [s for s in out["sections"]
                if s["label"] in {"results", "methods", "table", "figure"}]
    # 2 methods (parent + inherited subsection), 2 results, 2 tables, 1 figure.
    assert len(accepted) == 7
    for section in accepted:
        built = f7.SectionText(
            section_label=section["label"],
            text=section["text"],
            source_work_id=out["pmcid"],
            content_sha256=section["content_sha256"],
        )
        assert built.content_sha256 == _sha(section["text"])


def test_reader_labels_are_a_superset_of_the_f7_vocabulary():
    assert fr.SECTION_LABELS >= {"results", "methods", "table", "figure"}
    assert "other" in fr.SECTION_LABELS


# --------------------------------------------------------------------------
# Malformed PMIDs -- the only inputs that return None
# --------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", None, "   ", "PMC123", "12a", "-1", "1.0",
                                 "١٢٣"])
def test_malformed_pmid_returns_none_without_touching_a_seam(bad):
    assert fr.fetch_fulltext(bad, session=_BoomSession(), resolve_pmcid=_boom,
                             fetch_xml=_boom) is None


def test_int_and_padded_pmids_are_attempted():
    resolve, fetch, _ = _seams(FULL_JATS)
    assert fr.fetch_fulltext(1234, resolve_pmcid=resolve,
                             fetch_xml=fetch)["pmid"] == "1234"
    resolve2, fetch2, _ = _seams(FULL_JATS)
    assert fr.fetch_fulltext("  1234  ", resolve_pmcid=resolve2,
                             fetch_xml=fetch2)["pmid"] == "1234"


# --------------------------------------------------------------------------
# Invariants that hold on every path
# --------------------------------------------------------------------------
@pytest.mark.parametrize("xml_text,pmcid", [
    (FULL_JATS, "PMC1"), (FULL_JATS_NS, "PMC1"), (NO_BODY_JATS, "PMC1"),
    (DISCUSSION_ONLY_JATS, "PMC1"), (UNLABELLED_SEC_JATS, "PMC1"),
    (MALFORMED_JATS, "PMC1"), (FULL_JATS, ""),
])
def test_reasons_are_nonempty_exactly_when_incomplete(xml_text, pmcid):
    resolve, fetch, _ = _seams(xml_text, pmcid=pmcid)
    out = fr.fetch_fulltext("1212", resolve_pmcid=resolve, fetch_xml=fetch)
    complete = out["retrieval_complete"]
    assert complete is True or complete is False
    if complete is True:
        assert out["incomplete_reasons"] == []
    else:
        assert out["incomplete_reasons"]
        assert set(out["incomplete_reasons"]) <= fr.INCOMPLETE_REASONS
    assert all(s["label"] in fr.SECTION_LABELS for s in out["sections"])
    assert all(s["text"].strip() for s in out["sections"])
    assert out["source"] in ("cache", "live")
    json.dumps(out, ensure_ascii=False).encode("utf-8")


def test_completeness_requires_resolution_even_with_a_pmcid_shaped_string():
    """A PMCID that yields no document is not a complete retrieval."""
    out = fr.fetch_fulltext("1313", resolve_pmcid=lambda p: "PMC42",
                            fetch_xml=lambda c: None)
    assert out["pmcid"] == "PMC42"
    assert out["resolved"] is False
    assert out["retrieval_complete"] is False


def test_pure_container_sec_emits_no_blank_section():
    resolve, fetch, _ = _seams(
        '<?xml version="1.0"?><article><body>'
        '<sec sec-type="results"><title>Results</title>'
        '<sec><title>Primary</title><p>It worked.</p></sec></sec>'
        '</body></article>')
    out = fr.fetch_fulltext("1414", resolve_pmcid=resolve, fetch_xml=fetch)
    assert [(s["label"], s["title"]) for s in out["sections"]] == [
        ("results", "Primary")]


def test_sub_article_body_is_never_read_as_the_papers_own():
    resolve, fetch, _ = _seams(SUB_ARTICLE_JATS)
    out = fr.fetch_fulltext("1515", resolve_pmcid=resolve, fetch_xml=fetch)
    texts = [s["text"] for s in out["sections"]]
    assert texts == ["The article's own results."]


def test_figure_nested_in_a_paragraph_is_found_and_not_duplicated():
    resolve, fetch, _ = _seams(
        '<?xml version="1.0"?><article><body>'
        '<sec sec-type="results"><title>Results</title>'
        '<p>Leading text.'
        '<fig><label>Figure 1</label><caption><p>A caption.</p></caption></fig>'
        ' Trailing text.</p></sec></body></article>')
    out = fr.fetch_fulltext("1616", resolve_pmcid=resolve, fetch_xml=fetch)
    labels = [s["label"] for s in out["sections"]]
    assert labels == ["results", "figure"]
    assert "A caption." not in out["sections"][0]["text"]
    assert out["sections"][1]["text"] == "A caption."


def test_body_text_outside_any_sec_is_kept_as_other():
    resolve, fetch, _ = _seams(
        '<?xml version="1.0"?><article><body><p>Loose body text.</p>'
        '<sec sec-type="results"><title>Results</title><p>Found it.</p></sec>'
        '</body></article>')
    out = fr.fetch_fulltext("1717", resolve_pmcid=resolve, fetch_xml=fetch)
    assert [(s["label"], s["text"]) for s in out["sections"]] == [
        ("other", "Loose body text."), ("results", "Found it.")]


# --------------------------------------------------------------------------
# Labelling units
# --------------------------------------------------------------------------
@pytest.mark.parametrize("heading,label", [
    ("Results", "results"),
    ("3. Results", "results"),
    ("3.1. Results and Discussion", "results"),
    ("METHODS", "methods"),
    ("2 Materials and Methods", "methods"),
    ("Patients and methods", "methods"),
    ("Discussion", "discussion"),
    ("Introduction", "intro"),
    ("Background", "intro"),
    ("Acknowledgements", None),
    ("", None),
    ("5-year survival", None),
])
def test_heading_labels(heading, label):
    got = fr._label_from_heading(heading)
    if label is None:
        assert got is None
    else:
        assert got == label


@pytest.mark.parametrize("sec_type,label", [
    ("results", "results"),
    ("materials|methods", "methods"),
    ("subjects|methods", "methods"),
    ("intro", "intro"),
    ("discussion", "discussion"),
    ("supplementary-material", None),
    ("", None),
])
def test_sec_type_labels(sec_type, label):
    got = fr._label_from_sec_type(sec_type)
    if label is None:
        assert got is None
    else:
        assert got == label


def test_sec_type_wins_over_a_misleading_heading():
    resolve, fetch, _ = _seams(
        '<?xml version="1.0"?><article><body>'
        '<sec sec-type="methods"><title>What we did</title>'
        '<p>Enrolment.</p></sec></body></article>')
    out = fr.fetch_fulltext("1818", resolve_pmcid=resolve, fetch_xml=fetch)
    assert out["sections"][0]["label"] == "methods"


# --------------------------------------------------------------------------
# The LIVE seams, exercised offline through an injected session
# --------------------------------------------------------------------------
def test_live_default_seams_resolve_then_efetch_with_no_injection():
    session = _RoutedSession({ELINK: _elink_ok("7654321"),
                              EFETCH: _Resp(text=FULL_JATS)})
    out = fr.fetch_fulltext("1919", session=session, api_key="KEY")
    assert out["pmcid"] == "PMC7654321"

    urls = [url for url, _ in session.calls]
    assert urls == [ELINK, EFETCH]
    elink_params, efetch_params = session.calls[0][1], session.calls[1][1]
    assert elink_params["dbfrom"] == "pubmed" and elink_params["db"] == "pmc"
    assert efetch_params["db"] == "pmc"
    assert efetch_params["id"] == "7654321"          # digits, not "PMC7654321"
    assert efetch_params["api_key"] == "KEY"
    assert efetch_params["tool"] and efetch_params["email"]


def test_live_resolver_without_a_self_link_reports_no_pmcid():
    """ncbi_meta honors only pubmed_pmc; a refs-only linkset is not a PMCID."""
    session = _RoutedSession({ELINK: _Resp(payload={"linksets": [{"linksetdbs": [
        {"linkname": "pubmed_pmc_refs", "links": ["999999"]}]}]})})
    out = fr.fetch_fulltext("2020", session=session)
    assert out["incomplete_reasons"] == ["no_pmcid"]
    assert [url for url, _ in session.calls] == [ELINK]


def test_live_efetch_http_error_is_unparseable_not_a_crash():
    session = _RoutedSession({ELINK: _elink_ok(),
                              EFETCH: _Resp(status_code=500, text="")})
    out = fr.fetch_fulltext("2121", session=session)
    assert out["resolved"] is False
    assert out["incomplete_reasons"] == ["body_unparseable"]


def test_live_omits_api_key_when_absent():
    session = _RoutedSession({ELINK: _elink_ok(), EFETCH: _Resp(text=FULL_JATS)})
    fr.fetch_fulltext("2222", session=session)
    assert "api_key" not in session.calls[1][1]


def test_injected_seams_mean_no_session_is_ever_used():
    resolve, fetch, _ = _seams(FULL_JATS)
    out = fr.fetch_fulltext("2323", session=_BoomSession(),
                            resolve_pmcid=resolve, fetch_xml=fetch)
    # _BoomSession would have raised; reaching sections proves the seams ran.
    assert out["resolved"] is True
    assert out["sections"]


# ==========================================================================
# DEC-041 -- completeness means the body was retrieved, not that the paper
# is IMRAD. These rows see the REAL NONTRIVIAL_BODY_CHARS, never no_size_floor.
# ==========================================================================
def _essay_jats(paragraph_chars=400, paragraphs=4):
    """An essay-shaped paper: a real body, zero results and zero methods.

    This is the live-run shape that motivated DEC-041 -- both PMIDs that
    resolved were non-IMRAD, so the old rule reported no_results_or_methods on
    every one and the happy path never fired on real data."""
    filler = "This review argues the point at some length. " * 40
    body = "".join(
        f"<sec><title>{title}</title><p>{filler[:paragraph_chars]}</p></sec>"
        for title in ("Overview", "The argument", "Counterpoints",
                      "Conclusion")[:paragraphs])
    return ('<?xml version="1.0"?><pmc-articleset><article><body>'
            + body + "</body></article></pmc-articleset>")


def test_essay_style_paper_with_no_results_or_methods_is_complete():
    """The whole point: a review with a real body is a COMPLETE retrieval."""
    resolve, fetch, _ = _seams(_essay_jats())
    out = fr.fetch_fulltext("901", resolve_pmcid=resolve, fetch_xml=fetch)

    assert out["resolved"] is True
    assert out["retrieval_complete"] is True
    assert out["incomplete_reasons"] == []
    # Genuinely clears the real floor -- not an artefact of a lowered one.
    assert sum(len(s["text"]) for s in out["sections"]) >= fr.NONTRIVIAL_BODY_CHARS
    # Zero results, zero methods, and complete anyway.
    assert "results" not in out["sections_present"]
    assert "methods" not in out["sections_present"]


def test_a_stub_body_below_the_floor_is_incomplete():
    resolve, fetch, _ = _seams(
        MINIMAL_RESULTS_JATS.format(body="Too short to be a paper."))
    out = fr.fetch_fulltext("902", resolve_pmcid=resolve, fetch_xml=fetch)

    assert out["resolved"] is True
    assert out["retrieval_complete"] is False
    assert out["incomplete_reasons"] == ["body_too_small"]
    # Incomplete, yet the sections it DID find are still returned, never dropped.
    assert [s["label"] for s in out["sections"]] == ["results"]
    assert out["sections_present"] == ["results"]


def test_the_floor_is_a_named_tunable_constant():
    """A chosen floor, not a derived one; pinned so a silent edit is visible."""
    assert fr.NONTRIVIAL_BODY_CHARS == 1000


def test_a_body_that_only_just_clears_the_floor_is_complete():
    """Boundary: >= the floor, not > it."""
    exact = "x" * fr.NONTRIVIAL_BODY_CHARS
    resolve, fetch, _ = _seams(MINIMAL_RESULTS_JATS.format(body=exact))
    out = fr.fetch_fulltext("903", resolve_pmcid=resolve, fetch_xml=fetch)
    assert sum(len(s["text"]) for s in out["sections"]) == fr.NONTRIVIAL_BODY_CHARS
    assert out["retrieval_complete"] is True


def test_no_results_or_methods_is_no_longer_a_reason_at_all():
    assert not hasattr(fr, "REASON_NO_RESULTS_OR_METHODS")
    assert "no_results_or_methods" not in fr.INCOMPLETE_REASONS
    assert fr.INCOMPLETE_REASONS == {
        "no_pmcid", "no_body", "body_unparseable", "body_too_small"}


@pytest.mark.parametrize("heading", [
    "Conclusion", "Conclusions", "CONCLUSION", "Concluding remarks",
    "Summary", "4. Conclusion",
])
def test_closing_headings_are_discussion_not_other(heading):
    assert fr._label_from_heading(heading) == fr.LABEL_DISCUSSION


@pytest.mark.parametrize("heading,expected", [
    ("Introduction", fr.LABEL_INTRO),
    ("Results", fr.LABEL_RESULTS),
    ("Materials and Methods", fr.LABEL_METHODS),
    ("Discussion", fr.LABEL_DISCUSSION),
    ("A heading naming no canonical section", None),
])
def test_widening_the_label_map_moved_nothing_else(heading, expected):
    assert fr._label_from_heading(heading) == expected


def test_sections_present_matches_the_emitted_labels_exactly(no_size_floor):
    resolve, fetch, _ = _seams(FULL_JATS)
    out = fr.fetch_fulltext("904", resolve_pmcid=resolve, fetch_xml=fetch)
    assert out["sections_present"] == sorted({s["label"] for s in out["sections"]})
    assert out["sections_present"] == [
        "discussion", "figure", "intro", "methods", "results", "table"]


def test_sections_present_is_empty_when_nothing_was_emitted():
    resolve, fetch, _ = _seams(NO_BODY_JATS)
    out = fr.fetch_fulltext("905", resolve_pmcid=resolve, fetch_xml=fetch)
    assert out["sections_present"] == []
    assert out["incomplete_reasons"] == ["no_body"]


def test_a_pre_dec041_cache_entry_is_refetched_not_served(tmp_path):
    """A cache written under the old rule holds a False computed by the old
    predicate, and a reason that is no longer a member of INCOMPLETE_REASONS.
    Requiring sections_present retires those entries instead of serving them --
    otherwise the stale answer would survive on exactly the non-IMRAD papers
    this change exists to unblock."""
    cache_dir = tmp_path / "fulltext"
    cache_dir.mkdir()
    stale = {
        "pmid": "906", "pmcid": "PMC7654321", "resolved": True,
        "sections": [{"label": "discussion", "title": "Discussion",
                      "text": "We speculate.",
                      "content_sha256": _sha("We speculate.")}],
        "retrieval_complete": False,
        "incomplete_reasons": ["no_results_or_methods"],
        "sanitized_paths": [],
    }                                    # note: no sections_present
    (cache_dir / "fulltext_pmid_906.json").write_text(
        json.dumps(stale), encoding="utf-8")

    resolve, fetch, calls = _seams(_essay_jats())
    out = fr.fetch_fulltext("906", cache_dir=str(cache_dir),
                            resolve_pmcid=resolve, fetch_xml=fetch)

    assert calls["fetch"] == 1, "the stale entry was served instead of refetched"
    assert out["source"] == "live"
    assert out["retrieval_complete"] is True
    assert out["incomplete_reasons"] == []


def test_full_imrad_body_at_realistic_size_is_complete():
    """The ordinary case, under the real floor: results and methods present, a
    body of realistic length, complete."""
    resolve, fetch, _ = _seams(FULL_JATS_BIG)
    out = fr.fetch_fulltext("907", resolve_pmcid=resolve, fetch_xml=fetch)
    assert out["retrieval_complete"] is True
    assert out["incomplete_reasons"] == []
    assert "results" in out["sections_present"]
    assert "methods" in out["sections_present"]


def test_no_completeness_claim_hides_behind_a_lowered_floor():
    """Enforces the no_size_floor rule mechanically, so it cannot decay into a
    comment nobody reads.

    retrieval_complete and incomplete_reasons ARE the floor's output. Asserting
    either one while the floor is neutralized proves nothing about the shipped
    predicate, yet reads exactly like proof -- the most expensive kind of test to
    have, because it looks like coverage and is not. A row making a completeness
    claim must use a realistic fixture and face the real constant."""
    import ast

    source = os.path.join(os.path.dirname(__file__), "test_fulltext_reader.py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()

    offenders = []
    for node in ast.parse(text).body:
        if not (isinstance(node, ast.FunctionDef)
                and node.name.startswith("test_")
                and node.name != "test_no_completeness_claim_hides_behind_a_lowered_floor"):
            continue
        if "no_size_floor" not in [arg.arg for arg in node.args.args]:
            continue
        body = ast.get_source_segment(text, node) or ""
        claimed = [field for field in ("retrieval_complete", "incomplete_reasons")
                   if field in body]
        if claimed:
            offenders.append(f"{node.name} asserts {claimed} under no_size_floor")

    assert offenders == [], "; ".join(offenders)


def test_the_rule_is_written_on_the_fixture_itself():
    """The enforcement above is only half of it: the fixture has to SAY why, or
    the next person deletes the guard rather than the violation."""
    fixture_doc = no_size_floor.__doc__ or ""
    assert "MUST NOT" in fixture_doc
    assert "retrieval_complete" in fixture_doc
    assert "incomplete_reasons" in fixture_doc
