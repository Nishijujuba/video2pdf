# Issue 87 validator fixture impact

## Conclusion

Issue #87 adds a precedence-sensitive retirement gate around the public `retire-cutover-authority` seam. Issue #90 subsequently archived the old Global Gate, platform, and Batch cutover CLI commands while preserving current Global Gate final-quality reads. No new test method is introduced: the existing consolidated public-command case now exercises retirement behavior directly.

## Impact list

| Surface | Impact |
|---|---|
| Positive fixtures | The existing consolidated public-command case constructs a healthy live Control Store, preserved Global Gate final-quality authority, old platform and Batch projections, SQLite families, sidecars, intents, candidates, and available/missing historical paths. |
| Negative fixtures | The same existing case covers an unpublished Profile path, a competing PREPARED migration, a capability contradiction, and authority resurrection after Tombstone publication. |
| Shared builders and scenario APIs | Lifecycle-aware builders materialize one coherent retired-authority graph and invoke the public CLI; each negative scenario starts from its own coherent project graph. |
| Derived snapshots and golden data | Retirement moves the declared old authority family into one audit bundle. Tests distinguish live-state preservation, archive membership, source absence, and idempotent replay. |
| First-gate assertions | The unpublished Profile fails at `project_configuration`; a competing migration and resurrection fail at `retirement_resume`; a capability contradiction fails at `capability_consistency`. Stable error codes are asserted. |
| Precedence scenarios | The Tombstone is validated before any retired provider mutation. The Profile is loaded only after the exclusive retirement fence is acquired, and the public test simulates publication immediately before that acquisition. Issue #90 removed the old cutover CLI mutators, so no supported old command remains to claim a command-wide retirement fence. Current Global Gate policy checks use the preserved read-only authority path. |
| Focused contract tests | `test_batch_and_release_maintenance_commands_are_public` retains its existing identity and now executes the retirement seam; the test-method count remains unchanged. |
| Complete affected suites | Deferred until all Spec #83 implementation Issues are complete, following the operator's explicit test-execution boundary. |

The migration command itself remains the approved public verification seam. Private helper behavior carries no completion authority.
