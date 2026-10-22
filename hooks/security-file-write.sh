#!/bin/bash
# PreToolUse hook for file writes (Claude Code and Codex): block writes to sensitive files and hardcoded secrets.
# Exit 0 = allow, Exit 2 = block (reason on stderr). Needs jq.
# Claude Code: Write/Edit/MultiEdit/NotebookEdit with tool_input.file_path (+ content / new_string).
# Codex: tool_name "apply_patch" with the patch in tool_input.command; every "*** Add/Update/Delete File:" and
# "*** Move to:" path is checked, and the added ("+") lines are checked for secrets.

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
PATCH=$(echo "$INPUT" | jq -r '.tool_input.command // empty | if type == "array" then map(tostring) | join(" ") else tostring end')

if [ "$TOOL_NAME" = "apply_patch" ] || printf '%s' "$PATCH" | grep -q '^\*\*\* Begin Patch'; then
  FILE_PATHS=$(printf '%s\n' "$PATCH" | sed -n -E 's/^\*\*\* (Add File|Update File|Delete File|Move to):[[:space:]]*//p')
  ADDED=$(printf '%s\n' "$PATCH" | sed -n -E 's/^\+//p')
  KIND="patch"
else
  FILE_PATHS=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty')
  case "$TOOL_NAME" in
    Write) ADDED=$(echo "$INPUT" | jq -r '.tool_input.content // empty'); KIND="file" ;;
    Edit) ADDED=$(echo "$INPUT" | jq -r '.tool_input.new_string // empty'); KIND="edit" ;;
    MultiEdit) ADDED=$(echo "$INPUT" | jq -r '[.tool_input.edits[]?.new_string] | join("\n")'); KIND="edit" ;;
    *) ADDED="" ;;
  esac
fi

if [ -z "$FILE_PATHS" ]; then
  exit 0
fi

# === 1. Credential files ===
while IFS= read -r FILE_PATH; do
  [ -z "$FILE_PATH" ] && continue
  if [[ "$FILE_PATH" != *.env.sample ]] && [[ "$FILE_PATH" != *.env.example ]]; then
    if echo "$FILE_PATH" | grep -qE '\.env($|\.)'; then
      echo "BLOCKED: Writing to .env files is not allowed. Use environment variables." >&2
      exit 2
    fi
  fi

  if echo "$FILE_PATH" | grep -qiE '(credentials|secrets|service.account|private.key)\.(json|yaml|yml)$'; then
    echo "BLOCKED: Writing to credential/secret files is not allowed." >&2
    exit 2
  fi

  if echo "$FILE_PATH" | grep -qE '\.(key|pem|p12|pfx|jks)$'; then
    echo "BLOCKED: Writing to certificate/key files is not allowed." >&2
    exit 2
  fi
done <<< "$FILE_PATHS"

# === 2. Hardcoded secrets in what is written ===
if [ -n "$ADDED" ]; then
  if echo "$ADDED" | grep -qE '(xoxb-|xoxp-|xoxa-|xoxr-)[0-9a-zA-Z\-]+'; then
    echo "BLOCKED: Slack token detected in $KIND content. Use environment variables." >&2
    exit 2
  fi

  if echo "$ADDED" | grep -qE 'AIza[0-9A-Za-z\-_]{35}'; then
    echo "BLOCKED: Google API key detected in $KIND content. Use environment variables." >&2
    exit 2
  fi

  if echo "$ADDED" | grep -qE -e '-----BEGIN (RSA |EC )?PRIVATE KEY-----'; then
    echo "BLOCKED: Private key detected in $KIND content." >&2
    exit 2
  fi

  if [ "$KIND" != "edit" ] && echo "$ADDED" | grep -qiE '(password|secret|api_key|apikey|access_token)\s*[:=]\s*["\x27][^"\x27]{8,}'; then
    echo "BLOCKED: Possible hardcoded secret detected. Use environment variables." >&2
    exit 2
  fi
fi

exit 0
