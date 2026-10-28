"""Execute existing notebooks in an isolated copy; never overwrite project outputs."""
from pathlib import Path
import ast
import hashlib
import json
import os
import shutil
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / ".tmp_jupyter" / "audit_20261024"
STAGE.mkdir(parents=True, exist_ok=True)
os.environ["JUPYTER_RUNTIME_DIR"] = str(STAGE / "runtime")
os.environ["JUPYTER_DATA_DIR"] = str(STAGE / "jupyter_data")
os.environ["IPYTHONDIR"] = str(STAGE / "ipython")
os.environ["MPLCONFIGDIR"] = str(STAGE / "matplotlib")
os.environ["MPLBACKEND"] = "Agg"
kernel_dir = Path(os.environ["JUPYTER_DATA_DIR"]) / "kernels" / "biflow-audit"
kernel_dir.mkdir(parents=True, exist_ok=True)
(kernel_dir / "kernel.json").write_text(json.dumps({
    "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
    "display_name": "BIFlow audit isolated", "language": "python",
}), encoding="utf-8")

import nbformat
from nbclient import NotebookClient

results = {"python": sys.executable, "stage": str(STAGE), "notebooks": []}
tracked_paths = [p for folder in ["notebooks", "data", "outputs", "biflow_analyste"]
                 for p in (ROOT / folder).rglob("*") if p.is_file()]
before = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked_paths}
for folder in ["data/raw", "data/processed", "biflow_analyste"]:
    target = STAGE / folder
    target.mkdir(parents=True, exist_ok=True)
    for path in (ROOT / folder).iterdir():
        if path.is_file() and path.suffix in {".csv", ".json"}:
            shutil.copy2(path, target / path.name)

for name in ["01_etl_kpi.ipynb", "02_bi_analyst.ipynb"]:
    print("EXECUTING", name, flush=True)
    nb = nbformat.read(ROOT / "notebooks" / name, as_version=4)
    syntax_errors = []
    for idx, cell in enumerate(nb.cells):
        if cell.cell_type == "code":
            cell.execution_count = None
            cell.outputs = []
            try:
                ast.parse(cell.source)
            except SyntaxError as exc:
                syntax_errors.append({"cell": idx, "error": str(exc)})
    # The BI Analyst narrative calls an LLM and is outside the deterministic audit.
    skipped = []
    if name.startswith("02"):
        for idx, cell in enumerate(nb.cells):
            if cell.cell_type == "code" and "def build_llama_summary" in cell.source:
                skipped.append(idx)
                cell.source = "print('LLM narrative skipped during deterministic audit')"
    result = {"name": name, "syntax_errors": syntax_errors, "skipped_cells": skipped}
    try:
        client = NotebookClient(nb, timeout=180, kernel_name="biflow-audit", allow_errors=False,
                                resources={"metadata": {"path": str(STAGE)}})
        client.execute()
        result["status"] = "passed"
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__, error=str(exc)[-8000:])
    result["executed_code_cells"] = sum(c.cell_type == "code" and c.get("execution_count") is not None for c in nb.cells)
    result["errors"] = [{"cell": idx, "ename": out.get("ename"), "evalue": out.get("evalue")}
                        for idx, cell in enumerate(nb.cells) for out in cell.get("outputs", [])
                        if out.get("output_type") == "error"]
    nbformat.write(nb, STAGE / name)
    results["notebooks"].append(result)
    print(json.dumps(result, ensure_ascii=False), flush=True)

after = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked_paths}
results["existing_files_unchanged"] = before == after
(STAGE / "audit_execution.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print("EXISTING FILES UNCHANGED", before == after, flush=True)
