"""Post-cleanup validation of active datasets, historical replay and the six latest reports."""
from pathlib import Path
import sys
import json
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"tests"))
from bi_pipeline.common import read,write
from bi_analyst.demo_service import active_dataset,latest_cases,load_latest_evaluation
from bi_analyst.analytics import load_real_bundle

BASE=ROOT/"outputs/pipeline_delivery"
selected=active_dataset(ROOT)
bundle=load_real_bundle(ROOT,selected)
assert len(bundle["clean"])==265609
loader=unittest.TestLoader()
suite=unittest.TestSuite(loader.loadTestsFromName(name) for name in ("test_demo_service","test_demo_ui","test_consumers"))
with (BASE/"post_cleanup_tests.log").open("w",encoding="utf-8") as stream:
    outcome=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)

from streamlit.testing.v1 import AppTest
rows=[]
with patch("benchmark_llm.runner.OllamaClient.__init__",side_effect=AssertionError("No model calls allowed during saved-result UI checks")):
    app=AppTest.from_file(str(ROOT/"demo/streamlit_app.py"),default_timeout=45).run()
    assert not list(app.exception)
    app.radio(key="mode_choice").set_value("latest").run()
    for case in latest_cases(ROOT):
        app.selectbox(key="latest_case").set_value(case["id"]).run()
        report=app.session_state["active_run"].report
        captions=" ".join(str(item.value) for item in app.caption)
        passed=not list(app.exception) and report.execution_id==case["execution_id"] and app.text_area(key="question").value==case["question"] and selected in captions
        rows.append(dict(scenario=case["id"],execution_id=report.execution_id,passed=passed))
    app.radio(key="mode_choice").set_value("live").run()
    assert not list(app.exception) and "active_run" not in app.session_state
result=dict(passed=outcome.wasSuccessful() and all(row["passed"] for row in rows),dataset_version=selected,
            regression_tests=outcome.testsRun,failures=len(outcome.failures),errors=len(outcome.errors),
            skips=len(outcome.skipped),latest_ui=rows,additional_model_calls=0)
write(BASE/"post_cleanup_verification.json",result)
print(json.dumps(result,ensure_ascii=False),flush=True)
raise SystemExit(0 if result["passed"] else 1)
