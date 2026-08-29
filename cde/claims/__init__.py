"""Band 2 front end -- claims, evidence, spans and coverage.

Everything between "this reference cleared Band 1" and "here is what the citing
sentence asserted, and whether the cited work established it". The
discriminators in :mod:`cde.diagnose` consume what this subpackage produces and
never re-derive it.

``band_prompts`` is here and is FROZEN: its content is pinned by blob OID inside
``cde.freeze.semantic_validator_v1`` and both prompt-package seals. It may be
moved, because git addresses content and a move preserves the OID. It may not be
edited -- not for an import rewrite, not for whitespace. Unsealing it is a
deliberate operation, not a side effect of tidying.
"""
