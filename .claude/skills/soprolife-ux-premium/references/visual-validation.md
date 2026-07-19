# Visual validation checklist

Use synthetic records with intentionally long labels and values. Do not capture
or expose real people, clinical records, private identifiers, API endpoints, or
credentials.

## Before browser validation

- Run syntax and repository tests relevant to the touched files. For M15
  JavaScript, use:

  ```bash
  node --check painel-soprolife/js/m15-nucleo.js
  python3 painel-soprolife/scripts/test_command_center_m15_proxy.py
  ```

- Serve M15 through its same-origin proxy, not a generic static server:

  ```bash
  python3 painel-soprolife/scripts/command-center-local-server.py
  ```

- Keep visual fixtures synthetic and clearly non-production.

## Firefox and Chromium

Check both engines at representative wide, medium, and narrow widths. Also
check browser zoom at 200% for the edited flow.

- No horizontal page overflow, component overlap, clipped focus ring, clipped
  tooltip, or unreadable wrapped chip.
- Grids collapse in a deliberate order; primary content remains first.
- Cards and equivalent controls share alignment, spacing, height, border,
  radius, and state treatment where appropriate.
- Long content wraps without hiding identifiers, dates, statuses, or actions.
- Loading, empty, error, success, disabled, and permission-limited states remain
  understandable and stable.

## Forms and keyboard

- Traverse the entire edited flow with keyboard only. Focus order follows the
  visual order and every interactive control has a visible focus indicator.
- Labels programmatically identify controls; required fields and errors are
  announced and do not rely on color alone.
- Submission has one obvious primary action, prevents accidental duplicates,
  preserves valid input after an error, and moves focus to useful feedback.
- Touch targets and compact layouts remain usable without sacrificing the
  BlueDox-inspired operational density.

## Dates and calendars

- Show visible dates in the appropriate Brazilian format without changing the
  API/storage value or timezone contract.
- Test day, month-only, year-only, unknown, and assumed-day display when the
  screen supports them. Mark reduced precision explicitly.
- Confirm keyboard access, focus movement, labels, selected/today distinction,
  invalid-date feedback, narrow layout, and locale-specific ordering.
- Do not infer a calendar implementation from unmerged M15.4A work. Validate
  only behavior present in the current stable branch and the requested diff.

## Evidence to report

Record browsers, viewport widths, zoom, synthetic scenarios, keyboard path,
automated commands, results, and remaining limitations. Do not add screenshots
or reports containing private data to Git.
