"""Read-only inventory and historical fingerprints before the upstream migration."""
from pathlib import Path
import hashlib
import json
import os

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "outputs/pipeline_delivery/audit_before"
out.mkdir(parents=True, exist_ok=True)
inventory, errors, notebooks = [], [], []
for directory, subdirs, names in os.walk(ROOT, onerror=lambda e: errors.append(str(e))):
    subdirs[:] = [d for d in subdirs if d not in {".git", ".venv", "__pycache__", ".tmp_jupyter"}]
    for name in names:
        p = Path(directory) / name
        if p.is_relative_to(out):
            continue
        try:
            row = dict(path=p.relative_to(ROOT).as_posix(), bytes=p.stat().st_size,
                       sha256=hashlib.file_digest(p.open("rb"), "sha256").hexdigest())
            inventory.append(row)
            if p.suffix == ".ipynb":
                nb = json.loads(p.read_text(encoding="utf-8"))
                cells = [c for c in nb.get("cells", []) if c["cell_type"] == "code"]
                notebooks.append(dict(path=row["path"], code_cells=len(cells),
                                      executed_cells=sum(c.get("execution_count") is not None for c in cells),
                                      error_outputs=[o for c in cells for o in c.get("outputs", []) if o.get("output_type") == "error"],
                                      imports=[line for c in cells for line in "".join(c.get("source", [])).splitlines() if line.startswith(("import ", "from "))]))
        except (OSError, ValueError) as exc:
            errors.append(dict(path=str(p), error=str(exc)))
protected = {r["path"]: r["sha256"] for r in inventory if r["path"].startswith(("notebooks/", "data/raw/", "data/processed/", "outputs/llm_benchmark_local/", "outputs/business_intent/", "demo/recordings/"))}
result = dict(inventory=inventory, notebooks=notebooks, errors=errors,
              skipped_directories=[".git", ".venv", "__pycache__", ".tmp_jupyter"],
              conclusion={"upstream": "Executed deterministic notebook stages; no standalone acquisition/profiler/ETL/KPI controller or upstream LLM agents.",
                          "analyst": "Executable Python orchestration, evidence tools, constrained Granite, final validation and Streamlit.",
                          "data": "10000 window-sampled requests; 4 complete months plus partial October 2026; no executable refresh contract."})
(out / "inventory.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
(out / "protected_hashes.json").write_text(json.dumps(protected, indent=2), encoding="utf-8")
print(json.dumps(dict(files=len(inventory), notebooks=len(notebooks), protected=len(protected), read_errors=len(errors))))
