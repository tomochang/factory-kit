// The channel's only transport in v1: the owner's own WhatsApp chat, read and written through the Beeper Desktop
// API on this Mac. Another transport can replace this module by exporting the same createTransport() shape:
//   ownChat() -> boolean, messages() -> [{id, sortKey, type, text}] oldest first, send(text), react(messageID, key)
// send() refuses unless the chat is the account's own 1:1 chat (same check as ask-me.py). react() adds a reaction
// (Beeper Desktop API 5.0.0: POST /v1/chats/{chatID}/messages/{messageID}/reactions) to a message the channel
// already read from that chat.
import fs from 'node:fs'

const LOOPBACK = new Set(['127.0.0.1', 'localhost', '[::1]', '::1'])

export function createTransport ({ base, tokenFile, chat }) {
  const u = new URL(base)
  // Beeper Desktop runs on this Mac; a base elsewhere could pass the own-chat check and post anywhere.
  if (u.protocol !== 'http:' || !LOOPBACK.has(u.hostname)) {
    throw new Error(`refused: the Beeper base must be http://127.0.0.1 (Beeper on this Mac), not ${base}`)
  }
  const chatPath = '/v1/chats/' + encodeURIComponent(chat)

  async function call (method, p, body) {
    const res = await fetch(base + p, {
      method,
      headers: { Authorization: `Bearer ${fs.readFileSync(tokenFile, 'utf8').trim()}`, 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined
    })
    if (!res.ok) throw new Error(`Beeper ${method} ${p}: HTTP ${res.status}`)
    return res.json()
  }

  async function ownChat () {
    let accounts = await call('GET', '/v1/accounts')
    if (!Array.isArray(accounts)) accounts = accounts.items || []
    const c = await call('GET', chatPath)
    const own = accounts.find(a => a.accountID === c.accountID)?.user?.phoneNumber
    const others = (c.participants?.items || []).filter(p => !p.isSelf)
    return Boolean(own) && c.type === 'single' && others.length === 1 && others[0].phoneNumber === own
  }

  async function messages () {
    const d = await call('GET', chatPath + '/messages')
    return (d.items || d).slice().sort((a, b) => Number(a.sortKey) - Number(b.sortKey))
  }

  async function send (text) {
    if (!(await ownChat())) throw new Error('refused: the configured chat is not the owner\'s own WhatsApp chat; nothing sent')
    await call('POST', chatPath + '/messages', { text })
  }

  async function react (messageID, key) {
    await call('POST', chatPath + '/messages/' + encodeURIComponent(messageID) + '/reactions', { reactionKey: key })
  }

  return { ownChat, messages, send, react }
}
