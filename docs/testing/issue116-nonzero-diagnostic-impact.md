# Issue 116: nonzero SyncTeX diagnostic preservation

The registered SyncTeX query boundary now preserves a nonzero subprocess result as a controlled `compiler_source_map_query_nonzero` adapter error. Local diagnostic text includes the exact page, query coordinates, native exit code, and captured stderr. The public Final Compile provider continues to fail closed and point to retained adapter logs. A failing query cannot publish passing compile authority.

## Scope and selection

Actual qualification exposed a nonzero query failure whose native result was discarded by the generic error. Exact-query replays and one bounded comparison of the same first 28 queries with eight and one workers all succeeded. That comparison did not establish the internal trigger. This change therefore preserves evidence needed to diagnose another actual failure; it does not claim to resolve an underlying MiKTeX execution conflict.

Worker count, worker-owned log directories, subprocess environment, query order, source matching, the 90-second timeout, and coverage rules remain unchanged. No retry or fallback is introduced.

## Fixture impact

- Positive fixture: the existing registered-query fixture still resolves its source through the public `compiler_source_locations` function before a negative completion is introduced.
- Target contradiction: only the external SyncTeX completion changes to a nonzero result with known native stderr. Source inputs, manifest membership, and runtime roots remain valid.
- First failing gate: source-map query completion. `AdapterError` retains its existing text interface; the new stable message identity is `compiler_source_map_query_nonzero`.
- Shared builders, frozen generations, dependent manifests, schemas, snapshots, and unrelated negative scenarios are unaffected.
- The new exact method `test_registered_synctex_nonzero_retains_exact_query_and_native_error` proves one invocation, fail-closed behavior, and propagation of the query identity and native error. It replaces no existing timeout or worker-isolation assertion.

Verification is limited to this newly relevant exact method and subsequent real Final Compile qualification. No full or historical test collection is required. Passing the diagnostic test establishes failure visibility; Issue 116 still requires successful actual qualification before closure.
