# Style, layout and annotation

## Where the look comes from

Three independent layers, tune them separately:

1. **Materials / lighting** in `presets/*.json` — the physical look.
2. **Camera** per view — the composition of each 3D image.
3. **Figure** — panel geometry, typography, colour of headers and callouts.

Changing the picture to look "more like Nature style" is almost always a change
in 2 and 3, not in the mesh.

## Preset anatomy

```
palette   页面/墨色/主色/面板渐变/示警红
camera    默认 az / el / target / ortho_scale
lighting  world 环境光 + key / fill / rim 三盏面光
edge      轮廓线总开关、宽度、颜色、发光强度
render    cycles 采样、去噪、色彩变换、透明胶片、景深无关
materials 每个材质一条：kind / base_color / roughness / transmission / alpha / edge_color …
```

Material kinds:

- `principled` — 不透明实体（灰色障碍物、机器人、点）。
- `glass` — 透射 + alpha 的玻璃/亚克力（自由空间体素、视锥）。
- `beam` — 激光射线，自发光为主，保持细而亮。
- `flat` — 纯自发光（轮廓线）。

Useful knobs:

| 想要的效果 | 改什么 |
|---|---|
| 体素更"蓝"更实 | `glass_free.base_color` 加深、`alpha` 0.6→0.8、`transmission` ↓ |
| 体素更通透、能看到内部 | `alpha` ↓、`roughness` ↓、`transmission` ↑ |
| 边缘线更精致 | `edge_width` 0.006–0.009，`edge_color` 同色系加深 |
| 整体更干净（像 AI 插画） | 关 `shadow_catcher`，`world.strength` ↑，`exposure` ≈0 |
| 更有体积感 | 开 `shadow_catcher`，加大 `key.size`（更柔的阴影） |
| 更"塑料" | `roughness` ↓、`specular` ↑；反之把它们调高 |

Rules of thumb for a paper figure: 体素边缘线宽约等于面板宽度的 1/500；
障碍物用中性灰、明度 55–70%；红色只留给"错误/动态"，不要用于装饰。

## Figure layout

Coordinates are absolute pixels at `figure.size`. Design once at 3600 px wide and
scale — the composer multiplies all typographic sizes by `W/3600`.

Recommended structure for a 3-panel story:

```
margin 44 | panel 1120 wide | arrow gap 76 | panel | arrow | panel | margin 44
title band 150–190 px
panel: header 80 + render + legend 200 (3 rows × 62)
```

Concrete starting points (all in figure pixels, design width 3600):

| 版式 | figure | panel rect | render 槽 |
|---|---|---|---|
| 三栏 | 3600×1520 | x 44/1240/2436, 宽 1120, y 210–1460 | ≈1084×932 (1.16:1) |
| 两栏 | 3200×1500 | x 40/1660, 宽 1500, y 200–1450 | ≈1464×1060 (1.38:1) |
| 单栏 | 1800×1500 | x 40–1760, y 200–1450 | ≈1680×1050 |

主体（含墙、体素场）应占渲染槽宽度的 85–95%；`fit_margin` 0.02 是合适的默认值，
模型四周留白超过 15% 就说明相机太远，或图层里有多余对象。

The render resolution is derived from the panel's render slot, so the 3D image
always matches its slot exactly — never hand-set `resolution` unless you need a
specific pixel size.

## Annotations

### callouts

```json
{"panel":"p2zoom","text":"历史自由空间",
 "target":[2.25,2.75,1.02],        // world point
 "at":[0.03,0.05],                  // relative position inside the render slot
 "size":27, "align":"left",         // left | center | right
 "leader":true, "kink":[0.3,0.2]}   // optional elbow, relative coords
```

`target` is projected through the same camera math Blender uses, so a label stays
attached to a voxel even after you change the camera or move the object.
Place labels in empty corners; if a label must overlap geometry, it gets an
opaque white box automatically.

### zoom

Two coupled pieces:

```json
"panels": [ …, {"id":"p2zoom","view":"stage2_zoom","overlay":true,"rect":[...]} ],
"zoom_links": [{"from_panel":"p2","to_panel":"p2zoom",
                "source_box":[[1.15,2.15,0.35],[2.55,3.45,1.25]]}]
```

The dashed rectangle is the projection of `source_box` onto the main panel — it
cannot drift out of sync. The inset is a real render with its own camera, so it
is geometrically identical to the main view, just closer. Give the inset view
`"shadow": false` and a restricted `layers` list so it stays clean.

### chips (2D process boxes)

```json
{"rect":[2500,1082,2780,1152],"text":"位姿估计","size":33}
```

Use them for the "→ 建图 → 更新" tail of a pipeline. Connect with small arrows in
`extra_arrows` (`from`/`to` + `width`/`head`/`notch`).

## Typography

- 字体自动发现：Windows 用微软雅黑，Linux 用 Noto Sans CJK，可用
  `S3D_FONT` / `S3D_FONT_BOLD` 覆盖。
- 标题 90–100 px、面板标题 48–52 px、图例 36–40 px、标注 26–34 px
  （都按 3600 px 宽的设计稿）。
- 中文标题不要用字距；英文小标题可加 1–2 px letter-spacing。

## Density checklist

一张"精致"的科研图通常满足：

- 每个面板的主体占宽度 80–95%，四周没有大片死白；
- 图例 2–4 行，图标与文字基线对齐；
- 至少一处局部放大或引出标注来解释关键机制；
- 三个阶段之间用同一套相机/配色，读者一眼能看出"同一个场景在变化"；
- 全文只有一种蓝、一种灰、一种红。
