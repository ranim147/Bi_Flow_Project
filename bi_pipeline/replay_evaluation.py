"""Revalidate saved real reports against the current prepared revision, without model calls."""
from pathlib import Path
import argparse

from bi_analyst.analytics import load_real_bundle
from bi_analyst.business_evaluation import QUESTIONS, independent_checks
from bi_analyst.draft import validate_draft
from bi_analyst.models import AnalysisContext, BIAnalysisReport
from bi_analyst.scoped import materialize_sections, scoped_policy
from bi_analyst.verification import verify_report
from .common import read, sha, write
from .loader import version_path


def verify(root, version, output):
    context=AnalysisContext(project_root=root,dataset_version=version)
    bundle=load_real_bundle(root,version)
    rows=[]
    for row in read(output/"results.json"):
        folder=output/row["audit_dir"]
        report=BIAnalysisReport.model_validate(read(folder/"report.json"))
        import json
        events=[json.loads(line) for line in (folder/"audit.jsonl").read_text(encoding="utf-8").splitlines()]
        responses=[e for e in events if e["event"]=="llm_response"]
        raw=responses[-1]["raw_response"] if responses else None
        errors=verify_report(report,context,raw)
        if row["scenario"] in QUESTIONS:
            errors.extend(independent_checks(row["scenario"],report,read(folder/"analysis.json"),bundle)["errors"])
        if report.analytical_plan is not None and report.status=="validated":
            details=read(folder/"analysis.json")
            draft,validation=validate_draft(raw or "",scoped_policy(details,report.evidence))
            if validation.status!="validated" or draft is None or report.insights!=materialize_sections(report.execution_id,details,draft):
                errors.append("saved_granite_or_final_materialization_mismatch")
        if report.status=="validated" and (not responses or responses[-1].get("response_model")!="granite3.3:2b"):
            errors.append("saved_model_identity_mismatch")
        rows.append(dict(scenario=row["scenario"],execution_id=report.execution_id,passed=not errors,errors=errors))
    result=dict(model_calls=0,all_passed=all(r["passed"] for r in rows),dataset_version=version,
                current_prepared_manifest_sha256=sha(version_path(root,version)/"processed/manifest.json"),
                statement="Recomputed facts and validated saved Granite completions; coverage revision does not change cleaned data, KPI facts or model inputs",results=rows)
    write(output/"replay_verification.json",result)
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path.cwd());parser.add_argument("--version",required=True);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    result=verify(args.root.resolve(),args.version,args.output.resolve())
    print(result["all_passed"],flush=True)
    raise SystemExit(0 if result["all_passed"] else 1)
