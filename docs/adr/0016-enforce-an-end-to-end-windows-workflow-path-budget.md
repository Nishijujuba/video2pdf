# Enforce an end-to-end Windows workflow path budget

The project runs on Windows and invokes tools whose path behavior is more restrictive than Python's long-path support. Prompt guidance currently says to shorten long titles without defining a measurable limit, while later acceptance, compile, and archive paths add substantial fixed depth after the Video Output Directory is created.

## Considered Options

- Rely on Windows long-path prefixes: rejected because external tools and subprocess working directories do not share one long-path contract.
- Shorten paths only after a tool fails: rejected because recorded artifact bindings and populated directories may already depend on the unsafe path.
- Apply one title-character limit without considering descendants: rejected because final safety depends on the complete absolute artifact path.

## Decision

Every path governed by a new Video Workflow Run must remain at or below 240 UTF-16 code units. The Video Output Directory name is capped at 96 UTF-16 code units including its timestamp. Run Initialization computes the complete path budget against the longest reserved descendant path in the active scaffold version before creating the directory.

When normalized title text must be shortened, the name uses `{title-prefix}_{stable-8-character-hash}_{yyyyMMdd_HHmmss}`. The full timestamp and Stable Truncation Hash are never truncated. The hash is derived deterministically from the platform, canonical platform item identity, and original title. Full original titles remain in platform metadata and the Video Workflow Run Record.

Every other generated path component is budgeted against its actual parent and the 240-unit absolute limit. The kernel also enforces the Windows component limit, reserved device names, forbidden trailing characters, and the project normalization whitelist. Final PDF names are shortened from their article title only when the remaining absolute budget requires it and retain a Stable Truncation Hash.

Source acquisition stores platform-title-independent canonical names such as `video.<ext>`, `audio.<ext>`, `cover.<ext>`, and `subtitle.<language>.<ext>`. Scaffold and generator registries must bound their own filenames and reserved descendant depths. Short launch aliases in the LaTeX wrapper remain defensive execution support and do not make an over-budget workflow path valid.

## Consequences

Path failure becomes an initialization error instead of a late compile or acceptance failure. Human-readable names remain available within a predictable budget, and truncated names remain traceable to source identity. Collision handling, same-run resume behavior, scaffold-version budget tests, and legacy over-budget paths require follow-up decisions.
