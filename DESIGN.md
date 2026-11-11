---
name: gitsync
description: Professional multi-platform Git repository synchronization suite and operator dashboard
colors:
  bg: "#0d1117"
  side: "#010409"
  panel: "#161b22"
  panel-subtle: "#1b2230"
  hover: "#1f2632"
  line: "#263040"
  line-strong: "#333f4f"
  text: "#e6edf3"
  muted: "#8b949e"
  nav: "#c9d1d9"
  accent: "#6ea8fe"
  accent-strong: "#388bfd"
  accent-ink: "#040c18"
  ok: "#3fb950"
  bad: "#f85149"
  warn: "#d29922"
  shadow: "rgba(0,0,0,.5)"
typography:
  sans:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    fontSize: "14px"
    lineHeight: "1.5"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace"
    fontSize: "12.5px"
    lineHeight: "1.75"
    letterSpacing: "-0.01em"
  label:
    fontFamily: "{typography.sans.fontFamily}"
    fontSize: "12px"
    lineHeight: "1.4"
    letterSpacing: "0.05em"
    fontWeight: "600"
    textTransform: "uppercase"
  caption:
    fontFamily: "{typography.sans.fontFamily}"
    fontSize: "12px"
    lineHeight: "1.4"
  h1:
    fontFamily: "{typography.sans.fontFamily}"
    fontSize: "24px"
    fontWeight: "650"
    letterSpacing: "-0.01em"
  h2:
    fontFamily: "{typography.sans.fontFamily}"
    fontSize: "16px"
    fontWeight: "600"
  route-title:
    fontFamily: "{typography.sans.fontFamily}"
    fontSize: "16px"
    fontWeight: "600"
  metric:
    fontFamily: "{typography.sans.fontFamily}"
    fontSize: "30px"
    fontWeight: "650"
    letterSpacing: "-0.02em"
rounded:
  sm: "4px"
  md: "9px"
  card: "12px"
  modal: "14px"
  pill: "99px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "22px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.md}"
    height: "38px"
  button-danger:
    backgroundColor: "{colors.bad}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    height: "38px"
  card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.card}"
    padding: "18px"
  status-pill:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.muted}"
    rounded: "{rounded.pill}"
    padding: "5px 12px"
---

# Design System — gitsync

## Overview
`gitsync` is a professional multi-platform Git repository synchronization and backup engine with an operator dashboard. The visual world is precision engineering: dark-mode native, high contrast, zero-latency, and zero-runtime-dependency standard web technologies (vanilla HTML5, CSS3, ES6).

Every visual choice serves task scanability, real-time telemetry, and operational safety.

## Colors
The palette is rooted in deeply dark, non-pure-black surfaces with cold blue accents and semantic signal colors:

- **Surfaces**: `--bg` (`#0d1117`), `--side` (`#010409`), `--panel` (`#161b22`), `--panel2` (`#1b2230`).
- **Boundaries**: `--line` (`#263040`) for secondary layout divides, `--line2` (`#333f4f`) for active cards, forms, and tables.
- **Typography**: Primary `--text` (`#e6edf3`), secondary `--nav` (`#c9d1d9`), muted labels `--muted` (`#8b949e`).
- **Interactive Accent**: Electric cornflower blue `--accent` (`#6ea8fe`) paired with deep ink contrast `--accent-ink` (`#040c18`).
- **Operational Signals**:
  - Positive / In-sync: `--ok` (`#3fb950`)
  - Divergent / Broken: `--bad` (`#f85149`)
  - Warning / In-progress: `--warn` (`#d29922`)

## Typography
Typography is split strictly by function:
- **Interface & Prose**: System native sans-serif stack (`-apple-system, BlinkMacSystemFont, 'Segoe UI', ...`) for effortless legibility without external font dependencies.
- **Code, Hashes, Logs & Metrics**: Monospace stack (`ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace`).
- **Tabular Alignment**: `font-variant-numeric: tabular-nums` is enforced globally across all numbers, tables, timestamps, byte gauges, and metric cards to guarantee rock-solid visual column stability during live data streaming.
- **Headings**: `text-wrap: balance` prevents orphaned single-word lines across page headers, modal titles, and card headers.

## Layout
The dashboard employs an adaptive app-shell layout:
- **Desktop (≥860px)**: Two-column grid with a sticky 232px icon sidebar and a flexible content canvas with a sticky blurred top bar (`rgba(13,17,23,.82)`, `backdrop-filter: blur(10px)`).
- **Mobile (<860px)**: Single column with a horizontally scrolling compact navigation bar, collapsible header, and full-width card reflow.
- **Touch Interaction**: `touch-action: manipulation` and `-webkit-tap-highlight-color: transparent` prevent delay and tap flicker.

## Elevation & Depth
Depth is created through solid layering and subtle ambient shadows rather than theatrical gradients:
- **Cards**: `box-shadow: 0 10px 28px rgba(0,0,0,.35)` with a 1px solid border (`var(--line)`).
- **Modals**: Elevated `box-shadow: 0 20px 48px rgba(0,0,0,.5)` over a dark backdrop with `backdrop-filter: blur(6px)`.
- **Zero-Offset Halos Banned**: Pure solid indicator dots without artificial glowing shadows.

## Shapes
Geometric restraint defines all surfaces:
- **Cards**: 12px border radius.
- **Modals**: 14px border radius.
- **Controls (Buttons, Inputs, Selects)**: 9px border radius with a minimum height of 38px–42px for accessible click and tap targeting.
- **Status Pills & Chips**: 99px pill radius.

## Components
- **Top Bar**: Live heartbeat status pill, dry run / sync trigger buttons, and top progress bar.
- **Sidebar**: Icon navigation items with 3px left indicator on the active route.
- **Modal Confirm (`G.modalConfirm`)**: Accessible in-app dialog replacing browser native popups, complete with keyboard focus trapping, Escape key dismissal, and trigger focus return.
- **Viewer / Console**: Dark preformatted log viewer with per-level semantic line tinting (`.l-ok`, `.l-bad`, `.l-warn`, `.l-head`).
- **Toast Notifications**: Minimal, non-intrusive floating toasts with semantic icons and 1px borders.

## Do's and Don'ts

### Do
- Always use `font-variant-numeric: tabular-nums` when displaying data, counts, or metrics.
- Use `text-wrap: balance` on all primary and secondary headings.
- Use `G.modalConfirm()` for destructive or resource-intensive confirmations.
- Ensure all form inputs have meaningful `name`, `autocomplete`, and `spellcheck="false"` where credentials or paths are concerned.
- Keep the entire UI 100% dependency-free (vanilla HTML/CSS/JS only).

### Don't
- Never use colored side-tab borders on toasts or cards.
- Never use zero-offset colored glow box-shadows.
- Never use native `window.confirm()` or `window.alert()`.
- Never use generic purple, violet, or artificial AI neon gradients.
- Never use raw anchor tags (`<a href="#">`) for interactive button actions.
