import { createHash } from 'node:crypto'

export const name = 'raw-photo-image-tools'
export const inject = ['tools']

export function apply(ctx) {
  const skillIds = ['gpt-image-2-style-library', 'diagram-design', 'ecommerce-competitor-analysis', 'customer-research', 'competitor-profiling', 'ecommerce-competitor-analyzer']
  const properties = {
    prompt: { type: 'string', minLength: 1, maxLength: 12000, description: 'Single-image prompt, or a short description of the set when prompts is provided.' },
    prompts: { type: 'array', minItems: 1, maxItems: 4, items: { type: 'string', minLength: 1, maxLength: 12000 }, description: 'Required for distinct subjects or scenes: one complete, specific prompt per image, in order. Choose a concrete subject for EACH image, never repeat a shared list of alternatives. Shared style and reference identity must be repeated in every prompt. Example: [snow leopard on mountain, red fox in forest, monkey on branch]. Omit n, or set n to this array length; the values are not multiplied.' },
    n: { type: 'integer', minimum: 1, maximum: 4, description: 'Without prompts, n produces variants of the SAME subject and prompt. For different subjects, use prompts instead.' },
    reference_ids: { type: 'array', maxItems: 6, items: { type: 'string' } },
    model: { type: 'string', enum: ['gpt-image-2', 'gpt-image-2.5-flare', 'gpt-image-2.5-sunburst', 'gpt-image-2.6-flare', 'gpt-image-2.6-sunburst', 'banana-2'] },
    aspect_ratio: { type: 'string', description: 'Honor the requested ratio, or choose for the task. Use only model_capabilities.ratios; ask before changing unsupported requests.' },
    quality: { type: 'string', description: 'Honor the requested quality, otherwise choose from model_capabilities.qualities.' },
    image_size: { type: 'string', description: 'Resolution tier from model_capabilities.resolutions. Omit when that list is empty.' },
    background: { type: 'string', enum: ['auto', 'opaque', 'transparent'] },
  }
  for (const [tool, path, description, schema] of [
    ['read_skill', '/skill-reference', 'Read main skill instructions first, then a bundled reference stem without paths or .md. diagram-design alone is discoverable on demand. All others require explicit selection for this turn: gpt-image-2-style-library (style-library); ecommerce-competitor-analysis; customer-research (source-guides, interviews-and-surveys); competitor-profiling (templates, tool-reference); ecommerce-competitor-analyzer (analysis-prompt-base, n8n-workflow-analysis). Analysis uses existing web_search/web_fetch or uploaded materials only. No third-party commands, credentials, scheduled monitoring or Google Sheets access. Missing metrics are unavailable, never invented.', { type: 'object', properties: { skill_id: { type: 'string', enum: skillIds }, reference: { type: 'string', maxLength: 80 } }, required: ['skill_id'], additionalProperties: false }],
    ['render_diagram', '/diagram-render', 'Save a requested diagram as static standalone SVG after reading diagram-design instructions and the relevant type reference. Validates and attaches the diagram to this conversation with preview, zoom and download. No image-generation API, HTML, scripts, remote resources or shell. Make edits as new versions; do not repeat SVG in the text answer.', { type: 'object', properties: { title: { type: 'string', minLength: 1, maxLength: 100 }, svg: { type: 'string', minLength: 1, maxLength: 60000, description: 'Complete SVG with xmlns and finite positive viewBox, max 4096x4096, max 60000 UTF-8 bytes. Static basic shapes, text, defs/markers, inline styles; local #id references only. No comments, CSS imports, external fonts, filters, embedded images or active content. Use system fonts.' } }, required: ['title', 'svg'], additionalProperties: false }],
    ['get_diagram', '/diagram-get', 'Read a previous diagram SVG from this conversation for a user-requested revision. Read diagram-design instructions first; use reference_id from the system diagram catalog or a render_diagram result. Cannot read other users or conversations.', { type: 'object', properties: { reference_id: { type: 'string', minLength: 1, maxLength: 191 } }, required: ['reference_id'], additionalProperties: false }],
    ['read_expert_reference', '/expert-reference', 'Read an allowlisted design reference for the currently selected expert. Product rules for identity/editing, style library for layout/sets. No paths, URLs, shell or arbitrary files. Not available without an expert selection.', { type: 'object', properties: { reference: { type: 'string', enum: ['product-rules', 'style-library'] } }, required: ['reference'], additionalProperties: false }],
    ['generate_image', '/generate', 'Generate or edit images only when explicitly requested. Uses the selected image model. Omit reference_ids to use current uploads; use [] for text-to-image, or IDs from the system image catalog to edit previous images. Returns an asynchronous task immediately. Do not resubmit or loop waiting for completion.', { type: 'object', properties, required: ['prompt'], additionalProperties: false }],
    ['get_image_task', '/status', 'Check a previously submitted image task from this conversation. Never repeatedly poll; the UI updates automatically.', { type: 'object', properties: { reference_id: { type: 'string' } }, required: ['reference_id'], additionalProperties: false }],
  ]) {
    ctx.tools.register({
      name: tool, description, parameters: schema, isConcurrencySafe: false, timeoutMs: 60000,
      output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      async execute(args, exec) {
        const response = await fetch(`${process.env.DSH_AGENT_IMAGE_BASE_URL}${path}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${process.env.DSH_AGENT_IMAGE_TOKEN}` },
          body: JSON.stringify({ call_id: process.env.DSH_TEAMS_TOKEN
            ? createHash('sha256').update(`${exec.agent?.id}:${exec.callId}`).digest('hex') : exec.callId, arguments: args }), signal: exec.signal,
        })
        const result = await response.json()
        if (!response.ok) throw new Error(result.error || 'Image tool request failed; do not resubmit automatically.')
        return result
      },
    })
  }
}
