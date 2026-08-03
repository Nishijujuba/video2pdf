# Qualify implementation before final validation

Status: accepted.

## Context

Project 2.0 Implementation Tickets can require expensive final validators and
evidence publication. Running those stages before an independent review has
stabilized the implementation causes their fingerprint-bound results to become
stale whenever the review finds a code or contract defect.

## Decision

For every Implementation Ticket that requires formal Exit Evidence or another
publication proof, use the following Implementation Qualification Sequence:

1. Implementation.
2. Affected Tests.
3. Two-axis review through the authoritative `/code-review` skill.
4. Code Freeze.
5. Final Qualification Validator.
6. Publication.

The `/code-review` skill remains the sole authority for the meaning and conduct
of its two review axes. This ADR does not restate or specialize that contract.
The executing primary workflow owns the concrete tests, validators, evidence
commands, and internal gate logic required by its current Ticket.

A review finding returns the Ticket to Implementation and Affected Tests. A
final-validator finding that requires a code or contract change breaks Code
Freeze and returns the Ticket through Affected Tests and two-axis review. A
validator operation that only materializes or verifies evidence preserves Code
Freeze and does not require another review.

## Consequences

Expensive final evidence is generated after the reviewed implementation is
stable. Ticket-specific verification remains adaptable, while every formally
qualified Ticket has the same high-level ordering and rollback boundary.
