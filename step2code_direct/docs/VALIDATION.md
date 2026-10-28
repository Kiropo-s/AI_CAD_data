# 直接转换工具验证记录

日期：2026-10-26。版本：`step2scapi-direct 0.1.0`。目录：`F:\AI_CAD\step2code_direct`。

## 已完成验证

- 使用已有 `envs\simplecad` 环境；Python 3.13.14、SimpleCADAPI 2.2.6、cadquery-ocp 7.9.3.1.1。逐例 report 保留实际依赖版本。
- 最终 20 项 unittest 全部通过，日志为 `out/unittest-final-v1.log`。
- 12 类几何样例：box、cylinder、annulus、box_with_hole、two_holes、oblique_box、oblique_annulus、line_arc、major_arc、disjoint、overlapping_bodies、offset_cylinder；均实际构建、STEP 导出回读、逐实体覆盖和 CD/IoU/V 验证。
- 通过独立 Python `-I` 加载生成程序，确认生成代码不依赖仓库导入路径。
- 测试中的两实体模型验证：第一实体半径乘 1.1 后体积比 1.21；拉伸长度再乘 1.1 后体积比 1.1；位置平移 `(3,-4,8)` mm 后体积保持，第二实体不变。这是有限参数测试，不代表全面编辑验证。
- 验证失败与不确定状态不会伪装为通过；覆盖损坏 STEP、不支持几何、源实体缺失、逐实体对应错误、必需度量失败、worker 超时/非零退出/缺少报告、输入消失和已有输出保护。
- 对照测试覆盖历史 SHA256 缺失、哈希不同、未匹配样本、配置不同和未完成运行。

## 真实 ABC 首 20 例

输入 `F:\AI_CAD\datasets\abc\step\abc_0000`，按文件名取 `00000000–00000019`。world 模式；每形状 8192 个表面采样点；seed 0；每例 120 秒。保留共享评估器的 CD/L≤0.01、IoU≥0.999、相对体积误差≤0.001 规则。

最终结果：**5 train_ready / 15 provisional / 0 rejected**。15 个 provisional 均为 unsupported_geometry。

通过 ID：`00000005`、`00000007`、`00000017`、`00000018`、`00000019`。其中 `00000017` 保留 64 个独立实体。结果见：

- `out/abc-first20-direct-final-v1/summary.json`
- `out/abc-first20-direct-final-v1/summary.csv`
- 每例目录下的 `model.scapi.py`、`model.symbols.json`、`model.step`、`report.json`、`worker.log`

最终目录不含 IR 文件。源码通过独立的 OCP 原生几何路径产生，不调用原 IR 识别器或生成器。

## 保存的对照

均按文件名与历史源 SHA256 匹配；20 例完整匹配，没有未匹配样本。

| 已保存路线 | 通过数 | 首 20 例通过率 |
| --- | ---: | ---: |
| 原直线拉伸路线 0.2.0 | 5 | 25% |
| 直接路线 0.1.0 | 5 | 25% |
| 原路线 0.3.0，增加旋转体 | 6 | 30% |

与 0.2.0 的 20 个样本状态一致；对照在 `out/compare-prism-baseline-v1/`。

与当前 0.3.0 对照在 `out/compare-current-v03-v1/`。其多通过的输入是 `00000009_9b3d6a97e8de4aa193b81000_step_001.step`。直接版尚无一般旋转体重建能力，因此这份结果包含能力范围差异。

每份对照保存 JSON、CSV 和 Markdown。历史报告没有共享导入/运行时/评估器源码指纹，不能确认历史实现完全一致；新直接版逐例保存这些源码哈希。

## 解释边界

- 上述结果只覆盖首 20 例，没有处理全部 10,000 个 STEP，不能外推全量通过率。
- 两条路线使用独立识别器，代码生成接口、模板复用和当前支持范围有所不同。相同或不同的结果均不单独证明 IR 的因果影响。
- 原路线历史结果是只读加载，未为本次实验重新运行。耗时不作为同硬件、同负载的受控速度结论。
- 直接版暂只支持每源实体一次解析直线拉伸；未实现一般旋转、圆角/倒角、放样、扫掠、样条及复杂特征分解。
- 所有几何通过结果限于 geometry_reconstruction。history_recovered=false，原装配树 unsupported，普通运行 parameter_edit_validation=not_run。
- 本次修正了直接边/线框接口的孔方向约定：先用解析有向面积统一各轮廓为 CCW，再由 SCAPI 反转孔。不依赖样条离散化或曲线采样近似。

机器记录见 `docs/validation-summary.json`。复现、批量运行和对照命令见上一级 README。
