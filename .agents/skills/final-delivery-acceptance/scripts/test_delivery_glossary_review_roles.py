#!/usr/bin/env python3
"""Contract tests for Delivery Glossary enforcement in review roles."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


class DeliveryGlossaryReviewRoleTests(unittest.TestCase):
    def test_consistency_agent_instructions_require_evidence_bearing_glossary_checks(self) -> None:
        required_phrases = [
            "Consistency agent",
            "Delivery Glossary",
            "first-use wording",
            "later-use stability",
            "source-English preservation location",
            "body display strategy stability",
            "chapter-to-chapter terminology consistency",
        ]
        for relative in (
            "AGENTS.md",
            "CLAUDE.md",
            ".agents/skills/youtube-render-pdf/SKILL.md",
            ".agents/skills/bilibili-render-pdf/SKILL.md",
        ):
            text = read(relative)
            with self.subTest(relative=relative):
                for phrase in required_phrases:
                    self.assertIn(phrase, text)

    def test_acceptance_reviewer_reads_delivery_glossary_only_through_manifest(self) -> None:
        text = read(".agents/skills/final-delivery-acceptance/SKILL.md")

        required_phrases = [
            "review/acceptance/delivery_glossary.json",
            "only when it is listed in `review/acceptance/allowed_artifacts_manifest.json`",
            "list `review/acceptance/delivery_glossary.json` in `review_context_used.artifacts_read` only when the manifest includes it",
            "manifest final artifacts plus the criteria file",
        ]
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_acceptance_reviewer_checks_each_glossary_term_strategy_in_final_body_text(self) -> None:
        text = read(".agents/skills/final-delivery-acceptance/SKILL.md")

        required_phrases = [
            "for every Delivery Glossary term found in final body text",
            "`body_display_strategy`",
            "`where_to_preserve_english`",
            "where the original English expression may appear",
            "artifact-grounded evidence",
        ]
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_acceptance_reviewer_instructions_cover_grief_glossary_only_case(self) -> None:
        text = read(".agents/skills/final-delivery-acceptance/SKILL.md")

        required_phrases = [
            "`grief`",
            "`chinese_primary_only`",
            "`delivery_glossary_only`",
            "final body text should not make `grief` the sentence subject",
        ]
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_acceptance_reviewer_boundary_stays_read_only_and_final_artifact_only(self) -> None:
        text = read(".agents/skills/final-delivery-acceptance/SKILL.md")

        required_phrases = [
            "read-only",
            "final delivered artifacts",
            "generation notes",
            "writer drafts",
            "review/consistency/",
            "review/pyramid/",
            "work/",
            "intermediate drafts",
        ]
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
