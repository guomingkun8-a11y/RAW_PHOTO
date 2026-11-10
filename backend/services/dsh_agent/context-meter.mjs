export const name = 'raw-photo-context'
export const inject = ['tokenMeter', 'sessions', 'loader']

export async function apply(ctx) {
  const { appendPluginRecord } = await ctx.loader.import('@deepseek-ai/dsh-session')
  const budgets = JSON.parse(process.env.DSH_AGENT_CONTEXT_BUDGETS)
  const states = new WeakMap()
  ctx.on('session/event', (session, event) => {
    if (!['request/context', 'assistant/message', 'tool/result', 'compaction/start', 'compaction/end', 'turn/end'].includes(event.type)) return
    const header = session.requestHeader()
    const model = header?.config.model
    const budget = budgets[model]
    if (!budget) return
    const state = states.get(session) || { compacting: false, compacted: false, compaction_failed: false }
    if (event.type === 'compaction/start') Object.assign(state, { compacting: true, compaction_failed: false })
    if (event.type === 'compaction/end') Object.assign(state, { compacting: false, compacted: !event.data.error, compaction_failed: !!event.data.error })
    states.set(session, state)
    // The native meter prices the active surface, including tools and retained images.
    // This log-only record never enters model context or includes message content.
    const snapshotState = { ...state }
    queueMicrotask(() => {
      try {
        const measurement = ctx.tokenMeter.measure(session)
        appendPluginRecord(session, 'plugin:raw-photo/context', {
          model, ...budget, ...snapshotState,
          used_tokens: Math.ceil(measurement.totalTokens),
          estimated: true,
          measured_by: 'dsh-token-meter',
        })
      } catch {
        ctx.logger.warn('Context measurement unavailable; keeping the last snapshot.')
      }
    })
  })
}
