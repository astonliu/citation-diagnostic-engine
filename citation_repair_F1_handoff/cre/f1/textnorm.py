"""Shared deterministic normalization for bibliographic text.

The cheap lookup and the structured F2 matcher used to normalize the same
title differently.  In particular, only the cheap path expanded Greek letters
and stripped embedded HTML.  That made the audit path treat ``beta1`` and
``Β1`` as different works.  Keep the lossy, Unicode-level folding here; each
caller still owns its token/punctuation policy afterwards.
"""
from __future__ import annotations

import html
import re
import unicodedata


GREEK_NAMES = {
    "α": "alpha", "Α": "alpha", "β": "beta", "Β": "beta",
    "γ": "gamma", "Γ": "gamma", "δ": "delta", "Δ": "delta",
    "ε": "epsilon", "Ε": "epsilon", "ζ": "zeta", "Ζ": "zeta",
    "η": "eta", "Η": "eta", "θ": "theta", "Θ": "theta",
    "ι": "iota", "Ι": "iota", "κ": "kappa", "Κ": "kappa",
    "λ": "lambda", "Λ": "lambda", "μ": "mu", "Μ": "mu",
    "ν": "nu", "Ν": "nu", "ξ": "xi", "Ξ": "xi",
    "ο": "omicron", "Ο": "omicron", "π": "pi", "Π": "pi",
    "ρ": "rho", "Ρ": "rho", "σ": "sigma", "ς": "sigma",
    "Σ": "sigma", "τ": "tau", "Τ": "tau", "υ": "upsilon",
    "Υ": "upsilon", "φ": "phi", "Φ": "phi", "χ": "chi",
    "Χ": "chi", "ψ": "psi", "Ψ": "psi", "ω": "omega",
    "Ω": "omega", "µ": "mu",
}

_TAG_RE = re.compile(r"<[^>]+>")
_GREEK_RE = re.compile("|".join(map(re.escape, GREEK_NAMES)))
_DASH_RE = re.compile("[‐‑‒–—―−]")
_PAREN_CHARGE_RE = re.compile(
    r"(?<=[A-Za-z])\s*\(\s*(\d*)\s*([+-])\s*\)")
_VALENCE_CHARGE_RE = re.compile(
    r"(?<=[A-Za-z])\s*(\d+)\s*([+-])(?=\s|$|[),.;:/])")
_MONOVALENT_CHARGE_RE = re.compile(
    r"(?<=[A-Za-z])([+-])(?=\s|$|[),.;:/])")
# ``_text`` joins JATS inline/superscript nodes with spaces: ``Na<sup>+</sup>``
# therefore arrives as ``Na +``.  Restrict the spaced monovalent form to a
# capitalized chemical element token so ordinary prose such as ``A - B`` is not
# rewritten as an ion.
_SPACED_ELEMENT_CHARGE_RE = re.compile(
    r"(?<![A-Za-z])([A-Z][a-z]?)\s+([+-])(?=\s|$|[),.;:/])")


def fold_bibliographic_text(text: str) -> str:
    """Unescape markup, name Greek letters, fold accents and normalize dashes."""
    if not text:
        return ""
    text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    text = _GREEK_RE.sub(lambda m: GREEK_NAMES[m.group()], text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _DASH_RE.sub("-", text)


def fold_chemical_charges(text: str) -> str:
    """Preserve ionic charge semantics while folding parenthesized typography.

    ``Mn(2+)``, ``Mn2+`` and JATS-derived ``Mn 2+`` become ``Mn2plus``;
    monovalent ``Na+``/``Na-`` stay distinct as ``Naplus``/``Naminus``.  The
    letter/element guards avoid rewriting ordinary numeric ranges and dashes.
    """
    def repl(match) -> str:
        return match.group(1) + ("plus" if match.group(2) == "+" else "minus")

    text = _PAREN_CHARGE_RE.sub(repl, text or "")
    text = _VALENCE_CHARGE_RE.sub(repl, text)
    text = _MONOVALENT_CHARGE_RE.sub(
        lambda m: "plus" if m.group(1) == "+" else "minus", text)
    return _SPACED_ELEMENT_CHARGE_RE.sub(
        lambda m: (m.group(0) if m.group(1) == "A" else
                   m.group(1) + ("plus" if m.group(2) == "+" else "minus")),
        text)
