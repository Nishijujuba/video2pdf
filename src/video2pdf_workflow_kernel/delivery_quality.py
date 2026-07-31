from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import uuid

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from .errors import (
    ContractError,
    DeliveryQualityConformanceFailed,
    UnknownContractVersion,
)
from .utils import (
    canonical_json_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_atomic,
)


DELIVERY_QUALITY_ROOT = Path("delivery-quality/v1")
DELIVERY_QUALITY_SCHEMA_ROOT = Path("schemas/delivery-quality/v1")
DELIVERY_QUALITY_REGISTRY = Path("schemas/delivery-quality/registry.v1.json")
SUPPORTED_SCHEMA_VERSION = "1.0.0"
SUPPORTED_AUTHORITY = "target_only"


@dataclass(frozen=True)
class DeliveryQualityContractEntry:
    schema_name: str
    schema_version: str
    schema_id: str
    schema_path: Path
    canonical_instance: Path
    canonical_sha256: str
    positive_example: Path
    negative_example: Path


def semantic_sha256(value: dict[str, Any], field: str = "semantic_sha256") -> str:
    semantic_value = dict(value)
    semantic_value.pop(field, None)
    return sha256_bytes(canonical_json_bytes(semantic_value))


def _require_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ContractError(f"Delivery Quality {label} identities must be unique")


class DeliveryQualityRegistry:
    """Target-only contract authority and implementation-qualification runner."""

    def __init__(self, project_root: Path, registry_path: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self.registry_path = (
            registry_path or self.project_root / DELIVERY_QUALITY_REGISTRY
        ).resolve()
        self._manifest = read_json(self.registry_path)
        self.entries = self._load_entries()
        self.schemas: dict[str, dict[str, Any]] = {}

    def _resolve_project_path(self, value: Any, label: str) -> Path:
        if not isinstance(value, str) or not value or "\\" in value:
            raise ContractError(f"Delivery Quality {label} path is invalid")
        path = (self.project_root / value).resolve()
        try:
            path.relative_to(self.project_root)
        except ValueError as exc:
            raise ContractError(
                f"Delivery Quality {label} path escapes the project"
            ) from exc
        return path

    def _load_entries(self) -> tuple[DeliveryQualityContractEntry, ...]:
        expected_root_fields = {
            "schema_name",
            "schema_version",
            "authority",
            "contracts",
        }
        if not isinstance(self._manifest, dict) or set(self._manifest) != expected_root_fields:
            raise ContractError("Delivery Quality registry has unknown or missing fields")
        if self._manifest["schema_name"] != "delivery-quality-contract-registry":
            raise ContractError("Delivery Quality registry identity is invalid")
        if self._manifest["schema_version"] != SUPPORTED_SCHEMA_VERSION:
            raise UnknownContractVersion("unsupported Delivery Quality registry version")
        if self._manifest["authority"] != SUPPORTED_AUTHORITY:
            raise ContractError("Delivery Quality registry would activate runtime authority")
        contracts = self._manifest["contracts"]
        if not isinstance(contracts, list) or len(contracts) != 20:
            raise ContractError(
                "Delivery Quality registry must contain twenty contracts"
            )

        expected_entry_fields = {
            "schema_name",
            "schema_version",
            "schema_id",
            "schema_path",
            "canonical_instance",
            "canonical_sha256",
            "positive_example",
            "negative_example",
        }
        entries: list[DeliveryQualityContractEntry] = []
        names: list[str] = []
        ids: list[str] = []
        paths: list[Path] = []
        for raw in contracts:
            if not isinstance(raw, dict) or set(raw) != expected_entry_fields:
                raise ContractError(
                    "Delivery Quality registry contract has unknown or missing fields"
                )
            if raw["schema_version"] != SUPPORTED_SCHEMA_VERSION:
                raise UnknownContractVersion(
                    f"unsupported Delivery Quality contract version: "
                    f"{raw['schema_name']}@{raw['schema_version']}"
                )
            if (
                not isinstance(raw["canonical_sha256"], str)
                or len(raw["canonical_sha256"]) != 64
                or any(character not in "0123456789abcdef" for character in raw["canonical_sha256"])
            ):
                raise ContractError("Delivery Quality canonical SHA-256 is invalid")
            schema_path = self._resolve_project_path(raw["schema_path"], "schema")
            canonical_instance = self._resolve_project_path(
                raw["canonical_instance"], "canonical instance"
            )
            entry = DeliveryQualityContractEntry(
                schema_name=raw["schema_name"],
                schema_version=raw["schema_version"],
                schema_id=raw["schema_id"],
                schema_path=schema_path,
                canonical_instance=canonical_instance,
                canonical_sha256=raw["canonical_sha256"],
                positive_example=self._resolve_project_path(
                    raw["positive_example"], "positive example"
                ),
                negative_example=self._resolve_project_path(
                    raw["negative_example"], "negative example"
                ),
            )
            entries.append(entry)
            names.append(entry.schema_name)
            ids.append(entry.schema_id)
            paths.append(entry.schema_path)

        _require_unique(names, "contract")
        _require_unique(ids, "schema")
        _require_unique([str(path) for path in paths], "schema path")
        expected_names = {
            "delivery-quality-rule-catalog",
            "delivery-quality-language-profiles",
            "delivery-quality-role-projections",
            "delivery-quality-waiver-ledger",
            "delivery-quality-migration-ledger",
            "delivery-quality-conformance-corpus",
            "delivery-quality-conformance-report",
            "precompile-artifact-generation-set",
            "reader-facing-text-inventory",
            "precompile-semantic-dependencies",
            "precompile-review-skeleton",
            "precompile-judgment-patch",
            "precompile-quality-report",
            "precompile-text-seal",
            "text-equivalence-report",
            "final-artifact-seal",
            "render-evidence-manifest",
            "rendered-text-object-inventory",
            "text-origin-manifest",
            "rendered-text-reconciliation-report",
        }
        if set(names) != expected_names:
            raise ContractError("Delivery Quality registry contract set is incomplete")
        return tuple(entries)

    def _prepare_schemas(self) -> None:
        self.schemas = {}
        registered_paths = {entry.schema_path for entry in self.entries}
        disk_paths = {
            path.resolve()
            for path in (self.project_root / DELIVERY_QUALITY_SCHEMA_ROOT).glob(
                "*.schema.json"
            )
        }
        if registered_paths != disk_paths:
            raise ContractError("Delivery Quality schema inventory is incomplete")
        for entry in self.entries:
            schema = read_json(entry.schema_path)
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise ContractError(
                    f"Delivery Quality schema is invalid: {entry.schema_name}"
                ) from exc
            if schema.get("$id") != entry.schema_id:
                raise ContractError(
                    f"Delivery Quality schema id mismatch: {entry.schema_name}"
                )
            self.schemas[entry.schema_name] = schema

    def validate(self, schema_name: str, instance: dict[str, Any]) -> None:
        if not self.schemas:
            self._prepare_schemas()
        schema = self.schemas.get(schema_name)
        if schema is None:
            raise UnknownContractVersion(
                f"unknown Delivery Quality contract: {schema_name}"
            )
        try:
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            ).validate(instance)
        except ValidationError as exc:
            path = ".".join(str(part) for part in exc.absolute_path) or "<root>"
            raise ContractError(
                f"Delivery Quality {schema_name} invalid at {path}: {exc.message}"
            ) from exc

    def _instances(self) -> dict[str, dict[str, Any]]:
        instances: dict[str, dict[str, Any]] = {}
        for entry in self.entries:
            if not entry.canonical_instance.is_file():
                raise ContractError(
                    f"Delivery Quality canonical instance is missing: "
                    f"{entry.canonical_instance}"
                )
            if sha256_file(entry.canonical_instance) != entry.canonical_sha256:
                raise ContractError(
                    f"Delivery Quality canonical fingerprint mismatch: "
                    f"{entry.schema_name}"
                )
            instance = read_json(entry.canonical_instance)
            self.validate(entry.schema_name, instance)
            instances[entry.schema_name] = instance
        return instances

    def _validate_relations(self, instances: dict[str, dict[str, Any]]) -> dict[str, Any]:
        profiles = instances["delivery-quality-language-profiles"]
        profile_items = profiles["profiles"]
        profile_ids = [item["profile_id"] for item in profile_items]
        _require_unique(profile_ids, "language profile")
        profile_by_id = {item["profile_id"]: item for item in profile_items}
        for profile in profile_items:
            if semantic_sha256(profile) != profile["semantic_sha256"]:
                raise ContractError(
                    f"Delivery Quality language profile fingerprint mismatch: "
                    f"{profile['profile_id']}"
                )

        catalog = instances["delivery-quality-rule-catalog"]
        if catalog["authority"] != SUPPORTED_AUTHORITY:
            raise ContractError("Delivery Quality catalog would activate runtime authority")
        registered_profiles = catalog["language_profile_registry"]
        if {item["profile_id"] for item in registered_profiles} != set(profile_ids):
            raise ContractError("Delivery Quality catalog profile registry is incomplete")
        for binding in registered_profiles:
            profile = profile_by_id[binding["profile_id"]]
            if binding["profile_semantic_sha256"] != profile["semantic_sha256"]:
                raise ContractError("Delivery Quality catalog has a dangling profile fingerprint")

        rules = catalog["rules"]
        rule_ids = [rule["rule_id"] for rule in rules]
        _require_unique(rule_ids, "rule")
        rule_by_id = {rule["rule_id"]: rule for rule in rules}
        for rule in rules:
            if semantic_sha256(rule) != rule["semantic_sha256"]:
                raise ContractError(
                    f"Delivery Quality rule fingerprint mismatch: {rule['rule_id']}"
                )
            _require_unique(
                [item["violation_id"] for item in rule["violations"]],
                f"{rule['rule_id']} violation",
            )
            _require_unique(
                [item["exception_id"] for item in rule["exceptions"]],
                f"{rule['rule_id']} exception",
            )
            violation_ids = {
                item["violation_id"] for item in rule["violations"]
            }
            for exception in rule["exceptions"]:
                if not set(exception["applies_to_violation_ids"]) <= violation_ids:
                    raise ContractError(
                        f"Delivery Quality exception has a dangling violation: "
                        f"{rule['rule_id']}.{exception['exception_id']}"
                    )
            if not set(rule["language_profiles"]) <= set(profile_ids):
                raise ContractError(
                    f"Delivery Quality rule has a dangling language profile: "
                    f"{rule['rule_id']}"
                )

        projections = instances["delivery-quality-role-projections"]
        if projections["source_catalog_sha256"] != sha256_file(
            self._entry("delivery-quality-rule-catalog").canonical_instance
        ):
            raise ContractError("Delivery Quality projections bind a stale catalog")
        projection_ids = [
            projection["projection_id"] for projection in projections["projections"]
        ]
        _require_unique(projection_ids, "projection")
        owners: dict[str, str] = {}
        generated_prompt_paths: list[str] = []
        prompt_checks: list[tuple[dict[str, Any], Path, bytes]] = []
        for projection in projections["projections"]:
            projected_rule_ids: list[str] = []
            for snapshot in projection["rules"]:
                rule_id = snapshot["rule_id"]
                if rule_id not in rule_by_id:
                    raise ContractError("Delivery Quality projection has a dangling rule")
                rule = rule_by_id[rule_id]
                expected = {
                    "rule_id": rule_id,
                    "rule_semantic_sha256": rule["semantic_sha256"],
                    "requirement": rule["requirement"],
                    "blocking": True,
                }
                if snapshot != expected:
                    raise ContractError(
                        f"Delivery Quality projection rewrites policy: "
                        f"{projection['projection_id']}:{rule_id}"
                    )
                projected_rule_ids.append(rule_id)
                if projection["projection_kind"] == "evaluation":
                    if rule_id in owners:
                        raise ContractError(
                            f"Delivery Quality rule has overlapping Primary Semantic "
                            f"Decision Owners: {rule_id}"
                        )
                    owners[rule_id] = projection["consumer_role"]
            _require_unique(projected_rule_ids, f"{projection['projection_id']} rule")
            prompt_path = self._resolve_project_path(
                projection["generated_prompt"]["path"], "generated prompt"
            )
            expected_prompt = self._render_prompt(
                catalog, projections, projection
            ).encode("utf-8")
            prompt_checks.append((projection, prompt_path, expected_prompt))
        if set(owners) != set(rule_ids):
            raise ContractError("Delivery Quality Primary Semantic Decision ownership is incomplete")
        for projection, prompt_path, expected_prompt in prompt_checks:
            if (
                not prompt_path.is_file()
                or prompt_path.read_bytes() != expected_prompt
                or sha256_bytes(expected_prompt)
                != projection["generated_prompt"]["sha256"]
            ):
                raise ContractError(
                    f"Delivery Quality generated prompt is stale: "
                    f"{projection['projection_id']}"
                )
            generated_prompt_paths.append(str(prompt_path))
        _require_unique(generated_prompt_paths, "generated prompt path")

        waiver_ledger = instances["delivery-quality-waiver-ledger"]
        _require_unique(
            [waiver["waiver_id"] for waiver in waiver_ledger["waivers"]],
            "waiver",
        )
        for waiver in waiver_ledger["waivers"]:
            rule = rule_by_id.get(waiver["rule_id"])
            if rule is None:
                raise ContractError("Delivery Quality waiver has a dangling rule")
            violations = {item["violation_id"] for item in rule["violations"]}
            if not set(waiver["violation_ids"]) <= violations:
                raise ContractError("Delivery Quality waiver has a dangling violation")

        migration = instances["delivery-quality-migration-ledger"]
        source_contract = migration["source_contract"]
        source_path = self._resolve_project_path(
            source_contract["path"], "migration source"
        )
        if (
            not source_path.is_file()
            or sha256_file(source_path) != source_contract["sha256"]
        ):
            raise ContractError(
                "Delivery Quality migration ledger source fingerprint is stale"
            )
        migration_identities = [
            (
                entry["source_criterion_id"],
                entry["rule_id"],
                entry["decision_phase"],
            )
            for entry in migration["entries"]
        ]
        if len(migration_identities) != len(set(migration_identities)):
            raise ContractError("Delivery Quality migration identities are duplicated")
        migration_targets = [entry["rule_id"] for entry in migration["entries"]]
        if not set(migration_targets) <= set(rule_ids):
            raise ContractError("Delivery Quality migration ledger has a dangling rule")
        for entry in migration["entries"]:
            if entry["primary_semantic_decision_owner"] != owners[entry["rule_id"]]:
                raise ContractError(
                    "Delivery Quality migration ledger assigns the wrong "
                    f"Primary Semantic Decision Owner: {entry['rule_id']}"
                )
        if migration["activation_status"] != SUPPORTED_AUTHORITY:
            raise ContractError("Delivery Quality migration ledger activates runtime authority")

        corpus = instances["delivery-quality-conformance-corpus"]
        if set(corpus["applicable_language_profiles"]) != set(profile_ids):
            raise ContractError("Delivery Quality conformance corpus profile coverage is incomplete")
        template_ids = [template["template_id"] for template in corpus["case_templates"]]
        _require_unique(template_ids, "conformance template")
        expected_kinds = {
            ("predicate_object_fit", "violation"),
            ("predicate_object_fit", "compliant"),
            ("semantic_domain_drift", "violation"),
            ("semantic_domain_drift", "compliant"),
            ("lifecycle_stage_confusion", "violation"),
            ("lifecycle_stage_confusion", "compliant"),
            ("missing_modifier_dimensions", "violation"),
            ("missing_modifier_dimensions", "compliant"),
            ("modal_strength_error", "violation"),
            ("modal_strength_error", "compliant"),
            ("exception_boundary", "valid_exception"),
            ("exception_boundary", "rejected_exception"),
        }
        actual_kinds = {
            (template["pair_kind"], template["variant"])
            for template in corpus["case_templates"]
        }
        if actual_kinds != expected_kinds:
            raise ContractError("Delivery Quality semantic minimal-pair corpus is incomplete")
        for template in corpus["case_templates"]:
            target_rule = rule_by_id.get(template["target_rule_id"])
            if target_rule is None:
                raise ContractError("Delivery Quality corpus has a dangling rule")
            if set(template["inputs_by_profile"]) != set(profile_ids):
                raise ContractError("Delivery Quality corpus lacks per-profile inputs")
            expected = template["expected"]
            violations = {
                f"{target_rule['rule_id']}.{item['violation_id']}"
                for item in target_rule["violations"]
            }
            exceptions = {
                f"{target_rule['rule_id']}.{item['exception_id']}"
                for item in target_rule["exceptions"]
            }
            if (
                expected["violation_id"] is not None
                and expected["violation_id"] not in violations
            ) or (
                expected["exception_id"] is not None
                and expected["exception_id"] not in exceptions
            ):
                raise ContractError(
                    "Delivery Quality corpus oracle has a dangling identity"
                )

        return {
            "catalog_sha256": sha256_file(
                self._entry("delivery-quality-rule-catalog").canonical_instance
            ),
            "rule_count": len(rules),
            "language_profile_count": len(profile_ids),
            "projection_count": len(projection_ids),
            "primary_semantic_owner_count": len(owners),
            "primary_semantic_ownership_complete": True,
            "generated_prompt_paths": generated_prompt_paths,
            "generated_prompts_current": True,
            "semantic_case_count": len(profile_ids) * len(template_ids),
            "semantic_attempt_count_required": len(profile_ids)
            * len(template_ids)
            * 3,
        }

    def _entry(self, name: str) -> DeliveryQualityContractEntry:
        return next(entry for entry in self.entries if entry.schema_name == name)

    @staticmethod
    def _render_prompt(
        catalog: dict[str, Any],
        projections: dict[str, Any],
        projection: dict[str, Any],
    ) -> str:
        lines = [
            f"# {projection['consumer_role']}",
            "",
            f"Catalog: {catalog['catalog_id']}@{catalog['catalog_version']}",
            f"Projection: {projection['projection_id']}@{projections['projection_version']}",
            f"Projection kind: {projection['projection_kind']}",
            "",
            "## Immutable rules",
            "",
        ]
        lines.extend(
            f"- `{rule['rule_id']}`: {rule['requirement']}"
            for rule in projection["rules"]
        )
        lines.extend(
            [
                "",
                "The projection grants semantic decision authority only when its "
                "kind is evaluation. Unknown findings are Contract Gaps.",
                "",
            ]
        )
        return "\n".join(lines)

    def check(self, mechanical_fixture: str | None = None) -> dict[str, Any]:
        self._prepare_schemas()
        positive_count = 0
        negative_count = 0
        for entry in self.entries:
            self.validate(entry.schema_name, read_json(entry.positive_example))
            positive_count += 1
            try:
                self.validate(entry.schema_name, read_json(entry.negative_example))
            except ContractError:
                negative_count += 1
            else:
                raise ContractError(
                    f"Delivery Quality negative example passed: "
                    f"{entry.negative_example}"
                )
        instances = self._instances()
        if mechanical_fixture == "projection-identity-rewrite":
            instances["delivery-quality-role-projections"]["projections"][0][
                "rules"
            ][0]["requirement"] += " Rewritten."
        elif mechanical_fixture == "reviewer-ownership-missing":
            evaluation = next(
                projection
                for projection in instances[
                    "delivery-quality-role-projections"
                ]["projections"]
                if projection["projection_kind"] == "evaluation"
            )
            evaluation["rules"] = evaluation["rules"][1:]
        elif mechanical_fixture == "closed-contract-unregistered-violation":
            target_rule = next(
                rule
                for rule in instances["delivery-quality-rule-catalog"]["rules"]
                if rule["rule_id"] == "argument_chain_integrity"
            )
            self._validate_attempt_identities(
                {
                    "violation_id": "argument_chain_integrity.unregistered",
                    "exception_id": None,
                },
                target_rule,
            )
        elif mechanical_fixture not in {
            None,
            "projection-identity-valid",
            "reviewer-ownership-valid",
            "closed-contract-valid",
        }:
            raise ContractError(
                f"unknown Delivery Quality mechanical fixture: {mechanical_fixture}"
            )
        relation_data = self._validate_relations(instances)
        return {
            "authority": SUPPORTED_AUTHORITY,
            "registry_path": str(self.registry_path),
            "contract_count": len(self.entries),
            "positive_examples_validated": positive_count,
            "negative_examples_rejected": negative_count,
            "registry_complete": True,
            **relation_data,
        }

    @staticmethod
    def _attempt_signature(attempt: dict[str, Any]) -> tuple[Any, ...]:
        return (
            attempt["decision"],
            attempt["violation_id"],
            attempt["exception_id"],
            attempt["evidence_locator"],
        )

    @staticmethod
    def _expected_signature(template: dict[str, Any]) -> tuple[Any, ...]:
        expected = template["expected"]
        return (
            expected["decision"],
            expected["violation_id"],
            expected["exception_id"],
            template["evidence_locator"],
        )

    @staticmethod
    def _validate_attempt_shape(attempt: Any) -> None:
        fields = {
            "context_id",
            "process_id",
            "task_id",
            "task_sha256",
            "result_sha256",
            "provider",
            "model_revision",
            "sampling",
            "decision",
            "violation_id",
            "exception_id",
            "evidence_locator",
            "rationale",
        }
        if not isinstance(attempt, dict) or set(attempt) != fields:
            raise ContractError(
                "Delivery Quality semantic attempt has unknown or missing fields"
            )
        if attempt["decision"] not in {"pass", "fail", "pass_with_exception"}:
            raise ContractError("Delivery Quality semantic attempt decision is invalid")
        if not isinstance(attempt["process_id"], int) or attempt["process_id"] <= 0:
            raise ContractError(
                "Delivery Quality semantic attempt process identity is invalid"
            )
        for field in (
            "context_id",
            "task_id",
            "task_sha256",
            "result_sha256",
            "provider",
            "model_revision",
            "sampling",
            "evidence_locator",
            "rationale",
        ):
            if not isinstance(attempt[field], str) or not attempt[field]:
                raise ContractError(
                    f"Delivery Quality semantic attempt {field} is empty"
                )
        for field in ("task_sha256", "result_sha256"):
            if len(attempt[field]) != 64 or any(
                character not in "0123456789abcdef"
                for character in attempt[field]
            ):
                raise ContractError(
                    f"Delivery Quality semantic attempt {field} is invalid"
                )
        for field in ("violation_id", "exception_id"):
            if attempt[field] is not None and (
                not isinstance(attempt[field], str) or not attempt[field]
            ):
                raise ContractError(
                    f"Delivery Quality semantic attempt {field} is invalid"
                )

    @staticmethod
    def _validate_attempt_identities(
        attempt: dict[str, Any],
        target_rule: dict[str, Any],
    ) -> None:
        rule_id = target_rule["rule_id"]
        violations = {
            f"{rule_id}.{item['violation_id']}"
            for item in target_rule["violations"]
        }
        exceptions = {
            f"{rule_id}.{item['exception_id']}"
            for item in target_rule["exceptions"]
        }
        violation = attempt["violation_id"]
        exception = attempt["exception_id"]
        if violation is not None and violation not in violations:
            raise ContractError(
                f"Delivery Quality semantic result cites an unregistered violation: "
                f"{violation}"
            )
        if exception is not None and exception not in exceptions:
            raise ContractError(
                f"Delivery Quality semantic result cites an unregistered exception: "
                f"{exception}"
            )

    def _mechanical_results(
        self,
        instances: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        corpus = instances["delivery-quality-conformance-corpus"]
        by_id = {
            fixture["fixture_id"]: fixture
            for fixture in corpus["mechanical_fixtures"]
        }
        results: list[dict[str, Any]] = []
        cli_path = self.project_root / "scripts" / "video_workflow.py"
        for fixture_id in (
            "projection-identity-valid",
            "projection-identity-rewrite",
            "reviewer-ownership-valid",
            "reviewer-ownership-missing",
            "closed-contract-valid",
            "closed-contract-unregistered-violation",
        ):
            command = [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(cli_path),
                "delivery-quality-contracts-check",
                "--mechanical-fixture",
                fixture_id,
            ]
            completed = subprocess.run(
                command,
                cwd=self.project_root,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=60,
            )
            if completed.returncode == 0:
                actual = "pass"
            elif completed.returncode == 20:
                actual = "fail_closed"
            else:
                actual = "unexpected_error"
            results.append({
                "fixture_id": fixture_id,
                "entry_point": "delivery-quality-contracts-check",
                "expected": by_id[fixture_id]["expected"],
                "actual": actual,
                "exit_code": completed.returncode,
                "stdout_sha256": sha256_bytes(completed.stdout.encode("utf-8")),
                "conforms": actual == by_id[fixture_id]["expected"],
            })
        return results

    def _run_reviewer_attempt(
        self,
        *,
        reviewer_adapter: Path,
        case_id: str,
        attempt_number: int,
        profile: dict[str, Any],
        template: dict[str, Any],
        target_rule: dict[str, Any],
        projection: dict[str, Any],
        reviewer_prompt: str,
    ) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        task = {
            "schema_name": "delivery-quality-reviewer-task",
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "task_id": task_id,
            "attempt_number": attempt_number,
            "subject_id": sha256_bytes(case_id.encode("utf-8")),
            "language_profile": profile,
            "input_text": template["inputs_by_profile"][profile["profile_id"]],
            "evidence_locator": template["evidence_locator"],
            "target_rule": {
                "rule_id": target_rule["rule_id"],
                "requirement": target_rule["requirement"],
                "violations": target_rule["violations"],
                "exceptions": target_rule["exceptions"],
            },
            "projection": {
                "projection_id": projection["projection_id"],
                "prompt_sha256": projection["generated_prompt"]["sha256"],
                "prompt": reviewer_prompt,
            },
        }
        task_bytes = canonical_json_bytes(task)
        command = [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(reviewer_adapter),
        ]
        process = subprocess.Popen(
            command,
            cwd=self.project_root,
            text=True,
            encoding="utf-8",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout, stderr = process.communicate(
                task_bytes.decode("utf-8"),
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise ContractError(
                f"Delivery Quality Reviewer attempt timed out: {case_id}"
            ) from exc
        if process.returncode != 0 or stderr:
            raise ContractError(
                f"Delivery Quality Reviewer adapter failed: {case_id} "
                f"(exit {process.returncode})"
            )
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ContractError(
                f"Delivery Quality Reviewer output is not JSON: {case_id}"
            ) from exc
        expected_root = {
            "schema_name",
            "schema_version",
            "provider",
            "assessment",
        }
        if not isinstance(result, dict) or set(result) != expected_root:
            raise ContractError(
                "Delivery Quality Reviewer output has unknown or missing fields"
            )
        if (
            result["schema_name"] != "delivery-quality-reviewer-result"
            or result["schema_version"] != SUPPORTED_SCHEMA_VERSION
        ):
            raise ContractError("Delivery Quality Reviewer result identity is invalid")
        provider = result["provider"]
        provider_fields = {"name", "model_revision", "sampling"}
        if (
            not isinstance(provider, dict)
            or set(provider) != provider_fields
            or any(
                not isinstance(provider[field], str) or not provider[field]
                for field in provider_fields
            )
        ):
            raise ContractError("Delivery Quality Reviewer provider identity is invalid")
        assessment = result["assessment"]
        assessment_fields = {
            "decision",
            "violation_id",
            "exception_id",
            "evidence_locator",
            "rationale",
        }
        if not isinstance(assessment, dict) or set(assessment) != assessment_fields:
            raise ContractError(
                "Delivery Quality Reviewer assessment has unknown or missing fields"
            )
        context_id = sha256_bytes(
            canonical_json_bytes(
                {
                    "task_id": task_id,
                    "process_id": process.pid,
                    "task_sha256": sha256_bytes(task_bytes),
                }
            )
        )
        return {
            "context_id": context_id,
            "process_id": process.pid,
            "task_id": task_id,
            "task_sha256": sha256_bytes(task_bytes),
            "result_sha256": sha256_bytes(stdout.encode("utf-8")),
            "provider": provider["name"],
            "model_revision": provider["model_revision"],
            "sampling": provider["sampling"],
            **assessment,
        }

    def conformance(
        self,
        *,
        reviewer_adapter_path: Path,
        output_path: Path,
        implementation_commit: str,
        track: str,
        adapter_id: str,
    ) -> dict[str, Any]:
        if (
            len(implementation_commit) != 40
            or any(character not in "0123456789abcdef" for character in implementation_commit)
        ):
            raise ContractError("Delivery Quality implementation commit is invalid")
        if track not in {"kernel", "legacy"}:
            raise ContractError("Delivery Quality implementation track is invalid")
        if not adapter_id:
            raise ContractError("Delivery Quality adapter identity is empty")
        reviewer_adapter = reviewer_adapter_path.resolve()
        if not reviewer_adapter.is_file():
            raise ContractError("Delivery Quality Reviewer adapter is missing")
        output = output_path.resolve()
        try:
            output.relative_to(self.project_root)
        except ValueError as exc:
            raise ContractError(
                "Delivery Quality conformance output must stay inside the project"
            ) from exc
        if not output.parent.is_dir():
            raise ContractError(
                "Delivery Quality conformance output parent must already exist"
            )

        self.check()
        instances = self._instances()
        catalog = instances["delivery-quality-rule-catalog"]
        corpus = instances["delivery-quality-conformance-corpus"]
        profiles_by_id = {
            profile["profile_id"]: profile
            for profile in instances["delivery-quality-language-profiles"][
                "profiles"
            ]
        }
        rules = {rule["rule_id"]: rule for rule in catalog["rules"]}
        projections = instances["delivery-quality-role-projections"]
        evaluation_projection_by_rule: dict[str, dict[str, Any]] = {}
        for projection in projections["projections"]:
            if projection["projection_kind"] != "evaluation":
                continue
            for rule in projection["rules"]:
                evaluation_projection_by_rule[rule["rule_id"]] = projection
        semantic_results: list[dict[str, Any]] = []
        failures: list[str] = []
        context_ids: set[str] = set()
        for profile_id in corpus["applicable_language_profiles"]:
            for template in corpus["case_templates"]:
                case_id = f"{profile_id}.{template['template_id']}"
                target_rule = rules[template["target_rule_id"]]
                evaluation_projection = evaluation_projection_by_rule[
                    template["target_rule_id"]
                ]
                signatures: list[tuple[Any, ...]] = []
                materialized_attempts: list[dict[str, Any]] = []
                prompt_path = self._resolve_project_path(
                    evaluation_projection["generated_prompt"]["path"],
                    "generated prompt",
                )
                for attempt_number in range(1, 4):
                    attempt = self._run_reviewer_attempt(
                        reviewer_adapter=reviewer_adapter,
                        case_id=case_id,
                        attempt_number=attempt_number,
                        profile=profiles_by_id[profile_id],
                        template=template,
                        target_rule=target_rule,
                        projection=evaluation_projection,
                        reviewer_prompt=prompt_path.read_text(encoding="utf-8"),
                    )
                    self._validate_attempt_shape(attempt)
                    self._validate_attempt_identities(attempt, target_rule)
                    if attempt["context_id"] in context_ids:
                        raise ContractError(
                            "Delivery Quality Reviewer context identity was reused"
                        )
                    context_ids.add(attempt["context_id"])
                    signatures.append(self._attempt_signature(attempt))
                    materialized_attempts.append(
                        {
                            "context_id": attempt["context_id"],
                            "process_id": attempt["process_id"],
                            "task_id": attempt["task_id"],
                            "task_sha256": attempt["task_sha256"],
                            "result_sha256": attempt["result_sha256"],
                            "provider": attempt["provider"],
                            "model_revision": attempt["model_revision"],
                            "sampling": attempt["sampling"],
                            "decision": attempt["decision"],
                            "violation_id": attempt["violation_id"],
                            "exception_id": attempt["exception_id"],
                            "evidence_locator": attempt["evidence_locator"],
                            "rationale": attempt["rationale"],
                        }
                    )
                semantic_variance = len(set(signatures)) != 1
                conforms = (
                    not semantic_variance
                    and signatures[0] == self._expected_signature(template)
                )
                if semantic_variance:
                    failures.append(f"semantic_variance:{case_id}")
                elif not conforms:
                    failures.append(f"oracle_mismatch:{case_id}")
                semantic_results.append(
                    {
                        "case_id": case_id,
                        "profile_id": profile_id,
                        "target_rule_id": template["target_rule_id"],
                        "entry_point": "semantic-reviewer-task-entry",
                        "projection_id": evaluation_projection["projection_id"],
                        "task_projection_sha256": evaluation_projection[
                            "generated_prompt"
                        ]["sha256"],
                        "attempts": materialized_attempts,
                        "conforms": conforms,
                        "semantic_variance": semantic_variance,
                    }
                )

        mechanical_results = self._mechanical_results(instances)
        failures.extend(
            f"mechanical_failure:{result['fixture_id']}"
            for result in mechanical_results
            if not result["conforms"]
        )
        projections_path = self._entry(
            "delivery-quality-role-projections"
        ).canonical_instance
        corpus_path = self._entry(
            "delivery-quality-conformance-corpus"
        ).canonical_instance
        adapter_sha256 = sha256_file(reviewer_adapter)
        report = {
            "schema_name": "delivery-quality-conformance-report",
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "report_id": sha256_bytes(
                canonical_json_bytes(
                    {
                        "implementation_commit": implementation_commit,
                        "reviewer_adapter_sha256": adapter_sha256,
                    }
                )
            )[:24],
            "authority": "implementation_qualification_only",
            "implementation": {
                "track": track,
                "activation_status": SUPPORTED_AUTHORITY,
                "adapter_id": adapter_id,
                "adapter_sha256": adapter_sha256,
                "commit": implementation_commit,
            },
            "bindings": {
                "catalog_sha256": sha256_file(
                    self._entry(
                        "delivery-quality-rule-catalog"
                    ).canonical_instance
                ),
                "projections_sha256": sha256_file(projections_path),
                "corpus_sha256": sha256_file(corpus_path),
            },
            "semantic_results": semantic_results,
            "mechanical_results": mechanical_results,
            "failures": failures,
            "overall_decision": "pass" if not failures else "fail",
        }
        self.validate("delivery-quality-conformance-report", report)
        write_json_atomic(output, report)
        if failures:
            raise DeliveryQualityConformanceFailed(
                "Delivery Quality conformance found blocking failures",
                data={
                    "evidence_path": str(output),
                    "failure_count": len(failures),
                    "semantic_variance": any(
                        failure.startswith("semantic_variance:")
                        for failure in failures
                    ),
                },
            )
        return report
