import sys, json, tempfile, os; sys.path.insert(0,"/home/claude")
from cre.f1.adjudicate import Adjudicator
from cre.f1 import schema as S

d = tempfile.mkdtemp()
preds = os.path.join(d,"preds.jsonl"); logs = os.path.join(d,"logs.jsonl")

# 3 predictions: one F1 candidate, one F2 candidate, one accurate (should be skipped)
P = [
 {"citation_id":"c1","label":"F1","rationale":"not found anywhere",
  "evidence":{"decided_by":"confirm_not_found_f1","db_hits":{"pubmed":0,"crossref":0,"openalex":0}},
  "annotations":[{"annotator_id":"llm","label":"F1","confidence":0.95}]},
 {"citation_id":"c2","label":"F2","rationale":"found under different id",
  "evidence":{"decided_by":"confirm_found_f2","db_hits":{"pubmed":97}},
  "annotations":[{"annotator_id":"llm","label":"F2","confidence":0.7}]},
 {"citation_id":"c3","label":"accurate","rationale":"matched",
  "evidence":{"decided_by":"metadata_match"},"annotations":[]},
]
L = [
 {"citation_id":"c1","label":"F1","claimed":{"title":"Invented quantum neuro synthesis","authors":["Smith"],"year":2024,"claimed_pmid":"111"},
  "retrieved":{"resolved":True,"title":"A real unrelated paper","authors":["Lee"],"pmid":"111"},
  "log":{"title_similarity":12.0,"author_match":False,"year_match":None,"llm_verdict":"fabrication","db_hits":{"pubmed":0,"crossref":0,"openalex":0}},
  "source_pmid":"999","source_title":"Citing review"},
 {"citation_id":"c2","label":"F2","claimed":{"title":"Real study of widgets","authors":["Jones"],"year":2021,"claimed_pmid":"222"},
  "retrieved":{"resolved":True,"title":"Totally different paper","authors":["Kim"],"pmid":"222"},
  "log":{"title_similarity":20.0,"author_match":False,"llm_verdict":"reference_error","db_hits":{"pubmed":97}},
  "source_pmid":"999","source_title":"Citing review"},
 {"citation_id":"c3","label":"accurate","claimed":{},"retrieved":{},"log":{}},
]
open(preds,"w").write("\n".join(json.dumps(x) for x in P))
open(logs,"w").write("\n".join(json.dumps(x) for x in L))

# only F1/F2 surfaced
adj = Adjudicator(preds, logs)
assert {c.citation_id for c in adj.candidates}=={"c1","c2"}, [c.citation_id for c in adj.candidates]
print("PASS surfacing (accurate skipped)")

# evidence view renders the key fields
v = adj.candidates[0].evidence_view()
assert "Invented quantum" in v and "did not resolve" not in v and "similarity: 12.0" in v
print("PASS evidence_view")

# headless worklist round-trip
wl = os.path.join(d,"wl.csv"); adj.write_worklist(wl)
rows = list(__import__("csv").DictReader(open(wl)))
assert len(rows)==2 and rows[0]["predicted_label"]=="F1"
# fill verdicts: confirm c1 as F1, confirm c2 but relabel to F1 (human decides it's fabricated)
import csv as _csv
for r in rows:
    r["verdict"]="confirm"
    if r["citation_id"]=="c2": r["final_label"]="F1"; r["note"]="actually fabricated on review"
with open(wl,"w",newline="") as f:
    w=_csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
adj.apply_worklist(wl)
gold_path=os.path.join(d,"gold.jsonl"); n=adj.save_gold(gold_path)
assert n==2, n
gold=[json.loads(l) for l in open(gold_path)]
labels={g["citation_id"]:g["label"] for g in gold}
assert labels=={"c1":"F1","c2":"F1"}, labels
assert gold[1]["rationale"]=="actually fabricated on review"
assert gold[0]["annotations"][0]["annotator_id"]=="human_1"
print("PASS headless worklist + relabel + validated gold")

# summary
s=adj.summary(); assert s["confirmed"]==2 and s["gold_written"]==2, s
print("PASS summary", s)

# interactive path with mocked input: reject c1, confirm c2
adj2 = Adjudicator(preds, logs)
answers=iter(["reject","confirm"])
adj2.review(input_fn=lambda *a: next(answers), print_fn=lambda *a: None)
assert adj2.summary()["confirmed"]==1 and adj2.summary()["rejected"]==1
print("PASS interactive (mocked input)")

# invariant still bites: try to force an accurate label onto an F6-with-unsupported-claim via to_gold
from cre.f1.adjudicate import Candidate
bad=Candidate("x",{"label":"F6","rationale":""},
   {"claimed":{"title":"t"},"retrieved":{},"log":{}})
bad.final_label="accurate"; bad.verdict="confirm"
# accurate with no atomic claims is fine; the invariant only bites with claims present -> construct directly
from cre.f1.schema import GoldRecord, CitedPaper, SourcePaper, AtomicClaim
try:
    GoldRecord("z","c","[1]",CitedPaper(),SourcePaper(),label="accurate",
        atomic_claims=[AtomicClaim("a",False)]).validate()
    raise SystemExit("invariant did not fire")
except ValueError: pass
print("PASS invariant enforced on gold write")
print("\nALL ADJUDICATOR TESTS PASSED")
