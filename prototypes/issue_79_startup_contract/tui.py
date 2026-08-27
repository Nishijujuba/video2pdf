"""PROTOTYPE ONLY: interactive shell for the Issue 79 startup model."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model import ProjectConfig, ReleaseProfile, RuntimeState, StartRequest, evaluate_startup


SCENARIOS = {
    "1": (
        "ordinary Bilibili start",
        ReleaseProfile(),
        RuntimeState(),
        StartRequest(platform="bilibili"),
    ),
    "2": (
        "ordinary YouTube start while another Run holds an unrelated Claim",
        ReleaseProfile(),
        RuntimeState(unrelated_active_claims=1),
        StartRequest(platform="youtube"),
    ),
    "3": (
        "first Run in a pristine workspace",
        ReleaseProfile(),
        RuntimeState(control_store_state="absent_pristine"),
        StartRequest(platform="bilibili"),
    ),
    "4": (
        "incompatible published Release Profile",
        ReleaseProfile(contracts_compatible=False),
        RuntimeState(),
        StartRequest(platform="bilibili"),
    ),
    "5": (
        "Control Store identity is incomplete",
        ReleaseProfile(),
        RuntimeState(control_store_state="identity_incomplete"),
        StartRequest(platform="youtube"),
    ),
    "6": (
        "requested output path is already claimed",
        ReleaseProfile(),
        RuntimeState(output_path_available=False),
        StartRequest(platform="bilibili"),
    ),
    "7": (
        "pre-existing Legacy directory",
        ReleaseProfile(),
        RuntimeState(existing_directory=True),
        StartRequest(platform="youtube"),
    ),
}


def render(selected: str) -> None:
    title, release, runtime, request = SCENARIOS[selected]
    decision = evaluate_startup(ProjectConfig(), release, runtime, request)
    print("\033[2J\033[H", end="")
    print("\033[1mPROTOTYPE ONLY - Workflow 2.0 start-run contract\033[0m")
    print(f"\033[2mScenario {selected}: {title}\033[0m\n")
    print("\033[1mProposed invocation\033[0m")
    print(
        "python scripts\\video_workflow.py start-run "
        "--project-config config\\workflow-project.v1.json "
        f"--platform {request.platform} --source-url <url> --session-id {request.session_id}\n"
    )
    print("\033[1mFull evaluated state\033[0m")
    print(json.dumps(decision.as_dict(), ensure_ascii=False, indent=2))
    print("\n\033[1mScenarios\033[0m")
    for key, (name, *_rest) in SCENARIOS.items():
        print(f"  \033[1m[{key}]\033[0m \033[2m{name}\033[0m")
    print("  \033[1m[q]\033[0m \033[2mquit\033[0m")


def main() -> int:
    selected = "1"
    while True:
        render(selected)
        choice = input("\nChoose a scenario: ").strip().lower()
        if choice == "q":
            return 0
        if choice in SCENARIOS:
            selected = choice


if __name__ == "__main__":
    raise SystemExit(main())
