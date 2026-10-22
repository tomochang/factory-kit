// Clean-install check (tests/test_clean_install.sh): start the channel exactly as the generated mcp.json says,
// the way Claude Code would, against a fake Beeper. An "F:" message the owner types must reach the session as a
// notifications/claude/channel event, and the session's spec reply must land in the owner's own chat.
// usage: node e2e-installed.mjs <generated/mcp.json> <fake beeper base URL>
import fs from 'node:fs'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

const [mcpFile, base] = process.argv.slice(2)
const server = JSON.parse(fs.readFileSync(mcpFile, 'utf8')).mcpServers.whatsapp
const sleep = ms => new Promise(r => setTimeout(r, ms))
const fail = msg => { console.error('e2e: FAIL ' + msg); process.exit(1) }

const events = []
const client = new Client({ name: 'clean-install', version: '0' })
client.fallbackNotificationHandler = async n => { if (n.method === 'notifications/claude/channel') events.push(n.params) }
await client.connect(new StdioClientTransport({
  command: server.command,
  args: server.args,
  env: { ...process.env, ...(server.env || {}), FACTORY_POLL_MS: '200' },
  stderr: 'inherit'
}))
const instructions = client.getInstructions() || ''
if (!/F: go T/.test(instructions)) fail('instructions do not explain F: go')
await sleep(600) // first poll: history is never replayed
await fetch(base + '/test/add', { method: 'POST', body: JSON.stringify({ text: 'F: build a word counter CLI' }) })
for (let i = 0; i < 50 && events.length === 0; i++) await sleep(100)
if (events.length !== 1 || events[0].content !== 'build a word counter CLI') fail('task not delivered: ' + JSON.stringify(events))
console.log('e2e: task delivered to the session: ' + events[0].content)
const spec = 'T1 spec: wc CLI in ~/factory/wc, tested with pytest. Reply F: go T1'
const r = await client.callTool({ name: 'reply', arguments: { text: spec } })
if (r.isError) fail('reply refused: ' + JSON.stringify(r.content))
const sent = await (await fetch(base + '/test/sent')).json()
if (!sent.some(t => t.endsWith(spec))) fail('spec reply not in the own chat: ' + JSON.stringify(sent))
console.log('e2e: spec reply reached the own chat')
await client.close()
