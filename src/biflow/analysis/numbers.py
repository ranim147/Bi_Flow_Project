"""Vérification des nombres cités dans un texte (insight rédigé par le LLM ou par gabarit).

Règle : tout nombre du texte doit correspondre à une valeur calculée (les `evidence`),
à l'arrondi d'affichage près. Le LLM rédige ; il ne produit jamais un chiffre.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# 1 234,5 | 1234.5 | 12 % | -3,2 ; les espaces (fines) servent de séparateur de milliers
_NUMBER = re.compile(r"(?<![\w.,])-?\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|(?<![\w.,])-?\d+(?:[.,]\d+)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _numbers_with_decimals(text: str) -> list[tuple[float, int]]:
    text = _DATE.sub(" ", text)  # les dates ne sont pas des mesures
    out = []
    for raw in _NUMBER.findall(text):
        cleaned = re.sub(r"[  ]", "", raw).replace(",", ".")
        decimals = len(cleaned.split(".")[1]) if "." in cleaned else 0
        out.append((float(cleaned), decimals))
    return out


def extract_numbers(text: str) -> list[float]:
    return [n for n, _ in _numbers_with_decimals(text)]


def _flatten(values: Any) -> Iterable[float]:
    if isinstance(values, bool):
        return
    if isinstance(values, (int, float)):
        yield float(values)
    elif isinstance(values, dict):
        for v in values.values():
            yield from _flatten(v)
    elif isinstance(values, (list, tuple)):
        for v in values:
            yield from _flatten(v)
    elif isinstance(values, str):
        yield from (float(n) for n in extract_numbers(values))


def unsupported_numbers(text: str, evidence: dict[str, Any], allowed: Iterable[float] = ()) -> list[float]:
    """Nombres du texte qui ne correspondent à aucune valeur de `evidence` (ni de `allowed`)."""
    known = [*_flatten(evidence), *allowed]
    candidates = {*known, *(abs(k) for k in known), *(100 * k for k in known if abs(k) <= 1)}

    def supported(n: float, decimals: int) -> bool:
        tolerance = 0.5 * 10 ** -decimals + 1e-9  # arrondi d'affichage
        return any(abs(n - k) <= tolerance for k in candidates)

    return [n for n, d in _numbers_with_decimals(text) if not supported(n, d)]
