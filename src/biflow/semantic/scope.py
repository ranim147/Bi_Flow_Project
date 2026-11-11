"""Périmètre des questions : ce que les données permettent, ou non, de démontrer.

Les données décrivent des demandes (type, lieu, dates, statut). Elles ne contiennent ni
effectifs, ni budget, ni capacité des agences, ni causes. Une question qui demande une
cause, un besoin en personnel ou une prévision reçoit donc un refus motivé AVANT tout appel
au LLM : une requête SQL donnerait un chiffre, mais pas la réponse à la question posée.
"""

from __future__ import annotations

import re

from biflow.semantic.text_to_sql import _norm

_OUT_OF_SCOPE = [
    (r"\bpourquoi\b|\bcause|\bexpliqu\w* (la|le|les) (hausse|baisse|retard)|\bwhy\b",
     "cause",
     ("les données montrent des volumes et des délais, pas leurs causes ; une cause doit être "
     "établie par une enquête métier (le chiffre peut seulement orienter cette enquête)")),
    (r"\brecrut|\bembauch|\beffectif|\bpersonnel|\bcombien d.agents|\bstaff|\bbudget",
     "moyens",
     ("aucune donnée d'effectifs, de capacité ou de budget n'est disponible : impossible de "
     "chiffrer un besoin en personnel ou en moyens")),
    (r"\bprevo|\bpredi|\bprevision|\bforecast|\bannee prochaine|\bmois prochain|\ben 20[3-9]\d\b",
     "prévision",
     "le MVP mesure le passé ; il ne produit pas de prévision"),
]


def out_of_scope(question: str) -> tuple[str, str] | None:
    """(motif, explication) si la question sort du périmètre des données, sinon None."""
    q = _norm(question)
    for pattern, reason, explanation in _OUT_OF_SCOPE:
        if re.search(pattern, q):
            return reason, explanation
    return None
