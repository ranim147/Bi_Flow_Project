"""Contrôle de la question avant envoi au LLM : elle peut contenir un nom, une adresse...

Détection volontairement simple (motifs) : ce qui ressemble à une donnée personnelle est
remplacé par [MASQUÉ]. Les noms propres isolés ne sont pas détectables ainsi : c'est une
limite connue, à couvrir par l'AIPD avant tout usage sur données réelles.
"""

from __future__ import annotations

import re

MASK = "[MASQUÉ]"

_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "téléphone": re.compile(r"(?:\+?\d[\s.-]?){9,}\d"),
    "adresse": re.compile(
        r"\b\d{1,5}(?:-\d{1,4})?\s+(?:[A-Za-zÀ-ÿ'.-]+\s+){0,4}"
        r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|place|pl|drive|dr|lane|ln|parkway|pkwy"
        r"|rue|avenue|boulevard|chemin|allée|impasse)\b\.?",
        re.IGNORECASE,
    ),
    "clé de demande": re.compile(r"\b\d{8}\b"),
}


def redact(text: str) -> tuple[str, list[str]]:
    """Retourne (texte masqué, types de données personnelles trouvés)."""
    found = []
    for label, pattern in _PATTERNS.items():
        if pattern.search(text):
            found.append(label)
            text = pattern.sub(MASK, text)
    return text, found
