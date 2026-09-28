# CareFlow visual system

## Physical setting

A patient stands at a clinic touchscreen in bright light while a nurse reads a desktop queue nearby. Light surfaces, high contrast text and limited motion support that shared environment.

## Color and typography

Warm white #faf9f6, navy #172940 and cobalt #2457d6 replace the rejected sage and teal direction. Department icons use varied soft backgrounds to aid recognition. Clinical red, amber and green remain separate state roles. Provenance keeps its named source labels. Application palettes override the shared compatibility tokens; hex values remain readable by the repository's contrast tests, with optional OKLCH surface enhancement in base.css.

Noto Sans and IBM Plex Sans Arabic provide the existing Latin and Arabic text. Use weight and space for hierarchy. Patient headings can be generous; staff tables and phone screens use their density tokens.

## Layout and components

Kiosk: full-width registration, compact progress, a hospital header, illustrated department choices, editable review and a permanently visible Back/Continue action bar. Staff: persistent navigation, scannable live worklist, review panels and sourced briefings. Companion: single-column mobile layout with queue state, clinic questions and access to nurse escalation.

Shared buttons use 12px corners, visible hover and disabled states, and density-dependent touch targets. Content panels use subtle borders and shadow; controls use higher contrast borders. Avoid nested panels and ornamental motion. Clinical severity colours remain distinct from the hospital brand.

## Inclusion

Use logical inline/block spacing for RTL. Keep all labels in paired English/Arabic messages. Retain the prototype footer on every screen, visible keyboard focus and reduced-motion behavior. Test contrast rather than inferring it from appearance.
