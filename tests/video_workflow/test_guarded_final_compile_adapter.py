from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
import uuid

import fitz

from tests.video_workflow._test_run import module_test_root

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video2pdf_workflow_kernel.cli import _parser
from video2pdf_workflow_kernel.errors import CompileDependencyGap, ContractError
from video2pdf_workflow_kernel.delivery_quality import DeliveryQualityRegistry
from video2pdf_workflow_kernel.final_compile import GuardedFinalCompileProvider


TEST_RUNS = module_test_root(PROJECT_ROOT)
ADAPTER = PROJECT_ROOT / "scripts/guarded_final_compile_adapter.py"
FAKE_ENGINE = PROJECT_ROOT / "tests/video_workflow/fixtures/guarded-compile/fake_xelatex.py"
PACKAGE_INVENTORY = PROJECT_ROOT / "tests/video_workflow/fixtures/guarded-compile/package-inventory.json"
SYSTEM_FONT = Path("C:/Windows/Fonts/arial.ttf")
CLI = PROJECT_ROOT / "scripts/video_workflow.py"


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def fingerprint(value: dict, field: str) -> str:
    return hashlib.sha256(canonical_bytes({key: item for key, item in value.items() if key != field})).hexdigest()


def attacker_git_environment(root: Path, payload: bytes) -> dict[str, str]:
    repository = root / "adapter-git-authority"
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    tracked = repository / "scripts/guarded_final_compile_adapter.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(payload)
    subprocess.run(["git", "-C", str(repository), "add", "scripts/guarded_final_compile_adapter.py"], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"],
                   check=True, capture_output=True)
    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "git.cmd").write_text(
        "@echo attacker-controlled-git\r\n@exit /b 0\r\n", encoding="ascii"
    )
    return dict(
        os.environ,
        GIT_DIR=str(repository / ".git"),
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="core.hooksPath",
        GIT_CONFIG_VALUE_0=str(root),
        PATH=str(fake_bin),
        ProgramFiles=str(root / "attacker-program-files"),
        **{"ProgramFiles(x86)": str(root / "attacker-program-files-x86")},
    )


class GuardedFinalCompileAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = TEST_RUNS / f"final-adapter-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        self.source = self.root / "source/main.tex"
        self.source.parent.mkdir()
        self.source.write_text("\\documentclass{article}\\begin{document}Core claim\\end{document}\n", encoding="utf-8")
        self.output = self.root / "output"
        manifest = {
            "schema_name": "final-compile-manifest", "schema_version": "1.0.0",
            "activation_status": "target_only", "mode": "final",
            "precompile_text_seal_sha256": "1" * 64,
            "entries": [{"logical_id": "integrated_main", "generation": 1,
                         "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                         "source_path": str(self.source), "staging_path": "main.tex"}],
            "approved_runtime_inputs": [{"path": str(SYSTEM_FONT.resolve()),
                                          "sha256": hashlib.sha256(SYSTEM_FONT.read_bytes()).hexdigest(),
                                          "classification": "registered_system_font"}],
        }
        manifest["manifest_sha256"] = fingerprint(manifest, "manifest_sha256")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_bytes(canonical_bytes(manifest))
        plan = {
            "schema_name": "text-origin-plan", "schema_version": "1.0.0",
            "precompile_text_seal_sha256": "1" * 64,
            "sealed_items": [{"item_id": "main", "exact_utf8_text": "Core claim"}],
            "page_count": 1,
            "extractor_suite": [{"extractor_id": "pymupdf-text-v1", "extractor_sha256": "2" * 64}],
            "rendered_objects": [{"object_id": "page-1-text-1", "page": 1,
                                  "object_kind": "pdf_text_run", "bbox": [72.0, 60.17499923706055, 124.55799865722656, 75.28900146484375],
                                  "exact_utf8_text": "Core claim", "extractor_id": "pymupdf-text-v1",
                                  "evidence_locator": "page:1/block:1/line:1/span:1"}],
            "edges": [{"edge_id": "main-origin", "disposition": "sealed_origin",
                       "sealed_item_id": "main", "sealed_text_utf8": "Core claim",
                       "rendered_object_ids": ["page-1-text-1"], "recipe": "exact_utf8"}],
        }
        plan["plan_sha256"] = fingerprint(plan, "plan_sha256")
        self.plan = self.root / "plan.json"
        self.plan.write_bytes(canonical_bytes(plan))
        policy = {
            "schema_name": "compile-runtime-policy", "schema_version": "1.0.0", "kernel_version": "2.0.0",
            "policy_id": "fixture-miktex-runtime", "policy_version": "1.0.0", "runtime_family": "miktex",
            "engine": {"name": "xelatex-fixture", "version": "fixture-1",
                       "executable": str(Path(sys.executable).resolve()),
                       "sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                       "prefix_args": [str(FAKE_ENGINE.resolve())],
                       "prefix_file_fingerprints": [{"path": str(FAKE_ENGINE.resolve()),
                           "sha256": hashlib.sha256(FAKE_ENGINE.read_bytes()).hexdigest()}]},
            "package_inventory": {"version": "fixture-1", "path": str(PACKAGE_INVENTORY.resolve()),
                                  "sha256": hashlib.sha256(PACKAGE_INVENTORY.read_bytes()).hexdigest()},
            "system_fonts": [{"path": str(SYSTEM_FONT.resolve()), "sha256": hashlib.sha256(SYSTEM_FONT.read_bytes()).hexdigest()}],
            "allowed_packages": ["article"],
            "allowed_runtime_roots": [str(Path(sys.executable).resolve().parent)],
            "shell_escape": False, "automatic_package_install": False,
            "dependency_discovery_policy_version": "recorder-closure-v1",
        }
        policy["policy_sha256"] = fingerprint(policy, "policy_sha256")
        self.policy = self.root / "runtime-policy.json"
        self.policy.write_bytes(canonical_bytes(policy))
        manifest["runtime_policy"] = {
            "path": str(self.policy.resolve()),
            "sha256": hashlib.sha256(self.policy.read_bytes()).hexdigest(),
        }
        manifest["manifest_sha256"] = fingerprint(manifest, "manifest_sha256")
        self.manifest.write_bytes(canonical_bytes(manifest))
        request = {
            "schema_name": "guarded-final-compile-request", "schema_version": "1.0.0",
            "activation_status": "target_only", "precompile_text_seal_sha256": "1" * 64,
            "generation_set_sha256": "3" * 64, "compile_provider": {"provider_id": "test", "provider_sha256": "4" * 64},
            "compile_manifest_path": str(self.manifest), "compile_manifest_sha256": manifest["manifest_sha256"],
            "text_origin_plan_path": str(self.plan), "text_origin_plan_sha256": plan["plan_sha256"],
            "runtime_policy_path": str(self.policy), "runtime_policy_sha256": hashlib.sha256(self.policy.read_bytes()).hexdigest(),
            "compiled_at": "2026-08-11T13:00:00Z", "output_root": str(self.output),
        }
        self.request = self.root / "compile-request.json"
        self.request.write_bytes(canonical_bytes(request))

    def _run(self, *, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-X", "utf8", "-B", str(ADAPTER), str(self.request)], cwd=PROJECT_ROOT,
                              text=True, encoding="utf-8", capture_output=True, check=False, env=environment)

    def _write_policy(self, policy: dict) -> None:
        policy["policy_sha256"] = fingerprint(policy, "policy_sha256")
        self.policy.write_bytes(canonical_bytes(policy))
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["runtime_policy"] = {
            "path": str(self.policy.resolve()),
            "sha256": hashlib.sha256(self.policy.read_bytes()).hexdigest(),
        }
        manifest["manifest_sha256"] = fingerprint(manifest, "manifest_sha256")
        self.manifest.write_bytes(canonical_bytes(manifest))
        request = json.loads(self.request.read_text(encoding="utf-8"))
        request["runtime_policy_sha256"] = hashlib.sha256(self.policy.read_bytes()).hexdigest()
        request["compile_manifest_sha256"] = manifest["manifest_sha256"]
        self.request.write_bytes(canonical_bytes(request))

    def _write_engine_directive(self, directive: str) -> None:
        self.source.write_text(
            "\\documentclass{article}\\begin{document}Core claim\\end{document}\n"
            f"% {directive}\n",
            encoding="utf-8",
        )
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["entries"][0]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        manifest["manifest_sha256"] = fingerprint(manifest, "manifest_sha256")
        self.manifest.write_bytes(canonical_bytes(manifest))
        request = json.loads(self.request.read_text(encoding="utf-8"))
        request["compile_manifest_sha256"] = manifest["manifest_sha256"]
        self.request.write_bytes(canonical_bytes(request))

    def _declare_raster(
        self,
        bbox: list[float],
        *,
        include_source: bool = True,
        alpha: bool = False,
        extension: str = ".png",
    ) -> None:
        source_sha = "9" * 64
        if include_source:
            image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), alpha)
            image.clear_with(0x88CCFF)
            if alpha:
                image.set_alpha(bytes([128]) * (image.width * image.height))
            source = self.source.parent / f"figure{extension}"
            image.save(source)
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
            manifest["entries"].append({"logical_id": "figure_asset", "generation": 1,
                "sha256": source_sha, "source_path": str(source),
                "staging_path": f"figure{extension}"})
            manifest["manifest_sha256"] = fingerprint(manifest, "manifest_sha256")
            self.manifest.write_bytes(canonical_bytes(manifest))
            request = json.loads(self.request.read_text(encoding="utf-8"))
            request["compile_manifest_sha256"] = manifest["manifest_sha256"]
            self.request.write_bytes(canonical_bytes(request))
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        plan["extractor_suite"].append({"extractor_id": "declared-raster-v1", "extractor_sha256": "5" * 64})
        plan["sealed_items"].append({"item_id": "figure", "exact_utf8_text": "Raster words",
                                      "representation": "authoritative_raster_text"})
        plan["rendered_objects"].append({"object_id": "page-1-raster-1", "page": 1,
            "object_kind": "declared_raster_text", "bbox": bbox, "exact_utf8_text": "Raster words",
            "extractor_id": "declared-raster-v1", "evidence_locator": "page:1/image:1"})
        plan["rendered_objects"][-1].update({"source_artifact_logical_id": "figure_asset",
            "source_generation": 1, "source_sha256": source_sha,
            "source_path": f"figure{extension}"})
        plan["edges"].append({"edge_id": "figure-origin", "disposition": "sealed_origin",
            "sealed_item_id": "figure", "sealed_text_utf8": "Raster words",
            "rendered_object_ids": ["page-1-raster-1"], "recipe": "exact_utf8"})
        plan["plan_sha256"] = fingerprint(plan, "plan_sha256")
        self.plan.write_bytes(canonical_bytes(plan))
        request = json.loads(self.request.read_text(encoding="utf-8"))
        request["text_origin_plan_sha256"] = plan["plan_sha256"]
        self.request.write_bytes(canonical_bytes(request))

    def test_public_adapter_compiles_and_derives_complete_evidence(self) -> None:
        completed = self._run()
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        required = {"final.pdf", "compile-provenance.json", "compile-recorder.fls",
                    "rendered-text-object-inventory.json", "text-origin-trace.json",
                    "final-artifact-seal.json", "rendered_pages"}
        self.assertTrue(required.issubset({path.name for path in self.output.iterdir()}))
        with fitz.open(self.output / "final.pdf") as document:
            self.assertEqual(1, document.page_count)
            self.assertIn("Core claim", document[0].get_text())
        rendered = json.loads((self.output / "rendered-text-object-inventory.json").read_text(encoding="utf-8"))
        self.assertEqual([1], rendered["coverage"]["pages_scanned"])
        self.assertEqual("Core claim", rendered["objects"][0]["exact_utf8_text"])
        self.assertTrue((self.output / "rendered_pages/page_001.png").is_file())

    def test_public_adapter_records_successful_engine_stderr_without_exposing_text(self) -> None:
        warning = b"fixture log4cxx root overlap warning\n"
        self._write_engine_directive("VIDEO2PDF_FIXTURE_STDERR")
        completed = self._run()
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        provenance_path = self.output / "compile-provenance.json"
        provenance_bytes = provenance_path.read_bytes()
        provenance = json.loads(provenance_bytes)
        combined_warning = warning * 3
        self.assertEqual({
            "byte_length": len(combined_warning),
            "sha256": hashlib.sha256(combined_warning).hexdigest(),
        }, provenance["engine_stderr"])
        self.assertNotIn(warning, provenance_bytes)
        self.assertNotIn(warning.decode("ascii"), completed.stdout + completed.stderr)

    def test_public_adapter_rejects_nonzero_engine_exit(self) -> None:
        self._write_engine_directive("VIDEO2PDF_FIXTURE_NONZERO_EXIT")
        completed = self._run()
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("guarded compile engine failed", completed.stderr)
        self.assertFalse((self.output / "compile-provenance.json").exists())

    def test_public_adapter_rejects_missing_engine_outputs(self) -> None:
        self._write_engine_directive("VIDEO2PDF_FIXTURE_OMIT_PDF")
        completed = self._run()
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("guarded compile omitted required output", completed.stderr)
        self.assertFalse((self.output / "compile-provenance.json").exists())

    def test_registered_miktex_uses_the_diagnostic_runtime_identity(self) -> None:
        spec = importlib.util.spec_from_file_location("guarded_final_compile_adapter", ADAPTER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        staging = self.root / "registered-miktex-staging"
        staging.mkdir()
        entry = staging / "main.tex"
        entry.write_text("fixture", encoding="utf-8")
        captured_command: list[str] = []
        captured_environment: dict[str, str] = {}
        invocation_count = 0

        document = fitz.open()
        document.new_page()
        fixture_pdf = document.tobytes()
        document.close()

        def complete(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            nonlocal invocation_count
            invocation_count += 1
            captured_command.extend(command)
            captured_environment.update(kwargs["env"])
            (staging / "main.pdf").write_bytes(fixture_pdf)
            (staging / "main.log").write_text(
                "Output written on main.pdf (1 page).\n", encoding="utf-8"
            )
            (staging / "main.fls").write_text(f"INPUT {entry}\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        policy = {
            "policy_id": "miktex-xelatex-runtime",
            "engine": {"executable": str(Path(sys.executable)), "prefix_args": []},
            "allowed_runtime_roots": [r"D:\kits\MiKTex"],
            "system_fonts": [],
        }
        with mock.patch.object(adapter.subprocess, "run", side_effect=complete):
            adapter.compile_pdf(staging, entry, policy)

        self.assertEqual(3, invocation_count)
        installer_index = captured_command.index("--disable-installer")
        self.assertEqual(
            ["--miktex-disable-maintenance", "--miktex-disable-diagnose"],
            captured_command[installer_index - 2:installer_index],
        )
        configured = Path(r"D:\kits\MiKTex\video2pdf-runtime-v2")
        self.assertEqual(str(configured / "common-config"), captured_environment["MIKTEX_COMMONCONFIG"])
        self.assertEqual(str(configured / "common-data"), captured_environment["MIKTEX_COMMONDATA"])
        self.assertEqual(str(configured / "user-config"), captured_environment["MIKTEX_USERCONFIG"])
        self.assertEqual(str(configured / "user-data"), captured_environment["MIKTEX_USERDATA"])
        self.assertEqual(str(configured / "user-install"), captured_environment["MIKTEX_USERINSTALL"])
        self.assertEqual(str(configured / "user-data"), captured_environment["USERPROFILE"])
        self.assertEqual(str(configured / "user-data"), captured_environment["HOME"])
        self.assertEqual(str(staging / "engine-profile"), captured_environment["MIKTEX_USERLOGDIRECTORY"])

    def test_compile_waits_when_engine_returns_before_outputs_stabilize(self) -> None:
        spec = importlib.util.spec_from_file_location("guarded_final_compile_adapter_delayed", ADAPTER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        staging = self.root / "delayed-output-staging"
        staging.mkdir()
        entry = staging / "main.tex"
        entry.write_text("fixture", encoding="utf-8")
        document = fitz.open()
        document.new_page()
        fixture_pdf = document.tobytes()
        document.close()
        writers: list[threading.Thread] = []

        def return_before_output_is_complete(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            (staging / "main.pdf").write_bytes(b"%PDF-partial")
            (staging / "main.log").write_text("compile still running\n", encoding="utf-8")
            (staging / "main.fls").write_text("INPUT partial\n", encoding="utf-8")

            def finish_output() -> None:
                time.sleep(0.02)
                (staging / "main.pdf").write_bytes(fixture_pdf)
                (staging / "main.fls").write_text(f"INPUT {entry}\n", encoding="utf-8")
                (staging / "main.log").write_text(
                    "Output written on main.pdf (1 page).\n", encoding="utf-8"
                )

            writer = threading.Thread(target=finish_output)
            writer.start()
            writers.append(writer)
            return subprocess.CompletedProcess(command, 0, b"", b"")

        policy = {
            "policy_id": "fixture-runtime",
            "engine": {"executable": str(Path(sys.executable)), "prefix_args": []},
            "allowed_runtime_roots": [str(Path(sys.executable).resolve().parent)],
            "system_fonts": [],
        }
        with (
            mock.patch.object(adapter.subprocess, "run", side_effect=return_before_output_is_complete),
            mock.patch.object(adapter, "COMPILE_OUTPUT_TIMEOUT_SECONDS", 1.0),
            mock.patch.object(adapter, "COMPILE_OUTPUT_POLL_SECONDS", 0.005),
        ):
            pdf, recorder, _, _ = adapter.compile_pdf(staging, entry, policy)

        for writer in writers:
            writer.join()
        self.assertEqual(fixture_pdf, pdf.read_bytes())
        self.assertIn(f"INPUT {entry}", recorder.read_text(encoding="utf-8"))

    def test_compile_fails_when_returned_outputs_never_stabilize(self) -> None:
        spec = importlib.util.spec_from_file_location("guarded_final_compile_adapter_unstable", ADAPTER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        staging = self.root / "unstable-output-staging"
        staging.mkdir()
        entry = staging / "main.tex"
        entry.write_text("fixture", encoding="utf-8")

        def return_with_incomplete_output(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            (staging / "main.pdf").write_bytes(b"%PDF-partial")
            (staging / "main.log").write_text("compile still running\n", encoding="utf-8")
            (staging / "main.fls").write_text("INPUT partial\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        policy = {
            "policy_id": "fixture-runtime",
            "engine": {"executable": str(Path(sys.executable)), "prefix_args": []},
            "allowed_runtime_roots": [str(Path(sys.executable).resolve().parent)],
            "system_fonts": [],
        }
        with (
            mock.patch.object(adapter.subprocess, "run", side_effect=return_with_incomplete_output),
            mock.patch.object(adapter, "COMPILE_OUTPUT_TIMEOUT_SECONDS", 0.05),
            mock.patch.object(adapter, "COMPILE_OUTPUT_POLL_SECONDS", 0.005),
        ):
            with self.assertRaisesRegex(
                adapter.AdapterError,
                "guarded compile output did not stabilize before timeout: LaTeX log has no normal completion marker",
            ):
                adapter.compile_pdf(staging, entry, policy)

    def test_public_adapter_rejects_manifest_drift_and_incomplete_pages(self) -> None:
        request = json.loads(self.request.read_text(encoding="utf-8"))
        request["compile_manifest_sha256"] = "f" * 64
        self.request.write_bytes(canonical_bytes(request))
        drift = self._run()
        self.assertNotEqual(0, drift.returncode)
        self.assertIn("compile manifest identity is stale", drift.stderr)

        request["compile_manifest_sha256"] = json.loads(self.manifest.read_text(encoding="utf-8"))["manifest_sha256"]
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        plan["page_count"] = 2
        plan["plan_sha256"] = fingerprint(plan, "plan_sha256")
        self.plan.write_bytes(canonical_bytes(plan))
        request["text_origin_plan_sha256"] = plan["plan_sha256"]
        self.request.write_bytes(canonical_bytes(request))
        incomplete = self._run()
        self.assertNotEqual(0, incomplete.returncode)
        self.assertIn("page count", incomplete.stderr)

    def test_public_adapter_rejects_unsafe_paths_shell_and_secret_echo(self) -> None:
        request = json.loads(self.request.read_text(encoding="utf-8"))
        policy = json.loads(self.policy.read_text(encoding="utf-8"))
        policy["shell_escape"] = True
        self._write_policy(policy)
        shell = self._run()
        self.assertNotEqual(0, shell.returncode)
        self.assertIn("runtime policy validation failed", shell.stderr)

        policy["shell_escape"] = False
        self._write_policy(policy)
        request = json.loads(self.request.read_text(encoding="utf-8"))
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["entries"][0]["staging_path"] = "../escape.tex"
        manifest["manifest_sha256"] = fingerprint(manifest, "manifest_sha256")
        self.manifest.write_bytes(canonical_bytes(manifest))
        request["compile_manifest_sha256"] = manifest["manifest_sha256"]
        self.request.write_bytes(canonical_bytes(request))
        traversal = self._run()
        self.assertNotEqual(0, traversal.returncode)
        self.assertIn("staging path escapes", traversal.stderr)

        secret = "TOP_SECRET_DO_NOT_ECHO"
        request["schema_version"] = "9.9.9"
        request["secret_probe"] = secret
        self.request.write_bytes(canonical_bytes(request))
        sanitized = self._run()
        self.assertNotEqual(0, sanitized.returncode)
        self.assertNotIn(secret, sanitized.stdout + sanitized.stderr)

    def test_public_adapter_authenticates_complete_runtime_policy_closure(self) -> None:
        mutations = (
            ("engine SHA", lambda p: p["engine"].__setitem__("sha256", "0" * 64)),
            ("prefix fingerprint", lambda p: p["engine"]["prefix_file_fingerprints"][0].__setitem__("sha256", "0" * 64)),
            ("package inventory", lambda p: p["package_inventory"].__setitem__("sha256", "0" * 64)),
            ("runtime root", lambda p: p.__setitem__("allowed_runtime_roots", [str(self.root)])),
        )
        original = json.loads(self.policy.read_text(encoding="utf-8"))
        for label, mutate in mutations:
            with self.subTest(label=label):
                policy = json.loads(json.dumps(original))
                mutate(policy)
                self._write_policy(policy)
                completed = self._run()
                self.assertNotEqual(0, completed.returncode)
                self.assertIn("runtime policy", completed.stderr.casefold())

    def test_public_adapter_derives_declared_raster_from_embedded_manifest_figure(self) -> None:
        self._declare_raster([200.0, 100.0, 300.0, 200.0])
        completed = self._run()
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        inventory = json.loads((self.output / "rendered-text-object-inventory.json").read_text(encoding="utf-8"))
        raster = [item for item in inventory["objects"] if item["object_kind"] == "declared_raster_text"]
        self.assertEqual("Raster words", raster[0]["exact_utf8_text"])

    def test_public_adapter_matches_alpha_raster_with_embedded_soft_mask(self) -> None:
        self._declare_raster([200.0, 100.0, 300.0, 200.0], alpha=True)
        completed = self._run()
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def test_public_adapter_accepts_declared_jpeg_raster(self) -> None:
        self._declare_raster([200.0, 100.0, 300.0, 200.0], extension=".jpg")
        completed = self._run()
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def test_public_adapter_rejects_unbound_declared_raster(self) -> None:
        self._declare_raster([200.0, 100.0, 300.0, 200.0], include_source=False)
        missing = self._run()
        self.assertNotEqual(0, missing.returncode)
        self.assertIn("raster source", missing.stderr)

    def test_public_adapter_rejects_wrong_bbox_declared_raster(self) -> None:
        self._declare_raster([201.0, 100.0, 300.0, 200.0])
        wrong_bbox = self._run()
        self.assertNotEqual(0, wrong_bbox.returncode)
        self.assertIn("raster bbox", wrong_bbox.stderr)

    def test_public_adapter_rejects_swapped_declared_raster_source_identity(self) -> None:
        sources = []
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        for name, color in (("figure_a", 0xFF0000), ("figure_b", 0x0000FF)):
            image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
            image.clear_with(color)
            source = self.source.parent / f"{name}.png"
            image.save(source)
            identity = {"logical_id": name, "generation": 1,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
            sources.append(identity)
            manifest["entries"].append({**identity, "source_path": str(source), "staging_path": f"{name}.png"})
        manifest["manifest_sha256"] = fingerprint(manifest, "manifest_sha256")
        self.manifest.write_bytes(canonical_bytes(manifest))
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        plan["extractor_suite"].append({"extractor_id": "declared-raster-v1", "extractor_sha256": "5" * 64})
        plan["rendered_objects"].extend([
            {"object_id": "raster-a", "page": 1, "object_kind": "declared_raster_text",
             "bbox": [200.0, 100.0, 300.0, 200.0], "exact_utf8_text": "A",
             "extractor_id": "declared-raster-v1", "evidence_locator": "page:1/image:1",
             "source_artifact_logical_id": sources[1]["logical_id"],
             "source_generation": sources[1]["generation"], "source_sha256": sources[1]["sha256"],
             "source_path": "figure_b.png"},
            {"object_id": "raster-b", "page": 1, "object_kind": "declared_raster_text",
             "bbox": [320.0, 100.0, 420.0, 200.0], "exact_utf8_text": "B",
             "extractor_id": "declared-raster-v1", "evidence_locator": "page:1/image:2",
             "source_artifact_logical_id": sources[0]["logical_id"],
             "source_generation": sources[0]["generation"], "source_sha256": sources[0]["sha256"],
             "source_path": "figure_a.png"},
        ])
        plan["plan_sha256"] = fingerprint(plan, "plan_sha256")
        self.plan.write_bytes(canonical_bytes(plan))
        request = json.loads(self.request.read_text(encoding="utf-8"))
        request["compile_manifest_sha256"] = manifest["manifest_sha256"]
        request["text_origin_plan_sha256"] = plan["plan_sha256"]
        self.request.write_bytes(canonical_bytes(request))
        swapped = self._run()
        self.assertNotEqual(0, swapped.returncode)
        self.assertIn("raster source identity", swapped.stderr)

    def test_public_adapter_uses_minimal_named_runtime_environment(self) -> None:
        environment = dict(os.environ)
        system_root = environment.get("SYSTEMROOT", str(Path(sys.executable).anchor))
        windows_directory = environment.get("WINDIR", system_root)
        allowed_root = Path(sys.executable).resolve().parent
        valid_miktex_paths = os.pathsep.join(
            (str(allowed_root), str(allowed_root / "fixture-runtime-cache"))
        )
        environment.update({
            "SYSTEMROOT": system_root,
            "WINDIR": windows_directory,
            "MIKTEX_VALID_PATHS": valid_miktex_paths,
            "MIKTEX_USERDATA": str(allowed_root),
            "MIKTEX_MIXED_AUTHORITY": os.pathsep.join(
                (str(allowed_root), str(self.root))
            ),
            "UNNAMED_CREDENTIAL": "SHOULD_NOT_CROSS",
            "PYTHONPATH": "SHOULD_NOT_CROSS",
            "TEXINPUTS": "SHOULD_NOT_CROSS",
            "PATH": "SHOULD_NOT_CROSS",
            "COMSPEC": "SHOULD_NOT_CROSS",
            "APPDATA": "SHOULD_NOT_CROSS",
            "USERPROFILE": "SHOULD_NOT_CROSS",
            "HOME": "SHOULD_NOT_CROSS",
            "HOMEDRIVE": "X:",
            "HOMEPATH": "\\hostile-profile",
            "USERNAME": "hostile-user",
            "USERDOMAIN": "HOSTILE-DOMAIN",
            "SYSTEMDRIVE": "X:",
            "TEMP": "SHOULD_NOT_CROSS",
            "TMP": "SHOULD_NOT_CROSS",
        })
        completed = self._run(environment=environment)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        provenance = json.loads((self.output / "compile-provenance.json").read_text(encoding="utf-8"))
        runtime_environment = provenance["runtime_environment"]
        staging = (self.output / "compiler-staging").resolve()
        engine_temp = (staging / "engine-temp").resolve()
        self.assertEqual(system_root, runtime_environment["SYSTEMROOT"])
        self.assertEqual(windows_directory, runtime_environment["WINDIR"])
        self.assertEqual(valid_miktex_paths, runtime_environment["MIKTEX_VALID_PATHS"])
        self.assertEqual(str(allowed_root), runtime_environment["MIKTEX_USERDATA"])
        self.assertEqual(str(engine_temp), runtime_environment["TEMP"])
        self.assertEqual(str(engine_temp), runtime_environment["TMP"])
        self.assertEqual(str(allowed_root), runtime_environment["USERPROFILE"])
        self.assertEqual(str(allowed_root), runtime_environment["HOME"])
        self.assertEqual(allowed_root.drive, runtime_environment["HOMEDRIVE"])
        self.assertEqual(
            str(allowed_root)[len(allowed_root.drive):],
            runtime_environment["HOMEPATH"],
        )
        self.assertEqual("video2pdf", runtime_environment["USERNAME"])
        self.assertEqual("LOCAL", runtime_environment["USERDOMAIN"])
        self.assertEqual(Path(system_root).drive, runtime_environment["SYSTEMDRIVE"])
        self.assertTrue(engine_temp.is_dir())
        self.assertEqual(staging, engine_temp.parent)
        for forbidden in (
            "MIKTEX_MIXED_AUTHORITY", "UNNAMED_CREDENTIAL", "PYTHONPATH", "TEXINPUTS",
            "PATH", "COMSPEC", "APPDATA",
        ):
            self.assertNotIn(forbidden, runtime_environment)


class GuardedFinalCompileProviderAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = TEST_RUNS / f"final-provider-authority-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        self.provider = GuardedFinalCompileProvider(PROJECT_ROOT)

    def _completed_run(self) -> tuple[Path, Path]:
        from tests.video_workflow.test_single_section_production import (
            SingleSectionProductionTests,
        )

        fixture = SingleSectionProductionTests(
            methodName="test_public_plan_and_advance_reach_guarded_diagnostic_compile"
        )
        fixture.setUp()
        fixture._cli = lambda *args: (
            subprocess.CompletedProcess(args, 0, "", ""),
            {
                "classification": "diagnostic_compile_ready",
                "data": {"delivery_authority": False},
            },
        )
        fixture.test_public_plan_and_advance_reach_guarded_diagnostic_compile()
        run_dir = fixture.run_dir
        quality = run_dir / "review/quality"
        quality.mkdir(parents=True)
        return run_dir, quality

    def test_provider_requires_registered_adapter_path_and_current_sha(self) -> None:
        identity = self.provider._validate_adapter_authority(ADAPTER)
        self.assertEqual(hashlib.sha256(ADAPTER.read_bytes()).hexdigest(), identity["adapter_sha256"])
        environment = attacker_git_environment(self.root, b"attacker-controlled-adapter")
        with mock.patch.dict(os.environ, environment, clear=True):
            injected = self.provider._validate_adapter_authority(ADAPTER)
        self.assertEqual(identity, injected)
        with self.assertRaisesRegex(ContractError, "registered Final Compile adapter"):
            self.provider._validate_adapter_authority(FAKE_ENGINE)

    def test_manifest_requires_runtime_policy_binding(self) -> None:
        fixture = GuardedFinalCompileAdapterTests(
            methodName="test_public_adapter_compiles_and_derives_complete_evidence"
        )
        fixture.setUp()
        manifest = json.loads(fixture.manifest.read_text(encoding="utf-8"))
        manifest.pop("runtime_policy")
        manifest["manifest_sha256"] = fingerprint(manifest, "manifest_sha256")
        with self.assertRaises(ContractError):
            DeliveryQualityRegistry(PROJECT_ROOT).validate("final-compile-manifest", manifest)

    def test_public_cli_requires_runtime_policy(self) -> None:
        action = next(action for action in _parser()._subparsers._group_actions[0].choices[
            "delivery-quality-final-compile"
        ]._actions if "--runtime-policy" in action.option_strings)
        self.assertTrue(action.required)

    def test_provider_rejects_external_or_linked_workspace_before_write(self) -> None:
        synthetic_precompile = self.root / "synthetic/quality"
        synthetic_precompile.mkdir(parents=True)
        synthetic_workspace = self.root / "synthetic/final"
        with self.assertRaisesRegex(ContractError, "Workflow Run"):
            self.provider._validate_workspace_authority(
                synthetic_precompile,
                synthetic_workspace,
                self.root / "missing-runtime-policy.json",
            )
        self.assertFalse(synthetic_workspace.exists())

        run_root, precompile = self._completed_run()
        runtime_policy = run_root / "workflow/compile-runtime-policy.json"
        external = self.root.parent / f"outside-{uuid.uuid4().hex}" / "final"
        with self.assertRaisesRegex(ContractError, "workspace"):
            self.provider._validate_workspace_authority(
                precompile, external, runtime_policy
            )
        self.assertFalse(external.exists())

        target = self.root / "link-target"
        target.mkdir()
        link = run_root / "review/linked"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlink unavailable: {exc}")
        linked_workspace = link / "final"
        with self.assertRaisesRegex(ContractError, "link|reparse"):
            self.provider._validate_workspace_authority(
                precompile, linked_workspace, runtime_policy
            )
        self.assertFalse((target / "final").exists())

    def test_provider_requires_complete_graph_and_canonical_diagnostic_policy(self) -> None:
        run_dir, precompile = self._completed_run()
        policy = run_dir / "workflow/compile-runtime-policy.json"
        workspace = run_dir / "review/final-authority-check"
        self.assertEqual(
            workspace.resolve(),
            self.provider._validate_workspace_authority(
                precompile, workspace, policy
            ),
        )
        self.assertFalse(workspace.exists())

        state_path = run_dir / "workflow/production-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        main_claim = state["claims"]["pyramid-main"]
        attempt_root = (
            run_dir
            / "workflow/tasks"
            / main_claim["task_id"]
            / "attempts"
            / main_claim["active_attempt_id"]
        )
        attempt_path = attempt_root / "attempt.json"
        original_attempt = attempt_path.read_bytes()
        attempt = json.loads(original_attempt.decode("utf-8"))
        attempt["claim_token"] = "0" * 32
        attempt_path.write_bytes(canonical_bytes(attempt))
        with self.assertRaisesRegex(ContractError, "Attempt binding"):
            self.provider._validate_workspace_authority(
                precompile, workspace, policy
            )
        self.assertFalse(workspace.exists())
        attempt_path.write_bytes(original_attempt)

        output_path = attempt_root / "pyramid-report.json"
        original_output = output_path.read_bytes()
        output_path.write_bytes(original_output + b"\n")
        with self.assertRaisesRegex(ContractError, "output fingerprint"):
            self.provider._validate_workspace_authority(
                precompile, workspace, policy
            )
        self.assertFalse(workspace.exists())
        output_path.write_bytes(original_output)

        integration_main = run_dir / "work/integration/main.tex"
        original_main = integration_main.read_bytes()
        original_state = state_path.read_bytes()
        integration_main.write_bytes(original_main + b"% drift\n")
        state = json.loads(original_state.decode("utf-8"))
        state["artifacts"]["integrated_main"]["sha256"] = hashlib.sha256(
            integration_main.read_bytes()
        ).hexdigest()
        state["artifacts"]["integrated_main"]["size"] = integration_main.stat().st_size
        state_path.write_bytes(canonical_bytes(state))
        with self.assertRaisesRegex(ContractError, "Integration Manifest closure"):
            self.provider._validate_workspace_authority(
                precompile, workspace, policy
            )
        self.assertFalse(workspace.exists())
        integration_main.write_bytes(original_main)
        state_path.write_bytes(original_state)

        copied_policy = run_dir / "workflow/copied-runtime-policy.json"
        copied_policy.write_bytes(policy.read_bytes())
        with self.assertRaisesRegex(ContractError, "current diagnostic Runtime Policy"):
            self.provider._validate_workspace_authority(
                precompile, workspace, copied_policy
            )
        self.assertFalse(workspace.exists())

        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["sections"] = {}
        state_path.write_bytes(canonical_bytes(state))
        with self.assertRaisesRegex(ContractError, "complete current Production task graph"):
            self.provider._validate_workspace_authority(
                precompile, workspace, policy
            )
        self.assertFalse(workspace.exists())

    def _run_public_final_compile_fixture(
        self,
        directive: str | None = None,
        *,
        include_raster: bool = False,
        raster_source_path: str | None = "figure.png",
        via_cli: bool = False,
    ) -> Path:
        fixture = GuardedFinalCompileAdapterTests(methodName="test_public_adapter_compiles_and_derives_complete_evidence")
        fixture.setUp()
        fixture.source.write_text(
            "Core claim\n" + (f"% {directive}\n" if directive else ""),
            encoding="utf-8",
        )
        if include_raster:
            fixture._declare_raster([200.0, 100.0, 300.0, 200.0])
        source_sha = hashlib.sha256(fixture.source.read_bytes()).hexdigest()
        governance = fixture.root / "governance.json"
        governance.write_bytes(b'{"governed":true}\n')
        governance_sha = hashlib.sha256(governance.read_bytes()).hexdigest()
        generations = {"schema_name": "precompile-artifact-generation-set", "schema_version": "1.0.0",
            "generation_set_id": "adapter-public-1", "producer_ids": ["fixture-integration"],
            "artifacts": [
                {"logical_id": "integrated_main", "generation": 1, "sha256": source_sha},
                {"logical_id": "governance_manifest", "generation": 1, "sha256": governance_sha},
            ] + ([{
                "logical_id": "figure_asset",
                "generation": 1,
                "sha256": hashlib.sha256(
                    (fixture.source.parent / "figure.png").read_bytes()
                ).hexdigest(),
            }] if include_raster else [])}
        generations["generation_set_sha256"] = fingerprint(generations, "generation_set_sha256")
        item = {"item_id": "main", "kind": "paragraph", "semantic_region": "main",
            "language_profile_id": "zh-hans", "source_artifact_logical_id": "integrated_main",
            "source_generation": 1, "source_sha256": source_sha, "locator": "latex:main",
            "representation": "structured_text", "text_sha256": hashlib.sha256(b"Core claim").hexdigest(),
            "declared_text": "Core claim",
            "applicable_rule_ids": ["no_meta_writing_content"]}
        item["item_sha256"] = fingerprint(item, "item_sha256")
        items = [item]
        declared_surface = [{"region_id": "main", "kind": "paragraph"}]
        coverage_ledger = [{"region_id": "main", "item_id": "main", "status": "covered"}]
        if include_raster:
            figure_sha = generations["artifacts"][-1]["sha256"]
            figure_item = {
                "item_id": "figure",
                "kind": "raster_text",
                "semantic_region": "figure",
                "language_profile_id": "zh-hans",
                "source_artifact_logical_id": "figure_asset",
                "source_generation": 1,
                "source_sha256": figure_sha,
                "locator": "figure:1",
                "representation": "authoritative_raster_text",
                "text_sha256": hashlib.sha256(b"Raster words").hexdigest(),
                "declared_text": "Raster words",
                "applicable_rule_ids": ["no_meta_writing_content"],
            }
            figure_item["item_sha256"] = fingerprint(figure_item, "item_sha256")
            items.append(figure_item)
            declared_surface.append({"region_id": "figure", "kind": "raster_text"})
            coverage_ledger.append({"region_id": "figure", "item_id": "figure", "status": "covered"})
        inventory = {"schema_name": "reader-facing-text-inventory", "schema_version": "1.0.0",
            "inventory_id": "adapter-public-inventory", "language_profile_id": "zh-hans",
            "delivery_glossary": None, "generation_set_sha256": generations["generation_set_sha256"],
            "declared_surface": declared_surface, "items": items,
            "coverage_ledger": coverage_ledger,
            "extractors": [{"extractor_id": "latex-text-v1", "extractor_sha256": "6" * 64}]}
        inventory["reader_text_set_sha256"] = hashlib.sha256(canonical_bytes([{
            "item_id": current["item_id"], "kind": current["kind"],
            "representation": current["representation"],
            "text_sha256": current["text_sha256"],
        } for current in items])).hexdigest()
        inventory["inventory_sha256"] = fingerprint(inventory, "inventory_sha256")
        seal = {"schema_name": "precompile-text-seal", "schema_version": "1.0.0", "seal_id": "7" * 32,
            "activation_status": "target_only", "sealed_at": "2026-08-11T13:00:00Z",
            "decision_origin": "fresh_evaluation", "generation_set_sha256": generations["generation_set_sha256"],
            "catalog_sha256": "8" * 64, "role_projections_sha256": "9" * 64,
            "language_profile_id": "zh-hans", "delivery_glossary": None,
            "semantic_dependencies_sha256": "a" * 64, "inventory_sha256": inventory["inventory_sha256"],
            "reader_text_set_sha256": inventory["reader_text_set_sha256"],
            "precompile_quality_report_sha256": "b" * 64,
            "provider": {"provider_id": "precompile-quality-provider"}, "predecessor_seal_sha256": None,
            "text_equivalence_report_sha256": None}
        seal["seal_sha256"] = fingerprint(seal, "seal_sha256")
        run_dir, quality = self._completed_run()
        runtime_policy = run_dir / "workflow/compile-runtime-policy.json"
        (quality / "precompile-text-seal.json").write_bytes(canonical_bytes(seal))
        binding = quality / "seal-bindings" / seal["seal_sha256"]
        binding.mkdir(parents=True)
        (binding / "reader-facing-text-inventory.json").write_bytes(canonical_bytes(inventory))
        (binding / "artifact-generations.json").write_bytes(canonical_bytes(generations))
        manifest = json.loads(fixture.manifest.read_text(encoding="utf-8"))
        manifest["precompile_text_seal_sha256"] = seal["seal_sha256"]
        manifest["entries"][0]["sha256"] = source_sha
        manifest["entries"].append({
            "logical_id": "governance_manifest",
            "generation": 1,
            "sha256": governance_sha,
            "source_path": str(governance),
            "staging_path": "governance.unread",
        })
        manifest["runtime_policy"] = {
            "path": str(runtime_policy.resolve()),
            "sha256": hashlib.sha256(runtime_policy.read_bytes()).hexdigest(),
        }
        manifest["manifest_sha256"] = fingerprint(manifest, "manifest_sha256")
        fixture.manifest.write_bytes(canonical_bytes(manifest))
        workspace = run_dir / "review/final-compile"
        allowed_root = Path(sys.executable).resolve().parent
        valid_miktex_paths = os.pathsep.join(
            (str(allowed_root), str(allowed_root / "fixture-runtime-cache"))
        )
        invalid_miktex_paths = os.pathsep.join((str(allowed_root), str(self.root)))
        invocation_environment = dict(os.environ)
        invocation_environment.update({
            "MiKtEx_VALID_PATHS": valid_miktex_paths,
            "MIKTEX_MIXED_AUTHORITY": invalid_miktex_paths,
            "TEXINPUTS": "SHOULD_NOT_CROSS",
            "PYTHONPATH": "SHOULD_NOT_CROSS",
            "ORDINARY_SECRET": "SHOULD_NOT_CROSS",
            "PYTHONUTF8": "1",
        })
        if via_cli:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-B",
                    str(PROJECT_ROOT / "scripts/video_workflow.py"),
                    "delivery-quality-final-compile",
                    "--input-track",
                    "kernel",
                    "--precompile-workspace-root",
                    str(quality),
                    "--compile-manifest",
                    str(fixture.manifest),
                    "--compiler-adapter",
                    str(ADAPTER),
                    "--runtime-policy",
                    str(runtime_policy),
                    "--workspace-root",
                    str(workspace),
                    "--compiled-at",
                    "2026-08-11T13:00:00Z",
                ],
                cwd=PROJECT_ROOT,
                env=invocation_environment,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        else:
            with (
                mock.patch.dict(os.environ, invocation_environment, clear=True),
                mock.patch(
                    "video2pdf_workflow_kernel.final_compile.sha256_git_blob",
                    return_value=hashlib.sha256(ADAPTER.read_bytes()).hexdigest(),
                ),
            ):
                self.provider.compile(
                    input_track="kernel",
                    precompile_workspace_root=quality,
                    compile_manifest_path=fixture.manifest,
                    compiler_adapter_path=ADAPTER,
                    workspace_root=workspace,
                    compiled_at="2026-08-11T13:00:00Z",
                    runtime_policy_path=runtime_policy,
                )
        self.assertTrue((workspace / "final-compile-report.json").is_file())
        provenance = json.loads(
            (workspace / "adapter-output/compile-provenance.json").read_text(encoding="utf-8")
        )
        runtime_environment = {
            key.casefold(): value
            for key, value in provenance["runtime_environment"].items()
        }
        self.assertEqual(valid_miktex_paths, runtime_environment["miktex_valid_paths"])
        self.assertNotIn("miktex_mixed_authority", runtime_environment)
        self.assertNotIn("texinputs", runtime_environment)
        self.assertNotIn("pythonpath", runtime_environment)
        self.assertNotIn("ordinary_secret", runtime_environment)
        return workspace

    def test_public_final_compile_allows_unread_governance_entries(self) -> None:
        self._run_public_final_compile_fixture()

    def test_public_final_compile_accepts_bound_raster_source_path(self) -> None:
        self._run_public_final_compile_fixture(include_raster=True)

    def test_public_final_compile_rejects_missing_or_escaping_raster_source_path(self) -> None:
        for source_path in (None, "../figure.png"):
            with self.subTest(source_path=source_path):
                with self.assertRaisesRegex(
                    ContractError,
                    "declared raster source is invalid",
                ) as raised:
                    self._run_public_final_compile_fixture(
                        include_raster=True,
                        raster_source_path=source_path,
                    )
                self.assertEqual(
                    "text_origin_plan_raster_source",
                    raised.exception.data["first_failing_gate"],
                )
                self.assertEqual("contract_invalid", raised.exception.data["error_code"])

    def test_public_final_compile_rejects_raster_staging_path_mismatch(self) -> None:
        with self.assertRaisesRegex(
            CompileDependencyGap,
            "raster source binding is stale",
        ) as raised:
            self._run_public_final_compile_fixture(
                include_raster=True,
                raster_source_path="figures/drifted.png",
            )
        self.assertEqual(
            "final_compile_raster_source_binding",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual("compile_dependency_gap", raised.exception.data["error_code"])

    def test_public_final_compile_rejects_unobserved_entrypoint(self) -> None:
        with self.assertRaisesRegex(
            CompileDependencyGap,
            "recorder closure is not exact",
        ):
            self._run_public_final_compile_fixture(
                "VIDEO2PDF_FIXTURE_OMIT_ENTRYPOINT_INPUT"
            )

    def test_public_final_compile_rejects_undeclared_recorder_input(self) -> None:
        with self.assertRaisesRegex(
            CompileDependencyGap,
            "recorder contains undeclared input",
        ):
            self._run_public_final_compile_fixture(
                "VIDEO2PDF_FIXTURE_UNDECLARED_RECORDER_INPUT"
            )


if __name__ == "__main__":
    unittest.main()
