---
name: scientific-3d-illustration
description: 用 Blender 无界面渲染可复现的科研三维示意图（等轴测、半透明体素、点云、射线、机器人、局部放大、中文图注排版）。当用户要画论文/答辩/项目汇报里的 3D 技术示意图、要按参考图复刻一张信息图、要修改已有 scene.json 的几何/材质/相机、或抱怨三维示意图“穿模、错位、文字变形、不够精致”时使用。也适用于把一段方法描述（自由空间、占据栅格、激光回波、SLAM 建图）转成可编辑的 3D 配图。不适用于折线图/统计图、CAD 工程图、纯二维流程图。
---

# 科研三维示意图

用**一个 JSON 世界 + 多个相机视图 + 一个二维排版层**生成论文级 3D 示意图。
Blender 只负责几何与光照，中文文字、箭头、图例、放大框全部由 Pillow 按最终像素尺寸排版，
不会被三维投影拉花、缺字或变形。

## 产物

`build` 之后在 `out/` 下得到：

| 文件 | 用途 |
|---|---|
| `figure.png` | 完整信息图（可直接进论文/PPT） |
| `figure_rgba.png` | 页面背景透明的版本 |
| `panels/panel_<view>.png` | 每个视图的透明底 3D 渲染（可单独复用） |
| `scene.json` | 已解析相机、可直接再次编辑的场景 |
| `scene.blend` | 可在 Blender 里打开继续精修的工程文件 |

下面的命令都相对 skill 根目录；在其他目录调用时换成 `scripts/s3d.py` 的绝对路径，
或用 `--scene` 指向目标 JSON。

## 心智模型（先理解，再动手）

```
scene.json
├── world    几何只写一次：体素、砖墙、机器人、射线、点云……
├── views    相机 + 图层可见性；同一个 world 可以被多个相机拍
└── figure   面板/标题/箭头/图例/标注/放大框，纯二维
```

**几何只在 `world` 里存在一次**。三栏对比图里每个面板是同一个世界的不同相机与图层，
因此坐标系天然一致——不要为每个面板各写一套几何。

## 标准流程

```bash
python scripts/s3d.py doctor                      # 检查 Blender / Pillow / 中文字体
python scripts/s3d.py inspect scene.json          # 看对象、包围盒、相机取景
python scripts/s3d.py check   scene.json          # 几何体检（必须 0 error 再渲染）
python scripts/s3d.py build   scene.json          # check + 渲染 + 排版
python scripts/s3d.py figure  scene.json          # 只重排 2D（改文字/标注时用，秒级）
```

预览迭代时加 `--preview`（半分辨率、32 采样），定稿后去掉。
只改了一栏时用 `render --views stage2` 单独重渲那一栏，再 `figure` 重排。

**必须真的渲染并打开图片检查。** 只写 JSON 不看成图，等于没做完。
至少检查：体素是否错位、物体是否互相穿插、点是否贴在障碍物表面、
文字是否压住主体、局部放大框是否和被放大区域对应。

## 修改既有场景，而不是重写

```bash
# 一次改一个属性
python $S edit scene.json --set 'world.objects.wall_a.height=2.0' --in-place

# 批量补丁（可复用、可 diff）
python $S edit scene.json --patch patches/taller_wall.json --out scene.v2.json
```

补丁操作：`set` / `merge` / `add_object` / `remove_object` / `duplicate_object`
/ `translate` / `set_material`。路径里的列表段可以直接写对象 `id`：
`world.objects.points_dynamic.at`。

新增对象时照着已有对象的字段抄，改 `id` 和几何参数即可；
不要新建一套渲染脚本，也不要往 `blender_worker.py` 里加几何。

## 相机

正交等轴测是默认风格：`azimuth`（绕 Z 转角）、`elevation`（俯角）、
`ortho_scale`（画面宽度，单位=世界单位）。

```json
"camera": {"type": "ortho", "azimuth": -58, "elevation": 34,
           "target": [3.1, 2.4, 1.1], "fit": true, "fit_margin": 0.02}
```

`fit: true` 会自动把当前视图里所有可见对象装进画面并居中——**改完几何优先用它**，
比手调 `ortho_scale` 稳。要裁掉边缘（让主体更满）就写负的 `fit_margin`，
但先确认机器人、标注锚点没有被裁掉。

## 两条硬约束

1. **文字只在二维层。** 场景里的中文一律写在 `figure.callouts` / `legend` /
   `chips` / `title`，不要用 Blender 的 Text 物体——否则必然变形、缺字、无法换字体。
2. **几何必须可校验。** 体素用整数 cell 坐标；点云用显式坐标或
   `surface_sample`（在目标表面采样）；动态点用 `constraints.in_free_space`。
   `check` 会验证栅格对齐、实体互穿、自由/占据互斥、点-面一致性和相机裁切。

## 参考图怎么用

用户给参考图时，只提取**构图、配色、材质、相机角度、信息层级**。
参考图里的几何常常是错的（体素重叠、射线穿墙、点云飘在空中），
不要照抄这些错误——用 `check` 保证物理自洽，再让风格去接近参考图。

## 细节文档（按需读）

- `references/scene-schema.md` — 全部对象类型与字段（体素、砖墙、点云、射线、
  机器人、视锥、路径、高斯椭球）、相机、view、figure 的完整 schema。
- `references/style-and-layout.md` — 材质与光照参数、预设风格、图例/标注/
  放大框/流程块的排版规则与坐标约定。
- `references/workflow-and-checks.md` — 编辑配方、check 报错逐条含义与修法、
  性能与分辨率、常见翻车现场。

## 完整范例

`examples/dynamic-pointcloud-filter/scene.json` 是三栏「历史自由空间建模 →
几何一致性检测 → 动态点云过滤」信息图，包含共享世界、局部放大、中文图注、
流程块和三个相机。改这张图比重写一遍快得多。
