# EV Depot Dispatch — visual direction

> **REJECTED / SUPERSEDED:** This was the previous light-dashboard direction and is retained only as a historical record. The current implementation and reference decision are documented in `ev-depot-dispatch-workbench.md`.

## Decision

Build one original React/CSS visual system informed solely by Musemind's *Wind Power Energy Management Dashboard* light operational overview. The inspected screen combines a centred pill navigation, concise title and KPI area, a dominant annotated technical visual, and a row of compact, distinct information cards on a pale canvas. Transfer that hierarchy and modular data presentation, not its wind-turbine artwork, brand, diagrams, or metrics.

## Product adaptation

The central visual is the existing simulation's two-strategy comparison and interval-based vehicle schedule—not a turbine, map, physical-grid schematic, or live-control view. Use only scenario inputs and API-returned outputs. Carbon intensity and depot load are discrete interval data, so render interval columns/markers rather than implying a continuous measured signal. Keep the simulation-only and synthetic provenance visible.

## Composition

- 64 px light header with brand, compact pill navigation, and a persistent simulation-only indicator.
- Compact scenario title/context row and one summary strip; remove the oversized Dispatch Workbench introduction and separate workflow rail.
- Desktop content at 1440 px: 24 px page gutters; two columns with a 340 px scenario editor, 20 px gap, and a 1032 px analysis workspace.
- Put strategy comparison and the discrete carbon profile beside the compact scenario-to-schedule trace near the top of the analysis workspace. Keep the two strategy schedule cards within that analysis column below the overview, so detail follows the comparison without leaving a large blank desktop region beside the editor.
- At 390 px, stack the editor and analysis in one column, wrap navigation without horizontal page overflow, and preserve access to every input and action.

## Visual system

Use a warm, pale grey-green canvas (`#F1F3EE`), near-white cards (`#FFFFFF`), charcoal text (`#17241D`), forest-green primary action (`#28674F`), muted teal for the alternate strategy (`#2D7E83`), and amber/coral only for warnings. Use thin dividers, restrained card rounding, generous internal spacing, sans-serif headings, and tabular monospace only for units/technical values. No Adobe Stock file is selected, licensed, downloaded, embedded, or used; the inspected generic kits did not offer a suitable EV-depot/synthetic-simulation composition, so this implementation is original React/CSS/SVG.
