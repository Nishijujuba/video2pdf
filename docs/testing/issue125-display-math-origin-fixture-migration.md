# Issue 125 display-math origin fixture migration

Issue 125 adds one focused fixture module and leaves completed Final Compile and
Rendered Text Reconciliation evidence unchanged.

The positive graph starts from two authenticated staged TeX files, one display
formula, one extracted PDF page, and reverse SyncTeX responses that cite the
formula's closing `$$` delimiter. The unrelated file deliberately has matching
line numbers and formula letters. The adapter must keep the compiler-reported
file, resolve the unique adjacent display body, retain the original page and
coordinate query, bind every split formula object to the formula item, and feed
that result to the public Rendered Text Reconciliation operation for a pass.

The two negative graphs start from the existing passing public Final Compile
builder. One adds unsupported display-resolution metadata to one adapter-produced
source record. The other identifies one source record as display-span-derived
while omitting its mandatory resolution proof. Final Compile must reject each
single contradiction first at `final_compile_source_origin_evidence` with
`compile_dependency_gap`.

No existing fixture, source medium, completed `o1`/`b1` report, or historical
test expectation is migrated. Fresh actual `o2`/`b2` compilation after reviewed
publication qualifies valid display-span publication against the affected Runs.
