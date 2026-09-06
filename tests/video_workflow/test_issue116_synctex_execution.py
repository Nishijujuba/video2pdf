from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest import mock
import uuid

import fitz

from tests.video_workflow._test_run import module_test_root


PROJECT = Path(__file__).resolve().parents[2]
ADAPTER = PROJECT / "scripts/guarded_final_compile_adapter.py"


class Issue116SyncTexExecutionTests(unittest.TestCase):
    def test_registered_synctex_timeout_is_a_controlled_adapter_error(self) -> None:
        # scenario_id: issue116-source-map-query-timeout
        # target_invariant: query timeout has a controlled diagnostic identity
        # mutation_seam: registered SyncTeX subprocess completion
        # rematerialized_nodes: none; intentionally_stale_nodes: none
        # expected_first_gate: source-map query completion
        # expected_error_code: compiler_source_map_query_timeout
        # scenario_class: single_contradiction
        spec = importlib.util.spec_from_file_location("issue116_adapter", ADAPTER)
        if spec is None or spec.loader is None:
            self.fail("guarded adapter cannot be loaded")
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        root = module_test_root(PROJECT) / f"issue116-{uuid.uuid4().hex}"
        root.mkdir(parents=True)
        source = root / "main.tex"
        source.write_text("source-backed text\n", encoding="utf-8")
        tool = root / "synctex.exe"
        tool.write_bytes(b"controlled SyncTeX process fixture")
        pdf = root / "main.pdf"
        with fitz.open() as document:
            for _ in range(15):
                document.new_page()
            page = document[-1]
            page.insert_text((60, 300), "source-backed text")
            span = page.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
            document.save(pdf)
        obj = {
            "object_id": "page-15-text-1", "object_kind": "pdf_text_run",
            "page": 15, "bbox": list(span["bbox"]),
            "exact_utf8_text": span["text"],
        }
        arguments = {
            "policy": {
                "policy_id": "miktex-xelatex-runtime",
                "engine": {"executable": str(root / "xelatex.exe")},
                "allowed_runtime_roots": [str(root)],
            },
            "pdf": pdf, "staging": root, "objects": [obj],
            "entry": source, "manifest_entries": [{"staging_path": "main.tex"}],
            "observed_declared_paths": {source.resolve()},
            "runtime_environment": {"MIKTEX_USERLOGDIRECTORY": str(root / "profile")},
        }
        positive = subprocess.CompletedProcess(
            [str(tool)], 0,
            f"Input:{source.resolve()}\nLine:1\nColumn:1\n", "",
        )
        with mock.patch.object(adapter.subprocess, "run", return_value=positive):
            locations, _ = adapter.compiler_source_locations(**arguments)
        self.assertEqual(str(source.resolve()), locations[obj["object_id"]]["source_path"])

        def timeout(command: list[str], **kwargs: object) -> None:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with mock.patch.object(adapter.subprocess, "run", side_effect=timeout) as query:
            with self.assertRaises(adapter.AdapterError) as raised:
                adapter.compiler_source_locations(**arguments)
        query.assert_called_once()
        self.assertIn("compiler_source_map_query_timeout", str(raised.exception))
        self.assertIn("page=15", str(raised.exception))
        self.assertIn("timeout_seconds=90", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, subprocess.TimeoutExpired)


if __name__ == "__main__":
    unittest.main()
