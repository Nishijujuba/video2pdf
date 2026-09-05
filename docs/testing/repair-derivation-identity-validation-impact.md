# Repair derivation operation identity validation impact

## Conclusion

The Precompile repair promotion operation identity must bind every caller input
that changes immutable derived bytes. The bounded change adds the exact
`prepared_at` value and the candidate Reader-Facing Text Inventory and semantic
dependency file content identities, then advances the successor inventory
derivation version from 6 to 7. Existing operation directories remain immutable
historical evidence.

## Fixture dependency graph

| Role | Issue fixture binding |
| --- | --- |
| Authority inputs | Repair bundle, predecessor Generation set, current Production State, Compile Manifest, repair authority, exact `prepared_at`, candidate inventory bytes, candidate semantic-dependency bytes |
| Derived nodes | Operation ID, successor Generation set, successor inventory, current semantic dependencies, derived visual-source provenance |
| Boundary | Immutable `review/precompile/production-repair-promotions/<operation_id>` directory |
| Validation gates | Immutable derived-artifact equality, repair input advancement, successor workspace fencing |
| Observations | Distinct operation roots for distinct derivation inputs; `precompile_repair_evaluation_inputs_unchanged` for an otherwise identical repair; existing-successor replay classification |

## Scenario records

`prepared_at`, inventory, and semantic-dependency collision scenarios each begin
from a valid current Production graph. Their first request intentionally retains
all governed evaluation inputs and reaches the stable no-change gate after
materializing one partial immutable derivation. The second request changes one
authority input. Every other input stays fixed. The expected behavior is a new
operation identity; a changed timestamp reaches the same no-change gate in a
separate operation, while a changed governed inventory or dependency projection
can publish its eligible successor.

The exact replay scenario publishes one valid semantic-input successor, repeats
the same public request, and requires the existing successor classification with
unchanged operation bytes.

## Migration impact

- Positive fixtures: the new public-seam successor and exact replay scenarios.
- Negative fixtures: the three single-input collision scenarios.
- Shared builders: existing Issue 106 and Issue 113 current Production fixtures;
  no builder contract changes.
- Derived snapshots and golden data: none.
- First-gate assertions: the stable unchanged-evaluation-input error code.
- Precedence scenarios: none.
- Focused validation: only the two new test methods in the independent test file.
- Complete acceptance suites: intentionally excluded by the approved ticket
  boundary; the original frozen Run supplies the later recovery evidence.
