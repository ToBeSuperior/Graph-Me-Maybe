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

`0001-drawing-rotation-and-grid-geometry.patch` -- what the pinned commit still lacks of what a
shape or a cell range needs (it draws a picture's rotation and flips and a paragraph's spacing
itself now):

| File | Why |
| --- | --- |
| `parser/src/drawing.rs` | A field (`<a:fld>`: a slide number, a date) is a run whose text the producer filled in; it was dropped, and the words of a shape holding one went missing. It is read like any run. |
| `src/renderer.ts` | A shape's outline was drawn one pixel per point at any scale, so at the reading's device pixel ratio it came out thinner than the file says. It is scaled with the drawing. |
| `src/internal/grid-axis-geometry.ts` | A scaled axis summed rounded bands, drifting by up to half a pixel a band; it rounds the cumulative boundary instead, so a cell range maps onto the raster where it drew. |
| `src/index.ts` | Exports `getGridGeometryForWorksheet` and the header sizes, so a cell range can be mapped onto the rendered raster without re-deriving column widths. |

The combo chart on a hidden secondary category axis (our upstream #1984), the smoothed line's
axis (#1983, both merged in `bd1c008`) and a one-cell picture with no `<a:xfrm>` (`d7f60b865`)
are drawn by the pinned version itself: our patches and the copies made before drawing for them
are gone.

`0004-emf-16-bit-dib-pictures.patch` -- `core/src/image/dib.ts`: the same 16-bit metafile
bitmap the dist patch below fixes. Both builds carry their own copy of the decoder, so the
source patch is what reaches XLSX and the dist patch is what reaches DOCX and PPTX.

`0005-emf-bitfields-dib-pictures.patch` -- `core/src/image/dib.ts`: the same `BI_BITFIELDS`
bitmaps as dist patch `0004` below, for XLSX.

## What the dist patches change

Each is a diff against the exact npm version in `UPSTREAM`, applied by `setup.sh` before the
build is copied into the bundle.

| Patch | Why |
| --- | --- |
| `0001-docx-metric-only-segments-do-not-merge.patch` | A paragraph that only hosts an anchored drawing gets an empty, height-only segment with no script. Sizing a table cell merges adjacent text segments by fingerprint, and fingerprinting that segment threw `Cannot fingerprint undefined`, failing the whole document; merged, it would also have carried its height-only flag onto real text. Height-only segments are no longer merged. |
| `0002-emf-16-bit-dib-pictures.patch` | A picture pasted as a metafile keeps its photographs as device-independent bitmaps inside the EMF. The DIB decoder reads 32, 24, 8, 4 and 1 bits per pixel and returns nothing for 16 — the depth a screen capture pasted from a 16-bit display carries — so the metafile drew its frames, arrows and circles and left every photograph out, with no error anywhere. 16-bit `BI_RGB` is RGB555; it is decoded like the others. |
| `0004-emf-bitfields-dib-pictures.patch` | A metafile bitmap may say where each colour sits with three masks (`BI_BITFIELDS`) instead of the fixed layout: RGB565 from a 16-bit screen, or 32 bits in another byte order. The decoder took only `BI_RGB`, so such a picture was dropped as silently as the 16-bit one. Both depths are read through their masks now, as are the masks a packed WMF bitmap puts between its header and its pixels. A JPEG or PNG inside a DIB (`BI_JPEG`, `BI_PNG`) is still not drawn: GDI takes those only on a printer, so a metafile drawn on a screen does not hold them, and decoding one needs the browser's asynchronous image decoder inside a player that draws synchronously. |

The published build carries the metafile player twice: once in the chunk the viewers import
on the main thread, which is what draws a document here, and once inside
`dist/assets/render-worker-*.js` for a rendering mode this renderer does not use. The patch
changes the first. If a viewer ever draws through the worker instead, the same bitmap branches
has to reach that copy too — the symptom is a metafile that draws its lines and not its
photographs.

`UPSTREAM` pins both the commit and the npm version. To move forward, change them,
re-run setup, and fix any patch rejects. Upstream builds with pnpm and its lockfile;
`setup.sh` fetches the pnpm the checkout names into its scratch folder, so nothing extra is
installed.

[upstream]: https://github.com/yukiyokotani/office-open-xml-viewer
