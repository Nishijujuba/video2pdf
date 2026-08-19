# Bound source-agent judgment with script-owned acquisition evidence

Source preparation contains both deterministic mechanics and situational judgment. Platform downloads, canonical filenames, media probes, hashes, schema construction, and checkpoint transitions have repeatable rules. Subtitle-track choice, transcription fallback, and explicit treatment of missing source material can require semantic inspection. Allowing the Source Acquisition Agent to author the entire manifest would recreate the same schema-guessing failure already observed in review gates.

## Considered Options

- Let the Source Acquisition Agent create directories, downloads, and a freeform report: rejected because path, schema, and evidence contracts would remain prompt-dependent.
- Put every source choice into one fully automatic script: rejected because ambiguous subtitle quality and fallback decisions sometimes require semantic judgment.
- Generate a bounded decision shape for the agent and let scripts own acquisition evidence: selected because each responsibility has a verifiable owner.

## Decision

Run Initialization creates the fixed source and work directories through the Scaffold Generator. Before the Source Acquisition Agent starts, `source-prepare` verifies that scaffold and creates `work/source-acquisition/task.json` plus `work/source-acquisition/decision.skeleton.json`. These files contain the run identity, canonical source identity, allowed inputs and outputs, current acquisition policy, enumerated choices, and fields the agent may complete.

For the cold-start candidate, the public command owns this attachment boundary. After `init-cutover-candidate`, run the public `source-acquire` command against the candidate's existing `--run-dir`; it attaches source evidence to that same Run and must not create a second Run.

When no usable CC subtitle exists, `source-acquire` must stage Whisper output through the Kernel-issued Whisper Task/Attempt and promote the validated Attempt before `source_ready` becomes current.

The candidate workflow must never call `source-live-smoke`; no second Run may be created for source acquisition.

An expired or rejected Cookie is a recoverable `user_input` Source Blocker: preserve the same Run and its evidence, do not count it as a delivery attempt failure, and immediately request a refreshed Cookie from the user.

After receiving the refreshed Cookie, close the source circuit breaker, run `source-blocker-resolve`, and retry `source-acquire` on the same Run with a new `source_epoch`.

The Cookie path and Cookie contents are credential-bearing secrets and must never appear in logs, reports, shared evidence, or task prompts.

If acquisition is interrupted after terminal proof persistence and before Resource Lease release, run `source-acquire-reconcile --run-dir <candidate-run-dir>`.

`source-acquire-reconcile` reloads the persisted terminal proof, releases the existing Lease, and advances or retries the interrupted Task on the same Run; it must not initialize or attach another Run.

The source boundary participates in the sequence `PREPARED` -> `INITIALIZED` -> `source_ready` -> `ready_for_delivery` with a provider-current passing Acceptance Report v2 -> `PROVISIONAL` -> `accepted` -> fresh current Delivery Guard -> `delivered` -> published Slice 12 Exit Evidence -> `CONFIRMED`.

The Source Acquisition Agent may provide only the bounded Source Acquisition Decision, including subtitle-track selection, Whisper fallback rationale, and explicit known gaps. It does not create directory names, canonical filenames, Source Manifest structure, hashes, media probe results, or workflow checkpoint state.

Video Platform Adapter scripts perform platform download operations, canonical naming, conversion where required, and technical probing. `source-finalize` computes fingerprints, validates the decision against its skeleton and schema, writes the complete `source/manifest.json`, and records fresh Source Manifest evidence for the `source_ready` Workflow Checkpoint. Invalid or unauthorized structural changes fail closed.

After `source_ready`, the `source/` tree is read-only to Outline, Writer, Figure, Consistency, Independent Review, and Acceptance Reviewer agents. Any later source mutation requires Source Reopen. That operation preserves the earlier acquisition evidence, reactivates source preparation, and invalidates every dependent checkpoint before new source work begins.

An expired or rejected platform cookie remains a user-input blocker. The adapter records the failure and the workflow waits for a refreshed cookie instead of changing authentication strategy automatically.

## Consequences

The data-preparation subagent retains the semantic choices that require inspection, while scripts control paths, manifests, fingerprints, and state transitions. Downstream agents receive one technically validated and immutable source package. Additional task-envelope standardization across other subagent roles remains a follow-up decision.
