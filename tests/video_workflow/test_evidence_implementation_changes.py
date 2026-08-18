from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
import sys

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_case_dir
from video2pdf_workflow_kernel.evidence import (
    fingerprint_implementation_changes,
    implementation_change_tombstones,
    sha256_git_archive,
)


def _git(root: Path, *arguments: str, input_text: str | None = None) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stdout + completed.stderr)
    return completed.stdout.strip()


class ImplementationChangeEvidenceTests(unittest.TestCase):
    def _repository(self) -> tuple[Path, str]:
        repository = new_case_dir(self.id(), label="evidence-change-repository")
        _git(repository, "init")
        _git(repository, "config", "user.name", "Evidence Test")
        _git(repository, "config", "user.email", "evidence@example.invalid")
        (repository / "old.txt").write_text("old authority\n", encoding="utf-8")
        (repository / "keep.txt").write_text("kept\n", encoding="utf-8")
        _git(repository, "add", "old.txt", "keep.txt")
        _git(repository, "commit", "-m", "base")
        return repository, _git(repository, "rev-parse", "HEAD")

    def test_rename_fingerprints_target_and_records_source_tombstone(self) -> None:
        repository, base = self._repository()
        (repository / "old.txt").rename(repository / "new.txt")
        _git(repository, "add", "-A")
        _git(repository, "commit", "-m", "rename authority")
        implementation = _git(repository, "rev-parse", "HEAD")

        self.assertEqual(
            fingerprint_implementation_changes(repository, base, implementation),
            [
                {
                    "role": "implementation_artifact",
                    "path": "new.txt",
                    "sha256": sha256_git_archive(
                        repository, implementation, "new.txt"
                    ),
                }
            ],
        )
        self.assertEqual(
            implementation_change_tombstones(repository, base, implementation),
            [
                {
                    "role": "implementation_tombstone",
                    "path": "old.txt",
                    "base_sha256": hashlib.sha256(b"old authority\n").hexdigest(),
                    "change": "renamed",
                    "target_path": "new.txt",
                }
            ],
        )

    def test_real_deletion_has_tombstone_and_no_deleted_artifact_fingerprint(self) -> None:
        repository, base = self._repository()
        _git(repository, "read-tree", base)
        _git(repository, "update-index", "--force-remove", "--", "old.txt")
        tree = _git(repository, "write-tree")
        implementation = _git(
            repository,
            "commit-tree",
            tree,
            "-p",
            base,
            "-m",
            "delete authority",
        )

        self.assertEqual(
            fingerprint_implementation_changes(repository, base, implementation), []
        )
        self.assertEqual(
            implementation_change_tombstones(repository, base, implementation),
            [
                {
                    "role": "implementation_tombstone",
                    "path": "old.txt",
                    "base_sha256": hashlib.sha256(b"old authority\n").hexdigest(),
                    "change": "deleted",
                    "target_path": None,
                }
            ],
        )

    def test_gitlink_fingerprint_binds_the_referenced_commit(self) -> None:
        repository, base = self._repository()
        _git(repository, "read-tree", base)
        _git(
            repository,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{base},linked-authority",
        )
        tree = _git(repository, "write-tree")
        implementation = _git(
            repository,
            "commit-tree",
            tree,
            "-p",
            base,
            "-m",
            "bind gitlink authority",
        )

        self.assertEqual(
            fingerprint_implementation_changes(repository, base, implementation),
            [
                {
                    "role": "implementation_artifact",
                    "path": "linked-authority",
                    "sha256": hashlib.sha256(
                        f"gitlink {base}\n".encode("ascii")
                    ).hexdigest(),
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
