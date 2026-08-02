"use strict";
const http = require("http");
const path = require("path");
const {
  MODEL_LONG_WINDOW,
  MODEL_STABLE_WINDOW,
  stableWindowScore,
  wilsonLowerRate
} = require("../_shared/formula_kit");

const REPO_ROOT = path.join(__dirname, "..", "..");
const SCOPE_3 = Object.freeze([0, 1, 2]);
const SCOPE_4 = Object.freeze([0, 1, 2, 3]);
const RATE_WINDOWS = Object.freeze(
  [...new Set([30, 60, 120, 300, 1000, MODEL_STABLE_WINDOW, MODEL_LONG_WINDOW].map((n) => Math.max(1, Number(n || 0))))]
);

function resolveScope(width) {
  return Number(width || 3) >= 4 ? SCOPE_4 : SCOPE_3;
}

function scopeLabel(width) {
  return Number(width || 3) >= 4 ? "千百十个" : "千百十";
}

function dingLabel(width) {
  return Number(width || 3) >= 4 ? "四定" : "三定";
}

function httpGetJson(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: timeoutMs || 15000 }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
        catch (e) { reject(e); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout " + url)));
    req.on("error", reject);
  });
}

function asHistory(rows) {
  return (Array.isArray(rows) ? rows : [])
    .filter((r) => r && Array.isArray(r.numbers) && r.numbers.length >= 4)
    .map((r) => ({
      period: String(r.period || r.uid || ""),
      numbers: r.numbers.slice(0, 5).map(String),
      opentime: r.opentime || ""
    }));
}

function actualShapeCode(record, shapeKey, width) {
  const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
  const scope = resolveScope(width);
  const parts = [];
  for (let i = 0; i < scope.length; i += 1) {
    const n = Number(nums[scope[i]]);
    if (!Number.isFinite(n)) return "";
    if (shapeKey === "shapeSize") parts.push(n >= 5 ? "大" : "小");
    else if (shapeKey === "shapeParity") parts.push(n % 2 ? "单" : "双");
    else return "";
  }
  return parts.join("");
}

function enumerateShapeCodes(shapeKey, width) {
  const pool = shapeKey === "shapeSize" ? ["大", "小"] : ["单", "双"];
  const depth = Number(width || 3) >= 4 ? 4 : 3;
  let codes = [""];
  for (let i = 0; i < depth; i += 1) {
    const next = [];
    codes.forEach((prefix) => pool.forEach((ch) => next.push(prefix + ch)));
    codes = next;
  }
  return codes;
}

function shapeRateMultiWindow(code, shapeKey, records, width) {
  const lims = [...new Set(RATE_WINDOWS.map((n) => Math.max(1, Number(n || 0))))];
  const maxLimit = Math.max(...lims);
  const sliced = (records || []).slice(0, maxLimit);
  const results = {};
  lims.forEach((limit) => {
    results[limit] = { total: 0, hit: 0, rate: 0, missStreak: 0, maxMissStreak: 0, _currentRun: 0, _leading: true };
  });
  for (let i = 0; i < sliced.length; i += 1) {
    const actual = actualShapeCode(sliced[i], shapeKey, width);
    if (!actual || actual.length !== String(code).length) continue;
    const ok = actual !== String(code);
    lims.forEach((limit) => {
      if (i >= limit) return;
      const result = results[limit];
      result.total += 1;
      if (ok) {
        result.hit += 1;
        result._currentRun = 0;
        result._leading = false;
      } else {
        result._currentRun += 1;
        if (result._currentRun > result.maxMissStreak) result.maxMissStreak = result._currentRun;
        if (result._leading) result.missStreak += 1;
      }
    });
  }
  lims.forEach((limit) => {
    const result = results[limit];
    result.rate = result.total ? (result.hit * 100) / result.total : 0;
    delete result._currentRun;
    delete result._leading;
  });
  return results;
}

function scoreCandidate(code, shapeKey, rateWindows, width) {
  const r30 = rateWindows[30] || { total: 0, hit: 0, rate: 0, missStreak: 0 };
  const r60 = rateWindows[60] || r30;
  const r120 = rateWindows[120] || r60;
  const r300 = rateWindows[MODEL_STABLE_WINDOW] || r120;
  const rLong = rateWindows[MODEL_LONG_WINDOW] || rateWindows[1000] || r300;
  const weighted = stableWindowScore(r30, r60, r120, r300, rLong, 2.8);
  const lower120 = wilsonLowerRate(r120.hit, r120.total, 1.44);
  const lower300 = wilsonLowerRate(r300.hit, r300.total, 1.44);
  const lowerLong = wilsonLowerRate(rLong.hit, rLong.total, 1.44);
  // 百分比避开：多窗命中率综合，不以1000硬主导
  let score = weighted.score;
  score += Number(r120.rate || 0) * 0.12;
  score += Number(rLong.rate || 0) * 0.08;
  if (Number(r30.rate || 0) < 85) score -= 2.0;
  if (Number(r30.missStreak || 0) >= 2) score -= 1.6;
  score += Math.max(0, lower120 - 85) * 0.08;
  score += Math.max(0, lowerLong - 85) * 0.05;
  const ding = dingLabel(width);
  return {
    code: String(code),
    shapeKey,
    width: Number(width || 3) >= 4 ? 4 : 3,
    label: (shapeKey === "shapeSize" ? "大小形态" : "单双形态") + "·" + ding,
    score: Number(score.toFixed(4)),
    stage: weighted.stage,
    rate30: Number(Number(r30.rate || 0).toFixed(2)),
    rate60: Number(Number(r60.rate || 0).toFixed(2)),
    rate120: Number(Number(r120.rate || 0).toFixed(2)),
    rate300: Number(Number(r300.rate || 0).toFixed(2)),
    rateLong: Number(Number(rLong.rate || 0).toFixed(2)),
    lower120: Number(lower120.toFixed(2)),
    lower300: Number(lower300.toFixed(2)),
    lowerLong: Number(lowerLong.toFixed(2)),
    sample120: Number(r120.total || 0),
    sampleLong: Number(rLong.total || 0),
    missStreak30: Number(r30.missStreak || 0),
    hit120: Number(r120.hit || 0),
    total120: Number(r120.total || 0),
    hitLong: Number(rLong.hit || 0),
    totalLong: Number(rLong.total || 0)
  };
}

function rankShapes(history, shapeKey, topN, width) {
  const want = Math.max(1, Number(topN || 3));
  const w = Number(width || 3) >= 4 ? 4 : 3;
  const safe = asHistory(history);
  const candidates = enumerateShapeCodes(shapeKey, w).map((code) => {
    const windows = shapeRateMultiWindow(code, shapeKey, safe, w);
    return scoreCandidate(code, shapeKey, windows, w);
  });
  candidates.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
    if (b.rate120 !== a.rate120) return b.rate120 - a.rate120;
    return String(a.code).localeCompare(String(b.code), "zh-CN");
  });
  return candidates.slice(0, want).map((item, idx) => Object.assign({}, item, { rank: idx + 1 }));
}


function rankShapesMixed(history, topN, width) {
  const want = Math.max(1, Number(topN || 3));
  const w = Number(width || 3) >= 4 ? 4 : 3;
  const safe = asHistory(history);
  const candidates = [];
  ["shapeSize", "shapeParity"].forEach((shapeKey) => {
    enumerateShapeCodes(shapeKey, w).forEach((code) => {
      const windows = shapeRateMultiWindow(code, shapeKey, safe, w);
      candidates.push(scoreCandidate(code, shapeKey, windows, w));
    });
  });
  candidates.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
    if (b.rate120 !== a.rate120) return b.rate120 - a.rate120;
    if (b.rateLong !== a.rateLong) return b.rateLong - a.rateLong;
    return String(a.code).localeCompare(String(b.code), "zh-CN");
  });
  const picked = [];
  const used = new Set();
  candidates.forEach((item) => {
    if (picked.length >= want) return;
    const id = item.shapeKey + ":" + item.code;
    if (used.has(id) || used.has(item.code)) return;
    used.add(id);
    used.add(item.code);
    picked.push(Object.assign({}, item, {
      rank: picked.length + 1,
      group: picked.length + 1,
      groupLabel: "形态组合" + (picked.length + 1),
      attack: "除" + item.code
    }));
  });
  return picked;
}

async function fetchHistory(limit) {
  const urls = [
    process.env.SHAPE_HISTORY_URL || "http://127.0.0.1:8787/history?limit=" + (limit || 1200),
    "http://127.0.0.1:8787/history?limit=800",
    "http://127.0.0.1:3001/history?limit=500"
  ];
  for (const url of urls) {
    try {
      const data = await httpGetJson(url, 20000);
      const rows = Array.isArray(data) ? data
        : (Array.isArray(data && data.history) ? data.history
          : (Array.isArray(data && data.rows) ? data.rows : []));
      if (rows.length) return asHistory(rows);
    } catch (_) { /* try next */ }
  }
  return [];
}


function jointAvoidOk(record, trio, width) {
  for (let i = 0; i < trio.length; i += 1) {
    const item = trio[i];
    if (!item) return false;
    const actual = actualShapeCode(record, item.shapeKey, width);
    if (!actual || actual.length !== String(item.code).length) return false;
    if (actual === String(item.code)) return false;
  }
  return true;
}

function jointRateMultiWindow(trio, records, width) {
  const lims = [...new Set(RATE_WINDOWS.map((n) => Math.max(1, Number(n || 0))))];
  const maxLimit = Math.max(...lims);
  const sliced = (records || []).slice(0, maxLimit);
  const results = {};
  lims.forEach((limit) => {
    results[limit] = { total: 0, hit: 0, rate: 0, missStreak: 0, maxMissStreak: 0, _currentRun: 0, _leading: true };
  });
  for (let i = 0; i < sliced.length; i += 1) {
    const ok = jointAvoidOk(sliced[i], trio, width);
    lims.forEach((limit) => {
      if (i >= limit) return;
      const result = results[limit];
      result.total += 1;
      if (ok) {
        result.hit += 1;
        result._currentRun = 0;
        result._leading = false;
      } else {
        result._currentRun += 1;
        if (result._currentRun > result.maxMissStreak) result.maxMissStreak = result._currentRun;
        if (result._leading) result.missStreak += 1;
      }
    });
  }
  lims.forEach((limit) => {
    const result = results[limit];
    result.rate = result.total ? (result.hit * 100) / result.total : 0;
    delete result._currentRun;
    delete result._leading;
  });
  return results;
}

/**
 * 三个形态组合一起避开：三组条件同时成立（AND）才算中。
 * 联合命中率（百分比）主导，三组 AND 全过才算中。
 */
function pickJointShapeAvoidTrio(history, groupN, width) {
  const want = Math.max(1, Number(groupN || 3));
  const w = Number(width || 3) >= 4 ? 4 : 3;
  const safe = asHistory(history);
  const pool = [];
  ["shapeSize", "shapeParity"].forEach((shapeKey) => {
    enumerateShapeCodes(shapeKey, w).forEach((code) => {
      const windows = shapeRateMultiWindow(code, shapeKey, safe, w);
      pool.push(scoreCandidate(code, shapeKey, windows, w));
    });
  });
  pool.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
    if (b.rate120 !== a.rate120) return b.rate120 - a.rate120;
    return b.rateLong - a.rateLong;
  });
  if (want !== 3) {
    return { joint: null, groups: rankShapesMixed(history, want, w) };
  }
  const shortlist = pool.slice(0, Math.min(pool.length, 14));
  let best = null;
  for (let i = 0; i < shortlist.length; i += 1) {
    for (let j = i + 1; j < shortlist.length; j += 1) {
      for (let k = j + 1; k < shortlist.length; k += 1) {
        const trio = [shortlist[i], shortlist[j], shortlist[k]];
        const ids = trio.map((x) => x.shapeKey + ":" + x.code);
        if (new Set(ids).size !== 3) continue;
        const windows = jointRateMultiWindow(trio, safe, w);
        const r30 = windows[30] || { total: 0, hit: 0, rate: 0, missStreak: 0 };
        const r120 = windows[120] || r30;
        const rLong = windows[MODEL_LONG_WINDOW] || windows[1000] || r120;
        const lowerLong = wilsonLowerRate(rLong.hit, rLong.total, 1.44);
        const lower120 = wilsonLowerRate(r120.hit, r120.total, 1.44);
        const score = Number(r120.rate || 0) * 0.45 + lower120 * 0.25
          + Number(rLong.rate || 0) * 0.2 + lowerLong * 0.1
          - Number(r30.missStreak || 0) * 1.2;
        const cand = {
          score: Number(score.toFixed(4)),
          rate30: Number(Number(r30.rate || 0).toFixed(2)),
          rate120: Number(Number(r120.rate || 0).toFixed(2)),
          rateLong: Number(Number(rLong.rate || 0).toFixed(2)),
          lower120: Number(lower120.toFixed(2)),
          lowerLong: Number(lowerLong.toFixed(2)),
          hit120: Number(r120.hit || 0),
          total120: Number(r120.total || 0),
          hitLong: Number(rLong.hit || 0),
          totalLong: Number(rLong.total || 0),
          missStreak30: Number(r30.missStreak || 0),
          trio
        };
        if (!best
          || cand.rate120 > best.rate120
          || (cand.rate120 === best.rate120 && cand.lower120 > best.lower120)
          || (cand.rate120 === best.rate120 && cand.lower120 === best.lower120 && cand.score > best.score)) {
          best = cand;
        }
      }
    }
  }
  if (!best) {
    return { joint: null, groups: rankShapesMixed(history, 3, w) };
  }
  const groups = best.trio.map((item, idx) => Object.assign({}, item, {
    rank: idx + 1,
    group: idx + 1,
    groupLabel: "形态组合" + (idx + 1),
    attack: "除" + item.code
  }));
  return {
    joint: {
      mode: "and",
      label: "形态百分比三组一起避开",
      score: best.score,
      rate30: best.rate30,
      rate120: best.rate120,
      rateLong: best.rateLong,
      lower120: best.lower120,
      lowerLong: best.lowerLong,
      hit120: best.hit120,
      total120: best.total120,
      hitLong: best.hitLong,
      totalLong: best.totalLong,
      missStreak30: best.missStreak30,
      shot: groups.map((g) => g.attack).join("")
    },
    groups
  };
}


module.exports = {
  REPO_ROOT,
  SCOPE_3,
  SCOPE_4,
  SHAPE_SCOPE: SCOPE_3,
  fetchHistory,
  asHistory,
  rankShapes,
  rankShapesMixed,
  pickJointShapeAvoidTrio,
  jointAvoidOk,
  actualShapeCode,
  scopeLabel,
  dingLabel
};
