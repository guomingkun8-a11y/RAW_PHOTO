# CowAgent Runtime In RAW

This directory contains a vendored snapshot of CowAgent from
`D:\GMK\CowAgent-master`, imported on 2026-08-14 under its MIT license.

RAW keeps this source separate from the existing professional image Agent so
both engines can be reviewed and operated in parallel.

## Integration Changes

- CowAgent channel applications, frontend, logs, secrets, virtual environment,
  and runtime data are not included.
- CowAgent's provider-specific image generation script is not included.
- The `image-generation` skill calls RAW's authenticated image task tool.
- RAW owns authentication, user and conversation isolation, relay selection,
  attachments, task queues, persistence, cancellation, and public events.
- High-risk filesystem, shell, browser, scheduler, environment, and MCP tools
  remain present in the source snapshot but are disabled by default in RAW.

The original license is preserved in `LICENSE`; upstream documentation is in
`UPSTREAM_README.md`.
