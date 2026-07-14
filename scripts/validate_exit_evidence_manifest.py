from __future__ import annotations

import argparse
from pathlib import Path
import sys

from legacy_baseline_contracts import (
    ContractError,
    MANIFEST_SCHEMA_ID,
    load_json_object,
    load_schema_contract,
    validate_exit_evidence_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Slice 0 Exit Evidence Manifest.")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv or sys.argv[1:])
    try:
        load_schema_contract(
            PROJECT_ROOT,
            "exit-evidence-manifest.v1.schema.json",
            MANIFEST_SCHEMA_ID,
        )
        validate_exit_evidence_manifest(load_json_object(args.manifest.resolve()))
    except (ContractError, OSError, UnicodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {args.manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
