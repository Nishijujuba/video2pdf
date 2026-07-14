# Domain Docs

This repo uses a multi-context domain-doc layout. `CONTEXT-MAP.md` is the routing and relationship authority; each active glossary under `docs/contexts/` owns only its context's terminology.

## Before Exploring, Read These

1. Read `CONTEXT-MAP.md` at the repo root.
2. Read only the relevant `docs/contexts/<context>/CONTEXT.md` files named by the map.
3. Read relevant ADRs under the global `docs/adr/` ledger.

Archived context files under `docs/archive/` are historical evidence. They do not authorize current terminology, planning, or runtime behavior.

## File Structure

```text
/
|-- CONTEXT-MAP.md
`-- docs/
    |-- contexts/
    |   |-- project-governance/CONTEXT.md
    |   |-- video-workflow/CONTEXT.md
    |   |-- pyramid-evaluation/CONTEXT.md
    |   |-- final-acceptance/CONTEXT.md
    |   `-- legacy-workspace-maintenance/CONTEXT.md
    |-- archive/
    `-- adr/
```

## Use Context-Owned Vocabulary

Every canonical term has exactly one owning context. Consumers use the owner's term and follow the Published Language relationship recorded in `CONTEXT-MAP.md`; they do not redefine the term locally.

If a concept is missing, treat that as a domain-modeling signal: the wording is drifting, an existing owner must be identified, or one context glossary needs a new concise definition.

## Keep Runtime Status Separate

Context glossaries define stable language and boundaries. Current component activation status lives in `docs/adr/video-workflow-kernel-2.0-decision-map.md`; target-design vocabulary does not activate executable behavior.

## Flag ADR Conflicts

If output contradicts an existing ADR, surface the conflict explicitly and name the ADR.
