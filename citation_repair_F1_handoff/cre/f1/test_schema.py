import sys; sys.path.insert(0,"/home/claude")
from cre.f1.schema import (AtomicClaim, GoldRecord, PredictionRecord, EvalRecord,
    CitedPaper, SourcePaper, Repair, Annotation, check_f6_invariant,
    pipeline_state_to_taxonomy, F1, F2, F6, ACCURATE, CLEARED, UNVERIFIABLE,
    HUMAN_REVIEW)
from cre.f1.schema import Reference, ClaimedRef

# F6 invariant
ac_mixed=[AtomicClaim("a",True),AtomicClaim("b",False)]
ac_all=[AtomicClaim("a",True),AtomicClaim("b",True)]
assert check_f6_invariant(F6, ac_mixed) is None              # F6 + mixed -> ok
assert check_f6_invariant(F6, ac_all) is not None            # F6 + all supported -> error
assert check_f6_invariant(ACCURATE, ac_mixed) is not None    # accurate + an unsupported -> error
assert check_f6_invariant(ACCURATE, ac_all) is None          # accurate + all supported -> ok
assert check_f6_invariant(F1, []) is None                    # no claims -> unconstrained
print("PASS F6 invariant")

# GoldRecord.validate enforces it
g=GoldRecord("c1","Aspirin reduces mortality [12].","[12]",
    CitedPaper(pmid="12345678",title="X"),SourcePaper(pmid="999"),
    label=F6, atomic_claims=ac_mixed)
g.validate()  # ok
bad=GoldRecord("c2","x","[1]",CitedPaper(),SourcePaper(),label=F6,atomic_claims=ac_all)
try:
    bad.validate(); raise SystemExit("invariant not enforced")
except ValueError as e:
    assert "F6" in str(e)
# bad taxonomy label
try:
    GoldRecord("c3","x","[1]",CitedPaper(),SourcePaper(),label="supported").validate()
    raise SystemExit("bad label not caught")
except ValueError as e:
    assert "taxonomy" in str(e)
print("PASS GoldRecord.validate")

# pipeline state -> taxonomy
assert pipeline_state_to_taxonomy(F1)==F1
assert pipeline_state_to_taxonomy(CLEARED)==ACCURATE
assert pipeline_state_to_taxonomy(UNVERIFIABLE) is None
assert pipeline_state_to_taxonomy(HUMAN_REVIEW) is None
print("PASS state->taxonomy")

# Reference.to_prediction emits taxonomy label + evidence
r=Reference("c1","cit",ClaimedRef(title="t",claimed_pmid="1"))
r.label=F1; r.confidence="HIGH"; r.rationale="not found"
r.log.db_hits={"pubmed":0,"crossref":0,"openalex":0}; r.log.decided_by="confirm_not_found_f1"
pred=r.to_prediction()
assert pred.label==F1
assert pred.annotations[0].confidence==0.95
assert pred.evidence["db_hits"]=={"pubmed":0,"crossref":0,"openalex":0}
assert pred.evidence["pipeline_state"]==F1
print("PASS to_prediction")

# Eval scoring: label + repair
gold=GoldRecord("c1","x","[1]",CitedPaper(),SourcePaper(),label=F2,
    repair=Repair(action="replace",recommended_references=[{"pmid":"555"}]))
pred_ok=PredictionRecord("c1",label=F2,repair=Repair(action="replace",
    recommended_references=[{"pmid":"555"}]),
    annotations=[Annotation("llm",F2,confidence=0.9)])
ev=EvalRecord.score("c1",gold,pred_ok)
assert ev.evaluation["label_correct"] and ev.evaluation["exact_match"]
assert ev.evaluation["repair_correct"] is True
# wrong repair target
pred_bad=PredictionRecord("c1",label=F2,repair=Repair(action="replace",
    recommended_references=[{"pmid":"000"}]))
assert EvalRecord.score("c1",gold,pred_bad).evaluation["repair_correct"] is False
# no gold repair -> repair_correct None
gold2=GoldRecord("c2","x","[1]",CitedPaper(),SourcePaper(),label=ACCURATE)
pred2=PredictionRecord("c2",label=ACCURATE)
assert EvalRecord.score("c2",gold2,pred2).evaluation["repair_correct"] is None
# label mismatch
gold3=GoldRecord("c3","x","[1]",CitedPaper(),SourcePaper(),label=F1)
pred3=PredictionRecord("c3",label=ACCURATE)
assert EvalRecord.score("c3",gold3,pred3).evaluation["label_correct"] is False
print("PASS eval scoring (label + repair)")

print("\nALL SCHEMA TESTS PASSED")
