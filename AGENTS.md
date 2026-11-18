# User interface preferences

- Follow the visual preferences recorded in [UI_TASARIM_TERCIHLERI.md](UI_TASARIM_TERCIHLERI.md).
- Keep dropdowns and buttons compact and sized to their content. Never stretch
  them to fill a row; put extra space in the layout instead. Preview case
  dropdowns should stay within 100–200 px and use the shared control height.
- Keep related action buttons equal in width and height while staying compact.
- In numeric and text fields, Enter confirms the field value. It must not open
  TRUBA, start analysis, close the workspace, or trigger an unrelated action.
  Disable implicit default/auto-default buttons in the main settings workspace.
- Open the TRUBA connection panel only through explicit activation of its button.
  Opening the panel must not itself start a connection.
- Preserve the established compact Datenburg interface and its shared control
  styling when fixing behavior. Consult `studio_style.py` and
  `assets/ui/datenburg.qss` before changing control appearance.
- Persist window size, position, maximized state, splitter and dock layouts per
  user and application for main and resizable secondary windows. Preserve normal
  bounds while maximized/minimized and keep restored windows on available screens
  after monitor or DPI changes. Do not override restored preferences with startup
  centering, fixed geometry or unconditional maximization. Verify persistence when
  implementing window changes.

# Repository

- Canonical remote: https://github.com/aserdargl/RustMech.git (remote name `origin`, branch `main`).
- Local working copy: `D:\MySoftware\RustMech`. Commit and push finished work there; never force-push.
- Stage only the files you changed (`git add <file>`), never `git add -A`: the working copy often holds the owner's uncommitted work.
- Do not commit generated or private data (see `.gitignore`: `DoEResults/`, `HumanResults/`, `_backup_*/`, build output, credentials).
