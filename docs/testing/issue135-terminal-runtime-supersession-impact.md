# Issue #135 terminal Runtime supersession fixture impact

Issue #135 adds one public CLI regression boundary. A completed and internally
consistent Runtime content-repair supersession is retained historical closure.
It must not keep an unrelated later ordinary PRE repair attached to the old
Runtime operation, old bundle, old PRE provider identity, or old canonical
Diagnostic Compile bytes.

## Public seams

- `compile-runtime-refresh` consumes a real passing predecessor Seal and Final
  Compile Manifest, republishes Diagnostic Compile under a supported MiKTeX
  policy, and records `precompile_refresh_required` from a real failed PRE
  workspace. The public promotion command then asks the Runtime owner to
  prepare the handoff.
- `delivery-quality-precompile-repair-promote` owns both the original Runtime
  handoff promotion and the later ordinary PRE repair.
- `delivery-quality-precompile-patch-commit`,
  `delivery-quality-precompile-materialize`, and `delivery-quality-seal` create
  each provider-owned PRE report and Seal. The first Seal closes the Runtime
  handoff through the public CLI integration.

No private predicate is mocked. Existing fixture helpers create complete
Production Runs and provider-authored task attempts; no existing test method is
executed as setup.

## Fixture dependency graph

```text
complete Production claims and receipts
  -> Compile Manifest and Diagnostic Compile
  -> failed PRE generation, inventory, dependencies, report
  -> immutable repair bundle and current claim snapshot
  -> Runtime refresh journal (precompile_refresh_required)
  -> prepared handoff -> promoted Production successor
  -> fresh passing PRE report and Seal
  -> superseded handoff and successor Final Compile Manifest
  -> retained terminal Runtime closure
  -> private PRE provider upgrade and public stale-old-Seal proof
  -> later failed PRE authority and new immutable bundle
  -> later Production successor and Diagnostic Compile
  -> later passing PRE report and Seal -> exact replay
```

The first terminal boundary freezes the Runtime journal, handoff, original
generation set, original inventory and semantic dependencies, original passing
PRE report and Seal, and the successor Final Compile Manifest. Later repair
materialization may replace current Production, Diagnostic, and PRE authority.

## Scenarios and first gates

| Scenario | Target contradiction or behavior | Rematerialized nodes | Intentionally stale node | Expected first gate / code |
|---|---|---|---|---|
| `issue135_terminal_then_later_repair` | Valid retained terminal closure permits a later ordinary repair without Runtime attachments after PRE provider drift | Private provider copy, complete later bundle, Production graph, Diagnostic Compile, PRE report and Seal | Historical provider identity and diagnostic attestation remain frozen | old Seal is publicly stale; later public promotion and exact replay succeed |
| `issue135_pending_without_attachments` | A genuinely pending Runtime operation requires both attachment identities | Current Runtime policy, Diagnostic Compile, bundle policy entry and journal | Runtime attachment arguments are absent | `content_repair_runtime_state` / `runtime_refresh_handoff_identity_required` |
| `issue135_terminal_manifest_file_drift` | The retained successor Final Compile Manifest bytes must match the terminal handoff binding | Handoff and journal fingerprints only | `successor_final_compile_manifest_sha256` | `content_repair_terminal_manifest_binding` / `runtime_refresh_terminal_manifest_file_drift` |

The invalid-terminal scenario starts from the passing terminal graph, changes
one binding, recomputes only its enclosing fingerprints, and preserves all
Production, claim, Artifact Generation, and successor-workspace state. Restoring
the exact terminal journal bytes makes the same public repair request eligible
again.

## Focused commands

Only these newly added methods are in scope:

The qualification caller must set `VIDEO2PDF_ISSUE135_RUNTIME_POLICY_JSON` to
an existing supported MiKTeX Runtime Policy. The fixture copies its registered
runtime inventory into each private test Run and produces a fresh policy through
the supported provider API; the committed test does not name an actual video Run.
The positive scenario installs a test-private package containing the shared
package initializer plus an extended package search path, and changes only a
copied `precompile_quality.py`. Every other Kernel module resolves from the
shared source tree, so its normal `__file__` resource locator continues to use
the current repository contracts. A byte-identical copy of the public
`scripts/video_workflow.py` is the entry point. The private PRE module keeps its
copied `__file__`, so provider freshness drifts without changing shared source
or retained authority.

```powershell
python -m unittest tests.video_workflow.test_issue135_terminal_runtime_supersession.Issue135TerminalRuntimeSupersessionTests.test_terminal_runtime_closure_allows_later_public_precompile_repair_and_exact_replay
python -m unittest tests.video_workflow.test_issue135_terminal_runtime_supersession.Issue135TerminalRuntimeSupersessionTests.test_pending_runtime_refresh_without_attachments_fails_before_publication
python -m unittest tests.video_workflow.test_issue135_terminal_runtime_supersession.Issue135TerminalRuntimeSupersessionTests.test_invalid_terminal_closure_binding_fails_before_publication_and_recovers
```

Each command must run through `scripts/persisted_command.py`. Historical test
methods and broad test collections remain outside this Issue's execution scope.
