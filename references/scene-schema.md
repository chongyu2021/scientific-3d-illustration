# Scene schema

All units are metres; Z is up. A scene is one `world` + N `views` + one `figure`.

```json
{
  "version": 1,
  "meta": {"title": "...", "slug": "...", "unit": "m"},
  "style": {"preset": "iso-glass", "overrides": {}},
  "world": {"voxel_size": 0.5, "origin": [0,0,0], "objects": []},
  "views": [],
  "figure": {}
}
```

`style.preset` resolves to `presets/<name>.json` inside the skill. `overrides` is
deep-merged, so you can change one material without copying the preset:

```json
"style": {"preset": "iso-glass", "overrides": {"render": {"samples": 256}}}
```

## world

| field | meaning |
|---|---|
| `voxel_size` | 边长，所有栅格对象共用的唯一尺度 |
| `origin` | cell (0,0,0) 的世界坐标左下角 |
| `objects[]` | 见下 |

Every object needs a unique `id`, a `type`, and a `layer`. `layer` is what views
switch on/off; `mat` names a material in the style preset.

### voxels — free / occupied volume

```json
{
  "id": "free_space", "type": "voxels", "layer": "free_space", "mat": "glass_free",
  "gap": 0.035,
  "cells": {"grids": [
    {"i": [0,11], "j": [0,8], "k": [0,0],
     "exclude": [{"i": [0,1], "j": [0,1]}],
     "thickness": 0.28}
  ]}
}
```

- `i/j/k` accept `int` or `[min, max]` (inclusive).
- `exclude` removes rectangles (e.g. the robot footprint).
- `thickness` (0–1) draws thin tiles anchored at the cell floor while keeping the
  full-cell lattice. Use it for a "free-space sheet"; leave it out for real cubes.
- `cells` may also be `{"list": [[i,j,k], ...]}` or `[[i,j,k], ...]`.
- `gap` shrinks each cell slightly so edges read as separate voxels.

### blocks — axis-aligned lattice boxes

```json
{"id":"pillars","type":"blocks","layer":"obstacles","mat":"obstacle",
 "extent":[1,1,3],"cells":[[2,5,0],[6,2,0]],"gap":0.02}
```

`extent` is the box size in cells.

### brick_wall — staggered masonry

```json
{"id":"wall_a","type":"brick_wall","layer":"obstacles","mat":"obstacle",
 "origin":[0.5,4.5,0],"axis":"x","length":4.0,"height":2.5,"thickness":0.5,
 "brick":[1.0,0.5],"profile":[2.0,2.5,2.5,1.5,1.0],"gap":0.024}
```

- `axis: "x"` 墙沿 X 展开、厚度沿 Y；`axis: "y"` 相反。
- `brick: [长, 高]`，行间自动错缝半个砖长（仍在同一栅格上）。
- `profile` 按长度分段给高度，做出阶梯天际线。

### points — echoes, map points, any marker cloud

```json
{"id":"points_map","type":"points","layer":"points_map","mat":"point_static",
 "radius":0.045,
 "surface_sample": {"of":"wall_a","faces":["-y"],"count":46,"seed":17,
                    "margin":0.10,"offset":0.006},
 "constraints": {"on_surface_of":["wall_a"],"tolerance":0.04}}
```

Generators (combine freely):

| field | effect |
|---|---|
| `at: [[x,y,z],...]` | explicit positions |
| `surface_sample` | area-weighted random points on another object's faces; `faces` ∈ `-x/+x/-y/+y/-z/+z`; `margin` insets from the face border; `offset` pushes along the normal |
| `voxel_centers` | cell spec, puts one point at each cell centre |
| `random_in_box` | `{"min":[..],"max":[..],"count":n,"seed":s}` |

`radius` is per point; `radii: [...]` overrides per index.

`constraints` drives the checker:

- `on_surface_of: [ids]` + `tolerance` — every point must be within tolerance of
  one of those objects' surfaces.
- `in_free_space: true` + `surface_margin` — every point must fall inside a
  declared free voxel cell **and** stay away from solid surfaces.

### rays — laser beams

```json
{"id":"rays_history","type":"rays","layer":"rays_history","mat":"ray_history",
 "from":[0.5,0.5,0.62],"radius":0.019,"arrow":true,"arrow_size":0.06,
 "targets":[[1.45,4.48,0.55],[2.2,4.48,1.15]]}
```

`segments: [{"from":[..],"to":[..]}]` or `{"direction":[..],"length":..}` for free
segments. Beams are tapered cones; `arrow` adds a head at the far end.

### robot

```json
{"id":"robot","type":"robot","layer":"robot",
 "position":[0.5,0.5,0],"heading":8,"scale":1.0,
 "body_mat":"robot_body","dark_mat":"robot_dark","accent_mat":"robot_accent"}
```

Composite of chassis, four wheels, lidar drum and top disc. The robot sits in a
cleared hole in the free-space grid — carve the hole with `exclude`, otherwise
the checker will (correctly) report free/occupied overlap.

### frustum — camera view cone

```json
{"id":"cam_cone","type":"frustum","layer":"decor","mat":"frustum",
 "apex":[0,0,1.2],"target":[2,3,0.4],"h_fov":55,"v_fov":40,"depth":3.0,
 "faces":true,"edge_radius":0.006}
```

### path — trajectory / polyline

```json
{"id":"traj","type":"path","layer":"decor","mat":"path",
 "points":[[0,0,0.3],[1,0.4,0.5],[2,1.1,0.4]],"radius":0.02,"arrow":true}
```

### gaussian — soft density blob

```json
{"id":"uncertainty","type":"gaussian","layer":"decor","mat":"gauss",
 "center":[2,2,1],"radii":[0.6,0.4,0.3],"shells":3}
```

### box — one arbitrary box

```json
{"id":"panel","type":"box","layer":"decor","mat":"obstacle_light",
 "center":[1,1,1],"size":[2,0.2,1],"rot":[0,0,15],"role":"solid"}
```

`role` ∈ `solid | free | marker | decor` and decides which checks apply.
`align: "free"` opts an object out of lattice alignment checks.

## views

```json
{
  "id": "stage2",
  "camera": {"type":"ortho","azimuth":-58,"elevation":34,
             "target":[3.1,2.4,1.1],"ortho_scale":8.0,
             "fit":true,"fit_margin":0.02},
  "layers": ["free_space","obstacles","robot","points_static"],
  "hide": ["points_map"],
  "dim": {"glass_free": 0.2},
  "shadow": true,
  "ignore_clip": ["wall_b"],
  "pixel_scale": 1.75,
  "resolution": [1897,1631]
}
```

| field | meaning |
|---|---|
| `camera.type` | `ortho` (default) or `perspective` (`fov`) |
| `azimuth` / `elevation` | 相机方位（度）；`eye`+`target` 也可显式给 |
| `ortho_scale` | 画面水平宽度（世界单位）；`fit:true` 时自动覆盖 |
| `fit` | `true` 或对象 id 列表；按可见对象自动取景 |
| `fit_margin` | 取景留白比例，负值允许轻微出血 |
| `layers` | 只显示这些图层；缺省=全部 |
| `dim` | 本视图把某材质的 alpha/发光乘一个系数（做"幽灵"参考层） |
| `shadow` | 是否渲染地面接触阴影；放大图建议 `false` |
| `ignore_clip` | 允许被画框裁掉的对象 id（长墙出血、放大图里的参照物） |
| `resolution` | 显式像素；缺省从 figure 面板尺寸 × `pixel_scale` 推导 |

## figure

```json
{
  "size": [3600, 1520], "background": "#FFFFFF",
  "title": {"text":"...","size":96,"y":42,"color":"#0B2B4D"},
  "panels": [], "arrows": "auto", "zoom_links": [],
  "callouts": [], "chips": [], "extra_arrows": []
}
```

Panel:

```json
{"id":"p1","view":"stage1","rect":[44,210,1164,1460],
 "header":{"text":"（1）历史自由空间建模","height":80,"size":50},
 "legend":{"row_height":62,"size":37,"icon_size":44,
           "items":[{"icon":"ray_light","text":"历史激光射线"}]},
 "padding":18,"radius":20}
```

`rect` is `[x0,y0,x1,y1]` in figure pixels. The render slot is derived:
`pad + header` at the top, `legend` at the bottom. Override with `render_rect`
or `legend_rect` if you need exact control.

`overlay: true` marks a panel as a floating inset (zoom, detail view): it is
drawn last, has no legend, and is excluded from automatic arrows.

Icon names: `voxel`, `voxel_dim`, `block`, `point_static`, `point_dynamic`,
`point_warn`, `ray`, `ray_light`, `path`, `robot`, `dot`.
