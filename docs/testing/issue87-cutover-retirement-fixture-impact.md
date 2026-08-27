# Issue 87 validator fixture impact

## Conclusion

Issue #87 adds a precedence-sensitive retirement gate to the existing Global Gate, platform, and Batch cutover command seams. Existing pre-retirement fixtures remain valid. No new test case is introduced because Spec #83 explicitly forbids new cases; the existing public-command inventory assertion is updated to include `retire-cutover-authority`.

## Impact list

| Surface | Impact |
|---|---|
| Positive fixtures | Existing Global Gate, platform, and Batch fixtures represent the pre-Tombstone migration window and remain unchanged. |
| Negative fixtures | No existing fixture contains a Cutover Authority Tombstone or an active retirement fence, so no prior expected first gate changes. |
| Shared builders and scenario APIs | No fixture builder changes. Retirement inventory is read from the old public stores directly. |
| Derived snapshots and golden data | No existing snapshot, fingerprint, cache, or golden file derives from the Tombstone. |
| First-gate assertions | A published Tombstone owns `cutover_authority_tombstone` / `cutover_authority_retired`. A concurrently held retirement fence owns `retirement_fence` / `cutover_authority_retirement_in_progress`. |
| Precedence scenarios | Tombstone state is checked while the command owns the shared cutover fence; retirement cannot publish the Tombstone concurrently with a fenced old command. |
| Focused contract tests | The existing public-command inventory assertion is updated. Spec #83 prohibits adding a retirement scenario. |
| Complete affected suites | Deferred until all Spec #83 implementation Issues are complete, following the operator's explicit test-execution boundary. |

The migration command itself remains the approved public verification seam. Private helper behavior carries no completion authority.
