# F1 exact-DOI scope expansion

**Date:** 2026-08-20

## Decision

References without a PMID are no longer categorically excluded from F1 when
they carry a printed DOI. The DOI is normalized mechanically and checked
exactly; the system never searches edited or guessed DOI strings.

- Exact DOI found: returned authority metadata enters the existing F2 metadata
  comparison. A material mismatch is F2, not F1.
- Exact DOI absent from the DOI Foundation resolver and every configured
  metadata provider, and the complete
  independent title/work sweep also finds nothing: F1.
- Any provider error needed to establish absence, conflicting response,
  incomplete title sweep, missing DOI, or ambiguity: hold. A positive exact
  authority response still proves existence.

The DOI Foundation resolver, Crossref, DataCite, and OpenAlex DOI checks run
concurrently. The independent PubMed, Crossref, and OpenAlex title checks also
run concurrently. Results are consumed in fixed provider order so concurrency
does not change output order. A positive DOI existence response without usable
metadata prevents F1 but holds F2 rather than guessing a comparison.

## F2 measurement provenance

The seed-47 F2 figure of record was measured on commit
`d90196a7be3fe64e1eb9225a17b2ce4a26e0ecd1` on 2026-08-14. Seed 47 was drawn at
`8cf408f8478ad760f1527635103f1d7c1e9cc6ce` on 2026-08-13. The recorded figure
is `74/80 = 0.9250`.

This change does not alter the existing PMID-backed F2 matcher rules,
thresholds, or seed-47 verdict path. It expands eligibility to the previously
excluded no-PMID/exact-DOI population. Therefore the historical figure remains
the provenance record for the scope on which it was measured; it is not claimed
as a new precision estimate for the expanded DOI-only population.
