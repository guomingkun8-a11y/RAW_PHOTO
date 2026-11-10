export const name = 'raw-photo-team-policy'
import { createServer } from 'node:http'
import { pathToFileURL } from 'node:url'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'

export const inject = ['tools', 'agents', 'subagents']

export async function apply(ctx) {
  const root = process.env.DSH_TEAMS_PLUGIN_DIR
  const { haltTeamWork } = await import(pathToFileURL(join(root, 'lib/tools.js')))
  const { findTeamByCaptain } = await import(pathToFileURL(join(root, 'lib/state.js')))
  const sessionId = process.env.DSH_TEAMS_SESSION_ID
  const token = process.env.DSH_TEAMS_TOKEN
  const server = createServer(async (req, res) => {
    if (req.method !== 'POST' || req.url !== '/stop' || req.headers.authorization !== `Bearer ${token}`) {
      res.writeHead(403).end()
      return
    }
    try {
      const captain = ctx.agents.get(sessionId)
      const stateRoot = join(process.env.DSH_TEAMS_WORKSPACE, '.agent-teams')
      const team = captain && await findTeamByCaptain(stateRoot, sessionId)
      if (team) await haltTeamWork({ ctx, captain, stateRoot, teamId: team.id })
      res.writeHead(200).end('{}')
    } catch {
      res.writeHead(500).end('{}')
    }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  server.unref()
  ctx.on('dispose', () => { server.close() })
  const registered = await fetch(`${process.env.DSH_TEAMS_GATEWAY}/register`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ port: server.address().port }), signal: AbortSignal.timeout(5000),
  })
  if (!registered.ok) throw new Error('Team stop control registration failed')
  ctx.on('tools/pre-execute', async (exec, next) => {
    if (!exec.name.startsWith('agent_teams_') && !['generate_image', 'web_search', 'web_fetch'].includes(exec.name)) return next()
    try {
      const response = await fetch(`${process.env.DSH_TEAMS_GATEWAY}/policy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${process.env.DSH_TEAMS_TOKEN}` },
        body: JSON.stringify({ name: exec.name, arguments: exec.arguments, session_id: exec.agent?.id }),
        signal: AbortSignal.any([exec.signal, AbortSignal.timeout(10000)]),
      })
      if (!response.ok) return { kind: 'deny', reason: '协作授权已失效，请停止并告知用户。' }
      const result = await response.json()
      if (!result.allowed) return { kind: 'deny', reason: result.error || '请用户确认协作计划。' }
      if (result.retry_after_ms) await delay(result.retry_after_ms, null, { signal: exec.signal })
      return next()
    } catch {
      return { kind: 'deny', reason: '协作授权服务不可用，禁止继续执行。' }
    }
  })
  ctx.on('tools/post-execute', async (exec, result, next) => {
    if (['web_search', 'web_fetch'].includes(exec.name)) {
      const status = result.value?.statusCode
      const failed = result.isError || (typeof status === 'number' && status >= 400)
      const error = failed ? `${result.error?.code || ''} ${result.content?.filter(block => block.type === 'text').map(block => block.text).join(' ') || ''} ${status || ''}` : ''
      try {
        const response = await fetch(`${process.env.DSH_TEAMS_GATEWAY}/web-result`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ name: exec.name, session_id: exec.agent?.id, failed, error: error.slice(0, 1000) }),
          signal: AbortSignal.timeout(5000),
        })
        if (!response.ok) return { kind: 'block', feedback: [{ type: 'text', text: '协作授权已结束，请停止调用工具。' }] }
      } catch {
        return { kind: 'block', feedback: [{ type: 'text', text: '协作授权服务不可用，请停止调用工具。' }] }
      }
    }
    return next()
  })
}
