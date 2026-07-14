from __future__ import annotations

import argparse
from pathlib import Path
import sys

from legacy_baseline_contracts import (
    ContractError,
    DEFINITION_SCHEMA_ID,
    load_json_object,
    load_schema_contract,
    validate_json_schema_instance,
    validate_prevalidated_legacy_baseline_semantics,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Legacy workflow baseline definition.")
    parser.add_argument("definition", type=Path)
    args = parser.parse_args(argv or sys.argv[1:])
    try:
        schema = load_schema_contract(
            PROJECT_ROOT,
            "legacy-baseline-definition.v1.schema.json",
            DEFINITION_SCHEMA_ID,
        )
        definition = load_json_object(args.definition.resolve())
        validate_json_schema_instance(definition, schema, "legacy baseline definition")
        validate_prevalidated_legacy_baseline_semantics(definition)
    except (ContractError, OSError, UnicodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {args.definition.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
