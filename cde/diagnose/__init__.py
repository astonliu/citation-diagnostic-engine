"""The typed decision engine and the five discriminators.

``engine`` is pure: it does no retrieval and asks no model. Injected assessors
produce strictly typed evidence, and ``decide_judgment`` applies the
F7 > F6 > F4 > F3 > F5 hierarchy deterministically. That separation is what
makes the hierarchy testable without a network, and it is why a discriminator
that cannot reach its evidence returns an abstention rather than a verdict --
the engine has no way to invent one.

``pipeline`` is the orchestrator that wires the discriminators to real evidence
and writes the durable per-pair record.
"""
