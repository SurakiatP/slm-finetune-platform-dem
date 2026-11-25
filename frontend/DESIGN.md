---
version: alpha
name: "TuneLab"
description: "A calm, high-density workspace for turning a training idea into a reliable small-model pipeline."
colors:
  background: "#F3F5F7"
  card: "#FFFFFF"
  foreground: "#16202A"
  primary: "#2563A8"
  secondary: "#EAEEF2"
  muted: "#EDF0F3"
  mutedForeground: "#5F6B78"
  accent: "#EAF1F9"
  danger: "#CD2727"
  success: "#158049"
  warning: "#C2650A"
  border: "#E5E9EE"
  darkBackground: "#0F141A"
  darkCard: "#171E26"
  darkPrimary: "#5B9BE0"
typography:
  sans:
    fontFamily: "Inter, IBM Plex Sans Thai, system-ui, sans-serif"
  mono:
    fontFamily: "JetBrains Mono, monospace"
rounded:
  DEFAULT: "0.75rem"
  sm: "0.5rem"
  md: "0.625rem"
  lg: "0.75rem"
  xl: "1rem"
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

TuneLab should feel calm and clean: a light grey desk with white cards laid on it. Technical details are close at hand and the next safe action stays unmistakable. One calm blue is the only accent; it marks the primary action, focus and the current menu item. Everything else is grey or near-black, and other colors appear only for status. ("Soft" style, chosen 2026-11-12.)

### Product context and register

- **Audience and primary job:** people preparing datasets, fine-tuning compact language models, and inspecting long-running pipeline work.
- **Target market(s) and evidence:** global technical users; English and Thai are supported application locales.
- **Locale(s) and language policy:** interface strings follow `LanguageContext`; technical IDs and model names stay verbatim. System font fallback renders Thai where Inter has no glyph.
- **Usage scene:** desktop-first operational work with occasional narrow-screen review; tasks may take minutes or hours.
- **Register:** product.
- **Memorable signature:** borderless white cards with a faint shadow on a grey canvas; one calm blue for action and focus.
- **Restraint:** no gradients, glows or gradient text; cards, tables, and charts stay quiet and tonal.
- **Anti-references:** avoid a promotional AI landing-page feel inside the authenticated workspace; avoid treating a fictional metric, unavailable dataset, or local mock as production data.
- **Token ownership/runtime mapping:** existing runtime CSS variables in `src/index.css` remain canonical (Model B). Tailwind aliases in `tailwind.config.ts` and shared UI components consume them. This file mirrors accepted values; it does not generate CSS.

## Colors

`primary` maps to `--primary`, `danger` to `--destructive`, and the remaining semantic roles map to their like-named variables in `src/index.css`. Dark mode remaps those variables without changing roles. Blue identifies action and focus. The page canvas is `--background` (grey); surfaces are `--card` (white in light mode); green, amber, and red communicate job outcome with text and icons as well as color. Scrollbars inherit the muted/background and primary roles globally.

## Typography

Inter is the reading and control face, with IBM Plex Sans Thai as the Thai fallback (Inter has no Thai glyphs). Thai text (`:lang(th)`) gets a taller 1.65 line height. JetBrains Mono is reserved for API paths, model tags, identifiers, and prompt examples. Card titles are 16px semibold, page titles 30px bold; dense technical strings may wrap rather than silently clip.

## Layout

The sidebar shares the page canvas (no divider line) and groups links under quiet headings: the build links first, then "Ship" and "Account". The top bar has no border. Routes sit in a centered `80rem` (`max-w-7xl`) column with `1.5rem` section rhythm. Cards form responsive grids; detailed model IDs, prompts, and errors wrap. Scroll ownership belongs to the local data surface or dialog body, never a shared route shell. The global scrollbar baseline lives in `src/index.css`.

## Elevation & Depth

In light mode, cards have no border and sit on the grey canvas with `shadow-card` (a faint two-layer shadow). Dialogs, menus, popovers and toasts use `shadow-pop`. In dark mode `--shadow-card` is `none` and cards get a 1px `--border` instead. Never use `shadow-lg/xl/2xl` directly.

## Shapes

Cards and dialogs use `rounded-xl` (16px); buttons, inputs and menus use `rounded-lg` (12px) from `--radius`. Small chips use pills only for compact status or categorical metadata. Borders are thin and muted.

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
- **Don't:** use blue decoration where it could compete with an active job or primary action.
- **Don't:** add gradients, glow, gradient text, or a second accent color.

## Keycloak login theme

The `tunelab` login theme (`docker/keycloak/themes/tunelab/login`) extends Keycloak's `keycloak.v2` pages and restyles them with the same palette, copied as plain values into `resources/css/tunelab.css`. Keep the two in sync when the palette changes. Dark mode follows the app's saved choice (`localStorage.theme`, readable because Keycloak is served on the same origin under `/auth`), falling back to the OS setting. The realm offers English and Thai.
