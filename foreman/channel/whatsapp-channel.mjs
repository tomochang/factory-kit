#!/usr/bin/env node
// Claude Code channel: the owner's own WhatsApp chat (read through Beeper Desktop) is the factory's inbox.
// A message starting "F:" becomes a <channel source="whatsapp"> event; everything else in the chat (notes,
// ask-me answers "Q7 ...", agent messages marked 🤖) is ignored. The reply tool writes back to the same chat.
// Before every read and send it checks that the chat is the account's own 1:1 chat (same check as ask-me.py).
// No permission relay: whoever can reply in a channel could approve tool use (Channels docs, Security).
// Settings come from factory.env (FACTORY_ENV names it; real env wins); the transport is beeper-transport.mjs.
// After every successful read it writes <ASK_ME_STATE>/channel.heartbeat for the watchdog's quiet alert.
import fs from 'node:fs'
import path from 'node:path'
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { ListToolsRequestSchema, CallToolRequestSchema } from '@modelcontextprotocol/sdk/types.js'
import { load, renderInstructions } from './kitenv.mjs'
import { createTransport } from './beeper-transport.mjs'

const cfg = load()
const missing = ['CHAT_ID', 'TOKEN_FILE'].filter(k => !cfg[k])
if (missing.length) {
  process.stderr.write(`whatsapp channel: ${missing.join(' and ')} required\n  set in ${cfg.ENV_FILE} (see factory.env.example)\n`)
  process.exit(1)
}
const POLL_MS = Number(process.env.FACTORY_POLL_MS || 10000)
const MARK = '🤖'
const TASK = /^\s*f\s*[:：]\s*/i
const beeper = createTransport({ base: cfg.BEEPER_BASE, tokenFile: cfg.TOKEN_FILE, chat: cfg.CHAT_ID })
const instructions = process.env.FACTORY_INSTRUCTIONS && fs.existsSync(process.env.FACTORY_INSTRUCTIONS)
  ? fs.readFileSync(process.env.FACTORY_INSTRUCTIONS, 'utf8').trim()
  : renderInstructions(cfg).trim()

function clean (html) {
  return String(html || '')
    .replace(/<img[^>]*\balt="([^"]*)"[^>]*>/g, '$1')
    .replace(/<\/p>\s*<p>/g, '\n\n').replace(/<br\s*\/?>/g, '\n')
    // only Beeper's formatting tags: text the owner typed, like "<JST time>" or "x < 3", must survive
    .replace(/<\/?(p|b|i|u|s|em|strong|code|pre|blockquote|span|a|del|ins|sub|sup|ul|ol|li|h[1-6])(\s[^>]*)?>/gi, '')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&')
    .normalize('NFKC').trim()
}

function heartbeat () {
  try {
    fs.mkdirSync(cfg.ASK_ME_STATE, { recursive: true, mode: 0o700 })
    const file = path.join(cfg.ASK_ME_STATE, 'channel.heartbeat')
    fs.writeFileSync(file + '.tmp', String(Date.now() / 1000))
    fs.renameSync(file + '.tmp', file)
  } catch (e) {
    process.stderr.write(`whatsapp channel: heartbeat not written: ${e.message || e}\n`)
  }
}

const mcp = new Server(
  { name: 'whatsapp', version: '0.2.0' },
  { capabilities: { experimental: { 'claude/channel': {} }, tools: {} }, instructions }
)

mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [{
    name: 'reply',
    description: `Send a message to ${cfg.OWNER_NAME || 'the owner'} on their own WhatsApp chat (marked as from the factory)`,
    inputSchema: { type: 'object', properties: { text: { type: 'string', description: 'The message to send' } }, required: ['text'] }
  }]
}))

mcp.setRequestHandler(CallToolRequestSchema, async req => {
  if (req.params.name !== 'reply') throw new Error(`unknown tool: ${req.params.name}`)
  const text = String(req.params.arguments?.text || '').trim()
  try {
    if (!text) throw new Error('empty message')
    await beeper.send(`${MARK} · factory\n${text}`)
    return { content: [{ type: 'text', text: 'sent' }] }
  } catch (e) {
    return { isError: true, content: [{ type: 'text', text: String(e.message || e) }] }
  }
})

await mcp.connect(new StdioServerTransport())

// Start after the newest existing message: history is never replayed as tasks.
let seen = null
let own = null
async function poll () {
  try {
    if (own === null) own = await beeper.ownChat()
    if (!own) return // not the own chat: deliver nothing
    const items = await beeper.messages()
    heartbeat()
    if (seen === null) { seen = Math.max(0, ...items.map(m => Number(m.sortKey) || 0)); return }
    for (const m of items) {
      const key = Number(m.sortKey) || 0
      if (key <= seen) continue
      seen = key
      const text = clean(m.text)
      if ((m.type || 'TEXT') !== 'TEXT' || text.startsWith(MARK) || !TASK.test(text)) continue
      await mcp.notification({ method: 'notifications/claude/channel', params: { content: text.replace(TASK, ''), meta: { message_id: String(m.id) } } })
    }
  } catch (e) {
    process.stderr.write(`whatsapp channel: ${e.message || e}\n`) // Beeper down or Mac asleep: try again next tick
  }
}
await poll()
setInterval(poll, POLL_MS)
