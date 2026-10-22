#!/usr/bin/env python3
"""Write the per-machine files from templates/ into <kit>/generated/ (gitignored), with absolute paths.

    mcp.json                  the whatsapp channel for `claude --mcp-config` (no secrets: it names factory.env)
    workspace-settings.json   Claude Code guards, copied to <WORK_DIR>/.claude/settings.json by foreman.sh
    claude-settings.json      the Stop hook install.sh adds to ~/.claude/settings.json
    codex-hooks.json          the same guards and Stop hook install.sh adds to ~/.codex/hooks.json
    channel-instructions.txt  what the foreman session is told about the channel

Templates use {{NAME}} placeholders; an unknown name is an error. Usage: render.py [--out DIR]
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitenv  # noqa: E402

PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
JSON_TEMPLATES = ("mcp.json", "workspace-settings.json", "claude-settings.json", "codex-hooks.json")


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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    cfg = kitenv.load()
    for p in kitenv.problems(cfg):
        print("factory-kit: " + p, file=sys.stderr)
        return 2
    for p in render_all(cfg, a.out or Path(cfg["KIT"]) / "generated"):
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
