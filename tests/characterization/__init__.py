"""Golden-master tests: the tripwire on end-to-end behaviour.

A package rather than a bare directory so the shared recorded-seam helpers can
be imported by name instead of re-derived in each module -- a fixture rebuilt
per file is a fixture that can drift between files.
"""
