---
name: image-generation
description: Generate or edit images through RAW's authenticated image task queue. Use when the user asks to create, design, regenerate, or edit visual content.
---

# RAW Image Generation

Use the `raw_generate_image` tool for every image generation or image editing request.
Do not call provider-specific image APIs, local generation scripts, or shell commands.

## Workflow

1. Understand the user's visual goal and inspect attached references when useful.
2. Discuss or refine the direction when the user is still deciding.
3. Call `raw_marketing_strategy` only when the user explicitly asks for visible copy, typography, headlines, selling points, marketing copy, conversion messaging, or differentiation messaging. Do not call it just because the output is an ecommerce main image, carousel image, detail page, poster, banner, or ad creative. Skip `raw_marketing_strategy` when the latest user asks for no text, no copy, remove text, or no typography.
4. Only generate after the user explicitly asks to execute, generate, create, or regenerate.
5. Call `raw_generate_image` with one complete production prompt per image or page.
6. Report the queued task and continue using the returned image results when available.

## Tool Parameters

- `prompt`: Complete executable image prompt.
- `mode`: `generate` for a new image or `edit` when preserving or modifying references.
- `reference_images`: Workspace-relative attachment paths. Preserve product identity unless the user explicitly asks to change it.
- `size`: RAW image size such as `1024x1024`, `1536x1024`, or `1024x1536`.
- `quality`: RAW quality setting. Use the current conversation setting unless the user asks to change it.
- `count`: Number of outputs, from 1 to 8.
- `purpose`: Short user-visible purpose for the image.

## Ecommerce Direction

- Treat background, material, lighting, depth, props, and composition as creative decisions unless the user asks for a plain catalog or white-background image.
- For product references, preserve shape, structure, labels, logo, color, and material unless the requested edit explicitly changes them.
- Do not invent product claims, specifications, certifications, ingredients, or benefits.
- When text is requested, use the `raw_marketing_strategy` result for headline, subheadline, selling-point labels, hierarchy, placement, contrast, and safe area. Preserve exact user copy when provided.
- When text is not requested, reference templates may guide non-text composition, color, background, lighting, product placement, spacing, and visual rhythm only. Do not preserve or invent overlay titles, selling-point labels, badges, buttons, or decorative lettering by default.
- When the latest user asks for no text, no copy, remove text, or no typography, make this a hard generation constraint: do not create or preserve overlay titles, subtitles, selling-point labels, badges, buttons, watermarks, decorative lettering, Chinese characters, or English words from references or previous canvases. Use reference images only for non-text composition, color, background, lighting, product placement, and spatial rhythm. Preserve only unavoidable product packaging or Logo marks needed for product identity.
- For a detail-page set, create coherent pages with independent prompts and a shared product identity.

## Result Handling

The tool returns RAW task identifiers and any completed image metadata. Treat a queued task as accepted work, not as a finished image. Do not retry an identical failed request repeatedly; explain the failure or revise the failed dimension first.
