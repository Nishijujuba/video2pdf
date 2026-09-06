# Issue 121 validator fixture impact

The focused fixture starts from a complete current Content Production graph, a
current passing Precompile report and Seal, a fingerprinted repair-attempt
ledger, an exact Final Compile Manifest, and a terminal persisted Final Compile
failure. The public refresh command is the tested seam.

The positive scenario proves four separate relationships: Production set
membership is unchanged; Production and semantic-dependency bytes are
unchanged; the generated declaration changes after derivation; and fresh
Reviewer Skeletons receive new inventory bindings. Exact replay is idempotent,
while a different successor path is rejected by the Run-scoped claim.

The single-contradiction scenario changes only the predecessor path in the
persisted command after the otherwise valid graph is materialized. Its expected
first gate is `precompile_inventory_refresh_failure_binding` and its stable code
is `precompile_inventory_refresh_failure_command_mismatch`. No successor or
Run-scoped claim may be published.

The replay single-contradiction scenario changes only the published successor
inventory identity while retaining its old `inventory_sha256`. Its expected
first gate is `precompile_inventory_refresh_replay` and its stable code is
`precompile_inventory_refresh_replay_inventory_stale`. Generations,
dependencies, Reviewer Skeletons, and custody remain the valid baseline.

The current-Production contradictions each change one relation. One predecessor
generation advances beyond the authenticated current Production binding and
must fail with `precompile_inventory_refresh_current_production_mismatch`.
Separately, changing only the current Production compile-ready checkpoint to
`pending` must fail with
`precompile_inventory_refresh_current_production_incomplete`. Both fail at
`precompile_inventory_refresh_current_production` before successor or claim
publication.

Affected fixtures are limited to
`tests/video_workflow/test_precompile_inventory_refresh.py`, the new refresh
schema examples, and the Delivery Quality registry entry. Historical
Precompile and Final Compile fixtures do not require migration because the
existing contracts and provider-bound report format remain unchanged.
