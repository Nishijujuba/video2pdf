# Issue 129 Acceptance Patch fingerprint fixture impact

The focused fixture enters through public `acceptance-prepare`,
`acceptance-patch-commit`, and `acceptance-reconcile` commands. It uses a
Run-record-free Legacy Acceptance Input Set so the test isolates the shared
Judgment Patch ingress and publication authority from unrelated Kernel delivery
and Final Compile fixture evolution.

The dependency graph is: current Global Gate and Legacy input files (authority
inputs) -> Acceptance Execution, Task Envelope, and active Reviewer Claim
(boundary) -> reviewer-authored staging bytes without `patch_sha256` ->
provider-normalized canonical staging bytes (derived node) -> Patch publication
intent and committed Patch (boundaries) -> execution and Claim projections
(observations).

The positive scenario proves that provider normalization preserves every
reviewer-authored value, publishes one canonical fingerprint identity, and
keeps exact retry byte-idempotent. Recovery scenarios inject faults after Patch
file preparation and after the first Control Store intent commit. The former
reconciles by aborting the uncontrolled intent before a fresh commit; the latter
finishes the already controlled publication. Both retain the same normalized
staging identity.

Negative scenarios each start from the valid graph and introduce one target
contradiction:

- An array-wrapped otherwise valid Patch reaches the existing schema registry
  before mapping-only fingerprint normalization and fails first at
  `delivery_quality_schema_validation` with `contract_invalid` and public CLI
  exit 20.
- A supplied fingerprint mismatch remains intentionally stale and fails first
  at `patch_identity` with `acceptance_patch_fingerprint_invalid`.
- Duplicate visual page coverage keeps schema shape valid and fails first at
  `visual_page_coverage` with `acceptance_visual_page_coverage`.
- A stale Claim fencing token fails first at `patch_fencing` with
  `acceptance_patch_fencing_stale`.

All negative staged bytes remain unchanged. Gate precedence at the shared
`acceptance-patch-commit` ingress is: non-object JSON reaches schema validation;
object submissions then reach supplied-fingerprint identity or provider
derivation; derived objects reach the full schema and semantic gates; valid
semantic submissions reach pending-publication and Claim fencing; persistence
starts only after the current Claim and execution authority match. This keeps
malformed shape, identity, semantic, and stale-authority failures from
rewriting reviewer staging bytes.

The complete affected runtime and test surface is
`src/video2pdf_workflow_kernel/acceptance_v2.py`'s shared Patch commit ingress,
the focused builders and assertions in
`tests/video_workflow/test_issue129_acceptance_patch_fingerprint.py`, and the
shared Acceptance v2 fixture/suite in
`tests/video_workflow/test_acceptance_v2.py` that supplies the Task
Envelope-aware Patch builder. The other modules that directly exercise this
shared commit ingress are
`tests/video_workflow/test_issue94_rendered_page_authority.py` and
`tests/video_workflow/test_issue97_acceptance_delivery_order.py`. Their shared
builder implementations and fingerprinted fixture data remain unchanged.
Acceptance schemas, golden data, canonical committed-Patch validation fixtures,
and historical test inputs also remain unchanged and require no data migration.

The user authorized deferring the historical Acceptance module and full-suite
execution. Qualification therefore runs only the three exact Issue 129 public
CLI test methods. This execution boundary leaves the shared historical suite as
an identified affected verification surface without adding unrelated tests or
changing its fixtures.
