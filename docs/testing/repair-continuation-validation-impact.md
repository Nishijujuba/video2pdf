# Repair continuation validation impact

Issues #106 and #110 change the interpretation of existing repair gates. The authorized file set bounds remaining producer writes, and native-frame transform evidence remains required whenever the current Figure or its predecessor is native. Generated diagrams retain their own source-provenance path.

The user has explicitly excluded the obsolete full test collection for this work. Verification is limited to newly relevant exact test methods and the affected real public workflow qualifications. Historical complete-module and full-suite runs remain deferred under that instruction.

## Affected fixture graph

| Area | Issue #106: remaining repair writes | Issue #110: Figure evidence modes |
| --- | --- | --- |
| Positive fixtures | Public repair retries after a committed writer leave either an authorized subset or no remaining producer changes. | A generated diagram can update its source interval or regenerate its image with current bindings. |
| Negative fixtures | An extra changed producer output outside the failed-result authority is rejected. | A changed native Figure without transform evidence is rejected; changing its source kind to generated does not remove this requirement. |
| Shared builders | The new partial-resume tests obtain a genuine Production bundle from the existing Issue #106 continuation fixture. | The new generated-diagram tests reuse the existing Issue #107 source-backed Figure fixture. |
| Derived state | Frozen bundle entries, failed-result authority, claim generations, receipts, and the successor repair workspace remain coherent across interruption and replay. | Figure Manifest, contribution bytes, Production state, Artifact Generation Set, visual provenance, and Reader-Facing Text Inventory are rematerialized when their inputs change. |
| First failing gate | Unauthorized writes retain `precompile_repair_allowed_write_set` with `precompile_repair_write_set_mismatch`; contract-gap disposition validation retains its existing identity. | Native transform omissions retain `precompile_repair_figure_transform_record` with `precompile_repair_transform_required`. |
| Precedence | Existing claim/receipt and bound-workspace identity checks still precede continuation; changing the permitted-write comparison does not authorize a different bundle. | Native current or predecessor provenance takes precedence over the generated-diagram path. Source identity, decoded frame, crop, and output checks remain active. |
| Broader qualification | The original Run retries its frozen contract-gap command after Production has committed, then obtains fresh reviews. | The original and retained Runs exercise unchanged and regenerated generated diagrams through public repair promotion, diagnostic compile, and fresh reviews. |

No stored golden data, fixture schema, signature format, or cache format changes. The existing authorities retain their identity and validation responsibilities.

## Exact focused cases

`tests/video_workflow/test_issue106_partial_repair_resume.py`:

- `test_public_retry_accepts_authorized_partial_remaining_write_set`
- `test_public_retry_accepts_empty_remaining_write_set`
- `test_public_promotion_rejects_unauthorized_extra_producer_output`

`tests/video_workflow/test_issue110_generated_figure_repair.py`:

- `test_generated_diagram_source_interval_correction_needs_no_transform`
- `test_regenerated_generated_diagram_binds_current_figure_authority`
- `test_changed_native_source_timestamp_without_transform_fails_at_transform_gate`
- `test_native_to_generated_transition_without_transform_fails_at_transform_gate`

These focused cases establish the local contracts. Actual PDF qualification continues to require the public compile path, fresh semantic reviews, Acceptance Report v2, and Delivery Guard; a focused test pass does not itself confer delivery authority.
