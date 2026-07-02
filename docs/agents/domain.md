# Domain Docs

This repo uses a single-context domain-doc layout.

## Before Exploring, Read These

- `CONTEXT.md` at the repo root
- Relevant ADRs under `docs/adr/`

If any of these files are missing, proceed silently. Domain docs are created lazily when terms or decisions are resolved.

## File Structure

```text
/
|-- CONTEXT.md
|-- docs/adr/
`-- src/
```

## Use The Glossary's Vocabulary

When output names a domain concept, use the term as defined in `CONTEXT.md`.

If the concept is missing from the glossary, treat that as a domain-modeling signal: either the wording is drifting from project language, or the glossary needs expansion.

## Flag ADR Conflicts

If output contradicts an existing ADR, surface the conflict explicitly and name the ADR.
