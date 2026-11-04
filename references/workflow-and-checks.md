# Workflow, editing and checks

## The loop that actually converges

```
inspect  →  edit  →  check  →  build --preview  →  look at the image  →  repeat
                                        ↓
                                  build (full quality) → figure.png
```

`--preview` halves the resolution and uses 32 samples: ~5 s per view instead of
~30 s. Use it for every composition change; only run the full build when the
layout is settled. Geometry and camera are identical in both.

Always open the produced PNG. Typical things that look fine in your head and
wrong on screen: a label sitting on top of the red point, a zoom inset whose
rectangle does not enclose the feature, rays ending in mid-air, a legend row
whose icon is larger than the text.

## Editing recipes

### move / resize a wall

```bash
python scripts/s3d.py edit scene.json --in-place \
  --set 'world.objects.wall_a.height=2.0' \
  --set 'world.objects.wall_a.profile=[2.0,2.0,1.5,1.5,1.0]'
```

### add an object (copy the shape of an existing one)

Create `patch.json`:

```json
{"ops": [
  {"op": "duplicate_object", "id": "wall_a", "new_id": "wall_c",
   "merge": {"origin": [0.5, 6.0, 0], "length": 2.5, "height": 1.5}},
  {"op": "set", "path": "views.stage1.layers",
   "value": ["free_space","obstacles","robot","rays_history"]}
]}
```

### change the camera angle everywhere

```bash
python scripts/s3d.py edit scene.json --in-place \
  --set 'views.stage1.camera.azimuth=-50' \
  --set 'views.stage1.camera.elevation=38'
```

With `fit: true` you do not touch `ortho_scale`.

### dim a layer in one view (ghost reference)

```json
{"id":"stage3", "layers":["obstacles","points_map","free_space_core"],
 "dim": {"glass_free": 0.25}}
```

### re-compose text only (no re-render)

```bash
python scripts/s3d.py figure scene.json
```

## What `check` verifies

| error | meaning | fix |
|---|---|---|
| `voxel misalignment …` | a cell face is off the `voxel_size` lattice | use integer `i/j/k`, or set `"align": "free"` on decorative boxes |
| `voxel cell … declared by both` | two `voxels` objects own the same cell | split the cell ranges / add `exclude` |
| `solid overlap` | two solid primitives interpenetrate | move one, shrink it, or declare `allow_intersections` |
| `free voxel … intersects solid …` | occupied and free space are not exclusive | carve the footprint with `exclude`, move the object |
| `points of … farther than … from the surface` | a "surface" point cloud is floating | use `surface_sample`, or move the `at` positions back to the face |
| `point … not inside any declared free voxel` | a free-space point is outside the free volume | add the cell to `free_space`, or fix the coordinate |
| `objects entirely outside the frame` | the camera does not see an object at all | use `fit:true`, or check `layers` |
| `objects clipped by the frame` | object is cut by the border | intended for walls that run off-panel → add to `ignore_clip`; otherwise `fit_margin` ↑ |

Exit code is 2 on errors, so the commands can gate a build:

```bash
python scripts/s3d.py check scene.json && python scripts/s3d.py build scene.json
```

`build` refuses to render when checks fail; `--force` overrides (use it only when
the "error" is an intentional diagram convention, and say so in the final answer).

## Performance

Reference numbers on a laptop GPU (RTX 5070, OptiX, Cycles):

| scene | resolution | samples | time |
|---|---|---|---|
| 120 voxels + 2 brick walls + 8 rays | 1897×1631 | 32 (preview) | ~6 s |
| same | 1897×1631 | 160 | ~25 s |
| 4 views full build | — | 160 | ~110 s |

CPU-only is 3–8× slower. Reduce `render.samples` to 64 with denoising for drafts;
below 48 the glass gets blotchy.

`--preview` does not change the camera or geometry, so it is safe for judging
composition, just not for judging noise.

## Common failure modes

**"渲染是黑的 / 只有背景."** Camera is inside geometry, or `layers` hides
everything. Check `inspect` for the view's layer list.

**"文字是方块."** The font has no CJK glyphs. Run `doctor`; set
`S3D_FONT=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`.

**"Blender not found."** `python scripts/s3d.py install-blender` downloads a
portable build into `~/.local/share/s3d/` and remembers it in `~/.s3d/config.json`.
`BLENDER_BIN` also works and always wins.

**"渲染太慢."** Check that it uses the GPU: the log prints
`[s3d] using GPU backend OPTIX`. If it says CPU, the driver/CUDA toolkit is
missing; drop `render.samples` to 64 and continue.

**"体素看起来像一块实心玻璃板."** Add `gap` (0.03–0.05) and/or set
`thickness` on the grid so cells read as individual tiles.

**"点云穿过玻璃看不见."** Points inside glass are tinted by it: raise the point
material's `emission_strength`, or increase `radius`; in an inset view restrict
`layers` to the points and the free volume only.

**"面板之间几何对不上."** They are not supposed to be separate scenes. One
`world`, several `views`. If a panel needs a different object set, use `layers`,
not a copied world.

## Delivering

Report the absolute path of `figure.png`, note which files are editable
(`scene.json`, `scene.blend`), and state the check result. If a warning was
knowingly accepted (e.g. a wall deliberately bleeding off the panel edge), say
which and why.
