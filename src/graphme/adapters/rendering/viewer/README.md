# OOXML viewer bundle

The browser renderer draws DOCX, PPTX and XLSX with [office-open-xml-viewer][upstream] —
a Rust/WASM OOXML parser plus a Canvas renderer — so no Office install is involved and a
worksheet renders whole instead of being paginated onto A4 sheets.

## Setup

    src/graphme/adapters/rendering/viewer/setup.sh

Writes the bundle to `~/.cache/graphme/ooxml-viewer` (override with an argument or
`GRAPHME_VIEWER_DIR`).

Compiling the patched xlsx parser needs Rust with the wasm32 target:

    rustup target add wasm32-unknown-unknown

If `rust-lld` aborts with `Library not loaded: @rpath/libLLVM.dylib`, the toolchain is
missing a link the linker expects; point it at the copy rustup did install:

    T=~/.rustup/toolchains/stable-aarch64-apple-darwin
    ln -sfn "$T/lib/libLLVM.dylib" "$T/lib/rustlib/aarch64-apple-darwin/lib/libLLVM.dylib"

| Part | Source | Why |
| --- | --- | --- |
| `dist/` | published npm build + `dist-patches/` | Ships the compiled WASM parsers; used as shipped except where it breaks on a real document. |
| `xlsx/` | pinned commit + `patches/` | XLSX needs the patches below, so it is compiled here. |

## What the patches change

`0001-drawing-rotation-and-grid-geometry.patch`:

| File | Why |
| --- | --- |
| `parser/src/drawing.rs`, `parser/src/types.rs` | Upstream reads a shape's `rot`, `flipH` and `flipV` but never passes them to the rendered drawing, so rotated shapes render unrotated. Also adds paragraph spacing (`spcBef`/`spcAft`) and field text (`fld`), without which text inside shapes drifts vertically. |
| `src/renderer.ts` | Applies the rotation and flips when drawing. |
| `src/internal/grid-axis-geometry.ts` | Exposes the axis helpers that size a pixel margin in real column widths. |
| `src/index.ts` | Exports `getGridGeometryForWorksheet` and the header sizes, so a cell range can be mapped onto the rendered raster without re-deriving column widths. |

`0002-combo-chart-secondary-category-axis.patch` -- a column+line combo with the columns on the
secondary axis painted as `Unsupported chart`:

| File | Why |
| --- | --- |
| `ooxml-common/src/chart.rs` | Office lists that combo's deleted secondary category axis at the bottom beside the primary one. Two category axes on one side were left unresolved, which sends the whole chart to the fallback. Each one crosses exactly one of the (already distinct) primary and secondary value axes, and that `crossAx` pairing now resolves them. |
| `core/src/chart/plot-groups.ts` | The column+line painter took only groups on the primary category axis. A deleted secondary category axis has no layout of its own, so a group on it is painted against the primary categories. |

`0003-smoothed-line-axis-covers-the-drawn-curve.patch` -- `core/src/chart/smooth-extent.ts`:
a smoothed line (`<c:smooth val="1"/>`) is a Bézier through its points and overshoots them, so
a run leaving a flat stretch of zeros dips below zero. Excel scales the automatic value axis to
that drawn curve, which is how an all-positive series gets a -1 minimum; the viewer scaled to
the points only. Line charts and the line overlay of a combo now include the curve's extent.

Only the XLSX bundle is compiled here, so the fix reaches XLSX; DOCX and PPTX charts come from
the published build.

`0004-emf-16-bit-dib-pictures.patch` -- `core/src/image/dib.ts`: the same 16-bit metafile
bitmap the dist patch below fixes. Both builds carry their own copy of the decoder, so the
source patch is what reaches XLSX and the dist patch is what reaches DOCX and PPTX.

## What the dist patches change

Each is a diff against the exact npm version in `UPSTREAM`, applied by `setup.sh` before the
build is copied into the bundle.

| Patch | Why |
| --- | --- |
| `0001-docx-metric-only-segments-do-not-merge.patch` | A paragraph that only hosts an anchored drawing gets an empty, height-only segment with no script. Sizing a table cell merges adjacent text segments by fingerprint, and fingerprinting that segment threw `Cannot fingerprint undefined`, failing the whole document; merged, it would also have carried its height-only flag onto real text. Height-only segments are no longer merged. |
| `0002-emf-16-bit-dib-pictures.patch` | A picture pasted as a metafile keeps its photographs as device-independent bitmaps inside the EMF. The DIB decoder reads 32, 24, 8, 4 and 1 bits per pixel and returns nothing for 16 — the depth a screen capture pasted from a 16-bit display carries — so the metafile drew its frames, arrows and circles and left every photograph out, with no error anywhere. 16-bit `BI_RGB` is RGB555; it is decoded like the others. |

The published build carries the metafile player twice: once in the chunk the viewers import
on the main thread, which is what draws a document here, and once inside
`dist/assets/render-worker-*.js` for a rendering mode this renderer does not use. The patch
changes the first. If a viewer ever draws through the worker instead, the same 16-bit branch
has to reach that copy too — the symptom is a metafile that draws its lines and not its
photographs.

`UPSTREAM` pins both the commit and the npm version. To move forward, change them,
re-run setup, and fix any patch rejects.

[upstream]: https://github.com/yukiyokotani/office-open-xml-viewer
