"use strict";

/**
 * Node 桥接：调用 algorithms/run.py（更稳数学排除 v3，九条规则）
 * + 软筛选 / 风险评估（soft_risk_filter.js）。
 *
 * 热路径用 async；Tip 封存前用 runStableMathFilterSync 对齐期号（短暂阻塞可接受）。
 * 组合筛选请走 scoreCombination / softFilterCombinations，勿对 exclude_* 做绝对删号。
 */

const fs = require("fs");
const path = require("path");
const { spawn, spawnSync } = require("child_process");
const softRisk = require("./soft_risk_filter");

const ROOT = path.join(__dirname, "..");
const RUN_PY = path.join(__dirname, "run.py");
const DEFAULT_OUT = path.join(ROOT, "data", "stable_math_filter.json");

function buildHistoryPayload(history) {
  const list = Array.isArray(history) ? history : [];
  // 五维过滤只需近窗统计；限制 stdin 体积，避免父进程每次拼出全量 5000 期 JSON
  const maxRows = Math.max(200, Number(process.env.STABLE_MATH_HISTORY_LIMIT || 1200));
  const clipped = list.length > maxRows ? list.slice(0, maxRows) : list;
  return {
    latest_period: list[0] && list[0].period ? String(list[0].period) : null,
    history: clipped.map((row) => ({
      period: row && row.period != null ? String(row.period) : "",
      numbers: Array.isArray(row && row.numbers)
        ? row.numbers.slice(0, 4).map((n) => Number(n))
        : []
    }))
  };
}

function readCachedFilter(outPath) {
  const file = outPath || DEFAULT_OUT;
  try {
    if (!fs.existsSync(file)) return null;
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (_) {
    return null;
  }
}

/** 摘要版：供 state / prediction 持久化；完整 rules.details 仅保留在 data/stable_math_filter.json */
function slimStableMathFilter(filter) {
  if (!filter || typeof filter !== "object") return null;
  if (filter.slim === true && !filter.rules) {
    // 旧缓存也可能缺 softRisk；补齐后返回，避免下游仍按硬杀理解
    if (!filter.softRisk) {
      return Object.assign({}, filter, { softRisk: softRisk.softRiskMeta() });
    }
    return filter;
  }
  return {
    slim: true,
    version: filter.version || "stable-math-filter-v3",
    algorithm: filter.algorithm || null,
    ball_count: filter.ball_count != null ? filter.ball_count : 4,
    ball5_excluded_from_model: filter.ball5_excluded_from_model !== false,
    history_count: filter.history_count != null ? filter.history_count : null,
    latest_period: filter.latest_period || null,
    latest_draw: Array.isArray(filter.latest_draw) ? filter.latest_draw.slice(0, 4) : null,
    exclude_digits: Array.isArray(filter.exclude_digits) ? filter.exclude_digits.slice() : [],
    exclude_shapes: Array.isArray(filter.exclude_shapes) ? filter.exclude_shapes.slice() : [],
    exclude_spans: Array.isArray(filter.exclude_spans) ? filter.exclude_spans.slice() : [],
    exclude_sum_ranges: Array.isArray(filter.exclude_sum_ranges) ? filter.exclude_sum_ranges.slice() : [],
    exclude_repeats: Array.isArray(filter.exclude_repeats) ? filter.exclude_repeats.slice() : [],
    exclude_consecutives: Array.isArray(filter.exclude_consecutives) ? filter.exclude_consecutives.slice() : [],
    exclude_parity_ratios: Array.isArray(filter.exclude_parity_ratios) ? filter.exclude_parity_ratios.slice() : [],
    exclude_size_ratios: Array.isArray(filter.exclude_size_ratios) ? filter.exclude_size_ratios.slice() : [],
    summary: filter.summary && typeof filter.summary === "object" ? filter.summary : null,
    framework: filter.framework && typeof filter.framework === "object" ? filter.framework : null,
    source: filter.source || null,
    // 软筛选标识：exclude_* 为建议维度，组合筛选走 Risk Scoring
    softRisk: filter.softRisk && typeof filter.softRisk === "object"
      ? filter.softRisk
      : softRisk.softRiskMeta()
  };
}

function resolveFilterForSoft(filter, options) {
  if (filter && typeof filter === "object") return filter;
  const opts = options || {};
  return readCachedFilter(opts.outPath || DEFAULT_OUT);
}

function scoreCombination(draw, filter, options) {
  const opts = options || {};
  const base = resolveFilterForSoft(filter, opts);
  const enriched = softRisk.mergePositionKillsIntoFilter(base, opts.positionKills);
  return softRisk.scoreCombination(draw, enriched);
}

function softFilterCombinations(draws, filter, options) {
  const opts = options || {};
  const base = resolveFilterForSoft(filter, opts);
  const enriched = softRisk.mergePositionKillsIntoFilter(base, opts.positionKills);
  return softRisk.softFilterCombinations(draws, enriched, opts);
}

function applySoftFilterBatch(draws, options) {
  const opts = options || {};
  const filter = resolveFilterForSoft(opts.filter, opts);
  return softFilterCombinations(draws, filter, opts);
}

/**
 * 全量池 Soft Risk 选号：挂 soft_recommend / softRecommend 到 filter 结果。
 * positionKills 来自 tip 位杀（软加分）。
 */
function attachSoftRecommend(result, options) {
  if (!result || typeof result !== "object") return result;
  const opts = options || {};
  if (opts.skipSoftRecommend === true) return result;
  try {
    const soft = softRisk.softRecommendFromPool(result, {
      positionKills: opts.positionKills || null,
      retentionRatio: opts.retentionRatio,
      codeLimit: Number.isFinite(Number(opts.codeLimit)) ? Number(opts.codeLimit) : 0,
      keepRanked: false
    });
    result.soft_recommend = soft;
    result.softRecommend = softRisk.slimSoftRecommend(soft, {
      maxLow: opts.maxLow,
      maxMid: opts.maxMid,
      maxRecommend: opts.maxRecommend
    });
    if (!result.softRisk) result.softRisk = softRisk.softRiskMeta();
    if (opts.printConsole) {
      softRisk.printSoftRecommendConsole(soft, { printLimit: opts.printLimit || 80 });
    }
  } catch (err) {
    result.softRecommendError = String(err && err.message || err);
  }
  return result;
}

function extractPositionKillsFromPrediction(prediction) {
  if (!prediction || typeof prediction !== "object") return null;
  const pk = prediction.positionKills || (prediction.detail && prediction.detail.positionKills);
  if (!pk || typeof pk !== "object") return null;
  return softRisk.normalizePositionKills({ position_kills: pk });
}

const PYTHON_SOFT_POOL_FLAG = process.env.PENTA5_STABLE_MATH_SOFT_POOL_IN_PYTHON === "1";

function buildPythonArgs(outPath, options) {
  const opts = options || {};
  const args = [RUN_PY, "--stdin-json", "--quiet", "--dump-json", "--out", outPath];
  // 默认在 Node 侧做全量 Soft Recommend（便于注入 tip 位杀）；Python 侧可选
  if (!PYTHON_SOFT_POOL_FLAG && opts.pythonSoftPool !== true) {
    args.push("--no-soft-pool");
  }
  if (opts.retentionRatio != null) {
    args.push("--retention", String(opts.retentionRatio));
  }
  return args;
}

function runStableMathFilterAsync(history, options) {
  const opts = options || {};
  const outPath = opts.outPath || DEFAULT_OUT;
  const timeout = Number(opts.timeoutMs || 20000);
  const python = process.env.PENTA5_PYTHON || process.env.PYTHON || "python3";
  const payload = buildHistoryPayload(history);

  return new Promise((resolve) => {
    if (!payload.history.length) {
      resolve({ ok: false, error: "empty-history", result: readCachedFilter(outPath) });
      return;
    }
    try {
      fs.mkdirSync(path.dirname(outPath), { recursive: true });
    } catch (_) {}

    let settled = false;
    let stdout = "";
    let stderr = "";
    const child = spawn(python, buildPythonArgs(outPath, opts), {
      cwd: __dirname,
      env: process.env,
      stdio: ["pipe", "pipe", "pipe"]
    });

    const timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch (_) {}
      if (settled) return;
      settled = true;
      resolve({ ok: false, error: "timeout", result: readCachedFilter(outPath) });
    }, timeout);

    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ ok: false, error: err && err.message ? err.message : String(err), result: readCachedFilter(outPath) });
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (code !== 0) {
        resolve({
          ok: false,
          error: (stderr || stdout || `exit:${code}`).slice(0, 500),
          result: readCachedFilter(outPath)
        });
        return;
      }
      // 必须以 stdout JSON 为准。旧 stub 会 exit0 却只打印演示文本，
      // 若回落磁盘缓存并标 ok:true，会出现“刷新成功但期号永不前进”。
      let result = null;
      let parsedStdout = false;
      try {
        const text = String(stdout || "").trim();
        if (text) {
          result = JSON.parse(text);
          parsedStdout = !!(result && typeof result === "object");
        }
      } catch (_) {
        result = null;
        parsedStdout = false;
      }
      if (!parsedStdout) {
        const cached = readCachedFilter(outPath);
        resolve({
          ok: false,
          error: ((stderr || "").trim() || "python-no-json-stdout").slice(0, 500),
          result: cached
        });
        return;
      }
      if (!result.softRisk) result.softRisk = softRisk.softRiskMeta();
      if (!result.soft_recommend && !result.softRecommend) {
        attachSoftRecommend(result, opts);
        try {
          fs.writeFileSync(outPath, JSON.stringify(result, null, 2), "utf8");
        } catch (_) {}
      }
      resolve({ ok: true, result, outPath });
    });

    try {
      child.stdin.write(JSON.stringify(payload));
      child.stdin.end();
    } catch (err) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { child.kill("SIGKILL"); } catch (_) {}
      resolve({ ok: false, error: err && err.message ? err.message : String(err), result: readCachedFilter(outPath) });
    }
  });
}

/**
 * Tip 封存前同步刷新：必须拿到与 history[0].period 对齐的滤网，
 * 禁止“先封 tip、再异步刷新”导致当期吃上一期更稳（摆设）。
 */
function runStableMathFilterSync(history, options) {
  const opts = options || {};
  const outPath = opts.outPath || DEFAULT_OUT;
  const timeout = Number(opts.timeoutMs || 25000);
  const python = process.env.PENTA5_PYTHON || process.env.PYTHON || "python3";
  const payload = buildHistoryPayload(history);
  if (!payload.history.length) {
    return { ok: false, error: "empty-history", result: readCachedFilter(outPath) };
  }
  try {
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
  } catch (_) {}
  let child;
  try {
    child = spawnSync(python, buildPythonArgs(outPath, opts), {
      cwd: __dirname,
      env: process.env,
      input: JSON.stringify(payload),
      encoding: "utf8",
      timeout,
      maxBuffer: 32 * 1024 * 1024
    });
  } catch (err) {
    return {
      ok: false,
      error: err && err.message ? err.message : String(err),
      result: readCachedFilter(outPath)
    };
  }
  if (child.error) {
    return {
      ok: false,
      error: child.error.message || String(child.error),
      result: readCachedFilter(outPath)
    };
  }
  if (child.status !== 0) {
    return {
      ok: false,
      error: String(child.stderr || child.stdout || `exit:${child.status}`).slice(0, 500),
      result: readCachedFilter(outPath)
    };
  }
  let result = null;
  try {
    const text = String(child.stdout || "").trim();
    if (text) result = JSON.parse(text);
  } catch (_) {
    result = null;
  }
  if (!result || typeof result !== "object") {
    return {
      ok: false,
      error: String(child.stderr || "").trim().slice(0, 500) || "python-no-json-stdout",
      result: readCachedFilter(outPath)
    };
  }
  if (!result.softRisk) result.softRisk = softRisk.softRiskMeta();
  if (!result.soft_recommend && !result.softRecommend) {
    attachSoftRecommend(result, opts);
    try {
      fs.writeFileSync(outPath, JSON.stringify(result, null, 2), "utf8");
    } catch (_) {}
  }
  return { ok: true, result, outPath, sync: true };
}

/** 兼容旧同步调用：仅读缓存，不阻塞。 */
function runStableMathFilter(history, options) {
  const opts = options || {};
  const cached = readCachedFilter(opts.outPath || DEFAULT_OUT);
  if (cached) return { ok: true, result: cached, cached: true };
  return { ok: false, error: "cache-miss", result: null };
}

function attachStableMathFilter(prediction, history, options) {
  if (!prediction || typeof prediction !== "object") return { ok: false, error: "no-prediction" };
  const cached = readCachedFilter((options || {}).outPath || DEFAULT_OUT);
  if (cached) {
    const slim = slimStableMathFilter(cached);
    prediction.stableMathFilter = slim;
    if (!prediction.detail || typeof prediction.detail !== "object") prediction.detail = {};
    prediction.detail.stableMathFilter = slim;
    return { ok: true, result: slim, cached: true };
  }
  return { ok: false, error: "cache-miss", result: null };
}

module.exports = {
  DEFAULT_OUT,
  readCachedFilter,
  slimStableMathFilter,
  runStableMathFilter,
  runStableMathFilterSync,
  runStableMathFilterAsync,
  attachStableMathFilter,
  buildHistoryPayload,
  scoreCombination,
  softFilterCombinations,
  applySoftFilterBatch,
  softRiskMeta: softRisk.softRiskMeta,
  digitRiskBonus: softRisk.digitRiskBonus,
  softRisk
};
