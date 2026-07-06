#!/usr/bin/env python3
"""Generate dependency-focused Markdown views for local issue batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = ("status", "feature", "depends_on", "blocks", "related_adrs")

ALLOWED_STATUSES = {
    "needs-triage",
    "needs-info",
    "ready-for-agent",
    "ready-for-human",
    "in-progress",
    "blocked",
    "in-review",
    "done",
    "wontfix",
}

EXECUTABLE_STATUSES = {"ready-for-agent", "ready-for-human"}

STATUS_PALETTE = {
    "done": "#2ea043",
    "ready-for-agent": "#0969da",
    "ready-for-human": "#8250df",
    "in-progress": "#d4a72c",
    "blocked": "#cf222e",
    "in-review": "#bc4c00",
    "needs-info": "#8c959f",
    "needs-triage": "#d0d7de",
    "wontfix": "#57606a",
}


@dataclass(frozen=True)
class ConsistencyError:
    path: str
    message: str


@dataclass(frozen=True)
class IssueMetadata:
    path: Path
    docs_relative_path: str
    link_target: str
    title: str
    status: str
    feature: str
    depends_on: list[str]
    blocks: list[str]
    related_adrs: list[str]

    def fingerprint_record(self) -> dict[str, Any]:
        return {
            "path": self.docs_relative_path,
            "title": self.title,
            "status": self.status,
            "feature": self.feature,
            "depends_on": self.depends_on,
            "blocks": self.blocks,
            "related_adrs": self.related_adrs,
        }


@dataclass(frozen=True)
class FeatureIssueSet:
    slug: str
    path: Path
    issues: list[IssueMetadata]
    errors: list[ConsistencyError] = field(default_factory=list)

    @property
    def source_fingerprint(self) -> str:
        records = [issue.fingerprint_record() for issue in sorted(self.issues, key=lambda item: item.docs_relative_path)]
        payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DependencyBlockedIssue:
    issue: IssueMetadata
    waiting_on: list[str]


def strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end_marker = text.find("\n---", 4)
    if end_marker == -1:
        return {}, text

    frontmatter_text = text[4:end_marker]
    body = text[end_marker + len("\n---") :].lstrip("\r\n")
    data: dict[str, Any] = {}
    current_key: str | None = None

    for raw_line in frontmatter_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            current = data.setdefault(current_key, [])
            if isinstance(current, list):
                current.append(strip_wrapping_quotes(line[4:].strip()))
            continue
        if ":" not in line:
            current_key = None
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        current_key = key
        if value == "[]":
            data[key] = []
        elif value == "":
            data[key] = []
        else:
            data[key] = strip_wrapping_quotes(value)
            current_key = None

    return data, body


def extract_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def docs_relative_path(root: Path, path: Path) -> str:
    docs_root = root / "docs"
    try:
        return path.relative_to(docs_root).as_posix()
    except ValueError:
        return path.relative_to(root).as_posix()


def link_target_from_docs_relative(relative_path: str) -> str:
    if relative_path.endswith(".md"):
        return relative_path[:-3]
    return relative_path


def internal_link_target(link: str) -> str:
    value = strip_wrapping_quotes(link).strip()
    if value.startswith("[[") and value.endswith("]]"):
        value = value[2:-2]
    if "|" in value:
        value = value.split("|", 1)[0]
    return value.strip()


def issue_link(issue: IssueMetadata) -> str:
    return f"[[{issue.link_target}]]"


def parse_issue_file(root: Path, path: Path) -> tuple[IssueMetadata, list[ConsistencyError]]:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    relative_path = docs_relative_path(root, path)
    link_target = link_target_from_docs_relative(relative_path)
    title = extract_title(body, path.stem)
    errors: list[ConsistencyError] = []

    for field_name in REQUIRED_FIELDS:
        if field_name not in frontmatter:
            errors.append(ConsistencyError(relative_path, f"missing required frontmatter field: {field_name}"))

    issue = IssueMetadata(
        path=path,
        docs_relative_path=relative_path,
        link_target=link_target,
        title=title,
        status=str(frontmatter.get("status", "")),
        feature=str(frontmatter.get("feature", "")),
        depends_on=as_string_list(frontmatter.get("depends_on")),
        blocks=as_string_list(frontmatter.get("blocks")),
        related_adrs=as_string_list(frontmatter.get("related_adrs")),
    )
    return issue, errors


def load_feature_issue_set(root: str | Path, feature_slug: str) -> FeatureIssueSet:
    root_path = Path(root)
    feature_path = root_path / "docs" / "issues" / feature_slug
    issues: list[IssueMetadata] = []
    errors: list[ConsistencyError] = []

    for issue_path in sorted(feature_path.glob("*.md")):
        issue, issue_errors = parse_issue_file(root_path, issue_path)
        issues.append(issue)
        errors.extend(issue_errors)

    return FeatureIssueSet(slug=feature_slug, path=feature_path, issues=issues, errors=errors)


def issue_by_link_target(feature_set: FeatureIssueSet) -> dict[str, IssueMetadata]:
    return {issue.link_target: issue for issue in feature_set.issues}


def validate_feature_issue_set(feature_set: FeatureIssueSet) -> list[ConsistencyError]:
    errors = list(feature_set.errors)
    by_target = issue_by_link_target(feature_set)

    for issue in feature_set.issues:
        if issue.status not in ALLOWED_STATUSES:
            errors.append(ConsistencyError(issue.docs_relative_path, f"unknown status: {issue.status}"))

        for dependency_link in issue.depends_on:
            dependency_target = internal_link_target(dependency_link)
            dependency = by_target.get(dependency_target)
            if dependency is None:
                errors.append(ConsistencyError(issue.docs_relative_path, f"missing issue link: {dependency_link}"))
                continue
            dependency_blocks = {internal_link_target(link) for link in dependency.blocks}
            if issue.link_target not in dependency_blocks:
                errors.append(
                    ConsistencyError(
                        issue.docs_relative_path,
                        f"blocks inverse mismatch: {issue_link(dependency)} should block {issue_link(issue)}",
                    )
                )

        for blocked_link in issue.blocks:
            blocked_target = internal_link_target(blocked_link)
            blocked_issue = by_target.get(blocked_target)
            if blocked_issue is None:
                errors.append(ConsistencyError(issue.docs_relative_path, f"missing issue link: {blocked_link}"))
                continue
            blocked_depends = {internal_link_target(link) for link in blocked_issue.depends_on}
            if issue.link_target not in blocked_depends:
                errors.append(
                    ConsistencyError(
                        issue.docs_relative_path,
                        f"blocks inverse mismatch: {issue_link(issue)} blocks {issue_link(blocked_issue)} but reverse depends_on is missing",
                    )
                )

    cycle = find_dependency_cycle(feature_set)
    if cycle:
        errors.append(ConsistencyError(cycle[0], f"dependency cycle: {' -> '.join(cycle)}"))

    return errors


def find_dependency_cycle(feature_set: FeatureIssueSet) -> list[str]:
    by_target = issue_by_link_target(feature_set)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(target: str, stack: list[str]) -> list[str]:
        if target in visiting:
            cycle_start = stack.index(target)
            return stack[cycle_start:] + [target]
        if target in visited:
            return []
        visiting.add(target)
        issue = by_target[target]
        for dependency_link in issue.depends_on:
            dependency_target = internal_link_target(dependency_link)
            if dependency_target not in by_target:
                continue
            cycle = visit(dependency_target, stack + [dependency_target])
            if cycle:
                return cycle
        visiting.remove(target)
        visited.add(target)
        return []

    for issue in feature_set.issues:
        cycle = visit(issue.link_target, [issue.link_target])
        if cycle:
            return cycle
    return []


def dependency_waiting_links(issue: IssueMetadata, by_target: dict[str, IssueMetadata]) -> list[str]:
    waiting: list[str] = []
    for dependency_link in issue.depends_on:
        dependency = by_target.get(internal_link_target(dependency_link))
        if dependency is None or dependency.status != "done":
            waiting.append(dependency_link)
    return waiting


def currently_executable_issues(feature_set: FeatureIssueSet) -> list[IssueMetadata]:
    by_target = issue_by_link_target(feature_set)
    executable = [
        issue
        for issue in feature_set.issues
        if issue.status in EXECUTABLE_STATUSES and not dependency_waiting_links(issue, by_target)
    ]
    return sorted(executable, key=lambda issue: issue.docs_relative_path)


def status_blocked_issues(feature_set: FeatureIssueSet) -> list[IssueMetadata]:
    return sorted(
        [issue for issue in feature_set.issues if issue.status == "blocked"],
        key=lambda issue: issue.docs_relative_path,
    )


def dependency_blocked_issues(feature_set: FeatureIssueSet) -> list[DependencyBlockedIssue]:
    by_target = issue_by_link_target(feature_set)
    blocked: list[DependencyBlockedIssue] = []
    for issue in feature_set.issues:
        if issue.status not in EXECUTABLE_STATUSES:
            continue
        waiting_on = dependency_waiting_links(issue, by_target)
        if waiting_on:
            blocked.append(DependencyBlockedIssue(issue=issue, waiting_on=waiting_on))
    return sorted(blocked, key=lambda item: item.issue.docs_relative_path)


def status_class_name(status: str) -> str:
    return status.replace("-", "_")


def node_id(issue: IssueMetadata) -> str:
    stem = Path(issue.docs_relative_path).stem
    return "n_" + "".join(character if character.isalnum() else "_" for character in stem).strip("_")


def mermaid_label(issue: IssueMetadata) -> str:
    return issue.title.replace('"', '\\"')


def issue_layers(feature_set: FeatureIssueSet) -> dict[str, int]:
    by_target = issue_by_link_target(feature_set)
    cache: dict[str, int] = {}

    def layer_for(issue: IssueMetadata, stack: set[str]) -> int:
        if issue.link_target in cache:
            return cache[issue.link_target]
        if issue.link_target in stack:
            return 0
        dependency_layers: list[int] = []
        for dependency_link in issue.depends_on:
            dependency = by_target.get(internal_link_target(dependency_link))
            if dependency is not None:
                dependency_layers.append(layer_for(dependency, stack | {issue.link_target}))
        cache[issue.link_target] = (max(dependency_layers) + 1) if dependency_layers else 0
        return cache[issue.link_target]

    for issue in feature_set.issues:
        layer_for(issue, set())
    return cache


def format_issue_ref(issue: IssueMetadata) -> str:
    return f"{issue_link(issue)} {issue.title}"


def render_consistency_errors(errors: list[ConsistencyError]) -> str:
    if not errors:
        return "None"
    return "\n".join(f"- {error.path}: {error.message}" for error in errors)


def render_next_executable(feature_set: FeatureIssueSet) -> str:
    issues = currently_executable_issues(feature_set)
    if not issues:
        return "None"
    return "\n".join(f"- {format_issue_ref(issue)}" for issue in issues)


def render_waiting_on_dependencies(feature_set: FeatureIssueSet) -> str:
    blocked = dependency_blocked_issues(feature_set)
    if not blocked:
        return "None"
    return "\n".join(
        f"- {issue_link(item.issue)} waits on {', '.join(item.waiting_on)}" for item in blocked
    )


def render_mermaid(feature_set: FeatureIssueSet) -> str:
    layers = issue_layers(feature_set)
    issues_by_layer: dict[int, list[IssueMetadata]] = {}
    for issue in sorted(feature_set.issues, key=lambda item: item.docs_relative_path):
        issues_by_layer.setdefault(layers.get(issue.link_target, 0), []).append(issue)

    lines = ["```mermaid", "flowchart LR"]
    for layer_number in sorted(issues_by_layer):
        lines.append(f'  subgraph layer_{layer_number}["Layer {layer_number}"]')
        for issue in issues_by_layer[layer_number]:
            lines.append(f'    {node_id(issue)}["{mermaid_label(issue)}"]')
        lines.append("  end")

    by_target = issue_by_link_target(feature_set)
    for issue in sorted(feature_set.issues, key=lambda item: item.docs_relative_path):
        for dependency_link in issue.depends_on:
            dependency = by_target.get(internal_link_target(dependency_link))
            if dependency is not None:
                lines.append(f"  {node_id(dependency)} --> {node_id(issue)}")

    for issue in sorted(feature_set.issues, key=lambda item: item.docs_relative_path):
        lines.append(f"  class {node_id(issue)} {status_class_name(issue.status)}")

    for status, fill in STATUS_PALETTE.items():
        lines.append(f"  classDef {status_class_name(status)} fill:{fill},stroke:#1f2328,color:#ffffff")
    lines.append("```")
    return "\n".join(lines)


def render_feature_dependency_view(feature_set: FeatureIssueSet, generated_at: str) -> str:
    errors = validate_feature_issue_set(feature_set)
    lines = [
        "---",
        f"generated_at: {generated_at}",
        f"source_feature_slug: {feature_set.slug}",
        f"source_issue_count: {len(feature_set.issues)}",
        f"source_issue_fingerprint: {feature_set.source_fingerprint}",
        "---",
        "",
        f"# Issue Dependency View: {feature_set.slug}",
        "",
        "## Consistency errors",
        "",
        render_consistency_errors(errors),
        "",
        "## Next executable",
        "",
        render_next_executable(feature_set),
        "",
        "## Waiting on dependencies",
        "",
        render_waiting_on_dependencies(feature_set),
        "",
        "## Mermaid dependency graph",
        "",
        render_mermaid(feature_set),
        "",
    ]
    return "\n".join(lines)


def discover_feature_issue_sets(root: str | Path) -> list[FeatureIssueSet]:
    root_path = Path(root)
    issues_root = root_path / "docs" / "issues"
    if not issues_root.exists():
        return []
    feature_sets: list[FeatureIssueSet] = []
    for path in sorted(issues_root.iterdir(), key=lambda item: item.name):
        if not path.is_dir() or path.name.startswith("_"):
            continue
        if not list(path.glob("*.md")):
            continue
        feature_sets.append(load_feature_issue_set(root_path, path.name))
    return feature_sets


def combined_source_fingerprint(feature_sets: list[FeatureIssueSet]) -> str:
    records = [
        {
            "slug": feature_set.slug,
            "source_issue_count": len(feature_set.issues),
            "source_issue_fingerprint": feature_set.source_fingerprint,
        }
        for feature_set in sorted(feature_sets, key=lambda item: item.slug)
    ]
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def status_distribution(feature_set: FeatureIssueSet) -> str:
    counts = Counter(issue.status for issue in feature_set.issues)
    if not counts:
        return "None"
    return ", ".join(f"{status}={counts[status]}" for status in sorted(counts))


def root_issues(feature_set: FeatureIssueSet) -> list[IssueMetadata]:
    by_target = issue_by_link_target(feature_set)
    roots = [
        issue
        for issue in feature_set.issues
        if not any(internal_link_target(link) in by_target for link in issue.depends_on)
    ]
    return sorted(roots, key=lambda issue: issue.docs_relative_path)


def format_issue_links(issues: list[IssueMetadata]) -> str:
    if not issues:
        return "None"
    return ", ".join(issue_link(issue) for issue in issues)


def format_dependency_blocked_items(items: list[DependencyBlockedIssue]) -> str:
    if not items:
        return "None"
    return "; ".join(f"{issue_link(item.issue)} waits on {', '.join(item.waiting_on)}" for item in items)


def render_index_feature_section(feature_set: FeatureIssueSet) -> str:
    errors = validate_feature_issue_set(feature_set)
    lines = [
        f"## {feature_set.slug}",
        "",
        f"- View: [[issues/_views/{feature_set.slug}-dependencies]]",
        f"- Issue count: {len(feature_set.issues)}",
        f"- Status distribution: {status_distribution(feature_set)}",
        f"- Root issues: {format_issue_links(root_issues(feature_set))}",
        f"- Currently executable: {format_issue_links(currently_executable_issues(feature_set))}",
        f"- Status-blocked: {format_issue_links(status_blocked_issues(feature_set))}",
        f"- Dependency-blocked: {format_dependency_blocked_items(dependency_blocked_issues(feature_set))}",
        f"- Consistency errors: {render_consistency_errors(errors)}",
        "",
    ]
    return "\n".join(lines)


def render_dependency_index(feature_sets: list[FeatureIssueSet], generated_at: str) -> str:
    sorted_feature_sets = sorted(feature_sets, key=lambda item: item.slug)
    issue_count = sum(len(feature_set.issues) for feature_set in sorted_feature_sets)
    lines = [
        "---",
        f"generated_at: {generated_at}",
        f"source_feature_count: {len(sorted_feature_sets)}",
        f"source_issue_count: {issue_count}",
        f"source_issue_fingerprint: {combined_source_fingerprint(sorted_feature_sets)}",
        "---",
        "",
        "# Issue Dependency Index",
        "",
    ]
    for feature_set in sorted_feature_sets:
        lines.append(render_index_feature_section(feature_set))
    return "\n".join(lines)


def current_generated_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def dependency_views_dir(root: Path) -> Path:
    return root / "docs" / "issues" / "_views"


def selected_feature_sets(feature_sets: list[FeatureIssueSet], feature_slug: str | None) -> list[FeatureIssueSet]:
    if feature_slug is None:
        return feature_sets
    return [feature_set for feature_set in feature_sets if feature_set.slug == feature_slug]


def ensure_feature_exists(feature_sets: list[FeatureIssueSet], feature_slug: str | None) -> list[str]:
    if feature_slug is None:
        return []
    if any(feature_set.slug == feature_slug for feature_set in feature_sets):
        return []
    return [f"unknown feature: {feature_slug}"]


def write_dependency_views(root: str | Path, feature_slug: str | None = None, generated_at: str | None = None) -> list[Path]:
    root_path = Path(root)
    generated_at = generated_at or current_generated_at()
    feature_sets = discover_feature_issue_sets(root_path)
    errors = ensure_feature_exists(feature_sets, feature_slug)
    if errors:
        raise ValueError("; ".join(errors))

    output_dir = dependency_views_dir(root_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for feature_set in selected_feature_sets(feature_sets, feature_slug):
        path = output_dir / f"{feature_set.slug}-dependencies.md"
        path.write_text(render_feature_dependency_view(feature_set, generated_at), encoding="utf-8")
        written.append(path)

    index_path = output_dir / "index.md"
    index_path.write_text(render_dependency_index(feature_sets, generated_at), encoding="utf-8")
    written.append(index_path)
    return written


def generated_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    frontmatter, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return frontmatter


def validate_generated_views(root: str | Path, feature_slug: str | None = None) -> list[str]:
    root_path = Path(root)
    feature_sets = discover_feature_issue_sets(root_path)
    errors = ensure_feature_exists(feature_sets, feature_slug)
    target_feature_sets = selected_feature_sets(feature_sets, feature_slug)
    output_dir = dependency_views_dir(root_path)

    for feature_set in target_feature_sets:
        for error in validate_feature_issue_set(feature_set):
            errors.append(f"{feature_set.slug}: {error.path}: {error.message}")

        view_path = output_dir / f"{feature_set.slug}-dependencies.md"
        metadata = generated_metadata(view_path)
        if not metadata:
            errors.append(f"missing generated view: {view_path}")
            continue
        expected_count = str(len(feature_set.issues))
        if str(metadata.get("source_feature_slug", "")) != feature_set.slug:
            errors.append(f"stale generated view: {view_path} has wrong source_feature_slug")
        if str(metadata.get("source_issue_count", "")) != expected_count:
            errors.append(f"stale generated view: {view_path} has wrong source_issue_count")
        if str(metadata.get("source_issue_fingerprint", "")) != feature_set.source_fingerprint:
            errors.append(f"stale generated view: {view_path} fingerprint mismatch")

    index_path = output_dir / "index.md"
    index_metadata = generated_metadata(index_path)
    if not index_metadata:
        errors.append(f"missing generated index: {index_path}")
    else:
        expected_feature_count = str(len(feature_sets))
        expected_issue_count = str(sum(len(feature_set.issues) for feature_set in feature_sets))
        expected_fingerprint = combined_source_fingerprint(feature_sets)
        if str(index_metadata.get("source_feature_count", "")) != expected_feature_count:
            errors.append(f"stale generated index: {index_path} has wrong source_feature_count")
        if str(index_metadata.get("source_issue_count", "")) != expected_issue_count:
            errors.append(f"stale generated index: {index_path} has wrong source_issue_count")
        if str(index_metadata.get("source_issue_fingerprint", "")) != expected_fingerprint:
            errors.append(f"stale generated index: {index_path} fingerprint mismatch")

    return errors


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or validate Markdown dependency views for docs/issues feature issue sets."
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root containing docs/issues. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--feature",
        help="Optional feature slug under docs/issues to generate or validate one dependency view.",
    )
    parser.add_argument(
        "--check",
        "--validate",
        action="store_true",
        dest="check",
        help="Validate dependency consistency and generated view freshness without writing files. Exits 1 on errors.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.check:
        errors = validate_generated_views(root, args.feature)
        if errors:
            for error in errors:
                print(error)
            return 1
        print("Issue dependency views are fresh and consistent.")
        return 0

    try:
        written = write_dependency_views(root, args.feature)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    for path in written:
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
