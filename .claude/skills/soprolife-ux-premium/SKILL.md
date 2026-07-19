---
name: soprolife-ux-premium
description: Design, implement, or review premium UI/UX for the SoproLife panel, including cards, forms, controls, information hierarchy, responsive grids, accessibility, keyboard behavior, calendar/date presentation, and cross-browser visual validation. Use for visual refinements in both legacy and M15 interfaces; pair with soprolife-painel-command-center when business logic, data contracts, security, backend, migration, or deployment is involved.
---

# SoproLife Premium UX

Own presentation and interaction quality only. Use
[soprolife-painel-command-center](../soprolife-painel-command-center/SKILL.md)
for architecture, data, finance, security, backend, migration, Git, and
deployment rules; do not restate or override them here.

## Mandatory design rules

- Use the official SoproLife navy and teal identity already defined in
  [`style.css`](../../../painel-soprolife/css/style.css). Extend existing
  tokens and components before creating variants.
- Aim for BlueDox-inspired density, alignment, and hierarchy without copying
  its branding, layouts verbatim, or proprietary assets.
- Keep cards, labels, inputs, selects, textareas, buttons, messages, and states
  visually consistent. Align equivalent labels and controls predictably.
- Use responsive grids that collapse deliberately. Prevent overlap, clipped
  tooltips, horizontal overflow, and unreadable chips or long values.
- Make the hierarchy obvious: primary action/status first, secondary context
  next, technical metadata last. Never hide critical information in a tooltip.
- Preserve semantic HTML, visible focus, keyboard operation, accessible names,
  error association, sufficient contrast, and non-color status cues.
- Display dates in Brazilian format where visible while preserving backend
  date contracts. Show incomplete-date precision and assumed-day state; never
  make an approximate date appear exact.
- Add no external frontend dependency unless its benefit and security cost are
  clearly justified. Do not introduce a framework or build system for a small
  refinement.
- Validate in current Firefox and Chromium with synthetic data only. Never use
  real patient or clinical data in screenshots, fixtures, demos, or tests.

## Working method

1. Read the current HTML, CSS, rendering code, and nearby components before
   editing. Check both legacy styles and [`m15.css`](../../../painel-soprolife/css/m15.css)
   when the change touches native M15 UI.
2. Identify the screen's primary job and preserve existing data/API contracts.
   Escalate business-rule or backend changes to the command-center skill.
3. Reuse existing tokens and patterns; make the smallest coherent additive
   change. Avoid global selectors when a local component rule is safer.
4. Run syntax/tests relevant to touched files, then perform the complete
   [visual validation checklist](references/visual-validation.md).
5. Inspect the final diff and report the tested browsers, widths, states,
   keyboard path, and any remaining limitation.

## Component guidance

- For long metadata in a card, place the label above the value on a shared
  left axis with comfortable wrapping. Do not force long label/value pairs
  into opposite ends of one row.
- Prefer cards for a few information-rich records; prefer tables when row and
  column comparison is the user's primary task. Provide responsive handling
  for either choice.
- Keep analytical summaries before detail, use short labels, and explain source
  or interpretation with concise accessible help only when needed.
- Give every form a stable label/control rhythm, logical tab order, explicit
  required/error states, and a clear primary action. Preserve entered values
  when validation fails.

## Recommendations

- Keep one clear primary purpose per screen and reduce decorative noise.
- Use whitespace, size, weight, and color together to distinguish hierarchy.
- Test empty, loading, error, success, long-content, narrow, and zoomed states;
  the happy-path desktop view is insufficient.
