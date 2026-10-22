#!/usr/bin/env python3
"""guard_bash: parse a Bash command and refuse destructive, secret-reading or protected-folder-writing ones.

Used by hooks/security-bash.sh (Claude Code and Codex PreToolUse, Bash) and foreman/factory_guard.py.
Standard-library Python only.

How it works (design ported from cc-safety-net, MIT, see NOTICE; T15 REFERENCE.md):
- A shell lexer (quotes, $'..', escapes, $(..), backticks, <(..), ${..}, heredocs, comments) turns the text
  into simple commands joined by ; && || | & and newlines. Words keep which parts were quoted and which came
  from expansions, so quoted text and heredoc bodies stay data.
- Nested code is checked too, up to MAX_DEPTH levels: $(..), backticks, process substitutions, `bash/sh/zsh -c`,
  `eval`, heredocs or here-strings fed to a shell or interpreter, `xargs`, `find -exec`, `ssh host cmd`, shell
  strings inside python/node/ruby/perl code, and scripts written and run in the same command.
  Deeper nesting, text that does not parse, a variable or substitution in command position, and dynamic text
  run by a shell or eval are refused (fail closed: an unchecked command is not a safe command).
- Wrappers are stripped first: sudo doas env command builtin exec nice nohup timeout time stdbuf ionice
  caffeinate chronic unbuffer xargs watch, and absolute paths (/bin/rm -> rm).
- Rules run on the resulting argv: recursive rm, find -delete / -exec rm, inline-code deleters, destructive git
  (after stripping git's global options), secret files read by any reader, environment dumps, pipes into a
  shell, destructive SQL, terraform destroy, GitHub ref deletion.
- With protected folders (factory_guard.py), writes into them by any tool are refused too.

CLI: guard_bash.py hook        PreToolUse JSON on stdin; exit 2 + "BLOCKED: ..." on stderr to refuse
     guard_bash.py check CMD   prints the verdict (for debugging)
Extra secret paths: GUARD_SECRET_PATHS (colon-separated), plus the kit's TOKEN_FILE when lib/kitenv.py exists.
"""
import fnmatch
import json
import os
import posixpath
import re
import shlex
import sys

MAX_DEPTH = 10


class ParseError(Exception):
    pass


class Blocked(Exception):
    def __init__(self, reason, kind="danger"):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


# ---------------------------------------------------------------------------------------------------------------
# Lexer

class Word:
    """One shell word. segs: list of (kind, text, quoted) with kind lit | var | cmd | arith | proc."""

    __slots__ = ("segs", "raw")

    def __init__(self):
        self.segs = []
        self.raw = ""

    def add(self, kind, text, quoted):
        if kind == "lit" and self.segs and self.segs[-1][0] == "lit" and self.segs[-1][2] == quoted:
            self.segs[-1] = ("lit", self.segs[-1][1] + text, quoted)
        else:
            self.segs.append((kind, text, quoted))

    @property
    def quoted(self):
        return any(q for _, _, q in self.segs)

    @property
    def dynamic(self):
        return any(k != "lit" for k, _, _ in self.segs)

    @property
    def subs(self):
        return [t for k, t, _ in self.segs if k in ("cmd", "proc")]

    @property
    def var_names(self):
        return [t for k, t, _ in self.segs if k == "var"]

    @property
    def literal(self):
        """The value if it has no expansions, else None."""
        if self.dynamic:
            return None
        return "".join(t for _, t, _ in self.segs)

    @property
    def has_glob(self):
        return any(k == "lit" and not q and re.search(r"[*?\[]", t) for k, t, q in self.segs)

    def unquoted_text(self):
        return "".join(t for k, t, q in self.segs if k == "lit" and not q)

    def __repr__(self):
        return f"Word({self.raw!r})"


class Redir:
    __slots__ = ("op", "fd", "target", "body", "body_quoted", "delim", "strip")

    def __init__(self, op, fd, target):
        self.op, self.fd, self.target = op, fd, target
        self.body = None
        self.body_quoted = False
        self.delim = None
        self.strip = False


OPERATORS = ("&&", "||", ";;&", ";;", ";&", "|&", "|", "&", ";", "(", ")", "\n")
REDIR_OPS = ("<<<", "<<-", "<<", "<>", "<&", ">>", ">&", ">|", "&>>", "&>", "<", ">")
META = set(" \t\n;&|()<>")
VAR_SPECIAL = set("@*#?$!-0123456789")
ANSI = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "v": "\v",
        "\\": "\\", "'": "'", '"': '"', "?": "?"}


class Lexer:
    def __init__(self, s, depth=0):
        self.s = s
        self.i = 0
        self.depth = depth
        self.pending = []  # heredoc Redirs waiting for the next newline

    def error(self, msg):
        raise ParseError(msg)

    # -- entry points
    def tokens(self, close=None):
        """Tokens until end of text, or until an unmatched `close` (")" for $(..)). Returns (tokens, end)."""
        s = self.s
        out = []
        paren = 0
        case = 0
        at_start = True
        while True:
            # blanks and line continuations
            while self.i < len(s):
                c = s[self.i]
                if c in " \t":
                    self.i += 1
                elif c == "\\" and s.startswith("\\\n", self.i):
                    self.i += 2
                else:
                    break
            if self.i >= len(s):
                if close:
                    self.error("unterminated $(")
                if self.pending:
                    self.read_heredocs()
                return out, self.i
            c = s[self.i]
            if c == "#" and at_start:
                while self.i < len(s) and s[self.i] != "\n":
                    self.i += 1
                continue
            if c == "\n":
                self.i += 1
                out.append(("op", "\n"))
                if self.pending:
                    self.read_heredocs()
                at_start = True
                continue
            if c == ")" and paren == 0 and case:
                self.i += 1
                out.append(("op", ")"))
                at_start = True
                continue
            if close == ")" and c == ")" and paren == 0:
                self.i += 1
                return out, self.i
            # redirection with an fd number, e.g. 2>&1
            m = re.match(r"(\d+)(?=[<>])", s[self.i:])
            fd = None
            if m and (self.i == 0 or s[self.i - 1] in META):
                fd = m.group(1)
                self.i += len(fd)
                c = s[self.i]
            if c in "<>" and s.startswith(("<(", ">("), self.i) and fd is None:
                w = Word()
                start = self.i
                self.i += 2
                body = self.read_paren_body()
                w.add("proc", body, False)
                w.raw = s[start:self.i]
                self.continue_word(w, start)
                out.append(("w", w))
                at_start = True
                continue
            op = next((o for o in REDIR_OPS if s.startswith(o, self.i)), None)
            if op:
                self.i += len(op)
                if op in (">&", "<&") and re.match(r"\s*(\d+|-)(?![^\s;&|()<>])", s[self.i:]):
                    m2 = re.match(r"\s*(\d+|-)", s[self.i:])
                    self.i += len(m2.group(0))
                    out.append(("redir", Redir(op, fd, None)))
                    continue
                self.skip_blanks()
                if s.startswith(("<(", ">("), self.i):  # done < <(cmd)
                    target = Word()
                    start = self.i
                    self.i += 2
                    target.add("proc", self.read_paren_body(), False)
                    target.raw = s[start:self.i]
                    out.append(("redir", Redir(op, fd, target)))
                    continue
                if self.i >= len(s) or s[self.i] in "\n;&|()<>":
                    self.error("redirection without a target")
                target = self.word()
                r = Redir(op, fd, target)
                if op in ("<<", "<<-"):
                    r.delim = "".join(t for _, t, _ in target.segs) if not target.dynamic else target.raw
                    r.delim = target.raw.replace("'", "").replace('"', "").replace("\\", "") \
                        if target.quoted else r.delim
                    r.body_quoted = target.quoted
                    r.strip = op == "<<-"
                    self.pending.append(r)
                out.append(("redir", r))
                continue
            if fd is not None:
                self.i -= len(fd)
            op = next((o for o in OPERATORS if s.startswith(o, self.i)), None)
            if op:
                if op == "(":
                    paren += 1
                elif op == ")":
                    paren -= 1
                    if paren < 0:
                        self.error("unmatched )")
                self.i += len(op)
                out.append(("op", op))
                at_start = True
                continue
            w = self.word()
            if w.literal == "case" and not w.quoted and (not out or out[-1][0] == "op"):
                case += 1
            elif w.literal == "esac" and not w.quoted and case:
                case -= 1
            out.append(("w", w))
            at_start = False

    def skip_blanks(self):
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    def read_heredocs(self):
        s = self.s
        for r in self.pending:
            lines = []
            while True:
                if self.i >= len(s):
                    break  # bash accepts end of input as the end of the heredoc
                j = s.find("\n", self.i)
                line = s[self.i:] if j < 0 else s[self.i:j]
                self.i = len(s) if j < 0 else j + 1
                check = line.lstrip("\t") if r.strip else line
                if check == r.delim:
                    break
                lines.append(check)
            r.body = "\n".join(lines) + ("\n" if lines else "")
        self.pending = []

    def read_paren_body(self):
        """After `$(` or `<(`: lex to the matching ) and return the inner text."""
        start = self.i
        sub = Lexer(self.s, self.depth + 1)
        sub.i = self.i
        _, end = sub.tokens(close=")")
        self.i = end
        return self.s[start:end - 1]

    def continue_word(self, w, start):
        """Process substitution glued to more word text, e.g. <(x)foo - rare; just read on."""
        if self.i < len(self.s) and self.s[self.i] not in META:
            more = self.word()
            w.segs.extend(more.segs)
            w.raw = self.s[start:self.i]

    def word(self):
        s = self.s
        w = Word()
        start = self.i
        while self.i < len(s):
            c = s[self.i]
            if c in META:
                break
            if c == "\\":
                if self.i + 1 >= len(s):
                    self.i += 1
                    break
                nxt = s[self.i + 1]
                self.i += 2
                if nxt != "\n":
                    w.add("lit", nxt, True)
                continue
            if c == "'":
                j = s.find("'", self.i + 1)
                if j < 0:
                    self.error("unterminated '")
                w.add("lit", s[self.i + 1:j], True)
                self.i = j + 1
                continue
            if c == '"':
                self.i += 1
                self.double_quoted(w)
                continue
            if c == "$":
                self.dollar(w, quoted=False)
                continue
            if c == "`":
                w.add("cmd", self.backtick(), False)
                continue
            if c == "~" and self.i == start:
                m = re.match(r"~([A-Za-z0-9_.-]*)", s[self.i:])
                user = m.group(1)
                self.i += len(m.group(0))
                if user == "" and (self.i >= len(s) or s[self.i] in "/:" or s[self.i] in META):
                    w.add("var", "HOME", False)
                else:
                    w.add("lit", m.group(0), False)
                continue
            # assignment value tilde: NAME=~/x
            if c == "~" and w.segs and w.segs[-1][0] == "lit" and not w.segs[-1][2] \
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\+?=", w.raw_text_so_far() if False else
                                     "".join(t for _, t, _ in w.segs)) \
                    and (self.i + 1 >= len(s) or s[self.i + 1] in "/" or s[self.i + 1] in META):
                self.i += 1
                w.add("var", "HOME", False)
                continue
            w.add("lit", c, False)
            self.i += 1
        w.raw = s[start:self.i]
        if not w.segs:
            w.add("lit", "", True)
        return w

    def double_quoted(self, w):
        s = self.s
        while True:
            if self.i >= len(s):
                self.error('unterminated "')
            c = s[self.i]
            if c == '"':
                self.i += 1
                w.add("lit", "", True)
                return
            if c == "\\" and self.i + 1 < len(s) and s[self.i + 1] in '$`"\\\n':
                if s[self.i + 1] != "\n":
                    w.add("lit", s[self.i + 1], True)
                self.i += 2
                continue
            if c == "$":
                self.dollar(w, quoted=True)
                continue
            if c == "`":
                w.add("cmd", self.backtick(), True)
                continue
            w.add("lit", c, True)
            self.i += 1

    def backtick(self):
        s = self.s
        j = self.i + 1
        buf = []
        while j < len(s):
            if s[j] == "\\" and j + 1 < len(s):
                buf.append(s[j + 1] if s[j + 1] in "`$\\" else s[j:j + 2])
                j += 2
                continue
            if s[j] == "`":
                self.i = j + 1
                return "".join(buf)
            buf.append(s[j])
            j += 1
        self.error("unterminated `")

    def dollar(self, w, quoted):
        s = self.s
        nxt = s[self.i + 1] if self.i + 1 < len(s) else ""
        if nxt == "'" and not quoted:
            self.i += 2
            w.add("lit", self.ansi_c(), True)
            return
        if nxt == '"' and not quoted:
            self.i += 2
            self.double_quoted(w)
            return
        if s.startswith("$((", self.i):
            depth, j = 0, self.i + 1
            while j < len(s):
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= len(s):
                self.error("unterminated $((")
            w.add("arith", s[self.i + 3:j - 1], quoted)
            self.i = j + 1
            return
        if nxt == "(":
            self.i += 2
            w.add("cmd", self.read_paren_body(), quoted)
            return
        if nxt == "{":
            depth, j = 0, self.i + 1
            while j < len(s):
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                elif s[j] == "\\":
                    j += 1
                j += 1
            if j >= len(s):
                self.error("unterminated ${")
            inner = s[self.i + 2:j]
            m = re.match(r"[#!]?([A-Za-z_][A-Za-z0-9_]*|[@*#?$!0-9-])", inner)
            name = m.group(1) if m else inner
            # ${x:-$(cmd)} and friends: substitutions inside are run
            for sub in re.findall(r"\$\(([^()]*)\)", inner):
                w.add("cmd", sub, quoted)
            w.add("var", name if (m and m.end() == len(inner)) or not m else name + "?", quoted)
            self.i = j + 1
            return
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", s[self.i + 1:])
        if m:
            w.add("var", m.group(0), quoted)
            self.i += 1 + len(m.group(0))
            return
        if nxt and nxt in VAR_SPECIAL:
            w.add("var", nxt, quoted)
            self.i += 2
            return
        w.add("lit", "$", quoted)
        self.i += 1

    def ansi_c(self):
        s = self.s
        out = []
        while self.i < len(s):
            c = s[self.i]
            if c == "'":
                self.i += 1
                return "".join(out)
            if c == "\\" and self.i + 1 < len(s):
                n = s[self.i + 1]
                if n in ANSI:
                    out.append(ANSI[n])
                    self.i += 2
                    continue
                m = re.match(r"x([0-9a-fA-F]{1,2})", s[self.i + 1:])
                if m:
                    out.append(chr(int(m.group(1), 16)))
                    self.i += 1 + len(m.group(0))
                    continue
                m = re.match(r"[0-7]{1,3}", s[self.i + 1:])
                if m:
                    out.append(chr(int(m.group(0), 8)))
                    self.i += 1 + len(m.group(0))
                    continue
                m = re.match(r"u([0-9a-fA-F]{1,4})", s[self.i + 1:])
                if m:
                    out.append(chr(int(m.group(1), 16)))
                    self.i += 1 + len(m.group(0))
                    continue
                out.append(n)
                self.i += 2
                continue
            out.append(c)
            self.i += 1
        self.error("unterminated $'")


def lex(text, depth=0):
    toks, _ = Lexer(text, depth).tokens()
    return toks


def dq_subs(body):
    """Command substitutions inside an unquoted-delimiter heredoc body (they run). Parse errors: none found."""
    lx = Lexer(body + '"')
    w = Word()
    try:
        lx.double_quoted(w)
    except (ParseError, IndexError):
        return []
    return w.subs


# ---------------------------------------------------------------------------------------------------------------
# Parser: tokens -> pipelines of simple commands

class Command:
    __slots__ = ("words", "redirs", "assigns")

    def __init__(self):
        self.words, self.redirs, self.assigns = [], [], []

    def empty(self):
        return not self.words and not self.redirs and not self.assigns


ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?\+?=")
PREFIX_KEYWORDS = {"if", "then", "elif", "else", "do", "while", "until", "!", "time", "{", "coproc"}
CLOSE_KEYWORDS = {"fi", "done", "esac", "}"}


def is_assignment(w):
    if not w.segs or w.segs[0][2] or w.segs[0][0] != "lit":
        return None
    m = ASSIGN.match(w.segs[0][1])
    return m.group(1) if m else None


def parse(tokens):
    """Returns a list of pipelines; a pipeline is a list of Commands. Control keywords are dropped."""
    pipelines = [[]]
    cur = Command()
    case_mode = 0
    skip_until_sep = False  # for/select headers

    def end_cmd():
        nonlocal cur
        if not cur.empty():
            pipelines[-1].append(cur)
        cur = Command()

    def end_pipeline():
        end_cmd()
        if pipelines[-1]:
            pipelines.append([])

    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t[0] == "op":
            op = t[1]
            if op in ("|", "|&"):
                end_cmd()
            else:
                end_pipeline()
                skip_until_sep = False
                if case_mode and op in (";;", ";&", ";;&", "\n", ";"):
                    pass
            i += 1
            # case pattern: after `in` or `;;`, skip "pat | pat )"
            if case_mode and t[1] in (";;", ";&", ";;&", "\n", ";", "(") or (case_mode and t[1] == "in"):
                i = skip_case_pattern(tokens, i)
            continue
        if t[0] == "redir":
            cur.redirs.append(t[1])
            i += 1
            continue
        w = t[1]
        lit = w.literal
        if skip_until_sep:
            if lit == "do":
                skip_until_sep = False
                end_pipeline()
            else:
                cur.assigns.append(("_for", w))  # keep its substitutions checked
            i += 1
            continue
        if not cur.words:
            if lit in PREFIX_KEYWORDS and not w.quoted:
                if lit == "{":
                    end_pipeline()
                i += 1
                continue
            if lit in CLOSE_KEYWORDS and not w.quoted:
                if lit == "esac":
                    case_mode = max(0, case_mode - 1)
                end_pipeline()
                i += 1
                continue
            if lit in ("for", "select") and not w.quoted:
                skip_until_sep = True
                i += 1
                continue
            if lit == "case" and not w.quoted:
                case_mode += 1
                # case WORD in
                j = i + 1
                while j < n and not (tokens[j][0] == "w" and tokens[j][1].literal == "in"):
                    if tokens[j][0] == "w":
                        cur.assigns.append(("_case", tokens[j][1]))
                    j += 1
                end_pipeline()
                i = skip_case_pattern(tokens, j + 1)
                continue
            if lit == "function" and not w.quoted:
                i += 2  # function NAME
                if i < n and tokens[i] == ("op", "(") and i + 1 < n and tokens[i + 1] == ("op", ")"):
                    i += 2
                continue
            # NAME() { ...; }
            if i + 2 < n and tokens[i + 1] == ("op", "(") and tokens[i + 2] == ("op", ")"):
                i += 3
                continue
            name = is_assignment(w)
            if name:
                cur.assigns.append((name, w))
                i += 1
                continue
        cur.words.append(w)
        i += 1
    end_cmd()
    return [p for p in pipelines if p]


def skip_case_pattern(tokens, i):
    """At the start of a case item: skip newlines, then `[(] pat [| pat] )` if it is there."""
    n = len(tokens)
    while i < n and tokens[i] == ("op", "\n"):
        i += 1
    j = i
    if j < n and tokens[j] == ("op", "("):
        j += 1
    saw_word = False
    while j < n:
        t = tokens[j]
        if t[0] == "w":
            if t[1].literal == "esac" and not saw_word:
                return i
            saw_word = True
            j += 1
        elif t == ("op", "|"):
            j += 1
        elif t == ("op", ")") and saw_word:
            return j + 1
        else:
            return i
    return i


# ---------------------------------------------------------------------------------------------------------------
# Secrets

SECRET_DIRS = (".ssh", ".aws", ".gnupg")
SECRET_DIR_PAIRS = ((".config", "gh"), (".docker", "config.json"), (".kube", "config"))
SECRET_NAMES = {".netrc", ".pgpass", ".git-credentials", ".npmrc", ".pypirc", "credentials.json",
                "secrets.json", "secrets.yaml", "secrets.yml", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
                ".secrets", "service-account.json"}
SECRET_EXT = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk")
ENV_OK = ("example", "sample", "template", "dist", "defaults")
GLOB_PROBES = (".env", ".env.local", ".netrc", "id_rsa", "id_ed25519", "x.pem", "x.key")
SECRET_WORDS = re.compile(r"(?i)(key|token|secret|passw|credential|private|bearer|auth)")
SECRET_VAR = re.compile(r"(?i)(KEY|SECRET|TOKEN|PASSW|CREDENTIAL|PRIVATE)")


def env_secret(base):
    if base == ".env":
        return True
    if base.startswith(".env."):
        return base.split(".")[2].lower() not in ENV_OK
    return False


def name_secret(base):
    if env_secret(base) or base in SECRET_NAMES:
        return True
    if base.endswith(".pub"):
        return False
    if re.match(r"id_(rsa|dsa|ecdsa|ed25519)(_|$)", base):
        return True
    return base.lower().endswith(SECRET_EXT)


def path_secret(path, extra=(), cwd=None, home=None):
    """True if this path names a secret file or folder."""
    if not path:
        return False
    p = path
    if home and (p == "~" or p.startswith("~/")):
        p = home + p[1:]
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts:
        return False
    base = parts[-1]
    if base.endswith(".pub") or base == "known_hosts":
        return False
    for d in SECRET_DIRS:
        if d in parts[:-1] or (base == d):
            return True
    for a, b in SECRET_DIR_PAIRS:
        for k in range(len(parts) - 1):
            if parts[k] == a and parts[k + 1] == b:
                return True
    if name_secret(base):
        return True
    if extra:
        full = p if p.startswith("/") else (posixpath.join(cwd, p) if cwd else None)
        if full:
            full = posixpath.normpath(full)
            for e in extra:
                if full == e or full.startswith(e.rstrip("/") + "/"):
                    return True
    return False


def glob_secret(pattern):
    base = pattern.rstrip("/").split("/")[-1]
    if len(re.sub(r"[*?\[\]]", "", base)) < 2:
        return False  # a bare * or ?? names everything; secrets are caught by name when they are named
    for probe in GLOB_PROBES:
        if probe.startswith(".") and not base.startswith("."):
            continue
        if fnmatch.fnmatchcase(probe, base):
            return True
    return False


def extra_secret_paths():
    out = [p for p in os.environ.get("GUARD_SECRET_PATHS", "").split(":") if p.strip()]
    lib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
    if os.path.isfile(os.path.join(lib, "kitenv.py")):
        try:
            sys.path.insert(0, lib)
            import kitenv  # noqa: E402
            cfg = kitenv.load()
            if cfg.get("TOKEN_FILE"):
                out.append(cfg["TOKEN_FILE"])
        except Exception:  # the guard must not depend on a readable config
            pass
        finally:
            if sys.path and sys.path[0] == lib:
                sys.path.pop(0)
    return [posixpath.normpath(os.path.expanduser(p)) for p in out]


# ---------------------------------------------------------------------------------------------------------------
# Inline code (python -c, node -e, ...)

CODE_DELETE = re.compile(
    r"rmtree|\brm_rf\b|\brm_r\b|remove_dir_all|\brmSync\b|\brmdirSync\b|\bunlinkSync\b|\bunlink\s*\(|\bunlink\s+['\"$]"
    r"|\bos\.remove\b|\bos\.rmdir\b|removedirs|\.unlink\s*\(|FileUtils\.(rm|remove)|\bfs\.(rm|rmdir|unlink)\b"
    r"|fs\.promises\.(rm|unlink)|\bFile\.delete\b|\bFiles\.delete|Deno\.remove|\bshutil\.move\b(?=.*\.git)")
CODE_READ = re.compile(r"open\s*\(|read|cat\b|load_dotenv|dotenv|exec|system|popen|subprocess|spawn|File\.|fs\.|"
                       r"\bIO\.|slurp|<\s*\S|copy|shutil|require\(|import\s|source|Path\(")
CODE_SHELL = re.compile(r"os\.system|subprocess|popen|execSync|execFileSync|spawnSync|\bspawn\(|\bexec\(|"
                        r"child_process|\bsystem\s*\(|`|%x\{|Open3|IO\.popen|\bsh\s*\(|commands\.getoutput")
CODE_WRITE = re.compile(r"""open\s*\([^)]*['"](w|a|x|r\+|wb|ab|w\+|a\+)['"]|\bwrite|writeFile|appendFile|"""
                        r"""\bunlink|\bremove|rmtree|rename|replace\(|\bmkdir|touch|copy|move|chmod|symlink|"""
                        r"""truncate|File\.open\([^)]*['"][wa]|>>?\s*['"$/]|\bdump\(|\bsave\(|-i\b""")
STRING_LIT = re.compile(r"""'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`([^`]*)`""")
STRING_LIST = re.compile(r"""\[\s*((?:(?:'[^'\n]*'|"[^"\n]*")\s*,\s*)+(?:'[^'\n]*'|"[^"\n]*")\s*,?)\s*\]""")
INTERPRETERS = {"python", "python3", "python2", "pypy", "pypy3", "node", "nodejs", "deno", "bun", "ruby", "perl",
                "php", "lua", "Rscript", "osascript", "uv", "jshell"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "fish", "ash", "busybox"}


def code_strings(code):
    out = []
    for m in STRING_LIT.finditer(code):
        s = next(g for g in m.groups() if g is not None)
        out.append(s.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\"))
    for m in STRING_LIST.finditer(code):
        items = re.findall(r"""'([^'\n]*)'|"([^"\n]*)\"""", m.group(1))
        out.append(" ".join(shlex.quote(a or b) for a, b in items))
    return out


# ---------------------------------------------------------------------------------------------------------------
# Analysis

READ_FREE = {"ls", "test", "[", "[[", "stat", "file", "du", "realpath", "readlink", "dirname", "basename", "touch",
             "chmod", "chown", "chgrp", "mkdir", "rm", "rmdir", "mv", "wc", "which", "type", "echo", "printf",
             "print", "true", "false", ":", "exit", "return", "cd", "pushd", "popd", "export", "unset", "local",
             "declare", "typeset", "readonly", "trap", "shift", "set", "hash", "alias", "unalias", "lsof",
             "xattr", "getfattr", "shasum", "sha256sum", "md5", "md5sum", "cksum", "b2sum", "ssh-add", "sleep",
             "kill", "wait", "jobs", "fg", "bg", "umask", "ulimit", "history", "fc", "help", "man", "info",
             "whereis", "command", "builtin"}
GIT_SECRET_OK = {"status", "check-ignore", "ls-files", "rm", "mv", "check-attr"}
DB_CLIENTS = {"psql", "mysql", "mariadb", "sqlite3", "duckdb", "sqlcmd", "clickhouse-client", "cockroach", "pgcli",
              "mycli", "litecli", "supabase", "turso", "mongosh", "redis-cli"}
DESTRUCTIVE_SQL = re.compile(r"(?i)\b(DROP\s+(TABLE|DATABASE|SCHEMA|VIEW)|TRUNCATE(\s+TABLE)?\s+\w|DELETE\s+FROM)\b")
SAFE_CMD_VARS = {"HOME", "PWD", "TMPDIR"}
GIT_READ_ONLY = {"status", "log", "diff", "show", "rev-parse", "ls-files", "cherry", "merge-base", "blame", "grep",
                 "describe", "help", "version", "shortlog", "rev-list", "cat-file", "ls-tree", "ls-remote",
                 "for-each-ref", "name-rev", "show-ref", "whatchanged", "count-objects", "check-ignore",
                 "var", "verify-commit", "range-diff", "annotate"}
GIT_EXEC_KEYS = re.compile(r"^(alias\..*|core\.(sshcommand|pager|editor|hookspath|fsmonitor|gitproxy|askpass)|"
                           r"sequence\.editor|diff\.external|gpg\.(.*\.)?program|credential\..*helper|include\.path|"
                           r"includeif\..*|filter\..*|diff\..*\.(textconv|command)|merge\..*\.driver|"
                           r"uploadpack\.packobjectshook|protocol\..*allow|remote\..*\.(receivepack|uploadpack|vcs|"
                           r"mirror)|core\.worktree|pager\..*|interactive\.difffilter|ssh\.variant)$")
SAFE_PAGER = {"cat", "less", "more", "less -r", "less -R", "less -FRX", "true", "", "vim", "vi", "nano", ":"}


class Ctx:
    def __init__(self, cwd=None, home=None, protected=(), extra=(), check_danger=True):
        self.cwd = cwd
        self.home = home or os.environ.get("HOME") or os.path.expanduser("~")
        self.env = {}            # literal assignments seen in this command
        self.unknown_env = set()
        self.written = {}        # path -> ("heredoc", body) | ("other", None)
        self.protected = [posixpath.normpath(p) for p in protected]
        self.extra = list(extra)
        self.check_danger = check_danger
        self.hits = []           # protected-folder hits (factory_guard)

    # -- word resolution
    def var(self, name):
        if name in self.env:
            return self.env[name]
        if name == "HOME":
            return self.home
        if name == "PWD" and self.cwd:
            return self.cwd
        return None

    def value(self, w):
        """(text, fully_known). Unknown expansions become ""."""
        out, known = [], True
        for kind, text, _ in w.segs:
            if kind == "lit":
                out.append(text)
            elif kind == "var":
                v = self.var(text)
                if v is None:
                    known = False
                else:
                    out.append(v)
            else:
                known = False
        return "".join(out), known

    def path(self, text):
        """Absolute normalised path for a resolved word, or None when the folder is unknown."""
        if not text:
            return None
        if text == "~" or text.startswith("~/"):
            text = self.home + text[1:]
        if not text.startswith("/"):
            if not self.cwd:
                return None
            text = posixpath.join(self.cwd, text)
        return posixpath.normpath(text)

    def in_protected(self, p):
        return any(p == d or p.startswith(d + "/") for d in self.protected)


def danger(reason):
    raise Blocked(reason, "danger")


def unsure(reason):
    """Refused because it can't be checked (fail closed), not because it is known to be destructive."""
    raise Blocked(reason, "unsure")


def brace_expand(text):
    m = re.search(r"\{([^{}]*,[^{}]*)\}", text)
    if not m:
        return [text]
    out = []
    for alt in m.group(1).split(","):
        out.extend(brace_expand(text[:m.start()] + alt + text[m.end():]))
    return out


class Analyzer:
    def __init__(self, ctx):
        self.ctx = ctx

    # ---- scripts
    def script(self, text, depth):
        if depth > MAX_DEPTH:
            unsure(f"command nests shells/eval more than {MAX_DEPTH} levels deep; it can't be checked")
        try:
            toks = Lexer(text, depth).tokens()[0]
        except ParseError as e:
            unsure(f"the command can't be parsed ({e}); unparseable commands are not run")
        except RecursionError:
            unsure("the command nests too deeply to check")
        for pipeline in parse(toks):
            for k, cmd in enumerate(pipeline):
                self.command(cmd, depth, piped_in=k > 0, pipeline=pipeline)

    def subs_of(self, w, depth):
        for body in w.subs:
            self.script(body, depth + 1)

    # ---- one simple command
    def command(self, cmd, depth, piped_in=False, pipeline=()):
        ctx = self.ctx
        for name, w in cmd.assigns:
            self.subs_of(w, depth)
        for w in cmd.words:
            self.subs_of(w, depth)
        stdin_text, stdin_quoted = None, True
        for r in cmd.redirs:
            if r.target is not None:
                self.subs_of(r.target, depth)
            if r.op in ("<<", "<<-"):
                stdin_text, stdin_quoted = r.body or "", r.body_quoted
                if not r.body_quoted:
                    for body in dq_subs(r.body or ""):
                        self.script(body, depth + 1)
            elif r.op == "<<<":
                stdin_text, stdin_quoted = ctx.value(r.target)[0], not r.target.dynamic
                if r.target.dynamic and not r.target.literal:
                    stdin_quoted = False
        # assignments (only when there is no command: `x=1; ...`)
        if not cmd.words:
            for name, w in cmd.assigns:
                if name.startswith("_"):
                    continue
                v, known = ctx.value(w)
                v = v.split("=", 1)[1] if "=" in v else ""
                if known:
                    ctx.env[name] = v
                else:
                    ctx.env.pop(name, None)
                    ctx.unknown_env.add(name)
        else:
            for name, w in cmd.assigns:
                if name in ("GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
                    danger("git config injected through the environment can run commands")
        # redirections
        written_here = []
        for r in cmd.redirs:
            if r.target is None or r.op in ("<<", "<<-", "<<<"):
                continue
            text, _ = ctx.value(r.target)
            if r.op in ("<", "<>", "<&"):
                self.secret_read(r.target, "<")
            if r.op in (">", ">>", ">|", "&>", "&>>", "<>", ">&"):
                self.protected_write(text, f"redirection {r.op} {r.target.raw}")
                written_here.append(text)
        argv = list(cmd.words)
        content = ("other", None)
        if argv:
            first = ctx.value(argv[0])[0]
            base = first.rsplit("/", 1)[-1]
            if base == "cat" and stdin_text is not None and len(argv) == 1:
                content = ("heredoc", stdin_text) if stdin_quoted else ("heredoc", stdin_text)
            if base == "tee" and stdin_text is not None:
                content = ("heredoc", stdin_text)
        for p in written_here:
            ap = ctx.path(p)
            if ap:
                ctx.written[ap] = content
        if argv:
            self.argv(argv, depth, piped_in=piped_in, stdin_text=stdin_text, stdin_quoted=stdin_quoted,
                      content=content, pipeline=pipeline)

    # ---- argv after unwrapping
    def argv(self, words, depth, piped_in=False, stdin_text=None, stdin_quoted=True, content=("other", None),
             pipeline=()):
        ctx = self.ctx
        if depth > MAX_DEPTH:
            unsure(f"command nests wrappers more than {MAX_DEPTH} levels deep; it can't be checked")
        words = list(words)
        while words:
            w0 = words[0]
            if w0.dynamic:
                names = w0.var_names
                if w0.subs or any(n not in SAFE_CMD_VARS for n in names) or not w0.segs[0][0] == "var":
                    unsure(f"a variable or substitution in command position ({w0.raw}) hides what runs")
            name_text = ctx.value(w0)[0]
            name = name_text.rsplit("/", 1)[-1] if "/" in name_text else name_text
            args = words[1:]
            vals = [ctx.value(a)[0] for a in args]
            # --- wrappers
            if name in ("sudo", "doas"):
                words = self.skip_opts(args, vals, with_arg={"-u", "-g", "-U", "-C", "-D", "-h", "-p", "-r", "-t",
                                                              "-T", "--user", "--group", "--chdir", "--prompt"})
                continue
            if name == "env":
                rest, k = [], 0
                while k < len(args):
                    v = vals[k]
                    if v in ("-u", "--unset", "-C", "--chdir"):
                        k += 2
                        continue
                    if v in ("-S", "--split-string") and k + 1 < len(args):
                        return self.script(vals[k + 1] + " " + " ".join(shlex.quote(x) for x in vals[k + 2:]),
                                           depth + 1)
                    if v.startswith("-S"):
                        return self.script(v[2:] + " " + " ".join(shlex.quote(x) for x in vals[k + 1:]), depth + 1)
                    if v == "--":
                        k += 1
                        break
                    if v.startswith("-") and v != "-":
                        k += 1
                        continue
                    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", v):
                        k += 1
                        continue
                    break
                rest = args[k:]
                if not rest:
                    danger("`env` without a command prints every environment variable (secrets included)")
                words = rest
                continue
            if name == "command":
                if any(v in ("-v", "-V") for v in vals[:2]):
                    return
                words = self.skip_opts(args, vals, set())
                continue
            if name in ("builtin", "nohup", "chronic", "unbuffer", "noglob", "exec", "time", "caffeinate",
                        "nice", "timeout", "stdbuf", "ionice", "gtimeout", "setsid", "arch"):
                with_arg = {"exec": {"-a"}, "nice": {"-n", "--adjustment"}, "timeout": {"-s", "-k", "--signal",
                            "--kill-after"}, "gtimeout": {"-s", "-k"}, "ionice": {"-c", "-n", "-p", "-t"},
                            "caffeinate": {"-t", "-w"}, "stdbuf": {"-i", "-o", "-e"}}.get(name, set())
                words = self.skip_opts(args, vals, with_arg)
                if name in ("timeout", "gtimeout") and words:
                    words = words[1:]  # the duration
                if name == "nice" and words and re.fullmatch(r"-\d+", ctx.value(words[0])[0]):
                    words = words[1:]
                continue
            if name == "xargs":
                words = self.skip_opts(args, vals, {"-I", "-L", "-n", "-P", "-s", "-d", "-E", "-a", "--arg-file",
                                                    "--delimiter", "--max-args", "--max-procs", "--replace",
                                                    "--max-lines", "--eof"}, attached={"-I", "-i", "-L", "-n",
                                                                                     "-P", "-s", "-d", "-E", "-l",
                                                                                     "-e"})
                if not words:
                    return
                continue
            if name == "watch":
                rest = self.skip_opts(args, vals, {"-n", "--interval", "-d", "--differences"})
                if rest:
                    return self.script(" ".join(ctx.value(w)[0] for w in rest), depth + 1)
                return
            break
        if not words:
            return
        w0 = words[0]
        name_text = ctx.value(w0)[0]
        name = name_text.rsplit("/", 1)[-1]
        args = words[1:]
        vals = [ctx.value(a)[0] for a in args]

        # scripts written earlier in this same command
        if "/" in name_text or name_text in ctx.written:
            p = ctx.path(name_text)
            if p and p in ctx.written:
                self.run_written(p, depth)
                return

        if name in SHELLS:
            return self.shell(name, args, vals, depth, piped_in, stdin_text, stdin_quoted)
        if name == "eval":
            if any(a.dynamic for a in args):
                unsure("eval of text built at run time can't be checked")
            return self.script(" ".join(vals), depth + 1)
        if name in ("source", "."):
            for a, v in zip(args, vals):
                self.secret_read(a, name)
            if args:
                p = ctx.path(vals[0])
                if p and p in ctx.written:
                    self.run_written(p, depth)
            return
        if name == "ssh":
            rest = self.skip_opts(args, vals, set("-B -b -c -D -E -e -F -I -i -J -L -l -m -O -o -p -Q -R -S -W -w"
                                                  .split()))
            for a in args:
                self.secret_read(a, "ssh")
            if len(rest) > 1:
                return self.script(" ".join(ctx.value(w)[0] for w in rest[1:]), depth + 1)
            return
        if name in INTERPRETERS or re.fullmatch(r"python\d(\.\d+)?", name):
            return self.interpreter(name, args, vals, depth, piped_in, stdin_text)
        if name == "find":
            return self.find(args, vals, depth)
        if name == "git":
            return self.git(args, vals, depth)
        if name == "cd" or name == "pushd":
            self.cd(vals)
            return
        if name in ("apply_patch", "applypatch"):
            self.patch(stdin_text or " ".join(vals))
            return
        self.rules(name, args, vals, depth, piped_in, stdin_text, pipeline)

    def skip_opts(self, args, vals, with_arg, attached=()):
        k = 0
        while k < len(args):
            v = vals[k]
            if v == "--":
                return args[k + 1:]
            if not v.startswith("-") or v == "-":
                break
            if v in with_arg or (v.startswith("--") and v.split("=")[0] in with_arg and "=" not in v):
                k += 2
                continue
            k += 1
        return args[k:]

    def run_written(self, p, depth):
        kind, body = self.ctx.written[p]
        if kind == "heredoc":
            return self.script(body, depth + 1)
        danger("runs a script that this same command wrote; the script can't be checked")

    # ---- shells, interpreters
    def shell(self, name, args, vals, depth, piped_in, stdin_text, stdin_quoted):
        k, script_flag = 0, False
        while k < len(args):
            v = vals[k]
            if v == "--":
                k += 1
                break
            if v in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
                k += 2
                continue
            if v.startswith("-") and len(v) > 1 and not v.startswith("--"):
                if "c" in v[1:]:
                    script_flag = True
                k += 1
                continue
            if v.startswith("--"):
                k += 1
                continue
            break
        rest, rvals = args[k:], vals[k:]
        if script_flag:
            if not rest:
                return
            w = rest[0]
            if w.dynamic and (w.subs or any(self.ctx.var(n) is None for n in w.var_names)):
                unsure(f"`{name} -c` runs text built at run time; it can't be checked")
            return self.script(rvals[0], depth + 1)
        if rest and rvals[0] != "-":
            for a in rest:
                self.secret_read(a, name)
            p = self.ctx.path(rvals[0])
            if p and p in self.ctx.written:
                return self.run_written(p, depth)
            return  # a script file on disk: not visible to this guard (the sandbox covers builds)
        if stdin_text is not None:
            return self.script(stdin_text, depth + 1)
        if piped_in:
            danger(f"piping text into `{name}` runs code that can't be checked")

    def interpreter(self, name, args, vals, depth, piped_in, stdin_text):
        code, k, inplace, module, script = None, 0, False, False, None
        while k < len(args):
            v = vals[k]
            if name.startswith(("python", "pypy")) and v == "-c" and k + 1 < len(args):
                code = vals[k + 1]
                k += 2
                break
            if name.startswith(("python", "pypy")) and v.startswith("-c") and len(v) > 2:
                code = v[2:]
                k += 1
                break
            if name.startswith(("python", "pypy")) and v == "-m":
                module = True
                k += 2
                break
            if name.startswith(("python", "pypy")) and v in ("-W", "-X", "-Q"):
                k += 2
                continue
            if name in ("node", "nodejs", "bun", "deno") and v in ("-e", "--eval", "-p", "--print") \
                    and k + 1 < len(args):
                code = vals[k + 1]
                k += 2
                break
            if name in ("node", "nodejs") and v in ("-r", "--require", "--import", "--loader"):
                k += 2
                continue
            if name == "deno" and v == "eval" and k + 1 < len(args):
                code = vals[k + 1]
                k += 2
                break
            if name in ("ruby", "perl") and v.startswith("-") and not v.startswith("--") and len(v) > 1:
                flags = v[1:]
                if "i" in flags:
                    inplace = True
                if "e" in flags or "E" in flags:
                    tail = flags[max(flags.find("e"), flags.find("E")) + 1:]
                    if tail:
                        code = (code + "\n" if code else "") + tail
                        k += 1
                    elif k + 1 < len(args):
                        code = (code + "\n" if code else "") + vals[k + 1]
                        k += 2
                    else:
                        k += 1
                    continue
                k += 1
                continue
            if name == "php" and v == "-r" and k + 1 < len(args):
                code = vals[k + 1]
                k += 2
                break
            if name == "osascript" and v == "-e" and k + 1 < len(args):
                code = (code or "") + "\n" + vals[k + 1]
                k += 2
                continue
            if name == "uv" and v == "run":
                return self.argv(args[k + 1:], depth + 1) if args[k + 1:] else None
            if v.startswith("-") and v != "-":
                k += 1
                continue
            break
        rest, rvals = args[k:], vals[k:]
        if code is None and not module:
            if rvals and rvals[0] != "-":
                script = rvals[0]
                self.secret_read(rest[0], name)
                p = self.ctx.path(script)
                if p and p in self.ctx.written:
                    kind, body = self.ctx.written[p]
                    if kind == "heredoc":
                        self.code(body, depth)
                    else:
                        danger("runs a script that this same command wrote; the script can't be checked")
                rest, rvals = rest[1:], rvals[1:]
            elif stdin_text is not None:
                code = stdin_text
            elif piped_in:
                danger(f"piping text into `{name}` runs code that can't be checked")
        if code is not None:
            self.code(code, depth)
        for a in rest:
            self.secret_read(a, name)
            if inplace:
                self.protected_write(self.ctx.value(a)[0], f"{name} -i")

    def code(self, code, depth):
        ctx = self.ctx
        if ctx.check_danger and CODE_DELETE.search(code):
            danger("inline code deletes files (rmtree/unlink/rmSync/...); delete files individually instead")
        strings = code_strings(code)
        if ctx.check_danger and CODE_READ.search(code):
            for s in strings:
                for tok in re.split(r"[\s;|&<>()'\"]+", s) + [s]:
                    tok = tok.lstrip("@")
                    if tok and path_secret(tok, ctx.extra, ctx.cwd, ctx.home):
                        danger(f"inline code reads a secret file ({tok})", )
            if re.search(r"(?<![\w.$])\.env(?![\w.]|\.(?!" + "|".join(ENV_OK) + r"))|\.(ssh|aws|gnupg)/", code) \
                    and re.search(r"open|read|cat|File|fs\.|exec|system|spawn|popen|load|slurp|source", code):
                danger("inline code reads a secret file")
        if CODE_SHELL.search(code):
            for s in strings:
                if not s or len(s) > 4000:
                    continue
                try:
                    toks = lex(s, depth + 1)
                except (ParseError, RecursionError):
                    continue
                if any(t[0] == "w" for t in toks):
                    try:
                        self.script(s, depth + 1)
                    except Blocked as b:  # a string in code may be plain text: only a clear danger counts
                        if b.kind != "unsure":
                            raise
        if ctx.protected and CODE_WRITE.search(code):
            for s in strings:
                p = ctx.path(s) if s and ("/" in s or s.startswith("~") or ctx.cwd) else None
                if p and ctx.in_protected(p) and (s.startswith(("/", "~")) or ctx.cwd and ctx.in_protected(ctx.cwd)):
                    self.hit(f"inline code writes into a protected folder ({s})")

    # ---- find
    def find(self, args, vals, depth):
        k = 0
        while k < len(args):
            v = vals[k]
            if v == "-delete":
                danger("`find -delete` deletes files recursively")
            if v in ("-exec", "-execdir", "-ok", "-okdir"):
                j = k + 1
                inner = []
                while j < len(args) and vals[j] not in (";", "+", "\\;"):
                    inner.append(args[j])
                    j += 1
                if inner:
                    iname = self.ctx.value(inner[0])[0].rsplit("/", 1)[-1]
                    if iname in ("rm", "unlink", "shred", "rmdir", "srm", "trash"):
                        danger(f"`find {v} {iname}` deletes the files find matches")
                    self.argv(inner, depth + 1)
                k = j + 1
                continue
            if v in ("-fprint", "-fprint0", "-fprintf", "-fls"):
                if k + 1 < len(args):
                    self.protected_write(vals[k + 1], f"find {v}")
            k += 1
        for v in vals:
            if not v.startswith("-") and v not in ("(", ")", "!", ";", "+"):
                if path_secret(v, (), None, self.ctx.home) and not v.startswith("*"):
                    pass  # find lists names; it doesn't print contents

    # ---- cd
    def cd(self, vals):
        args = [v for v in vals if not (v.startswith("-") and v != "-")]
        if not args:
            self.ctx.cwd = self.ctx.home
            return
        if args[0] == "-" or args[0] == "":
            self.ctx.cwd = None
            return
        self.ctx.cwd = self.ctx.path(args[0])

    # ---- Codex apply_patch through Bash
    def patch(self, text):
        for m in re.finditer(r"^\*\*\* (?:Add File|Update File|Delete File|Move to):\s*(.+?)\s*$", text or "", re.M):
            self.protected_write(m.group(1), "apply_patch")

    # ---- secrets
    def secret_read(self, w, reader):
        ctx = self.ctx
        if not ctx.check_danger:
            return
        text, _ = ctx.value(w)
        cands = {text}
        if "=" in text:
            cands.add(text.split("=", 1)[1])
            cands.add(text.rsplit("=", 1)[1])
        for c in list(cands):
            if c.startswith("@"):
                cands.add(c[1:])
            if "=@" in c:
                cands.add(c.split("=@", 1)[1])
            if c.startswith("-") and "=" not in c and len(c) > 2 and c[1] != "-":
                pass
        for c in cands:
            if path_secret(c, ctx.extra, ctx.cwd, ctx.home):
                danger(f"`{reader}` would read a secret file ({w.raw}); secrets stay out of the session")
        if w.has_glob:
            for c in cands:
                if glob_secret(c):
                    danger(f"`{reader}` with {w.raw} matches secret files")

    # ---- protected folders
    def hit(self, why):
        self.ctx.hits.append(why)

    def protected_write(self, text, what):
        ctx = self.ctx
        if not ctx.protected or not text:
            return
        if text in ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "-"):
            return
        p = ctx.path(text)
        if p and ctx.in_protected(p):
            self.hit(f"{what} writes into a protected folder ({text})")

    # ---- git
    def git(self, args, vals, depth):
        ctx = self.ctx
        k = 0
        repo = ctx.cwd
        configs = []
        while k < len(args):
            v = vals[k]
            if v == "-c" and k + 1 < len(args):
                configs.append(vals[k + 1])
                k += 2
                continue
            if v.startswith("-c") and len(v) > 2 and not v.startswith("--"):
                configs.append(v[2:])
                k += 1
                continue
            if v == "--config-env" and k + 1 < len(args):
                configs.append(vals[k + 1].split("=")[0] + "=$env")
                k += 2
                continue
            if v.startswith("--config-env="):
                configs.append(v.split("=", 1)[1].split("=")[0] + "=$env")
                k += 1
                continue
            if v == "-C" and k + 1 < len(args):
                repo = ctx.path(vals[k + 1]) if (repo or vals[k + 1].startswith(("/", "~"))) else None
                if repo is None and vals[k + 1].startswith(("/", "~")):
                    repo = ctx.path(vals[k + 1])
                k += 2
                continue
            if v in ("--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix") \
                    and k + 1 < len(args):
                if v in ("--git-dir", "--work-tree"):
                    repo = ctx.path(vals[k + 1]) or repo
                k += 2
                continue
            if v.startswith(("--git-dir=", "--work-tree=")):
                repo = ctx.path(v.split("=", 1)[1]) or repo
                k += 1
                continue
            if v.startswith("-"):
                k += 1
                continue
            break
        sub = vals[k] if k < len(vals) else ""
        rest, rvals = args[k + 1:], vals[k + 1:]
        if ctx.check_danger:
            for c in configs:
                key, _, value = c.partition("=")
                key = key.lower()
                if key.startswith("alias."):
                    danger(f"`git -c {key}=...` defines an alias that can hide any command")
                if re.match(r"remote\..*\.push$", key) and value.startswith("+"):
                    danger("force push configured through a push refspec")
                if key == "clean.requireforce":
                    pass
                if GIT_EXEC_KEYS.match(key) and not (key in ("core.pager", "core.editor", "sequence.editor")
                                                     or key.startswith("pager.")) :
                    danger(f"`git -c {key}` can run arbitrary commands or rewrite remotes")
                if (key in ("core.pager", "core.editor", "sequence.editor") or key.startswith("pager.")) \
                        and value.strip() not in SAFE_PAGER:
                    danger(f"`git -c {key}={value}` runs a command")
            if "--no-verify" in rvals:
                danger("--no-verify skips the hooks; fix the hook issue instead")
            self.git_rules(sub, rest, rvals, depth)
            for a, v in zip(rest, rvals):
                if path_secret(v.split(":", 1)[-1] if sub == "show" else v, ctx.extra, ctx.cwd, ctx.home) \
                        and sub not in GIT_SECRET_OK:
                    danger(f"`git {sub}` with a secret file ({a.raw})")
        # protected folders: any git write whose repo is protected (factory_guard, kept from before T15)
        if ctx.protected and sub and sub not in GIT_READ_ONLY and repo and ctx.in_protected(repo):
            if not (sub == "stash" and rvals[:1] == ["list"]) and not (sub == "config" and "--get" in rvals) \
                    and not (sub == "branch" and not rvals) and not (sub == "worktree" and rvals[:1] == ["list"]):
                self.hit(f"git {sub}")

    @staticmethod
    def long_opt(v, names, minlen=2):
        """True if --v is a git-accepted abbreviation of one of names."""
        if not v.startswith("--") or len(v) < 3:
            return False
        given = v[2:].split("=", 1)[0]
        return len(given) >= minlen and any(n.startswith(given) for n in names)

    @staticmethod
    def short_has(v, letters):
        return v.startswith("-") and not v.startswith("--") and len(v) > 1 and any(c in v[1:] for c in letters)

    def git_rules(self, sub, rest, rvals, depth):
        lo, sh = self.long_opt, self.short_has
        opts = [v for v in rvals if v.startswith("-")]
        if sub == "reset":
            for v in opts:
                if lo(v, ("hard",), 2) or lo(v, ("merge",), 2) or lo(v, ("keep",), 1):
                    danger(f"`git reset {v}` throws away uncommitted work")
        elif sub == "checkout":
            if "--" in rvals and rvals.index("--") < len(rvals) - 1:
                danger("`git checkout -- <paths>` throws away uncommitted changes")
            for v in rvals:
                if v in (".", "./", "*", ":/") or lo(v, ("force",), 2) or (sh(v, "f") and not sh(v, "bBt")):
                    danger(f"`git checkout {v}` throws away uncommitted changes")
        elif sub == "restore":
            staged = any(v in ("-S", "--staged") or (sh(v, "S") and not v.startswith("--")) or lo(v, ("staged",), 3)
                         for v in opts)
            worktree = any(v in ("-W", "--worktree") or sh(v, "W") or lo(v, ("worktree",), 2) for v in opts)
            if worktree or not staged:
                danger("`git restore` on the working tree throws away uncommitted changes")
        elif sub == "switch":
            for v in opts:
                if lo(v, ("force", "discard-changes"), 2) or (sh(v, "f") and not sh(v, "cC")):
                    danger(f"`git switch {v}` throws away uncommitted changes")
        elif sub == "clean":
            dry = any(lo(v, ("dry-run",), 2) or sh(v, "n") for v in opts)
            if not dry:
                danger("`git clean` deletes untracked files; use `git clean -n` to look first")
        elif sub == "stash":
            if rvals[:1] and rvals[0] in ("drop", "clear"):
                danger(f"`git stash {rvals[0]}` deletes saved work")
        elif sub in ("push", "send-pack"):
            k = 0
            positional = []
            while k < len(rvals):
                v = rvals[k]
                if v in ("-o", "--push-option", "--repo", "--receive-pack", "--exec", "--signed"):
                    k += 2 if v not in ("--signed",) else 1
                    continue
                if v == "--":
                    positional.extend(rvals[k + 1:])
                    break
                if v.startswith("--"):
                    if lo(v, ("force", "force-with-lease"), 2):
                        danger("force push is not allowed")
                    if lo(v, ("mirror",), 2):
                        danger("mirror push is not allowed")
                    if lo(v, ("prune",), 3):
                        danger("prune push is not allowed")
                    if lo(v, ("delete",), 2):
                        danger("remote branch deletion is not allowed")
                elif v.startswith("-") and len(v) > 1:
                    letters = v[1:].split("o")[0] if "o" in v[1:] else v[1:]
                    if "f" in letters:
                        danger("force push (-f) is not allowed")
                    if "d" in letters:
                        danger("remote branch deletion is not allowed")
                    if v[1:].endswith("o"):
                        k += 1
                else:
                    positional.append(v)
                k += 1
            for v in positional[1:] if sub == "push" else positional:
                for alt in brace_expand(v):
                    if alt.startswith("+"):
                        danger("+refspec (force update) is not allowed")
                    if alt.startswith(":") and len(alt) > 1:
                        danger("remote branch deletion (:refspec) is not allowed")
            if sub == "send-pack":
                for v in positional:
                    if v.startswith("+"):
                        danger("+refspec (force update) is not allowed")
        elif sub == "branch":
            for v in opts:
                if (sh(v, "D") or (sh(v, "f") and not v.startswith("--"))) or lo(v, ("force",), 3):
                    danger("forced branch deletion/move is not allowed; use -d")
        elif sub == "update-ref":
            if "-d" in rvals or "--stdin" in rvals:
                danger("destructive update-ref is not allowed")
        elif sub == "reflog":
            if rvals[:1] and rvals[0] in ("expire", "delete"):
                danger("`git reflog expire/delete` removes the history needed to recover work")
        elif sub == "gc":
            for v in opts:
                if v.startswith("--prune") and v not in ("--prune=2.weeks.ago",):
                    danger("`git gc --prune` deletes unreachable commits now")
        elif sub in ("prune", "filter-branch", "filter-repo"):
            danger(f"`git {sub}` rewrites or deletes history")
        elif sub == "config":
            vals = [v for v in rvals if not v.startswith("-")]
            if "--get" in rvals or "--get-all" in rvals or "--list" in rvals or "-l" in rvals \
                    or "--unset" in rvals or "--get-regexp" in rvals:
                return
            if vals:
                key = vals[0].lower()
                value = " ".join(vals[1:])
                if re.match(r"remote\..*\.mirror$", key):
                    danger("remote mirror configuration is not allowed")
                if re.match(r"remote\..*\.push$", key) and value.startswith("+"):
                    danger("force push refspec in config is not allowed")
                if key == "core.hookspath":
                    danger("changing core.hooksPath turns the hooks off")
                if key.startswith("alias.") and value:
                    if value.startswith("!"):
                        self.script(value[1:], depth + 1)
                    else:
                        try:
                            parts = shlex.split(value)
                        except ValueError:
                            danger("unparseable git alias")
                        self.git_rules(parts[0] if parts else "", [], parts[1:], depth + 1)
        elif sub == "remote":
            for v in opts:
                if lo(v, ("mirror",), 2):
                    danger("remote mirror configuration is not allowed")

    # ---- generic rules
    def rules(self, name, args, vals, depth, piped_in, stdin_text, pipeline):
        ctx = self.ctx
        danger_on = ctx.check_danger
        if danger_on:
            if name == "rm":
                for v in vals:
                    if v == "--":
                        break
                    if (v.startswith("-") and not v.startswith("--") and ("r" in v or "R" in v)) \
                            or (v.startswith("--") and len(v) > 4 and "recursive".startswith(v[2:])):
                        danger("rm -r deletes folders recursively; delete files individually")
            elif name in ("shred", "srm"):
                danger(f"`{name}` destroys files")
            elif name == "unlink":
                danger("`unlink` deletes files; use rm on the single file instead")
            elif name == "rsync":
                for v in vals:
                    if v.startswith("--del") or v == "--remove-source-files":
                        danger("rsync --delete removes files at the destination")
            elif name in ("terraform", "tofu", "terragrunt"):
                if "destroy" in vals or "-destroy" in vals:
                    danger("terraform destroy is not allowed")
            elif name in ("printenv",):
                danger("printenv prints environment variables (secrets included)")
            elif name in ("export", "declare", "typeset", "set") and (not vals or vals in (["-p"], ["-x"], ["-px"])):
                if name != "set" or not vals:
                    danger(f"`{name}` without names prints the environment (secrets included)")
            elif name in ("echo", "printf", "print"):
                for a in args:
                    for n in a.var_names:
                        if SECRET_VAR.search(n):
                            danger(f"printing ${n} would show a secret")
            elif name in DB_CLIENTS:
                texts = vals + ([stdin_text] if stdin_text else [])
                for stage in pipeline:
                    texts += [ctx.value(w)[0] for w in stage.words]
                if any(DESTRUCTIVE_SQL.search(t or "") for t in texts):
                    danger("destructive SQL (DROP/TRUNCATE/DELETE FROM) is not allowed")
            elif name == "gh" and vals[:1] == ["api"]:
                method = None
                for k, v in enumerate(vals):
                    if v in ("-X", "--method") and k + 1 < len(vals):
                        method = vals[k + 1]
                    elif v.startswith("--method="):
                        method = v.split("=", 1)[1]
                    elif v.startswith("-X") and len(v) > 2:
                        method = v[2:]
                if method and method.upper() in ("DELETE", "PATCH") and any("git/refs" in v for v in vals):
                    danger("GitHub API ref deletion/forced update is not allowed")
            elif name in ("mkfs", "diskutil") and any(v in ("eraseDisk", "eraseVolume") or name == "mkfs"
                                                      for v in vals):
                danger("erasing disks is not allowed")
            # secrets: every reader except the ones that never print file contents
            if name in ("grep", "egrep", "fgrep", "rg", "ag", "ack", "ggrep"):
                self.grep(name, args, vals)
            elif name not in READ_FREE:
                for a in args:
                    self.secret_read(a, name)
        if ctx.protected:
            self.writes(name, args, vals)

    def grep(self, name, args, vals):
        quiet = recursive = False
        pattern, roots, k, pat_from_e = None, [], 0, False
        while k < len(vals):
            v = vals[k]
            if v in ("-e", "--regexp") and k + 1 < len(vals):
                pattern, pat_from_e = vals[k + 1], True
                k += 2
                continue
            if v in ("-f", "--file") and k + 1 < len(vals):
                self.secret_read(args[k + 1], name)
                k += 2
                continue
            if v in ("-A", "-B", "-C", "-m", "-g", "--glob", "-t", "--type", "-T", "--type-not", "--include",
                     "--exclude", "--exclude-dir", "-d", "-D", "--max-count", "--context", "-j", "--threads"):
                if v == "-d" and k + 1 < len(vals) and vals[k + 1] == "recurse":
                    recursive = True
                k += 2
                continue
            if v.startswith("--"):
                if v in ("--files-with-matches", "--files-without-match", "--count", "--quiet", "--silent",
                         "--files", "--count-matches"):
                    quiet = True
                if v in ("--recursive", "--dereference-recursive"):
                    recursive = True
                k += 1
                continue
            if v.startswith("-") and len(v) > 1:
                if any(c in v[1:] for c in "lLcq"):
                    quiet = True
                if any(c in v[1:] for c in "rR"):
                    recursive = True
                k += 1
                continue
            if pattern is None and not pat_from_e:
                pattern = v
            else:
                roots.append((args[k], v))
            k += 1
        if quiet:
            return
        for a, v in roots:
            self.secret_read(a, name)
        if name in ("rg", "ag", "ack"):
            recursive = True
        if recursive and pattern and SECRET_WORDS.search(pattern):
            home = self.ctx.home
            wide = {".", "./", "..", "~", "~/", "/", home, home + "/", self.ctx.cwd or "."}
            if not roots or all(v in wide for _, v in roots):
                danger(f"a recursive search for {pattern!r} over the whole folder would print secrets (.env, keys)")

    def writes(self, name, args, vals):
        pw = self.protected_write
        paths = [v for v in vals if not v.startswith("-")]
        if name == "tee":
            for v in paths:
                pw(v, "tee")
        elif name in ("sed", "gsed"):
            inplace = any(v.startswith("-i") or v.startswith("--in-place") or
                          (v.startswith("-") and not v.startswith("--") and "i" in v[1:]) for v in vals)
            if inplace:
                has_e = any(v in ("-e", "-f", "--expression", "--file") or v.startswith(("-e", "--expression="))
                            for v in vals)
                files, k, skipped_script = [], 0, has_e
                while k < len(vals):
                    v = vals[k]
                    if v in ("-e", "-f", "--expression", "--file"):
                        k += 2
                        continue
                    if v == "-i" and k + 1 < len(vals) and vals[k + 1] == "":
                        k += 2
                        continue
                    if v.startswith("-"):
                        k += 1
                        continue
                    if not skipped_script:
                        skipped_script = True
                    else:
                        files.append(v)
                    k += 1
                for v in files:
                    pw(v, "sed -i")
        elif name in ("cp", "install", "ln", "gcp"):
            t = None
            for k, v in enumerate(vals):
                if v in ("-t", "--target-directory") and k + 1 < len(vals):
                    t = vals[k + 1]
                elif v.startswith("--target-directory="):
                    t = v.split("=", 1)[1]
            if t:
                pw(t, name)
            elif paths:
                pw(paths[-1], name)
        elif name in ("mv", "touch", "mkdir", "rm", "rmdir", "unlink", "shred", "chmod", "chown", "chgrp",
                      "truncate", "chflags", "xattr", "setfacl", "ditto", "gmv"):
            for v in paths:
                pw(v, name)
        elif name == "rsync":
            if paths:
                pw(paths[-1], "rsync")
        elif name == "dd":
            for v in vals:
                if v.startswith("of="):
                    pw(v[3:], "dd")
        elif name in ("curl", "wget"):
            for k, v in enumerate(vals):
                if v in ("-o", "--output", "-O" if name == "wget" else "--x") and k + 1 < len(vals):
                    pw(vals[k + 1], name)
                elif v.startswith("--output="):
                    pw(v.split("=", 1)[1], name)
        elif name in ("tar", "unzip", "bsdtar"):
            target = None
            for k, v in enumerate(vals):
                if v in ("-C", "--directory", "-d") and k + 1 < len(vals):
                    target = vals[k + 1]
            extract = name == "unzip" or any(re.match(r"^-?[a-zA-Z]*x", v) for v in vals[:2]) or "--extract" in vals
            if extract:
                pw(target or ".", name)
        elif name == "patch":
            pw(".", "patch")
        elif name in ("perl", "ruby"):
            pass


# ---------------------------------------------------------------------------------------------------------------
# Public API

def command_text(raw):
    """Claude sends a string; Codex may send an argv list such as ["bash", "-lc", "<script>"]."""
    if isinstance(raw, list):
        return " ".join(shlex.quote(str(w)) for w in raw)
    return raw if isinstance(raw, str) else ""


def check(command, cwd=None, home=None, extra=None):
    """(blocked, reason) for the security-bash rules."""
    if not command or not command.strip():
        return False, ""
    ctx = Ctx(cwd=cwd, home=home, extra=extra_secret_paths() if extra is None else extra)
    try:
        Analyzer(ctx).script(command, 0)
    except Blocked as b:
        return True, b.reason
    except RecursionError:
        return True, "the command nests too deeply to check"
    return False, ""


def protected_writes(command, cwd, protected, home=None):
    """Reasons the command writes into a protected folder (factory_guard). Empty list = none found."""
    if not command or not command.strip() or not protected:
        return []
    ctx = Ctx(cwd=cwd, home=home, protected=protected, check_danger=False)
    try:
        Analyzer(ctx).script(command, 0)
    except Blocked as b:
        # the danger rules are off here; what remains is "can't check" (too deep / unparseable)
        ctx.hits.append(b.reason)
    except RecursionError:
        ctx.hits.append("the command nests too deeply to check")
    return ctx.hits


def hook_main():
    try:
        ev = json.load(sys.stdin)
    except ValueError:
        return 0
    inp = ev.get("tool_input") or {}
    raw = inp.get("command") if isinstance(inp, dict) else None
    text = command_text(raw)
    if not text.strip():
        return 0
    try:
        blocked, why = check(text, cwd=ev.get("cwd") or None)
    except Exception as e:  # fail closed: a guard bug must not let a command through unchecked
        blocked, why = True, f"the command guard failed ({type(e).__name__}); not run"
    if blocked:
        print(f"BLOCKED: {why}", file=sys.stderr)
        return 2
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "hook":
        return hook_main()
    if len(argv) >= 3 and argv[1] == "check":
        blocked, why = check(argv[2], cwd=os.getcwd())
        print(("BLOCK " + why) if blocked else "ALLOW")
        return 2 if blocked else 0
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
