# Run consistency and source-faithfulness as parallel assurance adapters

Consistency review and the current Independent Review both evaluate the integrated draft before final acceptance, but they require different evidence. Consistency checks cross-document coherence and terminology. Independent Review compares the draft with subtitles and source evidence for completeness and fidelity. Running them sequentially adds latency, while combining their judgments in one agent widens context and obscures authority.

## Considered Options

- Keep the two reviews as unrelated coordinator steps: rejected because shared generation binding, retry, and readiness logic would remain prompt-owned.
- Merge both judgments into one reviewer: rejected because glossary and cross-chapter analysis would compete with full source comparison in one context.
- Replace both with Text Acceptance: rejected because final acceptance has a final-artifact-only input policy and cannot inspect source transcripts or upstream contracts.
- Place two isolated Adapters behind one deep orchestration Module: selected because common mechanics gain one owner while semantic responsibilities remain separate.

## Decision

The Content Assurance Module exposes `content-assurance-prepare(run)` and `content-assurance-materialize(run)`. It starts only after integrated `main.tex` passes Main Pyramid and a guarded diagnostic draft PDF has been compiled from a current Compile Manifest.

`content-assurance-prepare` creates two isolated Subagent Task Envelopes bound to the same Outline, section, figure, `main.tex`, and draft PDF Artifact Generations. The Consistency Reviewer reads the Outline Contract, all sections, `main.tex`, Delivery Glossary where applicable, Figure Manifests, and cross-reference evidence. It evaluates terminology, notation, duplicate definitions, transitions, glossary strategy, figure-slot usage, and chapter coherence.

The Source-Faithfulness Reviewer is the canonical name for the role previously called Independent Review. It reads the Source Manifest, subtitles or Whisper transcript, platform metadata, figure source provenance, integrated TeX, and draft PDF. It evaluates source coverage, important omissions, unsupported additions, subtle factual or terminology drift, and missing evidence. It does not read writer notes, generation chat, or the Consistency Judgment Patch.

Both Reviewers run concurrently and submit bounded Judgment Patches. Provider scripts materialize separate authoritative reports at `review/consistency/report.json` and `review/independent/report.json`. The legacy directory name `review/independent/` remains the canonical artifact path while the role terminology becomes Source-Faithfulness. Neither reviewer reads the other's patch.

`content-assurance-materialize` validates both reports against the common input generations and computes the Content Assurance Checkpoint. `content_assurance_ready` requires both reports to pass and remain fresh. A semantic failure in one review does not cancel the other; all findings are collected before repair planning. A technical failure may retry only the affected Adapter while the shared inputs remain current.

When either report fails, `content-assurance-materialize` produces the Content Assurance Failure Set defined by ADR 0052 after both reports finish. Deterministic repairs route to the earliest affected production or integration checkpoint. A change to section content, terminology, figures, source expression, or integrated TeX reruns the applicable Section and Main Pyramid gates plus a new guarded diagnostic compile before both Content Assurance Adapters review the new integrated generation. Final Artifact Sealing under ADR 0053 starts only after both reports pass. Final Acceptance remains isolated and cannot read Content Assurance reports or source transcripts. Delivery Guard proves mechanical freshness and does not replace either semantic report.

## Consequences

Draft assurance wall-clock time approaches the slower reviewer instead of their sum. Each semantic context stays focused and each report keeps independent evidence. The coordinator learns two operations rather than two bespoke procedures. Existing Independent Review wording, Markdown-only checks, batch reconciliation, schemas, prompts, and tests must migrate to the Source-Faithfulness contract while retaining the established review path.
