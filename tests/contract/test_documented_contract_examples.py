from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("name", "schema_path"),
    [
        ("work-package", "schemas/contracts/work-package.schema.json"),
        ("task-state", "schemas/contracts/task-state.schema.json"),
        ("worker-result", "schemas/worker_result.schema.json"),
        ("validation-result", "schemas/contracts/validation-result.schema.json"),
        ("event-record", "schemas/contracts/event-record.schema.json"),
        ("handoff-packet", "schemas/contracts/handoff-packet.schema.json"),
        ("merge-decision", "schemas/contracts/merge-decision.schema.json"),
        ("supervisor-action", "schemas/contracts/supervisor-action.schema.json"),
    ],
)
def test_documented_contract_examples_are_validated(
    name: str, schema_path: str
) -> None:
    examples = json.loads(
        (ROOT / "schemas" / "contracts" / "examples.json").read_text(encoding="utf-8")
    )
    schema = json.loads((ROOT / schema_path).read_text(encoding="utf-8"))
    jsonschema.validate(examples[name]["valid"], schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(examples[name]["invalid"], schema)
