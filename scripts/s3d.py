#!/usr/bin/env python3
"""scientific-3d-illustration command line tool.

Typical use::

    python s3d.py doctor
    python s3d.py check  scene.json
    python s3d.py build  scene.json --preview
    python s3d.py build  scene.json
    python s3d.py edit   scene.json --set 'views.stage2.camera.azimuth=-50'
    python s3d.py figure scene.json          # re-compose only, no re-render

The Blender scene is generated from the JSON on every build; nothing is
hard-coded in the renderer.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import s3d_core as core  # noqa: E402
import s3d_compose  # noqa: E402

SKILL_ROOT = core.SKILL_ROOT
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".s3d", "config.json")


# --------------------------------------------------------------------------
# blender discovery
# --------------------------------------------------------------------------
def _config_blender() -> str | None:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh).get("blender")
    except Exception:  # noqa: BLE001
        return None


def find_blender(explicit: str | None = None) -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("BLENDER_BIN") or os.environ.get("BLENDER")
    if env:
        candidates.append(env)
    cfg = _config_blender()
    if cfg:
        candidates.append(cfg)
    which = shutil.which("blender")
    if which:
        candidates.append(which)

    home = os.path.expanduser("~")
    patterns = [
        os.path.join(home, ".local", "share", "s3d", "blender-*", "blender.exe"),
        os.path.join(home, ".local", "share", "s3d", "blender-*", "blender"),
        os.path.join(home, ".local", "share", "s3d", "Blender.app", "Contents", "MacOS", "Blender"),
        os.path.join(home, "Blender", "blender-*", "blender.exe"),
        "/usr/bin/blender",
        "/usr/local/bin/blender",
        "/opt/blender/blender",
        "/opt/blender-*/blender",
        "/Applications/Blender.app/Contents/MacOS/Blender",
        r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
        r"C:\Program Files (x86)\Blender Foundation\Blender*\blender.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Blender Foundation", "Blender*", "blender.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Blender*", "blender.exe"),
    ]
    for pat in patterns:
        candidates.extend(sorted(glob.glob(pat), reverse=True))
    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    raise FileNotFoundError(
        "Blender not found. Install Blender 4.x, run\n"
        "  python s3d.py install-blender\n"
        "or set BLENDER_BIN=/path/to/blender")


def blender_version(path: str) -> str:
    try:
        out = subprocess.run([path, "-b", "--factory-startup", "--python-expr",
                              "import bpy;print('VER', bpy.app.version_string)"],
                             capture_output=True, text=True, timeout=180)
        for line in out.stdout.splitlines():
            if line.startswith("VER "):
                return line[4:].strip()
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def load_scene(path: str) -> dict:
    if not os.path.exists(path):
        raise SystemExit("scene not found: %s" % path)
    return core.load_scene(path)


def report_check(result: dict, quiet: bool = False) -> bool:
    if not quiet:
        for e in result.get("errors", []):
            print("  ERROR  %s" % e)
        for w in result.get("warnings", []):
            print("  WARN   %s" % w)
    if result.get("errors"):
        print("check failed: %d error(s), %d warning(s)" %
              (len(result["errors"]), len(result.get("warnings", []))))
        return False
    print("check ok: 0 errors, %d warning(s)" % len(result.get("warnings", [])))
    return True


def full_check(scene: dict, strict: bool = False) -> dict:
    v = core.validate_scene(scene)
    if not v["ok"]:
        return v
    res = core.check_geometry(scene, strict=strict)
    res["warnings"] = v.get("warnings", []) + res.get("warnings", [])
    return res


def resolution_map(scene: dict) -> dict:
    out = {}
    fig = scene.get("figure", {})
    for view in scene.get("views", []):
        panel = None
        for p in fig.get("panels", []):
            if p.get("view") == view["id"]:
                panel = p
                break
        out[view["id"]] = list(core.view_resolution(view, panel, fig))
    return out


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_doctor(args):
    print("scientific-3d-illustration")
    print("  skill root : %s" % SKILL_ROOT)
    print("  python     : %s %s" % (sys.version.split()[0], sys.executable))
    try:
        import PIL
        print("  pillow     : %s" % PIL.__version__)
    except Exception:  # noqa: BLE001
        print("  pillow     : MISSING (pip install pillow)")
    try:
        import s3d_fonts
        print("  font       : %s" % s3d_fonts.find_font(False))
        print("  bold font  : %s" % s3d_fonts.find_font(True))
    except Exception as exc:  # noqa: BLE001
        print("  font       : MISSING (%s)" % exc)
    try:
        b = find_blender(args.blender)
        print("  blender    : %s" % b)
        print("  version    : %s" % blender_version(b))
    except Exception as exc:  # noqa: BLE001
        print("  blender    : NOT FOUND")
        print("               %s" % str(exc).replace("\n", "\n               "))
    return 0


def cmd_install_blender(args):
    import urllib.request
    version = args.version
    system = sys.platform
    if system.startswith("win"):
        plat = "windows-x64"
        folder = "Blender%s" % version.replace(".", ".")
        name = "blender-%s-%s.zip" % (version, plat)
        exe = "blender.exe"
    elif system == "darwin":
        plat = "macos-arm64" if "arm" in os.uname().machine else "macos-x64"
        name = "blender-%s-%s.dmg" % (version, plat)
        exe = "blender"
    else:
        plat = "linux-x64"
        name = "blender-%s-%s.tar.xz" % (version, plat)
        exe = "blender"
    url = args.url or "https://download.blender.org/release/Blender%s/%s" % (
        ".".join(version.split(".")[:2]), name)
    dest_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "s3d")
    os.makedirs(dest_dir, exist_ok=True)
    archive = os.path.join(dest_dir, name)
    print("downloading %s" % url)
    urllib.request.urlretrieve(url, archive)
    print("extracting ...")
    if name.endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest_dir)
    elif name.endswith(".tar.xz"):
        import tarfile
        with tarfile.open(archive) as t:
            t.extractall(dest_dir)
    else:
        print("macOS: open %s and drag Blender.app to /Applications" % archive)
        return 0
    found = find_blender()
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump({"blender": found}, fh, indent=2)
    print("installed: %s" % found)
    print("wrote %s" % CONFIG_PATH)
    return 0


def cmd_validate(args):
    scene = load_scene(args.scene)
    res = core.validate_scene(scene)
    return 0 if report_check(res) else 2


def cmd_check(args):
    scene = load_scene(args.scene)
    res = full_check(scene, strict=args.strict)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    ok = report_check(res)
    if args.verbose and res.get("stats"):
        print(json.dumps(res["stats"], ensure_ascii=False, indent=2))
    return 0 if ok else 2


def cmd_inspect(args):
    scene = load_scene(args.scene)
    print(core.scene_summary(scene))
    print("")
    res = resolution_map(scene)
    for v in scene.get("views", []):
        cam = v.get("camera", {})
        print("view %-16s %5dx%-5d ortho=%-6s az=%-6s el=%-6s layers=%s" % (
            v["id"], res[v["id"]][0], res[v["id"]][1],
            cam.get("ortho_scale", "-"), cam.get("azimuth", "-"), cam.get("elevation", "-"),
            ",".join(v.get("layers", ["*"])) if v.get("layers") else "*"))
    fig = scene.get("figure", {})
    print("figure %s  panels=%d" % (fig.get("size"), len(fig.get("panels", []))))
    return 0


def _run_blender(blender: str, scene: dict, scene_path: str, out_dir: str,
                 views=None, preview=False, save_blend=True) -> None:
    res_map = resolution_map(scene)
    if preview:
        res_map = {k: [max(64, v[0] // 2), max(64, v[1] // 2)] for k, v in res_map.items()}
    build_dir = os.path.join(out_dir, "build")
    os.makedirs(build_dir, exist_ok=True)
    resolved = os.path.join(build_dir, "scene.resolved.json")
    core.save_json(scene, resolved)
    core.save_json(scene, os.path.join(out_dir, "scene.json"))

    panels_dir = os.path.join(out_dir, "panels")
    os.makedirs(panels_dir, exist_ok=True)

    cmd = [blender, "-b", "-noaudio", "--factory-startup",
           "--python", os.path.join(HERE, "blender_worker.py"), "--",
           "--scene", resolved, "--out", panels_dir,
           "--resolution", json.dumps(res_map)]
    if views:
        cmd += ["--views", ",".join(views)]
    if save_blend:
        cmd += ["--blend", os.path.join(out_dir, "scene.blend")]
    if preview:
        cmd.append("--preview")

    print("running blender ...")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log_path = os.path.join(out_dir, "blender.log")
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(proc.stdout + "\n" + proc.stderr)
    for line in proc.stdout.splitlines():
        if line.startswith("[s3d]"):
            print("  " + line)
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
        raise SystemExit("blender failed (exit %d)\n%s\nlog: %s" %
                         (proc.returncode, tail, log_path))
    print("blender finished in %.1fs" % (time.time() - t0))


def cmd_render(args):
    scene_path = os.path.abspath(args.scene)
    scene = load_scene(scene_path)
    out = os.path.abspath(args.out or os.path.join(os.path.dirname(scene_path), "out"))
    if not args.skip_check:
        ok = report_check(full_check(scene, strict=args.strict))
        if not ok and not args.force:
            print("fix the errors above, or pass --force")
            return 2
    blender = find_blender(args.blender)
    _run_blender(blender, scene, scene_path, out, views=args.views,
                 preview=args.preview, save_blend=not args.no_blend)
    if not args.no_figure:
        p1 = s3d_compose.compose(scene, out, preview=args.preview)
        p2 = s3d_compose.compose(scene, out, transparent_page=True, preview=args.preview)
        print("figure: %s" % p1)
        print("figure: %s" % p2)
    return 0


def cmd_figure(args):
    scene_path = os.path.abspath(args.scene)
    scene = load_scene(scene_path)
    out = os.path.abspath(args.out or os.path.join(os.path.dirname(scene_path), "out"))
    p1 = s3d_compose.compose(scene, out, preview=args.preview)
    p2 = s3d_compose.compose(scene, out, transparent_page=True, preview=args.preview)
    print("figure: %s" % p1)
    print("figure: %s" % p2)
    return 0


def cmd_build(args):
    rc = cmd_render(args)
    return rc


def cmd_new(args):
    scene = {
        "version": 1,
        "meta": {"title": args.title or "Scientific 3D illustration", "slug": args.name},
        "style": {"preset": args.preset},
        "world": {
            "voxel_size": 0.5,
            "origin": [0, 0, 0],
            "objects": [
                {"id": "free_space", "type": "voxels", "layer": "free_space",
                 "mat": "glass_free", "gap": 0.035,
                 "cells": {"grids": [{"i": [0, 9], "j": [0, 7], "k": [0, 0],
                                      "thickness": 0.3}]}},
                {"id": "obstacle", "type": "brick_wall", "layer": "obstacles",
                 "mat": "obstacle", "origin": [0, 4.0, 0], "axis": "x",
                 "length": 4.0, "height": 1.5, "thickness": 0.5}
            ]
        },
        "views": [
            {"id": "main", "camera": {"type": "ortho", "azimuth": -58, "elevation": 34,
                                      "ortho_scale": 7.0, "target": [2.4, 2.0, 0.6],
                                      "fit": True, "fit_margin": 0.04},
             "layers": ["free_space", "obstacles"]}
        ],
        "figure": {
            "size": [2400, 1700],
            "title": {"text": args.title or "Scientific 3D illustration", "size": 96, "y": 40},
            "panels": [{"id": "p1", "view": "main", "rect": [40, 150, 2360, 1650],
                        "header": {"text": "场景", "height": 70, "size": 54}}]
        }
    }
    out = args.scene or "scene.json"
    core.save_json(scene, out)
    print("wrote %s" % out)
    return 0


# --------------------------------------------------------------------------
# editing
# --------------------------------------------------------------------------
def _walk(root, path, create=False):
    parts = [p for p in path.split(".") if p]
    cur = root
    parent = None
    for i, part in enumerate(parts):
        parent, key = cur, part
        if isinstance(cur, dict):
            if part not in cur:
                if not create:
                    raise KeyError(path)
                cur[part] = {}
            cur = cur[part]
        elif isinstance(cur, list):
            found = None
            if part.isdigit():
                idx = int(part)
                if -len(cur) <= idx < len(cur):
                    found = cur[idx]
            if found is None:
                for item in cur:
                    if isinstance(item, dict) and str(item.get("id")) == part:
                        found = item
                        break
            if found is None:
                raise KeyError("%s (no list item %r)" % (path, part))
            cur = found
        else:
            raise KeyError(path)
    return parent, parts[-1], cur


def _apply_op(scene, op):
    kind = op.get("op", "set")
    if kind == "set":
        parent, key, _ = _walk(scene, op["path"], create=True)
        if isinstance(parent, dict):
            parent[key] = op["value"]
        elif isinstance(parent, list):
            parent[int(key)] = op["value"]
        return "%s = %s" % (op["path"], json.dumps(op["value"], ensure_ascii=False))
    if kind == "merge":
        _, _, obj = _walk(scene, op["path"])
        merged = core.deep_merge(obj, op["value"])
        parent, key, _ = _walk(scene, op["path"])
        if isinstance(parent, dict):
            parent[key] = merged
        else:
            parent[int(key)] = merged
        return "merged %s" % op["path"]
    if kind == "add_object":
        obj = op["object"]
        scene.setdefault("world", {}).setdefault("objects", []).append(obj)
        return "added object %s" % obj.get("id")
    if kind == "remove_object":
        objs = scene.get("world", {}).get("objects", [])
        n = len(objs)
        scene["world"]["objects"] = [o for o in objs if o.get("id") != op["id"]]
        return "removed object %s (%d -> %d)" % (op["id"], n, len(scene["world"]["objects"]))
    if kind == "duplicate_object":
        objs = scene.get("world", {}).get("objects", [])
        src = next((o for o in objs if o.get("id") == op["id"]), None)
        if src is None:
            raise KeyError(op["id"])
        new = json.loads(json.dumps(src))
        new["id"] = op["new_id"]
        new.update(op.get("merge", {}))
        objs.append(new)
        return "duplicated %s -> %s" % (op["id"], op["new_id"])
    if kind == "translate":
        obj = next((o for o in scene.get("world", {}).get("objects", [])
                    if o.get("id") == op["id"]), None)
        if obj is None:
            raise KeyError(op["id"])
        by = op["by"]
        for key in ("position", "center", "origin", "apex"):
            if key in obj:
                obj[key] = [obj[key][i] + by[i] for i in range(3)]
        for key in ("at", "targets"):
            if key in obj and isinstance(obj[key], list):
                obj[key] = [[p[i] + by[i] for i in range(3)] for p in obj[key]]
        if "spheres" in obj:
            for s in obj["spheres"]:
                s["center"] = [s["center"][i] + by[i] for i in range(3)]
        if "points" in obj:
            obj["points"] = [[p[i] + by[i] for i in range(3)] for p in obj["points"]]
        return "translated %s by %s" % (op["id"], by)
    if kind == "set_material":
        obj = next((o for o in scene.get("world", {}).get("objects", [])
                    if o.get("id") == op["id"]), None)
        if obj is None:
            raise KeyError(op["id"])
        obj["mat"] = op["mat"]
        return "material %s -> %s" % (op["id"], op["mat"])
    raise ValueError("unknown edit op %r" % kind)


def cmd_edit(args):
    scene = load_scene(args.scene)
    ops = []
    if args.patch:
        patch = core.load_json(args.patch)
        ops.extend(patch.get("ops", patch) if isinstance(patch, dict) else patch)
    for s in args.set or []:
        if "=" not in s:
            raise SystemExit("--set needs path=value, got %r" % s)
        path, raw = s.split("=", 1)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        ops.append({"op": "set", "path": path, "value": value})
    if args.add_object:
        ops.append({"op": "add_object", "object": core.load_json(args.add_object)})
    if not ops:
        raise SystemExit("nothing to edit: pass --patch or --set")
    for op in ops:
        print(_apply_op(scene, op))
    out = args.out or (args.scene if args.in_place else None)
    if out:
        core.save_json(scene, out)
        print("wrote %s" % out)
    else:
        print(json.dumps(scene, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog="s3d", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("doctor", help="check Blender, Pillow and fonts")
    p.add_argument("--blender")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("install-blender", help="download a portable Blender")
    p.add_argument("--version", default="4.5.15")
    p.add_argument("--url")
    p.set_defaults(func=cmd_install_blender)

    p = sub.add_parser("validate", help="schema / reference validation")
    p.add_argument("scene")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("check", help="geometry consistency checks")
    p.add_argument("scene")
    p.add_argument("--strict", action="store_true", help="treat clipping as an error")
    p.add_argument("--json", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("inspect", help="list objects, bounds and views")
    p.add_argument("scene")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("render", help="render panels (+ compose unless --no-figure)")
    p.add_argument("scene")
    p.add_argument("--out")
    p.add_argument("--views", nargs="*")
    p.add_argument("--blender")
    p.add_argument("--preview", action="store_true")
    p.add_argument("--force", action="store_true", help="render even if checks fail")
    p.add_argument("--skip-check", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--no-blend", action="store_true")
    p.add_argument("--no-figure", action="store_true")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("figure", help="compose the 2D figure from existing renders")
    p.add_argument("scene")
    p.add_argument("--out")
    p.add_argument("--preview", action="store_true")
    p.set_defaults(func=cmd_figure)

    p = sub.add_parser("build", help="check + render + compose")
    p.add_argument("scene")
    p.add_argument("--out")
    p.add_argument("--views", nargs="*")
    p.add_argument("--blender")
    p.add_argument("--preview", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--skip-check", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--no-blend", action="store_true")
    p.add_argument("--no-figure", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("edit", help="patch an existing scene JSON")
    p.add_argument("scene")
    p.add_argument("--patch")
    p.add_argument("--set", action="append")
    p.add_argument("--add-object")
    p.add_argument("--out")
    p.add_argument("--in-place", action="store_true")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("new", help="scaffold a minimal scene")
    p.add_argument("--scene")
    p.add_argument("--name", default="scene")
    p.add_argument("--title")
    p.add_argument("--preset", default="iso-glass")
    p.set_defaults(func=cmd_new)

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
