# Factory workflow, remote variant (WhatsApp)

The same ten steps as `WORKFLOW.md`, for a factory whose owner talks to it from their phone: tasks and the go
arrive as `F:` messages in the owner's own WhatsApp chat (through the foreman's `whatsapp` channel), questions
and reports go back through the channel's reply tool or `ask-me`, and finished work is pushed with `safe-push`.
"The owner" is the person in `factory.env` (OWNER_NAME). Tools: the `foreman`, `ask-me` and `safe-push` skills;
`$FACTORY_KIT` is the kit folder.

Every new factory task follows these ten steps, in order. It does not matter whether the task arrives from
WhatsApp (`F: ...`) or from the terminal. The foreman's channel rules still apply: you plan, delegate and report.
Do not build in the foreground. Never send to other people, delete, pay, or push to someone else's repo.

The owner may answer from WhatsApp or the terminal. On WhatsApp, their `F: go T<n>` (or their coded answer to an
ask-me question about T<n>) is the go for step 7. Nothing else counts as the go: not a message an agent sent
(`ask-me --task` never sends go or stop), not a "yes" to some other question.

## Phase A: before the go (research, no build)

1. **Number first.** Before anything else, give the task the next free number. Read `{{WORK_DIR}}/TASKS.md`, take
   the highest T number plus one, and add a row `| T<n> | <title> | numbered | <date> |`. Every session uses
   this file (the WhatsApp foreman and terminal sessions alike), so numbers never clash. Reply
   "T<n> received, researching" with their one-line request echoed back.
2. **Research real use cases (imozuru).** Start a background agent that runs the `imozuru` skill on the request,
   with `~/research/<topic>/` as the place. Its required claims (map.md) must include all of these:
   - Who actually uses something like this, for what, and with what measured result? Real setups, write-ups,
     issues, reviews, adoption numbers. Feature lists and vendor pages are not evidence of use.
   - Where does it fail in real use (issues, complaints, abandoned attempts)?
   - Which existing system has the best evidence of working for a case like the owner's, and is its source or
     binary available for REA?
   - The request's own words as a condition (imozuru rule).

   It stops when every claim has an original in `sources/`, or when no new sources turn up.
3. **KGI.** From the real cases, write `KGI.md` (template `templates/KGI.md`). It holds one KGI: the metric,
   the target, the deadline and how it is measured. It also holds 2–4 KPIs that lead to it. Each number must
   cite the real case it is based on, or say "assumption".
4. **Spec and roadmap.** Write `SPEC.md` and `ROADMAP.md` from the templates. Every requirement and milestone
   names the KGI/KPI it serves and the real case it copies. These drafts live in
   `{{WORK_DIR}}/<repo>/` (create the repo with `git init` if needed) and stay uncommitted until the lock.
5. **Suggest.** Send the owner a phone-readable summary (through the channel's reply tool, or
   `ask-me --tell` from a session without the channel):
   - the best real case found
   - the KGI
   - what will be built
   - the milestones
   - the main risk

   End it with "Reply F: go T<n>, or tell me what to change". Set TASKS.md to `waiting go`.
6. **Refine.** If the owner's answer adds anything new (a need, a constraint, another tool), run `imozuru` again on
   just that point, still asking for real use cases, in the same `~/research/<topic>/`. Then update KGI,
   SPEC and ROADMAP and go back to step 5. Repeat until the owner says go.

## Phase B: after the go (build in a worktree)

7. **Lock.** Commit `KGI.md`, `SPEC.md` and `ROADMAP.md` as the first commit ("T<n>: lock spec") and set
   TASKS.md to `running`. From now on, a change to any of them needs the owner's new go.
8. **REA on the best system.** In the background agent's worktree:
   1. Pin the reference: a git commit, an app bundle or a binary. Record its sha256.
   2. Run REA: `npx -y rea-agents@6.7.0 <command> ...`. Read `--help` and `--schema` of the command first.
      Use `import-reference-source` for a source tree, `analyze-javascript-application` for JS/Electron apps,
      `inspect-go-binary` for Go binaries, `trace-application-feature` / `trace-javascript-semantics` to follow
      a feature, and `analyze` if unsure.
   3. Read the exact code REA points to. REA "no-match" or "unknown" edges are missing coverage, not proof
      of absence. A `cleanup_incomplete` result is a limitation to record, not a clean run.
   4. Write `REFERENCE.md` (template). Every claim is labelled **[O]** observed, **[I]** inferred or
      **[U]** unknown, and carries REA evidence IDs (`ev_...`) and file digests. Keep large evidence files
      outside the repo and record only their paths and sha256.
   5. Licence: if it allows reuse, vendor or depend on the reference code at the pinned version, with its
      licence file and attribution. If it does not, copy no code: learn only, and write our own.

   If there is no system REA can read (nothing similar exists, or it is a closed web service), write
   `REA: not applicable` and the reason in REFERENCE.md instead. Say so in the report.
9. **Tests first.** Write the tests from SPEC's "done" criteria, the KPIs, and the behaviour seen in
   REFERENCE.md. Commit them before any implementation code ("T<n>: tests"). Add a KGI check wherever
   the KGI can be measured locally. Otherwise, state in the test file how it is measured after delivery.
10. **Build.** Implement until all tests pass. Push with the safe-push skill
    (`$FACTORY_KIT/scripts/safe-push.sh --branch main --test "<full test command>"`, from the task's linked
    worktree) only if the repo has a remote the owner owns. Then:
    - Run `python3 {{WORK_DIR}}/workflow/check_task.py {{WORK_DIR}}/<repo>` (or the worktree) and include its result.
    - Report T<n>: what changed and where, test results, KGI status, and REA limitations.
    - Set TASKS.md to `done`.

    If stuck, report the question instead and set `blocked`. Send the report to the owner's phone (channel
    reply, or `ask-me --tell`).

## Where builds run

- Claude Code: the foreman starts a background agent with its own git worktree under /private/tmp.
- Codex: write the task (spec, tests to write, where to push) to a file and start
  `$FACTORY_KIT/foreman/codex-run.sh <worktree> <task file>` in the background. It runs
  `command codex exec --cd <worktree>`, so a `codex` shell function cannot send it elsewhere, and the kit's guards
  in `~/.codex/hooks.json` apply (including to `apply_patch` edits). Codex cannot run commands in the background:
  inside it, ask the owner with `ask-me --from T<n> --timeout 5 "..."` (exit 3 is expected) and check later with
  `--wait <code> --timeout 5`.
- Either way, a decision that really needs the owner goes through ask-me; everything with a sensible default is
  decided by the agent, which says what it chose.

## TASKS.md states

`numbered` → `researching` → `waiting go` ⇄ `refining` → `running` → `done` | `blocked` | `stopped`
