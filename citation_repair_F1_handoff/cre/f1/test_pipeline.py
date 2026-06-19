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
fab=Reference("fb","",ClaimedRef(title="x",claimed_pmid="1")); fab.log.pmid_present=True; fab.log.pmid_resolved=True
assert decide(fab,True,S.V_FABRICATION,{"pubmed":10,"crossref":0,"openalex":None}).label==S.F1
f2=Reference("f2","",ClaimedRef(title="x",claimed_pmid="1")); f2.log.pmid_present=True; f2.log.pmid_resolved=True
assert decide(f2,True,S.V_REFERENCE_ERROR,{"pubmed":97,"crossref":0,"openalex":0}).label==S.F2
print("PASS decision branches")

# end-to-end mocked, now emitting a prediction record
runmod=importlib.import_module("cre.f1.run"); confmod=importlib.import_module("cre.f1.confirm")
runmod.fetch_pubmed=lambda pmid,*a,**k: RetrievedRecord(resolved=True,title="Unrelated real paper",pmid=pmid)
confmod.search_pubmed=lambda *a,**k:5.0; confmod.search_crossref=lambda *a,**k:0.0; confmod.search_openalex=lambda *a,**k:0.0
r=Reference("e2e","",ClaimedRef(title="Fabricated quantum neuro synthesis",claimed_pmid="123"))
runmod.process_reference(r, lambda p:'{"verdict":"fabrication","reason":"invented"}', ncbi_key="", session=None)
assert r.label==S.F1
pred=r.to_prediction()
assert pred.label==S.F1 and pred.evidence["decided_by"]=="confirm_not_found_f1"
print("PASS end-to-end (mocked) -> prediction", pred.label)

# package imports clean, no shadow
import cre.f1, cre.f1.run, cre.f1.decide, cre.f1.confirm
assert callable(cre.f1.run_pipeline) and callable(cre.f1.decide.decide)
print("PASS imports / no shadow")
print("\nALL PIPELINE TESTS PASSED")
