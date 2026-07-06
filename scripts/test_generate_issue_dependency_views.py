import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4


def load_script():
    script_path = Path(__file__).with_name("generate_issue_dependency_views.py")
    spec = importlib.util.spec_from_file_location("generate_issue_dependency_views", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class IssueDependencyViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = load_script()
        self.root = Path.cwd() / "待删除" / "issue-dependency-view-tests" / uuid4().hex
        self.docs_issues = self.root / "docs" / "issues"
        self.feature_dir = self.docs_issues / "sample-feature"
        self.feature_dir.mkdir(parents=True)

    def write_issue(
        self,
        filename: str,
        *,
        feature_slug: str = "sample-feature",
        status: str = "ready-for-agent",
        depends_on: list[str] | None = None,
        blocks: list[str] | None = None,
        related_adrs: list[str] | None = None,
        title: str = "Sample issue",
        body: str = "## Goal\nShip the behavior.\n",
    ) -> Path:
        depends_on = depends_on or []
        blocks = blocks or []
        related_adrs = related_adrs or []

        def list_block(items: list[str]) -> str:
            if not items:
                return "[]"
            return "\n" + "\n".join(f"  - \"{item}\"" for item in items)

        feature_dir = self.docs_issues / feature_slug
        feature_dir.mkdir(parents=True, exist_ok=True)
        path = feature_dir / filename
        path.write_text(
            "\n".join(
                [
                    "---",
                    "type: issue",
                    f"status: {status}",
                    f'feature: "[[prd/{feature_slug}]]"',
                    f"depends_on: {list_block(depends_on)}",
                    f"blocks: {list_block(blocks)}",
                    f"related_adrs: {list_block(related_adrs)}",
                    "owner: unassigned",
                    "created: 2026-07-05",
                    "updated: 2026-07-05",
                    "tags:",
                    "  - issue",
                    f"  - status/{status}",
                    "---",
                    "",
                    f"# {filename[:2]} - {title}",
                    "",
                    f"Status: {status}",
                    "",
                    body,
                ]
            ),
            encoding="utf-8",
        )
        return path

    def test_issue_metadata_parses_frontmatter_title_and_obsidian_link_target(self) -> None:
        self.write_issue(
            "01-first.md",
            blocks=["[[issues/sample-feature/02-second]]"],
            related_adrs=["[[adr/0001-example]]"],
            title="First dependency slice",
        )

        feature_set = self.script.load_feature_issue_set(self.root, "sample-feature")

        self.assertEqual("sample-feature", feature_set.slug)
        self.assertEqual(1, len(feature_set.issues))
        issue = feature_set.issues[0]
        self.assertEqual("01 - First dependency slice", issue.title)
        self.assertEqual("ready-for-agent", issue.status)
        self.assertEqual("[[prd/sample-feature]]", issue.feature)
        self.assertEqual("issues/sample-feature/01-first", issue.link_target)
        self.assertEqual(["[[issues/sample-feature/02-second]]"], issue.blocks)
        self.assertEqual(["[[adr/0001-example]]"], issue.related_adrs)

    def test_source_fingerprint_changes_for_metadata_and_ignores_body_prose(self) -> None:
        issue_path = self.write_issue("01-first.md", body="## Goal\nOriginal prose.\n")
        first = self.script.load_feature_issue_set(self.root, "sample-feature").source_fingerprint

        issue_path.write_text(
            issue_path.read_text(encoding="utf-8").replace("Original prose.", "Changed prose."),
            encoding="utf-8",
        )
        body_only = self.script.load_feature_issue_set(self.root, "sample-feature").source_fingerprint

        issue_path.write_text(
            issue_path.read_text(encoding="utf-8").replace("status: ready-for-agent", "status: done", 1),
            encoding="utf-8",
        )
        metadata_changed = self.script.load_feature_issue_set(self.root, "sample-feature").source_fingerprint

        self.assertEqual(first, body_only)
        self.assertNotEqual(first, metadata_changed)

    def test_missing_required_metadata_is_reported_as_consistency_error(self) -> None:
        path = self.write_issue("01-first.md")
        path.write_text(
            path.read_text(encoding="utf-8").replace("feature: \"[[prd/sample-feature]]\"\n", ""),
            encoding="utf-8",
        )

        feature_set = self.script.load_feature_issue_set(self.root, "sample-feature")

        self.assertEqual(1, len(feature_set.errors))
        self.assertIn("missing required frontmatter field: feature", feature_set.errors[0].message)

    def test_validation_passes_for_consistent_issue_set(self) -> None:
        self.write_issue("01-first.md", blocks=["[[issues/sample-feature/02-second]]"])
        self.write_issue("02-second.md", depends_on=["[[issues/sample-feature/01-first]]"])

        feature_set = self.script.load_feature_issue_set(self.root, "sample-feature")

        self.assertEqual([], self.script.validate_feature_issue_set(feature_set))

    def test_validation_reports_missing_links_inverse_mismatch_cycle_and_unknown_status(self) -> None:
        self.write_issue(
            "01-first.md",
            status="mystery",
            depends_on=["[[issues/sample-feature/02-second]]", "[[issues/sample-feature/99-missing]]"],
        )
        self.write_issue(
            "02-second.md",
            depends_on=["[[issues/sample-feature/01-first]]"],
            blocks=["[[issues/sample-feature/03-extra]]"],
        )

        feature_set = self.script.load_feature_issue_set(self.root, "sample-feature")
        messages = [error.message for error in self.script.validate_feature_issue_set(feature_set)]

        self.assertTrue(any("unknown status: mystery" in message for message in messages))
        self.assertTrue(any("missing issue link: [[issues/sample-feature/99-missing]]" in message for message in messages))
        self.assertTrue(any("blocks inverse mismatch" in message for message in messages))
        self.assertTrue(any("dependency cycle" in message for message in messages))

    def test_execution_state_helpers_separate_executable_dependency_blocked_and_status_blocked(self) -> None:
        self.write_issue(
            "01-first.md",
            status="done",
            blocks=["[[issues/sample-feature/02-second]]"],
        )
        self.write_issue(
            "02-second.md",
            status="ready-for-agent",
            depends_on=["[[issues/sample-feature/01-first]]"],
            blocks=["[[issues/sample-feature/03-third]]"],
        )
        self.write_issue(
            "03-third.md",
            status="ready-for-human",
            depends_on=["[[issues/sample-feature/02-second]]"],
        )
        self.write_issue("04-blocked.md", status="blocked")

        feature_set = self.script.load_feature_issue_set(self.root, "sample-feature")

        executable = self.script.currently_executable_issues(feature_set)
        dependency_blocked = self.script.dependency_blocked_issues(feature_set)
        status_blocked = self.script.status_blocked_issues(feature_set)

        self.assertEqual(["issues/sample-feature/02-second"], [issue.link_target for issue in executable])
        self.assertEqual(["issues/sample-feature/03-third"], [item.issue.link_target for item in dependency_blocked])
        self.assertEqual(
            ["[[issues/sample-feature/02-second]]"],
            dependency_blocked[0].waiting_on,
        )
        self.assertEqual(["issues/sample-feature/04-blocked"], [issue.link_target for issue in status_blocked])

    def test_single_feature_markdown_view_contains_metadata_lists_mermaid_and_status_colors(self) -> None:
        self.write_issue(
            "01-first.md",
            status="done",
            blocks=["[[issues/sample-feature/02-second]]"],
            title="First",
        )
        self.write_issue(
            "02-second.md",
            status="ready-for-agent",
            depends_on=["[[issues/sample-feature/01-first]]"],
            blocks=["[[issues/sample-feature/03-third]]"],
            title="Second",
        )
        self.write_issue(
            "03-third.md",
            status="ready-for-human",
            depends_on=["[[issues/sample-feature/02-second]]"],
            title="Third",
        )
        feature_set = self.script.load_feature_issue_set(self.root, "sample-feature")

        markdown = self.script.render_feature_dependency_view(
            feature_set,
            generated_at="2026-07-05T00:00:00Z",
        )

        self.assertIn("generated_at: 2026-07-05T00:00:00Z", markdown)
        self.assertIn("source_feature_slug: sample-feature", markdown)
        self.assertIn("source_issue_count: 3", markdown)
        self.assertIn(f"source_issue_fingerprint: {feature_set.source_fingerprint}", markdown)
        self.assertIn("## Consistency errors\n\nNone", markdown)
        self.assertIn("## Next executable\n\n- [[issues/sample-feature/02-second]] 02 - Second", markdown)
        self.assertIn(
            "- [[issues/sample-feature/03-third]] waits on [[issues/sample-feature/02-second]]",
            markdown,
        )
        self.assertIn("flowchart LR", markdown)
        self.assertIn('["01 - First"]', markdown)
        self.assertIn("n_02_second --> n_03_third", markdown)
        self.assertIn("classDef done fill:#2ea043", markdown)
        self.assertIn("classDef ready_for_agent fill:#0969da", markdown)
        self.assertIn("classDef ready_for_human fill:#8250df", markdown)

    def test_dependency_index_discovers_feature_sets_and_summarizes_execution_state(self) -> None:
        self.write_issue(
            "01-first.md",
            status="done",
            blocks=["[[issues/sample-feature/02-second]]"],
            title="First",
        )
        self.write_issue(
            "02-second.md",
            status="ready-for-agent",
            depends_on=["[[issues/sample-feature/01-first]]"],
            blocks=["[[issues/sample-feature/03-third]]"],
            title="Second",
        )
        self.write_issue(
            "03-third.md",
            status="ready-for-agent",
            depends_on=["[[issues/sample-feature/02-second]]"],
            title="Third",
        )
        self.write_issue("04-blocked.md", status="blocked", title="Blocked")
        self.write_issue(
            "01-ready.md",
            feature_slug="second-feature",
            status="ready-for-human",
            title="Ready",
        )
        views_dir = self.docs_issues / "_views"
        views_dir.mkdir()
        (views_dir / "not-a-feature.md").write_text("# ignored\n", encoding="utf-8")

        feature_sets = self.script.discover_feature_issue_sets(self.root)
        index = self.script.render_dependency_index(
            feature_sets,
            generated_at="2026-07-05T00:00:00Z",
        )

        self.assertEqual(["sample-feature", "second-feature"], [feature_set.slug for feature_set in feature_sets])
        self.assertIn("generated_at: 2026-07-05T00:00:00Z", index)
        self.assertIn("source_feature_count: 2", index)
        self.assertIn("source_issue_count: 5", index)
        self.assertIn("## sample-feature", index)
        self.assertIn("- View: [[issues/_views/sample-feature-dependencies]]", index)
        self.assertIn("- Issue count: 4", index)
        self.assertIn("- Status distribution: blocked=1, done=1, ready-for-agent=2", index)
        self.assertIn("- Root issues: [[issues/sample-feature/01-first]], [[issues/sample-feature/04-blocked]]", index)
        self.assertIn("- Currently executable: [[issues/sample-feature/02-second]]", index)
        self.assertIn("- Status-blocked: [[issues/sample-feature/04-blocked]]", index)
        self.assertIn(
            "- Dependency-blocked: [[issues/sample-feature/03-third]] waits on [[issues/sample-feature/02-second]]",
            index,
        )
        self.assertIn("## second-feature", index)
        self.assertIn("- View: [[issues/_views/second-feature-dependencies]]", index)
        self.assertIn("- Currently executable: [[issues/second-feature/01-ready]]", index)
        self.assertNotIn("not-a-feature", index)

    def test_cli_generates_all_views_single_feature_views_and_validates_freshness_without_writes(self) -> None:
        self.write_issue(
            "01-first.md",
            status="done",
            blocks=["[[issues/sample-feature/02-second]]"],
            title="First",
        )
        second = self.write_issue(
            "02-second.md",
            status="ready-for-agent",
            depends_on=["[[issues/sample-feature/01-first]]"],
            title="Second",
        )
        self.write_issue(
            "01-ready.md",
            feature_slug="second-feature",
            status="ready-for-human",
            title="Ready",
        )
        script_path = Path(__file__).with_name("generate_issue_dependency_views.py")

        feature_only = subprocess.run(
            [sys.executable, str(script_path), "--root", str(self.root), "--feature", "sample-feature"],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, feature_only.returncode, feature_only.stderr + feature_only.stdout)
        views_dir = self.docs_issues / "_views"
        self.assertTrue((views_dir / "sample-feature-dependencies.md").exists())
        self.assertFalse((views_dir / "second-feature-dependencies.md").exists())
        self.assertIn("## second-feature", (views_dir / "index.md").read_text(encoding="utf-8"))

        all_features = subprocess.run(
            [sys.executable, str(script_path), "--root", str(self.root)],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, all_features.returncode, all_features.stderr + all_features.stdout)
        self.assertTrue((views_dir / "second-feature-dependencies.md").exists())

        before_check = {path: path.read_text(encoding="utf-8") for path in sorted(views_dir.glob("*.md"))}
        fresh_check = subprocess.run(
            [sys.executable, str(script_path), "--root", str(self.root), "--check"],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
        after_check = {path: path.read_text(encoding="utf-8") for path in sorted(views_dir.glob("*.md"))}
        self.assertEqual(0, fresh_check.returncode, fresh_check.stderr + fresh_check.stdout)
        self.assertEqual(before_check, after_check)

        second.write_text(
            second.read_text(encoding="utf-8").replace("status: ready-for-agent", "status: done", 1),
            encoding="utf-8",
        )
        stale_check = subprocess.run(
            [sys.executable, str(script_path), "--root", str(self.root), "--check"],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, stale_check.returncode)
        self.assertIn("stale generated view", stale_check.stdout + stale_check.stderr)

    def test_cli_validation_reports_consistency_errors_and_help_documents_modes(self) -> None:
        self.write_issue(
            "01-first.md",
            depends_on=["[[issues/sample-feature/02-second]]"],
            title="First",
        )
        self.write_issue(
            "02-second.md",
            depends_on=["[[issues/sample-feature/01-first]]"],
            title="Second",
        )
        script_path = Path(__file__).with_name("generate_issue_dependency_views.py")

        generated = subprocess.run(
            [sys.executable, str(script_path), "--root", str(self.root)],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, generated.returncode, generated.stderr + generated.stdout)

        invalid = subprocess.run(
            [sys.executable, str(script_path), "--root", str(self.root), "--check"],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, invalid.returncode)
        self.assertIn("blocks inverse mismatch", invalid.stdout + invalid.stderr)
        self.assertIn("dependency cycle", invalid.stdout + invalid.stderr)

        help_result = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, help_result.returncode)
        self.assertIn("Generate or validate Markdown dependency views", help_result.stdout)
        self.assertIn("--feature", help_result.stdout)
        self.assertIn("--check", help_result.stdout)
        self.assertIn("--validate", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
