"""PROTOTYPE: terminal driver for Issue 40 rendered-text reconciliation."""

from __future__ import annotations

import json
import os
import sys


sys.path.insert(0, os.path.dirname(__file__))

from model import (  # noqa: E402 - prototype-local import
    add_classified_unexpected_text,
    add_unmapped_text,
    artifact_view,
    break_extraction_coverage,
    corrupt_generated_text,
    new_state,
    omit_sealed_item_rendering,
    reconcile,
    substitute_caption,
)


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
VIEWS = ("state", "sealed", "rendered", "origins", "report")


def _clear() -> None:
    print("\033[2J\033[H", end="")


def _render(state: dict, view_index: int) -> None:
    _clear()
    view = VIEWS[view_index]
    print(f"{BOLD}PROTOTYPE - Rendered Text Reconciliation{RESET}")
    print(f"{DIM}View: {view} | in-memory only | no runtime authority{RESET}\n")
    print(
        json.dumps(
            artifact_view(state, view),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    print()
    print(
        f"{BOLD}[r]{RESET} reconcile  "
        f"{BOLD}[o]{RESET} omit sealed item  "
        f"{BOLD}[s]{RESET} substitute text"
    )
    print(
        f"{BOLD}[a]{RESET} classified addition  "
        f"{BOLD}[u]{RESET} unmapped text  "
        f"{BOLD}[g]{RESET} generated mismatch"
    )
    print(
        f"{BOLD}[x]{RESET} extraction gap  "
        f"{BOLD}[v]{RESET} next view  "
        f"{BOLD}[n]{RESET} reset  "
        f"{BOLD}[q]{RESET} quit"
    )


def main() -> int:
    state = new_state()
    view_index = 0
    while True:
        _render(state, view_index)
        try:
            choice = input(f"\n{BOLD}action>{RESET} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice == "q":
            return 0
        if choice == "r":
            reconcile(state)
        elif choice == "o":
            omit_sealed_item_rendering(state)
        elif choice == "s":
            substitute_caption(state)
        elif choice == "a":
            add_classified_unexpected_text(state)
        elif choice == "u":
            add_unmapped_text(state)
        elif choice == "g":
            corrupt_generated_text(state)
        elif choice == "x":
            break_extraction_coverage(state)
        elif choice == "v":
            view_index = (view_index + 1) % len(VIEWS)
        elif choice == "n":
            state = new_state()


if __name__ == "__main__":
    raise SystemExit(main())
