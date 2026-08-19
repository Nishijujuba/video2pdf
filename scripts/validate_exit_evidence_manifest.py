from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, cast

from legacy_baseline_contracts import (
    ContractError,
    MANIFEST_SCHEMA_ID,
    load_json_value,
    load_schema_object,
    validate_json_schema_instance,
    validate_prevalidated_exit_evidence_bindings,
    validate_prevalidated_exit_evidence_semantics,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Slice 0 Exit Evidence Manifest.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Validate only the self-contained Schema contract, for positive and negative fixtures.",
    )
    parser.add_argument(
        "--pre-publication",
        action="store_true",
        help=(
            "Validate unpublished evidence against the exact implementation HEAD. "
            "The default derives the committed evidence publication from Git history."
        ),
    )
    args = parser.parse_args(argv or sys.argv[1:])
    try:
        schema = load_schema_object(
            PROJECT_ROOT,
            "exit-evidence-manifest.v1.schema.json",
            MANIFEST_SCHEMA_ID,
        )
        manifest_value = load_json_value(args.manifest.resolve())
        validate_json_schema_instance(
            manifest_value, schema, "exit evidence manifest"
        )
        if not args.schema_only:
            manifest = cast(dict[str, Any], manifest_value)
            validate_prevalidated_exit_evidence_semantics(manifest)
            validate_prevalidated_exit_evidence_bindings(
                manifest,
                PROJECT_ROOT,
                args.manifest.resolve(),
                pre_publication=args.pre_publication,
            )
    except (ContractError, OSError, UnicodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {args.manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
