# User interface preferences

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
