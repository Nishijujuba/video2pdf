# PROTOTYPE - Final Quality Materialization and Evidence Invalidation

This throwaway prototype answers one question:

> Can one deterministic state model combine current sealed precompile judgments
> with postcompile mechanical and visual evidence, invalidate only the checks
> affected by a repair, fail closed on Contract Gaps, enforce a three-attempt
> repair budget, and leave Delivery Guard as a mechanical freshness check?

It is a planning artifact for
[Prototype final quality materialization and evidence invalidation](https://github.com/Nishijujuba/video2pdf/issues/33).
It is not a production contract, runtime activation, validator, or
implementation slice.

The prototype starts with a complete passing delivery generation. All state is
in memory.

## Run

From the repository root:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B src\video2pdf_workflow_kernel\prototypes\issue_33_final_quality_materialization\tui.py
```

## Suggested paths

### Passing baseline

1. Press `d` to rematerialize the current Delivery Quality Report.
2. Press `k` to run the mechanical Delivery Guard check.

The report passes because every canonical rule has one current semantic owner,
the precompile and postcompile partitions are disjoint and complete, rendered
text reconciliation passes, and every report binds the current seals.

### Postcompile visual failure and layout repair

1. Press `v` to inject a current Visual Quality failure.
2. Press `d`, then `k`. Quality fails and Guard blocks.
3. Press `l` to begin repair attempt 1 with a layout-only mutation.
4. Press `a` to execute the invalidated checks.
5. Press `d`, then `k`.

Only the successor Precompile Text Seal, Final Compile, rendered-text
reconciliation, Visual Quality, final materialization, and Guard rerun. The
three precompile semantic reports remain reusable.

### Cross-phase semantic finding and text repair

1. Press `x` to add a postcompile finding against a precompile Writing Quality
   rule.
2. Press `d` to observe an authoritative final failure.
3. Press `t` to begin a text repair attempt.
4. Press `a`, `d`, then `k`.

The add-only finding cannot grant a pass or replace the Writing Quality owner.
The text mutation invalidates every precompile owner that depends on reader
text, then invalidates both seals and all postcompile evidence.

### Figure-content repair

1. Press `f`, then `a`.
2. Inspect `last_rerun`.

Source-Faithfulness reruns because the figure is admitted source content.
Writing Quality and Pyramid remain reusable because their declared inputs did
not change. Final Compile, rendered-text reconciliation, and Visual Quality
rerun because the final PDF changed.

### Reader-facing metadata repair

1. Press `m`, then `a`.
2. Inspect `last_rerun`.

Writing Quality and Pyramid rerun because the metadata is part of the
Reader-Facing Text Inventory. Source-Faithfulness remains reusable.

### Contract Gap

1. Press `g` to inject an unknown postcompile evidence identity.
2. Press `d`, then `k`.

Materialization reports `blocked_contract_gap`; Guard blocks mechanically.
The Contract Gap does not consume a repair attempt because automated repair is
not authorized to invent a missing contract.

### Repair budget exhaustion

1. Press `v`, then `d` to establish a quality failure.
2. Press `l`, `a`, `v`, `d` to make repair attempt 1 fail.
3. Repeat the previous step for attempts 2 and 3.

After attempt 3, the quality decision remains `fail` and routing becomes
`manual_repair_required`. The budget changes repair routing; it never converts
a semantic failure into another decision.

## State-model choice

The prototype compares three invalidation strategies:

| Strategy | Benefit | Blocking weakness |
|---|---|---|
| Rerun everything after every mutation | Simple and conservative | Discards valid independent judgments and makes repair cost unbounded |
| Trust a declared repair label | Cheap selective reruns | Lets a mislabeled or unclassified mutation preserve stale evidence |
| Fingerprinted dependency graph with fail-closed classification | Reuses unaffected reports while proving every retained binding | Requires explicit inputs, immutable generations, and a closed mutation taxonomy |

The prototype uses the fingerprinted dependency graph. Each check declares its
artifact and policy dependencies. A mutation advances immutable generations.
Only reports whose dependency snapshot differs become stale. Seals and final
materialization are derived nodes and therefore become stale when any bound
input changes.

The final `Delivery Quality Report` is the sole semantic delivery decision. It
normalizes already-owned judgments and validates partition, identity, coverage,
seal, and freshness invariants. It performs no prose reinterpretation.

`Delivery Guard` reads only the materialized decision, report fingerprint,
current seals, current final PDF, lifecycle stage, and repair-routing state. It
does not decide quality and cannot weaken a failure or Contract Gap.
