"""Export versioned public contracts and the internal investigation draft."""
from pathlib import Path
import copy
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bi_analyst.models import BIAnalysisReport, DecisionDraft

destination = ROOT / "schemas"
destination.mkdir(exist_ok=True)
schema = BIAnalysisReport.model_json_schema()
for version in ("v1", "v2"):
    exported = copy.deepcopy(schema)
    exported["properties"]["contract_version"].update(const=f"biflow-bi-analyst-{version}", default=f"biflow-bi-analyst-{version}")
    if version == "v1":
        exported["properties"].pop("decision_support")
        exported["properties"].pop("analytical_plan")
        exported["properties"]["data_kind"] = {"const": "real", "default": "real", "type": "string"}
        exported["$defs"]["EvidenceFact"]["properties"]["data_kind"] = {"const": "real", "default": "real", "type": "string"}
    else:
        exported["allOf"] = [{
            "if": {"properties": {"data_kind": {"const": "synthetic"}}, "required": ["data_kind"]},
            "then": {"properties": {"evidence": {"items": {"properties": {"data_kind": {"const": "synthetic"}}, "required": ["data_kind"]}}}},
            "else": {"properties": {"evidence": {"items": {"properties": {"data_kind": {"const": "real"}}}}}},
        }]
    (destination / f"BIAnalysisReport.{version}.schema.json").write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8")
(destination / "DecisionDraft.v1.schema.json").write_text(json.dumps(DecisionDraft.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
print("Exported versioned report schemas and decision draft schema.")
