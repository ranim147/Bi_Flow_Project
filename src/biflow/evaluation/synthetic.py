"""Jeu de données synthétique de type NYC 311, avec défauts et anomalies connus à l'avance.

Sert aux tests et à l'évaluation du MVP : on sait exactement quels défauts ont été
injectés (colonne + type + nombre), quelle anomalie (zone + jour), et on peut calculer les
KPI attendus « à la main » sur la vérité terrain, sans passer par le code du pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from biflow.common.reference import BOROUGH_SEED, SLA_SEED
from biflow.common.schema import RAW_COLUMNS

BOROUGH_PROFILE = {  # part des demandes, préfixe de code postal, centre (lat, lon)
    "BROOKLYN": (0.32, "112", (40.65, -73.95)),
    "MANHATTAN": (0.26, "100", (40.78, -73.97)),
    "BRONX": (0.19, "104", (40.84, -73.88)),
    "QUEENS": (0.19, "113", (40.72, -73.82)),
    "STATEN ISLAND": (0.04, "103", (40.59, -74.13)),
}
COMPLAINTS = {  # poids, agence, descripteur, délai médian de résolution (h)
    "Noise - Residential": (0.38, "NYPD", "Loud Music/Party", 3.0),
    "Noise - Street/Sidewalk": (0.15, "NYPD", "Loud Talking", 2.5),
    "Noise - Commercial": (0.07, "NYPD", "Loud Music/Party", 2.0),
    "Noise - Vehicle": (0.05, "NYPD", "Car/Truck Horn", 2.0),
    "Street Condition": (0.15, "DOT", "Pothole", 150.0),
    "Street Light Condition": (0.12, "DOT", "Street Light Out", 200.0),
    "Street Sweeping Complaint": (0.08, "DSNY", "Failure To Sweep", 72.0),
}
WEEKDAY_FACTOR = [0.9, 0.9, 0.95, 1.0, 1.15, 1.3, 1.2]  # lundi -> dimanche


@dataclass
class InjectedDefect:
    column: str
    issue_type: str
    count: int


@dataclass
class SyntheticDataset:
    raw: pd.DataFrame                 # tel que reçu (texte brut, défauts compris)
    truth: pd.DataFrame               # vérité terrain : lignes valides, une par demande
    defects: list[InjectedDefect]
    anomalies: list[dict]             # {"zone", "day"}
    params: dict = field(default_factory=dict)

    def write(self, directory: str | Path, name: str = "synthetic_nyc311") -> Path:
        path = Path(directory) / f"{name}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.raw.to_csv(path, index=False)
        return path


def _base_frame(rng: np.random.Generator, start: pd.Timestamp, days: int, daily: float) -> pd.DataFrame:
    rows = []
    names = list(COMPLAINTS)
    weights = np.array([c[0] for c in COMPLAINTS.values()])
    for d in range(days):
        day = start + pd.Timedelta(days=d)
        for borough, (share, _, _) in BOROUGH_PROFILE.items():
            n = rng.poisson(daily * share * WEEKDAY_FACTOR[day.weekday()])
            types = rng.choice(names, size=n, p=weights / weights.sum())
            seconds = rng.integers(0, 86_400, size=n)
            rows += [(borough, t, day + pd.Timedelta(seconds=int(s))) for t, s in zip(types, seconds, strict=True)]
    return pd.DataFrame(rows, columns=["borough", "complaint_type", "created_date"])


def _complete(df: pd.DataFrame, rng: np.random.Generator, now: pd.Timestamp) -> pd.DataFrame:
    n = len(df)
    df = df.sort_values("created_date").reset_index(drop=True)
    df.insert(0, "unique_key", np.arange(60_000_000, 60_000_000 + n))
    info = df["complaint_type"].map(COMPLAINTS)
    df["agency"] = info.str[1]
    df["descriptor"] = info.str[2]
    median_h = info.str[3].astype(float)
    hours = median_h * np.exp(rng.normal(0, 0.8, size=n))
    closed = (df["created_date"] + pd.to_timedelta(hours, unit="h")).dt.floor("s")  # précision NYC 311
    is_closed = (rng.random(n) < 0.88) & (closed < now)
    df["closed_date"] = closed.where(is_closed)
    df["status"] = np.where(is_closed, "Closed", rng.choice(["Open", "In Progress"], size=n))
    df["resolution_description"] = np.where(
        is_closed, "The agency responded to the complaint and took action.", None)
    profile = df["borough"].map(BOROUGH_PROFILE)
    df["incident_zip"] = profile.str[1] + pd.Series(rng.integers(1, 99, size=n)).map("{:02d}".format)
    df["latitude"] = profile.str[2].str[0] + rng.normal(0, 0.02, size=n)
    df["longitude"] = profile.str[2].str[1] + rng.normal(0, 0.02, size=n)
    return df[RAW_COLUMNS]


def generate(days: int = 56, start: str = "2026-01-05", daily: float = 140.0, seed: int = 42,
             spike_day: int = 40, spike_zone: str = "BROOKLYN", spike_factor: float = 3.0) -> SyntheticDataset:
    rng = np.random.default_rng(seed)
    start_ts = pd.Timestamp(start)
    now = start_ts + pd.Timedelta(days=days)
    base = _base_frame(rng, start_ts, days, daily)

    # Anomalie : pic de « Noise - Residential » dans une zone, un jour donné
    spike_date = start_ts + pd.Timedelta(days=spike_day)
    share = BOROUGH_PROFILE[spike_zone][0]
    extra = int(daily * share * WEEKDAY_FACTOR[spike_date.weekday()] * spike_factor)
    seconds = rng.integers(18 * 3600, 86_400, size=extra)
    spike = pd.DataFrame({"borough": spike_zone, "complaint_type": "Noise - Residential",
                          "created_date": [spike_date + pd.Timedelta(seconds=int(s)) for s in seconds]})
    df = _complete(pd.concat([base, spike], ignore_index=True), rng, now)

    truth = df.copy()
    raw = df.astype(object).where(df.notna(), None)
    for col in ("created_date", "closed_date"):
        raw[col] = df[col].dt.strftime("%Y-%m-%dT%H:%M:%S.000").where(df[col].notna(), None)

    # Défauts : chacun sur des lignes distinctes, pour une vérité terrain exacte
    pool = rng.permutation(len(df))
    closed_pool = [i for i in pool if pd.notna(df.loc[i, "closed_date"])]
    used: set[int] = set()

    def take(n: int, candidates=None) -> list[int]:
        picked = [i for i in (pool if candidates is None else candidates) if i not in used][:n]
        used.update(picked)
        return picked

    defects: list[InjectedDefect] = []

    def inject(column: str, issue_type: str, rows: list[int], value) -> None:
        for i in rows:
            raw.at[i, column] = value(i) if callable(value) else value
        defects.append(InjectedDefect(column, issue_type, len(rows)))

    bad_created = take(10)
    inject("created_date", "invalid_type", bad_created, "pas une date")
    inconsistent = take(12, closed_pool)
    inject("closed_date", "inconsistent", inconsistent,
           lambda i: (df.loc[i, "created_date"] - pd.Timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000"))
    missing_borough = take(20)
    inject("borough", "missing", missing_borough, None)
    unspecified = take(6)
    inject("borough", "invalid_value", unspecified, "Unspecified")
    inject("latitude", "invalid_type", take(10), "inconnue")
    inject("incident_zip", "invalid_format", take(10), lambda i: ["1234", "ABCDE", "100-1"][i % 3])
    outside = take(8)
    inject("latitude", "out_of_range", outside, 0.0)
    inject("longitude", "out_of_range", outside, 0.0)
    duplicated = take(15)
    raw = pd.concat([raw, raw.loc[duplicated]], ignore_index=True)
    defects.append(InjectedDefect("unique_key", "duplicate", len(duplicated)))
    raw = raw.sample(frac=1, random_state=seed).reset_index(drop=True)

    # Vérité terrain « nettoyée » : ce que les KPI doivent refléter
    truth = truth.drop(index=bad_created + inconsistent)
    truth.loc[truth.index.isin(missing_borough + unspecified), "borough"] = "UNSPECIFIED"
    return SyntheticDataset(
        raw=raw, truth=truth.reset_index(drop=True), defects=defects,
        anomalies=[{"zone": spike_zone, "day": spike_date.date().isoformat()}],
        params={"days": days, "start": start, "daily": daily, "seed": seed, "spike_extra_rows": extra},
    )


def expected_kpis(truth: pd.DataFrame) -> dict[str, float]:
    """KPI calculés « à la main » sur la vérité terrain (indépendamment du pipeline)."""
    closed = truth["closed_date"].notna()
    hours = (truth["closed_date"] - truth["created_date"]).dt.total_seconds() / 3600
    targets = truth["complaint_type"].map({s["complaint_type"]: s["target_hours"] for s in SLA_SEED})
    rated = closed & targets.notna()
    population = sum(b["population"] for b in BOROUGH_SEED)
    in_boroughs = truth["borough"].isin([b["borough"] for b in BOROUGH_SEED])
    return {
        "total_requests": float(len(truth)),
        "closure_rate": float(closed.mean()),
        "median_resolution_hours": float(hours[closed].median()),
        "sla_compliance_rate": 100 * float((hours[rated] <= targets[rated]).mean()),
        "requests_per_10k": 10_000 * float(in_boroughs.sum()) / population,
    }
