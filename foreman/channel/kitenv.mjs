// Kit settings for the channel, read the same way as lib/kitenv.py: <kit>/factory.env (or FACTORY_ENV),
// real environment wins, the scripts' older names (BEEPER_API_BASE, BEEPER_TOKEN_FILE, FACTORY_CHAT) win over
// the generic ones, and $HOME / ~ are expanded.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const KIT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')

const DEFAULTS = {
  BEEPER_BASE: 'http://127.0.0.1:23373',
  WORK_DIR: '~/factory',
  PROTECTED_DIRS: '',
  TZ: 'UTC',
  ASK_ME_STATE: '~/.factory-kit/ask-me',
  OWNER_NAME: '',
  CHAT_ID: '',
  TOKEN_FILE: ''
}
const ALIASES = { BEEPER_BASE: ['BEEPER_API_BASE'], TOKEN_FILE: ['BEEPER_TOKEN_FILE'], CHAT_ID: ['FACTORY_CHAT', 'ASK_ME_CHAT'] }
const PATHS = ['TOKEN_FILE', 'WORK_DIR', 'ASK_ME_STATE']

export function parse (text) {
  const out = {}
  for (const line of text.split('\n')) {
    const m = line.match(/^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/)
    if (!m) continue
    let v = m[2]
    if (v[0] === '"' || v[0] === "'") {
      const end = v.indexOf(v[0], 1)
      v = end > 0 ? v.slice(1, end) : v.slice(1)
    } else {
      v = v.replace(/\s+#.*$/, '')
    }
    out[m[1]] = v
  }
  return out
}

function expand (v, env) {
  const home = env.HOME || os.homedir()
  v = v.replace(/\$\{HOME\}|\$HOME/g, () => home)
  if (v === '~' || v.startsWith('~/')) v = home + v.slice(1)
  return v
}

export function load (env = process.env) {
  const file = env.FACTORY_ENV || path.join(KIT, 'factory.env')
  let values = {}
  try { values = parse(fs.readFileSync(file, 'utf8')) } catch {}
  const cfg = {}
  for (const [key, def] of Object.entries(DEFAULTS)) {
    let v
    for (const name of [...(ALIASES[key] || []), key]) if (env[name]) { v = env[name]; break }
    if (v === undefined) v = values[key] || def
    cfg[key] = expand(v, env).trim()
  }
  for (const key of PATHS) if (cfg[key]) cfg[key] = path.resolve(cfg[key])
  const dirs = cfg.PROTECTED_DIRS.split(':').map(s => s.trim()).filter(Boolean).map(p => path.resolve(expand(p, env)))
  if (!dirs.includes(KIT)) dirs.push(KIT)
  cfg.PROTECTED_DIRS = dirs
  cfg.KIT = KIT
  cfg.ENV_FILE = file
  return cfg
}

// Same placeholders as lib/render.py; an unknown one is an error.
export function renderInstructions (cfg) {
  const values = {
    KIT,
    OWNER_NAME: cfg.OWNER_NAME || 'the owner',
    WORK_DIR: cfg.WORK_DIR,
    PROTECTED_TEXT: cfg.PROTECTED_DIRS.join(' or '),
    ASK_ME: path.join(KIT, 'scripts', 'ask-me.py'),
    SAFE_PUSH: path.join(KIT, 'scripts', 'safe-push.sh'),
    CODEX_RUN: path.join(KIT, 'foreman', 'codex-run.sh'),
    CLAUDE_RUN: path.join(KIT, 'foreman', 'claude-run.sh')
  }
  const tmpl = fs.readFileSync(path.join(KIT, 'templates', 'channel-instructions.txt.tmpl'), 'utf8')
  return tmpl.replace(/\{\{([A-Z_]+)\}\}/g, (_, k) => {
    if (!(k in values)) throw new Error(`unknown placeholder {{${k}}} in channel-instructions template`)
    return values[k]
  })
}
