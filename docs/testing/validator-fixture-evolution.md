# Validator Fixture Evolution Standard

Multi-stage fail-closed validators must treat their fixtures as dependency graphs. A negative scenario is valid only when the graph is coherent except for the one invariant that the scenario intends to violate. The test must also identify the first validation gate expected to observe that violation.

This standard prevents a common false signal: a test remains red, yet an incidental upstream inconsistency masks the behavior that the test claims to cover.

## Scope

This standard applies when a validator test has any of these characteristics:

- multiple ordered validation gates can reject the same input;
- fixture fields, files, records, snapshots, hashes, signatures, caches, or summaries are derived from other fixture state;
- a commit, snapshot, seal, publication, or finalization boundary fixes an earlier generation;
- changing one authority input requires downstream materialization;
- the intended result depends on failure precedence.

Ordinary unit tests with independent literal inputs do not require this process.

## Fixture dependency graph

A qualifying fixture must identify these roles, using code, a compact table, or an adjacent comment:

| Role | Meaning |
|---|---|
| Authority input | State from which other fixture values are derived. |
| Derived node | A field or artifact computed from one or more upstream nodes. |
| Boundary | A commit, snapshot, seal, or lifecycle transition after which an earlier generation must remain fixed. |
| Validation gate | An ordered check that owns one or more invariants. |
| Observation | The stable error code or structured result asserted by the test. |

A typical graph has this shape:

```text
authority input
  -> committed or frozen generation
  -> derived records
  -> aggregate or summary
  -> finalization binding
  -> report fingerprint
```

Changing an upstream node without rematerializing its reachable downstream nodes creates additional contradictions. Those contradictions are fixture defects unless the test explicitly targets them.

## Normative rules

### 1. Start from a valid graph

Every negative scenario must first construct a positive fixture that passes all applicable gates. Shared builders should make this valid state the default.

### 2. Declare one target contradiction

A negative scenario must declare:

- the target invariant;
- the mutation seam where the contradiction is introduced;
- the nodes rematerialized after the mutation;
- any deliberately stale node;
- the expected first failing gate;
- the stable gate or error code expected from that gate.

All non-target invariants must remain coherent. A helper such as `assert_coherent_except(target_invariant)` is encouraged when it can independently check that property.

### 3. Rematerialize by dependency, not by memory

After an authority input changes, the fixture builder must recompute every reachable downstream node unless a stale relationship is the declared target contradiction. Tests must not manually guess which hashes, summaries, snapshots, bindings, or fingerprints need updates.

Reusable high-coupling fixtures must expose lifecycle-aware scenario operations. Suitable operations describe when a mutation occurs, such as before commit, before snapshot, before seal, after publication, or before report binding. Thin setters that merely replace arbitrary nested fields do not own enough behavior.

### 4. Make the first failing gate part of the contract

The expected first failing gate is part of each negative scenario. If the test fails at another gate, the fixture is invalid for its stated purpose even when the overall result is still rejection.

Gate precedence tests are a separate scenario class. They intentionally contain multiple contradictions and declare which failure must dominate. They must not be used as substitutes for single-contradiction coverage.

### 5. Separate membership, materialization, and change

Assertions must distinguish these relationships:

| Relationship | Question |
|---|---|
| Governed membership | Which objects belong to the authoritative output set? |
| Materialization | Which objects were recomputed or written? |
| Value change | Which objects have different semantic values or bytes? |
| Idempotent equality | Which materialized objects may legitimately remain identical? |
| Freshness binding | Which objects require a new generation or binding even when content is equal? |

Membership in a generated artifact set does not imply that every member changes bytes on every materialization. Tests may require byte differences only when byte change is itself a documented invariant.

### 6. Assert stable gate identity

Validators should expose stable, machine-readable gate or error codes. Human-readable messages may evolve independently. Negative tests assert the stable code plus only the structured fields needed for the scenario.

An older validator without structured failures may temporarily use the smallest stable message fragment. The test or migration record must identify this as a gap rather than treating full prose as a permanent interface.

## Scenario record

Each qualifying negative scenario should make the following record visible in its name, parameter table, builder call, or adjacent comment:

```text
scenario_id:
target_invariant:
mutation_seam:
rematerialized_nodes:
intentionally_stale_nodes:
expected_first_gate:
expected_error_code:
scenario_class: single_contradiction | precedence
```

The record is a review aid. It does not require a separate file or schema when the test code already expresses the same information clearly.

## Validator change protocol

Adding, removing, strengthening, or reordering a validation gate requires one atomic fixture migration. The change must include an impact list covering:

- positive fixtures;
- negative fixtures;
- shared fixture builders and scenario APIs;
- derived snapshots, hashes, signatures, caches, and golden data;
- expected first-gate assertions;
- precedence scenarios;
- focused contract tests;
- the complete affected module or acceptance suite.

Review must reject a validator change when the implementation and affected fixture graph migrate in separate, temporarily inconsistent steps.

## Test layers for expensive modules

Long-running modules use two complementary layers:

### Fast contract layer

The fast layer exercises dependency rematerialization, single contradictions, first-gate identity, and gate precedence with bounded fixtures. It provides immediate feedback for validator and builder changes.

### Complete acceptance layer

The complete layer exercises the real module and complete evidence graph. It is required before merge, authority refresh, release, or any other operation that gives the result governing status.

A fast pass cannot replace the complete layer. An interrupted complete run, an unknown terminal state, or a run without durable terminal evidence remains unverified.

## Diagnosing an unexpected earlier failure

When a negative scenario reaches an earlier gate than expected:

1. Preserve the actual first failure and identify its owning invariant.
2. Trace the mutated node's outgoing dependency edges.
3. Find the first downstream node that retained an obsolete generation.
4. Decide whether that stale relation is the declared contradiction.
5. If it is incidental, move the mutation to the correct lifecycle seam or rematerialize the downstream closure.
6. Rerun the focused scenario and then the complete affected module.
7. Do not weaken the earlier gate merely to expose the later assertion.

Repeated failures of this kind indicate a missing dependency edge or a fixture builder that does not own materialization fully.

## Worked example: committed evidence chain

One project validator consumes a chain containing implementation authority, a committed execution generation, source snapshots, discovery, assignments and results, summaries, finalization data, persisted output, and a report fingerprint.

Several negative scenarios changed an authority field after the committed generation while leaving downstream bindings untouched. The validator correctly rejected the live-versus-committed mismatch before reaching the intended safety or reassignment assertion. Another scenario assumed that every governed authority artifact must change bytes after generation, even though one artifact could be materialized idempotently.

The reusable lesson is independent of that validator:

- a sealed evidence package is a graph rather than a bag of JSON files;
- local field mutation is unsafe when downstream nodes encode the earlier generation;
- rejection at the wrong gate is missing coverage, even when the test remains red;
- generated-set membership and changed-value membership require separate assertions;
- validator hardening and fixture migration form one review unit.

## Review checklist

- [ ] The fixture's authority inputs, derived nodes, boundaries, and ordered gates are visible.
- [ ] The scenario starts from a passing graph.
- [ ] A negative scenario declares exactly one target contradiction, or is explicitly a precedence scenario.
- [ ] All incidental downstream state is rematerialized.
- [ ] The expected first gate and stable error code are asserted.
- [ ] Membership, materialization, value change, idempotence, and freshness are not conflated.
- [ ] A gate change includes its fixture migration impact list.
- [ ] Focused contract tests pass.
- [ ] The complete affected module has a verified terminal result.
