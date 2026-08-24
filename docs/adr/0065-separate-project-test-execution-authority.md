---
status: accepted
---

# Separate project-test execution authority from planning and video-workflow runtime

Project Test Execution owns repository-wide test classification, resource ownership, admission, execution authority, and effective-runtime language because those concerns span every registered project-test suite. Project Governance publishes approved scope without runtime authority, Video Workflow publishes the runtime concepts and verification seams under test without consuming test decisions, and the runner remains the sole authority for each current execution from declarations, Discovery Evidence, valid admission evidence, and approved policy.

## Considered Options

- Project Governance ownership was rejected because that context owns planning and Human Publication authority while explicitly excluding runtime state.
- Video Workflow ownership was rejected because project tests span multiple suites and because Video Workflow already owns separate runtime meanings for Resource Classes, Resource Leases, and resource admission.
- A separate Project Test Execution Context was selected to preserve one canonical language without creating a second manually maintained execution authority.

## Consequences

The Project Test Execution glossary and Context Map own the stable cross-ticket vocabulary. Static policy schemas, resource APIs, scheduling algorithms, migration mechanics, and performance sampling protocols remain downstream consumer contracts. ADR 0059 and its current Promotion authority remain unchanged until a separately governed amendment succeeds.

