"""PROTOTYPE: terminal driver for the Issue 35 text-seal state model."""

from __future__ import annotations

import json
import os
import sys


sys.path.insert(0, os.path.dirname(__file__))

from model import (  # noqa: E402 - prototype-local import
    add_unprovable_raster_text,
    apply_presentation_only_change,
    artifact_view,
    attempt_final_compile_admission,
    edit_reader_text,
    inject_semantic_failure,
    new_state,
    prove_text_equivalence_and_reseal,
    run_writing_quality_gate,
)


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
VIEWS = ("state", "inventory", "report", "seal", "equivalence", "compile")


def _clear() -> None:
    print("\033[2J\033[H", end="")


def _render(state: dict, view_index: int) -> None:
    _clear()
    view = VIEWS[view_index]
    print(f"{BOLD}PROTOTYPE - Precompile Writing-Quality Gate and Text Seal{RESET}")
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
        f"{BOLD}[g]{RESET} gate + seal  "
        f"{BOLD}[p]{RESET} presentation edit  "
        f"{BOLD}[e]{RESET} equivalence + reseal"
    )
    print(
        f"{BOLD}[t]{RESET} text edit    "
        f"{BOLD}[f]{RESET} semantic failure  "
    )
    print(
        f"{BOLD}[u]{RESET} unprovable raster text  "
        f"{BOLD}[c]{RESET} compile admission"
    )
    print(
        f"{BOLD}[v]{RESET} next artifact view  "
        f"{BOLD}[r]{RESET} reset  "
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
        if choice == "g":
            run_writing_quality_gate(state)
        elif choice == "p":
            apply_presentation_only_change(state)
        elif choice == "e":
            prove_text_equivalence_and_reseal(state)
        elif choice == "t":
            edit_reader_text(state)
        elif choice == "f":
            inject_semantic_failure(state)
        elif choice == "u":
            add_unprovable_raster_text(state)
        elif choice == "c":
            attempt_final_compile_admission(state)
        elif choice == "v":
            view_index = (view_index + 1) % len(VIEWS)
        elif choice == "r":
            state = new_state()


if __name__ == "__main__":
    raise SystemExit(main())
