# Default to fresh source acquisition with explicit verified import

A `source_only` version may be rebuilt from original video evidence without consuming an earlier PDF. Re-downloading every artifact is simple and isolated but can repeat expensive network and transcription work. Reusing a prior package without complete validation can silently inherit stale, partial, or mismatched source evidence.

## Considered Options

- Always download every source artifact again: safe for isolation but unnecessarily expensive when an identical validated package already exists.
- Automatically reuse the newest package for a matching URL: rejected because URL and recency do not prove canonical identity, completeness, current policy compliance, or user intent.
- Reference files directly from a prior run: rejected because later moves or mutations would break the receiving run's self-contained evidence.
- Default to fresh acquisition and allow an explicit, validated, run-local import: selected because reuse remains intentional, verifiable, and isolated.

## Decision

Every run declares one Source Acquisition Mode. `fresh_download` is the default and assigns the Source Acquisition Agent a complete platform acquisition task. `verified_import` is enabled only through an explicit prior Video Output Directory supplied by the user or coordinator.

Verified Source Import is performed by kernel and adapter scripts. Before copying, they validate canonical platform and item identity, Source Manifest schema compatibility, required artifact fingerprints, subtitle-language policy, media technical properties, and current source-quality requirements. Only original-source artifacts may be imported: platform metadata, video, audio, subtitles or transcription, cover material, and their provenance. PDFs, TeX sources, outlines, section drafts, figures derived for the article, and review reports are excluded.

The receiving run gets its own complete `source/` tree and a freshly finalized Source Manifest. The first implementation uses deterministic copies. It does not use shared mutable paths, hard links, or an external blob cache. If any required import invariant fails, the import operation records machine-readable failure evidence and returns the run to a complete `fresh_download` acquisition task for the Source Acquisition Agent.

## Consequences

`source_only` describes the evidence basis independently from network behavior. New runs remain self-contained, while explicitly requested reuse can save download or transcription work. Disk use may increase because imported packages are copied. Shared content-addressed storage remains a future optimization that requires a separate lifecycle and immutability design.
