# CAD IR v0.1（DeepCAD 兼容）

状态：草案<br>
适用范围：DeepCAD `cad_json` → IR → FTC / build123d / CadQuery<br>
参考样例：`examples/deepcad_00000007.ir.json`

---

## 1. 目标

本 IR 以 DeepCAD 建模序列为骨架，是归一化的建模语义表示，不是原始 JSON 字段的无损超集。原始标志和 ID 通过 `source` / `extensions.source` 追溯。

v0.1 只覆盖：

- 草图平面；
- 直线、圆弧、圆；
- 闭合 loop；
- profile；
- 拉伸；
- 拉伸自带的布尔操作（new / join / cut / intersect）。

## 2. 非目标

v0.1 不覆盖：

- fillet / chamfer / shell / draft / offset；
- revolve / loft / sweep；
- 样条、椭圆、NURBS；
- 草图约束；
- 拓扑选择与几何指纹；
- 装配实例、装配约束、装配树；
- 材质、PMI、公差、渲染信息。

这些能力通过后续版本增量增加，不改变 v0.1 已有字段的含义。

## 3. 设计原则

1. **以 DeepCAD 为骨架**：保留 `entities + sequence` 的组织方式。
2. **线性历史优先**：`inputs` / `params.plane` 表达几何依赖；v0.1 的 `sequence` 还决定非 `new` 特征作用于此前累积的 body 集合。
3. **单位与坐标显式**：长度单位、局部/世界坐标、容差必须写清楚。
4. **草图用二维局部坐标**：曲线点存 `[u, v]`，由 `sketch.plane.frame` 变换到世界坐标。
5. **目标无关**：IR 不出现 `BODIES`、`ql`、`@scad.part`、`make_*_rsolid` 等目标库概念。
6. **不支持项显式**：无法映射的内容进入 `unsupported`，不允许静默丢弃。
7. **原始数据只读**：DeepCAD 文件不修改；兼容性由 adapter 负责。

---

## 4. 顶层结构

```json
{
  "ir_version": "0.1",
  "kind": "part",
  "units": "mm",
  "tolerance": {
    "length": 1e-6,
    "angle_deg": 1e-6
  },
  "entities": {},
  "sequence": [],
  "results": [],
  "extensions": {},
  "unsupported": [],
  "evidence": {}
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `ir_version` | 是 | 固定 `"0.1"` |
| `kind` | 是 | v0.1 固定 `"part"` |
| `units` | 是 | `mm` / `m` / `inch` / `normalized`；当前 FTC emitter 仅接收已归一化为 `mm` 的 IR |
| `tolerance` | 否 | 缺省 `length=1e-6`（以 `units` 为单位）、`angle_deg=1e-6`；DeepCAD adapter 显式写入 `length=1e-3 mm` |
| `entities` | 是 | 实体字典，key 为 IR id |
| `sequence` | 是 | 顶层操作顺序，元素为 IR id |
| `results` | 是 | v0.1 恰好一个 ID，指向 `sequence` 中最后一个拉伸步骤；表示该步执行后的 body 集合状态，不限制实体数量 |
| `extensions` | 否 | 源格式、追溯信息、目标提示 |
| `unsupported` | 是 | 无法映射项，元素为对象 |
| `evidence` | 否 | 体积、面积、包围盒等证据，不参与语义 |

`sequence` 必须包含所有 `feature.extrude`，可以包含 sketch plane；不要求包含曲线/loop/profile 节点。非 `new` 特征按顺序作用于当前 body 集合；第一个拉伸必须为 `new`。v0.1 不表示按目标 body 选择的分支历史。

---

## 5. ID 与引用

- ID 必须匹配 `^[A-Za-z][A-Za-z0-9_.:-]*$`。
- ID 在单个文档内唯一。
- 文档来源放入顶层 `extensions.source`；节点来源放入实体 `source`。源 ID 不作为 IR 几何执行所必需的语义。
- 推荐命名：

| 实体 | ID 形式 | 示例 |
| --- | --- | --- |
| 草图平面 | `sketch_<sketch_id>` | `sketch_FQgWGf8WhgalpUy` |
| 曲线 | `curve_<sketch_id>_<curve_id>` | `curve_FQgWGf8WhgalpUy_JGB` |
| Loop | `loop_<sketch_id>_<profile_id>_<index>` | `loop_FQgWGf8WhgalpUy_JGC_0` |
| Profile | `profile_<sketch_id>_<profile_id>` | `profile_FQgWGf8WhgalpUy_JGC` |
| 拉伸 | `extrude_<entity_id>` | `extrude_FI4bCL9y0XvsF52` |

---

### 5.1 来源与源特征分组

顶层 `extensions.source` 记录文档来源（如路径、单位）；实体 `source` 记录节点来源。通用 IR 允许省略实体 `source`，几何执行不得依赖来源字段；adapter 应尽量保留它们。已声明字段须满足以下约定，其他来源扩展字段允许保留：

| 字段 | 类型与含义 |
| --- | --- |
| `format` | 非空字符串，如 `deepcad-1` |
| `entity_id` | 非空字符串，标识当前源文档内的源实体；必须结合 format 和文档作用域解释 |
| `profile_id` / `curve_id` | 非空字符串，标识所属源草图内的 profile / 曲线 |
| `profile_index` | 非负整数，表示源拉伸引用 profile 列表的零基位置；不等于 region 序号 |
| `loop_index` | 非负整数，表示源 profile 的零基 loop 位置 |
| `ordered` / `reversed` | 可选布尔值，记录 adapter 的曲线排序与反向处理 |

对同一 IR 文档，按 `sequence` 中拉伸步骤的顺序，只能将相邻且具有相同 `source.format + source.entity_id` 的步骤视为一个源特征组。跳过非拉伸节点不改变此顺序。组内声明的 `profile_index` 必须递增，允许因源 profile 被拒绝而存在间隔。不完整或缺失来源的步骤各自成组；相同源 ID 非连续出现时分别成组，不移动或收拢步骤。不得解析 IR ID 字符串来猜测来源关系。

源特征组仅用于追溯和代码组织：不得替换 `sequence`，不得合并工具体后改为一次布尔，也不得改变每步的 `new/join/cut/intersect`、作用范围、region 工具集合和结果状态。

### 5.2 等价表达与尺寸追溯

- emitter 可以把同一 profile 的草图构造放进函数、把纯直线 loop 写成有序顶点列表；须保留曲线顺序、坐标精度、端点连接及显式 `regions` 的外环/孔归属。不能通过函数重组重新猜测轮廓拓扑。
- 单个几何字段可以提取为命名参数。跨节点共享参数必须有依据：同一 IR 字段被复用、同一源曲线的同一种尺寸字段，或同一连续源拉伸组展开出的同一种距离/角度字段；当前值还必须一致。数值相等本身不是共享依据；正向/反向距离属于不同字段。
- `start/mid/end` 仍决定弧的几何。用可选 `center/radius` 参数化弧前，必须检查其与三点一致，并避免由多个弧独立控制同一端点。三点有效且辅助字段一致、但无法证明半径编辑安全时，保留固定三点表达；辅助字段冲突超过声明容差则拒绝，不作为参数化回退。
- 默认参数应重建原几何；参数编辑产生新几何不代表推断出了原设计约束。生成参数及其全部 IR 字段路径、来源关联记录在伴随元数据中，不改变原 IR 数值。
- 变量名、函数层次、SCAPI 辅助函数和 `symbols.json` 属于 emitter 契约，详见 [READABLE_CODE_SPEC.md](READABLE_CODE_SPEC.md)，不进入目标无关 IR 的几何语义。

---

## 6. 单位、坐标与容差

- `units` 必填；推荐 IR 使用 **mm**，当前 FTC emitter 只接收 `mm`。
- 长度字段和 `tolerance.length` 全部使用 `units` 指定的单位；`normalized` 的绝对尺度未定义，不能直接送入 FTC emitter。
- DeepCAD `cad_json` 内部长度单位是 **m**，adapter 必须乘以 1000 转成 mm，并在 `extensions.source.units` 记录 `"m"`。
- 草图曲线坐标是**二维局部坐标** `[u, v]`：

```text
world = frame.origin + u * frame.x_axis + v * frame.y_axis
```

- `frame.z_axis` 是草图法向。
- `feature.extrude.direction` 是**世界坐标**下的单位向量。
- 角度字段使用**度**；adapter 从 DeepCAD 的弧度值转换。
- 数值必须是有限值，不允许 NaN / Inf。

---

### 6.1 几何准备契约（ir-geometry-v1）

所有生产生成入口，包括直接创建 readable emitter，执行相同流程：结构/引用检查 → 原始曲线一致性检查 → 有界端点规范化 → 派生圆弧计算 → 闭合、自交及显式 region 校验。输入对象不修改；不依赖 source、DeepCAD ID 或目标 CAD API。

- 端点身份是曲线字段身份；仅同一 loop 的有序相邻边建立连接。共享曲线的同一端点字段在全部引用中保持一致，不按空间距离跨 loop 合并。
- 规范化按实体 ID、字段名的字典序选择已有端点为代表，每个原始端点到代表的距离必须不大于 length 容差。传递合并超限或共享引用产生冲突时拒绝；不能连续小步吸附而累计超限。
- 规范化后零长/短于容差的直线、半径不大于容差的圆弧/圆、面积不大于容差平方的 loop、自交及重叠均拒绝。相邻边允许共同端点，但不允许重叠或其他交点。
- 线、圆、弧的边界相交、距离和包含判断使用共享解析几何；不以固定离散采样决定拓扑。不同 loop 边界距离不大于容差时作为接触拒绝，无法可靠分类也拒绝。
- 原始 IR 保留，另存准备后的 IR。元数据记录准备版本、原字段路径、原值、准备后值、端点位移和代表字段；派生圆弧的更新也记录。再次准备所得几何相同，且不产生新调整。
- 规范化不改变平面 frame、拉伸方向、距离或布尔顺序；不做缩放、姿态对齐、healing、阵列或约束推断。

## 7. 实体类型

所有实体共享：

```json
{
  "type": "实体类型",
  "op": "规范操作名",
  "inputs": ["依赖实体 id"],
  "params": {},
  "outputs": 1,
  "selection": [],
  "source": {},
  "tags": []
}
```

`outputs`、`selection`、`source`、`tags` 可选。`outputs` 表示 IR 节点的逻辑输出槽数，默认 1；**不是**生成的 B-rep solid 数量。一个拉伸节点可从多个 profile region 生成多个实体，并仍只输出一个更新后的 body 集合状态。

### 7.1 `sketch.plane`

```json
{
  "type": "SketchPlane",
  "op": "sketch.plane",
  "inputs": [],
  "params": {
    "frame": {
      "origin": [0, 0, 0],
      "x_axis": [1, 0, 0],
      "y_axis": [0, 1, 0],
      "z_axis": [0, 0, 1]
    }
  }
}
```

规则：

- `origin / x_axis / y_axis / z_axis` 都是三维向量；
- 三个轴必须是单位向量且两两正交；
- `x_axis × y_axis = z_axis`（右手系）。

### 7.2 `sketch.line`

```json
{
  "type": "SketchLine",
  "op": "sketch.line",
  "inputs": [],
  "params": {
    "start": [0, 0],
    "end": [10, 0]
  }
}
```

### 7.3 `sketch.arc`

```json
{
  "type": "SketchArc",
  "op": "sketch.arc",
  "inputs": [],
  "params": {
    "start": [10, 0],
    "mid": [5, 5],
    "end": [0, 0],
    "center": [5, 0],
    "radius": 5,
    "ccw": true
  }
}
```

规则：

- `start / mid / end` 必填，决定圆弧走向；
- `center / radius / ccw` 可选；若存在，必须与三点计算的圆心、半径及走向一致。圆心距离和半径误差超过 `tolerance.length`，或走向相反，均拒绝；不能由辅助字段覆盖三点。
- 三点共线、重复或数值上无法可靠确定圆弧时拒绝。先检查原始辅助字段，再规范化端点，最后重算派生圆心、半径及走向；
- 完整圆必须使用 `sketch.circle`，不要用 360° 圆弧。

### 7.4 `sketch.circle`

```json
{
  "type": "SketchCircle",
  "op": "sketch.circle",
  "inputs": [],
  "params": {
    "center": [0, 0],
    "radius": 10
  }
}
```

### 7.5 `sketch.loop`

```json
{
  "type": "SketchLoop",
  "op": "sketch.loop",
  "inputs": [
    "curve_sketchA_L1",
    "curve_sketchA_A1",
    "curve_sketchA_L2"
  ],
  "params": {
    "is_outer": true,
    "closed": true
  }
}
```

规则：

- `inputs` 是有序边列表；
- 相邻边的终点必须与下一条起点一致，误差在 `tolerance.length` 内。进入 emitter 前按第 6.1 节在副本中统一顶点；超容差拒绝；
- 单条 `sketch.circle` 可以单独构成一个 loop；
- `params.is_outer` 是 **IR 归一化后的区域角色**：填充区域的外环（包括孔内的实体岛）为 `true`，孔为 `false`。它不一定等于 DeepCAD 同名字段；adapter 将原值保存在 `source.deepcad_is_outer`，并用 `source.role_provenance="geometry_nesting"` 标明推断来源；
- v0.1 只支持 `closed: true`。

### 7.6 `sketch.profile`

```json
{
  "type": "SketchProfile",
  "op": "sketch.profile",
  "inputs": ["loop_sketchA_outer", "loop_sketchA_hole"],
  "params": {
    "plane": "sketch_sketchA",
    "closed": true,
    "regions": [
      {"outer": "loop_sketchA_outer", "holes": ["loop_sketchA_hole"]}
    ]
  }
}
```

规则：

- `inputs` 是该 profile 的全部 loop 列表；`params.regions` 必填，按几何包含关系列出每个填充区域及其**直属**内孔；
- `params.plane` 必须引用一个 `sketch.plane`；
- 每个 `inputs` loop 在 `regions` 中**恰好出现一次**，作为 `outer` 或一个 `holes` 成员；至少有一个 region；
- 多个互不包含的外环构成多个 region；外环包含的直属 loop 是孔，孔内再次嵌套的 loop 是新 region（实体岛）。外环 `is_outer=true`，孔 `is_outer=false`；
- 各 region 必须与轮廓的真实包含层级一致；边界接触、相交、重叠或无法可靠分组时拒绝 IR，不得默默将孔填实或丢掉；
- 目标 emitter **使用 IR 中的 `regions`**，不重新从几何猜测外环与孔。FTC emitter 为每个 region 建立一个 face，对该特征所有 region 施加相同拉伸和布尔操作；
- v0.1 只支持 `closed: true`。

### 7.7 `feature.extrude`

```json
{
  "type": "ExtrudeFeature",
  "op": "feature.extrude",
  "inputs": ["profile_sketchA_P1"],
  "params": {
    "distance": 25.4,
    "direction": [0, 0, 1],
    "extent": "one_side",
    "operation": "new",
    "start": "profile_plane",
    "taper": 0.0
  }
}
```

规则：

- `inputs` 恰好一个 profile；
- 此处的一个 profile 可以含多个 region；一个特征步骤会从每个 region 产生刀具，`new` 将它们加入当前 body 集合，`join/cut/intersect` 将这些刀具作为同一个步骤的集合操作。结果可为多个 solid，但仍是一个逻辑 body 集合状态；
- `distance > 0`；
- `direction` 是世界坐标下的单位向量；
- `extent`：
  - `one_side`：沿 `direction` 拉伸 `distance`；
  - `symmetric`：两侧各拉伸 `distance`，总长 `2 * distance`；
  - `two_sides`：沿 `direction` 拉伸 `distance`，反方向拉伸 `distance2`；
- `two_sides` 时必须提供 `distance2 > 0`；
- `operation`：`new` / `join` / `cut` / `intersect`；
- `start`：v0.1 只支持 `"profile_plane"`；
- `taper`：度，缺省 0。

v0.1 FTC emitter 行为：

- `results` 指向最后一个拉伸步骤的 body 集合状态；单实体时 `build()` 返回 `Solid`，多实体时返回 `Assembly`，`BODIES` 始终是实体列表。
- `new` 添加新 body；`join/cut/intersect` 对当前 body 集合做集合布尔，保留所有结果实体。内核失败和空最终结果不视为成功。
- 只接受毫米单位且 `unsupported` 为空；其他来源应先在 adapter 中归一化或报告缺口。
- 多 profile、多 extrude、`symmetric` / `two_sides` 已具备基础生成逻辑，但仍需更多样本回归。

### 7.8 预留操作

以下 op 已预留名称，但 v0.1 不要求实现：

- `boolean.union` / `boolean.cut` / `boolean.intersect`
- `modify.fillet` / `modify.chamfer` / `modify.shell` / `modify.draft`
- `feature.revolve` / `feature.loft` / `feature.sweep`
- `transform.translate` / `transform.rotate` / `transform.matrix`
- `assembly.instance` / `assembly.constraint`

---

## 8. DeepCAD 映射

| DeepCAD | IR |
| --- | --- |
| `Sketch.transform` | `sketch.plane.params.frame` |
| `Sketch.profiles[pid].loops[i]` | 一个 `sketch.loop`；按包含关系生成 `sketch.profile.params.regions`，源 `is_outer` 放入 loop 的 `source.deepcad_is_outer` |
| `Line3D.start_point / end_point` | `sketch.line.params.start / end`（二维局部） |
| `Arc3D.center_point / radius / start_angle / end_angle / reference_vector` | `sketch.arc.params`（计算 start/mid/end，保留 center/radius/ccw） |
| `Circle3D.center_point / radius` | `sketch.circle.params.center / radius` |
| `ExtrudeFeature.profiles[i]` | 每个被引用的 profile 转为一个 `feature.extrude` 节点，节点的 `inputs[0]` 引用该 profile；一个源特征可展开为多个连续 IR 步骤 |
| `extent_one.distance` | `distance` |
| `extent_two.distance` | `distance2` |
| `extent_type = OneSideFeatureExtentType` | `extent = "one_side"` |
| `extent_type = SymmetricFeatureExtentType` | `extent = "symmetric"` |
| `extent_type = TwoSidesFeatureExtentType` | `extent = "two_sides"` |
| `operation = NewBodyFeatureOperation` | `operation = "new"` |
| `operation = JoinFeatureOperation` | `operation = "join"` |
| `operation = CutFeatureOperation` | `operation = "cut"` |
| `operation = IntersectFeatureOperation` | `operation = "intersect"` |
| `taper_angle` | `taper`（弧度 → 度） |
| `start_extent = ProfilePlaneStartDefinition` | `start = "profile_plane"` |
| `properties.bounding_box` | `evidence.bounding_box_mm`（米 → 毫米） |

补充约定：

- 多 profile `NewBody` 的第一步映射为 `new`，其余步骤映射为 `join`；其他布尔角色沿用源操作。最后一个有效拉伸节点进入 `results`，表示最终 body 集合状态；后续 `NewBody` 即使与已有 body 空间分离，也保留为独立 body。
- DeepCAD 的 `Sketch` 可以有多个 profile；只为被 `ExtrudeFeature` 引用的 profile 生成节点。
- 未被引用的 profile 不进入 `results`，可以丢弃或记录到 `extensions`。
- `Line3D` 的 `z` 必须接近 0；否则说明曲线不在草图平面，v0.1 标记 `unsupported`。
- `Arc3D` 的 `start_angle/end_angle/reference_vector` 用于计算二维 `start/mid/end`；如果角度方向不明确，标记 `unsupported`。
- 源实体 ID 写入各实体的 `source.entity_id`；源文档路径、单位等信息放入顶层 `extensions.source`，便于回溯。

---

## 9. 语义校验规则

JSON Schema 之外，还必须检查：

1. `entities` 的 key 唯一（JSON 对象天然唯一）。
2. 所有 `inputs`、`params.plane` 引用都存在。
3. 依赖图无环。
4. `sequence` 中的 ID 都存在，且顺序与依赖不冲突。
5. `results` 中的 ID 都存在。
6. `sketch.plane.frame` 三轴单位正交。
7. `sketch.loop` 的边首尾相接；单圆 loop 例外。
8. `sketch.profile` 的 `regions` 非空，包含且仅包含每个输入 loop 一次；外环/孔标志与真实几何包含层级一致，轮廓接触/重叠被拒绝。
9. `feature.extrude.inputs` 恰好一个 profile。
10. `distance > 0`；`two_sides` 必须提供 `distance2 > 0`。
11. `direction` 模长与 1 的误差小于 `1e-9`。
12. 所有数值有限。
13. `unsupported` 每项必须包含 `code` 和 `message`。
14. 已声明的实体来源字段符合第 5.1 节类型；连续源拉伸组中声明的 profile 序号递增。

---

## 10. 版本演进

- `ir_version` 使用 `MAJOR.MINOR`。
- 增加可选字段或新 op：提升 MINOR。
- 修改已有字段含义、删除字段、修改默认语义：提升 MAJOR。
- `deepcad-1` 是只读输入 profile，原始文件永不修改。
- v0.1 仍是未冻结草案；引入必填 `regions` 后，旧草案 IR 文件须从源 JSON 重新生成，不能直接交给新 emitter。冻结后若再改变字段含义，应遵循上述 MAJOR 规则并提供迁移。
- 新能力通过新实体类型或 `extensions` 增量加入，不改变 v0.1 语义。
- adapter 应保留源格式追溯信息；缺少来源的通用 IR 仍有效。
- 本次仅补齐未冻结 v0.1 的可选来源字段契约和等价重写边界，不增加几何 op、必填字段或新的参数表达式语义。

---

## 11. 不在 v0.1 范围

- 装配：实例、约束、求解结果；
- 约束：草图几何约束、尺寸约束；
- 高级特征：fillet / chamfer / shell / draft / offset / revolve / loft / sweep；
- 复杂曲线：椭圆、样条、NURBS；
- 选择语义：面/边指纹、拓扑引用；
- 多目标 emitter 的能力矩阵。

这些内容在后续版本中加入，不破坏 v0.1 文档的可读性。

---

## 12. 下一步

1. 用独立 DeepCAD STEP 验证多 region、多实体以及 `new/join/cut/intersect` 的最终与逐特征几何。
2. 解决源 bbox 失配和多实体 STEP 差集异常，不通过放宽验收阈值掩盖问题。
3. 实现 CadQuery / build123d 最小 emitter，直接消费相同的 `regions` 和线性 body 集合语义。
4. 后续版本再扩展显式 body 目标选择、参数关系和草图约束。


## Readable-only 适配规则补充（2026-10-23）

IR v0.1 仍为草案，几何字段保持规范形式；生产只启用 readable emitter。

- **平面残差**：DeepCAD 局部点 z 从 m 换算后，`abs(z_mm) <= 1e-4` 才允许投影到局部 XY；不修改 x/y。非零残差在曲线 `source.planarity_projection` 保存点字段、原始 z_mm 和容差。超过容差拒绝，禁止无条件抹去 z。
- **轴长度舍入**：源轴长度距 1 不超过 `1e-6` 时归一化；方向、轴顺序和 handedness 不改变。非正交/非右手坐标拒绝，不通过猜测重建。可见修正记录于平面 `source.frame_normalization`。
- **有符号双侧拉伸**：无 taper 时，原始沿法向参数 a、反向参数 b 对应区间 `[min(0,a,-b), max(0,a,-b)]`。跨零转换为正 `distance/distance2` 的 `two_sides`；同侧或一侧为零转换为 `one_side`，按区间选择正负方向。负/零侧且非零 taper 仍 unsupported；零总区间拒绝。
- **追溯**：双侧源参数以 `source.extent_normalization` 保存 `rule=signed_interval_v1`、`extent_one_m`、`extent_two_m` 和 `interval_mm`。该记录不是额外几何操作；修改 IR 几何需同步更新或移除旧追溯。
- **实体语义**：`new` 继续保留独立实体，不因官方参考融合 NewBody 而偷换为 join。实体划分差异单独保留诊断。当前训练评估按 [EVALUATION_SPEC.md](EVALUATION_SPEC.md) 比较占据空间的形状一致性，不以实体数量作为默认拒绝条件；这不改变 IR 的独立实体语义。
- **数据缺损**：空 profile、缺失初始 body 等继续拒绝，不补造数据。
- **验收**：JSON → IR → readable → STEP → 独立参考；候选有效不等于通过。参考坐标来自原始笛卡尔 frame，官方克隆不修改。不依赖 faithful 自洽比较授予 train_ready。

### 2026-10-25：IR→Code 几何契约修正

IR 0.1 仍为未冻结草案。生成规则升级为 readable v3 / ir-geometry-v1；旧 IR 必须重新校验，旧通过状态不迁移。双侧生效 taper 不一致（包括一侧为零）明确不支持；signed/zero extent 的非零 taper 继续拒绝，本轮不新增 taper2、目标 body 或工具分组。DeepCAD adapter 反向曲线时同步 ccw，不再静默修改闭合端点。

相似形状评估允许整体正等比缩放、旋转和平移，但生成器仍须执行 IR 原始尺寸、坐标与布尔语义；本次未修改评估策略。
