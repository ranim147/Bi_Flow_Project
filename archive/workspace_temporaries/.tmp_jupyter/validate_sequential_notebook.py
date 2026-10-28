"""Valide les cellules sans lancer les 81 inférences ; conserve le notebook utilisateur."""
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / ".tmp_jupyter/sequential_notebook_validation"
STAGE.mkdir(parents=True, exist_ok=True)
for variable, name in [("JUPYTER_RUNTIME_DIR", "runtime"), ("JUPYTER_DATA_DIR", "jupyter_data"),
                       ("IPYTHONDIR", "ipython"), ("MPLCONFIGDIR", "matplotlib")]:
    os.environ[variable] = str(STAGE / name)
kernel = Path(os.environ["JUPYTER_DATA_DIR"]) / "kernels/biflow-sequential"
kernel.mkdir(parents=True, exist_ok=True)
(kernel / "kernel.json").write_text(json.dumps({"argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
    "display_name": "BIFlow sequential validation", "language": "python"}), encoding="utf-8")

import nbformat
from nbclient import NotebookClient

nb = nbformat.read(ROOT / "notebooks/03_benchmark_llm.ipynb", as_version=4)
for cell in nb.cells:
    if cell.cell_type == "code":
        cell.source = cell.source.replace("RUN_MODELS = True", "RUN_MODELS = False")
        cell.source = cell.source.replace("RUN_UNIT_TESTS = True", "RUN_UNIT_TESTS = False")
        cell.execution_count = None
        cell.outputs = []
client = NotebookClient(nb, timeout=240, kernel_name="biflow-sequential",
                        resources={"metadata": {"path": str(ROOT / "notebooks")}})
client.execute()
nbformat.write(nb, STAGE / "03_validation.ipynb")
print("PASS: all notebook cells executed with inference disabled; unit tests validated separately.", flush=True)
