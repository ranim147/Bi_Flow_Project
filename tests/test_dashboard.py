"""Le dashboard Streamlit s'affiche sans erreur et refuse une spec modifiée après l'audit."""

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from biflow.agents.dashboard_generator import APP_PATH


def _app(runs_dir: Path, monkeypatch) -> AppTest:
    monkeypatch.setenv("BIFLOW_RUNS_DIR", str(runs_dir))
    return AppTest.from_file(str(APP_PATH), default_timeout=60).run()


def test_dashboard_renders_published_run(pipeline, monkeypatch):
    state = pipeline(questions=["Combien de demandes à Brooklyn ?"])
    app = _app(Path(state.workspace).parent, monkeypatch)
    assert not app.exception
    assert len(app.metric) == len(state.outputs["kpi_semantic"]["kpis"])
    assert any("Audit" in h.value for h in app.subheader)


def test_dashboard_refuses_tampered_spec(pipeline, monkeypatch):
    state = pipeline()
    spec_path = Path(state.outputs["dashboard_generator"]["spec_path"])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["charts"][0]["value"] = 0
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    app = _app(spec_path.parent.parent, monkeypatch)
    assert app.error and "modifiée" in app.error[0].value


def test_dashboard_without_published_run(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    assert app.warning and not app.exception
