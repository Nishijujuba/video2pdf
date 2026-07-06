# Use session-scoped delivery targets for Final Delivery Guard

Final Delivery Guard will use a session-scoped active target instead of a project-level singleton target. Codex command hooks receive a `session_id` in the hook JSON input, so the Stop hook can guard only the delivery target owned by the current Codex session while still sharing video-level acceptance evidence inside the video output directory.

## Considered Options

- Project-level `.codex/delivery-targets/current.json`: rejected because concurrent Codex sessions can overwrite each other's active target and make one PDF delivery block or approve another session.
- `CODEX_THREAD_ID` as the target key: rejected because it is observable in this environment but is not the documented hook input contract. It may be stored as diagnostic metadata only.
- Stop hook scanning every active task: rejected because one session's failed delivery task would block unrelated sessions in the same project.
- Video-output-local active marker only: rejected because the Stop hook would need to scan the project to infer which video belongs to the current session.

## Consequences

The active target path is `.codex/delivery-targets/sessions/{session_id}/current.json`. The project-level `task-index.json` may support recovery, ownership checks, and observability, but it cannot be used by Stop hook as a blocking source. A video output directory remains the durable evidence boundary through `review/acceptance/delivery_target.json`, `acceptance_report.json`, rendered pages, and `delivery_guard_report.json`.
