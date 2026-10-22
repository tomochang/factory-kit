#!/usr/bin/env python3
"""scan_private: fail if the kit repo holds a personal value.

Two kinds of checks, over every tracked or to-be-added file (git ls-files -co --exclude-standard), its
path included:

1. Generic patterns: Beeper chat IDs, phone numbers, absolute home paths (/Users/<name>/...), tokens with a
   known shape. Fake values used by the tests are allowed (FAKE below).
2. Real values read at runtime from this machine's live config, never printed: the kit's factory.env
   (OWNER_NAME, CHAT_ID, TOKEN_FILE and the token in it, PROTECTED_DIRS), the user's agent configs
   (~/.claude/settings.json, ~/.claude/CLAUDE.md, ~/.codex/hooks.json, ~/.codex/AGENTS.md,
   ~/.codex/config.toml) and the scripts they point to (chat IDs, phone numbers, "<Name>-san", owner words,
   the token file next to them), the login name, git user name/email and the GitHub owner of origin.
   Folders under $HOME that hold the user's hook scripts count as private folders: "~/<folder>" in the repo
   is a finding (except in the locked task docs, which describe this very scan). The locked task docs and the
   LICENSE copyright line may name the GitHub account that owns the repo.

Output names only the file, the line and the kind of value. Exit 1 on any finding, 0 when clean.
Usage: scan_private.py [--root DIR] [--no-live] [--values FILE] [PATH ...]
"""
import argparse

import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
ROOM = re.compile(r"![A-Za-z0-9_-]{6,}:beeper\.[a-z]+")
PHONE = re.compile(r"(?<![\w.])\+\d{9,15}\b")
USERS_PATH = re.compile(r"/Users/([A-Za-z0-9._-]+)/")
SECRET = re.compile(r"(xox[abpr]-[0-9A-Za-z-]{10,}|AIza[0-9A-Za-z_-]{35}|gh[pousr]_[0-9A-Za-z]{30,}"
                    r"|sk-[A-Za-z0-9_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
# fake values the tests use on purpose
FAKE = {"!self:beeper.local", "!other:beeper.local", "+81700000000", "+819999999999", "+15550000000",
        "+15550000001"}
FAKE_USERS = {"x", "you", "me", "example", "alice", "Shared"}
GENERIC_DIRS = {".claude", ".codex", ".agents", ".config", ".local", "factory", "factory-kit", "research",
                "Library", "bin", "tmp", ".npm", ".cache"}
# locked task docs (old root names, and docs/t<n>/ since T14) may name the protected folder and the repo's own
# GitHub account; so may the LICENSE copyright line (the holder must be named, and the repo URL names it anyway)
LOCKED_DOCS = re.compile(r"^(T\d+-(KGI|SPEC|ROADMAP|REFERENCE)|docs/t\d+/(KGI|SPEC|ROADMAP|REFERENCE))\.md$")
PUBLIC_OWNER = "GitHub account"
OWNER_SET = re.compile(r"OWNER_WORDS\s*=\s*\{([^}]*)\}")
SAN = re.compile(r"\b([A-Z][a-z]{2,})-san\b")
SELF_NAME = re.compile(r":\s*([A-Z][a-z]+(?: [A-Z][a-z]+)+) \(self\)")
ABS_SCRIPT = re.compile(r"(?:~|\$HOME|%s)(/[^\s'\"`]+\.(?:py|sh|mjs))" % re.escape(str(HOME)))


def read(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def parse_env(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[m.group(1)] = v
    return out


def expand(p):
    return os.path.expanduser(os.path.expandvars(p))


class Values:
    """Real personal values, kept in memory only."""

    def __init__(self):
        self.exact = {}      # value -> kind (case-sensitive substring)
        self.words = {}      # lowercased word -> kind (whole word, case-insensitive)
        self.dirs = set()    # private folder names under $HOME

    def add(self, value, kind):
        value = (value or "").strip()
        if len(value) >= 6 and value not in FAKE:
            self.exact.setdefault(value, kind)

    def word(self, value, kind):
        value = (value or "").strip().lower()
        if len(value) >= 4 and value not in {"owner", "user", "agent", "factory", "admin", "root", "runner"}:
            self.words.setdefault(value, kind)

    def private_dir(self, path):
        try:
            rel = Path(expand(path)).resolve().relative_to(HOME.resolve())
        except (ValueError, OSError):
            return
        if rel.parts and rel.parts[0] not in GENERIC_DIRS:
            self.dirs.add(rel.parts[0])

    def harvest_text(self, text):
        for m in ROOM.finditer(text):
            self.add(m.group(0), "chat ID")
        for m in PHONE.finditer(text):
            self.add(m.group(0), "phone number")
        for m in SAN.finditer(text):
            self.word(m.group(1), "owner name")
        for m in SELF_NAME.finditer(text):
            self.add(m.group(1), "owner name")
            for w in m.group(1).split():
                self.word(w, "owner name")
        for m in OWNER_SET.finditer(text):
            for w in re.findall(r"[\"']([^\"']+)[\"']", m.group(1)):
                self.word(w, "owner name")

    def token_file(self, path):
        self.add(str(path), "token path")
        tok = read(path).strip()
        if tok:
            self.add(tok, "token")


def live_values(root, extra=None):
    v = Values()
    v.word(HOME.name, "login name")
    v.add(str(HOME) + "/", "home path")
    env_file = os.environ.get("FACTORY_ENV") or str(Path(root) / "factory.env")
    env = parse_env(read(env_file))
    if env.get("OWNER_NAME"):
        v.add(env["OWNER_NAME"], "owner name")
        for w in env["OWNER_NAME"].split():
            v.word(w, "owner name")
    v.add(env.get("CHAT_ID"), "chat ID")
    if env.get("TOKEN_FILE"):
        v.token_file(expand(env["TOKEN_FILE"]))
    for p in (env.get("PROTECTED_DIRS") or "").split(":"):
        if p.strip():
            v.private_dir(p.strip())
    configs = [HOME / ".claude/settings.json", HOME / ".claude/CLAUDE.md", HOME / ".codex/hooks.json",
               HOME / ".codex/AGENTS.md", HOME / ".codex/config.toml"]
    scripts = set()
    for c in configs:
        text = read(c)
        v.harvest_text(text)
        for m in ABS_SCRIPT.finditer(text):
            p = Path(str(HOME) + m.group(1))
            scripts.add(p)
            v.private_dir(p)
    for s in sorted(scripts):
        text = read(s)
        v.harvest_text(text)
        # a script that reads a token from <repo>/credentials/ keeps it next to itself
        for base in (s.parent, s.parent.parent):
            cand = base / "credentials" / "beeper-token"
            if cand.is_file():
                v.token_file(cand)
    for key in ("user.name", "user.email"):
        r = subprocess.run(["git", "config", "--get", key], capture_output=True, text=True, cwd=root)
        val = r.stdout.strip()
        if val:
            v.add(val, "git " + key)
            if key == "user.name":
                for w in val.split():
                    v.word(w, "git user name")
    r = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=root)
    m = re.search(r"github\.com[:/]([^/]+)/", r.stdout)
    if m:
        v.word(m.group(1), "GitHub account")
    if extra:
        for line in read(extra).splitlines():
            if line.strip() and not line.startswith("#"):
                v.add(line.strip(), "listed value")
    return v


def files(root):
    r = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], capture_output=True,
                       text=True, cwd=root)
    if r.returncode != 0:
        sys.exit("scan_private: not a git repo: %s" % root)
    return [f for f in r.stdout.split("\0") if f]


def staged(root):
    """{path: bytes} of the index (what the next commit holds) - it can differ from the working tree."""
    r = subprocess.run(["git", "ls-files", "-s", "-z"], capture_output=True, cwd=root)
    entries = [e.decode("utf-8", "replace") for e in r.stdout.split(b"\0") if e]
    blobs = [(e.split("\t", 1)[1], e.split()[1]) for e in entries if "\t" in e and not e.startswith("160000")]
    if not blobs:
        return {}
    batch = subprocess.run(["git", "cat-file", "--batch"], input="".join(h + "\n" for _, h in blobs).encode(),
                           capture_output=True, cwd=root).stdout
    out, pos = {}, 0
    for path, _ in blobs:
        nl = batch.index(b"\n", pos)
        size = int(batch[pos:nl].split()[2])
        out[path] = batch[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
    return out


def decode(raw):
    return "" if b"\0" in raw[:4096] else raw.decode("utf-8", "replace")


def scan_text(name, text, v):
    found = []
    rel = name.replace(os.sep, "/")
    locked = bool(LOCKED_DOCS.match(os.path.basename(name)) or LOCKED_DOCS.match(rel))
    licence = rel == "LICENSE"
    lines = text.splitlines() or [""]
    for i, line in enumerate([name] + lines):
        where = "%s:%s" % (name, i if i else "path")
        for m in ROOM.finditer(line):
            if m.group(0) not in FAKE:
                found.append((where, "chat ID"))
        for m in PHONE.finditer(line):
            if m.group(0) not in FAKE:
                found.append((where, "phone number"))
        for m in USERS_PATH.finditer(line):
            if m.group(1) not in FAKE_USERS:
                found.append((where, "absolute home path"))
        if SECRET.search(line):
            found.append((where, "token-shaped string"))
        for val, kind in v.exact.items():
            if val in line:
                found.append((where, kind))
        low = line.lower()
        for w, kind in v.words.items():
            if kind == PUBLIC_OWNER and (locked or (licence and line.startswith("Copyright"))):
                continue
            if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(w), low):
                found.append((where, kind))
        if not locked:
            for d in v.dirs:
                pat = r"(~|\$HOME|\$\{HOME\}|homedir\(\)\s*,\s*['\"]|home\(\)\s*/\s*['\"])/?%s\b" % re.escape(d)
                if re.search(pat, line):
                    found.append((where, "private folder path"))
    return sorted(set(found))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="files to scan instead of the repo's files")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--no-live", action="store_true", help="skip values read from this machine (tests)")
    ap.add_argument("--values", help="file with extra private values, one per line (never printed)")
    a = ap.parse_args(argv)
    v = Values() if a.no_live else live_values(a.root, a.values)
    if a.no_live and a.values:
        for line in read(a.values).splitlines():
            if line.strip():
                v.add(line.strip(), "listed value")
    names = a.paths or files(a.root)
    index = {} if a.paths else staged(a.root)
    found = []
    for n in names:
        p = Path(n) if os.path.isabs(n) or a.paths else Path(a.root) / n
        raw = b"" if p.is_symlink() or not p.is_file() else p.read_bytes()
        found += scan_text(n, decode(raw), v)
        if n in index and index[n] != raw:  # staged content differs: scan what would be committed too
            found += [(w + " (staged)", k) for w, k in scan_text(n, decode(index[n]), v)]
    for where, kind in found:
        print("FOUND %s at %s" % (kind, where))
    print("scan_private: %d files, %d real values and %d private folders checked, %d findings"
          % (len(names), len(v.exact) + len(v.words), len(v.dirs), len(found)))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
