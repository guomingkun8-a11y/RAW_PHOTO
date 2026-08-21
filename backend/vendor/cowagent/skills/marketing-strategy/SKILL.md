---
name: marketing-strategy
description: Build ecommerce marketing copy, differentiated selling points, and typography layout strategy before RAW image generation.
---

# RAW Marketing Strategy

Use `raw_marketing_strategy` only when the current request involves ecommerce main images, carousel images, detail pages, visible copy, typography, selling points, marketing, conversion, differentiation, posters, banners, or ad creatives.

Do not use this skill for simple background replacement, color adjustment, ratio change, retouching, or non-commercial visual edits.

## Workflow

1. Inspect product references with `raw_vision` first when product facts, packaging text, or reference layout are important.
2. Call `raw_marketing_strategy` with the current goal, known product context, platform, and any useful reference-style notes.
3. Use the returned strategy as the copy and layout source for `raw_generate_image`.
4. Do not let the image-generation prompt invent new headline copy, English slogans, certifications, percentages, prices, rankings, medical claims, sterilization claims, anti-microbial claims, or unavailable product specs.

## Strategy Boundary

The strategy tool plans:

- product understanding
- target audience
- user pain points
- differentiated selling points
- headline
- subheadline
- short selling-point labels
- typography hierarchy and placement
- visual hook
- forbidden claims

It does not generate images and does not submit RAW tasks.

## Copy Rules

- Default all new overlay copy to Simplified Chinese.
- Use English only when the user explicitly requests English or provides exact English text.
- Preserve English already visible on packaging or logos as product identity, but do not expand it into new English selling points.
- If product facts are unknown, mark them unknown and use neutral scene-benefit or pain-point framing.
- Avoid generic copy such as `品质之选`, `清新相伴`, `深层清洁`, or `好物推荐` unless grounded by a specific visible or user-provided product reason.
- Keep on-image copy short and readable.

## Prompt Handoff

When calling `raw_generate_image`, include the strategy explicitly:

- exact headline
- exact subheadline when present
- exact short selling-point labels
- layout placement and hierarchy
- safe area and contrast requirements
- forbidden claims

The image model should execute the strategy; it should not create unrelated marketing copy.
