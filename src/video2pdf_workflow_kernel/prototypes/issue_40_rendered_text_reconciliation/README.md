# PROTOTYPE - Rendered Text Reconciliation

This throwaway prototype answers one question:

> Can a deterministic provider prove that the current final PDF contains the
> complete reader-facing text sealed before Final Compile, while classifying
> every rendered text object without performing semantic reinterpretation?

It is a planning artifact for
[Prototype rendered-text reconciliation against the precompile text seal](https://github.com/Nishijujuba/video2pdf/issues/40).
It is not a production contract, PDF extractor, validator, runtime activation,
or implementation slice.

## Run

From the repository root:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B src\video2pdf_workflow_kernel\prototypes\issue_40_rendered_text_reconciliation\tui.py
```

The terminal app keeps all state in memory. Suggested paths:

1. Press `r` to reconcile the intact fixture. Exact, declared transformation,
   generated, split, and raster-backed text all pass.
2. Press `o`, then `r` to observe a sealed item omission.
3. Press `s`, then `r` to observe a mapped substitution.
4. Press `a`, then `r` to observe a fully classified unexpected addition.
5. Press `u`, then `r` to observe an unclassified rendered object become a
   blocking Contract Gap.
6. Press `g`, then `r` to observe generated text whose deterministic recipe no
   longer reproduces the rendered value.
7. Press `x`, then `r` to observe incomplete PDF extraction coverage block the
   provider before comparison.
8. Press `v` to cycle through state, sealed inventory, rendered inventory,
   origin manifest, and reconciliation report.

## Contract choice

Raw extracted strings are insufficient evidence. Repeated text, split glyph
runs, reading-order differences, raster text, ligatures, and generated text make
string-only matching ambiguous. The prototype therefore requires four current,
fingerprinted inputs:

1. the `Precompile Text Seal` and complete `Reader-Facing Text Inventory`;
2. a `Final Artifact Seal` binding the final PDF and compile inputs;
3. a complete `Rendered Text Object Inventory` produced by a closed extractor
   suite over every page content stream, text annotation, form XObject, and
   declared raster-text representation;
4. a compiler-produced `Text Origin Manifest` that classifies every rendered
   object as sealed-origin, generated, or unexpected, and supplies stable
   provenance edges.

The reconciliation provider validates those inputs and materializes one report.
It never guesses origin from text similarity and never decides whether prose is
good. It accepts only closed deterministic comparison recipes:

- `exact_utf8`;
- `layout_whitespace`;
- `unicode_presentation`;
- `declared_generated`.

Multiple rendered objects may reconstruct one sealed item, so the core relation
is a provenance graph rather than a one-to-one string table. Every sealed item
must receive its declared rendering cardinality. Every rendered object must
have exactly one disposition. Generated text must reproduce from declared
sealed or Final Artifact inputs. Unexpected additions are validly classified
failures; missing disposition is a Contract Gap.

## Result classes

The report keeps evidence completeness separate from fidelity:

- `omission`: a required sealed item has no complete rendered reconstruction;
- `substitution`: provenance exists, while the declared comparison recipe does
  not reproduce the sealed text;
- `addition`: a rendered object is explicitly classified as unexpected;
- `generated_mismatch`: a generated recipe does not reproduce rendered text;
- `contract_gap`: origin, recipe, extraction coverage, identity, or object
  disposition cannot be proven;
- `pass`: every sealed item and generated object reconciles, every rendered
  object is classified exactly once, and every input binding is current.

This separation prevents a structurally complete report containing a diagnosed
addition from being confused with an incomplete report that silently missed a
text object.

## Boundary exposed to the next ticket

The report is mechanical evidence for
[Prototype final quality materialization and evidence invalidation](https://github.com/Nishijujuba/video2pdf/issues/33).
That materializer may validate freshness and aggregate the report's decision.
It must not reinterpret text or turn a reconciliation failure into a pass.
