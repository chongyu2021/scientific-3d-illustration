"""Shared, dependency-free core for scientific-3d-illustration.

This module is imported both by the command line tool (plain CPython) and by the
Blender worker.  It therefore must not import bpy, numpy or PIL.

Responsibilities
----------------
* load / merge scene JSON and style presets
* expand declarative objects into renderable primitives (boxes, spheres, tubes)
* camera math (orthographic / perspective, azimuth-elevation or explicit vector)
* geometry validation (grid alignment, overlap, point-surface consistency,
  camera clipping)

Keeping expansion in one place guarantees that what the checker validates is
exactly what Blender draws.
"""

from __future__ import annotations

import copy
import json
import math
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]

EPS = 1e-6

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# small vector helpers (no numpy on purpose)
# --------------------------------------------------------------------------
def vadd(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vmul(a: Sequence[float], s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def vdot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def vlen(a: Sequence[float]) -> float:
    return math.sqrt(vdot(a, a))


def vnorm(a: Sequence[float]) -> Vec3:
    n = vlen(a)
    if n < EPS:
        return (0.0, 0.0, 1.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# --------------------------------------------------------------------------
# deterministic pseudo random (independent of python's random version)
# --------------------------------------------------------------------------
class Rng:
    def __init__(self, seed: int = 1):
        self.s = (int(seed) * 2654435761 + 1013904223) & 0xFFFFFFFF

    def next(self) -> float:
        x = self.s
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        self.s = x & 0xFFFFFFFF
        return self.s / 4294967296.0

    def range(self, a: float, b: float) -> float:
        return a + (b - a) * self.next()


# --------------------------------------------------------------------------
# json / style helpers
# --------------------------------------------------------------------------
def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(obj: Dict[str, Any], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def resolve_style(scene: Dict[str, Any]) -> Dict[str, Any]:
    """Merge preset + inline overrides into one style dict."""
    spec = scene.get("style", "iso-glass")
    if isinstance(spec, str):
        spec = {"preset": spec}
    preset_name = spec.get("preset", "iso-glass")
    path = preset_name
    if not os.path.isabs(path):
        candidates = [os.path.join(SKILL_ROOT, "presets", preset_name + ".json"),
                      os.path.join(SKILL_ROOT, "presets", preset_name)]
        for c in candidates:
            if os.path.exists(c):
                path = c
                break
    base = load_json(path) if os.path.exists(path) else {}
    return deep_merge(base, spec.get("overrides", {}))


def load_scene(path: str) -> Dict[str, Any]:
    """Load a scene file and resolve auto-framed cameras."""
    import copy as _copy
    scene = load_json(path)
    return apply_camera_fit(_copy.deepcopy(scene))


def scene_paths(scene_path: str) -> Tuple[str, str]:
    """Return (scene_dir, output_dir) for a scene file."""
    scene_path = os.path.abspath(scene_path)
    scene_dir = os.path.dirname(scene_path)
    out = os.path.join(scene_dir, "out")
    return scene_dir, out


# --------------------------------------------------------------------------
# world helpers
# --------------------------------------------------------------------------
def world_voxel_size(world: Dict[str, Any]) -> float:
    return float(world.get("voxel_size", 0.5))


def world_origin(world: Dict[str, Any]) -> Vec3:
    o = world.get("origin", [0, 0, 0])
    return (float(o[0]), float(o[1]), float(o[2]))


def cell_center(world: Dict[str, Any], cell: Sequence[int]) -> Vec3:
    vs = world_voxel_size(world)
    o = world_origin(world)
    return (o[0] + (cell[0] + 0.5) * vs,
            o[1] + (cell[1] + 0.5) * vs,
            o[2] + (cell[2] + 0.5) * vs)


def cell_bounds(world: Dict[str, Any], cell: Sequence[int]) -> Tuple[Vec3, Vec3]:
    vs = world_voxel_size(world)
    o = world_origin(world)
    lo = (o[0] + cell[0] * vs, o[1] + cell[1] * vs, o[2] + cell[2] * vs)
    hi = (lo[0] + vs, lo[1] + vs, lo[2] + vs)
    return lo, hi


def _axis_range(spec) -> List[int]:
    if isinstance(spec, int):
        return [spec]
    if isinstance(spec, (list, tuple)) and len(spec) == 1:
        return [int(spec[0])]
    if isinstance(spec, (list, tuple)) and len(spec) >= 2:
        return list(range(int(spec[0]), int(spec[1]) + 1))
    raise ValueError("bad axis range: %r" % (spec,))


def _grid_cells(grid: Dict[str, Any], base_k: Optional[int] = None) -> List[Tuple[int, int, int]]:
    ii = _axis_range(grid.get("i", grid.get("x", 0)))
    jj = _axis_range(grid.get("j", grid.get("y", 0)))
    kk = _axis_range(grid["k"]) if "k" in grid else [base_k if base_k is not None else 0]
    cells = [(i, j, k) for i in ii for j in jj for k in kk]
    for ex in grid.get("exclude", []) or []:
        ei = set(_axis_range(ex.get("i", ex.get("x", 0))))
        ej = set(_axis_range(ex.get("j", ex.get("y", 0))))
        ek = set(_axis_range(ex["k"])) if "k" in ex else None
        cells = [c for c in cells
                 if not (c[0] in ei and c[1] in ej and (ek is None or c[2] in ek))]
    return cells


def voxel_cells(obj: Dict[str, Any]) -> List[Tuple[int, int, int]]:
    """Resolve a ``voxels`` object into an explicit list of integer cells."""
    cells: List[Tuple[int, int, int]] = []
    spec = obj.get("cells", {})
    if isinstance(spec, list):
        for c in spec:
            cells.append((int(c[0]), int(c[1]), int(c[2])))
        return cells
    for grid in spec.get("grids", []) or []:
        cells.extend(_grid_cells(grid))
    for c in spec.get("list", []) or []:
        cells.append((int(c[0]), int(c[1]), int(c[2])))
    # de-duplicate, keep deterministic order
    seen = set()
    out = []
    for c in cells:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


# --------------------------------------------------------------------------
# primitive expansion
# --------------------------------------------------------------------------
def _box(obj_id: str, mat: str, center, size, rot=(0, 0, 0), **kw) -> Dict[str, Any]:
    p = {"kind": "box", "obj": obj_id, "mat": mat,
         "center": tuple(float(v) for v in center),
         "size": tuple(float(v) for v in size),
         "rot": tuple(float(v) for v in rot)}
    p.update(kw)
    return p


def _expand_voxels(obj: Dict[str, Any], world: Dict[str, Any]) -> List[Dict[str, Any]]:
    vs = float(obj.get("voxel_size", world_voxel_size(world)))
    origin = obj.get("origin", world_origin(world))
    gap = float(obj.get("gap", 0.0))
    mat = obj.get("mat", "glass_free")
    spec = obj.get("cells", {})
    # per-grid thickness lets a "floor" of free space render as thin tiles while
    # keeping the exact same cell lattice (and therefore the same validation).
    entries: List[Tuple[Tuple[int, int, int], float]] = []
    if isinstance(spec, dict):
        for grid in spec.get("grids", []) or []:
            th = float(grid.get("thickness", 1.0))
            for c in _grid_cells(grid):
                entries.append((c, th))
        for c in spec.get("list", []) or []:
            entries.append(((int(c[0]), int(c[1]), int(c[2])), 1.0))
        for c in spec.get("cells", []) or []:
            entries.append(((int(c[0]), int(c[1]), int(c[2])), 1.0))
    else:
        for c in voxel_cells(obj):
            entries.append((c, 1.0))

    out = []
    seen = set()
    for c, th in entries:
        if c in seen:
            continue
        seen.add(c)
        zsize = max(vs * th - gap, vs * 0.08)
        center = (origin[0] + (c[0] + 0.5) * vs,
                  origin[1] + (c[1] + 0.5) * vs,
                  origin[2] + c[2] * vs + zsize / 2.0)
        size = (vs - gap, vs - gap, zsize)
        out.append(_box(obj["id"], mat, center, size, cell=c, lattice=vs,
                        role="free", gap=gap, extent=(vs, vs, vs),
                        bevel=obj.get("bevel", 0.012)))
    return out


def _expand_blocks(obj: Dict[str, Any], world: Dict[str, Any]) -> List[Dict[str, Any]]:
    vs = float(obj.get("voxel_size", world_voxel_size(world)))
    origin = obj.get("origin", world_origin(world))
    gap = float(obj.get("gap", 0.02))
    ext = obj.get("extent", [1, 1, 1])
    size = (ext[0] * vs - gap, ext[1] * vs - gap, ext[2] * vs - gap)
    mat = obj.get("mat", "obstacle")
    out = []
    for c in obj.get("cells", []):
        center = (origin[0] + (c[0] + ext[0] / 2.0) * vs,
                  origin[1] + (c[1] + ext[1] / 2.0) * vs,
                  origin[2] + (c[2] + ext[2] / 2.0) * vs)
        out.append(_box(obj["id"], mat, center, size,
                        cell=tuple(c), lattice=vs, role="solid", gap=gap,
                        bevel=obj.get("bevel", 0.01)))
    return out


def _expand_brick_wall(obj: Dict[str, Any], world: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A staggered brick wall built on the shared voxel lattice.

    Staggering is done inside the same lattice (brick length an even number of
    cells), so every brick edge stays grid aligned while the silhouette reads
    as masonry instead of a plain cuboid.
    """
    vs = float(obj.get("voxel_size", world_voxel_size(world)))
    origin = obj.get("origin", world_origin(world))
    axis = obj.get("axis", "x")          # wall runs along this axis
    length = float(obj.get("length", 4.0))
    height = float(obj.get("height", 2.0))
    thick = float(obj.get("thickness", 0.5))
    brick = obj.get("brick", [1.0, 0.5])  # [length, height]
    gap = float(obj.get("gap", 0.025))
    stagger = bool(obj.get("stagger", True))
    profile = obj.get("profile")  # optional list of heights sampled along length
    mat = obj.get("mat", "obstacle")

    bl = float(brick[0])
    bh = float(brick[1])
    n_rows = max(1, int(round(height / bh)))
    n_cols = max(1, int(round(length / bl)))
    out = []
    idx = 0
    for r in range(n_rows):
        z0 = r * bh
        shift = (0.5 * bl) if (stagger and r % 2 == 1) else 0.0
        c_start = -1 if shift > 0 else 0
        for c in range(c_start, n_cols + 1):
            x0 = c * bl + shift
            x1 = x0 + bl
            # clip to wall extent
            x0 = max(x0, 0.0)
            x1 = min(x1, n_cols * bl)
            if x1 - x0 < bl * 0.35:
                continue
            if profile:
                # height profile sampled by segment
                seg = min(len(profile) - 1, int((x0 / max(n_cols * bl, EPS)) * len(profile)))
                if z0 >= float(profile[seg]) - EPS:
                    continue
            cx_along = 0.5 * (x0 + x1)
            ci = int(round(cx_along / vs))
            ck = int(round((z0 + bh / 2.0) / vs))
            cl = int(round((x1 - x0) / vs))
            cth = int(round(thick / vs))
            if axis == "x":
                center = (origin[0] + ci * vs + (cx_along - ci * vs),
                          origin[1] + cth * vs / 2.0,
                          origin[2] + ck * vs + (z0 + bh / 2.0 - ck * vs))
                size = (cl * vs - gap, cth * vs - gap, int(round(bh / vs)) * vs - gap)
                cell = (int(round(x0 / vs)), 0, ck)
            else:
                center = (origin[0] + cth * vs / 2.0,
                          origin[1] + ci * vs + (cx_along - ci * vs),
                          origin[2] + ck * vs + (z0 + bh / 2.0 - ck * vs))
                size = (cth * vs - gap, cl * vs - gap, int(round(bh / vs)) * vs - gap)
                cell = (0, int(round(x0 / vs)), ck)
            out.append(_box(obj["id"], mat, center, size,
                            cell=cell, lattice=vs, role="solid", gap=gap,
                            bevel=obj.get("bevel", 0.008), brick_idx=idx))
            idx += 1
    return out


def _expand_box(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [_box(obj["id"], obj.get("mat", "obstacle"),
                 obj.get("center", [0, 0, 0]), obj.get("size", [1, 1, 1]),
                 obj.get("rot", [0, 0, 0]), role=obj.get("role", "solid"),
                 bevel=obj.get("bevel", 0.01))]


def _expand_spheres(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    mat = obj.get("mat", "point_static")
    r = float(obj.get("radius", 0.06))
    out = []
    for s in obj.get("spheres", []):
        out.append({"kind": "sphere", "obj": obj["id"], "mat": mat,
                    "center": tuple(float(v) for v in s.get("center", [0, 0, 0])),
                    "radius": float(s.get("radius", r)), "role": "marker"})
    return out


def _expand_gaussian(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"kind": "ellipsoid", "obj": obj["id"],
             "mat": obj.get("mat", "gauss"),
             "center": tuple(float(v) for v in obj.get("center", [0, 0, 0])),
             "radii": tuple(float(v) for v in obj.get("radii", [0.5, 0.5, 0.5])),
             "shells": int(obj.get("shells", 3)),
             "role": obj.get("role", "decor")}]


def _expand_frustum(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    apex = tuple(float(v) for v in obj.get("apex", [0, 0, 1]))
    if "target" in obj:
        d = vnorm(vsub(obj["target"], apex))
    else:
        d = vnorm(obj.get("direction", [1, 0, 0]))
    up_hint = obj.get("up", [0, 0, 1])
    right = vnorm(vcross(d, up_hint))
    if vlen(vcross(d, up_hint)) < 1e-5:
        right = (1.0, 0.0, 0.0)
    up = vnorm(vcross(right, d))
    hf = math.radians(float(obj.get("h_fov", 60)) / 2.0)
    vf = math.radians(float(obj.get("v_fov", 45)) / 2.0)
    depth = float(obj.get("depth", 2.0))
    corners = []
    for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
        t = math.tan(hf) * sx
        u = math.tan(vf) * sy
        corners.append(vadd(apex, vmul(vadd(d, vadd(vmul(right, t), vmul(up, u))), depth)))
    return [{"kind": "frustum", "obj": obj["id"],
             "mat": obj.get("mat", "frustum"),
             "apex": apex, "corners": corners,
             "faces": bool(obj.get("faces", True)),
             "edge_radius": float(obj.get("edge_radius", 0.013)),
             "role": "decor"}]


def _expand_rays(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    mat = obj.get("mat", "ray_history")
    r = float(obj.get("radius", 0.012))
    arrow = bool(obj.get("arrow", False))
    arrow_size = float(obj.get("arrow_size", r * 6.0))
    out = []
    starts = obj.get("from")
    if starts is not None and not isinstance(starts[0], (list, tuple)):
        starts_list = [starts]
    elif starts is not None:
        starts_list = starts
    else:
        starts_list = [None]

    i = 0
    for target in obj.get("targets", []):
        a = starts_list[i % len(starts_list)] if starts_list[0] is not None else target
        if starts_list[0] is None:
            continue
        out.append({"kind": "tube", "obj": obj["id"], "mat": mat,
                    "a": tuple(float(v) for v in a),
                    "b": tuple(float(v) for v in target),
                    "r1": r, "r2": r * 0.55, "role": "decor",
                    "arrow": arrow})
        i += 1
    for seg in obj.get("segments", []):
        a = seg["from"]
        if "to" in seg:
            b = seg["to"]
        else:
            d = vnorm(seg["direction"])
            b = vadd(a, vmul(d, float(seg.get("length", 1.0))))
        out.append({"kind": "tube", "obj": obj["id"], "mat": mat,
                    "a": tuple(float(v) for v in a),
                    "b": tuple(float(v) for v in b),
                    "r1": float(seg.get("radius", r)), "r2": float(seg.get("radius", r)) * 0.55,
                    "role": "decor", "arrow": arrow or bool(seg.get("arrow", False)),
                    "arrow_size": float(seg.get("arrow_size", arrow_size))})
    return out


def _expand_path(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    pts = [tuple(float(v) for v in p) for p in obj.get("points", [])]
    return [{"kind": "polyline", "obj": obj["id"],
             "mat": obj.get("mat", "path"),
             "points": pts,
             "radius": float(obj.get("radius", 0.015)),
             "arrow": bool(obj.get("arrow", True)),
             "role": "decor"}]


def _expand_robot(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    pos = tuple(float(v) for v in obj.get("position", [0, 0, 0]))
    heading = math.radians(float(obj.get("heading", 0)))
    s = float(obj.get("scale", 1.0))
    body_mat = obj.get("body_mat", "robot_body")
    dark_mat = obj.get("dark_mat", "robot_dark")
    accent_mat = obj.get("accent_mat", "robot_accent")
    out = []

    def local(px, py, pz):
        x = px * math.cos(heading) - py * math.sin(heading)
        y = px * math.sin(heading) + py * math.cos(heading)
        return (pos[0] + x * s, pos[1] + y * s, pos[2] + pz * s)

    # chassis
    out.append(_box(obj["id"], body_mat, local(0, 0, 0.17), (0.86 * s, 0.70 * s, 0.24 * s),
                    rot=(0, 0, math.degrees(heading)), role="solid", bevel=0.03))
    # wheels
    for sx in (-1, 1):
        for sy in (-1, 1):
            out.append(_box(obj["id"], dark_mat,
                            local(sx * 0.36, sy * 0.30, 0.085),
                            (0.16 * s, 0.10 * s, 0.17 * s),
                            rot=(0, 0, math.degrees(heading)), role="solid",
                            bevel=0.025, wheel=True))
    # lidar drum
    out.append({"kind": "cylinder", "obj": obj["id"], "mat": accent_mat,
                "center": local(0, 0, 0.49), "radius": 0.13 * s,
                "depth": 0.16 * s, "axis": (0, 0, 1), "role": "solid"})
    out.append({"kind": "cylinder", "obj": obj["id"], "mat": dark_mat,
                "center": local(0, 0, 0.585), "radius": 0.145 * s,
                "depth": 0.035 * s, "axis": (0, 0, 1), "role": "solid"})
    out.append({"kind": "cylinder", "obj": obj["id"], "mat": body_mat,
                "center": local(0, 0, 0.37), "radius": 0.24 * s,
                "depth": 0.06 * s, "axis": (0, 0, 1), "role": "solid"})
    for p in out:
        p["composite"] = True
    return out


EXPANDERS = {
    "voxels": _expand_voxels,
    "blocks": _expand_blocks,
    "brick_wall": _expand_brick_wall,
    "box": _expand_box,
    "spheres": _expand_spheres,
    "gaussian": _expand_gaussian,
    "frustum": _expand_frustum,
    "rays": _expand_rays,
    "path": _expand_path,
    "robot": _expand_robot,
}


def obj_layer(obj: Dict[str, Any]) -> str:
    return obj.get("layer", obj.get("type", "default"))


def obj_role(obj: Dict[str, Any]) -> str:
    if "role" in obj:
        return obj["role"]
    return {"voxels": "free", "points": "marker", "spheres": "marker",
            "rays": "decor", "path": "decor", "frustum": "decor",
            "gaussian": "decor"}.get(obj.get("type"), "solid")


# --------------------------------------------------------------------------
# surface sampling (used when generating point clouds)
# --------------------------------------------------------------------------
def _box_corners(center, size, rot_deg) -> List[Vec3]:
    rx, ry, rz = (math.radians(a) for a in rot_deg)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    hx, hy, hz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    out = []
    for ix in (-1, 1):
        for iy in (-1, 1):
            for iz in (-1, 1):
                x, y, z = ix * hx, iy * hy, iz * hz
                # rz
                x1, y1 = x * cz - y * sz, x * sz + y * cz
                # ry
                x2, z2 = x1 * cy + z * sy, -x1 * sy + z * cy
                # rx
                y3, z3 = y1 * cx - z2 * sx, y1 * sx + z2 * cx
                out.append((center[0] + x2, center[1] + y3, center[2] + z3))
    return out


def box_axes(rot_deg) -> Tuple[Vec3, Vec3, Vec3]:
    rx, ry, rz = (math.radians(a) for a in rot_deg)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    ax = (cz * cy, sz * cy, -sy)
    ay = (cz * sy * sx - sz * cx, sz * sy * sx + cz * cx, cy * sx)
    az = (cz * sy * cx + sz * sx, sz * sy * cx - cz * sx, cy * cx)
    return ax, ay, az


def _surface_points(obj: Dict[str, Any], world: Dict[str, Any],
                    all_objs: Dict[str, Dict[str, Any]]) -> List[Vec3]:
    """Sample points on the expanded faces of another object.

    Sampling the *expanded* primitives means generated points sit exactly on the
    drawn surface, including the small mortar gaps of brick walls.
    """
    spec = obj["surface_sample"]
    target_id = spec["of"]
    target = all_objs.get(target_id)
    if target is None:
        raise ValueError("surface_sample target %r not found" % target_id)
    prims = _expand(target, world)
    faces = spec.get("faces")
    normal_allow = None
    if faces:
        normal_allow = {
            "-x": lambda n: n[0] < -0.9, "+x": lambda n: n[0] > 0.9,
            "-y": lambda n: n[1] < -0.9, "+y": lambda n: n[1] > 0.9,
            "-z": lambda n: n[2] < -0.9, "+z": lambda n: n[2] > 0.9,
        }
        normal_allow = [normal_allow[f] for f in faces if f in normal_allow]

    # gather candidate face quads
    quads = []
    for p in prims:
        if p["kind"] != "box":
            continue
        ax, ay, az = box_axes(p["rot"])
        hx, hy, hz = (p["size"][0] / 2, p["size"][1] / 2, p["size"][2] / 2)
        c = p["center"]
        specs = [((-1, 0, 0), vmul(ax, -hx), ay, az, hy, hz),
                 ((1, 0, 0), vmul(ax, hx), ay, az, hy, hz),
                 ((0, -1, 0), vmul(ay, -hy), ax, az, hx, hz),
                 ((0, 1, 0), vmul(ay, hy), ax, az, hx, hz),
                 ((0, 0, -1), vmul(az, -hz), ax, ay, hx, hy),
                 ((0, 0, 1), vmul(az, hz), ax, ay, hx, hy)]
        for n, off, u, v, hu, hv in specs:
            if normal_allow is not None and not any(f(n) for f in normal_allow):
                continue
            quads.append((vadd(c, off), u, v, hu, hv, n))

    if not quads:
        return []
    rng = Rng(spec.get("seed", 3))
    margin = float(spec.get("margin", 0.05))
    jitter = float(spec.get("jitter", 0.0))
    offset = float(spec.get("offset", 0.0))
    count = int(spec.get("count", 30))
    areas = [max(4 * (q[3] - margin) * (q[4] - margin), 1e-5) for q in quads]
    total = sum(areas)

    out = []
    for _ in range(count):
        r = rng.next() * total
        acc = 0.0
        q = quads[-1]
        for qi, a in zip(quads, areas):
            acc += a
            if r <= acc:
                q = qi
                break
        center, u, v, hu, hv, n = q
        du = rng.range(-(hu - margin), (hu - margin))
        dv = rng.range(-(hv - margin), (hv - margin))
        p = vadd(center, vadd(vmul(u, du), vmul(v, dv)))
        p = vadd(p, vmul(n, offset + (jitter * rng.range(-1, 1))))
        out.append(p)
    return out


def _expand_points(obj: Dict[str, Any], world: Dict[str, Any],
                   all_objs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    mat = obj.get("mat", "point_static")
    r = float(obj.get("radius", 0.055))
    pts: List[Vec3] = []
    if "at" in obj:
        pts.extend(tuple(float(v) for v in p) for p in obj["at"])
    if "surface_sample" in obj:
        pts.extend(_surface_points(obj, world, all_objs))
    if "voxel_centers" in obj:
        spec = obj["voxel_centers"]
        for c in voxel_cells({"cells": spec}):
            pts.append(cell_center(world, c))
    if "random_in_box" in obj:
        spec = obj["random_in_box"]
        rng = Rng(spec.get("seed", 5))
        lo, hi = spec["min"], spec["max"]
        for _ in range(int(spec.get("count", 20))):
            pts.append((rng.range(lo[0], hi[0]), rng.range(lo[1], hi[1]), rng.range(lo[2], hi[2])))
    sizes = obj.get("radii")
    out = []
    for i, p in enumerate(pts):
        rr = float(sizes[i]) if isinstance(sizes, list) and len(sizes) == len(pts) and isinstance(sizes[0], (int, float)) else r
        out.append({"kind": "sphere", "obj": obj["id"], "mat": mat,
                    "center": p, "radius": rr, "role": "marker"})
    return out


def _expand(obj: Dict[str, Any], world: Dict[str, Any],
            all_objs: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    t = obj.get("type")
    if t == "points":
        return _expand_points(obj, world, all_objs or {})
    fn = EXPANDERS.get(t)
    if fn is None:
        raise ValueError("unknown object type %r (id=%r)" % (t, obj.get("id")))
    if t in ("voxels", "blocks", "brick_wall") and "voxel_size" not in obj:
        obj = dict(obj, voxel_size=world_voxel_size(world))
    return fn(obj, world) if t in ("voxels", "blocks", "brick_wall") else fn(obj)


def expand_world(scene: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    world = scene.get("world", {})
    objs = world.get("objects", [])
    by_id = {o["id"]: o for o in objs}
    prims: List[Dict[str, Any]] = []
    per_obj: Dict[str, List[Dict[str, Any]]] = {}
    for o in objs:
        p = _expand(o, world, by_id)
        for q in p:
            q["layer"] = obj_layer(o)
            q["obj_role"] = obj_role(o)
        prims.extend(p)
        per_obj[o["id"]] = p
    return prims, per_obj


# --------------------------------------------------------------------------
# camera
# --------------------------------------------------------------------------
def camera_basis(cam: Dict[str, Any]) -> Tuple[Vec3, Vec3, Vec3, Vec3]:
    """Return (eye, right, up, forward) for a view camera."""
    target = tuple(float(v) for v in cam.get("target", [0, 0, 0]))
    if "eye" in cam:
        eye = tuple(float(v) for v in cam["eye"])
        forward = vnorm(vsub(target, eye))
        dist = vlen(vsub(target, eye))
    else:
        az = math.radians(float(cam.get("azimuth", -60.0)))
        el = math.radians(float(cam.get("elevation", 30.0)))
        dist = float(cam.get("distance", 20.0))
        direction = (math.cos(el) * math.cos(az),
                     math.cos(el) * math.sin(az),
                     math.sin(el))
        if cam.get("type", "ortho") == "perspective":
            eye = target
            forward = vnorm(vmul(direction, -1.0))
            return eye, _right_up(forward)[0], _right_up(forward)[1], forward
        eye = vadd(target, vmul(direction, dist))
        forward = vnorm(vsub(target, eye))
    right, up = _right_up(forward)
    return eye, right, up, forward


def _right_up(forward: Sequence[float]) -> Tuple[Vec3, Vec3]:
    up_hint = (0.0, 0.0, 1.0)
    if abs(vdot(vnorm(forward), up_hint)) > 0.999:
        up_hint = (0.0, 1.0, 0.0)
    right = vnorm(vcross(forward, up_hint))
    up = vnorm(vcross(right, forward))
    return right, up


def panel_layout(figure: Dict[str, Any], panel: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a panel rectangle into its header / render / legend sub-rects.

    Shared by the renderer (which derives the render resolution from the render
    rect) and the 2D composer, so the image always matches its slot exactly.
    """
    rect = panel["rect"]
    x0, y0, x1, y1 = rect
    pad = float(panel.get("padding", figure.get("padding", 16)))
    header = panel.get("header") or {}
    hh = float(header.get("height", 0)) if header else 0.0
    legend = panel.get("legend") or {}
    items = legend.get("items", []) if legend else []
    rows = legend.get("rows") or (len(items) if items else 0)
    row_h = float(legend.get("row_height", 52))
    legend_h = float(legend.get("height", rows * row_h + (16 if rows else 0)))

    rx0 = x0 + pad
    rx1 = x1 - pad
    ry0 = y0 + pad + hh
    ry1 = y1 - pad - legend_h
    render_rect = panel.get("render_rect") or [rx0, ry0, rx1, ry1]
    legend_rect = panel.get("legend_rect")
    if legend_rect is None and rows:
        legend_rect = [rx0, ry1 + 8, rx1, y1 - pad]
    return {
        "render_rect": [float(v) for v in render_rect],
        "header_h": hh,
        "legend_rect": legend_rect,
        "legend_row_height": row_h,
        "pad": pad,
    }


def view_resolution(view: Dict[str, Any], panel: Optional[Dict[str, Any]] = None,
                    figure: Optional[Dict[str, Any]] = None,
                    scale: float = 2.0) -> Tuple[int, int]:
    res = view.get("resolution")
    if isinstance(res, list) and len(res) == 2:
        return int(res[0]), int(res[1])
    if panel is not None:
        lay = panel_layout(figure or {"panels": [panel]}, panel)
        r = lay["render_rect"]
        s = float(view.get("pixel_scale", scale))
        w = max(64, int(round((r[2] - r[0]) * s)))
        h = max(64, int(round((r[3] - r[1]) * s)))
        return w, h
    return 1400, 1000


def project_point(p: Sequence[float], cam: Dict[str, Any], res: Tuple[int, int]) -> Tuple[float, float]:
    """Project a world point to render pixel coordinates (origin top-left)."""
    eye, right, up, forward = camera_basis(cam)
    rel = vsub(p, eye)
    u = vdot(rel, right)
    v = vdot(rel, up)
    depth = vdot(rel, forward)
    W, H = res
    if cam.get("type", "ortho") == "perspective":
        fov = math.radians(float(cam.get("fov", 35.0)))
        f = (H / 2.0) / math.tan(fov / 2.0)
        if depth <= 1e-4:
            depth = 1e-4
        px = W / 2.0 + u * f / depth
        py = H / 2.0 - v * f / depth
    else:
        oscale = float(cam.get("ortho_scale", 8.0))
        px = W / 2.0 + (u / oscale) * W
        py = H / 2.0 - (v / oscale) * W
    return px, py


def project_depth(p: Sequence[float], cam: Dict[str, Any]) -> float:
    eye, right, up, forward = camera_basis(cam)
    return vdot(vsub(p, eye), forward)


def apply_camera_fit(scene: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-frame views declared with ``camera.fit``.

    ``fit: true`` uses every object visible in the view; ``fit: [id, ...]`` uses
    the listed objects.  Only orthographic cameras are supported: the target is
    moved to the projected centre and the ortho scale is enlarged until the
    projected bounds plus margin fit the frame.
    """
    world = scene.get("world", {})
    if not world.get("objects"):
        return scene
    fig = scene.get("figure", {})
    try:
        prims, per_obj = expand_world(scene)
    except Exception:  # noqa: BLE001
        return scene

    layer_of = {o["id"]: obj_layer(o) for o in world["objects"]}
    for view in scene.get("views", []):
        cam = view.get("camera", {})
        spec = cam.get("fit")
        if not spec:
            continue
        if cam.get("type", "ortho") != "ortho":
            continue
        if isinstance(spec, list):
            ids = [i for i in spec if i in per_obj]
        else:
            layers = view.get("layers")
            hide = set(view.get("hide", []) or [])
            ids = [o["id"] for o in world["objects"]
                   if o["id"] not in hide and (layers is None or layer_of[o["id"]] in layers)]
        ids = [i for i in ids if i not in set(cam.get("fit_ignore", []) or [])]
        pts: List[Vec3] = []
        for oid in ids:
            for p in per_obj.get(oid, []):
                pts.extend(primitive_points(p))
        if not pts:
            continue
        panel = None
        for p in fig.get("panels", []):
            if p.get("view") == view["id"]:
                panel = p
                break
        W, H = view_resolution(view, panel, fig)
        base = tuple(float(v) for v in cam.get("target", [0, 0, 0]))
        eye, right, up, forward = camera_basis(dict(cam, target=base))
        us = [vdot(vsub(p, base), right) for p in pts]
        vs = [vdot(vsub(p, base), up) for p in pts]
        uc = (min(us) + max(us)) / 2.0
        vc = (min(vs) + max(vs)) / 2.0
        need_w = max(us) - min(us)
        need_h = max(vs) - min(vs)
        margin = float(cam.get("fit_margin", 0.07))
        scale = max(need_w, need_h * (W / float(H))) * (1.0 + margin)
        cam["target"] = [base[i] + right[i] * uc + up[i] * vc for i in range(3)]
        cam["ortho_scale"] = round(float(scale), 4)
    return scene



def _aabb_of_points(points: Iterable[Sequence[float]]) -> Tuple[Vec3, Vec3]:
    pts = list(points)
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return (tuple(lo), tuple(hi))


def primitive_points(p: Dict[str, Any]) -> List[Vec3]:
    k = p["kind"]
    if k == "box":
        return _box_corners(p["center"], p["size"], p["rot"])
    if k == "sphere":
        c, r = p["center"], p["radius"]
        return [(c[0] + r, c[1], c[2]), (c[0] - r, c[1], c[2]),
                (c[0], c[1] + r, c[2]), (c[0], c[1] - r, c[2]),
                (c[0], c[1], c[2] + r), (c[0], c[1], c[2] - r)]
    if k == "ellipsoid":
        c, r = p["center"], p["radii"]
        return [(c[0] + r[0], c[1], c[2]), (c[0] - r[0], c[1], c[2]),
                (c[0], c[1] + r[1], c[2]), (c[0], c[1] - r[1], c[2]),
                (c[0], c[1], c[2] + r[2]), (c[0], c[1], c[2] - r[2])]
    if k == "cylinder":
        c, r, d = p["center"], p["radius"], p["depth"]
        return [(c[0] + r, c[1], c[2] + d / 2), (c[0] - r, c[1], c[2] - d / 2)]
    if k == "tube":
        return [p["a"], p["b"]]
    if k == "polyline":
        return list(p["points"])
    if k == "frustum":
        return [p["apex"]] + list(p["corners"])
    return []


def obj_bbox(prims: List[Dict[str, Any]]) -> Tuple[Vec3, Vec3]:
    pts: List[Vec3] = []
    for p in prims:
        pts.extend(primitive_points(p))
    if not pts:
        return (0, 0, 0), (0, 0, 0)
    return _aabb_of_points(pts)


# --------------------------------------------------------------------------
# geometry validation
# --------------------------------------------------------------------------
def _box_overlap_sat(a: Dict[str, Any], b: Dict[str, Any], tol: float = 1e-4) -> bool:
    """Separating axis test for two oriented boxes."""
    ca, sa = a["center"], a["size"]
    cb, sb = b["center"], b["size"]
    axes_a = box_axes(a["rot"])
    axes_b = box_axes(b["rot"])
    t = vsub(cb, ca)
    for ax in list(axes_a) + list(axes_b):
        ra = sum(abs(vdot(ax, axes_a[i])) * sa[i] / 2 for i in range(3))
        rb = sum(abs(vdot(ax, axes_b[i])) * sb[i] / 2 for i in range(3))
        if abs(vdot(t, ax)) > ra + rb - tol:
            return False
    for i in range(3):
        for j in range(3):
            ax = vcross(axes_a[i], axes_b[j])
            if vlen(ax) < 1e-6:
                continue
            ax = vnorm(ax)
            ra = sum(abs(vdot(ax, axes_a[k])) * sa[k] / 2 for k in range(3))
            rb = sum(abs(vdot(ax, axes_b[k])) * sb[k] / 2 for k in range(3))
            if abs(vdot(t, ax)) > ra + rb - tol:
                return False
    return True


def point_box_distance(p: Sequence[float], box: Dict[str, Any]) -> float:
    axes = box_axes(box["rot"])
    d = vsub(p, box["center"])
    half = [box["size"][i] / 2.0 for i in range(3)]
    dist2 = 0.0
    for i in range(3):
        local = vdot(d, axes[i])
        excess = abs(local) - half[i]
        if excess > 0:
            dist2 += excess * excess
    return math.sqrt(dist2)


def point_primitive_distance(p: Sequence[float], prim: Dict[str, Any]) -> float:
    k = prim["kind"]
    if k == "box":
        return point_box_distance(p, prim)
    if k == "sphere":
        return max(0.0, vlen(vsub(p, prim["center"])) - prim["radius"])
    if k == "ellipsoid":
        c, r = prim["center"], prim["radii"]
        d = vsub(p, c)
        # normalized ellipsoid distance approximation
        q = math.sqrt((d[0] / r[0]) ** 2 + (d[1] / r[1]) ** 2 + (d[2] / r[2]) ** 2)
        return max(0.0, (q - 1.0) * min(r))
    if k == "cylinder":
        d = vsub(p, prim["center"])
        radial = math.sqrt(d[0] ** 2 + d[1] ** 2) - prim["radius"]
        axial = abs(d[2]) - prim["depth"] / 2.0
        return math.sqrt(max(0.0, radial) ** 2 + max(0.0, axial) ** 2)
    if k == "tube":
        a, b = prim["a"], prim["b"]
        ab = vsub(b, a)
        t = vdot(vsub(p, a), ab) / max(vdot(ab, ab), EPS)
        t = max(0.0, min(1.0, t))
        return max(0.0, vlen(vsub(p, vadd(a, vmul(ab, t)))) - max(prim["r1"], prim["r2"]))
    if k == "polyline":
        pts = prim["points"]
        best = 1e9
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            ab = vsub(b, a)
            t = vdot(vsub(p, a), ab) / max(vdot(ab, ab), EPS)
            t = max(0.0, min(1.0, t))
            best = min(best, vlen(vsub(p, vadd(a, vmul(ab, t)))))
        return best
    return 1e9


def validate_scene(scene: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    info: List[str] = []

    if not isinstance(scene, dict):
        return {"ok": False, "errors": ["scene is not an object"], "warnings": [], "info": []}

    world = scene.get("world")
    if not isinstance(world, dict):
        errors.append("missing 'world'")
        world = {}
    objects = world.get("objects", [])
    if not objects:
        errors.append("world.objects is empty")
    seen_ids = set()
    for o in objects:
        oid = o.get("id")
        if not oid:
            errors.append("object without id: %r" % (o.get("type"),))
        elif oid in seen_ids:
            errors.append("duplicate object id %r" % oid)
        seen_ids.add(oid)
        if o.get("type") not in EXPANDERS and o.get("type") != "points":
            errors.append("object %r has unknown type %r" % (oid, o.get("type")))
        if "layer" not in o:
            warnings.append("object %r has no layer; every view shows it" % oid)

    views = scene.get("views", [])
    if not views:
        errors.append("scene has no views")
    view_ids = set()
    for v in views:
        if not v.get("id"):
            errors.append("view without id")
        elif v["id"] in view_ids:
            errors.append("duplicate view id %r" % v["id"])
        view_ids.add(v["id"])
        cam = v.get("camera", {})
        if cam.get("type", "ortho") == "ortho" and float(cam.get("ortho_scale", 0)) <= 0:
            errors.append("view %r: ortho_scale must be > 0" % v.get("id"))

    fig = scene.get("figure", {})
    for panel in fig.get("panels", []):
        if panel.get("view") not in view_ids:
            errors.append("panel %r references unknown view %r" % (panel.get("id"), panel.get("view")))

    return {"ok": not errors, "errors": errors, "warnings": warnings, "info": info}


def check_geometry(scene: Dict[str, Any], strict: bool = False) -> Dict[str, Any]:
    """Full physical/visual consistency check on the expanded geometry."""
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {}

    world = scene.get("world", {})
    vs = world_voxel_size(world)
    origin = world_origin(world)
    objects = world.get("objects", [])
    by_id = {o["id"]: o for o in objects}

    try:
        prims, per_obj = expand_world(scene)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "errors": ["expansion failed: %s" % exc],
                "warnings": [], "stats": {}}

    # --- grid alignment -----------------------------------------------------
    misaligned = 0
    for o in objects:
        if o.get("type") not in ("voxels", "blocks", "brick_wall"):
            continue
        align = o.get("align", "half" if o.get("type") == "brick_wall" else "lattice")
        if align == "free":
            continue
        step = 2.0 if align == "half" else 1.0
        for p in per_obj.get(o["id"], []):
            if p["kind"] != "box":
                continue
            gap = float(p.get("gap", 0.0))
            if o.get("type") == "voxels" and "cell" in p:
                c = p["cell"]
                for i, ci in enumerate(c):
                    if abs(ci - round(ci)) > 1e-6:
                        misaligned += 1
                        if misaligned <= 6:
                            errors.append("voxel misalignment: object %r has non-integer cell %r" %
                                          (o["id"], c))
                        break
                    base = origin[i] + ci * vs
                    if i == 2:
                        lo = p["center"][i] - p["size"][i] / 2.0   # tiles sit on the cell floor
                    else:
                        lo = p["center"][i] - p["size"][i] / 2.0 - gap / 2.0
                    if abs(lo - base) > 1e-4:
                        misaligned += 1
                        if misaligned <= 6:
                            errors.append("voxel misalignment: object %r cell %r axis %d "
                                          "starts at %.4f, expected %.4f" %
                                          (o["id"], c, i, lo, base))
                continue
            half_xy = [(p["size"][i] + gap) / 2.0 for i in range(3)]
            for i in range(3):
                half = half_xy[i]
                for edge in (p["center"][i] - half, p["center"][i] + half):
                    q = (edge - origin[i]) / vs * step
                    if abs(q - round(q)) > 1e-4:
                        misaligned += 1
                        if misaligned <= 6:
                            errors.append("voxel misalignment: object %r face at %.4f voxels on axis %d" %
                                          (o["id"], (edge - origin[i]) / vs, i))
            for i in range(3):
                n = (p["size"][i] + gap) / vs
                if abs(n - round(n)) > 1e-4:
                    misaligned += 1
                    if misaligned <= 6:
                        errors.append("voxel misalignment: object %r size axis %d = %.4f voxels" %
                                      (o["id"], i, n))
    stats["misaligned_axes"] = misaligned

    # --- duplicate cells ----------------------------------------------------
    occupied: Dict[Tuple[int, int, int], str] = {}
    duplicates = 0
    for o in objects:
        if o.get("type") != "voxels":
            continue
        for c in voxel_cells(o):
            if c in occupied:
                duplicates += 1
                if duplicates <= 8:
                    errors.append("voxel cell %r declared by both %r and %r" % (c, occupied[c], o["id"]))
            else:
                occupied[c] = o["id"]
    stats["free_cells"] = len(occupied)
    stats["duplicate_cells"] = duplicates

    # --- solid vs solid / solid vs free ------------------------------------
    solids = [p for p in prims if p.get("obj_role") == "solid"]
    frees = [p for p in prims if p.get("obj_role") == "free"]
    stats["solid_primitives"] = len(solids)
    stats["free_primitives"] = len(frees)

    allow_pairs = set()
    for o in objects:
        for other in o.get("allow_intersections", []) or []:
            allow_pairs.add((o["id"], other))
            allow_pairs.add((other, o["id"]))

    overlap_count = 0
    for i in range(len(solids)):
        for j in range(i + 1, len(solids)):
            a, b = solids[i], solids[j]
            if a["obj"] == b["obj"] and (a.get("composite") or b.get("composite")):
                continue
            if (a["obj"], b["obj"]) in allow_pairs:
                continue
            if a["kind"] == "box" and b["kind"] == "box":
                if _box_overlap_sat(a, b, tol=2e-3):
                    overlap_count += 1
                    if overlap_count <= 8:
                        errors.append("solid overlap: %s <-> %s near %s" %
                                      (a["obj"], b["obj"],
                                       tuple(round(v, 3) for v in a["center"])))
    stats["solid_overlaps"] = overlap_count

    solid_free_overlap = 0
    for f in frees:
        for s in solids:
            if (f["obj"], s["obj"]) in allow_pairs:
                continue
            if f["kind"] == "box" and s["kind"] == "box":
                if _box_overlap_sat(f, s, tol=2e-3):
                    solid_free_overlap += 1
                    if solid_free_overlap <= 8:
                        errors.append("free voxel %s at %r intersects solid %s "
                                      "(occupied and free space must be exclusive)" %
                                      (f["obj"], f.get("cell"), s["obj"]))
    stats["free_solid_overlaps"] = solid_free_overlap

    # --- point constraints -------------------------------------------------
    marker_groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in prims:
        if p["kind"] == "sphere" and p.get("obj_role") == "marker":
            marker_groups.setdefault(p["obj"], []).append(p)

    surface_check_errors = 0
    for o in objects:
        cons = o.get("constraints", {}) or {}
        pts = marker_groups.get(o["id"], [])
        if not pts:
            continue
        if "on_surface_of" in cons:
            tol = float(cons.get("tolerance", 0.03))
            target_ids = cons["on_surface_of"]
            target_prims = [p for tid in target_ids for p in per_obj.get(tid, [])
                            if p["kind"] == "box"]
            bad = []
            for p in pts:
                best = min((point_box_distance(p["center"], tp) for tp in target_prims),
                           default=1e9)
                if best > tol:
                    bad.append((p["center"], best))
            stats.setdefault("surface_points", {})[o["id"]] = len(pts) - len(bad)
            if bad:
                surface_check_errors += len(bad)
                errors.append("%d/%d points of %r are farther than %.3f from the surface of %s "
                              "(worst %.3f at %r)" %
                              (len(bad), len(pts), o["id"], tol, target_ids,
                               max(b[1] for b in bad),
                               tuple(round(v, 2) for v in bad[0][0])))
        if "in_free_space" in cons:
            margin = float(cons.get("surface_margin", 0.02))
            inside = 0
            for p in pts:
                if _point_in_free_cells(p["center"], occupied, world):
                    inside += 1
                else:
                    errors.append("point of %r at %r is not inside any declared free voxel" %
                                  (o["id"], tuple(round(v, 2) for v in p["center"])))
                    break
            # distance from solid surfaces
            near = []
            for p in pts:
                d = min((point_primitive_distance(p["center"], s) for s in solids), default=1e9)
                if d < margin:
                    near.append((p["center"], d))
            if near:
                errors.append("%d points of %r are within %.3f of a solid surface "
                              "(dynamic points must lie in free space)" %
                              (len(near), o["id"], margin))
            stats.setdefault("free_points", {})[o["id"]] = inside

    stats["point_surface_errors"] = surface_check_errors

    # --- camera framing ----------------------------------------------------
    view_report = {}
    for v in scene.get("views", []):
        cam = v.get("camera", {})
        panel = None
        for p in scene.get("figure", {}).get("panels", []):
            if p.get("view") == v["id"]:
                panel = p
                break
        res = view_resolution(v, panel, scene.get("figure", {}))
        W, H = res
        layers = v.get("layers")
        hide = set(v.get("hide", []) or [])

        clipped = []
        fully_out = []
        for o in objects:
            oid = o["id"]
            if hide and oid in hide:
                continue
            if layers and obj_layer(o) not in layers:
                continue
            if oid in (v.get("ignore_clip", []) or []):
                continue
            pts = [primitive_points(p) for p in per_obj.get(oid, [])]
            pts = [q for sub in pts for q in sub]
            if not pts:
                continue
            px = [project_point(p, cam, res) for p in pts]
            xs = [p[0] for p in px]
            ys = [p[1] for p in px]
            margin = float(v.get("clip_margin", 0.0)) * min(W, H)
            if max(xs) < -margin or min(xs) > W + margin or max(ys) < -margin or min(ys) > H + margin:
                fully_out.append(oid)
            elif min(xs) < margin or max(xs) > W - margin or min(ys) < margin or max(ys) > H - margin:
                clipped.append(oid)
        if fully_out:
            errors.append("view %r: objects entirely outside the frame: %s" %
                          (v["id"], ", ".join(fully_out)))
        if clipped:
            msg = "view %r: objects clipped by the frame: %s" % (v["id"], ", ".join(clipped))
            if strict or v.get("require_contained"):
                errors.append(msg)
            else:
                warnings.append(msg)
        view_report[v["id"]] = {"clipped": clipped, "outside": fully_out, "resolution": res}
    stats["views"] = view_report

    # --- rays should end on something --------------------------------------
    for o in objects:
        if o.get("type") != "rays" or not o.get("require_hit", False):
            continue
        targets = [p["b"] for p in per_obj.get(o["id"], []) if p["kind"] == "tube"]
        obstacles = [p for p in prims if p.get("obj_role") in ("solid", "free", "marker")]
        for t in targets:
            d = min((point_primitive_distance(t, s) for s in obstacles), default=1e9)
            if d > float(o.get("hit_tolerance", 0.05)):
                warnings.append("ray of %r ends in open space at %r (%.3f from geometry)" %
                                (o["id"], tuple(round(v, 2) for v in t), d))

    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


def _point_in_free_cells(p: Sequence[float], occupied: Dict[Tuple[int, int, int], str],
                         world: Dict[str, Any]) -> bool:
    vs = world_voxel_size(world)
    o = world_origin(world)
    c = (math.floor((p[0] - o[0]) / vs), math.floor((p[1] - o[1]) / vs),
         math.floor((p[2] - o[2]) / vs))
    return tuple(c) in occupied


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------
def scene_summary(scene: Dict[str, Any]) -> str:
    world = scene.get("world", {})
    lines = []
    lines.append("scene: %s" % scene.get("meta", {}).get("title", "(untitled)"))
    lines.append("voxel_size: %s   objects: %d   views: %d" %
                 (world_voxel_size(world), len(world.get("objects", [])),
                  len(scene.get("views", []))))
    try:
        _, per_obj = expand_world(scene)
    except Exception as exc:  # noqa: BLE001
        return "\n".join(lines + ["expansion failed: %s" % exc])
    for o in world.get("objects", []):
        prims = per_obj.get(o["id"], [])
        lo, hi = obj_bbox(prims)
        lines.append("  %-22s %-12s layer=%-14s prims=%-4d bbox=%s..%s" % (
            o["id"], o.get("type"), obj_layer(o), len(prims),
            tuple(round(v, 2) for v in lo), tuple(round(v, 2) for v in hi)))
    return "\n".join(lines)
