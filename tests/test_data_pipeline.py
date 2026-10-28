"""Explicit synthetic fixtures for upstream contracts; no live network or invented real data."""
from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from bi_pipeline.acquire import AcquisitionConfig, Socrata, acquire_month, literal, sample_predicate
from bi_pipeline.cli import import_local, prepare
from bi_pipeline.common import read, sha, write
from bi_pipeline.kpi import build_kpis, load_clean, validate_processed
from bi_pipeline.loader import load_prepared_bundle, version_path
from bi_pipeline.quality import names, parse_dates, profile, etl, transform
from bi_analyst.analytics import prepare_investigation_frame, backlog_snapshots
from test_benchmark_llm import workspace_trial


def fixture(folder, *, count=500, bad=False, months=4, skew=False):
    version = folder / "data/versions/fixture"
    periods = [str(p) for p in pd.period_range("2024-01", periods=months, freq="M")]
    rows = []
    for index in range(count):
        month = periods[index % len(periods)]
        rows.append(dict(unique_key=str(index), created_date=month+"-10T10:00:00", closed_date=month+"-11T10:00:00",
                         borough=" brooklyn ", agency=" hpd ", complaint_type=" unsanitary   condition ", status="Closed"))
    if bad:
        rows += [rows[0].copy(), {**rows[1], "unique_key": None}, {**rows[2], "unique_key":"negative", "closed_date":"2023-01-01"},
                 {**rows[3], "unique_key":"bad_date", "created_date":"not-a-date"},
                 {**rows[4], "unique_key":"open", "closed_date":None, "status":"Open"}]
    if skew:
        for row in rows[300:]:
            row.update(borough="BRONX", closed_date=row["created_date"][:7]+"-20T10:00:00")
    output = version / "raw/local/part-00000.parquet"; output.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), output)
    counts = pd.Series([r["created_date"][:7] for r in rows if r["created_date"] != "not-a-date"]).value_counts().to_dict()
    write(version / "raw/manifest.json", dict(data_version="fixture", data_kind="synthetic", source_url="synthetic://unit-test",
        extracted_at_utc="2027-03-01T00:00:00Z", source_timezone="America/New_York", period_start_inclusive="2024-01-01",
        period_end_exclusive=str(pd.Period(periods[-1], "M")+1)+"-01", sample_method="Explicit unit-test fixture, not real NYC data",
        sample_rows=len(rows), sample_monthly_counts=counts, population_monthly_counts={m:1000 for m in periods}, partial_months=[],
        parts=[dict(path=output.relative_to(version).as_posix(), rows=len(rows), sha256=sha(output))]))
    return version, rows


class ProfilingETLTests(unittest.TestCase):
    def test_timezone_elapsed_duration_and_dst_ambiguity_are_explicit(self):
        metadata = dict(extracted_at_utc="2026-10-28T00:00:00Z", source_timezone="America/New_York")
        row = pd.DataFrame([dict(unique_key="1", created_date="2024-03-10T01:30:00", closed_date="2024-03-10T03:30:00",
            borough="Staten Is", agency=" dpr ", complaint_type="Damaged Tree", status="Closed")])
        clean = transform(row, metadata)
        self.assertAlmostEqual(clean.resolution_days.iloc[0], 1/24)
        self.assertEqual(clean.borough.iloc[0], "STATEN ISLAND")
        self.assertTrue(pd.isna(parse_dates(pd.Series(["2024-11-03T01:30:00"]), "America/New_York").iloc[0]))

    def test_schema_aliases_and_collisions_do_not_silently_drop_columns(self):
        self.assertIn("complaint_type", names(pd.DataFrame(columns=["Problem"])))
        with self.assertRaisesRegex(ValueError, "collision"):
            names(pd.DataFrame(columns=["Complaint Type", "Problem"]))
        with self.assertRaisesRegex(ValueError, "absent"):
            transform(pd.DataFrame([dict(unique_key="1")]), dict(extracted_at_utc="2026-01-01"))

    def test_profile_and_etl_reconcile_duplicates_invalids_and_quarantine_without_changing_raw(self):
        with workspace_trial() as root:
            version, rows = fixture(root, bad=True)
            original = sha(version / "raw/local/part-00000.parquet")
            report = profile(version)
            self.assertEqual(report["counts"]["rows"], len(rows))
            self.assertEqual(report["counts"]["exact_duplicate_rows"], 1)
            self.assertEqual(report["counts"]["duplicate_key_rows"], 1)
            self.assertEqual(report["quality_gate"], "pass")
            self.assertIn("status", report["distributions"])
            result = etl(version)
            self.assertEqual(result["counts"]["input_rows"], result["counts"]["cleaned_rows"] + result["counts"]["excluded_rows"])
            self.assertEqual(result["counts"]["excluded_rows"], 2)
            clean = load_clean(version)
            self.assertTrue(pd.isna(clean.loc[clean.unique_key.eq("negative"), "resolution_days"].iloc[0]))
            self.assertTrue(pd.isna(clean.loc[clean.unique_key.eq("open"), "resolution_days"].iloc[0]))
            self.assertGreater(len(result["quarantine_parts"]), 0)
            self.assertEqual(sha(version / "raw/local/part-00000.parquet"), original)
            self.assertEqual(etl(version), result)

    def test_quality_gate_blocks_critical_failure_and_incomplete_month_is_reported(self):
        with workspace_trial() as root:
            version, _ = fixture(root, count=5, bad=True)
            metadata = read(version / "raw/manifest.json"); metadata["extracted_at_utc"]="2024-04-17T00:00:00Z"
            write(version / "raw/manifest.json", metadata)
            report = profile(version)
            self.assertEqual(report["quality_gate"], "fail")
            self.assertIn("2024-04", report["temporal_coverage"]["partial_months"])
            with self.assertRaisesRegex(ValueError, "Profiling failed"):
                etl(version)

    def test_raw_corruption_blocks_profile(self):
        with workspace_trial() as root:
            version, _ = fixture(root)
            with (version / "raw/local/part-00000.parquet").open("ab") as stream: stream.write(b"tampered")
            with self.assertRaisesRegex(ValueError, "digest"):
                profile(version)


class SemanticKPIAndIntegrationTests(unittest.TestCase):
    def test_canonical_stratum_variants_do_not_duplicate_sample_counts(self):
        with workspace_trial() as root:
            version,_=fixture(root)
            for month in read(version/"raw/manifest.json")["population_monthly_counts"]:
                write(version/f"raw/{month}/population_strata.json",[
                    dict(borough="brooklyn",complaint_type="unsanitary condition",agency="hpd",request_count=500),
                    dict(borough="BROOKLYN",complaint_type="UNSANITARY CONDITION",agency="HPD",request_count=500)])
            prepare(root,"fixture")
            table=pd.read_parquet(version/"processed/coverage.parquet")
            self.assertEqual(len(table),4)
            self.assertEqual(int(table.sample_n.sum()),500)
            self.assertEqual(int(table.population_n.sum()),4000)

    def test_end_to_end_offline_exact_kpis_denominators_and_synthetic_separation(self):
        with workspace_trial() as root:
            version, _ = fixture(root, bad=True)
            result = prepare(root, "fixture")
            self.assertTrue(read(version / "processed/verification.json")["passed"])
            clean = load_clean(version)
            summary = pd.read_parquet(version / "processed/summary.parquet").set_index("kpi_name").value
            self.assertEqual(summary.loc["request_volume"], len(clean))
            self.assertAlmostEqual(summary.loc["median_resolution_days"], clean.resolution_days.median())
            self.assertAlmostEqual(summary.loc["closure_rate"], clean.state.eq("closed").sum()/clean.created_date.notna().sum()*100)
            catalog = read(version / "processed/semantic_catalog.json")
            self.assertTrue(all(set(["definition","formula","source_columns","unit","period","allowed_dimensions","inclusions","exclusions","limitations","data_version","provenance"]) <= set(row) for row in catalog))
            with self.assertRaisesRegex(ValueError, "Synthetic"):
                load_prepared_bundle(root, "fixture")
            self.assertEqual(build_kpis(version)["quality"], result["quality"])

    def test_global_median_is_not_average_of_group_medians_and_tampering_fails(self):
        with workspace_trial() as root:
            version, _ = fixture(root, count=501, skew=True)
            prepare(root, "fixture")
            target = version / "processed/summary.parquet"
            table = pd.read_parquet(target)
            medians = pd.read_parquet(version / "processed/groups.parquet")
            borough_medians = medians.loc[medians.dimension.eq("borough"), "median_days"]
            self.assertEqual(table.loc[table.kpi_name.eq("median_resolution_days"), "value"].iloc[0], 1.0)
            self.assertNotEqual(borough_medians.mean(), 1.0)
            table.loc[table.kpi_name.eq("median_resolution_days"), "value"] = 99.0
            table.to_parquet(target)
            # Even changing the manifest digest cannot evade independent KPI recomputation.
            manifest = read(version / "processed/manifest.json")
            manifest["files"]["summary"]["sha256"] = sha(target)
            write(version / "processed/manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "Recomputed global KPI"):
                validate_processed(version)

    def test_new_york_months_and_snapshot_cutoffs_are_not_utc_months(self):
        data = pd.DataFrame([dict(unique_key="1", created_date="2024-02-01T03:00:00Z", closed_date=None,
                                  borough="BROOKLYN", agency="A", complaint_type="T", resolution_days=np.nan, state="open")])
        data.attrs["reporting_timezone"] = "America/New_York"
        prepared = prepare_investigation_frame(data, "2024-03-01T00:00:00Z")
        self.assertEqual(prepared.creation_month.iloc[0], "2024-01")
        self.assertEqual(backlog_snapshots(prepared, ("2024-01",))[0]["backlog_n"], 1)

    def test_dataset_path_cannot_escape_version_root(self):
        with self.assertRaisesRegex(ValueError, "Invalid dataset"):
            version_path(Path.cwd(), "../../outside")

    def test_local_import_rejects_a_different_raw_file_instead_of_relabelling_its_metadata(self):
        with workspace_trial() as root:
            source = root / "input.csv"
            pd.DataFrame([dict(unique_key="1", created_date="2024-01-01", closed_date="2024-01-02", agency="A", borough="BRONX", complaint_type="T", status="Closed")]).to_csv(source,index=False)
            metadata = root / "metadata.json"
            write(metadata, dict(sample_rows=2, sample_monthly_counts={"2024-01":2}))
            with self.assertRaisesRegex(ValueError, "does not match"):
                import_local(root, "wrong", source, metadata)
            self.assertFalse((root / "data/versions/wrong/raw/manifest.json").exists())


class AcquisitionContractTests(unittest.TestCase):
    def test_keyset_resume_reuses_only_complete_verified_pages_and_rejects_tampering(self):
        with workspace_trial() as version:
            client=Mock()
            def query(**params):
                if "group" in params: return [dict(borough="BRONX",complaint_type="T",agency="A",request_count="2")]
                if params["select"]=="count(*) as rows": return [dict(rows="2")]
                key="10057" if "unique_key >" in params["where"] else "10007"
                return [dict(unique_key=key,created_date="2024-01-01",closed_date="2024-01-02",borough="BRONX",complaint_type="T",agency="A",status="Closed")]
            client.query.side_effect=query
            result=acquire_month(version,"2024-01",AcquisitionConfig(page_size=1),client)
            self.assertEqual(len(result["pages"]),2)
            calls=client.query.call_count
            self.assertEqual(acquire_month(version,"2024-01",AcquisitionConfig(page_size=1),client),result)
            self.assertEqual(client.query.call_count,calls)
            with (version/result["pages"][0]["path"]).open("ab") as stream: stream.write(b"changed")
            with self.assertRaisesRegex(ValueError,"integrity"):
                acquire_month(version,"2024-01",AcquisitionConfig(page_size=1),client)

    def test_sampling_is_reproducible_nonwindowed_and_bounded(self):
        config = AcquisitionConfig()
        predicate = sample_predicate("2024-01", ["BROOKLYN", None], config)
        self.assertEqual(predicate, sample_predicate("2024-01", ["BROOKLYN", None], config))
        self.assertNotEqual(predicate, sample_predicate("2024-02", ["BROOKLYN", None], config))
        self.assertIn("unique_key like", predicate)
        self.assertIn("borough IS NULL", predicate)
        self.assertEqual(literal("O'Brien"), "'O''Brien'")
        with self.assertRaises(ValueError): AcquisitionConfig(suffix_count=0).validate()

    def test_transient_network_failure_is_logged_and_retried(self):
        with workspace_trial() as folder:
            response = Mock(status_code=200, content=b"[]", url="https://data.cityofnewyork.us/test")
            response.json.return_value=[]
            session=Mock(); session.get.side_effect=[requests.Timeout("TEST ONLY"),response]
            with patch("bi_pipeline.acquire.time.sleep"):
                self.assertEqual(Socrata(folder/"audit.jsonl",session=session).query(select="count(*)"), [])
            events=[json.loads(line) for line in (folder/"audit.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([e["event"] for e in events],["http_failure","http_success"])


if __name__ == "__main__": unittest.main()
