# Partition acceptance with a versioned dimension map

Acceptance Criteria File v1 remains the approved quality standard, while Final Acceptance now uses separate Text and Visual reviewers. Editing the published criteria file to encode agent topology would couple quality policy to orchestration and silently change a versioned artifact. Hard-coding category ownership only in Python would make the partition difficult to inspect, fingerprint, and validate before reviewer launch.

## Considered Options

- Edit `acceptance_criteria.v1.json` in place: rejected because a versioned contract must not change meaning without a new version.
- Create Acceptance Criteria v2 solely for reviewer names: rejected because the quality rules themselves remain valid and unchanged.
- Hard-code the partition only in the Final Acceptance provider: rejected because the Skeleton could not bind a separately inspectable assignment contract.
- Add an independently versioned Acceptance Dimension Map: selected because quality policy and execution topology can evolve under separate contracts.

## Decision

The project adds `docs/acceptance/acceptance_dimension_map.v1.json` and its registered JSON Schema. The initial map assigns:

- `style`, `logic_readability`, and `formula_information_gain` to the Text Acceptance Dimension;
- `figure_visual_integrity`, `table_layout_integrity`, and `credibility_disclosure_placement` to the Visual Acceptance Dimension.

`acceptance-prepare` loads the Acceptance Criteria File and Acceptance Dimension Map, resolves every criterion to exactly one Primary Acceptance Dimension, and fails closed when the assignments overlap, omit a criterion, refer to an unknown criterion or category, or register a dimension unsupported by the active provider.

The immutable Acceptance Report Skeleton records the map path, schema version, contract version, and SHA-256 alongside the criteria path and SHA-256. Each dimension's Subagent Task Envelope and Generated Task Prompt receive the full rule text only for assigned criteria. Both also receive a generated Acceptance Criterion Reference Index containing every `criterion_id`, category, and Primary Acceptance Dimension, which permits a Cross-Dimension Finding to cite the other partition without granting normal evaluation authority. The v2 materializer verifies that the two submitted primary partitions remain disjoint and that their union exactly covers the current criteria list.

`docs/acceptance/acceptance_criteria.v1.json` remains byte-for-byte unchanged. Its generic phrase `Acceptance Reviewer` means the registered dimension reviewer assigned to execute that criterion. The phrase does not grant a reviewer access to criteria outside its Task Envelope.

Cross-Dimension Findings continue to use the configured criterion identity and the rules in their dedicated ADR. The Dimension Map assigns primary pass authority; it does not suppress valid failure observations from another dimension.

The map is part of Acceptance Report v2 provenance and Delivery Guard freshness checks. Changing its content invalidates existing skeletons and Judgment Patches.

## Consequences

The quality criteria remain stable while reviewer topology becomes deterministic and inspectable. Future criteria changes can retain or revise the map explicitly. Final Acceptance gains another versioned artifact that must be generated, validated, fingerprinted, and included in the atomic v2 activation group.
