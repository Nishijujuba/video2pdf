# Use one video workflow kernel with platform adapters

Fixed workflow behavior is currently split across Bilibili and YouTube skill prose, project instructions, and several independent scripts. The project will use one Video Workflow Kernel for the deterministic single-video lifecycle, with Bilibili and YouTube represented as Video Platform Adapters, so path rules, artifact contracts, checkpoints, and recovery have one authority while semantic work remains agent-owned.

## Considered Options

- Keep prompt orchestration and add more independent helper scripts: rejected because lifecycle order, schema preparation, path limits, and recovery rules would remain distributed and bypassable.
- Build one complete workflow per platform: rejected because the downstream outline, Pyramid Gate, compile, acceptance, and delivery contracts are shared.
- Put semantic writing and visual judgment inside the kernel: rejected because these steps require source-dependent interpretation and cannot be reduced to deterministic state transitions.

## Decision

Implement one root Video Workflow Kernel as the sole owner of deterministic single-video lifecycle mechanics. Bilibili and YouTube integrate through registered Video Platform Adapters. Semantic roles receive Kernel-issued contracts and retain content-dependent interpretation within their bounded Judgment Patches.

## Consequences

The kernel owns deterministic run initialization, bounded path naming, declared artifact identity, checkpoint preconditions, state transitions, schema-derived artifacts, fingerprints, idempotent retries, and fail-closed recovery. Platform adapters own platform metadata plus subtitle, cookie, format, and download behavior. Existing Pyramid, compile, acceptance, and delivery validators remain their contract authorities and are invoked by the kernel rather than reimplemented inside it. ADR 0006 defines the delivery-lifecycle portion of this broader kernel boundary.
