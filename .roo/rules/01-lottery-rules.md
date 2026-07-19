# 过滤与数据清洗核心规则

本文件为 Roo Code / 本仓库的**永久系统规范**。后续改动「更稳排除 / 数据清洗」时必须遵守。

## 强制约束

- **输入清洗**：必须使用 `normalize_draw` 去掉特殊字符/字母，仅保留 0-9 四码。
- **保留维度**：原有的 Z-Score、遗漏、全奇偶大小路、跨度 0/1/9、和值 `<6/>30` 必须严格保留。
- **代码约束**：只允许修改相关的过滤和前端展示文件（如 `algorithms/lottery_filter.py` 等），严防内存泄漏，保持进程 RSS 稳定在 300MB 以下。

## 与主模型对齐的排除口径（stable-math-filter-v3）

在上述五维基础上，静态极值须与主模型 `extra.*` 键名对齐，不可擅自删减：

| 维度 | 更稳静态极值 | 主模型键 |
|------|--------------|----------|
| 重号 | `三重` / `四重` | `repeatAvoid` |
| 连号 | `三连` / `四连` | `consecutiveAvoid` |
| 奇偶比 | `4单0双` / `0单4双` | `parityRatioAvoid` |
| 大小比 | `4大0小` / `0大4小` | `sizeRatioAvoid` |
| 和值区间 | 增补 `0-5` / `31-36` | `sumRangeAvoid` |

口径：**球一~球四**；球五不参与统计与排除。

## 允许改动的文件范围

- `algorithms/lottery_filter.py`
- `algorithms/run.py`
- `algorithms/stable_math_filter_bridge.js`
- `algorithms/README.md`
- `penta5_backend_runner.js`（仅 stableMath 挂载 / assist / 面板所需字段）
- `panel.html`（更稳排除展示行）
- `data/stable_math_filter.json`（算法输出缓存，勿手改业务语义）

非过滤相关的机器人、代理、RDS、监控脚本等，**默认不要为“排除规则”去改**。

## 内存与长跑

- 禁止在循环中无上限地累积 DataFrame / 全局 List / 大 `codes` 数组。
- 预测侧 `recentExactCode*` 封存后必须 omit，结算按 `baseUid + lookback` 重建。
- 关注 `penta5_backend_runner` RSS；监控日志见根目录 `memory.log`（目标：稳定 **&lt; 300MB**）。
