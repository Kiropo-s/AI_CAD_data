# step2scapi v0.1 验证记录

> 历史记录：本文件描述 0.x 阶段。其 `out/` 逐例产物已于 2026-10-28 清理；保留的测试日志见 [legacy-logs](results/legacy-logs/)，清理范围见 [PRUNE_LOG](results/PRUNE_LOG.md)。文内指向 `../out/...` 的链接为当时路径，已不再存在。

日期：2026-10-26。运行环境：本机 `envs/simplecad`，Python 3.13.14、SimpleCADAPI 2.2.6、cadquery-ocp 7.9.3.1.1。

## 验证结果

- 生成代码额外通过 Python `-I` 隔离模式验证：不依赖项目模块即可 build；顶部圆半径乘以 1.1 后，体积比实测为 1.21。
- 15 项 unittest 全部通过；其中端到端测试覆盖 12 类真实 OCC 几何，均以导出的独立 STEP 为输入并生成独立 SCAPI。
- 12 类样例的全部产物保存于 [readable-review-v1](../out/readable-review-v1/summary.json)。已审阅尺寸常量、profile/feature/build 层次、显式传参及多实体 body 数据流；圆、孔、混合线弧、大圆弧和倾斜平面额外检查了草图表达。
- 测试日志：[unittest.log](results/legacy-logs/validation-unittest.log)。测试包含世界坐标下平移和缩放错误的拒绝、必需度量异常的 provisional、子进程超时/崩溃隔离和输出目录保护。
- ABC 前 20 例：5 个 train_ready、15 个 provisional，后者均因 unsupported_geometry。没有运行全量 1,000 例。
- 批量报告：[summary.json](../out/abc-first20-v1/summary.json)；最新 CLI 单例复核：[00000007](../out/abc-00000007-final/summary.json)。

## ABC 通过样本的实测指标

默认 world 模式；CD 为双向采样平均距离，L 为参考形状的惯性特征长度。V 为实体空间并集体积。下表保持 IoU 原始值。

| ID | 拉伸特征数 | CD (mm) | CD/L | IoU | 相对 V 误差 |
|---|---:|---:|---:|---:|---:|
| 00000005 | 10 | 2.43905742449 | 0.00369158445184 | 0.99999999978503673 | 2.14962547244e-10 |
| 00000007 | 1 | 1.33997632947 | 0.00596966760659 | 1 | 1.7598709595e-16 |
| 00000017 | 64 | 1.66880211829 | 0.00387104092728 | 1 | 0 |
| 00000018 | 16 | 1.2029734664 | 0.00380038433333 | 1.0000000000042333 | 4.23284886385e-12 |
| 00000019 | 16 | 1.2029734664 | 0.00380038433333 | 1.0000000000042333 | 4.23284886385e-12 |

`00000018`、`00000019` 的 IoU 存在约 4e-12 的微小正向数值越界；沿用 v4 的双向 Cut 可靠性核查后通过，报告保留原值及 `volume_iou_roundoff_verified` 诊断，没有将其截断为 1。

## 12 类样例结构审阅

| 样例 | 验证内容 |
|---|---|
| box | 有序闭合折线、拉伸距离参数 |
| cylinder | 完整圆、半径参数 |
| annulus | 外圆和孔各自独立半径 |
| box_with_hole | 折线外轮廓和圆孔 |
| two_holes | 明确 inner_profiles、多孔参数独立 |
| line_arc | 共享端点、线弧表达、弧半径参数 |
| major_arc | 大于半圆的弧方向、弦边闭合 |
| oblique_box | 源平面坐标架、非轴向拉伸 |
| oblique_annulus | 倾斜草图上的同心孔 |
| offset_cylinder | 保留源平移位置 |
| disjoint | 多 profile/feature、有序 body 集合及装配 |
| overlapping_bodies | 每个实体独立重建、验收按空间并集去重 |

## 首版边界

本工具只识别可证明的解析直线拉伸体。球、锥体、分层台阶实体、复杂圆角及自由曲面不生成替代代码，保留 provisional；仅表面数据也不支持。识别端面的候选经过 bbox 和双向 Cut 对称差检查，最终状态还必须依据实际 build 返回值、候选 STEP 回读，以及源参考的三项度量决定。

生成方案不是原始建模历史，所有报告均标记 history_recovered=false。默认 world 模式也不会用自由缩放或姿态配准掩盖尺寸/位置错误。相似变换验收仅在显式选择 `--validation-mode similarity` 时启用。
