# Datenburg design system — RustMech native UI QA

final result: passed

## Scope and visual truth

The user requested Datenburg's buttons, dropdowns, steppers, menus and screen design throughout the existing RustMech desktop application. This is a native PySide6 production change, not a web prototype. RustMech retains its scientific workflows and actual VTK scene.

Sources inspected:
- D:/MySoftware/Datenburg/docs/ui-reference/current_main_window.png (1280 x 850).
- D:/MySoftware/RustMech/design/compact-datenburg/datenburg-controls-reference.png (300 x 850; Datenburg Plot Edit controls).
- Datenburg shell/ui_style_tokens.py, shell/widget_numeric_controls.py, shell/compact_gradient_button.py, shell/popup_style.py, and docs/standard-button-face-2026-11-06.md.
- The current main shell color #FFF7E3 comes from Datenburg's current UI rules; the older main screenshot predates that warm shell color.

## Comparison evidence

Full-view board: D:/MySoftware/RustMech/design/datenburg-system/full-comparison.png. Both apps are captured at 1280 x 850 native client pixels, DPR 1, without resampling. RustMech implementation: main-1280.png. The apps have different domain content: graph versus FE model; the comparison evaluates the shared control and panel language, not identical application content.

Focused board: D:/MySoftware/RustMech/design/datenburg-system/controls-comparison.png. Reference 300 x 850 and RustMech inspector 300 x 922, both retained at 1:1 density without stretching. The extra height is content, not scaled controls. Separate source and implementation were opened together, then the combined boards were inspected.

Additional implementation captures in D:/MySoftware/RustMech/design/datenburg-system/:
- main-1440.png: 1440 x 1024.
- main-1100.png: 1100 x 760; main-1000.png: 1000 x 700.
- analysis.png, material.png, experiments.png: native application pages.
- convergence.png: 1080 x 430, minimal default state.
- convergence-advanced.png: expanded settings and table remain visible.
- truba.png, dropdown.png, methods-menu.png: actual native auxiliary dialogs and popup windows.

There is no CSS viewport or browser console for this native application. Captures combine actual QWidget pixels with the live VTK framebuffer; no static mesh image substitutes for the preview.

## Findings and comparison history

- P2 resolved — old flat, 22 px action faces did not follow the reference. Replaced the cascading legacy overrides with one bundled theme: 25 px actions, 4 px corners, 1 px slate frames, pale vertical shading, dark blue primary/checked states, visible hover and keyboard focus. Post-fix controls.png, convergence.png and truba.png show consistent faces and uncut text.
- P2 resolved — dropdowns, numeric controls, tabs, popup menus and secondary dialogs had different surface and spacing rules. Applied Datenburg's gray input surface, centered numeric values, original blue chevrons, compact popup spacing, bordered tabs and neutral panel frames throughout. Post-fix analysis.png, dropdown.png, methods-menu.png and convergence-advanced.png were inspected.
- P2 resolved — the DoE table's local stylesheet bypassed the shared theme and initial header widths cut labels. Removed that override and sized columns from their captions. The revised experiments.png shows complete visible captions; further columns use normal horizontal scrolling.
- P2 resolved — the initial focused captures showed inconsistent sizing in newly-created convergence controls. Those dialogs now use the same theme and centered 25 px steppers as the main window. The post-fix convergence and TRUBA captures and UI checks confirm their sizing.

No actionable P0/P1/P2 findings remain in the requested design scope.

## Required fidelity surfaces

- Fonts/typography: Segoe UI, regular 13 px controls and 12 px dense table text. Small status text remains readable. Group headings are regular rather than oversized. Long solver options retain their full dropdown text and tooltips.
- Spacing/layout: 25 px controls, tight form gaps, thin panel borders, compact framed tabs and consistent action rows. The scientific viewport retains most of the window. At 1000 x 700, the expanded material form scrolls and the analysis action remains visible.
- Colors/tokens: gray #E5E7EB input faces, #9CA3AF input borders, #A6B8CA button frames, #3978BE focus/hover, #2D67B5-to-#1E4D8D primary face, #F1F3F5 panels, #F8FAFC work surfaces and current Datenburg warm shell. Disabled/pending/error/selected states remain distinct.
- Assets/image quality: original Datenburg blue chevron and checkbox SVG assets are bundled locally. Native Qt arrow controls and the existing scientific scene are retained. No raster mock, handmade replacement art or external runtime dependency was introduced.
- Copy/content: RustMech's existing engineering units, fields, solver descriptions, table data, convergence logic and connection controls are preserved. Product Design or implementation instructions do not appear in product UI.

## Verification

- tools/verify_precision_light.py --output design/datenburg-system: 35 native UI checks passed, recorded in verification.json and ui-verification.log.
- Actual integer arrow click, centered values, arrow-free dimension behavior, all display modes, camera presets/perspective, platens, material and analysis navigation, adding a design to DoE, three reduced window sizes and primary-action visibility passed.
- Minimal convergence state, advanced integer control sizing, actual dropdown/menu captures, and TRUBA button sizing passed. TRUBA remained disconnected.
- 25 convergence tests and 6 numeric-control tests passed. Source modules compile and the changed shared UI modules pass Ruff.
- No licensed solver or remote connection was launched.
- The Windows executable was rebuilt successfully. Packaged smoke test passed (exit 0): actual 8,040-element / 17,101-node mesh preview and splitter interactions verified; no licensed solve. See build.log and packaged-smoke.json. The packaged QSS hash matches the source and all three original SVG assets are present.
- Nine native Rust mesh tests passed during the build.
- Popup state comparison additionally used Datenburg docs/ui-reference/current_toolbar_open_menu.png alongside methods-menu.png; compact surface, border and row rhythm match the reference family.

## Follow-up polish

P3: Long contact formulation names use Qt's normal compact dropdown presentation; their full values remain available in the popup and tooltip. This preserves the requested dense inspector.


## Navigation row follow-up

final result: passed

User-directed layout: 3D Model, Experiments (DoE), a vertical divider, then Geometry / FE Mesh / Overlay together at the left of one row. Show platens and View occupy its far right. The previous extra viewport toolbar row is removed.

Source: D:/MySoftware/RustMech/design/datenburg-system/main-1440.png (before revision).
Implementation: D:/MySoftware/RustMech/design/navigation-row/main-1440.png, main-1280.png, main-1100.png and main-1000.png.
Focused comparison: D:/MySoftware/RustMech/design/navigation-row/navigation-comparison.png. Both source and implementation are native DPR 1 captures at 1440 x 1024; top regions are cropped at 1:1 without resampling. Font, control colors, original icon assets and product copy remain consistent with the approved Datenburg theme.

Resolved P2: display modes were isolated at the right of the workspace strip and Show platens occupied another row above the 3D area. One external, synchronized navigation strip now contains all these controls. The viewport and left setup tabs both start at client y=71, compared with y=98 and y=67 before the revision.

41 native UI checks passed, including actual workspace-tab clicks, return from DoE, synchronized selection, adjacent display controls, right-side platen controls and top alignment. The existing view-mode, platen, camera and smaller-window checks also passed. Evidence: design/navigation-row/verification.json. Source compilation and the verification script's Ruff check passed. Packaging verification is recorded in design/navigation-row-build.log and design/navigation-row/packaged-smoke.json.

No actionable P0/P1/P2 findings remain for this follow-up.
