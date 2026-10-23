// Shipped channel test, generalised for the kit. End-to-end: start the channel the way Claude Code does (stdio
// MCP) against a fake Beeper Desktop API. Values come from the environment here (real env wins over factory.env).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { createTransport } from '../beeper-transport.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SERVER = path.join(HERE, '..', 'whatsapp-channel.mjs')
const KIT = path.resolve(HERE, '..', '..', '..')
const SELF = '!self:beeper.local'
const ACCOUNT = 'whatsapp.81700000000'
const sleep = ms => new Promise(r => setTimeout(r, ms))

function fakeBeeper (selfPhone = '+81700000000') {
  const msgs = []
  const sent = []
  const reactions = [] // {id, key}: POST /v1/chats/{chat}/messages/{id}/reactions (Beeper Desktop API 5.0.0)
  const fake = { failReactions: false }
  const add = text => msgs.push({ id: String(msgs.length + 1), sortKey: String(1000 + msgs.length), type: 'TEXT', text, isSender: true })
  const srv = http.createServer((req, res) => {
    const reply = (obj, code = 200) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)) }
    if (req.method === 'POST') {
      let body = ''
      req.on('data', c => { body += c })
      const r = req.url.match(/\/messages\/([^/]+)\/reactions$/)
      if (r) {
        req.on('end', () => {
          if (fake.failReactions) return reply({ error: 'boom' }, 500)
          const key = JSON.parse(body).reactionKey
          reactions.push({ id: decodeURIComponent(r[1]), key })
          reply({ success: true, chatID: SELF, messageID: r[1], reactionKey: key, transactionID: 't' })
        })
        return
      }
      req.on('end', () => { const t = JSON.parse(body).text; sent.push(t); add('<p>' + t.replace(/\n/g, '<br>') + '</p>'); reply({ chatID: SELF }) })
      return
    }
    if (req.url.startsWith('/v1/accounts')) return reply([{ accountID: ACCOUNT, user: { phoneNumber: '+81700000000' } }])
    if (req.url.endsWith('/messages')) return reply({ items: [...msgs].reverse() })
    if (req.url.startsWith('/v1/chats/')) {
      return reply({ id: SELF, accountID: ACCOUNT, type: 'single', participants: { items: [{ id: 'wa', phoneNumber: selfPhone, isSelf: false }, { id: 'me', isSelf: true }] } })
    }
    reply({}, 404)
  })
  return new Promise(resolve => srv.listen(0, '127.0.0.1', () => resolve(Object.assign(fake, { srv, msgs, sent, add, reactions, port: srv.address().port }))))
}

function baseEnv (beeper, dir, extra = {}) {
  const env = {}
  for (const [k, v] of Object.entries(process.env)) {
    if (!/^(FACTORY_|ASK_ME_|BEEPER_)/.test(k) && !['OWNER_NAME', 'CHAT_ID', 'TOKEN_FILE', 'BEEPER_BASE', 'WORK_DIR', 'PROTECTED_DIRS'].includes(k)) env[k] = v
  }
  return {
    ...env,
    BEEPER_API_BASE: `http://127.0.0.1:${beeper.port}`,
    BEEPER_TOKEN_FILE: path.join(dir, 'token'),
    FACTORY_CHAT: SELF,
    FACTORY_POLL_MS: '100',
    FACTORY_ENV: path.join(dir, 'no-factory.env'),
    ASK_ME_STATE: path.join(dir, 'state'),
    OWNER_NAME: 'Alex Example',
    WORK_DIR: '/Users/x/factory',
    PROTECTED_DIRS: '/Users/x/private-repo',
    ...extra
  }
}

async function startChannel (beeper, extra = {}, dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-channel-'))) {
  fs.writeFileSync(path.join(dir, 'token'), 't\n')
  const events = []
  const client = new Client({ name: 'test', version: '0' })
  client.fallbackNotificationHandler = async n => { if (n.method === 'notifications/claude/channel') events.push(n.params) }
  const transport = new StdioClientTransport({ command: process.execPath, args: [SERVER], env: baseEnv(beeper, dir, extra), stderr: 'pipe' })
  const started = await Promise.race([client.connect(transport).then(() => true), sleep(5000).then(() => false)])
  if (!started) { await transport.close(); throw new Error('channel server did not start within 5s') }
  return { client, events, dir, close: () => client.close() }
}

test('only new messages starting with F: reach the session; history, notes, Q-answers and agent messages do not', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  b.add('F: old task from yesterday')
  const ch = await startChannel(b)
  try {
    await sleep(300)
    b.add('just a note to self')
    b.add('Q5 yes')
    b.add('<p>🤖 Q6 · factory<br>question</p>')
    b.add('F: add a /health endpoint to the sandbox app')
    b.add('f:  lower-case prefix works too')
    await sleep(500)
    assert.deepEqual(ch.events.map(e => e.content), ['add a /health endpoint to the sandbox app', 'lower-case prefix works too'])
    assert.match(ch.events[0].meta.message_id, /^\d+$/)
  } finally { await ch.close(); b.srv.close() }
})

test('the reply tool posts to the own chat, marked as the factory', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const ch = await startChannel(b)
  try {
    const { tools } = await ch.client.listTools()
    assert.deepEqual(tools.map(t => t.name), ['reply'])
    const r = await ch.client.callTool({ name: 'reply', arguments: { text: 'Spec: GET /health returns ok. Build it? reply F: go' } })
    assert.equal(r.isError, undefined)
    assert.deepEqual(b.sent, ['🤖 · factory\nSpec: GET /health returns ok. Build it? reply F: go'])
    await sleep(300)
    assert.equal(ch.events.length, 0) // its own message never comes back as a task
  } finally { await ch.close(); b.srv.close() }
})

test('refuses when the configured chat is not the owner\'s own chat: nothing sent, nothing delivered', { timeout: 15000 }, async () => {
  const b = await fakeBeeper('+819999999999')
  const ch = await startChannel(b)
  try {
    const r = await ch.client.callTool({ name: 'reply', arguments: { text: 'hi' } })
    assert.equal(r.isError, true)
    b.add('F: do something')
    await sleep(400)
    assert.deepEqual(b.sent, [])
    assert.equal(ch.events.length, 0)
  } finally { await ch.close(); b.srv.close() }
})

test('instructions make the foreman number specs and hand approved tasks to background agents in worktrees', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const ch = await startChannel(b)
  try {
    const text = ch.client.getInstructions()
    assert.match(text, /T1/)
    assert.match(text, /F: go T/)
    assert.match(text, /background/i)
    assert.match(text, /worktree/i)
    assert.match(text, /safe-push/)
    assert.match(text, /Never send messages or mail/)
    assert.match(text, /own chat/) // notifying the owner in their own chat is not "sending messages"; their go approves it
    // kit: filled from the template with the configured values, nothing left over
    assert.match(text, /Alex Example/)
    assert.match(text, /\/Users\/x\/private-repo/)
    assert.ok(text.includes(path.join(KIT, 'scripts', 'ask-me.py')))
    assert.doesNotMatch(text, /\{\{|\}\}/)
  } finally { await ch.close(); b.srv.close() }
})

test('angle brackets typed in a task survive; only Beeper formatting tags are removed', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const ch = await startChannel(b)
  try {
    await sleep(300)
    b.add("F: send 'stopped at <JST time>' when x < 3 and y > 2")
    b.add('<p>F: formatted <b>bold</b><br>second line</p>')
    await sleep(500)
    assert.deepEqual(ch.events.map(e => e.content), ["send 'stopped at <JST time>' when x < 3 and y > 2", 'formatted bold\nsecond line'])
  } finally { await ch.close(); b.srv.close() }
})

// --- kit additions

test('a generated instructions file is used as is', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'wa-instr-')), 'channel-instructions.txt')
  fs.writeFileSync(f, 'Generated instructions for the foreman: reply F: go T1.\n')
  const ch = await startChannel(b, { FACTORY_INSTRUCTIONS: f })
  try {
    assert.equal(ch.client.getInstructions().trim(), 'Generated instructions for the foreman: reply F: go T1.')
  } finally { await ch.close(); b.srv.close() }
})

test('each successful read leaves a heartbeat for the watchdog', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const ch = await startChannel(b)
  try {
    await sleep(400)
    const hb = path.join(ch.dir, 'state', 'channel.heartbeat')
    const t = Number(fs.readFileSync(hb, 'utf8'))
    assert.ok(Math.abs(Date.now() / 1000 - t) < 5, String(t))
  } finally { await ch.close(); b.srv.close() }
})

test('values come from factory.env when the environment has none', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-envfile-'))
  fs.writeFileSync(path.join(dir, 'tok'), 't\n')
  const envFile = path.join(dir, 'factory.env')
  fs.writeFileSync(envFile, `OWNER_NAME="Alex Example"\nCHAT_ID="${SELF}"\nTOKEN_FILE="${dir}/tok"\nBEEPER_BASE="http://127.0.0.1:${b.port}"\nASK_ME_STATE="$HOME/st"\n`)
  const env = baseEnv(b, dir, { FACTORY_ENV: envFile, HOME: dir })
  for (const k of ['BEEPER_API_BASE', 'BEEPER_TOKEN_FILE', 'FACTORY_CHAT', 'ASK_ME_STATE', 'OWNER_NAME']) delete env[k]
  const client = new Client({ name: 'test', version: '0' })
  await client.connect(new StdioClientTransport({ command: process.execPath, args: [SERVER], env, stderr: 'pipe' }))
  try {
    const r = await client.callTool({ name: 'reply', arguments: { text: 'from the env file' } })
    assert.equal(r.isError, undefined, JSON.stringify(r))
    assert.deepEqual(b.sent, ['🤖 · factory\nfrom the env file'])
    await sleep(300)
    assert.ok(fs.existsSync(path.join(dir, 'st', 'channel.heartbeat'))) // $HOME expanded
  } finally { await client.close(); b.srv.close() }
})

test('without a chat ID the server stops and names factory.env', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-nochat-'))
  const env = baseEnv(b, dir, { FACTORY_CHAT: '' })
  const r = spawnSync(process.execPath, [SERVER], { env, input: '', encoding: 'utf8', timeout: 10000 })
  b.srv.close()
  assert.notEqual(r.status, 0)
  assert.match(r.stderr, /CHAT_ID/)
  assert.match(r.stderr, /factory\.env/)
})

test('the Beeper transport is one module: own-chat check, read, send', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-tr-'))
  fs.writeFileSync(path.join(dir, 'token'), 't\n')
  const t = createTransport({ base: `http://127.0.0.1:${b.port}`, tokenFile: path.join(dir, 'token'), chat: SELF })
  try {
    assert.equal(await t.ownChat(), true)
    b.add('hello')
    assert.deepEqual((await t.messages()).map(m => m.text), ['hello'])
    await t.send('x')
    assert.deepEqual(b.sent, ['x'])
  } finally { b.srv.close() }
  const other = await fakeBeeper('+819999999999')
  const t2 = createTransport({ base: `http://127.0.0.1:${other.port}`, tokenFile: path.join(dir, 'token'), chat: SELF })
  try {
    assert.equal(await t2.ownChat(), false)
  } finally { other.srv.close() }
})

test('a Beeper base off this Mac is refused', () => {
  assert.throws(() => createTransport({ base: 'http://example.com:23373', tokenFile: '/x', chat: SELF }), /127\.0\.0\.1/)
})

// --- T22: receipt and answered marks, state for the reply guard and the unanswered Stop hook

const inboxFile = (dir, id) => path.join(dir, 'state', 'channel', 'inbox', id + '.json')
const readInbox = (dir, id) => JSON.parse(fs.readFileSync(inboxFile(dir, id), 'utf8'))

test('a new F: message is marked 👀 at once, before the session sees it, and written to the inbox state', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const ch = await startChannel(b)
  const seenAtEvent = []
  ch.client.fallbackNotificationHandler = async n => { seenAtEvent.push(b.reactions.length); ch.events.push(n.params) }
  try {
    await sleep(300)
    b.add('just a note')
    b.add('F: build a word counter')
    await sleep(500)
    const id = ch.events[0].meta.message_id
    assert.deepEqual(b.reactions, [{ id, key: '👀' }]) // notes get no mark
    assert.deepEqual(seenAtEvent, [1]) // marked without waiting for the model
    const st = readInbox(ch.dir, id)
    assert.equal(st.answered_at, null)
    assert.equal(st.stale, false)
    assert.equal(st.text, 'build a word counter')
    assert.ok(Math.abs(st.received_at - Date.now() / 1000) < 5)
    assert.deepEqual(b.sent, []) // a reaction, not an extra message
  } finally { await ch.close(); b.srv.close() }
})

test('after the reply every waiting message gets ✅, is marked answered, and the reply is logged', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const ch = await startChannel(b)
  try {
    await sleep(300)
    b.add('F: one')
    b.add('F: two')
    await sleep(500)
    const ids = ch.events.map(e => e.meta.message_id)
    const r = await ch.client.callTool({ name: 'reply', arguments: { text: 'Got it - both noted' } })
    assert.equal(r.isError, undefined)
    assert.deepEqual(b.reactions.filter(x => x.key === '✅').map(x => x.id).sort(), ids.slice().sort())
    for (const id of ids) assert.ok(readInbox(ch.dir, id).answered_at >= readInbox(ch.dir, id).received_at)
    const log = fs.readFileSync(path.join(ch.dir, 'state', 'channel', 'replies.jsonl'), 'utf8').trim().split('\n').map(JSON.parse)
    assert.equal(log.length, 1)
    assert.equal(log[0].text, 'Got it - both noted')
    // a second reply does not mark again
    await ch.client.callTool({ name: 'reply', arguments: { text: 'Nothing needed - more' } })
    assert.equal(b.reactions.filter(x => x.key === '✅').length, 2)
  } finally { await ch.close(); b.srv.close() }
})

test('messages left unanswered by an earlier channel run are marked stale on start', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-stale-'))
  fs.mkdirSync(path.join(dir, 'state', 'channel', 'inbox'), { recursive: true })
  fs.writeFileSync(inboxFile(dir, '77'), JSON.stringify({ id: '77', text: 'old', received_at: 1, answered_at: null, stale: false, stop_blocks: 0 }))
  const ch = await startChannel(b, {}, dir)
  try {
    await sleep(300)
    assert.equal(readInbox(dir, '77').stale, true)
  } finally { await ch.close(); b.srv.close() }
})

test('when reactions fail, a one-line receipt marked as the factory is posted instead', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  b.failReactions = true
  const ch = await startChannel(b)
  try {
    await sleep(300)
    b.add('F: build it')
    await sleep(500)
    assert.equal(ch.events.length, 1)
    assert.deepEqual(b.sent, ['🤖 · factory: ✓ got it'])
    assert.equal(readInbox(ch.dir, ch.events[0].meta.message_id).receipt, 'text')
    await sleep(300)
    assert.equal(ch.events.length, 1) // the receipt never comes back as a task
  } finally { await ch.close(); b.srv.close() }
})

test('the instructions carry the reply format the guard enforces', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const ch = await startChannel(b)
  try {
    const text = ch.client.getInstructions()
    assert.match(text, /Reply format/)
    assert.match(text, /You:/)
    assert.match(text, /Nothing needed/)
    assert.match(text, /700 characters/)
    assert.match(text, /10 lines/)
    assert.match(text, /File:/)
    assert.match(text, /gh pr checks/)
    assert.match(text, /sensible default/)
  } finally { await ch.close(); b.srv.close() }
})

test('the transport adds a reaction to one message', { timeout: 15000 }, async () => {
  const b = await fakeBeeper()
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-react-'))
  fs.writeFileSync(path.join(dir, 'token'), 't\n')
  const t = createTransport({ base: `http://127.0.0.1:${b.port}`, tokenFile: path.join(dir, 'token'), chat: SELF })
  try {
    await t.react('5', '👀')
    assert.deepEqual(b.reactions, [{ id: '5', key: '👀' }])
  } finally { b.srv.close() }
})
