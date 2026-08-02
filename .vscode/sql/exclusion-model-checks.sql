-- Penta5 排除模型 · RDS 实战核对 SQL（SQLTools 打开后选中执行）
-- 用途：对照面板/runner 显示，禁止在此文件做 DELETE/UPDATE/INSERT

-- 1) 开奖历史：最新期 + 总量
SELECT COUNT(*) AS draw_count, MAX(period) AS latest_period
FROM official_draws;

-- 2) 最近 5 期开奖（球一~球五）
SELECT period, open_time, ball1, ball2, ball3, ball4, ball5
FROM official_draws
ORDER BY period DESC
LIMIT 5;

-- 3) 预测账本：最新 base_period
SELECT COUNT(*) AS prediction_count, MAX(base_period) AS latest_prediction_period
FROM model_predictions;

-- 4) 评估记录总量
SELECT COUNT(*) AS evaluation_count FROM model_evaluations;

-- 5) 各算法项目快照是否在 RDS（排除模型相关）
SELECT state_key, state_type, updated_at
FROM model_states
WHERE state_key LIKE 'algoProject:%'
ORDER BY updated_at DESC;

-- 6) 更稳排除 / 核心 runner 状态键
SELECT state_key, updated_at
FROM model_states
WHERE state_key IN (
  'currentPrediction',
  'algorithmProjectHealth',
  'modelWatchdog',
  'tipReady'
)
ORDER BY state_key;

-- 7) 指定期号预测 JSON 片段（把 20260801138 换成你要核对的期）
SELECT prediction_id, base_period, generated_at,
       JSON_EXTRACT(raw_json, '$.stableMathFilter') AS stable_math,
       JSON_EXTRACT(raw_json, '$.extraKills') AS extra_kills
FROM model_predictions
WHERE base_period = '20260801138'
ORDER BY generated_at DESC
LIMIT 3;
