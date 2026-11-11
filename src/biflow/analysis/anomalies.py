"""Tendances et anomalies sur une série journalière : STL + écart robuste à la normale.

STL (statsmodels) sépare tendance, saisonnalité hebdomadaire et résidu ; un jour est
anormal si son résidu est loin de la normale (z-score robuste : médiane / MAD). Pour des
comptages, l'échelle ne descend pas sous l'écart-type de Poisson (racine de l'attendu) :
STL absorbe une partie du bruit et sous-estimerait sinon la variabilité normale.
Méthode choisie pour être facile à expliquer : « ce jour-là, X demandes au lieu de Y attendues ».
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

PERIOD = 7          # saisonnalité hebdomadaire
MIN_POINTS = 2 * PERIOD
# Lissages STL plus longs que les valeurs par défaut (7 et 15) : sur 2 mois de données, les
# valeurs par défaut suivent trop le bruit d'une zone et créent de fausses anomalies.
STL_SEASONAL = 13
STL_TREND = 29
Z_THRESHOLD = 5.0   # un doublement du volume d'une zone de 25 demandes/jour donne z = 5


@dataclass
class Anomaly:
    day: str
    value: float
    expected: float
    z_score: float
    direction: str  # "hausse" | "baisse"
    zone: str = "ALL"

    def to_dict(self) -> dict:
        return asdict(self)


def robust_z(values: np.ndarray, floor: np.ndarray | float = 0.0) -> np.ndarray:
    """(valeur - médiane) / échelle, échelle = 1,4826 x MAD, au moins `floor`."""
    median = np.median(values)
    scale = np.maximum(1.4826 * np.median(np.abs(values - median)), floor)
    if np.all(scale == 0):
        return np.zeros_like(values, dtype=float)
    return (values - median) / np.where(scale == 0, np.inf, scale)


def daily_series(days: pd.Series) -> pd.Series:
    """Nombre de demandes par jour, jours sans demande inclus (0)."""
    counts = pd.to_datetime(days).dt.normalize().value_counts().sort_index()
    if counts.empty:
        return counts.astype(float)
    full = pd.date_range(counts.index.min(), counts.index.max(), freq="D")
    return counts.reindex(full, fill_value=0).astype(float)


def detect_anomalies(series: pd.Series, z_threshold: float = Z_THRESHOLD,
                     zone: str = "ALL") -> tuple[list[Anomaly], str]:
    """Retourne (anomalies, méthode utilisée). Série trop courte : aucune détection."""
    if len(series) < PERIOD:
        return [], "insuffisant"
    if len(series) >= MIN_POINTS:
        trend = STL_TREND if len(series) > STL_TREND else None
        fit = STL(series, period=PERIOD, seasonal=STL_SEASONAL, trend=trend, robust=True).fit()
        expected = (fit.trend + fit.seasonal).to_numpy()
        method = "stl"
    else:
        expected = np.full(len(series), float(series.median()))
        method = "z-score"
    resid = series.to_numpy() - expected
    z = robust_z(resid, floor=np.sqrt(np.maximum(expected, 1.0)))
    anomalies = [
        Anomaly(day=day.date().isoformat(), value=float(v), expected=round(float(e), 1),
                z_score=round(float(zz), 2), direction="hausse" if zz > 0 else "baisse", zone=zone)
        for day, v, e, zz in zip(series.index, series.to_numpy(), expected, z, strict=True)
        if abs(zz) > z_threshold
    ]
    return anomalies, method


def trend_window(n_days: int) -> int | None:
    """Fenêtre de comparaison : 365 jours si la série couvre 2 ans (saison neutralisée),
    sinon 28 jours si elle couvre 8 semaines, sinon 7 jours ; None si trop courte."""
    for window in (365, 28, PERIOD):
        if n_days >= 2 * window:
            return window
    return None


def trend_pct(series: pd.Series) -> tuple[float, int] | None:
    """(évolution en %, fenêtre en jours) entre la moyenne journalière des `fenêtre` premiers
    et des `fenêtre` derniers jours. Avec 365 jours : dernière année contre la précédente."""
    window = trend_window(len(series))
    if window is None:
        return None
    if window == 365:
        first, last = series.iloc[-2 * window:-window].mean(), series.iloc[-window:].mean()
    else:
        first, last = series.iloc[:window].mean(), series.iloc[-window:].mean()
    return None if first == 0 else (round(100 * (last - first) / first, 1), window)


def top_contributor(day_counts: pd.Series, usual: pd.Series) -> tuple[str, int, float] | None:
    """Type de plainte qui s'écarte le plus de son niveau habituel un jour donné :
    (type, nombre ce jour-là, moyenne journalière habituelle). Une piste, pas une cause."""
    excess = day_counts.sub(usual.reindex(day_counts.index, fill_value=0.0), fill_value=0.0)
    if excess.empty or excess.max() <= 0:
        return None
    key = str(excess.idxmax())
    return key, int(day_counts.get(key, 0)), round(float(usual.get(key, 0.0)), 1)
