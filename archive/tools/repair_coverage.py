"""Explicit derived-layer revision; retain original files and never edit raw/cleaned data."""
from pathlib import Path
import shutil

from .common import log, now, read, sha, write
from .kpi import build_coverage, load_clean, validate_processed


def repair(version):
    version=Path(version)
    folder=version/"processed"
    manifest=read(folder/"manifest.json")
    if manifest.get("coverage_revision")==2:
        return validate_processed(version)
    archive=version/"revisions/coverage_r1"
    archive.mkdir(parents=True,exist_ok=False)
    original={}
    for name in ("manifest.json","coverage.parquet","verification.json"):
        if (folder/name).exists():
            shutil.copyfile(folder/name,archive/name)
            original[name]=sha(folder/name)
    clean=load_clean(version)
    table=build_coverage(clean,version,manifest["metadata"])
    if table.sample_n.sum()!=clean.created_month.notna().sum():
        raise ValueError("Revised coverage still fails sample denominator reconciliation")
    target=folder/"coverage.parquet"
    temporary=folder/"coverage_revision_2.parquet"
    table.to_parquet(temporary,index=False,compression="zstd")
    temporary.replace(target)
    manifest["files"]["coverage"].update(rows=len(table),sha256=sha(target))
    manifest.update(coverage_revision=2,coverage_revision_reason="Canonicalize and aggregate source strata before the sample join; original case variants must not duplicate sample counts")
    write(folder/"manifest.json",manifest)
    log(version/"pipeline_audit.jsonl","derived_coverage_revision",old_manifest_sha256=original["manifest.json"],new_manifest_sha256=sha(folder/"manifest.json"),archived_files=original,raw_and_cleaned_unchanged=True)
    return validate_processed(version)


if __name__ == "__main__":
    import argparse
    from .loader import version_path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path.cwd());parser.add_argument("--version",required=True)
    args=parser.parse_args()
    result=repair(version_path(args.root,args.version))
    print("Coverage revision verified:",result.get("coverage_revision"),flush=True)
