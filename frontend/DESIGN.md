---
version: alpha
name: "TuneLab"
description: "A calm, high-density workspace for turning a training idea into a reliable small-model pipeline."
colors:
  background: "#FFFFFF"
  foreground: "#1B1C22"
  primary: "#5D4FE5"
  secondary: "#F3F4F6"
  muted: "#F3F4F6"
  mutedForeground: "#6C6F7A"
  accent: "#F0EFFF"
  danger: "#F43F3F"
  success: "#21B45B"
  warning: "#F59E0B"
  border: "#E6E7EB"
typography:
  sans:
    fontFamily: "Inter, system-ui, sans-serif"
  mono:
    fontFamily: "JetBrains Mono, monospace"
rounded:
  DEFAULT: "0.625rem"
  sm: "0.375rem"
  md: "0.5rem"
  lg: "0.625rem"
spacing:
  page-max: "80rem"
  section-gap: "1.5rem"
  card-padding: "1.25rem"
components:
  button: {}
  input: {}
  select: {}
  card: {}
  dialog: {}
---

# TuneLab Design System

## Overview

### Creative North Star

TuneLab should feel like a well-organized model-training notebook: technical details are close at hand, while the next safe action stays unmistakable. The purple signal is reserved for the active stage and primary commit, like a highlighted run in a lab log.

### Product context and register

- **Audience and primary job:** people preparing datasets, fine-tuning compact language models, and inspecting long-running pipeline work.
- **Target market(s) and evidence:** global technical users; English and Thai are supported application locales.
- **Locale(s) and language policy:** interface strings follow `LanguageContext`; technical IDs and model names stay verbatim. System font fallback renders Thai where Inter has no glyph.
- **Usage scene:** desktop-first operational work with occasional narrow-screen review; tasks may take minutes or hours.
- **Register:** product.
- **Memorable signature:** restrained violet is the consistent marker of the active pipeline stage, focus, and primary action.
- **Restraint:** cards, tables, and charts remain quiet, mostly tonal and bordered instead of decorative.
- **Anti-references:** avoid a promotional AI landing-page feel inside the authenticated workspace; avoid treating a fictional metric, unavailable dataset, or local mock as production data.
- **Token ownership/runtime mapping:** existing runtime CSS variables in `src/index.css` remain canonical (Model B). Tailwind aliases in `tailwind.config.ts` and shared UI components consume them. This file mirrors accepted values; it does not generate CSS.

## Colors

`primary` maps to `--primary`, `danger` to `--destructive`, and the remaining semantic roles map to their like-named variables in `src/index.css`. Dark mode remaps those variables without changing roles. Violet identifies action and focus; green, amber, and red communicate job outcome with text and icons as well as color. Scrollbars inherit the muted/background and primary roles globally.

## Typography

Inter is the reading and control face. JetBrains Mono is reserved for API paths, model tags, identifiers, and prompt examples. Body text is compact but never smaller than the shared component defaults for editable fields. Thai text uses browser system fallback rather than being forced into a Latin-only face; dense technical strings may wrap rather than silently clip.

## Layout

Routes use a maximum `80rem` working width and `1.5rem` section rhythm. Cards form responsive grids; detailed model IDs, prompts, and errors wrap. Scroll ownership belongs to the local data surface or dialog body, never a shared route shell. The global scrollbar baseline lives in `src/index.css`.

## Elevation & Depth

Borders and subtly differentiated surfaces establish hierarchy. Dialogs and authored select popups use the shared component shadow; ordinary cards do not rely on elevation. Dark mode keeps borders visible rather than adding heavy shadows.

## Shapes

Controls and cards share the `0.625rem` radius family through `--radius`; small chips use smaller corners or pills only for compact categorical metadata. Borders are thin and muted.

## Components

### Foundational visual states

Shared `Button`, `Input`, Radix `Select`, dialog, alert, toast, and card primitives own state styling. Focus uses `--ring`; disabled controls lower opacity and cannot receive pointer events; busy controls preserve their geometry. Initial lists use a stable spinner or existing skeleton, while background refresh preserves current content.

### Buttons and actions

Solid primary buttons perform the next safe commit. Outline buttons reveal secondary actions. Destructive actions use the shared destructive variant inside `ConfirmDialog`; unavailable backend templates are visibly labeled and their start action is disabled.

### Navigation and data display

The sidebar and shared tabs organize pipeline stages. Cards summarize independent records; tables retain native semantics where used. Status and queue badges always pair color with a label.

### Forms and overlays

Radix Select is the authored select owner. Search fields expose an explicit clear control and debounce remote requests for 300ms after IME composition ends. Dialogs use shared focus-management primitives and constrain long bodies internally.

### Iconography

Lucide is the icon family. Icons clarify a labeled action; icon-only actions provide an accessible name.

### Motion

Route and list entrances use short, restrained fades. Motion communicates loading or hierarchy only and must remain nonessential under reduced-motion preferences.

### Content and data visualization

Use plain verbs and backend truth: “Run evaluation,” “Cancel generation,” “Unavailable.” Never turn absent prices into $0 or unavailable templates into actionable presets.

## Do's and Don'ts

- **Do:** show Engine status, availability, and long-running progress as the backend reports it.
- **Do:** reuse shared UI primitives and semantic token aliases for all product controls.
- **Don't:** place fictional model results, unsupported models, or invented availability in an Engine-backed route.
- **Don't:** use violet decoration where it could compete with an active job or primary action.
