from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import threading
import unittest
from unittest import mock
import uuid

import fitz

from tests.video_workflow._test_run import module_test_root


PROJECT = Path(__file__).resolve().parents[2]
ADAPTER = PROJECT / "scripts/guarded_final_compile_adapter.py"


class Issue116SyncTexExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location("issue116_adapter", ADAPTER)
        if spec is None or spec.loader is None:
            self.fail("guarded adapter cannot be loaded")
        self.adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.adapter)
        root = module_test_root(PROJECT) / f"issue116-{uuid.uuid4().hex}"
        root.mkdir(parents=True)
        (root / "profile").mkdir()
        (root / "runtime-data").mkdir()
        source = root / "main.tex"
        source.write_text("source-backed text\n", encoding="utf-8")
        tool = root / "synctex.exe"
        tool.write_bytes(b"controlled SyncTeX process fixture")
        pdf = root / "main.pdf"
        objects = []
        with fitz.open() as document:
            for page_number in range(1, 17):
                page = document.new_page()
                page.insert_text((60, 300), "source-backed text")
                span = page.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
                objects.append({
                    "object_id": f"page-{page_number}-text-1",
                    "object_kind": "pdf_text_run", "page": page_number,
                    "bbox": list(span["bbox"]), "exact_utf8_text": span["text"],
                })
            document.save(pdf)
        self.arguments = {
            "policy": {
                "policy_id": "miktex-xelatex-runtime",
                "engine": {"executable": str(root / "xelatex.exe")},
                "allowed_runtime_roots": [str(root)],
            },
            "pdf": pdf, "staging": root, "objects": objects,
            "entry": source, "manifest_entries": [{"staging_path": "main.tex"}],
            "observed_declared_paths": {source.resolve()},
            "runtime_environment": {
                "MIKTEX_USERLOGDIRECTORY": str(root / "profile"),
                "MIKTEX_USERDATA": str(root / "runtime-data"),
                "MIKTEX_ENABLE_INSTALLER": "0",
            },
        }
        self.positive = subprocess.CompletedProcess(
            [str(tool)], 0,
            f"Input:{source.resolve()}\nLine:1\nColumn:1\n", "",
        )

    def test_registered_synctex_timeout_is_a_controlled_adapter_error(self) -> None:
        # scenario_id: issue116-source-map-query-timeout
        # target_invariant: query timeout has a controlled diagnostic identity
        # mutation_seam: registered SyncTeX subprocess completion
        # rematerialized_nodes: none; intentionally_stale_nodes: none
        # expected_first_gate: source-map query completion
        # expected_error_code: unavailable (existing AdapterError text interface)
        # expected_message_fragment: compiler_source_map_query_timeout
        # scenario_class: single_contradiction
        arguments = {**self.arguments, "objects": [self.arguments["objects"][14]]}
        with mock.patch.object(self.adapter.subprocess, "run", return_value=self.positive):
            locations, _ = self.adapter.compiler_source_locations(**arguments)
        self.assertEqual(
            str(arguments["entry"].resolve()),
            locations["page-15-text-1"]["source_path"],
        )

        def timeout(command: list[str], **kwargs: object) -> None:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with mock.patch.object(self.adapter.subprocess, "run", side_effect=timeout) as query:
            with self.assertRaises(self.adapter.AdapterError) as raised:
                self.adapter.compiler_source_locations(**arguments)
        query.assert_called_once()
        self.assertIn("compiler_source_map_query_timeout", str(raised.exception))
        self.assertIn("page=15", str(raised.exception))
        self.assertIn("timeout_seconds=90", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, subprocess.TimeoutExpired)

    def test_concurrent_synctex_workers_own_distinct_persistent_log_directories(self) -> None:
        # scenario_id: issue116-worker-owned-query-logs
        # target_invariant: concurrent workers retain separate mutable log state
        # observation_seam: registered SyncTeX subprocess environment
        # scenario_class: positive_execution_contract
        barrier = threading.Barrier(8, timeout=10)
        observations = []
        observation_lock = threading.Lock()
        base_environment = dict(self.arguments["runtime_environment"])

        def query(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            with observation_lock:
                observations.append((threading.get_ident(), dict(kwargs["env"])))
            barrier.wait()
            return self.positive

        with mock.patch.object(self.adapter.subprocess, "run", side_effect=query):
            locations, _ = self.adapter.compiler_source_locations(**self.arguments)
        self.assertEqual(16, len(locations))
        self.assertEqual(16, len(observations))
        worker_logs = {}
        for worker_id, environment in observations:
            log_directory = Path(environment.pop("MIKTEX_USERLOGDIRECTORY"))
            self.assertTrue(log_directory.is_relative_to(Path(base_environment["MIKTEX_USERLOGDIRECTORY"])))
            self.assertTrue(log_directory.is_dir())
            self.assertEqual(
                {key: value for key, value in base_environment.items() if key != "MIKTEX_USERLOGDIRECTORY"},
                environment,
            )
            worker_logs.setdefault(worker_id, set()).add(log_directory)
        self.assertEqual(8, len(worker_logs))
        self.assertTrue(all(len(paths) == 1 for paths in worker_logs.values()))
        self.assertEqual(8, len(set().union(*worker_logs.values())))
        self.assertEqual(base_environment, self.arguments["runtime_environment"])


if __name__ == "__main__":
    unittest.main()
