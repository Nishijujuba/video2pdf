# Final Compile adapter failure diagnostics validation impact

## Conclusion

Issue #115 retains the governed adapter's complete stdout and stderr inside the
existing Final Compile operation workspace. A failed public provider result
identifies those local files and the adapter exit code. These files support
diagnosis only: they do not enter the Compile Manifest, Final Artifact Seal,
Compile Report, artifact fingerprints, Acceptance, or delivery
authority.

## Fixture dependency graph

| Role | Issue #115 fixture binding |
| --- | --- |
| Authority inputs | Current Precompile Text Seal, Reader-Facing Text Inventory, Artifact Generation set, Final Compile Manifest, Runtime Policy, registered adapter identity |
| Derived nodes | Immutable operation identity, compile request, execution state, adapter output, Final Compile publication artifacts |
| Boundary | Registered adapter child-process invocation in the existing operation workspace |
| Validation gates | Adapter execution identity, nonzero exit or forbidden stderr rejection, required adapter evidence, publication and reconciliation guards |
| Observations | `final_compile_adapter_execution`, `final_compile_adapter_failed`, exit code, retained stdout path, retained stderr path |

## Scenario records

`issue115-controlled-adapter-failure` starts from the existing passing public
provider fixture and changes only the registered adapter process result. The
controlled result returns a nonzero exit with multiline Unicode stdout and an
`AdapterError` detail on stderr. The execution terminal state and both diagnostic
files are rematerialized. No authority node is stale. The first failing gate is
`final_compile_adapter_execution`, with error code
`final_compile_adapter_failed`.

`issue115-unexpected-adapter-exception` starts from the same graph and changes
only the adapter call to raise an unexpected exception. The adapter emits its
traceback to captured stderr, the provider retains that stream, and the operation
fails at the same gate and error code. No Final Compile report is published.

## Migration impact

- Positive fixtures: unchanged; the coherent existing public adapter fixture is
  the base graph.
- Negative fixtures: two new single-contradiction scenarios in
  `test_issue115_adapter_failure_diagnostics.py`.
- Shared fixture builders and scenario APIs: unchanged.
- Derived snapshots, hashes, signatures, caches, and golden data: unchanged.
- First-gate assertions: both new scenarios assert
  `final_compile_adapter_execution` and `final_compile_adapter_failed`.
- Precedence scenarios: none.
- Focused contract validation: only the two exact new methods are run.
- Historical and complete suites: excluded by the approved Issue #115 boundary.

The retained diagnostics remain under the immutable operation workspace so the
existing interrupted-workspace and reconciliation behavior continues to own
recovery. No retry, truncation, fingerprint, or external logging system is
introduced.
