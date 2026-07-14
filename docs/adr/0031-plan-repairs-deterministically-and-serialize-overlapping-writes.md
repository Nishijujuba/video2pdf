# Plan repairs deterministically and serialize overlapping writes

A failed Content Assurance cycle or Acceptance Report can contain independent text, figure, table, fidelity, terminology, and layout defects. Sending the entire failure set to one repair agent wastes parallelism, while launching one agent per finding can make several workers edit the same TeX or figure path. Materialized reports contain concrete evidence and bounded fix types that scripts can use for routing.

## Considered Options

- Use one repair agent for every failed report: rejected because unrelated section and figure work remains serialized and one context receives all failures.
- Launch one repair agent per failed criterion: rejected because criterion boundaries do not guarantee disjoint artifact writes.
- Ask a separate LLM planner to assign repairs: rejected because role selection and write-set conflict detection are deterministic contract work.
- Generate a deterministic plan and parallelize only disjoint write sets: selected because safe concurrency and recovery become mechanically provable.

## Decision

The Repair Planning Module accepts exactly one registered source type: a complete current Acceptance Report v2 or a Content Assurance Failure Set under ADR 0052. It creates `work/repairs/<source-gate>/cycle_XX/plan.json`. A Repair Plan binds the source gate and cycle, failed evidence, Artifact Generations, registered Repair Capabilities, exact read and candidate write sets, task dependencies, and required integration steps. It is validated before any Repair Agent launches.

The initial Repair Capabilities are `text_repair`, `figure_repair`, `layout_repair`, and `integration_repair`. Criterion results may select only registered fix types. The planner maps evidence locations and allowed fix types to candidate artifacts through deterministic rules. A Text Repair task edits assigned prose or TeX content. A Figure Repair task generates or repairs figure assets without changing canonical TeX references. A Layout Repair task owns assigned table, pagination, spacing, or presentation changes. Work requiring coordinated asset and TeX changes uses Integration Repair.

Tasks whose declared write sets are disjoint may run concurrently through independent Subagent Task Envelopes and Task Attempts. When two proposed tasks need the same `section_XX.tex`, `main.tex`, figure asset, or other canonical artifact, the planner combines them into one Integration Repair task. When safe ownership cannot be computed, the plan fails closed into one Integration Repair task rather than launching competing writers.

Repair agents receive only their assigned failure slice, relevant final artifacts and source context, exact paths, and required constraints. They do not receive reviewer chat history or another repair task's working files. Every output remains staged and passes the Task Completion Gate before Transactional Artifact Promotion.

After all planned repairs are promoted, the workflow follows the invalidation route declared by the source gate. Content Assurance repair reruns affected Pyramid gates, integration, diagnostic compile, and both assurance Adapters before final artifacts can be sealed. Acceptance repair reruns affected Pyramid and Content Assurance gates, Final Compile, complete rendered-page refresh, and both Acceptance dimensions. Compile or render failure remains a technical failure inside the current semantic cycle. Only a complete materialized failed Acceptance Report consumes the next item in the three-attempt acceptance budget.

## Consequences

Independent repairs gain parallel speed while shared files retain single-writer ownership. Repair planning becomes reproducible and testable without another semantic agent. Integration Repair can receive a larger context when failures overlap, but that cost is explicit and safer than post-hoc merge conflict resolution. The allowed-fix-type registry and evidence-to-artifact mapping require contract tests.
