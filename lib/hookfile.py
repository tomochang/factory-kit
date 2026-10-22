#!/usr/bin/env python3
"""Add the kit's hooks to a settings/hooks JSON file, or take them out again.

    hookfile.py show FRAGMENT                 print the lines that would be added
    hookfile.py add FILE FRAGMENT             add missing handlers as new groups (backup first if FILE changes)
    hookfile.py remove FILE KIT               drop every handler whose command names KIT (backup first)

Only the "hooks" key is touched; other settings and the owner's own hooks stay as they are. New groups go at
the end of each event list, so the positions of existing hooks (Codex keys its trust records on them) don't
move. Prints "changed" or "unchanged". Exit 2 if FILE is not valid JSON (it is never overwritten then).
"""
import json
import shutil
import sys
import time
from pathlib import Path


def read(path):
    p = Path(path)
    if not p.exists():
        return {}
    text = p.read_text()
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def backup(path):
    p = Path(path)
    if p.exists():
        dest = p.with_name(p.name + ".factory-kit-backup-" + time.strftime("%Y%m%d%H%M%S"))
        n = 1
        while dest.exists():
            dest = p.with_name(p.name + ".factory-kit-backup-" + time.strftime("%Y%m%d%H%M%S") + f"-{n}")
            n += 1
        shutil.copy2(p, dest)
        print(f"backup: {dest}")


def write(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(p)


def add(data, fragment):
    hooks = data.setdefault("hooks", {})
    changed = False
    for event, groups in fragment.get("hooks", {}).items():
        existing = hooks.setdefault(event, [])
        have = {h.get("command") for g in existing for h in g.get("hooks", [])}
        for g in groups:
            new = [h for h in g["hooks"] if h.get("command") not in have]
            if new:
                group = {k: v for k, v in g.items() if k != "hooks"}
                group["hooks"] = new
                existing.append(group)
                have.update(h.get("command") for h in new)
                changed = True
    if not hooks:
        data.pop("hooks")
    return changed


def remove(data, kit):
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event in list(hooks):
        groups = []
        for g in hooks[event]:
            keep = [h for h in g.get("hooks", []) if kit not in str(h.get("command", ""))]
            if len(keep) != len(g.get("hooks", [])):
                changed = True
            if keep:
                groups.append({**g, "hooks": keep})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
            changed = True
    if not hooks:
        del data["hooks"]
    return changed


def main(argv):
    if len(argv) == 3 and argv[1] == "show":
        print(json.dumps(json.loads(Path(argv[2]).read_text()), indent=2))
        return 0
    if len(argv) != 4 or argv[1] not in ("add", "remove"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    cmd, path, arg = argv[1:]
    try:
        data = read(path)
    except ValueError as e:
        print(f"factory-kit: {path} is not valid JSON ({e}); fix it by hand, nothing was changed", file=sys.stderr)
        return 2
    changed = add(data, json.loads(Path(arg).read_text())) if cmd == "add" else remove(data, arg)
    if changed:
        backup(path)
        write(path, data)
    print("changed" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
