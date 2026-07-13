#!/usr/bin/env python3
"""Bilibili render workflow contract tests for Delivery Glossary handling."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
BILIBILI_SKILL = REPO_ROOT / ".agents" / "skills" / "bilibili-render-pdf" / "SKILL.md"


def read_bilibili_skill() -> str:
    return BILIBILI_SKILL.read_text(encoding="utf-8")


class BilibiliDeliveryGlossaryWorkflowTests(unittest.TestCase):
    def test_bilibili_workflow_carries_delivery_glossary_to_acceptance(self) -> None:
        text = read_bilibili_skill()

        source_acquisition = text.index("## Source Acquisition")
        glossary_workflow = text.index("## Terminology Glossary Workflow")
        final_acceptance = text.index("## Final Delivery Acceptance Gate")
        self.assertLess(source_acquisition, glossary_workflow)
        self.assertLess(glossary_workflow, final_acceptance)

        required_phrases = [
            "non-English teaching PDFs",
            "English-learning, IELTS, TOEFL, pronunciation, grammar, vocabulary",
            "existing English-primary behavior",
            "Outline agent must create the initial global Delivery Glossary",
            "review/acceptance/delivery_glossary.json",
            "schema_version",
            "delivery_glossary.v1",
            "language_profile",
            "non_english_teaching_pdf",
            "default_reader_mode",
            "standalone_readable_video_learning_note",
            "body_display_strategy",
            "where_to_preserve_english",
            "Writer agents must include `new_term_candidates` in every handoff note",
            "`new_term_candidates: none`",
            "coordinator must accept or reject each candidate",
            "merge accepted candidates into `review/acceptance/delivery_glossary.json`",
            "before consistency review and final acceptance",
            "validate_delivery_glossary.py",
            "--include-delivery-glossary",
            "not a PDF appendix unless the user or task explicitly requests one",
        ]
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
