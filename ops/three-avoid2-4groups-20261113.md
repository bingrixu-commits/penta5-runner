# 三数共现第4组 + 顶部精简卡片 + 单组统计（2026-11-13）

## 口径

- 新增 `threeDigitAvoid3`「三数共现避开4组（参考）」，由后端按同一候选池每期生成；
- **只做面板展示与单组统计**：kill1 / kill3 的 `THREE_AVOID_KEYS` 仍为前 3 组，
  攻击语与「整组命中」定义不变（kill3 仍 8 项）；
- 三组合并 `current` / `previous` 保持历史口径（1+2+3），供旧调用方与 `panel_context` 使用；
- 新增四组合并 `merged4_current` / `merged4_previous`（1+2+3+4 全避开），基线按每期
  4 组号码 10^4 精确枚举；第 4 组缺失的期不计入；
- 单组统计按 4 组分别结算（避开成功=命中），单项基线 97.96%。

## 改动

- `algorithms/kill/extra-kills/formula.js`：生产 L0 额外杀码新增第 4 组生成；
  三数共现的数据滞后换码名单加入新键。
- `penta5_backend_runner.js`：legacy `calculateExtraAvoidKills` 同步新增第 4 组；
  `slimExtraKills` 白名单、独立核验 `independentExtras`、`previousExtraKills`、
  完整性校验与模型标签补齐。**未**加入 `REQUIRED_EXTRA_KILL_KEYS`，旧 tip 不会因缺该键
  被判结构不完整。
- `lib/three_avoid2_stats.py`：v3。4 组单组统计 + 三组合并（兼容）+ 四组合并；
  history 按日存 `groups` / `merged` / `merged4` / `single`。
- `lib/three_avoid2_view.py`：详细区块改为「单组统计」+「合并口径」两张表，标题
  1/2/3/4 组，号码条显示 4 组。
- `lib/three_avoid2_top_view.py`（新增）：面板顶部精简卡片，显示下一期/最新一期
  4 组号码与本业务日 4 组单组命中率，锚点跳到 `#three-avoid2`。
- `run_stats.py`（/root，不在 git）：包装原详细区块调用，先后更新详细区块和顶部卡片。
- `lib/panel_live_refresh.py`：前端热刷新 names 加入 `THREE_AVOID2_TOP`。

## 验证（2026-11-13）

- 新 tip `20261113135` 第 4 组 = `034`，开奖 `6·1·1·3` → 避开成功；第 4 组单组
  1/1，四组合并 1/1（精确基线 91.84%）。
- 服务端 `/panel` 同时含 `THREE_AVOID2_TOP` 与 `THREE_AVOID2` 区块，顶部卡片在
  R1 killer 之前；热刷新脚本已含新 section 名。
- `kill3_tail.THREE_AVOID_KEYS` / `kill1_tail.THREE_AVOID_KEYS` 仍为 3 组；
  `panel_context._three_avoid2()` 仍只输出避开1/2/3组，今日合并口径仍为三组合并。

## 回滚

备份目录：`/root/backups/three-avoid2-4groups-20261113-105633/`
（含 `penta5_backend_runner.js`、`extra-kills-formula.js`、`three_avoid2_stats.py`、
`three_avoid2_view.py`、`panel_live_refresh.py`、`run_stats.py`、`panel.html`）。

回滚顺序：还原上述文件 → `node --check` / `py_compile` → `pm2 restart penta5-runner
penta5-panel-sync` → 重跑一次面板重建（`run_stats.apply_three_avoid2_block`）。
`data/three_avoid2_daily.json` 与 history 中的 `groups`/`merged4` 字段可保留，旧 view
会忽略；若要彻底回退可一并还原 data 备份。
