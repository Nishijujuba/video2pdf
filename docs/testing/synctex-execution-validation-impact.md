# SyncTeX execution validation impact

Issue #116 retains the current eight-worker source-map query path and gives
each worker its own persistent MiKTeX log directory. The subprocess boundary
also corrects the proven error-handling gap: `TimeoutExpired` becomes an
`AdapterError` with the stable local diagnostic identifier
`compiler_source_map_query_timeout`, page, coordinates, and timeout duration.
The timeout duration is unchanged. A failed query still aborts origin derivation
and reaches the existing public `final_compile_adapter_execution` rejection.

## Focused scenarios

`test_registered_synctex_timeout_is_a_controlled_adapter_error` invokes the
registered-engine branch of `compiler_source_locations`. Its coherent positive
baseline supplies a real staged source file, a contained extractor identity,
the declared source membership, a text object extracted from an actual fixture
PDF, and matching
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

`test_concurrent_synctex_workers_own_distinct_persistent_log_directories` maps
16 actual fixture PDF spans across two synchronized waves of eight queries.
It observes the environment at the subprocess boundary: eight active workers
must own eight distinct existing log directories under the original log root,
and each worker must retain its directory across queries. All other environment
values and the caller's environment remain unchanged. This catches shared logs,
query-ordinal assignment to logs under a dynamic work queue, and per-query
directory creation. It does not claim to reproduce MiKTeX's operating-system
logging behavior.

The first attempted RED fixture omitted the base log directory and failed that
incidental precondition. That run is retained as invalid RED evidence. After
the base directory was created, the exact method failed at the intended
observation: eight workers shared one log directory.

## Runtime qualification boundary

Isolated serial and eight-worker probes both completed the exact retained page
15 query. A second comparison began with an exact retained log copy above the
locally configured 1 MB rotation limit; both scenarios again completed. These
short probes did not establish the original timeout's cause.

The subsequent finite replay of all 1,431 actual queries reproduced one
90-second timeout with shared logs (857.640 seconds overall). The same frozen
query sequence, PDF, SyncTeX data, eight-worker limit and 90-second limit then
completed all 1,431 queries with zero failures using worker-owned log
directories (951.373 seconds overall). Only `MIKTEX_USERLOGDIRECTORY` changed
between the query environments. This comparison supports isolating mutable
logs; it does not prove the exact internal MiKTeX failure mechanism or establish
a speed improvement.

Worker initialization owns the environment copy and directory lifecycle. A
thread retains its directory while consuming the dynamic query queue. Runtime
configuration, data, installation roots and query inputs remain shared as
before. Serial execution would discard the useful eight-worker behavior, and
per-query directories would create 1,431 unnecessary directories in this case.

A fresh public Final Compile remains the separate runtime acceptance boundary.
Diagnostic script completion is not compile or delivery acceptance, and Issue
#116 remains open until its actual qualification criteria are met.

## Migration impact

- Two new exact test methods; no historical or full collection.
- Existing fixture builders, authority schemas, snapshots, seals and origin
  contracts are unchanged.
- Existing query order, worker count and timeout limit are unchanged; only the
  worker's mutable log-directory environment value changes.
- Unexpected exceptions other than the identified timeout still follow the
  traceback-retention behavior from Issue #115.
