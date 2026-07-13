#!/usr/bin/env python3
"""Tests for YouTube Delivery Glossary workflow instructions."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
YOUTUBE_SKILL = REPO_ROOT / ".agents" / "skills" / "youtube-render-pdf" / "SKILL.md"


def read_youtube_skill() -> str:
    return YOUTUBE_SKILL.read_text(encoding="utf-8")


class YouTubeDeliveryGlossaryWorkflowTests(unittest.TestCase):
    def assert_skill_contains(self, *phrases: str) -> None:
        text = read_youtube_skill()
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_outline_agent_creates_global_delivery_glossary_for_non_english_teaching_pdfs(self) -> None:
        self.assert_skill_contains(
            "Outline agent",
            "global Delivery Glossary",
            "non-English teaching PDFs",
            "review/acceptance/delivery_glossary.json",
            "validate_delivery_glossary.py",
        )

    def test_writer_handoffs_report_new_term_candidates_or_none(self) -> None:
        self.assert_skill_contains(
            "Writer agents",
            "Each Writer agent handoff note must include `new_term_candidates`",
            "new_term_candidates: none",
            "source English expression",
            "proposed Chinese primary name",
        )

    def test_coordinator_merges_accepted_candidates_into_delivery_glossary(self) -> None:
        self.assert_skill_contains(
            "workflow coordinator",
            "accept or reject `new_term_candidates`",
            "merges accepted candidates into `review/acceptance/delivery_glossary.json`",
            "reruns `validate_delivery_glossary.py`",
        )

    def test_final_manifest_includes_delivery_glossary_when_applicable(self) -> None:
        self.assert_skill_contains(
            "final manifest must include the glossary when applicable",
            "validate_acceptance_report.py manifest",
            "--include-delivery-glossary",
        )

    def test_delivery_glossary_is_not_a_default_pdf_appendix(self) -> None:
        self.assert_skill_contains(
            "glossary is not a PDF appendix unless explicitly requested",
            "workflow evidence",
            "reader-facing glossary, concept index, or appendix",
        )

    def test_english_learning_and_ielts_keep_english_primary_behavior(self) -> None:
        self.assert_skill_contains(
            "English-learning and IELTS YouTube content keeps existing English-primary behavior",
            "TOEFL",
            "pronunciation",
            "source English as primary evidence",
            "do not use this Chinese-primary glossary behavior unless the user explicitly asks",
        )


if __name__ == "__main__":
    unittest.main()
