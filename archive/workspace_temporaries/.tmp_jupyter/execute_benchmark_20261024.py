"""Exécution réelle de Notebook 3 avec le Python .venv et un noyau local isolé."""
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".tmp_jupyter" / "benchmark_execution"
RUN.mkdir(parents=True, exist_ok=True)
for variable, folder in [("JUPYTER_RUNTIME_DIR", "runtime"), ("JUPYTER_DATA_DIR", "jupyter_data"),
                         ("IPYTHONDIR", "ipython"), ("MPLCONFIGDIR", "matplotlib")]:
    os.environ[variable] = str(RUN / folder)
kernel = Path(os.environ["JUPYTER_DATA_DIR"]) / "kernels" / "biflow-benchmark"
kernel.mkdir(parents=True, exist_ok=True)
(kernel / "kernel.json").write_text(json.dumps({
    "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
    "display_name": "BIFlow benchmark .venv", "language": "python",
}), encoding="utf-8")

import nbformat
from nbclient import NotebookClient

path = ROOT / "notebooks/03_benchmark_llm.ipynb"
nb = nbformat.read(path, as_version=4)
for cell in nb.cells:
    if cell.cell_type == "code":
        cell.execution_count = None
        cell.outputs = []
nbformat.validate(nb)
client = NotebookClient(nb, timeout=240, kernel_name="biflow-benchmark",
                        resources={"metadata": {"path": str(ROOT / "notebooks")}})
client.execute()
nbformat.write(nb, path)
print("NOTEBOOK EXECUTED:", path)
print("CODE CELLS:", sum(c.cell_type == "code" for c in nb.cells))
for cell in nb.cells:
    for output in cell.get("outputs", []):
        if output.get("output_type") == "stream":
            value = output.get("text", "")
            if "Sorties :" in value or "Ran " in value:
                print(value[-2000:])
