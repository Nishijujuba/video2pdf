# Use phase plus a checkpoint graph for workflow state

Video-to-PDF work contains parallel section writing and figure preparation, gate checkpoints tied to individual artifacts, and repair loops that invalidate only part of the downstream evidence. A single linear stage cannot represent these conditions without hiding useful progress or treating stale evidence as current.

## Considered Options

- Use one linear stage value: rejected because parallel sections, figures, and partial repair cannot be represented accurately.
- Use only a checkpoint graph: rejected because operators and task indexes still need a compact progress projection for recovery and observability.
- Infer checkpoint readiness from file timestamps: rejected because timestamps do not prove schema validity, semantic pass decisions, or matching artifact fingerprints.

## Decision

The Video Workflow Run Record uses a hybrid state model: a coarse `phase` for human observability and a dependency-aware checkpoint graph for continuation decisions. Each checkpoint has a stable identity, declared prerequisites, current status, evidence references, and the fingerprints needed to determine whether its evidence still matches its inputs.

The kernel may advance work only from current checkpoint evidence. A `phase` value alone cannot authorize continuation. When an upstream input or authoritative report changes, the kernel invalidates every affected downstream checkpoint through declared dependencies while preserving unrelated parallel work.

## Consequences

Writer and Figure Agent work can progress independently, individual section gates can pass or retry separately, and a repair can invalidate main-document, compile, render, or acceptance evidence without discarding still-current source and outline evidence. The exact phase vocabulary, checkpoint inventory, status vocabulary, and invalidation transaction order require follow-up decisions.
