# Acceptance successor lineage fixture impact

## Conclusion

[Issue #131](https://github.com/Nishijujuba/video2pdf/issues/131) treats the canonical Final Evidence binding as a replaceable projection and the committed attempt-1 execution binding as the immutable repair predecessor. [Issue #133](https://github.com/Nishijujuba/video2pdf/issues/133) records the identical-page FILES_PUBLISHED recovery dependency and blocks #131. The test uses the public Production, PRE, Final Compile, RTR, Final Evidence, Acceptance publication, failed materialization, lifecycle repair, and Acceptance repair commands. It creates no operator-authored origin plan and changes no Control Store authority directly.

## Fixture dependency graph

| Role | Fixture node |
|---|---|
| Authority input | Current Production artifact generations and the current Run revision |
| Derived nodes | Attempt-scoped generation set, inventory, PRE report and seal, Final Compile outputs, RTR report, canonical Final Evidence binding |
| Boundary | Materialized failed Acceptance execution with its immutable `executions/<id>/input-binding.json` |
| Successor materialization | Superseded writer, fresh fenced section Pyramid and main Pyramid attempts with Diagnostic Compile, then fresh attempt-scoped PRE, Final Compile, RTR, Final Evidence authority |
| Validation gate | Final Evidence successor lineage followed by immutable-predecessor Acceptance `repair_generation` and lifecycle preflight |
| Observation | The repaired public successor enters semantic attempt 2 with the exact predecessor generation set and the exact full-record changed-artifact set |

The registered compiler fixture requires its stable-TOC source directive before it models the ordinary multi-pass LaTeX TOC. The new fixture sets the valid Outline compile-support basename to `refs_VIDEO2PDF_FIXTURE_STABLE_TOC.bib`. Production creates that bibliography artifact and places its stem in the generated `\\bibliography{...}` command in `main.tex`; the directive therefore reaches the registered fake engine through the actual public integration path. The attempt-scoped Final Compile manifest stages the bibliography under the current Production artifact basename. Its PRE inventory declares the structured source text `\\section{Core claim}`, which is the compiler-derived TOC heading authority consumed by the registered adapter. The existing registered Runtime Policy, fake engine, `.toc` generation, and rendered contents page remain unchanged, and no integrated, staged, or sealed TeX is rewritten. `article_title` was rejected as a control point because Production does not place it in generated `main.tex`. A run-local compiler script was rejected by the Runtime Policy registration gate and is excluded without bypassing runtime authority.

The vertical scenario starts from a coherent positive attempt-1 graph. Both PRE and Final Compile workspaces are attempt-scoped. The failed report, attempt record, repair ledger companion, immutable binding, Precompile seal, and archived rendered-page bytes remain byte-identical after canonical Final Evidence advances. Final Evidence authenticates the predecessor through the materialized execution and committed report-publication intent, then derives lineage before replacing the canonical projection. Acceptance repair reads the same execution-owned predecessor and checks the declared changed IDs against the union of predecessor and successor logical IDs.

The successor publication injects the existing `after_pages_published` fault. The registered compiler can produce successor rendered pages whose bytes equal the predecessor pages even when the governed PDF artifact record changed. The FILES_PUBLISHED intent and valid `previous` archive identify the canonical directory as the interrupted candidate in that case; recovery preserves it under the failed-publication archive and restores `previous`. The first public reprepare returns `final_evidence_page_publication_restored`, and the next public reprepare commits the same successor lineage. After attempt 2 materializes a passing report and while the Run remains `ready_for_delivery`, an exact repeat returns `idempotent: true` with the same acceptance revision and byte-identical binding, current execution projection, and execution record. The final public Delivery Acceptance bind then reaches `accepted`.

Delivery lifecycle preflight and binding keep the strict empty-decision-slot rule owned by `DeliveryAcceptanceBindingProvider`. The repair transition first records the failed report while moving `ready_for_delivery` to `generating`. The later `generating` to `ready_for_delivery` transition publishes fresh final artifacts and clears the obsolete Acceptance and Delivery Guard projection slots through the existing lifecycle transaction and recovery journal. Immutable Acceptance execution history remains retained independently from those replaceable delivery projections.

The full-record changed set includes additions, removals, path changes, SHA changes, and any other record-field change. A removed optional artifact therefore remains a valid declared changed ID even though it is absent from the successor artifact list. Initial bindings still require an empty declaration, and repair admission still rejects empty, invented, or incomplete declarations by comparing against the immutable predecessor/successor union.

The invalid-predecessor negative starts from the same public Production through failed Acceptance materialization graph, then completes fresh Production, PRE, Final Compile, RTR, canonical Final Evidence successor, and Final Evidence authority publication. Its successor is therefore admissible for attempt 2 before the target mutation. The failed execution, immutable input binding, committed failed report bundle, attempt record, repair ledger, retained Precompile Text Seal, and Control Store authority are complete. The mutation copies the complete execution directory outside `review/acceptance/executions/` and changes only the replaceable `current.json.execution_root` pointer to that copy while preserving the execution ID. No dependent authority is rematerialized because pointer ownership is the single target contradiction. Public `acceptance-repair-prepare` must stop first at `repair_admission / acceptance_repair_history_invalid`, while the original immutable execution tree remains byte-identical. Restoring the original pointer and repeating the identical public command must succeed and prepare attempt 2, proving the successor baseline itself contains no competing contradiction.

## Affected surfaces

- New public vertical regression: `tests/video_workflow/test_issue131_acceptance_successor_lineage.py`.
- Initial Final Evidence semantics: `tests/video_workflow/test_issue13_final_evidence_cli.py`.
- Canonical page publication and recovery: `tests/video_workflow/test_issue100_final_evidence_page_publication.py`.
- Repair lineage validation: `tests/video_workflow/test_acceptance_v2.py`.
- Delivery lifecycle preflight: `tests/video_workflow/test_issue97_acceptance_delivery_order.py`.
- Fresh-ready projection owner: `src/video2pdf_workflow_kernel/delivery_lifecycle.py`.

Each new Issue #131 method is run by exact test ID through the persisted-command runner. Historical methods, full modules, and complete collections remain outside this slice. The public vertical directly qualifies successor page-publication recovery and exact-repeat ordering ahead of predecessor admission.
