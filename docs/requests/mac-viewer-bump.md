# Request: move the OOXML viewer forward (Mac)

**For:** whoever works on the Mac checkout of `Graph-Me-Maybe`.
**Why the Mac:** rebuilding the viewer needs Rust and wasm-pack, which the Windows PC does not have.
**Branch:** `q-folder` (pull first; 0.1.72 is the latest release).

## Goal

Move the viewer from `@silurus/ooxml` 0.86.1 to **0.89.11**, the newest tag. Every tag from 0.89.8
on holds upstream PR #1991 (merge `bd1c008`: our #1983 smoothed-line axis and #1984 combo chart on
a hidden secondary category axis) and `d7f60b865` (picture anchors). Then drop our own workarounds
for those fixes. Every format must still render as before.

## Steps

1. **Tools.** `rustup` (stable) with the `wasm32-unknown-unknown` target, and `wasm-pack`
   (`cargo install wasm-pack` or the installer). Node 24. If `rust-lld` aborts with
   `Library not loaded: @rpath/libLLVM.dylib`, see the symlink fix in
   `src/graphme/adapters/rendering/viewer/README.md`.
2. **Pin.** In `src/graphme/adapters/rendering/viewer/UPSTREAM`:
   - `commit` = the commit of tag `v0.89.11` in
     `https://github.com/yukiyokotani/office-open-xml-viewer`
   - `npm_version=0.89.11`
   - Leave `hwp_*` as it is.
3. **Patches now upstream.** Delete them and their counterparts:
   - `viewer/patches/0002-combo-chart-secondary-category-axis.patch`
   - `viewer/patches/0003-smoothed-line-axis-covers-the-drawn-curve.patch`
   - `viewer/dist-patches/0003-smoothed-line-axis-covers-the-drawn-curve.patch`
4. **The remaining patches.** For each of the following, check that it still applies with no fuzz
   and is still needed. If upstream now does the same thing, remove the patch.
   - `patches/0001`, `0004`, `0005`
   - `dist-patches/0001`, `0002`, `0004`
5. **Our workarounds.** Remove `src/graphme/adapters/rendering/chart_axes.py` and
   `picture_anchors.py` together with their callers, so the viewer's own output is used. Keep any
   test that pinned the behaviour, and point it at the viewer's output.
6. **Build.** Run `src/graphme/adapters/rendering/viewer/setup.sh <dir>`, or with no argument to use
   `GRAPHME_VIEWER_DIR`.
7. **Check.**
   - `uv run pytest tests/public -q`, then the local suite `uv run pytest -q`.
   - `uv run ruff check .` and `uv run mypy`.
   - Write the invented samples (`python -m graphme.samples /tmp/s`) and build them in the dev app.
   - Look at each sample's pages beside the 0.86.1 rendering: DOCX, PPTX, XLSX (charts with a
     smoothed line and a combo chart especially), and an HWP/HWPX file (its bundle is
     separate, but it must still load).
   - Pass: no page lost, no chart axis or picture moved.
8. **Commit** as `Graph Me Maybe <graphme365@gmail.com>`:
   - one commit for the pin and patches, one for the removed workarounds;
   - a CHANGELOG line under Unreleased;
   - in `docs/roadmap.md` §7, tick the viewer item.
   - Do not push or release. Say when it is in and the Windows side takes it from there.

## If something does not go as planned

- A patch that no longer applies but is still needed: rebase it onto 0.89.11 and say so in the
  commit.
- A sample that renders differently: stop and send a screenshot of the old and new pages side by
  side, instead of adjusting our code to the new output.
