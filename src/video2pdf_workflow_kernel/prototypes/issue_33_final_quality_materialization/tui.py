"""PROTOTYPE: terminal shell for the Issue 33 pure state model."""

from __future__ import annotations

import json
import os

from model import (
    apply_repair,
    artifact_view,
    inject_contract_gap,
    inject_cross_phase_finding,
    inject_visual_failure,
    materialize,
    new_state,
    run_affected_checks,
    run_delivery_guard,
)


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
VIEWS = ("state", "reports", "quality", "guard", "attempts")


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def render(state: dict, view: str) -> None:
    clear()
    print(f"{BOLD}PROTOTYPE - Final Quality Materialization{RESET}")
    print(
        f"{DIM}Issue 33 | view={view} | planning artifact; no runtime authority{RESET}\n"
    )
    print(json.dumps(artifact_view(state, view), ensure_ascii=False, indent=2))
    print(f"\n{BOLD}Evidence actions{RESET}")
    print("[a] run affected checks  [d] materialize final decision  [k] guard check")
    print("[v] inject visual failure [x] inject cross-phase failure [g] Contract Gap")
    print(f"\n{BOLD}Repair actions (each starts one budgeted attempt){RESET}")
    print("[t] text  [f] figure content  [l] layout-only  [m] reader metadata")
    print(f"\n[c] cycle view  [r] reset  [q] quit")


def main() -> None:
    state = new_state()
    view_index = 0
    while True:
        render(state, VIEWS[view_index])
        choice = input("\n> ").strip().lower()[:1]
        if choice == "q":
            return
        if choice == "a":
            run_affected_checks(state)
        elif choice == "d":
            materialize(state)
        elif choice == "k":
            run_delivery_guard(state)
        elif choice == "v":
            inject_visual_failure(state)
        elif choice == "x":
            inject_cross_phase_finding(state)
        elif choice == "g":
            inject_contract_gap(state)
        elif choice == "t":
            apply_repair(state, "reader_text")
        elif choice == "f":
            apply_repair(state, "figure_content")
        elif choice == "l":
            apply_repair(state, "layout")
        elif choice == "m":
            apply_repair(state, "reader_metadata")
        elif choice == "c":
            view_index = (view_index + 1) % len(VIEWS)
        elif choice == "r":
            state = new_state()


if __name__ == "__main__":
    main()
