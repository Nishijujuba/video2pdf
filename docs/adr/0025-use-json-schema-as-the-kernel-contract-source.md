# Use JSON Schema as the kernel contract source

The kernel needs fixed machine-readable contracts that scripts can validate and agents can inspect without guessing report shapes. Defining the same fields independently in Python models, JSON Schema files, prompts, and examples would create multiple drift-prone authorities. The required Python environment currently lacks `jsonschema`; Pydantic is importable only as an undeclared transitive dependency.

## Considered Options

- Hand-write Python validation and keep schema files as documentation: rejected because executable behavior could diverge from the shapes shown to agents.
- Use Pydantic models as the authority and generate schemas: rejected because Pydantic is not a declared project runtime dependency and generated output would depend on its version and model configuration.
- Use versioned JSON Schema as the structural authority with a declared standards-based validator: selected because contracts remain language-neutral, inspectable, and directly executable.

## Decision

Kernel-owned contract schemas use JSON Schema Draft 2020-12 and live under `schemas/video-workflow/<major-version>/`. Every schema has a unique `$id`, closed object rules where appropriate, explicit required fields, bounded enumerations, and a registered `schema_name` plus `schema_version`. The initial registry covers the Video Workflow Run Record, Artifact Plan, Subagent Task Envelope, Source Manifest, and Source Acquisition Decision.

The Kernel Schema Registry is the only authority for structural field names, types, required fields, enumerations, and additional-property policy. Python code does not repeat those structures in Pydantic or parallel field tables. Cross-file relationships that JSON Schema cannot prove, such as path containment, fingerprint freshness, write-set overlap, and provider evidence binding, use separately registered Python invariant checks. Those checks may add relational constraints and cannot redefine schema fields.

Implementation explicitly adds and locks the `jsonschema` package in the required uv-managed runtime. The current design phase records the dependency and does not install it. Kernel startup and `contracts-check` fail with an actionable runtime error when the declared validator is unavailable.

`contracts.py` resolves schemas only through the registry, validates instances with the registered Draft 2020-12 validator, invokes applicable invariant checks, and calls per-contract skeleton builders for runtime values. Every Contract Skeleton is validated immediately before publication. Skeleton builders populate current paths, identities, fingerprints, and reserved judgment slots; they do not define an alternative contract shape.

`contracts-check` verifies unique schema identities, registry completeness, resolvable references, validator compatibility, valid skeleton-builder fixtures, and valid positive and negative examples. An unregistered schema or builder cannot be used by a kernel operation. Gate Provider schemas remain under their existing owners in the first implementation phase.

## Consequences

Agents receive executable current contracts instead of prose reconstructions. Structural changes require one versioned schema update and corresponding registry tests. Runtime relationship checks remain explicit without duplicating field definitions. The project gains one declared dependency whose installation and lock update belong to the implementation phase.
