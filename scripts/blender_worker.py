"""Blender backend for scientific-3d-illustration.

Run headless, for example::

    blender -b --factory-startup --python blender_worker.py -- \
        --scene scene.json --out out --views stage1,stage2

The script never requires the GUI.  Geometry comes exclusively from the scene
JSON through ``s3d_core.expand_world`` so that the validator and the renderer
can never disagree about the geometry.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time

import bpy
import bmesh
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import s3d_core as core  # noqa: E402


# --------------------------------------------------------------------------
# colour helpers
# --------------------------------------------------------------------------
def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgba(value, alpha: float = 1.0):
    if isinstance(value, (list, tuple)):
        r, g, b = value[0], value[1], value[2]
        a = value[3] if len(value) > 3 else alpha
        return (r, g, b, a)
    s = str(value).lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    r = int(s[0:2], 16) / 255.0
    g = int(s[2:4], 16) / 255.0
    b = int(s[4:6], 16) / 255.0
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), alpha)


# --------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------
def set_input(node, name: str, value):
    if name in node.inputs:
        node.inputs[name].default_value = value


def build_principled(name: str, spec: dict, fallback_color="#CCCCCC"):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    base = hex_rgba(spec.get("base_color", fallback_color))
    alpha = float(spec.get("alpha", 1.0))

    set_input(bsdf, "Base Color", base)
    set_input(bsdf, "Roughness", float(spec.get("roughness", 0.4)))
    set_input(bsdf, "Metallic", float(spec.get("metallic", 0.0)))
    set_input(bsdf, "IOR", float(spec.get("ior", 1.45)))
    set_input(bsdf, "Alpha", alpha)
    set_input(bsdf, "Specular IOR Level", float(spec.get("specular", 0.5)))
    set_input(bsdf, "Coat Weight", float(spec.get("coat", 0.0)))
    if "transmission" in spec:
        set_input(bsdf, "Transmission Weight", float(spec["transmission"]))
    if "emission" in spec:
        set_input(bsdf, "Emission Color", hex_rgba(spec["emission"]))
        set_input(bsdf, "Emission Strength", float(spec.get("emission_strength", 0.2)))

    mat.diffuse_color = base
    mat.use_backface_culling = False
    if hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND" if alpha < 0.999 else "OPAQUE"
    if hasattr(mat, "shadow_method"):
        mat.shadow_method = "HASHED" if alpha < 0.999 else "OPAQUE"
    return mat


def build_emission(name: str, color, strength: float = 1.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = hex_rgba(color)
    emit.inputs["Strength"].default_value = strength
    nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])
    mat.diffuse_color = hex_rgba(color)
    return mat


def material_registry(style: dict):
    mats = {}
    for name, spec in style.get("materials", {}).items():
        if spec.get("kind") == "flat":
            mats[name] = build_emission("s3d_" + name, spec.get("emission", spec.get("base_color", "#888888")),
                                        float(spec.get("emission_strength", 1.0)))
        else:
            mats[name] = build_principled("s3d_" + name, spec)
    mats["edge"] = build_emission("s3d_edge", style.get("edge", {}).get("color", "#6FA8D8"),
                                  float(style.get("edge", {}).get("emission", 1.0)))
    return mats


def mat_spec(style: dict, name: str) -> dict:
    return style.get("materials", {}).get(name, {})


# --------------------------------------------------------------------------
# geometry accumulation
# --------------------------------------------------------------------------
class MeshBuilder:
    def __init__(self, smooth: bool = False):
        self.bm = bmesh.new()
        self.smooth = smooth

    def add_raw(self, verts, faces, matrix):
        bm = self.bm
        mv = [bm.verts.new(matrix @ Vector(v)) for v in verts]
        for f in faces:
            try:
                face = bm.faces.new([mv[i] for i in f])
                face.smooth = self.smooth
            except ValueError:
                pass

    def finish(self, name: str, collection, material):
        try:
            bmesh.ops.remove_doubles(self.bm, verts=list(self.bm.verts), dist=1e-5)
        except Exception:  # noqa: BLE001
            pass
        mesh = bpy.data.meshes.new(name)
        self.bm.to_mesh(mesh)
        self.bm.free()
        mesh.materials.append(material)
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        return obj


def box_matrix(center, size, rot_deg):
    rx, ry, rz = (math.radians(a) for a in rot_deg)
    m = (Matrix.Rotation(rz, 4, "Z") @ Matrix.Rotation(ry, 4, "Y") @
         Matrix.Rotation(rx, 4, "X"))
    return Matrix.Translation(Vector(center)) @ m @ Matrix.Diagonal(
        Vector((size[0], size[1], size[2], 1.0)))


def cube_template(bevel: float):
    key = ("cube", round(bevel, 5))
    if key in _TEMPLATES:
        return _TEMPLATES[key]
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    if bevel > 0:
        geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
        bmesh.ops.bevel(bm, geom=geom, offset=bevel, offset_type="OFFSET",
                        segments=2, profile=0.5, affect="EDGES",
                        clamp_overlap=True, material=-1)
    verts = [tuple(v.co) for v in bm.verts]
    faces = [tuple(v.index for v in f.verts) for f in bm.faces]
    bm.free()
    _TEMPLATES[key] = (verts, faces)
    return _TEMPLATES[key]


def sphere_template(subdiv: int = 2):
    key = ("ico", subdiv)
    if key in _TEMPLATES:
        return _TEMPLATES[key]
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdiv, radius=1.0)
    verts = [tuple(v.co) for v in bm.verts]
    faces = [tuple(v.index for v in f.verts) for f in bm.faces]
    bm.free()
    _TEMPLATES[key] = (verts, faces)
    return _TEMPLATES[key]


def cylinder_template(segments: int = 24):
    key = ("cyl", segments)
    if key in _TEMPLATES:
        return _TEMPLATES[key]
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments,
                          radius1=0.5, radius2=0.5, depth=1.0)
    verts = [tuple(v.co) for v in bm.verts]
    faces = [tuple(v.index for v in f.verts) for f in bm.faces]
    bm.free()
    _TEMPLATES[key] = (verts, faces)
    return _TEMPLATES[key]


_TEMPLATES = {}


def add_cone(builder: MeshBuilder, a: Vector, b: Vector, r1: float, r2: float, segments=16):
    d = b - a
    length = d.length
    if length < 1e-6:
        return
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=True, segments=segments,
                          radius1=max(r1, 1e-5), radius2=max(r2, 1e-5), depth=length)
    verts = [tuple(v.co) for v in bm.verts]
    faces = [tuple(v.index for v in f.verts) for f in bm.faces]
    bm.free()
    rot = d.to_track_quat("Z", "Y").to_matrix().to_4x4()
    builder.add_raw(verts, faces, Matrix.Translation((a + b) / 2.0) @ rot)


def add_box_edges(builder: MeshBuilder, center, size, rot_deg, width: float):
    """Thin bars along the 12 edges -> crisp technical outlines."""
    w = width
    hx, hy, hz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    tmpl = cube_template(0.0)
    rot = Matrix.Rotation(math.radians(rot_deg[2]), 4, "Z") @ \
        Matrix.Rotation(math.radians(rot_deg[1]), 4, "Y") @ \
        Matrix.Rotation(math.radians(rot_deg[0]), 4, "X")
    for axis in range(3):
        if axis == 0:
            lens, offs = size[0] + w, [(0, -hy, -hz), (0, -hy, hz), (0, hy, -hz), (0, hy, hz)]
        elif axis == 1:
            lens, offs = size[1] + w, [(-hx, 0, -hz), (-hx, 0, hz), (hx, 0, -hz), (hx, 0, hz)]
        else:
            lens, offs = size[2] + w, [(-hx, -hy, 0), (-hx, hy, 0), (hx, -hy, 0), (hx, hy, 0)]
        for off in offs:
            s = [w, w, w]
            s[axis] = lens
            local = Matrix.Translation(Vector(off)) @ Matrix.Diagonal(
                Vector((s[0], s[1], s[2], 1.0)))
            builder.add_raw(tmpl[0], tmpl[1], Matrix.Translation(Vector(center)) @ rot @ local)


def emit_primitive(builder: MeshBuilder, p: dict, bevel: float):
    kind = p["kind"]
    if kind == "box":
        tmpl = cube_template(bevel)
        builder.add_raw(tmpl[0], tmpl[1], box_matrix(p["center"], p["size"], p["rot"]))
    elif kind == "sphere":
        r = p["radius"]
        tmpl = sphere_template(2)
        builder.add_raw(tmpl[0], tmpl[1],
                        Matrix.Translation(Vector(p["center"])) @
                        Matrix.Diagonal(Vector((r, r, r, 1.0))))
    elif kind == "ellipsoid":
        tmpl = sphere_template(3)
        rx, ry, rz = p["radii"]
        shells = max(1, int(p.get("shells", 1)))
        for s in range(shells, 0, -1):
            k = (s / float(shells)) ** 0.55
            builder.add_raw(tmpl[0], tmpl[1],
                            Matrix.Translation(Vector(p["center"])) @
                            Matrix.Diagonal(Vector((rx * k, ry * k, rz * k, 1.0))))
    elif kind == "cylinder":
        tmpl = cylinder_template(28)
        r, d = p["radius"], p["depth"]
        builder.add_raw(tmpl[0], tmpl[1],
                        Matrix.Translation(Vector(p["center"])) @
                        Matrix.Diagonal(Vector((2 * r, 2 * r, d, 1.0))))
    elif kind == "tube":
        a, b = Vector(p["a"]), Vector(p["b"])
        add_cone(builder, a, b, p["r1"], p["r2"], 16)
        if p.get("arrow"):
            d = b - a
            head = min(float(p.get("arrow_size", p["r1"] * 6.0)) * 1.8, d.length * 0.85)
            base = b - d.normalized() * head
            add_cone(builder, base, b, max(p["r1"] * 3.0, head * 0.42), 1e-4, 20)
    elif kind == "polyline":
        pts = [Vector(q) for q in p["points"]]
        for i in range(len(pts) - 1):
            add_cone(builder, pts[i], pts[i + 1], p["radius"], p["radius"], 10)
        if p.get("arrow") and len(pts) >= 2:
            d = pts[-1] - pts[-2]
            head = min(p["radius"] * 12.0, d.length * 0.5)
            base = pts[-1] - d.normalized() * head
            add_cone(builder, base, pts[-1], p["radius"] * 4.5, 1e-4, 16)
    elif kind == "frustum":
        apex = Vector(p["apex"])
        corners = [Vector(c) for c in p["corners"]]
        if p.get("faces"):
            bm = bmesh.new()
            va = bm.verts.new(apex)
            vc = [bm.verts.new(c) for c in corners]
            for i in range(4):
                bm.faces.new([va, vc[i], vc[(i + 1) % 4]])
            bm.faces.new(list(reversed(vc)))
            verts = [tuple(v.co) for v in bm.verts]
            faces = [tuple(v.index for v in f.verts) for f in bm.faces]
            bm.free()
            builder.add_raw(verts, faces, Matrix.Identity(4))
        for i, c in enumerate(corners):
            nxt = corners[(i + 1) % 4]
            add_cone(builder, apex, c, p["edge_radius"], p["edge_radius"], 8)
            add_cone(builder, c, nxt, p["edge_radius"], p["edge_radius"], 8)


# --------------------------------------------------------------------------
# scene construction
# --------------------------------------------------------------------------
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.render.fps = 24
    return scene


def make_collections(scene):
    root = bpy.data.collections.new("S3D")
    scene.collection.children.link(root)
    rig = bpy.data.collections.new("RIG")
    root.children.link(rig)
    return root, rig


def build_world(scene, scene_data, style, mats, root):
    world = scene_data.get("world", {})
    prims, per_obj = core.expand_world(scene_data)
    style_edge = style.get("edge", {})
    edge_enabled = bool(style_edge.get("enabled", True))

    layers = {}
    obj_collections = {}

    def layer_col(name):
        if name not in layers:
            c = bpy.data.collections.new("layer_" + name)
            root.children.link(c)
            layers[name] = c
        return layers[name]

    obj_layer_map = {o["id"]: core.obj_layer(o) for o in world.get("objects", [])}

    def ensure_obj_col(obj_id):
        col = obj_collections.get(obj_id)
        if col is None:
            col = bpy.data.collections.new("obj_" + obj_id)
            layer_col(obj_layer_map.get(obj_id, "default")).children.link(col)
            obj_collections[obj_id] = col
        return col

    grouped = {}
    for p in prims:
        grouped.setdefault((p["obj"], p["mat"]), []).append(p)

    edge_mats = {}
    objects_by_mat = {}
    for (obj_id, mat_name), plist in grouped.items():
        spec = mat_spec(style, mat_name)
        bevel = float(spec.get("bevel", 0.008))
        smooth = mat_name.startswith("point_") or mat_name == "gauss"
        builder = MeshBuilder(smooth=smooth)
        for p in plist:
            emit_primitive(builder, p, bevel)
        obj = builder.finish("%s__%s" % (obj_id, mat_name), ensure_obj_col(obj_id),
                             mats[mat_name])
        obj["s3d_object"] = obj_id
        obj["s3d_mat"] = mat_name
        objects_by_mat.setdefault(mat_name, []).append(obj)

        # crisp edge overlay for box-like geometry
        if not edge_enabled or spec.get("alpha", 1.0) < 0.02:
            continue
        w = float(spec.get("edge_width", style_edge.get("width", 0.005)))
        if w <= 0:
            continue
        boxes = [p for p in plist if p["kind"] == "box"]
        if not boxes:
            continue
        key = spec.get("edge_color", style_edge.get("color", "#6FA8D8"))
        if key not in edge_mats:
            edge_mats[key] = build_principled(
                "s3d_edge_" + str(len(edge_mats)),
                {"base_color": key, "emission": key, "emission_strength":
                 float(style_edge.get("emission", 1.0)), "roughness": 1.0,
                 "specular": 0.0, "alpha": float(spec.get("edge_alpha", 1.0))})
        eb = MeshBuilder()
        for p in boxes:
            add_box_edges(eb, p["center"], p["size"], p["rot"], w)
        eobj = eb.finish("edge__%s__%s" % (obj_id, mat_name), ensure_obj_col(obj_id),
                         edge_mats[key])
        eobj["s3d_object"] = obj_id
        eobj["s3d_mat"] = mat_name
        objects_by_mat.setdefault(mat_name, []).append(eobj)

    return obj_collections, layers, objects_by_mat


# --------------------------------------------------------------------------
# lighting / camera / render
# --------------------------------------------------------------------------
def build_lighting(scene, style, rig):
    light_cfg = style.get("lighting", {})
    world_cfg = light_cfg.get("world", {})
    world = bpy.data.worlds.new("S3DWorld")
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    bg.inputs["Color"].default_value = hex_rgba(world_cfg.get("color", [0.9, 0.95, 1.0]))[:3] + (1.0,)
    bg.inputs["Strength"].default_value = float(world_cfg.get("strength", 0.5))
    scene.world = world

    for spec in light_cfg.get("lights", []):
        data = bpy.data.lights.new(spec.get("name", "light"), spec.get("type", "AREA"))
        data.energy = float(spec.get("energy", 300))
        if hasattr(data, "size"):
            data.size = float(spec.get("size", 5.0))
        data.color = tuple(hex_rgba(spec.get("color", "#FFFFFF"))[:3])
        obj = bpy.data.objects.new(spec.get("name", "light"), data)
        rig.objects.link(obj)
        pos = Vector(spec.get("position", [0, 0, 5]))
        tgt = Vector(spec.get("target", [0, 0, 0]))
        obj.location = pos
        obj.rotation_euler = (tgt - pos).to_track_quat("-Z", "Y").to_euler()


def build_camera(scene, view, rig, res):
    cam_data = bpy.data.cameras.new("cam_" + view["id"])
    cam = view.get("camera", {})
    ctype = cam.get("type", "ortho")
    cam_data.type = "ORTHO" if ctype == "ortho" else "PERSP"
    if ctype == "ortho":
        cam_data.ortho_scale = float(cam.get("ortho_scale", 8.0))
    else:
        cam_data.lens = float(cam.get("lens", 50.0))
        cam_data.sensor_fit = "HORIZONTAL"
    cam_data.clip_start = float(cam.get("clip_start", 0.05))
    cam_data.clip_end = float(cam.get("clip_end", 200.0))

    eye, right, up, forward = core.camera_basis(cam)
    m = Matrix((
        (right[0], up[0], -forward[0], eye[0]),
        (right[1], up[1], -forward[1], eye[1]),
        (right[2], up[2], -forward[2], eye[2]),
        (0.0, 0.0, 0.0, 1.0),
    ))
    obj = bpy.data.objects.new("cam_" + view["id"], cam_data)
    rig.objects.link(obj)
    obj.matrix_world = m
    scene.camera = obj
    scene.render.resolution_x = res[0]
    scene.render.resolution_y = res[1]
    scene.render.resolution_percentage = 100
    return obj


def build_shadow_catcher(scene, style, rig, z_min):
    cfg = style.get("render", {})
    if not cfg.get("shadow_catcher", True):
        return
    mesh = bpy.data.meshes.new("shadow_catcher")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=60.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("shadow_catcher", mesh)
    rig.objects.link(obj)
    obj.location = (0, 0, z_min - 0.004)
    obj.is_shadow_catcher = True
    obj.visible_diffuse = False
    obj.visible_glossy = False
    return obj


def configure_render(scene, style, preview=False):
    cfg = style.get("render", {})
    engine = cfg.get("engine", "cycles")
    scene.render.engine = "CYCLES" if engine == "cycles" else "BLENDER_EEVEE_NEXT"
    scene.render.film_transparent = bool(cfg.get("transparent", True))
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 15
    scene.render.filter_size = float(cfg.get("filter_width", 1.4))
    scene.view_settings.view_transform = cfg.get("view_transform", "Standard")
    scene.view_settings.look = cfg.get("look", "None")
    scene.view_settings.exposure = float(cfg.get("exposure", 0.0))
    scene.view_settings.gamma = 1.0

    if engine == "cycles":
        cy = scene.cycles
        cy.samples = int(cfg.get("preview_samples", 32) if preview else cfg.get("samples", 128))
        cy.use_adaptive_sampling = True
        cy.adaptive_threshold = float(cfg.get("adaptive_threshold", 0.01))
        cy.use_denoising = bool(cfg.get("denoise", True))
        cy.denoiser = "OPENIMAGEDENOISE"
        cy.max_bounces = int(cfg.get("max_bounces", 8))
        cy.transmission_bounces = int(cfg.get("transmission_bounces", 8))
        cy.transparent_max_bounces = int(cfg.get("transparent_bounces", 8))
        cy.glossy_bounces = 4
        cy.caustics_reflective = False
        cy.caustics_refractive = False
        cy.blur_glossy = 1.0
        cy.sample_clamp_indirect = 8.0
        setup_device(scene, cfg.get("device", "auto"))

    fs = cfg.get("freestyle", {})
    if fs.get("enabled"):
        scene.render.use_freestyle = True
        vl = scene.view_layers[0]
        vl.use_freestyle = True
        fs_settings = vl.freestyle_settings
        lineset = fs_settings.linesets[0] if fs_settings.linesets else fs_settings.linesets.new("S3D")
        lineset.select_silhouette = True
        lineset.select_border = True
        lineset.select_crease = True
        lineset.linestyle.color = hex_rgba(fs.get("color", "#4C6C8A"))[:3]
        lineset.linestyle.thickness = float(fs.get("thickness", 1.0))


def setup_device(scene, device):
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        if device in ("auto", "gpu"):
            for backend in ("OPTIX", "CUDA", "HIP", "METAL"):
                try:
                    prefs.compute_device_type = backend
                    prefs.get_devices()
                    gpus = [d for d in prefs.devices if d.type != "CPU"]
                    if gpus:
                        for d in prefs.devices:
                            d.use = d.type != "CPU"
                        scene.cycles.device = "GPU"
                        print("[s3d] using GPU backend", backend, len(gpus), "device(s)")
                        return
                except Exception:  # noqa: BLE001
                    continue
        scene.cycles.device = "CPU"
        print("[s3d] using CPU")
    except Exception as exc:  # noqa: BLE001
        print("[s3d] device setup failed:", exc)


def apply_visibility(view, obj_collections, layers):
    vis_layers = view.get("layers")
    hide_ids = set(view.get("hide", []) or [])
    for name, col in layers.items():
        col.hide_render = bool(vis_layers is not None and name not in vis_layers)
    for oid, col in obj_collections.items():
        col.hide_render = oid in hide_ids


_DIM_CACHE = {}


def dim_material(mat, factor: float):
    key = (mat.name, round(factor, 3))
    if key in _DIM_CACHE:
        return _DIM_CACHE[key]
    m = mat.copy()
    m.name = "%s_dim%d" % (mat.name, int(factor * 100))
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is not None:
        if "Alpha" in bsdf.inputs:
            bsdf.inputs["Alpha"].default_value = max(
                0.0, bsdf.inputs["Alpha"].default_value * factor)
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value *= factor
        if "Transmission Weight" in bsdf.inputs:
            bsdf.inputs["Transmission Weight"].default_value *= max(factor, 0.2)
        if hasattr(m, "blend_method"):
            m.blend_method = "BLEND"
    _DIM_CACHE[key] = m
    return m


def apply_dim(view, objects_by_mat, mats):
    dims = view.get("dim") or {}
    if not dims:
        return
    for name, objs in objects_by_mat.items():
        for obj in objs:
            if obj.data.materials:
                obj.data.materials[0] = mats.get(name, obj.data.materials[0])
    for name, factor in dims.items():
        base = mats.get(name)
        if base is None:
            continue
        for obj in objects_by_mat.get(name, []):
            if obj.data.materials:
                obj.data.materials[0] = dim_material(base, float(factor))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    args = {"scene": None, "out": None, "views": None, "blend": None,
            "preview": False, "resolution": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--scene":
            args["scene"] = argv[i + 1]; i += 2
        elif a == "--out":
            args["out"] = argv[i + 1]; i += 2
        elif a == "--views":
            args["views"] = argv[i + 1].split(","); i += 2
        elif a == "--blend":
            args["blend"] = argv[i + 1]; i += 2
        elif a == "--resolution":
            args["resolution"] = json.loads(argv[i + 1]); i += 2
        elif a == "--preview":
            args["preview"] = True; i += 1
        else:
            i += 1
    return args


def main():
    t0 = time.time()
    args = parse_args()
    scene_data = core.load_json(args["scene"])
    style = core.resolve_style(scene_data)

    scene = reset_scene()
    root, rig = make_collections(scene)
    mats = material_registry(style)
    obj_collections, layers, objects_by_mat = build_world(scene, scene_data, style, mats, root)
    build_lighting(scene, style, rig)

    prims, _ = core.expand_world(scene_data)
    zs = []
    for p in prims:
        pts = core.primitive_points(p)
        if pts:
            zs.append(min(q[2] for q in pts))
    z_min = min(zs) if zs else 0.0
    shadow_obj = build_shadow_catcher(scene, style, rig, z_min)
    configure_render(scene, style, preview=args["preview"])

    out_dir = args["out"]
    os.makedirs(out_dir, exist_ok=True)

    views = scene_data.get("views", [])
    wanted = set(args["views"]) if args["views"] else None
    res_map = args.get("resolution") or {}
    for view in views:
        if wanted is not None and view["id"] not in wanted:
            continue
        panel = None
        for p in scene_data.get("figure", {}).get("panels", []):
            if p.get("view") == view["id"]:
                panel = p
                break
        res = tuple(res_map.get(view["id"]) or core.view_resolution(
            view, panel, scene_data.get("figure", {})))
        build_camera(scene, view, rig, res)
        apply_visibility(view, obj_collections, layers)
        apply_dim(view, objects_by_mat, mats)
        if shadow_obj is not None:
            shadow_obj.hide_render = not bool(view.get("shadow", True))
        png = os.path.abspath(os.path.join(out_dir, "panel_%s.png" % view["id"]))
        scene.render.filepath = png
        print("[s3d] rendering %s at %dx%d" % (view["id"], res[0], res[1]))
        bpy.ops.render.render(write_still=True)
        print("[s3d] wrote", png)

    if args.get("blend"):
        os.makedirs(os.path.dirname(os.path.abspath(args["blend"])), exist_ok=True)
        try:
            bpy.context.preferences.filepaths.save_version = 0
        except Exception:  # noqa: BLE001
            pass
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args["blend"]))
        print("[s3d] saved", args["blend"])
    print("[s3d] done in %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
