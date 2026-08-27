# PROTOTYPE ONLY: simplified Workflow 2.0 startup contract

This throwaway prototype answers one question from [Prototype the simplified
Workflow 2.0 startup contract](https://github.com/Nishijujuba/video2pdf/issues/79):
can one public CLI command plus project-local JSON start an ordinary Bilibili or
YouTube Kernel Run without replaying historical Exit Evidence, while retaining
live Control Store coordination and the complete final-quality route?

Run it from the repository root:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B prototypes\issue_79_startup_contract\tui.py
```

The prototype is in-memory and writes no Run, Control Store, or delivery state.
Each numbered scenario evaluates the same proposed public seam:

```powershell
python scripts\video_workflow.py start-run `
  --project-config config\workflow-project.v1.json `
  --platform bilibili `
  --source-url "<url>" `
  --session-id "<session-id>"
```

## Candidate interface

`start-run` owns this ordered operation:

1. Load the project configuration and its repository-owned Workflow Release
   Profile.
2. Validate Profile structure, contract compatibility, and requested-platform
   activation. This step reads no historical Exit Evidence.
3. Open the existing Control Store, or initialize it only for a provably
   pristine workspace.
4. Run the existing Bootstrap Probe.
5. Atomically claim the output path and initialize the Run through the live
   Control Store.
6. Bind the Run to the mandatory quality lifecycle. The final PDF remains
   undeliverable until compile provenance, Acceptance Report v2, every-page
   visual review, and Delivery Guard pass.

The command returns one machine-readable result. A failed check leaves no new
Run. It does not expose release publication, historical audit, authority
refresh, candidate cutover, or Legacy fallback switches.

## Why this shape

Three shapes were considered:

- Keeping `workflow-policy-check`, `bootstrap-probe`, and `init-run` as an
  operator-orchestrated sequence leaves ordering and duplicate admission calls
  in the public interface.
- Moving the whole request into JSON would mix stable project policy with
  invocation-specific source URLs, credentials, and session identity.
- One `start-run` command with stable project JSON and explicit invocation
  arguments gives callers a small interface while the module owns ordering,
  failure atomicity, and the release/runtime/quality separation.

## Verdict

The user approved the third shape on 2026-08-27: one deep `start-run` interface,
stable project configuration in JSON, and invocation-specific source and
session inputs on the CLI.

The prototype directory includes both JSON shapes. In production,
`workflow-project.v1.json` would be the operator-edited project configuration,
while `workflow-release-profile.v1.json` would be repository-owned and changed
only by release publication. Neither file contains a historical evidence path.
