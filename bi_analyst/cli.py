from __future__ import annotations

import argparse
import json
from pathlib import Path

from .models import AnalysisContext
from .orchestrator import analyze


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the BIFlow BI Analyst agent.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--dataset-version", help="Prepared Parquet version; omitted uses the active dataset")
    parser.add_argument("--legacy-data", action="store_true", help="Use the immutable historical CSV inputs instead of the active dataset")
    parser.add_argument(
        "--question",
        default="Quels arrondissements ont les délais de résolution relativement élevés ?",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Run deterministic tools only; do not call Ollama.",
    )
    args = parser.parse_args()
    from .demo_service import active_dataset
    selected = None if args.legacy_data else args.dataset_version or active_dataset(args.root)
    report = analyze(
        args.question,
        AnalysisContext(project_root=args.root, dataset_version=selected),
        use_llm=not args.baseline,
    )
    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )
    return 0 if report.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
