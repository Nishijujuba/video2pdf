# RGB-family raster identity validation

Issue #117 fixes a false raster-binding rejection in an actual Final Compile.
The staged WSL/VM Figure and the PDF page 25 image have identical RGBA samples.
Both contain fully opaque alpha, but their color spaces are DeviceRGB and
CalRGB. Converting the already-RGB CalRGB image merely because alpha exists
changes its samples and causes an incorrect missing-raster decision.

The normalizer now converts only inputs that lack a color space or do not have
three color components. RGB-family samples retain the same treatment with and
without alpha. Alpha remains part of the hashed sample bytes. This removes the
unnecessary conversion without adding an opaque-alpha scan, dropping real
transparency, flattening an image onto a background, or matching approximate
colors. The existing non-RGB conversion path remains intact.

## Focused coverage

Two new exact methods exercise `render_and_derive` with a real source PNG,
embedded PDF image and soft mask. The CalRGB definition comes from the retained
actual PDF. A DeviceRGB positive baseline passes before the calibrated case.
The calibrated fixture verifies equal raw source/embedded samples, then requires
the adapter to derive a raster object and sealed-origin edge from actual PDF
membership. It does not provide an operator-authored Text Origin Plan.

The negative method first proves the calibrated positive baseline. Each subtest
then changes only one embedded color sample or alpha value while retaining the
declared source. The PDF and observed raster objects are rematerialized. The
expected first rejection is the declared source-to-PDF raster identity gate.
The existing local `AdapterError` interface lacks a structured error code; the
test temporarily asserts the smallest stable fragment, `declared raster text
is absent`, and records this interface gap.

## Dependency and migration impact

- Authority inputs: staged source PNG, its existing source binding, and the
  actual embedded PDF image/mask.
- Derived observations: normalized sample identity, rendered raster object,
  and its sealed-origin edge.
- Existing Production, inventory and manifest schemas, source-file hashes,
  page coverage, origin contracts and publication gates are unchanged.
- No shared fixture builder or historical snapshot is migrated. The tests use
  the narrow rendered-raster derivation seam; they do not claim to qualify the
  full public Final Compile provider.
- Only the two new exact methods and bounded read-only matching diagnosis run
  before the required fresh actual public Final Compile. The obsolete full and
  historical test collections remain excluded.

The actual compile remains a separate acceptance gate. Retained failure
artifacts are diagnostic inputs and cannot acquire passing compile authority
from the matching probe or unit results.
