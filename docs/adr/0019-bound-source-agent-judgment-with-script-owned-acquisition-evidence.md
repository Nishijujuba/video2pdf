# Bound source-agent judgment with script-owned acquisition evidence

Source preparation contains both deterministic mechanics and situational judgment. Platform downloads, canonical filenames, media probes, hashes, schema construction, and checkpoint transitions have repeatable rules. Subtitle-track choice, transcription fallback, and explicit treatment of missing source material can require semantic inspection. Allowing the Source Acquisition Agent to author the entire manifest would recreate the same schema-guessing failure already observed in review gates.

## Considered Options

- Let the Source Acquisition Agent create directories, downloads, and a freeform report: rejected because path, schema, and evidence contracts would remain prompt-dependent.
- Put every source choice into one fully automatic script: rejected because ambiguous subtitle quality and fallback decisions sometimes require semantic judgment.
- Generate a bounded decision shape for the agent and let scripts own acquisition evidence: selected because each responsibility has a verifiable owner.

## Decision

Run Initialization creates the fixed source and work directories through the Scaffold Generator. Before the Source Acquisition Agent starts, `source-prepare` verifies that scaffold and creates `work/source-acquisition/task.json` plus `work/source-acquisition/decision.skeleton.json`. These files contain the run identity, canonical source identity, allowed inputs and outputs, current acquisition policy, enumerated choices, and fields the agent may complete.

The Source Acquisition Agent may provide only the bounded Source Acquisition Decision, including subtitle-track selection, Whisper fallback rationale, and explicit known gaps. It does not create directory names, canonical filenames, Source Manifest structure, hashes, media probe results, or workflow checkpoint state.

Video Platform Adapter scripts perform platform download operations, canonical naming, conversion where required, and technical probing. `source-finalize` computes fingerprints, validates the decision against its skeleton and schema, writes the complete `source/manifest.json`, and records fresh Source Manifest evidence for the `source_ready` Workflow Checkpoint. Invalid or unauthorized structural changes fail closed.

After `source_ready`, the `source/` tree is read-only to Outline, Writer, Figure, Consistency, Independent Review, and Acceptance Reviewer agents. Any later source mutation requires Source Reopen. That operation preserves the earlier acquisition evidence, reactivates source preparation, and invalidates every dependent checkpoint before new source work begins.

An expired or rejected platform cookie remains a user-input blocker. The adapter records the failure and the workflow waits for a refreshed cookie instead of changing authentication strategy automatically.

## Consequences

The data-preparation subagent retains the semantic choices that require inspection, while scripts control paths, manifests, fingerprints, and state transitions. Downstream agents receive one technically validated and immutable source package. Additional task-envelope standardization across other subagent roles remains a follow-up decision.
