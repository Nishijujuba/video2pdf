from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_HEADING = "## Persisted Command Contract"
NEXT_HEADING = "## "


def contract_section(path: Path) -> str:
    document = path.read_text(encoding="utf-8")
    start = document.index(CONTRACT_HEADING)
    end = document.find(NEXT_HEADING, start + len(CONTRACT_HEADING))
    if end == -1:
        end = len(document)
    return document[start:end].strip()


class RepositoryPersistedCommandContractTests(unittest.TestCase):
    def test_codex_and_claude_publish_the_same_operational_contract(self) -> None:
        agents_contract = contract_section(PROJECT_ROOT / "AGENTS.md")
        claude_contract = contract_section(PROJECT_ROOT / "CLAUDE.md")

        self.assertEqual(agents_contract, claude_contract)
        for required_text in (
            "expected runtime exceeds five minutes",
            "still running and requires a later wait",
            "continue beyond the initiating agent session",
            "supports acceptance, review, or diagnosis",
            "scripts\\persisted_command.py start",
            "scripts\\persisted_command.py wait",
            "scripts\\persisted_command.py list",
            "scripts\\persisted_command.py show",
            "scripts\\persisted_command.py reconcile",
            "--accepted-exit-code",
            "launch_failed",
            "interrupted",
            "unknown",
            "acceptance_evidence_eligible",
            "待删除/long-running/",
            "docs/operations/persisted-command-runner.md",
            "does not activate Workflow Kernel 2.0",
            "does not replace Acceptance Reports, Delivery Guard reports, Exit Evidence manifests, or Workflow Kernel Run Records",
        ):
            self.assertIn(required_text, agents_contract)

    def test_adr_and_cross_process_example_publish_the_decisions(self) -> None:
        adr = (
            PROJECT_ROOT
            / "docs/adr/0058-adopt-persisted-command-execution.md"
        ).read_text(encoding="utf-8")
        example = (
            PROJECT_ROOT / "docs/operations/persisted-command-runner.md"
        ).read_text(encoding="utf-8")

        for decision in (
            "detached supervisor",
            "durable evidence",
            "immutable history",
            "manual retention",
            "Rejected Alternatives",
        ):
            self.assertIn(decision, adr)
        for example_step in (
            "scripts\\persisted_command.py start",
            "scripts\\persisted_command.py list",
            "scripts\\persisted_command.py show",
            "scripts\\persisted_command.py wait",
            "scripts\\persisted_command.py reconcile",
            "exit-code.txt",
            "a separate process",
        ):
            self.assertIn(example_step, example)


if __name__ == "__main__":
    unittest.main()
