#!/usr/bin/env python3
"""Write the per-machine files from templates/ into <kit>/generated/ (gitignored), with absolute paths.

    mcp.json                  the whatsapp channel for `claude --mcp-config` (no secrets: it names factory.env)
    workspace-settings.json   Claude Code guards, copied to <WORK_DIR>/.claude/settings.json by foreman.sh
    build-settings.json       the same guards plus the OS sandbox, for Claude builds started by claude-run.sh
    claude-settings.json      the Stop hook install.sh adds to ~/.claude/settings.json
    codex-hooks.json          the same guards and Stop hook install.sh adds to ~/.codex/hooks.json
    channel-instructions.txt  what the foreman session is told about the channel

Templates use {{NAME}} placeholders; an unknown name is an error. Usage: render.py [--out DIR]

`render.py --work print|apply|uninstall` handles the work folder (WORK_DIR) instead:
    workflow/      the kit's workflow/ (WORKFLOW*.md, README.md, check_task.py, templates/), rendered with the work
                   folder filled in. Rewritten on every apply; a manifest (.factory-kit-rendered) lists what the
                   kit wrote, so uninstall removes exactly that. A workflow folder the kit did not render is kept.
                   An old link into the kit's workflow/ (T13 installs) is replaced.
    CLAUDE.md, AGENTS.md, TASKS.md   from templates/factory/, created only when missing. Uninstall removes
                   CLAUDE.md and AGENTS.md only while they are unchanged; TASKS.md (the owner's data) always stays.
The work folder is written as "~/<path>" when it is under HOME (so the default stays "~/factory"), else absolute.
"""
import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitenv  # noqa: E402

PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
JSON_TEMPLATES = ("mcp.json", "workspace-settings.json", "build-settings.json", "claude-settings.json",
                  "codex-hooks.json")


def render_text(text, values):
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), text)


def render_json(obj, values):
    if isinstance(obj, str):
        return render_text(obj, values)
    if isinstance(obj, list):
        return [render_json(x, values) for x in obj]
    if isinstance(obj, dict):
        return {k: render_json(v, values) for k, v in obj.items()}
    return obj


def values(cfg, out):
    kit = Path(cfg["KIT"])
    node = shutil.which("node") or "node"
    protected = [p for p in cfg["PROTECTED_DIRS"]]
    return {
        "KIT": str(kit),
        "OWNER_NAME": cfg["OWNER_NAME"] or "the owner",
        "WORK_DIR": cfg["WORK_DIR"],
        "PROTECTED_TEXT": " or ".join(protected),
        "NODE": node,
        "PYTHON": "python3",
        "ENV_FILE": cfg["ENV_FILE"],
        "GENERATED": str(out),
        "ASK_ME": str(kit / "scripts" / "ask-me.py"),
        "SAFE_PUSH": str(kit / "scripts" / "safe-push.sh"),
        "CODEX_RUN": str(kit / "foreman" / "codex-run.sh"),
        "CLAUDE_RUN": str(kit / "foreman" / "claude-run.sh"),
        "TOKEN_FILE": cfg["TOKEN_FILE"],
        "BEEPER_HOST": urlsplit(cfg["BEEPER_BASE"]).netloc or "127.0.0.1:23373",
    }


def instructions(cfg, out=None):
    tmpl = (Path(cfg["KIT"]) / "templates" / "channel-instructions.txt.tmpl").read_text()
    return render_text(tmpl, values(cfg, out or Path(cfg["KIT"]) / "generated"))


def render_all(cfg, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    vals = values(cfg, out)
    tdir = Path(cfg["KIT"]) / "templates"
    written = []
    for name in JSON_TEMPLATES:
        data = render_json(json.loads((tdir / (name + ".tmpl")).read_text()), vals)
        (out / name).write_text(json.dumps(data, indent=2) + "\n")
        written.append(out / name)
    (out / "channel-instructions.txt").write_text(instructions(cfg, out))
    written.append(out / "channel-instructions.txt")
    return written


WORK_PLACEHOLDER = "{{WORK_DIR}}"
WORKFLOW_SKIP = {"test_check_task.py", "__pycache__", ".DS_Store"}
OWNER_FILES = ("CLAUDE.md", "AGENTS.md", "TASKS.md")
KEEP_ALWAYS = {"TASKS.md"}
MANIFEST = ".factory-kit-rendered"


def work_name(cfg):
    """How the rendered files name the work folder: ~/<path> under HOME, else the absolute path."""
    home = os.path.abspath(os.environ.get("HOME") or os.path.expanduser("~"))
    work = cfg["WORK_DIR"]
    if work == home:
        return "~"
    if work.startswith(home.rstrip("/") + "/"):
        return "~/" + work[len(home.rstrip("/")) + 1:]
    return work


def fill(data, name):
    try:
        return data.decode("utf-8").replace(WORK_PLACEHOLDER, name).encode("utf-8")
    except UnicodeDecodeError:
        return data


def workflow_files(cfg):
    """[(relative path in workflow/, bytes, mode)] rendered from the kit's workflow/."""
    src = Path(cfg["KIT"]) / "workflow"
    name = work_name(cfg)
    out = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if any(part in WORKFLOW_SKIP for part in rel.parts) or not p.is_file():
            continue
        out.append((rel.as_posix(), fill(p.read_bytes(), name), p.stat().st_mode & 0o777))
    return out


def owner_files(cfg):
    tdir = Path(cfg["KIT"]) / "templates" / "factory"
    return [(f, fill((tdir / f).read_bytes(), work_name(cfg))) for f in OWNER_FILES]


def read_manifest(wf):
    try:
        return [line for line in (wf / MANIFEST).read_text().splitlines() if line.strip()]
    except OSError:
        return []


def is_kit_link(wf, cfg):
    return wf.is_symlink() and os.path.realpath(wf) == os.path.realpath(Path(cfg["KIT"]) / "workflow")


def work_apply(cfg, mode):
    work = Path(cfg["WORK_DIR"])
    wf = work / "workflow"
    files = workflow_files(cfg)
    if mode == "print":
        if wf.exists() and not wf.is_symlink() and not (wf / MANIFEST).is_file():
            print(f"skip: {wf} exists")
        else:
            if is_kit_link(wf, cfg):
                print(f"would replace the link {wf} with rendered files")
            for rel, _, _ in files:
                print(f"would render {wf / rel}")
        for f, _ in owner_files(cfg):
            dest = work / f
            print(f"skip: {dest} exists" if dest.exists() or dest.is_symlink() else f"would create {dest}")
        return 0
    work.mkdir(parents=True, exist_ok=True)
    if is_kit_link(wf, cfg):
        wf.unlink()
        print(f"replaced the link {wf} with rendered files")
    if (wf.exists() or wf.is_symlink()) and not (wf / MANIFEST).is_file():
        print(f"skip: {wf} exists (not rendered by the kit)")
    else:
        old = set(read_manifest(wf))
        for rel, data, perm in files:
            dest = wf / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            dest.chmod(perm)
        new = [rel for rel, _, _ in files]
        for rel in sorted(old - set(new)):
            remove_file(wf, rel)
        (wf / MANIFEST).write_text("".join(r + "\n" for r in new))
        print(f"rendered: {wf} ({len(new)} files, work folder {work_name(cfg)})")
    for f, data in owner_files(cfg):
        dest = work / f
        if dest.exists() or dest.is_symlink():
            print(f"skip: {dest} exists")
        else:
            dest.write_bytes(data)
            print(f"created: {dest}")
    return 0


def remove_file(wf, rel):
    p = wf / rel
    if p.is_file() or p.is_symlink():
        p.unlink()
    d = p.parent
    while d != wf and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def work_uninstall(cfg):
    work = Path(cfg["WORK_DIR"])
    wf = work / "workflow"
    if is_kit_link(wf, cfg):
        wf.unlink()
        print(f"unlinked: {wf}")
    elif (wf / MANIFEST).is_file():
        for rel in read_manifest(wf):
            remove_file(wf, rel)
        (wf / MANIFEST).unlink()
        if not any(wf.iterdir()):
            wf.rmdir()
            print(f"removed: {wf}")
        else:
            print(f"removed the kit's files from {wf} (your other files there are kept)")
    for f, data in owner_files(cfg):
        dest = work / f
        if f in KEEP_ALWAYS or not dest.is_file() or dest.is_symlink():
            continue
        if dest.read_bytes() == data:
            dest.unlink()
            print(f"removed: {dest} (unchanged since install)")
        else:
            print(f"kept: {dest} (you changed it)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out")
    ap.add_argument("--work", choices=("print", "apply", "uninstall"))
    a = ap.parse_args(argv)
    cfg = kitenv.load()
    if a.work == "uninstall":
        return work_uninstall(cfg)
    if a.work:
        return work_apply(cfg, a.work)
    for p in kitenv.problems(cfg):
        print("factory-kit: " + p, file=sys.stderr)
        return 2
    for p in render_all(cfg, a.out or Path(cfg["KIT"]) / "generated"):
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
