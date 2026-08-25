import sys, importlib; sys.path.insert(0,"/home/claude")
from cre.f1.schema import Reference, ClaimedRef, RetrievedRecord
from cre.f1 import lookup, schema as S
from cre.f1.decide import decide

# parser still works
xml='<article><back><ref-list><ref id="r1"><element-citation><person-group><name><surname>Smith</surname></name></person-group><article-title>A real study</article-title><source>J</source><year>2021</year><pub-id pub-id-type="pmid">123</pub-id></element-citation></ref></ref-list></back></article>'
open("/tmp/d.xml","w").write(xml)
from cre.f1.parser import parse_pmc_xml
refs=parse_pmc_xml("/tmp/d.xml",source_pmcid="PMC1")
assert refs[0].claimed.title=="A real study" and refs[0].claimed.claimed_pmid=="123"
print("PASS parser")

# decision branches -> pipeline states
u=Reference("u","",ClaimedRef(title="x")); u.log.pmid_present=False
assert decide(u,False,None,None).label==S.UNVERIFIABLE
c=Reference("c","",ClaimedRef(title="x",claimed_pmid="1")); c.log.pmid_present=True; c.log.title_similarity=99
assert decide(c,False,None,None).label==S.CLEARED
# Dead/mismatched PMID + a title none of the three searches matched -> HELD.
# That evidence supports neither F1 (no such work exists) nor F2 (it names no
# work at all), so it goes to a human (decide.py, 2026-08-25).
fab=Reference("fb","",ClaimedRef(title="x",claimed_pmid="1")); fab.log.pmid_present=True; fab.log.pmid_resolved=True
assert decide(fab,True,S.V_FABRICATION,{"pubmed":10,"crossref":0,"openalex":0}).label==S.HUMAN_REVIEW
# ...but an unanswered search holds it: the absence route needs every database
# to have replied.
fabh=Reference("fbh","",ClaimedRef(title="x",claimed_pmid="1")); fabh.log.pmid_present=True; fabh.log.pmid_resolved=True
assert decide(fabh,True,S.V_FABRICATION,{"pubmed":10,"crossref":0,"openalex":None}).label==S.HUMAN_REVIEW
f2=Reference("f2","",ClaimedRef(title="x",claimed_pmid="1")); f2.log.pmid_present=True; f2.log.pmid_resolved=True
assert decide(f2,True,S.V_REFERENCE_ERROR,{"pubmed":97,"crossref":0,"openalex":0}).label==S.F2
print("PASS decision branches")

# end-to-end mocked, now emitting a prediction record
runmod=importlib.import_module("cre.f1.run"); confmod=importlib.import_module("cre.f1.confirm")
# These patches are applied to the MODULES, not via monkeypatch, and this file
# runs at COLLECTION time -- so without the restore below they leak into every
# other test in the session (they silently disabled the live-path fakes in
# test_f1_fabrication_guard.py, which passed alone and failed in the suite).
_saved={"fetch_pubmed":runmod.fetch_pubmed,
        "search_pubmed":confmod.search_pubmed,
        "search_crossref":confmod.search_crossref,
        "search_openalex":confmod.search_openalex}
try:
    runmod.fetch_pubmed=lambda pmid,*a,**k: RetrievedRecord(resolved=True,title="Unrelated real paper",pmid=pmid,transport_status=S.FETCH_ANSWERED_RECORD)
    confmod.search_pubmed=lambda *a,**k:5.0; confmod.search_crossref=lambda *a,**k:0.0; confmod.search_openalex=lambda *a,**k:0.0
    r=Reference("e2e","",ClaimedRef(title="Fabricated quantum neuro synthesis",claimed_pmid="123"))
    runmod.process_reference(r, lambda p:'{"verdict":"fabrication","reason":"invented"}', ncbi_key="", session=None)
    assert r.label==S.HUMAN_REVIEW
    pred=r.to_prediction()
    assert pred.evidence["decided_by"]=="confirm_not_found_human_review"
finally:
    runmod.fetch_pubmed=_saved["fetch_pubmed"]
    confmod.search_pubmed=_saved["search_pubmed"]
    confmod.search_crossref=_saved["search_crossref"]
    confmod.search_openalex=_saved["search_openalex"]
print("PASS end-to-end (mocked) -> prediction", pred.label)

# package imports clean, no shadow
import cre.f1, cre.f1.run, cre.f1.decide, cre.f1.confirm
assert callable(cre.f1.run_pipeline) and callable(cre.f1.decide.decide)
print("PASS imports / no shadow")
print("\nALL PIPELINE TESTS PASSED")
