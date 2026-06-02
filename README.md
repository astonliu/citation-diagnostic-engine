# Citation Repair Engine

Fine-grained biomedical citation diagnosis and evidence-backed repair.

## Overview

The Citation Repair Engine is a dual-mode NLP system that (a) diagnoses why a citation fails using an 8-category failure-mode taxonomy (F1–F8), (b) proposes evidence-backed replacement citations grounded in PubMed/Crossref retrieval, and (c) generates citations from scratch when a claim is provided without one.

**Primary venue target:** *Bioinformatics* (Oxford) or *JAMIA Open*  
**Dataset:** CitationRepair-1000 (releasing on HuggingFace)  
**Preregistration:** See `PREREGISTRATION.md` — committed before annotation began.

## Repository Structure

```
/data        — annotated dataset files and sourcing metadata
/src         — pipeline source code
/notebooks   — exploration and analysis notebooks
/eval        — evaluation scripts and results
/docs        — paper drafts and supplementary materials
/prompts     — versioned LLM prompt templates
```

## Preregistration

The analysis plan (claims, baselines, IAA thresholds, statistical protocol) is preregistered in `PREREGISTRATION.md`. The git commit hash of that file predates all annotation and is cited in the manuscript.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# populate .env with your API keys (never commit .env)
```

## Citation

If you use CitationRepair-1000, please cite the accompanying paper (forthcoming).

## License

MIT
