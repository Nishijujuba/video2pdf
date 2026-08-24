# Project Test Execution Context

Status: active.

This context owns the repository-wide language for project-test execution, concurrency classification, test-resource ownership, admission, and execution authority. It excludes Project 2.0 planning authority, Video Workflow runtime state, implementation schemas, scheduling algorithms, and performance qualification protocols.

## Test Module Declaration

The test author's statically interpretable statement of a module's concurrency classification, permitted execution granularity, and complete correctness-relevant Resource Claims. It asserts intent and completeness without proving safety or granting execution authority.

## Discovery Evidence

The runner-generated observation of the discovered test inventory, Execution Unit membership, and successfully interpreted Test Module Declarations at one defined source state. It proves what the runner observed and evaluated without proving declaration completeness or parallel safety.

## Runner Execution Authority

The runner's sole authority to admit and execute the current project-test workload from current Discovery Evidence, Test Module Declarations, valid Parallel Admission evidence, and approved policy. Declarations and admission evidence constrain that decision, while scheduling results record completed decisions without independently authorizing execution.

## Test Resource Boundary

The execution-time ownership boundary established by the runner for one admitted Execution Unit. It binds that unit's complete Resource Allocations, isolation identities, and lifecycle evidence across every correctness-relevant resource it uses.

## Resource Claim

A Test Module Declaration's statement of a required correctness resource, its conflict identity, required access semantics, and lifecycle scope. It states the conditions an Execution Unit needs for safe execution without reserving or proving isolation of the resource.

## Resource Allocation

The runner's execution-time satisfaction of an admitted Resource Claim through a concrete isolated resource, shared permission, exclusive instance, or capacity share. It is valid only for its owning Execution Unit and the lifetime of that unit's Test Resource Boundary.

## Resource Conflict

The relation between concurrent Resource Claims that cannot both receive valid Resource Allocations under the same conflict identity and required access semantics. A conflict applies to the claims involved and does not make their modules permanently mutually exclusive.

## Correctness Resource

A resource whose unsafe concurrent use can change test semantics, state, or outcome. An unsatisfied Correctness Resource Claim blocks concurrent admission.

## Capacity Resource

A finite host capability whose concurrent consumption can affect duration, reliability, or variance while preserving test semantics. Capacity constrains bounded admission and performance qualification without independently making a module Exclusive.

## Filesystem Resource

A Correctness Resource identified by a runner-allocated isolated root or a specific pre-existing filesystem target. Private allocated roots do not conflict, repository reads may be shared, and writes to the same pre-existing or repository-local identity require exclusive allocation.

## Database Resource

A Correctness Resource identified by one database's storage or service identity. A private database allocated inside one Test Resource Boundary may run concurrently, while Execution Units using the same existing database identity conflict according to their access semantics.

## Process Environment Resource

The Worker-process-local environment, temporary-directory bindings, and working directory allocated to an Execution Unit and inherited by its child processes. Mutations contained within that process tree are private; host-persistent or cross-process-visible configuration requires a separate Correctness Resource Claim.

## Network Endpoint Resource

A Correctness Resource identified by a private allocated endpoint, a fixed socket address, or a real network-service identity actually used by a registered test. Private temporary endpoints may run concurrently, while matching fixed endpoints or service identities conflict according to their access semantics; network capacity remains a Capacity Resource.

## Process Tree Resource

The Worker process and complete descendant process tree exclusively owned by one Execution Unit's Test Resource Boundary. Its allocations remain held until expected terminal state and cleanup are proven; surviving or unidentified descendants prevent successful release.

## In-Process Shared State

Threads, barriers, executors, module globals, and other state shared only inside one Worker process and owned by its Execution Unit lifecycle. It is not independently allocated across Execution Units, and any cross-Worker state must be represented by the corresponding external Resource Claim.

## External Dependency Resource

An external tool, host service, fixed device, license entitlement, or shared service instance used by an Execution Unit. Reentrant independent use may be shared, while singleton state, global configuration, fixed instances, and bounded entitlements require identity-specific exclusive or capacity allocation.

## Host Capacity Resource

A runner-managed pool of finite host capability such as CPU, memory, disk throughput, or process capacity. Its shortage delays admission or affects performance qualification without changing Correctness Resource semantics or module classification.

## Resource Access Semantics

The concurrency meaning of a Resource Claim: Shared permits compatible concurrent allocations, Private Instance requires a distinct identity per Test Resource Boundary, Keyed Exclusive permits one holder for each conflict key, and Capacity Share consumes a bounded pool without expressing correctness exclusivity.

## Parallel-Safe Test Module

A module with an interpretable Test Module Declaration, complete Correctness Resource Claims, and current valid Parallel Admission evidence. Its Execution Units may run concurrently whenever the runner can provide compatible Resource Allocations.

## Exclusive Test Module

A module whose safe execution cannot be expressed through bounded identity-specific Resource Claims and therefore requires a runner-wide exclusive allocation for the complete Execution Unit lifetime. Keyed resource conflicts and host-capacity pressure do not independently make a module Exclusive.

## Unclassified Test Module

A module lacking a current interpretable and evidence-consistent execution classification. The runner rejects its admission without silently converting it to serial or Exclusive execution; observed resource use beyond its declaration invalidates the current execution and its classification evidence.

## Execution Unit

The smallest test workload that the runner independently discovers, admits, assigns one Test Resource Boundary, executes in one Worker process, and records to terminal state. Its fixture, process-tree, resource, and result lifecycle is indivisible during that execution.

## Module Execution Unit

The default Execution Unit containing one discovered Python test module. Module fixtures, module globals, TestCase ordering dependencies, and In-Process Shared State remain inside one Worker lifecycle unless the module receives explicit class-level admission.

## TestCase-Class Execution Unit

An opt-in Execution Unit containing one discovered TestCase class and preserving that class's complete fixture and Worker lifecycle. It is available only when every class in the module is independently attributable and the module has current Class Execution Admission.

## Class Execution Admission

The evidence-backed approval to replace one Module Execution Unit with complete, non-partial TestCase-Class Execution Units. It requires exact test ownership, preserved class fixtures, equivalent or absent module-level shared lifecycle, attributable Resource Claims, and valid Parallel Admission evidence for every resulting unit; missing or invalid admission fails without automatic module fallback.

## Execution Granularity

The admitted shape of an Execution Unit: module by default or complete TestCase class after Class Execution Admission. Individual test methods remain inside their owning unit and have no independent admission, resource, or Worker lifecycle in the initial model.

## Parallel Admission

The one-time evidence-backed qualification of a new or materially changed parallel contract through genuinely overlapping, independently allocated instances of the candidate Execution Unit. It establishes reusable parallel eligibility from complete outcomes, boundary containment, and resource release without authorizing any particular execution or deciding performance qualification.

## Resource Ownership Lifecycle

The responsibility chain in which test authors declare Resource Claims, Discovery binds and interprets them, the runner validates and allocates them, the Worker uses and reports them, and the runner verifies terminal cleanup before release. Undeclared use, boundary escape, incomplete cleanup, or an unprovable terminal state fails the execution and prevents successful release.

## Admission Invalidation

The loss of current classification or Parallel Admission eligibility after a change that can alter discovered test identity, Execution Granularity, fixture lifecycle, Resource Claims, process behavior, or cross-Worker state, or after an observed boundary or cleanup violation. Uncertain relevance fails closed and requires renewed admission, while changes proven unable to affect those concerns preserve eligibility.

## Effective Runtime Reduction

A same-epoch end-to-end elapsed-time improvement in which the slowest valid bounded-parallel sample is faster than the fastest valid equivalent jobs-one sample. Overlapping observed ranges are inconclusive host variance, and a parallel range no faster than the serial range is no material improvement; diagnostic timings and historical absolute thresholds cannot override this relation.
