# Derive Final Editable TeX Source Sets from Compile Evidence

## Context

Final Compile already records an exact recorder dependency closure, while delivery language commonly treats `main.tex` as the complete editable source. Split documents, nested includes, and generated TeX snippets make that assumption incomplete. Directory-wide `.tex` enumeration would include unused drafts and unrelated files and therefore cannot establish authority.

## Decision

Video Workflow owns a generation-bound `final-editable-tex-source-set/1.0.0` artifact published beside the unchanged Delivery Quality `final-compile-report/1.0.0`.

- One Final PDF binds exactly one source set and exactly one TeX Entry Point.
- Recorder-observed, project-local `.tex` inputs determine membership. Compile Manifest entries provide current path, logical generation, fingerprint, and size identity.
- The artifact carries its resolved project root; every member path must be a unique `.tex` path within that boundary, and logical identities must also be unique.
- Other consumed TeX members use the `included_tex_source` role, including nested and generated snippets.
- Registered runtime `.tex` dependencies remain runtime inputs and do not become project-editable members.
- Non-TeX dependencies remain in the Final Compile Input Set and outside the editable source set.
- Missing, incomplete, ambiguous, stale, or contradictory evidence fails at a stable source-set gate before report publication.
- Kernel and run-record-free Legacy tracks call the same provider and projection contract. Input-track admission does not alter source-set semantics.

Three publication alternatives were evaluated: adding a required field to report v1, publishing report v2, and publishing an independent Video Workflow artifact. Mutating v1 violates explicit version compatibility. Report v2 would assign a Video Workflow-owned lifecycle to Delivery Quality. The independent artifact preserves the active report contract and gives source discovery, validation, error policy, and lifecycle one honest owner.

For membership discovery, directory enumeration, parsing TeX directives, and recorder projection were compared. Directory enumeration cannot exclude stale files. Directive parsing cannot fully reproduce compiler resolution. Recorder projection has the strongest evidence because it observes the current compile generation; the Compile Manifest supplies the generation identity that recorder paths alone lacks. The public Final Compile request's explicit `tex_entrypoint_logical_id` is canonical; an exact monolithic `main.tex` remains a compatibility fallback when no explicit identity is supplied.

For multiple Final PDFs, separate output directories, compile-identity child directories, and explicit output naming were compared. Separate directories do not satisfy the same-directory workflow. Compile-identity child directories also change the visible output location. The public Final Compile boundary therefore accepts an explicit Final PDF name and output directory while retaining `final.pdf` and the existing workspace layout as defaults. Internal compile workspaces remain isolated, and each named PDF publishes its own evidence paths so closures cannot overwrite or cross-bind.

Named output stays inside the current video-level authority root (the Legacy video root, or the Kernel run boundary); one video cannot publish into another video's directory. Public artifacts are published only after every validation including the Legacy Global Gate re-check, with the Final Compile Report written last as the commit marker. The named publication is one package-level transaction: every artifact is first written to a private temp name inside the target directory, then all artifacts are renamed into place in deterministic order, and any failure moves that attempt's created files under the video-level `待删除/final-compile-publish-<stem>` rollback root. A failed compile therefore leaves no partial named output and a same-name retry starts from an empty target set.

## Consequences

Monolithic documents produce a one-member set. Split, nested, and generated-source workflows preserve exact editable closure without widening the Final Delivery Package to non-TeX assets. Each source-set identity changes when its PDF binding, generation set, membership, role, path, fingerprint, or size changes.

Final Acceptance, Acceptance Report, Allowed Artifact Manifest, reviewer read-set, and Delivery Guard changes remain outside this decision and consume this projection in later tickets. Historical output directories remain unchanged.

## Validator fixture migration impact

Source-set negative fixtures start from a complete positive graph, declare one recorder, entrypoint, identity, membership, or PDF-binding contradiction, rematerialize the dependent source-set fingerprint, preserve only the declared stale target node, and assert the first public gate and stable error code. Contract schema, examples, registry entry, projection tests, and standalone artifact publication change atomically.
