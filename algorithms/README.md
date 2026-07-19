# 五维更稳数学排除算法

给 Penta5 框架提供额外、可独立运行的数学过滤算法源。

**口径（与主模型一致）：** 只用球一~球四（千百十个）；球五不参与统计与排除。「五维」指五条规则，不是五星五码。

## 文件

| 文件 | 作用 |
|------|------|
| `lottery_filter.py` | 五维算法核心（四码） |
| `run.py` | CLI：读 CSV / JSON / RDS，打印排除结论并写 JSON |
| `stable_math_filter_bridge.js` | Node 桥，供 `penta5_backend_runner.js` 调用 |
| `data.csv.sample` | 离线样例（可复制为 `data.csv`） |

## 运行

```bash
# 阿里服务器默认：从 RDS 读全量官方开奖
cd /root/penta5-runner
python3 run.py --rds

# 或读 CSV
cp algorithms/data.csv.sample algorithms/data.csv
python3 algorithms/run.py --csv algorithms/data.csv

# 或直接传 JSON（取前四码；第五码可省略）
python3 algorithms/run.py --json '[[1,4,8,6],[5,9,5,9]]'
```

输出文件：`data/stable_math_filter.json`（完整版，含 `rules.details`；`version: stable-math-filter-v3`）

内存/state/prediction 仅挂载 `slimStableMathFilter` 摘要；调试完整明细请读缓存文件或 `GET /stable-math-filter?full=1`。

## HTTP

- `GET /stable-math-filter` — 返回当前排除摘要（需面板鉴权）
- `GET /stable-math-filter?full=1` — 返回完整缓存（含 rules.details）
- `GET /stable-math-filter?refresh=1` — 强制按内存 history 重算

面板「下一期大数据排除建议」中展示更稳排除：单码 / 形态 / 跨度 / 和值 / 重号 / 连号 / 奇偶比 / 大小比。

## 规则摘要

与主模型 `extra.*` 结构排除口径对齐（静态「更稳」极值集）：

1. 近 100 期频次 Z-Score，`|Z| > 2.58` 排除单码
2. 当前遗漏 ≥ 历史最大遗漏 80%，且近 10 期出现 ≤ 1 次
3. 形态：全奇/全偶/全大/全小/全012路（对应 `4单0双`/`0单4双`/`4大0小`/`0大4小`）
4. 跨度 `0/1/9`（`spanAvoid`）
5. 和值 `<6` 或 `>30`，并输出区间 `0-5`/`31-36`（`sumRangeAvoid`）
6. 重号：`三重`/`四重`（`repeatAvoid`）
7. 连号：`三连`/`四连`（`consecutiveAvoid`）
8. 奇偶比：`4单0双`/`0单4双`（`parityRatioAvoid`）
9. 大小比：`4大0小`/`0大4小`（`sizeRatioAvoid`）

输出版本：`stable-math-filter-v3`
