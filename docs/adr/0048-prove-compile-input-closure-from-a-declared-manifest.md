# Prove compile input closure from a declared manifest

The current compile wrapper recursively copies every file with an allowed extension from the source directory. That behavior can include unrelated drafts, stale figures, review artifacts, or hidden support files and cannot prove which committed generations produced the PDF. A purely static TeX parser is also incomplete because TeX allows macros, extension resolution, package loading, and generated auxiliary inputs.

## Considered Options

- Keep recursive extension-filtered copying: rejected because directory contents become an implicit and unstable compile contract.
- Derive the complete manifest only by statically parsing TeX: rejected because general TeX dependency resolution is dynamic.
- Compile against the original run directory and copy every recorder-discovered input afterward: rejected because the first compile can consume undeclared project files and silently enlarge authority.
- Stage a producer-declared allowlist and use recorder evidence to prove actual closure: selected because undeclared project files are absent from the compile environment and actual reads remain auditable.

## Decision

The Content Production Module creates a schema-valid Compile Manifest from committed Artifact Generations. Every project entry records at least:

- logical artifact identity, generation, SHA-256, size, and producer identity;
- canonical run-relative source path and unique staging-relative destination;
- role such as entry TeX, section TeX, figure, bibliography, local class/package, local font, or declared support file;
- media type and required/optional status;
- the compile-runtime policy and dependency-discovery policy versions.

The manifest identifies exactly one entrypoint and binds the Integration Manifest generation that produced it. Paths are normalized, case-fold collision checked, resolved inside the Video Output Directory, and rejected when they are absolute, escape through `..`, traverse a link or junction outside the run, collide in ASCII staging, or target an unregistered generation.

Guarded compilation follows this sequence:

1. validate the manifest schema, artifact fingerprints, path boundaries, entrypoint, and declared staging destinations;
2. run a bounded static preflight that finds obvious `\input`, `\include`, graphics, bibliography, local package, font, and external-tool references and rejects known absolute paths, traversal, shell escape, and undeclared direct references;
3. copy only manifest entries into a fresh attempt-scoped ASCII staging directory;
4. invoke XeLaTeX with shell escape and automatic package installation disabled and recorder output enabled;
5. parse every recorder `.fls` input after each pass and normalize its real path;
6. classify each input as a fingerprinted manifest entry, a known auxiliary file generated inside the current compile attempt, or a dependency allowed by the registered MiKTeX/font runtime policy;
7. fail closed with a Compile Dependency Gap for every unclassified, missing, escaped, stale, or unsupported input;
8. emit a LaTeX Compile Report that binds the Compile Manifest, recorder evidence, runtime identity, executed passes, generated outputs, and final PDF fingerprint.

Static preflight is diagnostic and preventive; it does not establish Compile Dependency Closure. Recorder evidence is required because successful TeX resolution is authoritative for actual reads. Recorder evidence also cannot add a project file to the manifest. A missing project input returns a gap to its owning producer or integration step, which must create and promote a new manifest generation before compilation retries.

Generated `.aux`, `.toc`, `.out`, `.fls`, logs, and other approved pass-local files live only inside the compile attempt's disposable build area. The runtime policy identifies allowed MiKTeX roots, package state, engine identity, and explicitly resolved system-font inputs. A broad operating-system directory is never accepted merely because it is a system path.

The first implementation disables shell escape. `minted`, SVG conversion, bibliography generation, and similar external tools require an explicitly registered provider step that produces committed inputs before the guarded compile. Unsupported dynamic dependencies block with a Compile Dependency Gap. Pre-rendered code listings, PDF/PNG figures, prepared bibliography outputs, and local support files remain usable when declared.

Recorder validation proves post-execution reads and is not a complete hostile-TeX security sandbox. Platform preflight, restricted staging, disabled shell escape, bounded runtime, and path validation reduce risk. Strong operating-system sandboxing remains a separate future security decision.

## Consequences

The delivered PDF has an exact project-input lineage and unrelated workspace files cannot enter through directory discovery. Producers must declare local support files explicitly. Some previously successful dynamic TeX projects will fail until their dependencies are modeled or pre-generated. Tests need fixtures for transitive input, extension-resolved graphics, local classes, bibliography, local and system fonts, generated auxiliaries, missing files, traversal, links, case collisions, shell escape, and recorder paths outside every allowed class.
