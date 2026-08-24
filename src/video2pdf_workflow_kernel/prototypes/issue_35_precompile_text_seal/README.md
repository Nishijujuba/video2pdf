# PROTOTYPE - Precompile Writing-Quality Gate and Text Seal

This throwaway prototype answers one question:

> Can a precompile state model prove that every declared reader-facing text item
> was evaluated against the canonical writing rules, bind the passing judgment
> to the current compile inputs, and safely reuse that judgment after a
> presentation-only change?

It is a planning artifact for
[Prototype the precompile writing-quality gate and text seal](https://github.com/Nishijujuba/video2pdf/issues/35).
It is not a production contract, runtime activation, validator, or implementation
slice.

## Run

From the repository root:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B src\video2pdf_workflow_kernel\prototypes\issue_35_precompile_text_seal\tui.py
```

The terminal app keeps all state in memory. It starts with six declared
reader-facing kinds: title, paragraph, caption, table cell, callout, and
footnote.

Suggested paths:

1. Press `g`, then `c` to observe a fresh review, seal, and compile admission.
2. Press `p`, then `c` to observe a presentation-only change block compilation.
   Press `e`, then `c` to observe deterministic text-equivalence and resealing.
3. Press `t` after a pass to observe a reader-text change invalidate the report
   and seal.
4. Press `f`, then `g` to observe complete coverage with one semantic failure
   produce no seal.
5. Press `u`, then `g` to observe unrepresented raster text become a blocking
   Contract Gap.
6. Press `v` to cycle through the state, inventory, report, seal, equivalence,
   and compile-admission views.

## Model choice

The prototype compares three identity strategies:

| Strategy | Benefit | Blocking weakness |
|---|---|---|
| Source-file fingerprints only | Strong compile-input lineage | Cannot prove that every visible text region was inventoried or reviewed |
| Extracted-text fingerprint only | Precise semantic reuse boundary | Cannot prove that the text came from current compile inputs |
| Dual identity with coverage ledger | Proves current source lineage, item coverage, and semantic equivalence separately | Requires a declared reader-facing surface and authoritative text representation for raster text |

The prototype uses dual identity. A `Reader-Facing Text Inventory` has:

- an exact inventory fingerprint that includes current Artifact Generations;
- a reader-text-set fingerprint over stable item identities and exact UTF-8 text;
- a coverage ledger from every declared visible region to one inventory item;
- an extraction record for each source artifact.

The `Writing Quality Report` records one result for every projected rule and
every inventory item. A pass requires the Cartesian coverage set
`projected rules x inventory items`, current fingerprints, and no Contract Gap.

The `Precompile Text Seal` binds the catalog, projection, inventory, coverage
ledger, report, and current Artifact Generations. It is immutable. A
presentation-only mutation makes the old seal stale. A deterministic
`Text Equivalence Report` may then prove that the reader-text set, coverage
surface, rule semantics, projection, and language profile are unchanged. The
report also records a bijective old-to-new mapping for every stable item
identity, including prior and current locators and text fingerprints. The
provider creates a successor seal for the new Artifact Generations and cites
the prior semantic judgment. A text change requires fresh semantic review.

## Boundary exposed to the next ticket

`Final Compile Admission` is only a boundary stub. It records the exact current
seal and compile-input closure admitted to Final Compile. The downstream final
quality materializer, rendered-PDF lineage, cross-phase findings, and global
evidence invalidation remain owned by
[Prototype final quality materialization and evidence invalidation](https://github.com/Nishijujuba/video2pdf/issues/33).
