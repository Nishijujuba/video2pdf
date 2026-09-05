# ADR 0067: Refresh Compile Runtime through a governed successor

## Status

Accepted

## Context

A retained Kernel Run can keep valid source and reader-facing content while its
Compile Runtime Policy becomes stale. The previous public surface could consume
that policy, but it could neither derive a successor from observed runtime use
nor rebuild the run-specific Diagnostic and Final Compile bindings.

The historical package inventory included every file below the MiKTeX tree.
That captured mutable configuration and cache files which the recorder did not
identify as compile inputs. Four such files later drifted and blocked compilation.

## Decision

`compile-runtime-refresh` is the public, resumable recovery operation. It derives
the successor package inventory from the predecessor Diagnostic Compile recorder
closure, fingerprints each currently registered runtime input, and runs a fresh
Diagnostic Compile through Content Production before publishing successor
bindings. Newly observed runtime dependencies remain fail-closed.

The operation archives exact predecessor policy, manifest, report, PDF, and
Production State bytes below its retained operation directory. A journal blocks
Final Compile while publication is incomplete. Repeating the command with the
same Run and `--refreshed-at` resumes the same operation.

A current Precompile Report and Text Seal are reused byte-for-byte only after
their provider, reviewer commits, catalog, projections, semantic dependencies,
artifact generations, and reader-facing inventory validate as current. A stale
Precompile authority produces exact public preparation inputs for a fresh review.
The operation never creates a semantic judgment or Text Seal. Once a current Seal
is supplied, the provider derives a successor Final Compile Manifest in the
operation directory and leaves the predecessor manifest unchanged.

## Consequences

Runtime-only recovery preserves source, subtitle, figure, and reader-facing
content generations. Mutable files absent from recorder evidence do not enter the
successor inventory. Runtime files that the recorder actually consumed, including
the MiKTeX XeLaTeX format, retain exact identity. Acceptance v2, independent
every-page review, Delivery Guard, and delivery lifecycle remain required.
