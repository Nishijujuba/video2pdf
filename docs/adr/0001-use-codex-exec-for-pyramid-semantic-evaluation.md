# Use codex exec for Pyramid semantic evaluation

The Pyramid Principle validation skill will use `codex exec` as its first-version semantic evaluation backend instead of calling the OpenAI SDK directly. This lets the evaluator reuse the local Codex CLI authentication already available in the project and avoids creating a second API-key configuration path while the workflow is still being hardened.

The wrapper will run `codex exec` as a constrained evaluator: read-only sandbox, no approval prompts, hooks disabled, ephemeral session output, JSON-schema-constrained final output, and file-based report writing. This keeps the evaluator shaped like a deterministic quality gate around a semantic model judgment, rather than a general-purpose nested agent workflow.

The main rejected alternative is an OpenAI SDK backend. That would be lighter and more direct for CI or server automation, but it would require a separate dependency and credential path now. The project can revisit an SDK backend later after the `pyramid-principle-validate`, Bilibili render, YouTube render, and hook contracts have stabilized.
