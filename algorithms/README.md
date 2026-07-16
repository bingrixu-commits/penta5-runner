# 五维更稳数学排除算法

给 Penta5 框架提供额外、可独立运行的数学过滤算法源。

## 文件

| 文件 | 作用 |
|------|------|
| `lottery_filter.py` | 五维算法核心 |
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

# 或直接传 JSON
python3 algorithms/run.py --json '[[1,4,8,6,2],[5,9,5,9,7]]'
```

输出文件：`data/stable_math_filter.json`

## HTTP

- `GET /stable-math-filter` — 返回当前排除结论
- `GET /stable-math-filter?refresh=1` — 强制按内存 history 重算

面板「下一期大数据排除建议」中新增 4 行：更稳排除单码/形态/跨度/和值。

## 规则摘要

1. 近 100 期频次 Z-Score，`|Z| > 2.58` 排除
2. 当前遗漏 ≥ 历史最大遗漏 80%，且近 10 期出现 ≤ 1 次
3. 全奇/全偶/全大/全小/全012路
4. 跨度 0/1/9
5. 和值 `<8` 或 `>37`
