# SyncTeX execution validation impact

Issue #116 retains the current eight-worker source-map query path while its
actual 90-second timeout trigger is investigated. The proven error-handling gap
is corrected at the subprocess boundary: `TimeoutExpired` becomes an
`AdapterError` with the stable local diagnostic identifier
`compiler_source_map_query_timeout`, page, coordinates, and timeout duration.
The timeout duration is unchanged. A failed query still aborts origin derivation
and reaches the existing public `final_compile_adapter_execution` rejection.

## Focused scenario

`test_registered_synctex_timeout_is_a_controlled_adapter_error` invokes the
registered-engine branch of `compiler_source_locations`. Its coherent positive
baseline supplies a real staged source file, a contained extractor identity,
the declared source membership, one actual-shaped PDF text object, and matching
SyncTeX output. The positive mapping must resolve to that source before the
single contradiction is introduced.

The negative scenario changes only the subprocess outcome to `TimeoutExpired`.
No authority or derived input is stale. The first failing boundary is source-map
query completion. The test checks the stable local diagnostic identifier, page,
90-second limit, preserved exception cause, and one invocation with no retry.
The local `AdapterError` interface does not expose a structured error code; this
is an explicit interface gap. The new test temporarily asserts only the stable
message fragment `compiler_source_map_query_timeout` at this local boundary.
It does not claim to assert the public provider's structured error code.
It does not claim to exercise the operating system's timeout mechanism or to
reproduce the real retained timeout. The public provider's failure/report gates
remain owned by the independently qualified Issue #115 coverage.

## Runtime qualification boundary

Isolated serial and eight-worker probes both completed the exact retained page
15 query. A second comparison began with an exact retained log copy above the
locally configured 1 MB rotation limit; both scenarios again completed. These
results do not support replacing the concurrent path with serial execution or
adding per-worker profiles. They do not establish the original timeout's cause.

The finite complete actual query sequence and a fresh public Final Compile
remain separate runtime qualifications. Diagnostic script completion is not
compile or delivery acceptance, and Issue #116 remains open until its actual
qualification criteria are met.

## Migration impact

- One new exact test method; no historical or full collection.
- Existing fixture builders, authority schemas, snapshots, seals and origin
  contracts are unchanged.
- Existing query order, worker count, environment and timeout limit are
  unchanged.
- Unexpected exceptions other than the identified timeout still follow the
  traceback-retention behavior from Issue #115.
