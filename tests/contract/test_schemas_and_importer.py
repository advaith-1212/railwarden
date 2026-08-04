from __future__ import annotations

import jsonschema

from railwarden.validation.worker_result import SCHEMA


def test_worker_result_schema_contract() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
