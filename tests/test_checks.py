"""Behavioural tests for the geometry checker and the scene editor.

Run with:  python tests/test_checks.py
No pytest required; exits non-zero on failure.
"""

import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import s3d_core as core  # noqa: E402
import s3d  # noqa: E402

SCENE = os.path.join(ROOT, "examples", "dynamic-pointcloud-filter", "scene.json")


def load():
    # raw load: no camera fit, so injected camera errors stay visible
    return core.load_json(SCENE)


def obj(scene, oid):
    return next(o for o in scene["world"]["objects"] if o["id"] == oid)


def errors(scene):
    res = core.check_geometry(scene)
    return res, " | ".join(res["errors"])


def expect_ok(name, scene):
    res, text = errors(scene)
    assert res["ok"], "%s: expected clean, got: %s" % (name, text)
    print("  ok    %s" % name)


def expect_error(name, scene, needle):
    res, text = errors(scene)
    assert not res["ok"], "%s: expected an error, scene passed" % name
    assert needle in text, "%s: expected %r in: %s" % (name, needle, text)
    print("  ok    %s  -> %s" % (name, text.split(" | ")[0][:90]))


def main():
    print("checker")
    base = load()
    expect_ok("example scene is clean", base)

    s = copy.deepcopy(base)
    obj(s, "free_space_core")["voxel_size"] = 0.47
    expect_error("one object uses a different voxel scale", s, "misalignment")

    s = copy.deepcopy(base)
    s["world"]["objects"].append({
        "id": "dup_free", "type": "voxels", "layer": "free_space",
        "mat": "glass_free", "cells": {"list": [[3, 3, 0]]}})
    expect_error("two objects own the same cell", s, "declared by both")

    s = copy.deepcopy(base)
    s["world"]["objects"].append({
        "id": "intruder", "type": "box", "layer": "obstacles", "mat": "obstacle",
        "center": [0.75, 4.6, 1.0], "size": [0.6, 0.6, 0.6]})
    expect_error("two solids interpenetrate", s, "solid overlap")

    s = copy.deepcopy(base)
    s["world"]["objects"].append({
        "id": "bad_free", "type": "voxels", "layer": "free_space",
        "mat": "glass_free", "cells": {"list": [[3, 9, 1]]}})
    expect_error("free voxel inside the wall", s, "intersects solid")

    s = copy.deepcopy(base)
    obj(s, "points_echo")["at"][0] = [3.0, 2.0, 1.6]
    expect_error("static point floating off the wall", s, "farther than")

    s = copy.deepcopy(base)
    obj(s, "points_dynamic")["at"][0] = [8.5, 8.5, 0.75]
    expect_error("dynamic point outside free space", s, "not inside any declared free voxel")

    s = copy.deepcopy(base)
    for v in s["views"]:
        v["camera"].pop("fit", None)
        v["camera"]["ortho_scale"] = 2.0
    expect_error("camera crops the scene", s, "outside the frame")

    print("editor")
    s = copy.deepcopy(base)
    s3d._apply_op(s, {"op": "set", "path": "world.objects.wall_a.height", "value": 2.0})
    assert obj(s, "wall_a")["height"] == 2.0
    z0 = obj(s, "points_dynamic")["at"][0][2]
    s3d._apply_op(s, {"op": "translate", "id": "points_dynamic", "by": [0, 0, 0.5]})
    moved = obj(s, "points_dynamic")["at"][0]
    assert moved[0] == 1.75 and moved[1] == 2.75 and abs(moved[2] - (z0 + 0.5)) < 1e-9
    s3d._apply_op(s, {"op": "duplicate_object", "id": "wall_a", "new_id": "wall_c",
                      "merge": {"origin": [0.5, 6.0, 0]}})
    assert any(o["id"] == "wall_c" for o in s["world"]["objects"])
    s3d._apply_op(s, {"op": "remove_object", "id": "wall_c"})
    assert not any(o["id"] == "wall_c" for o in s["world"]["objects"])
    print("  ok    edit ops: set / translate / duplicate / remove")

    print("camera fit")
    s = copy.deepcopy(base)
    core.apply_camera_fit(s)
    res = core.check_geometry(s)
    assert res["ok"], res["errors"]
    print("  ok    fit_geometry frames every view without clipping")

    print("\nall tests passed")


if __name__ == "__main__":
    main()
