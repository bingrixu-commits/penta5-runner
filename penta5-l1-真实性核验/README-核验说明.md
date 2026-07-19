# Penta5 L1 算法独立性 — 真实性核验包

生成时间：2026-07-19  
仓库：https://github.com/bingrixu-commits/penta5-runner.git  
工作区：`/root/penta5-runner`

## 怎么证明「不是空壳」

1. **真公式源码**在本目录 `algorithms/` 下，不是只调 host 回调的 stub。
2. L1 构建逻辑集中在：
   - `algorithms/_shared/framework_items.js`（约 230KB+，含 11 个框架的 build/ensure）
3. 每个 L1 包有本地 `formula.js`，`index.js` 带 `formulaPath`。
4. Runner 热路径已薄封装：`penta5_backend_runner.js` 中  
   `return frameworkItems.ensure*/build*(...)`  
   （摘录见 `证据/runner-thin-wrappers.txt`）

## 建议你本地打开核对的文件

| 文件 | 看什么 |
|------|--------|
| `algorithms/_shared/framework_items.js` | 是否有完整 `build*Snapshot` / `ensurePrediction*Snapshot` |
| `algorithms/auto-top6/formula.js` | 是否 `require("../_shared/framework_items")` |
| `algorithms/auto-top6/index.js` | 是否有 `formulaPath: .../formula.js` |
| `证据/offline-ensure-success.txt` | 11 个 ensure 离线跑通记录 |
| `证据/smoke-health.txt` | SMOKE OK + tipReady |
| `证据/l1-hasLocalFormula.txt` | 11 包均为 true |

## L0（此前已落地）

- `algorithms/position-kills/formula.js`
- `algorithms/hewei/formula.js`
- `algorithms/extra-kills/formula.js`

## 注意

- `data/algorithm-projects/*/state.json` 只是计算后镜像，不是公式本身。
- Runner 里仍保留部分 `*SnapshotIsComplete` / seal 辅助，用于完整性门闩；**构建公式**已在 `framework_items.js`。
