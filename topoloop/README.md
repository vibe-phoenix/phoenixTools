# Phoenix Topo Loop Preview 1.2.0 — Maya 2024–2027

Hold **Shift** in polygon vertex / edge / face mode to see where loops (**blue**) and rings (**lavender**) travel. Release Shift and it's gone. Selection, undo and the mesh are never touched.

## Files

| File | Purpose |
|---|---|
| `phoenix_topo_core.py` | Pure-Python loop/ring algorithms + shared draw slot (no Maya imports) |
| `phoenix_topo_preview.py` | Controller: Shift watcher, hover raycast, caches, settings window, public API |
| `phoenixTopoPreview.py` | Viewport 2.0 plugin: locator + `MPxDrawOverride` drawing with `MUIDrawManager` |
| `install_phoenix_topo_preview.py` | Drag-and-drop installer |
| `test_phoenix_topo_core.py` | Topology unit tests (plain Python) |
| `test_smoke_headless.py` | Controller/plugin smoke test on a stubbed Maya API (needs PySide6) |

## Install / update / repair

Unzip, then **drag `install_phoenix_topo_preview.py` into a Maya viewport**. Use the same step for every update or if anything stops working. It is a clean reinstall:

1. shuts the tool down and unloads its plugin,
2. deletes every old copy of the three tool files (plus `.old` copies and cached bytecode) from every folder on Maya's Python path, including version folders like `Documents/maya/2025/scripts`,
3. installs the new files into `Documents/maya/scripts`,
4. keeps one guarded autostart block in that folder's `userSetup.py` (only runs if *Start with Maya* is ticked) and removes old ones,
5. replaces the **TopoLoop** shelf button (Phoenix themed icon; click = toggle, double-click = settings, right-click = toggles),
6. imports the new code fresh and enables it. The Script Editor lists what was removed and where it loaded from.

Keep the unzipped folder outside `Documents/maya/scripts` (e.g. Downloads). The installer refuses a source folder with mixed versions. If an older plugin is still loaded in Maya it is unloaded and replaced automatically. If the three files ever come from different builds, Maya shows an error naming the stale file instead of silently drawing nothing; reinstall to fix.

Script calls: `toggle()`, `show_settings()`, `install_shelf_button()`, `enable()`, `disable()`, `unload_plugin()`, `reset_settings()`, `debug_hover()`.

**Uninstall:** `phoenix_topo_preview.unload_plugin()`, delete the three files from `Documents/maya/scripts`, the shelf button, and the block between the `# >>> phoenix_topo_preview autostart >>>` markers in that folder's `userSetup.py`.

## Use

1. Toggle on (shelf button).
2. Enter vertex, edge or face mode on one or more meshes.
3. Hold Shift. The seed is the component under the cursor (turn off *Shift mode: use hovered component* to always use the last selected one); if the cursor isn't over a mesh you can pick components on, it's your most recently selected component of the current type.
4. Shift + Ctrl/Alt hides the preview (Maya's add/toggle and camera combos stay clean); back to Shift-only shows it again.

## What is drawn

Two colours everywhere: **primary** (blue) and **secondary** (lavender). The seed component (hovered, or last selected) is drawn in the **selection colour** (amber).

| Mode | Primary | Secondary |
|---|---|---|
| **Vertex** | Direction 1 through the vertex: dots + line | Direction 2: dots + line |
| **Edge** | Edge loop | Edge ring (edges across neighbouring quads) |
| **Face** | Face loop 1: filled, outlined | Face loop 2: filled, outlined |

How directions are found (nothing is guessed; ambiguous paths stop):

- **Vertex:** the edges around the vertex are paired. A regular interior vertex (valence 4, one manifold fan) gives 2 straight directions; a border vertex gives the border line + the inner line; a corner gives its 2 edges. Poles and irregular vertices get one direction per edge, alternating colours around the fan. Each direction is the edge loop leaving the vertex.
- **Edge loop** continues through valence-4 interior vertices (opposite edge) and along borders through valence-3 border vertices; stops at poles, corners, non-manifold or lamina geometry. **Edge ring** crosses quads to the opposite edge; stops at triangles, n-gons, borders, non-manifold edges.
- **Face:** quads only; the two face loops (strips of quads) through the face, each stopping at non-quads, borders or non-manifold edges. Triangles / n-gons have no loops; only the seed face is highlighted.

## Settings

Double-click the shelf button. The panel uses the Phoenix retro style: **PREVIEW: ON/OFF** at the top, *Start with Maya*, then collapsible sections (Behaviour and Colours open, the rest closed). Colours are chips (click to pick), numbers are typed boxes (scroll or arrow keys nudge). Double-click the **TOPO LOOP** title for themes: 6 presets, 11 colour swatches and the shelf icon in each theme colour. Preview colours (loop / ring / seed) are separate from the panel theme.

- **Colours (all modes):** Primary, Secondary, Selection, *Draw the seed in the selection colour*, **Line thickness** (one slider for loop and ring).
- **Vertex / Edge / Face mode:** *Custom colours for this mode* overrides the global pair for that mode only. Also dot size, lines between dots (vertex), show ring, dashed ring spine (edge), fill opacity, centre spine (face).
- **General:** hover seeding in Shift mode, X-ray mode, max path steps. Stored in optionVars; *Reset defaults* restores everything.

## Behaviour toggles

Settings → *Behaviour* (also on the shelf button's right-click menu):

- **Hover preview** (on by default) — while you hold Shift, the seed is the component Maya is pre-highlighting under the cursor (read with `ls -preSelectHilite`, so it matches Maya's own picking exactly). If Maya isn't pre-highlighting anything (e.g. Preselection Highlight is off in Selection preferences) the tool falls back to its own ray cast, then to your last selected component. Off = Shift always uses the last selected component. Script: `phoenix_topo_preview.toggle_hover_preview()`.
- **Hide Maya's red hover highlight** (on by default) — Maya's pre-selection colour can't be changed, so while Shift is held the tool turns *Preselection Highlight* off and turns it back on when you release Shift (also on disable, and on the next start if Maya quit mid-hover). Hover seeding then uses the tool's own ray pick. The first time you hover, it checks the ray pick finds the component Maya highlights; if it can't, the red highlight is kept, hover keeps using Maya's highlight, and a warning asks you to run `debug_hover()`. Script: `phoenix_topo_preview.set_hide_maya_hover(False)`.
- **See selection** (on by default) — the overlay never draws over components you have selected, so Maya's own selection highlight (e.g. a partial loop you picked) stays visible. Edge mode skips selected edges, vertex mode skips selected vertex dots, face mode leaves selected faces and their edges unfilled. Script: `phoenix_topo_preview.toggle_see_selection()`.

## If hover doesn't pick anything

Run `phoenix_topo_preview.debug_hover()` in a Python tab, then within 3 seconds rest the cursor over a mesh in component mode. The Script Editor prints each step: panel under the pointer, widget/port sizes, the ray, the candidate meshes and whether each was hit. Pick errors are also reported once as a warning with the full traceback.

## Debug / screenshot mode

Settings → *Debug / screenshots* (or right-click the shelf button):

- **Always on** — preview shows without holding Shift and stays when Maya loses focus (screenshot tools, Snipping Tool, Win+Shift+S). The seed is your **last selected component** (the mouse is ignored), so the screenshot shows exactly what you picked. Persists until you turn it off.
- **Freeze** — locks the current preview exactly as drawn; mouse and selection changes are ignored until you unfreeze. Not saved between sessions.

Script: `phoenix_topo_preview.toggle_always_on()`, `phoenix_topo_preview.toggle_freeze()`.

## Implementation notes and limitations (read before use)

- **Why a plugin node exists.** VP2 has no supported API to draw arbitrary 3D lines without a DAG node (alternatives are a full render override or deprecated OpenGL callbacks that break on DirectX). So the tool keeps **one** helper locator (`phoenixTopoPreview_helper`) while enabled. It is not geometry, created with `MDagModifier` outside the undo queue (no undo entries), non-selectable (reference display + non-selectable drawables), hidden in the Outliner, `doNotWrite` (never saved; also deleted in before-save/export callbacks), and doesn't leave the scene flagged modified. It's removed on disable and re-created lazily after File > New/Open.
- **Isolate Select:** works. Each time you press Shift (and when isolate, selection or panel settings change while it's held) the helper is added to every isolated panel's isolate set, with undo recording paused and the file-modified flag restored. Deleting the helper on disable / before save removes the membership, so the saved isolate set stays clean.
- **Hover** uses a ray cast against hilited meshes and meshes with selected components. It needs the cursor over a face; hovering just off a silhouette (where Maya can still preselect a vertex or edge) falls back to the selected component. Nearest vertex/edge is picked in object space on the hit face, which can differ slightly from Maya's screen-space preselection at grazing angles.
- **Multi-component mode** (vertex+edge+face on): the mode follows your last selected component's type, else edge.
- **X-ray resolution** uses the hovered panel (or focused panel); with several viewports showing different X-ray states, the hovered panel wins for all of them.
- **Shift while typing** in text fields is ignored. If Maya loses focus or a Shift release is missed, a 16 ms poll clears the preview.
- **Performance:** no whole-mesh scan ever. Topology is queried per component through `MFnMesh`/iterators and memoised per mesh; caches drop on topology change (`MPolyMessage`, plus a vertex/edge/face count check) and ray accelerators drop on geometry change (dirty-plug callback filtered to geometry attributes). Paths are capped by *Max path steps* (default 20 000).
- **Cleanup:** disable/unload/rerun removes the event filter, scene/event/per-mesh callbacks, ray accelerators, the helper node and draw payload. Re-running `enable()` or the installer tears down any previous instance first. `unloadPlugin` alone is refused by Maya while the helper exists; use `unload_plugin()`.

## Verification

**Tested here (outside Maya):**
- `test_phoenix_topo_core.py` — 27 tests, run twice (CSR topology and the lazy `CachedTopology` used in Maya), 54/54 pass: grids, borders/corners, cylinder and torus closed loops/rings, cube valence-3 corners, UV-sphere poles and triangle caps, all-triangle mesh, n-gon stops, 3-face non-manifold edge, bowtie vertex, Möbius strip termination, vertex direction pairing (interior / border / corner / pole), face loop groups, isolated/invalid indices, max-steps cap, 60 random fuzz meshes.
- Lazy benchmark on a 1 000 000-face grid (pure Python stand-in for API calls): full-width vertex preview ~50 ms cold, ~30 ms warm, edge ~20 ms, face ~10 ms, zero up-front build.
- `test_smoke_headless.py` — 110/110: real controller and plugin code on a stubbed Maya API with real PySide6 key events: Shift press/release, events never consumed, all three modes (two coloured directions / loop+ring / two coloured face loops), hover preview (Shift required, fallback to last selected, off = selection only), viewport detection via getPanel(underPointer) with HiDPI port mapping, closestIntersection call-form fallback, hover errors falling back to the selection seed, version mismatch detection, stale plugin in memory replaced automatically (cached plugin module purged before reload; "restart Maya once" message if Maya still refuses), hover seed from Maya pre-highlight (edge / vertex / face, wrong type ignored, late highlight caught by settle ticks), ray-cast fallback with and without accelerator, hiding Maya's hover highlight while Shift is held (validated ray pick, restore on release, kept on when the ray pick fails, crash recovery), see selection (edge / vertex / face), selection colour, global colours with per-mode override, single thickness, always-on (no Shift, focus loss, Shift release, scene change, seed = last selected in click order) and freeze, selection-order pref on/restore, isolate-set membership (no undo, no modified flag, no duplicates, re-add after reload), world transform, hover nearest edge/vertex, hover miss fallback, mode/selection mismatch → nothing, Ctrl/Alt hide/re-show, focus loss and missed-release cleanup, cache invalidation, depth priority order, X-ray wrapping, full cleanup on disable.
- Installer clean-reinstall logic on a fake Maya folder layout: 12/12 (mixed-version source folder refused, old copies in shared + version folders, `.old` files and bytecode removed, unrelated files and the download folder untouched, one autostart block).
- All files compile and pass `pyflakes`.

**Not tested (no Maya available here) — please verify in Maya and report exact errors:**
- Plugin load and VP2 drawing (line width, depth bias, occlusion, X-ray) on DirectX 11 and OpenGL Core.
- Real Maya API signatures used: `closestIntersection` tuple layout, `M3dView.viewToWorld`/`widget()`, `scheduleRefreshAllViews`, `MPolyMessage.addPolyTopologyChangedCallback`, `MFnDependencyNode.setDoNotWrite`, `MUIDrawManager.beginDrawable(kNonSelectable)`. Each has a guarded fallback, but behaviour should be confirmed.
- Preview drawing inside an isolated panel; helper node invisibility in Outliner/selection, no undo entries, no file-modified flag, not written on save.
- Real Maya Shift-select and hotkey behaviour alongside the filter, high-DPI cursor mapping, multiple viewports, Maya 2027 (assumed PySide6, unreleased at time of writing).

Quick in-Maya check: `polyPlane` + `polySphere` + `polyTorus`, enter each component mode, hold Shift over borders, poles, triangulated areas; Shift-click to add selection and confirm nothing changes; Ctrl+Z history should show no preview entries; save and confirm `phoenixTopoPreview` isn't in the .ma.
