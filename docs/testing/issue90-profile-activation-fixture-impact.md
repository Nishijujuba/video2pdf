# Issue #90 Profile Activation Fixture Impact

Issue #90 changes the ordered public admission gates from cutover-specific
authority checks to one coordinated Profile activation. This record identifies
the fixture graph migrated with that change.

## Impact list

- Positive fixtures: the committed Workflow Release Profile, Profile activation,
  project configuration, Cutover Authority Tombstone, and Control Store identity
  remain one coherent graph.
- Negative fixtures: inactive or stale Profile activation, maintenance-fence
  contention, unsafe session identity, and retired-command inventory each retain
  one declared contradiction.
- Shared builders: the existing `start-run`, `batch-plan`, release-maintenance,
  retirement, and Control Store recovery builders remain the owning scenario APIs.
- Derived state: Profile SHA bindings, Tombstone compatibility, Batch/Run
  projections, and operator-contract mirrors are rematerialized from their current
  authority input.
- First-gate assertions: `project_maintenance_fence` precedes activation and Run or
  Batch publication; `cutover_authority_tombstone` precedes Profile activation;
  Profile activation precedes capability-specific admission.
- Precedence scenarios: maintenance ownership wins before ordinary Run or Batch
  creation; a committed Tombstone wins before any retired cutover mutator.
- Focused contract tests: the existing Issue #13 and Issue #15 public-seam test
  cases own activation, `start-run`, `batch-plan`, retired-command inventory, and
  operator-contract behavior. Issue #90 adds no test case or test method.
- Complete affected modules: the Issue #13 initialization, Issue #15 Batch CLI,
  Issue #15 cutover, Control Store recovery, platform-policy documentation,
  Batch-policy documentation, and skill-contract modules form the affected set.

## Positive activation-order graph

The activation record is written while the exclusive project maintenance fence
is held and before retirement publishes the Tombstone. Ordinary admission still
requires both records, so the Tombstone remains the final commit marker.

```text
validated Profile and historical release package
  -> durable Profile activation record
  -> retired cutover authority audit bundle
  -> committed Cutover Authority Tombstone
  -> Profile-backed ordinary admission
```

This is the positive fixture graph for the existing public-seam tests. It is not
a negative single-contradiction scenario and therefore declares no expected
failure gate or error code.
