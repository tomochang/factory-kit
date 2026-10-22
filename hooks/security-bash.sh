#!/bin/bash
# PreToolUse hook for Bash (Claude Code and Codex): block dangerous commands.
# Exit 0 = allow, Exit 2 = block (reason on stderr). Needs jq.
# Codex may send the command as an argv list (["bash", "-lc", "..."]): it is joined and checked as text.
# Shipped with factory-kit: the generic sections 1-6 of the owner's guard.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty | if type == "array" then map(tostring) | join(" ") else tostring end')

if [ -z "$COMMAND" ]; then
  exit 0
fi

# === 1. File destruction ===
if echo "$COMMAND" | grep -qE 'rm\s+-rf\s'; then
  echo "BLOCKED: rm -rf is not allowed. Delete files individually." >&2
  exit 2
fi

# === 2. Destructive git ===
if echo "$COMMAND" | grep -qE 'git\s+push\s+.*--force'; then
  echo "BLOCKED: git push --force is not allowed." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE 'git\s+reset\s+--hard'; then
  echo "BLOCKED: git reset --hard is not allowed." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE 'git\s+checkout\s+--\s+\.'; then
  echo "BLOCKED: git checkout -- . is not allowed." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE 'git\s+clean\s+-f'; then
  echo "BLOCKED: git clean -f is not allowed." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE 'git\s+branch\s+-D'; then
  echo "BLOCKED: git branch -D is not allowed. Use -d instead." >&2
  exit 2
fi

# --- Destructive git ref changes, every spelling (accident guard; raw-text regex, fails closed).
# Spec: tests/test_security_bash.py.
# Join backslash-newline continuations, then drop the '&' of redirections such
# as 2>&1, &> and >&2 so they are not mistaken for command separators.
NORM="$(echo "$COMMAND" | sed -e ':a' -e '/\\$/{N;s/\\\n//;ba' -e '}' | sed -e 's/&>//g' -e 's/>&/>/g')"

# git, optional global options (each may take one non-dash argument), then a
# subcommand.  This makes "stash push" and "safe-push" not look like "git push".
PFX="(^|[^[:alnum:]_-])git([[:space:]]+-[^[:space:]]+([[:space:]]+[^[:space:]-][^[:space:]]*)?)*[[:space:]]+"

# Force push via --force / --force-with-lease.
if echo "$NORM" | grep -qE "${PFX}(push|send-pack)([[:space:]]|$)[^;&|]*--force"; then
  echo "BLOCKED: force push is not allowed." >&2
  exit 2
fi

# Force push via a short -f flag, including bundles (-uf, -fq) and -f>redirection.
if echo "$NORM" | grep -qE "${PFX}(push|send-pack)([[:space:]]|$)([^;&|]*[[:space:]\"'<(=])?-[[:alnum:]]*f[[:alnum:]]*([^[:alnum:]_-]|$)"; then
  echo "BLOCKED: force push (-f) is not allowed." >&2
  exit 2
fi

# Force push via an ANSI-C quoted short flag, e.g. -$'f'.
if echo "$NORM" | grep -qE "${PFX}(push|send-pack)([[:space:]]|$)([^;&|]*[[:space:]\"'<(=])?-\\\$['\"]?f"; then
  echo "BLOCKED: force push (-f) is not allowed." >&2
  exit 2
fi

# Force push via a + refspec, after any whitespace/quote/brace boundary.
if echo "$NORM" | grep -qE "${PFX}(push|send-pack)([[:space:]]|$)[^;&|]*[[:space:]\"'{,=($]['\"]?\+"; then
  echo "BLOCKED: + refspec (force update) is not allowed." >&2
  exit 2
fi

# Force push configured through a push refspec (e.g. remote.origin.push=+...).
if echo "$NORM" | grep -qE "(^|[^[:alnum:]_-])git[[:space:]][^;&|]*(^|[^[:alnum:]_-])push[=[:space:]]+['\"]?\+"; then
  echo "BLOCKED: force push refspec in config is not allowed." >&2
  exit 2
fi

# Mirror push (including git's unique-prefix abbreviations --mi through --mirror).
if echo "$NORM" | grep -qE "${PFX}(push|send-pack)([[:space:]]|$)[^;&|]*--mi(r(r(o(r)?)?)?)?([^[:alnum:]_-]|$)"; then
  echo "BLOCKED: mirror push is not allowed." >&2
  exit 2
fi

# Prune push (including git's unique-prefix abbreviations --pru through --prune).
if echo "$NORM" | grep -qE "${PFX}push([[:space:]]|$)[^;&|]*--pru(n(e)?)?([^[:alnum:]_-]|$)"; then
  echo "BLOCKED: prune push is not allowed." >&2
  exit 2
fi

# Remote branch delete via --delete (any abbreviation) or a bundled -d.
if echo "$NORM" | grep -qE "${PFX}(push|send-pack)([[:space:]]|$)([^;&|]*[[:space:]\"'<(=])?(--de(l(e(t(e)?)?)?)?|-[[:alnum:]]*d[[:alnum:]]*)([^[:alnum:]_-]|$)"; then
  echo "BLOCKED: remote branch deletion is not allowed." >&2
  exit 2
fi

# Remote branch delete via an empty source :refspec.
if echo "$NORM" | grep -qE "${PFX}(push|send-pack)([[:space:]]|$)[^;&|]*[[:space:]\"'][\"']?:[^[:space:]]"; then
  echo "BLOCKED: remote branch deletion (:refspec) is not allowed." >&2
  exit 2
fi

# Mirror mode configured on a remote (any value, or no value at all).
if echo "$NORM" | grep -qE "(^|[^[:alnum:]_-])git[[:space:]][^;&|]*remote\.[^[:space:]\"']*\.mirror([=[:space:]\"']|$)"; then
  echo "BLOCKED: remote mirror configuration is not allowed." >&2
  exit 2
fi

# Mirror mode set while adding a remote (git remote add --mirror[=...]).
if echo "$NORM" | grep -qE "${PFX}remote([[:space:]]|$)[^;&|]*--mi(r(r(o(r)?)?)?)?([=[:space:]]|$)"; then
  echo "BLOCKED: remote mirror configuration is not allowed." >&2
  exit 2
fi

# Alias that hides a force push (alias.p=push -f).
if echo "$NORM" | grep -qE "(^|[^[:alnum:]_-])git[[:space:]][^;&|]*alias\.[^[:space:]\"']*[=[:space:]]*['\"]?push[[:space:]]+(-f|--force)"; then
  echo "BLOCKED: alias for force push is not allowed." >&2
  exit 2
fi

# Destructive local ref update: update-ref -d or --stdin.
if echo "$NORM" | grep -qE "${PFX}update-ref([[:space:]]|$)([^;&|]*[[:space:]\"'<(=])?(-d([[:space:]\"'<(=]|$)|--stdin)"; then
  echo "BLOCKED: destructive update-ref is not allowed." >&2
  exit 2
fi

# Local branch delete/move with force: -D, -f/--force, or -d combined with force.
if echo "$NORM" | grep -qE "${PFX}branch([[:space:]]|$)([^;&|]*[[:space:]\"'<(=])?(-[[:alnum:]]*[Df][[:alnum:]]*|--for(c(e)?)?)([^[:alnum:]_-]|$)"; then
  echo "BLOCKED: forced branch deletion/move is not allowed." >&2
  exit 2
fi

# GitHub API ref deletion or forced update: gh api -X/--method DELETE|PATCH on git/refs.
if echo "$NORM" | grep -iE "(^|[^[:alnum:]_-])gh[[:space:]]+api[[:space:]][^;&|]*(-X[[:space:]]*|--method[=[:space:]]+)[[:space:]]*['\"]?(DELETE|PATCH)([^[:alnum:]_]|$)" | grep -qi 'git/refs'; then
  echo "BLOCKED: GitHub API ref deletion/forced update is not allowed." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE '\-\-no-verify'; then
  echo "BLOCKED: --no-verify is not allowed. Fix the hook issue instead." >&2
  exit 2
fi

# === 3. Destructive SQL ===
if echo "$COMMAND" | grep -qiE '(DROP\s+(TABLE|DATABASE|SCHEMA|VIEW)|TRUNCATE\s+TABLE|DELETE\s+FROM)'; then
  echo "BLOCKED: Destructive SQL operations are not allowed." >&2
  exit 2
fi

# === 4. Sensitive directories ===
if echo "$COMMAND" | grep -qE '(~|\$HOME)/\.(ssh|aws|config/gh|gnupg|netrc|npmrc)'; then
  echo "BLOCKED: Access to sensitive directories is not allowed." >&2
  exit 2
fi

# === 5. Secret exposure ===
if echo "$COMMAND" | grep -qE '(cat|less|more|head|tail)\s+.*\.env(\s|$)'; then
  echo "BLOCKED: Reading .env files is not allowed." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE '\bprintenv\b'; then
  echo "BLOCKED: printenv is not allowed." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE 'echo\s+\$.*?(KEY|SECRET|TOKEN|PASSWORD)'; then
  echo "BLOCKED: Echoing secret environment variables is not allowed." >&2
  exit 2
fi

# === 6. Terraform destroy ===
if echo "$COMMAND" | grep -qE 'terraform\s+destroy'; then
  echo "BLOCKED: terraform destroy is not allowed." >&2
  exit 2
fi

exit 0
