// Live smoke test against the real Beeper Desktop (manual, not part of npm test):
// starts the channel over stdio, sends one marked message to the own chat, then listens for F: tasks.
// usage: node test/smoke-live.mjs [seconds-to-listen]
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

const server = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'whatsapp-channel.mjs')
const listen = Number(process.argv[2] || 0)
const client = new Client({ name: 'smoke', version: '0' })
client.fallbackNotificationHandler = async n => console.log('EVENT', JSON.stringify(n.params))
await client.connect(new StdioClientTransport({ command: process.execPath, args: [server], env: { ...process.env, FACTORY_POLL_MS: '3000' } }))
const r = await client.callTool({ name: 'reply', arguments: { text: 'Factory channel live test: this message came through the new WhatsApp channel. If you like, send "F: ping" to see a task arrive.' } })
console.log('REPLY', JSON.stringify(r))
await new Promise(res => setTimeout(res, listen * 1000))
await client.close()
