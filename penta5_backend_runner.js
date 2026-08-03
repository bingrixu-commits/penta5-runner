"use strict";

/**
 * Penta5 阿里云后台常驻分析服务
 * - 不依赖浏览器 localStorage
 * - 开奖 history 以阿里 RDS 为主库（先写 RDS，再灌注内存）；state.json 只落账本/预测/锁，不落全量 history
 * - 默认直连官网 latest 取最新开奖 JSON（已废弃本机 source-proxy :3000）
 */

const http = require("http");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const v8 = require("v8");
const { spawn } = require("child_process");

// 必须在依赖 ALGO_DURABLE_STORE 等开关的 algorithm 模块 require 之前加载 .env
(function loadLocalEnvFile() {
  try {
    const envPath = process.env.PENTA5_ENV_PATH || path.join(__dirname, ".env");
    if (!fs.existsSync(envPath)) return;
    const text = fs.readFileSync(envPath, "utf8");
    for (const rawLine of text.split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#") || line.indexOf("=") <= 0) continue;
      const eq = line.indexOf("=");
      const key = line.slice(0, eq).trim();
      let value = line.slice(eq + 1).trim();
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }
      if (process.env[key] === undefined) process.env[key] = value;
    }
  } catch (_) {}
})();

const stableMathFilterBridge = require("./algorithms/stable_math_filter_bridge");
const codexStablePosition = require("./algorithms/codex_stable_position");
const algorithmProjectIsolation = require("./algorithms/project_isolation");
const fourPosExcludeAlgo = require("./algorithms/four-pos-exclude");
const fourDingFrameworkAlgo = require("./algorithms/four-ding-framework/engine");
const algoOrchestrateL0 = require("./algorithms/orchestrate_l0");
const algoOrchestrateL1 = require("./algorithms/orchestrate_l1");

const qualifiedPicksScanner = require("./algorithms/qualified-picks/scanner");
const qualifiedPicksAudit = require("./algorithms/qualified-picks/audit");
const qualifiedPicksSeal = require("./algorithms/qualified-picks/seal");
const qualifiedPicksSticky = require("./algorithms/qualified-picks/sticky");
const qualifiedPicksDual = require("./algorithms/qualified-picks/dual-position");
const qualifiedPicksSecureStore = require("./algorithms/qualified-picks/secure-store");
const attackTxtConverter = require("./txt_attack_convert.js").Penta5AttackTxtConvert;
const { createBackdeleteSnapshotStore } = require("./backdelete_snapshot_store");
const FOUR_POS_EXCLUDE_SNAPSHOT_VERSION = fourPosExcludeAlgo.VERSION || "four-pos-exclude-v1";
let algorithmOrchestratorsBound = false;

const PORT = Number(process.env.PORT || 3001);
const SOURCE_API_URL = process.env.SOURCE_API_URL || "https://www.uk-wl.net/Penta5Classic/0d2d48772cd48f41e761ef99f40e3ac0/latest";
const REQUEST_TIMEOUT = Number(process.env.REQUEST_TIMEOUT || 10000);
// 开奖时刻：每5分钟的 :12 秒（0/5/10/15…分），全天24小时。
// 源站常在 :05 左右就出本期（opentime 仍标 :12），故从开奖前约7秒开始密拉，开奖后再补多档重试。
const DRAW_SLOT_MINUTES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];
const DRAW_SECOND = Math.max(0, Math.min(59, Number(process.env.DRAW_SECOND || 12)));
const DRAW_SYNC_OFFSETS_MS = String(process.env.DRAW_SYNC_OFFSETS_MS || "-7000,-3000,0,1500,3000,6000,12000,20000,35000,55000")
  .split(",")
  .map((x) => Number(String(x).trim()))
  .filter((n) => Number.isFinite(n) && n >= -60000 && n <= 180000)
  .sort((a, b) => a - b);
const DRAW_SYNC_OFFSETS = DRAW_SYNC_OFFSETS_MS.length
  ? DRAW_SYNC_OFFSETS_MS
  : [-7000, -3000, 0, 1500, 3000, 6000, 12000, 20000, 35000, 55000];
const AUTO_REFRESH_INTERVAL = Math.max(60 * 1000, Number(process.env.AUTO_REFRESH_INTERVAL || process.env.AUTO_REFRESH_MS || 60 * 1000));
const DRAW_FALLBACK_SYNC_MS = Math.max(AUTO_REFRESH_INTERVAL, Number(process.env.DRAW_FALLBACK_SYNC_MS || 3 * 60 * 1000));
const PANEL_USER = process.env.PANEL_USER || "";
const PANEL_PASSWORD = process.env.PANEL_PASSWORD || "";
const PANEL_VIEWER_USER = process.env.PANEL_VIEWER_USER || "";
const PANEL_VIEWER_PASSWORD = process.env.PANEL_VIEWER_PASSWORD || "";
const MAX_HISTORY = Number(process.env.MAX_HISTORY || 5000);
const MAX_PREDICTION_LOG = Number(process.env.MAX_PREDICTION_LOG || 1000);
const PANEL_PREDICTION_LIMIT = Number(process.env.PANEL_PREDICTION_LIMIT || 300);
const PANEL_STATE_PREDICTION_LIMIT = Number(process.env.PANEL_STATE_PREDICTION_LIMIT || 30);
// 面板展示/冷热只需近百期；完整历史可走 GET /history。过大 history 会让前端 JSON.parse+重绘卡顿。
const PANEL_HISTORY_LIMIT = Number(process.env.PANEL_HISTORY_LIMIT || 120);
const PANEL_STATE_CACHE_TTL_MS = Number(process.env.PANEL_STATE_CACHE_TTL_MS || 30 * 1000);
const PANEL_LOG_LIMIT = Number(process.env.PANEL_LOG_LIMIT || 20);
const SERVER_REQUEST_TIMEOUT = Number(process.env.SERVER_REQUEST_TIMEOUT || 15000);
// /sync 会串行抓代理 + 结算 + hydrate，15s 会被掐断 → 监督报 Remote end closed
const SYNC_REQUEST_TIMEOUT = Number(process.env.SYNC_REQUEST_TIMEOUT || Math.max(SERVER_REQUEST_TIMEOUT, 120000));
const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, "data");
const QUALIFIED_PICKS_FILE = path.join(DATA_DIR, "qualified-picks.json");
const QUALIFIED_PICKS_SEALED_FILE = path.join(DATA_DIR, "qualified-picks.sealed.json");
const QUALIFIED_PICKS_LOCKS_FILE = path.join(DATA_DIR, "algorithm-projects", "qualified-picks", "locks.json");
const QUALIFIED_PICKS_LOCKS_SEALED_FILE = path.join(DATA_DIR, "algorithm-projects", "qualified-picks", "locks.sealed.json");
const QUALIFIED_PICKS_DUAL_LOCKS_FILE = path.join(DATA_DIR, "algorithm-projects", "qualified-picks", "dual-locks.json");
const QUALIFIED_PICKS_DUAL_LOCKS_SEALED_FILE = path.join(DATA_DIR, "algorithm-projects", "qualified-picks", "dual-locks.sealed.json");
const SHAPE_AVOID_3_STATE_FILE = path.join(DATA_DIR, "algorithm-projects", "shape-avoid-3", "state.json");
const FOUR_FACTOR_RANK_STATE_FILE = path.join(DATA_DIR, "algorithm-projects", "four-factor-rank", "state.json");
const FOUR_DING_SHAPE_SIZE_STATE_FILE = path.join(DATA_DIR, "algorithm-projects", "four-ding-shape-size", "state.json");
const FOUR_DING_SHAPE_PARITY_STATE_FILE = path.join(DATA_DIR, "algorithm-projects", "four-ding-shape-parity", "state.json");
// const WAN_AVOID_3_STATE_FILE retired
const CLAUDE_ENSEMBLE_STATE_FILE = path.join(DATA_DIR, "algorithm-projects", "claude-ensemble", "state.json");
const CLAUDE_STABLE_STATE_FILE = path.join(DATA_DIR, "algorithm-projects", "claude-stable", "state.json");
const CLAUDE_ENSEMBLE_UPSTREAM = String(process.env.CLAUDE_ENSEMBLE_UPSTREAM || "http://127.0.0.1:3002").replace(/\/$/, "");
const CLAUDE_STABLE_UPSTREAM = String(process.env.CLAUDE_STABLE_UPSTREAM || "http://127.0.0.1:3003").replace(/\/$/, "");
const STATE_FILE = path.join(DATA_DIR, "state.json");
const BACKUP_DIR = path.join(DATA_DIR, "backup");
const backdeleteSnapshotStore = createBackdeleteSnapshotStore({
  dataDir: DATA_DIR,
  converter: attackTxtConverter
});
const BIND_HOST = String(process.env.PENTA5_BIND_HOST || "0.0.0.0").trim() || "0.0.0.0";
const PANEL_SESSION_COOKIE = "penta5_panel_session";
const PANEL_SESSION_MAX_AGE_MS = Number(process.env.PANEL_SESSION_MAX_AGE_MS || 12 * 60 * 60 * 1000);
const LOGIN_RATE_WINDOW_MS = Math.max(60 * 1000, Number(process.env.PENTA5_LOGIN_RATE_WINDOW_MS || 15 * 60 * 1000));
const LOGIN_RATE_MAX_FAILURES = Math.max(3, Number(process.env.PENTA5_LOGIN_MAX_FAILURES || 8));

const DIGIT_POOL = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];
// 二数合统一规则：仅从 02468 中挑 4 个，等价于固定排除其中 1 个。
const ANY_TWO_HEWEI_SOURCE_DIGITS = "02468";
const ANY_TWO_HEWEI_CODE_SIZE = 4;
// 三数合宽命中：混奇偶 5 码（与二数合5码相同：禁止全单/全双）。
const ANY_THREE_HEWEI_CODE_SIZE = 5;
const BACKEND_STATE_VERSION = "backend-truth-audit-v86-remove-fourcode-oldcode";
const FRAMEWORK_SNAPSHOT_STORE_KEYS = [
  "autoTop6",
  "recommend2",
  "killFramework",
  "fourDingFramework"
];
const MODEL_VERSION = "adaptive-independent-v70-remove-fourcode-oldcode";
const THREE_DUISHU_SNAPSHOT_VERSION = "backend-three-duishu-snapshot-v3-single-pair-rotation";
const FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION = fourDingFrameworkAlgo.FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION
  || "backend-four-ding-framework-snapshot-v2-hewei-duishu3-shape3-mixed-vote";
const FOUR_DING_FRAMEWORK_ITEM_COUNT = Number(fourDingFrameworkAlgo.FOUR_DING_FRAMEWORK_ITEM_COUNT || 8);

const STORM_ENTER_AUTO_TOP6_MISS = Math.max(1, Number(process.env.STORM_ENTER_AUTO_TOP6_MISS || 4));
const STORM_ENTER_THREE_DUISHU_MISS = Math.max(1, Number(process.env.STORM_ENTER_THREE_DUISHU_MISS || 5));
const STORM_EXIT_WIN = Math.max(1, Number(process.env.STORM_EXIT_WIN || 2));
const STORM_EXIT_STABLE_TICKS = Math.max(1, Number(process.env.STORM_EXIT_STABLE_TICKS || 2));
const STORM_MODE_FILE = path.join(DATA_DIR, "storm_mode.json");
const MODEL_RATE_WINDOW = Number(process.env.MODEL_RATE_WINDOW || 30);
const MODEL_NEAR_WINDOW = Number(process.env.MODEL_NEAR_WINDOW || 40);
const MODEL_LONG_WINDOW = Number(process.env.MODEL_LONG_WINDOW || MAX_HISTORY || 1000);
const MODEL_STABLE_WINDOW = Number(process.env.MODEL_STABLE_WINDOW || 1000);
const MODEL_FIXED_300_WINDOW = 300;
// 三定等新框架封存真相样本少时，后台重建参考回测目标期数（不计入官方 truth）
// FRAMEWORK_SIM_ENABLED / FRAMEWORK_BACKFILL_ENABLED 默认关闭：后台回填会堵死事件循环，导致结算慢、面板 /state 超时。
function envFlagEnabled(name, defaultValue) {
  const raw = process.env[name];
  if (raw === undefined || raw === null || String(raw).trim() === "") return !!defaultValue;
  return !/^(0|false|off|no)$/i.test(String(raw).trim());
}
const FRAMEWORK_SIM_ENABLED = envFlagEnabled("FRAMEWORK_SIM_ENABLED", false);
const FRAMEWORK_BACKFILL_ENABLED = envFlagEnabled("FRAMEWORK_BACKFILL_ENABLED", false);
const FRAMEWORK_SIM_SAMPLE_TARGET = Math.max(
  20,
  Math.min(Number(process.env.FRAMEWORK_SIM_SAMPLE_TARGET || 80), Number(MAX_HISTORY || 5000), 2500)
);
const FRAMEWORK_BACKFILL_CHUNK = Math.max(1, Math.min(Number(process.env.FRAMEWORK_BACKFILL_CHUNK || 2), 60));
const FRAMEWORK_BACKFILL_MAX_ROUNDS = Math.max(10, Math.min(Number(process.env.FRAMEWORK_BACKFILL_MAX_ROUNDS || 40), 300));
const FRAMEWORK_BACKFILL_GAP_MS = Math.max(1500, Number(process.env.FRAMEWORK_BACKFILL_GAP_MS || 4000));
const PENTA5_PERIODS_PER_DAY = Math.max(1, Math.trunc(Number(process.env.PENTA5_PERIODS_PER_DAY || process.env.ROUNDS_PER_DAY || 288)));
const MODEL_MIN_SAMPLE = Number(process.env.MODEL_MIN_SAMPLE || 8);
const MODEL_LOW_HIT_RATE = Number(process.env.MODEL_LOW_HIT_RATE || 85);
// 用户主指标：千百十个「单定顶1」单码；双码 dual 仅观察
const POSITION_MAIN_LOW_RATE = Number(process.env.POSITION_MAIN_LOW_RATE || 85);
const POSITION_DUAL_LOW_RATE = Number(process.env.POSITION_DUAL_LOW_RATE || 80);
const POSITION_DUAL_RANDOM_BASELINE = Number(process.env.POSITION_DUAL_RANDOM_BASELINE || 80);

function modelLowRateForKey(key) {
  const raw = String(key || "");
  const clean = raw.replace(/^extra\./, "");
  if (/^position\.(wan|qian|bai|shi)\.main$/.test(raw)) return POSITION_MAIN_LOW_RATE;
  if (/^position\.(wan|qian|bai|shi)\.dual$/.test(raw)) return POSITION_DUAL_LOW_RATE;
  if (/^position\.(wan|qian|bai|shi)\.backup$/.test(raw)) return POSITION_DUAL_LOW_RATE;
  // 官方账本键 wan/qian/bai/shi = 双码排除账本（非单定顶1）
  if (/^(wan|qian|bai|shi)$/.test(clean)) return POSITION_DUAL_LOW_RATE;
  const meta = highBaselineScoreMeta(raw);
  if (meta) return Number(meta.baseline) - 3;
  return MODEL_LOW_HIT_RATE;
}

function isPositionDualKey(key) {
  const raw = String(key || "");
  const clean = raw.replace(/^extra\./, "");
  return /^position\.(wan|qian|bai|shi)\.dual$/.test(raw) || /^(wan|qian|bai|shi)$/.test(clean);
}

function isPositionMainKey(key) {
  return /^position\.(wan|qian|bai|shi)\.main$/.test(String(key || ""));
}
// 高基线模型：原始通过率天然很高，禁止当成「强预测力」展示/评级
const HIGH_BASELINE_SCORE_META = {
  spanAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.5, hint: "极罕形态过滤" },
  sumAvoid: { scoreClass: "structural-rare-avoid", baseline: 97.0, hint: "单点和值过滤" },
  sumRangeAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.0, hint: "极端区间过滤" },
  repeatAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.5, hint: "极罕形态过滤" },
  consecutiveAvoid: { scoreClass: "structural-rare-avoid", baseline: 99.5, hint: "极罕形态过滤" },
  parityRatioAvoid: { scoreClass: "structural-rare-avoid", baseline: 93.0, hint: "极端奇偶比过滤" },
  sizeRatioAvoid: { scoreClass: "structural-rare-avoid", baseline: 93.0, hint: "极端大小比过滤" },
  anyTwoHeweiHit: { scoreClass: "wide-hit-baseline", baseline: 99.0, hint: "宽命中参考" },
  anyThreeHeweiHit: { scoreClass: "wide-hit-baseline", baseline: 97.0, hint: "宽命中参考" },
  twoDigitHit: { scoreClass: "pair-avoid-baseline", baseline: 90.0, hint: "除对数（非共现命中）" }
};

function highBaselineScoreMeta(key) {
  const clean = String(key || "").replace(/^extra\./, "");
  return HIGH_BASELINE_SCORE_META[clean] || null;
}

function isHighBaselineScoreKey(key) {
  return !!highBaselineScoreMeta(key);
}
const POSITION_BLOCK_WINDOW = Number(process.env.POSITION_BLOCK_WINDOW || 120);
const POSITION_BLOCK_SIZE = Number(process.env.POSITION_BLOCK_SIZE || 20);
const POSITION_BLOCK_FLOOR = Number(process.env.POSITION_BLOCK_FLOOR || 65);
const POSITION_SWITCH_MARGIN = Number(process.env.POSITION_SWITCH_MARGIN || 4.5);
const POSITION_MIN_HOLD = Number(process.env.POSITION_MIN_HOLD || 3);
const HEWEI_BLOCK_WINDOW = Number(process.env.HEWEI_BLOCK_WINDOW || 120);
const HEWEI_BLOCK_SIZE = Number(process.env.HEWEI_BLOCK_SIZE || 20);
const HEWEI_BLOCK_FLOOR = Number(process.env.HEWEI_BLOCK_FLOOR || 72);
const HEWEI_UNIFIED_BLOCK_FLOOR = Number(process.env.HEWEI_UNIFIED_BLOCK_FLOOR || 60);
const HEWEI_SWITCH_MARGIN = Number(process.env.HEWEI_SWITCH_MARGIN || 4);
const HEWEI_MIN_HOLD = Number(process.env.HEWEI_MIN_HOLD || 3);
const HEWEI_GUARD_RATE = Number(process.env.HEWEI_GUARD_RATE || 85);
const HEWEI_UNIFIED_GUARD_RATE = Number(process.env.HEWEI_UNIFIED_GUARD_RATE || 72);
const MODEL_WATCHDOG_INTERVAL = Number(process.env.MODEL_WATCHDOG_INTERVAL || 10 * 60 * 1000);
const MODEL_WATCHDOG_LOW_RATE = Number(process.env.MODEL_WATCHDOG_LOW_RATE || MODEL_LOW_HIT_RATE);
const MODEL_WATCHDOG_MIN_SAMPLE = Number(process.env.MODEL_WATCHDOG_MIN_SAMPLE || MODEL_MIN_SAMPLE);
const MODEL_WATCHDOG_BLOCK_FLOOR = Number(process.env.MODEL_WATCHDOG_BLOCK_FLOOR || 65);
const MODEL_WATCHDOG_MEMORY_LIMIT = Number(process.env.MODEL_WATCHDOG_MEMORY_LIMIT || 0.82);
const MODEL_WATCHDOG_REPAIR_COOLDOWN = Number(process.env.MODEL_WATCHDOG_REPAIR_COOLDOWN || 90 * 1000);
const TOP_RECOMMEND_COUNT = 5;
const AUTO_TOP6_RECENT_MISS_LIMIT = Number(process.env.AUTO_TOP6_RECENT_MISS_LIMIT || 3);
const AUTO_TOP6_LONG_FLOOR_OFFSET = Number(process.env.AUTO_TOP6_LONG_FLOOR_OFFSET || 3);
const AUTO_TOP6_LOWER_FLOOR_OFFSET = Number(process.env.AUTO_TOP6_LOWER_FLOOR_OFFSET || 12);
const THREE_DUISHU_MIN_TRAIN = Number(process.env.THREE_DUISHU_MIN_TRAIN || 80);
const THREE_DUISHU_WALK_WINDOW = Number(process.env.THREE_DUISHU_WALK_WINDOW || 120);
const THREE_DUISHU_BLOCK_WINDOW = Number(process.env.THREE_DUISHU_BLOCK_WINDOW || 120);
const THREE_DUISHU_BLOCK_SIZE = Number(process.env.THREE_DUISHU_BLOCK_SIZE || 20);
const THREE_DUISHU_BLOCK_FLOOR = Number(process.env.THREE_DUISHU_BLOCK_FLOOR || 55);
const THREE_DUISHU_SWITCH_MARGIN = Number(process.env.THREE_DUISHU_SWITCH_MARGIN || 4.5);
const THREE_DUISHU_MIN_HOLD = Number(process.env.THREE_DUISHU_MIN_HOLD || 3);
const THREE_DUISHU_PAIR_MAX_HOLD = Math.max(THREE_DUISHU_MIN_HOLD, Number(process.env.THREE_DUISHU_PAIR_MAX_HOLD || 8));
const THREE_DUISHU_PAIRS = [
  { key: "05", digits: ["0", "5"], codes: ["0", "5"] },
  { key: "16", digits: ["1", "6"], codes: ["1", "6"] },
  { key: "27", digits: ["2", "7"], codes: ["2", "7"] },
  { key: "38", digits: ["3", "8"], codes: ["3", "8"] },
  { key: "49", digits: ["4", "9"], codes: ["4", "9"] }
];
const POSITION_LIST = [
  { key: "wan", label: "千", index: 0 },
  { key: "qian", label: "百", index: 1 },
  { key: "bai", label: "十", index: 2 },
  { key: "shi", label: "个", index: 3 },
  { key: "ge", label: "末位", index: 4 }
];
const RETIRED_FOUR_CODE_FRAMEWORK_KEYS = ["sideStable", "coldFramework", "dingKill", "pick5000"];
const REQUIRED_EXTRA_KILL_KEYS = [
  "sumAvoid",
  "sumRangeAvoid",
  "spanAvoid",
  "repeatAvoid",
  "consecutiveAvoid",
  "parityRatioAvoid",
  "sizeRatioAvoid",
  "threeDigitAvoid",
  "twoDigitAvoid",
  "twoDigitAvoid2",
  "twoDigitAvoid3",
  "twoDigitHit",
  "threeDigitHit",
  "threeDigitAvoid2",
  "anyTwoHeweiHit",
  "anyThreeHeweiHit"
];

const DEFAULT_RECORD = {
  uid: "default",
  period: "--",
  opentime: "--",
  numbers: ["5", "1", "6", "9", "1"],
  positions: { wan: "5", qian: "1", bai: "6", shi: "9", ge: "1" },
  metrics: {
    totalSum: 22,
    totalHewei: "2",
    qianBaiShiHewei: "6",
    baiShiGeHewei: "6",
    qianBaiShiGeHewei: "7"
  },
  rawJson: {},
  capturedAt: ""
};

let state = null;
let autoTimer = null;
let watchdogTimer = null;
let syncRunning = false;
let predictionBuildRunning = false;
let stableMathFilterRunning = false;
let stableMathFilterQueued = false;
let algorithmProjectSyncQueued = false;
const loginFailureBuckets = new Map();

function isoNow() {
  return new Date().toISOString();
}

function nowText() {
  return new Date().toLocaleString("zh-CN", { hour12: false });
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function safeJsonStringify(value) {
  try {
    return JSON.stringify(value);
  } catch (err) {
    return String(value);
  }
}

const CORE_PREDICTION_ISSUE_TYPES = new Set([
  "prediction-missing",
  "prediction-base-mismatch",
  "prediction-version-mismatch",
  "stable-math-filter-lag"
]);

function isCorePredictionIssue(issue) {
  return !!(issue && CORE_PREDICTION_ISSUE_TYPES.has(String(issue.type || "")));
}

function normalizeAlgorithmProjectHealth(input) {
  const source = input && typeof input === "object" ? input : {};
  const normalized = {};
  algorithmProjectIsolation.PROJECTS.forEach((project) => {
    const raw = source[project.id] && typeof source[project.id] === "object" ? source[project.id] : {};
    normalized[project.id] = {
      status: raw.status === "degraded" ? "degraded" : "ok",
      issueCount: Math.max(0, Number(raw.issueCount || 0) || 0),
      lastIssue: raw.lastIssue && typeof raw.lastIssue === "object" ? raw.lastIssue : null,
      signature: String(raw.signature || ""),
      updatedAt: String(raw.updatedAt || ""),
      baseUid: String(raw.baseUid || ""),
      basePeriod: String(raw.basePeriod || "")
    };
  });
  return normalized;
}

function summarizeAlgorithmProjectHealth(input) {
  const health = normalizeAlgorithmProjectHealth(input);
  const degraded = Object.entries(health)
    .filter(([, value]) => value && value.status === "degraded")
    .map(([projectId, value]) => ({
      projectId,
      issueCount: Math.max(0, Number(value && value.issueCount || 0) || 0)
    }));
  return {
    degradedProjectCount: degraded.length,
    degradedIssueCount: degraded.reduce((total, item) => total + item.issueCount, 0),
    degradedProjectIds: degraded.map((item) => item.projectId)
  };
}

function resetAlgorithmProjectHealthForPrediction(prediction) {
  if (!state || typeof state !== "object") return;
  const baseUid = String(prediction && prediction.baseUid || "");
  const basePeriod = String(prediction && prediction.basePeriod || "");
  state.algorithmProjectHealth = normalizeAlgorithmProjectHealth({});
  Object.keys(state.algorithmProjectHealth).forEach((projectId) => {
    state.algorithmProjectHealth[projectId].baseUid = baseUid;
    state.algorithmProjectHealth[projectId].basePeriod = basePeriod;
    state.algorithmProjectHealth[projectId].updatedAt = isoNow();
  });
}

function markAlgorithmProjectIssue(issue) {
  if (!state || typeof state !== "object") return { changed: false, projectId: "core-runner" };
  state.algorithmProjectHealth = normalizeAlgorithmProjectHealth(state.algorithmProjectHealth);
  const projectId = algorithmProjectIsolation.projectIdForIssue(issue);
  const entry = state.algorithmProjectHealth[projectId] || {
    status: "ok",
    issueCount: 0,
    lastIssue: null,
    signature: "",
    updatedAt: "",
    baseUid: "",
    basePeriod: ""
  };
  const prediction = state.currentPrediction && typeof state.currentPrediction === "object" ? state.currentPrediction : null;
  const signature = safeJsonStringify({
    type: issue && issue.type || "",
    key: issue && issue.key || "",
    reason: issue && issue.reason || "",
    baseUid: prediction && prediction.baseUid || ""
  });
  if (entry.status === "degraded" && entry.signature === signature) {
    return { changed: false, projectId };
  }
  entry.status = "degraded";
  entry.issueCount = Math.max(0, Number(entry.issueCount || 0) || 0) + 1;
  entry.lastIssue = issue && typeof issue === "object" ? Object.assign({}, issue) : null;
  entry.signature = signature;
  entry.updatedAt = isoNow();
  entry.baseUid = String(prediction && prediction.baseUid || "");
  entry.basePeriod = String(prediction && prediction.basePeriod || "");
  state.algorithmProjectHealth[projectId] = entry;
  try {
    const recorded = algorithmProjectIsolation.recordProjectIssue(DATA_DIR, issue, prediction);
    if (recorded && recorded.payload) {
      if (!state.algorithmProjectSnapshots || typeof state.algorithmProjectSnapshots !== "object") {
        state.algorithmProjectSnapshots = {};
      }
      state.algorithmProjectSnapshots[recorded.projectId] = recorded.payload;
    }
  } catch (error) {
    console.error("[AlgorithmProject] issue record failed", projectId, error && error.message ? error.message : error);
  }
  invalidateModelAuditCache();
  invalidatePanelStateCache();
  return { changed: true, projectId };
}

function reconcileAlgorithmProjectHealth(issues) {
  if (!state || typeof state !== "object") return;
  state.algorithmProjectHealth = normalizeAlgorithmProjectHealth(state.algorithmProjectHealth);
  const activeByProject = Object.create(null);
  (Array.isArray(issues) ? issues : []).forEach((issue) => {
    if (!issue || isCorePredictionIssue(issue)) return;
    // observe-only 不占用 degraded，避免挡住 qualityReady
    if (String(issue.action || "") === "observe-only") return;
    const projectId = algorithmProjectIsolation.projectIdForIssue(issue);
    if (!projectId) return;
    if (!activeByProject[projectId]) activeByProject[projectId] = [];
    activeByProject[projectId].push(issue);
  });
  Object.keys(state.algorithmProjectHealth).forEach((projectId) => {
    const entry = state.algorithmProjectHealth[projectId];
    const active = activeByProject[projectId] || [];
    if (active.length) return;
    if (entry.status === "ok" && !entry.issueCount && !entry.lastIssue) return;
    entry.status = "ok";
    entry.issueCount = 0;
    entry.lastIssue = null;
    entry.signature = "";
    entry.updatedAt = isoNow();
  });
}

function markAlgorithmProjectIssues(issues, actions) {
  const changed = [];
  reconcileAlgorithmProjectHealth(issues);
  (Array.isArray(issues) ? issues : []).forEach((issue) => {
    if (isCorePredictionIssue(issue)) return;
    if (String(issue && issue.action || "") === "observe-only") return;
    const result = markAlgorithmProjectIssue(issue);
    if (!result.changed) return;
    const action = {
      type: "isolate-algorithm-project",
      projectId: result.projectId,
      issueType: String(issue && issue.type || ""),
      key: String(issue && issue.key || "")
    };
    changed.push(action);
    if (Array.isArray(actions)) actions.push(action);
  });
  return changed;
}

function scheduleAlgorithmProjectStateSync(reason) {
  if (algorithmProjectSyncQueued) return;
  algorithmProjectSyncQueued = true;
  setImmediate(() => {
    algorithmProjectSyncQueued = false;
    try {
      if (!state || typeof state !== "object") return;
      state.algorithmProjectHealth = normalizeAlgorithmProjectHealth(state.algorithmProjectHealth);
      const syncResult = algorithmProjectIsolation.syncProjectStates({
        dataDir: DATA_DIR,
        currentPrediction: state.currentPrediction,
        predictions: state.predictions,
        latestRecord: state.latestRecord,
        modelVersion: MODEL_VERSION,
        projectHealth: state.algorithmProjectHealth,
        historyLimit: 120,
        reason: String(reason || "state-save"),
        onSnapshots: (snapshots) => {
          state.algorithmProjectSnapshots = snapshots && typeof snapshots === "object" ? snapshots : {};
        }
      });
      if (syncResult && syncResult.snapshots) {
        state.algorithmProjectSnapshots = syncResult.snapshots;
      }
    } catch (error) {
      console.error("[AlgorithmProject] state sync failed", error && error.message ? error.message : error);
    }
  });
}

function isRetiredFourCodeKey(key) {
  return /^recentExactCodeAvoid(?:\d+|Max)$/i.test(String(key || ""));
}

function deleteRetiredKeys(block, keys) {
  if (!block || typeof block !== "object") return 0;
  let changed = 0;
  keys.forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(block, key)) {
      delete block[key];
      changed += 1;
    }
  });
  return changed;
}

function deleteRetiredFourCodeKeys(block) {
  if (!block || typeof block !== "object") return 0;
  let changed = 0;
  Object.keys(block).forEach((key) => {
    if (!isRetiredFourCodeKey(key)) return;
    delete block[key];
    changed += 1;
  });
  return changed;
}

function stripRetiredFourCodeItems(items) {
  if (!Array.isArray(items)) return 0;
  const kept = items.filter((item) => !isRetiredFourCodeKey(item && item.key));
  const changed = items.length - kept.length;
  if (changed) items.splice(0, items.length, ...kept);
  return changed;
}

function stripRetiredFourCodeArtifacts(prediction) {
  if (!prediction || typeof prediction !== "object") return 0;
  let changed = 0;
  changed += deleteRetiredFourCodeKeys(prediction.extraKills);
  changed += deleteRetiredFourCodeKeys(prediction.detail && prediction.detail.extraKills);
  if (prediction.evaluation && typeof prediction.evaluation === "object") {
    changed += deleteRetiredFourCodeKeys(prediction.evaluation.independentExtras);
    changed += deleteRetiredFourCodeKeys(prediction.evaluation.previousExtraKills);
    changed += deleteRetiredKeys(prediction.evaluation, RETIRED_FOUR_CODE_FRAMEWORK_KEYS);
  }
  FRAMEWORK_SNAPSHOT_STORE_KEYS.forEach((key) => {
    const snapshot = prediction[key];
    changed += stripRetiredFourCodeItems(snapshot && snapshot.items);
    changed += stripRetiredFourCodeItems(prediction[`${key}Items`]);
    changed += stripRetiredFourCodeItems(prediction.evaluation && prediction.evaluation[key] && prediction.evaluation[key].items);
  });
  RETIRED_FOUR_CODE_FRAMEWORK_KEYS.forEach((key) => {
    [key, `${key}Items`, `${key}RepairReport`].forEach((field) => {
      if (Object.prototype.hasOwnProperty.call(prediction, field)) {
        delete prediction[field];
        changed += 1;
      }
    });
    if (prediction.recommendationMeta && Object.prototype.hasOwnProperty.call(prediction.recommendationMeta, `${key}SnapshotVersion`)) {
      delete prediction.recommendationMeta[`${key}SnapshotVersion`];
      changed += 1;
    }
  });
  return changed;
}

function stripRetiredFourCodeState(target) {
  if (!target || typeof target !== "object") return 0;
  let changed = stripRetiredFourCodeArtifacts(target.currentPrediction);
  if (Array.isArray(target.predictions)) {
    target.predictions.forEach((prediction) => {
      changed += stripRetiredFourCodeArtifacts(prediction);
    });
  }
  changed += deleteRetiredFourCodeKeys(target.counters);
  changed += deleteRetiredKeys(target.dailyStreakLedger, RETIRED_FOUR_CODE_FRAMEWORK_KEYS);
  changed += deleteRetiredKeys(target.algorithmProjectHealth, RETIRED_FOUR_CODE_FRAMEWORK_KEYS);
  return changed;
}

function createZeroMap() {
  const map = {};
  DIGIT_POOL.forEach((digit) => {
    map[digit] = 0;
  });
  return map;
}

function calcRate(total, match) {
  if (!total) return null;
  return ((match / total) * 100).toFixed(2);
}

function calcRateNumber(total, match) {
  if (!total) return null;
  return Number(((match / total) * 100).toFixed(2));
}

function decorateTruthScoreFields(target, key, rate) {
  const out = target && typeof target === "object" ? target : {};
  const meta = highBaselineScoreMeta(key);
  const rateNum = rate === null || rate === undefined || !Number.isFinite(Number(rate)) ? null : Number(rate);
  if (meta) {
    out.scoreClass = meta.scoreClass;
    out.randomBaseline = Number(meta.baseline);
    out.scoreHint = `${meta.hint}，非预测力`;
    out.predictivePower = false;
    out.edgeOverBaseline = rateNum === null ? null : Number((rateNum - Number(meta.baseline)).toFixed(2));
    out.truthDisplay = "structural-baseline";
  } else if (isPositionMainKey(key)) {
    out.scoreClass = out.scoreClass || "predictive";
    out.randomBaseline = 90;
    out.scoreHint = "单定顶1 baseline 90";
    out.predictivePower = true;
    out.edgeOverBaseline = rateNum === null ? null : Number((rateNum - 90).toFixed(2));
    out.truthDisplay = "predictive";
  } else if (isPositionDualKey(key)) {
    out.scoreClass = out.scoreClass || "predictive-secondary";
    out.randomBaseline = POSITION_DUAL_RANDOM_BASELINE;
    out.scoreHint = "次要观察：双码排除随机基线约80%";
    out.predictivePower = false;
    out.edgeOverBaseline = rateNum === null ? null : Number((rateNum - POSITION_DUAL_RANDOM_BASELINE).toFixed(2));
    out.truthDisplay = "observe-secondary";
  } else {
    out.scoreClass = out.scoreClass || "predictive";
    out.randomBaseline = null;
    out.scoreHint = "";
    out.predictivePower = true;
    out.edgeOverBaseline = null;
    out.truthDisplay = "predictive";
  }
  return out;
}

function buildCounterTruthRecord(counter, key) {
  const total = Number(counter && counter.total || 0);
  const match = Number(counter && counter.match || 0);
  const rate = calcRateNumber(total, match);
  const record = {
    key,
    label: (counter && counter.label) || key,
    total,
    match,
    rate,
    mainMatch: counter && counter.mainMatch !== undefined ? Number(counter.mainMatch || 0) : undefined,
    sampleOk: total >= MODEL_MIN_SAMPLE,
    truthSource: "official-counter-truth-eligible-only"
  };
  decorateTruthScoreFields(record, key, rate);
  if (record.predictivePower === false && rate !== null && total >= MODEL_MIN_SAMPLE) {
    record.low = rate < (Number(record.randomBaseline || 0) - 3);
  } else if (rate !== null && total >= MODEL_MIN_SAMPLE) {
    record.low = rate < modelLowRateForKey(key);
  } else {
    record.low = false;
  }
  return record;
}

function counterRateText(counter, key) {
  const total = Number(counter && counter.total || 0);
  const match = Number(counter && counter.match || 0);
  if (!total) return "暂无命中记录";
  if (total < MODEL_MIN_SAMPLE) return "数据不足";
  const value = calcRate(total, match);
  if (value === null) return "数据不足";
  const meta = highBaselineScoreMeta(key);
  if (!meta) return `${value}%`;
  const edge = Number(value) - Number(meta.baseline);
  const edgeText = `${edge >= 0 ? "+" : ""}${edge.toFixed(2)}pp`;
  return `${value}%｜相对基线${edgeText}（${meta.hint}，非预测力）`;
}

function createDefaultCounters() {
  return {
    version: 10,
    updatedAt: "",
    positionTotal: { label: "万千百十定位双码总击", total: 0, match: 0 },
    wan: { label: "万位定位双码排除", total: 0, match: 0, mainMatch: 0 },
    qian: { label: "千位定位双码排除", total: 0, match: 0, mainMatch: 0 },
    bai: { label: "百位定位双码排除", total: 0, match: 0, mainMatch: 0 },
    shi: { label: "十位定位双码排除", total: 0, match: 0, mainMatch: 0 },
    totalHewei: { label: "总合尾综合排除", total: 0, match: 0 },
    threeFour: { label: "三个合尾统一避开码", total: 0, match: 0 },
    wanQianBai: { label: "千百十合尾", total: 0, match: 0 },
    qianBaiShi: { label: "百十个合尾", total: 0, match: 0 },
    wanQianBaiShi: { label: "千百十个合尾", total: 0, match: 0 },
    wanQianGe: { label: "千百个合尾(稳定)", total: 0, match: 0 },
    wanQianGeFreq: { label: "千百个合尾(频次)", total: 0, match: 0 },
    wanBaiShi: { label: "千十个合尾(稳定)", total: 0, match: 0 },
    wanBaiShiFreq: { label: "千十个合尾(频次)", total: 0, match: 0 },
    threeDuishu: { label: "三对数排除", total: 0, match: 0 },
    sumAvoid: { label: "单点和值过滤（高基线）", total: 0, match: 0 },
    threeDigitAvoid: { label: "三数共现避开1组", total: 0, match: 0 },
    twoDigitAvoid: { label: "四定对数避开1组", total: 0, match: 0 },
    twoDigitAvoid2: { label: "四定对数避开2组", total: 0, match: 0 },
    twoDigitAvoid3: { label: "四定对数避开3组", total: 0, match: 0 },
    twoDigitHit: { label: "两数共现避开（基线参考）", total: 0, match: 0 },
    threeDigitHit: { label: "三数共现避开2组", total: 0, match: 0 },
    threeDigitAvoid2: { label: "三数共现避开3组", total: 0, match: 0 },
    anyTwoHeweiHit: { label: "二数合尾02468排1码命中（高基线参考）", total: 0, match: 0 },
    anyThreeHeweiHit: { label: "三数合尾宽命中（高基线参考）", total: 0, match: 0 },
    sumRangeAvoid: { label: "和值极端区间过滤（高基线）", total: 0, match: 0 },
    spanAvoid: { label: "跨度极罕形态过滤（高基线）", total: 0, match: 0 },
    repeatAvoid: { label: "重号极罕形态过滤（高基线）", total: 0, match: 0 },
    consecutiveAvoid: { label: "连号极罕形态过滤（高基线）", total: 0, match: 0 },
    parityRatioAvoid: { label: "极端奇偶比过滤（高基线）", total: 0, match: 0 },
    sizeRatioAvoid: { label: "极端大小比过滤（高基线）", total: 0, match: 0 }
  };
}

const COUNTER_METRIC_KEYS = [
  "positionTotal", "wan", "qian", "bai", "shi", "totalHewei", "threeFour",
  "wanQianBai", "qianBaiShi", "wanQianBaiShi",
  "wanQianGe", "wanQianGeFreq", "wanBaiShi", "wanBaiShiFreq",
  "threeDuishu", "sumAvoid",
  "threeDigitAvoid", "twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3",
  "twoDigitHit", "threeDigitHit", "threeDigitAvoid2", "anyTwoHeweiHit", "anyThreeHeweiHit",
  "sumRangeAvoid", "spanAvoid", "repeatAvoid", "consecutiveAvoid",
  "parityRatioAvoid", "sizeRatioAvoid"
];

function currentCounterCohortDescriptor() {
  const snapshotVersions = {
    autoTop6: AUTO_TOP6_SNAPSHOT_VERSION,
    recommend2: RECOMMEND2_SNAPSHOT_VERSION,
    killFramework: KILL_FRAMEWORK_SNAPSHOT_VERSION,
    fourDingFramework: FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION,
    compactAttack: COMPACT_ATTACK_SNAPSHOT_VERSION,
    threeDingTwo: THREE_DING_TWO_SNAPSHOT_VERSION,
    threeDingOne: THREE_DING_ONE_SNAPSHOT_VERSION,
    threeDuishu: THREE_DUISHU_SNAPSHOT_VERSION
  };

  return {
    schema: 1,
    key: [MODEL_VERSION, ...Object.keys(snapshotVersions).sort().map((name) => `${name}=${snapshotVersions[name]}`)].join("|"),
    algorithmVersion: MODEL_VERSION,
    snapshotVersions,
    startedAt: "",
    startPeriod: "",
    endPeriod: "",
    appliedTargetPeriods: []
  };
}

function normalizeCounterCohort(counters, base) {
  const expected = currentCounterCohortDescriptor();
  const existing = counters && counters.cohort && typeof counters.cohort === "object" ? counters.cohort : null;
  if (existing && String(existing.key || "") === expected.key) {
    counters.cohort = Object.assign({}, expected, existing, {
      appliedTargetPeriods: Array.isArray(existing.appliedTargetPeriods)
        ? existing.appliedTargetPeriods.map(String).filter(Boolean).slice(-MAX_HISTORY)
        : []
    });
    return;
  }
  const archivedMetrics = {};
  COUNTER_METRIC_KEYS.forEach((key) => {
    const value = counters && counters[key];
    if (!value || typeof value !== "object") return;
    if (Number(value.total || 0) <= 0 && Number(value.match || 0) <= 0) return;
    archivedMetrics[key] = {
      label: value.label || key,
      total: Number(value.total || 0),
      match: Number(value.match || 0),
      ...(Object.prototype.hasOwnProperty.call(value, "mainMatch") ? { mainMatch: Number(value.mainMatch || 0) } : {})
    };
  });
  const legacy = Array.isArray(counters.legacyCohorts) ? counters.legacyCohorts.slice(-4) : [];
  if (Object.keys(archivedMetrics).length) {
    legacy.push({
      key: existing && existing.key || `legacy-schema-v${Number(counters.version || 0)}`,
      algorithmVersion: existing && existing.algorithmVersion || "unknown",
      snapshotVersions: existing && existing.snapshotVersions || {},
      startPeriod: existing && existing.startPeriod || "",
      endPeriod: existing && existing.endPeriod || "",
      archivedAt: isoNow(),
      metrics: archivedMetrics
    });
  }
  COUNTER_METRIC_KEYS.forEach((key) => {
    counters[key] = Object.assign({}, base[key]);
  });
  counters.legacyCohorts = legacy.slice(-5);
  counters.cohort = Object.assign({}, expected, { startedAt: isoNow() });
}

function normalizeCounters(input) {
  const base = createDefaultCounters();
  const counters = input && typeof input === "object" ? input : {};
  normalizeCounterCohort(counters, base);
  COUNTER_METRIC_KEYS.forEach((key) => {
    if (!counters[key] || typeof counters[key] !== "object") {
      counters[key] = Object.assign({}, base[key]);
    }
    const prevTruthSource = String(counters[key].truthSource || "");
    const prevDayScope = counters[key].dayScope;
    const prevDayResetHour = counters[key].dayResetHour;
    counters[key].label = base[key].label || counters[key].label || key;
    counters[key].total = Number(counters[key].total || 0);
    counters[key].match = Number(counters[key].match || 0);
    if (["wan", "qian", "bai", "shi"].includes(key)) {
      counters[key].mainMatch = Number(counters[key].mainMatch || 0);
    }
    const rate = calcRateNumber(counters[key].total, counters[key].match);
    counters[key].rate = rate;
    decorateTruthScoreFields(counters[key], key, rate);
    // 按天账本字段不可被 normalize 冲掉，否则面板会从「今日」跳回累计百分比
    if (prevTruthSource.indexOf("daily") >= 0 || prevDayScope) {
      counters[key].truthSource = prevTruthSource.indexOf("daily") >= 0
        ? prevTruthSource
        : "official-counter-truth-eligible-only-daily";
      if (prevDayScope) counters[key].dayScope = prevDayScope;
      if (prevDayResetHour != null) counters[key].dayResetHour = prevDayResetHour;
    } else {
      counters[key].truthSource = "official-counter-truth-eligible-only";
    }
    if (counters[key].predictivePower === false) {
      counters[key].low = counters[key].total >= MODEL_MIN_SAMPLE && rate !== null && rate < (Number(counters[key].randomBaseline || 0) - 3);
    } else {
      counters[key].low = counters[key].total >= MODEL_MIN_SAMPLE && rate !== null && rate < modelLowRateForKey(key);
    }
  });
  counters.version = 10;
  counters.updatedAt = counters.updatedAt || "";
  return counters;
}

function buildRateDetailsFromCounters(counters) {
  const c = normalizeCounters(counters);
  const details = {};
  Object.keys(c).forEach((key) => {
    if (["version", "updatedAt", "cohort", "legacyCohorts"].includes(key)) return;
    if (!c[key] || typeof c[key] !== "object") return;
    details[key] = buildCounterTruthRecord(c[key], key);
  });
  return details;
}

function createDefaultModelWatchdog() {
  return {
    enabled: true,
    intervalMs: MODEL_WATCHDOG_INTERVAL,
    lowRate: MODEL_WATCHDOG_LOW_RATE,
    minSample: MODEL_WATCHDOG_MIN_SAMPLE,
    blockFloor: MODEL_WATCHDOG_BLOCK_FLOOR,
    memoryLimit: MODEL_WATCHDOG_MEMORY_LIMIT,
    repairCooldownMs: MODEL_WATCHDOG_REPAIR_COOLDOWN,
    totalChecks: 0,
    totalRepairs: 0,
    lastCheckAt: "",
    lastRepairAt: "",
    lastQualityRepairBaseUid: "",
    lastQualityRepairAt: "",
    // Keep quality repairs idempotent across draw periods. A persistent low
    // score is an observation until it clears; re-clearing the same lock each
    // period only creates write amplification and prediction churn.
    qualityRepairKeys: {},
    lastTrigger: "",
    lastIssues: [],
    lastActions: []
  };
}

function normalizeModelWatchdog(input) {
  const base = createDefaultModelWatchdog();
  const src = input && typeof input === "object" ? input : {};
  const merged = Object.assign(base, src);
  merged.enabled = merged.enabled !== false;
  merged.intervalMs = Number(merged.intervalMs || MODEL_WATCHDOG_INTERVAL);
  merged.lowRate = Number(merged.lowRate || MODEL_WATCHDOG_LOW_RATE);
  merged.minSample = Number(merged.minSample || MODEL_WATCHDOG_MIN_SAMPLE);
  merged.blockFloor = Number(merged.blockFloor || MODEL_WATCHDOG_BLOCK_FLOOR);
  merged.memoryLimit = Number(merged.memoryLimit || MODEL_WATCHDOG_MEMORY_LIMIT);
  merged.repairCooldownMs = Number(merged.repairCooldownMs || MODEL_WATCHDOG_REPAIR_COOLDOWN);
  merged.totalChecks = Number(merged.totalChecks || 0);
  merged.totalRepairs = Number(merged.totalRepairs || 0);
  merged.lastQualityRepairBaseUid = String(merged.lastQualityRepairBaseUid || "");
  merged.lastQualityRepairAt = String(merged.lastQualityRepairAt || "");
  merged.qualityRepairKeys = merged.qualityRepairKeys && typeof merged.qualityRepairKeys === "object"
    ? Object.keys(merged.qualityRepairKeys).reduce((out, key) => {
      const value = merged.qualityRepairKeys[key];
      if (key && value && typeof value === "object") {
        out[String(key)] = {
          baseUid: String(value.baseUid || ""),
          at: String(value.at || "")
        };
      }
      return out;
    }, {})
    : {};
  merged.lastIssues = Array.isArray(merged.lastIssues) ? merged.lastIssues.slice(0, 80) : [];
  merged.lastActions = Array.isArray(merged.lastActions) ? merged.lastActions.slice(0, 80) : [];
  return merged;
}

function createDefaultStormMode() {
  return {
    active: false,
    reason: "",
    enteredAt: "",
    exitedAt: "",
    lastChangedAt: "",
    day: "",
    autoTop6CurrentMiss: 0,
    autoTop6CurrentWin: 0,
    threeDuishuCurrentMiss: 0,
    threeDuishuCurrentWin: 0,
    enterAutoTop6Miss: STORM_ENTER_AUTO_TOP6_MISS,
    enterThreeDuishuMiss: STORM_ENTER_THREE_DUISHU_MISS,
    exitWin: STORM_EXIT_WIN,
    exitStableTicks: STORM_EXIT_STABLE_TICKS,
    stableClearTicks: 0,
    lockFreeze: false,
    robotsPaused: false,
    version: "storm-v1"
  };
}

function normalizeStormMode(input) {
  const base = createDefaultStormMode();
  const src = input && typeof input === "object" ? input : {};
  const merged = Object.assign(base, src);
  merged.active = !!merged.active;
  merged.reason = String(merged.reason || "");
  merged.enteredAt = String(merged.enteredAt || "");
  merged.exitedAt = String(merged.exitedAt || "");
  merged.lastChangedAt = String(merged.lastChangedAt || "");
  merged.day = String(merged.day || "");
  merged.autoTop6CurrentMiss = Number(merged.autoTop6CurrentMiss || 0);
  merged.autoTop6CurrentWin = Number(merged.autoTop6CurrentWin || 0);
  merged.threeDuishuCurrentMiss = Number(merged.threeDuishuCurrentMiss || 0);
  merged.threeDuishuCurrentWin = Number(merged.threeDuishuCurrentWin || 0);
  merged.enterAutoTop6Miss = Number(merged.enterAutoTop6Miss || STORM_ENTER_AUTO_TOP6_MISS);
  merged.enterThreeDuishuMiss = Number(merged.enterThreeDuishuMiss || STORM_ENTER_THREE_DUISHU_MISS);
  merged.exitWin = Number(merged.exitWin || STORM_EXIT_WIN);
  merged.exitStableTicks = Number(merged.exitStableTicks || STORM_EXIT_STABLE_TICKS);
  merged.stableClearTicks = Number(merged.stableClearTicks || 0);
  merged.lockFreeze = merged.active ? true : !!merged.lockFreeze;
  merged.robotsPaused = merged.active ? true : !!merged.robotsPaused;
  merged.version = "storm-v1";
  return merged;
}

function ledgerDayStats(storeName, day) {
  const ledger = normalizeDailyStreakLedger(state && state.dailyStreakLedger);
  const group = ledger && ledger[storeName] && typeof ledger[storeName] === "object" ? ledger[storeName] : {};
  const stats = group && group[day] && typeof group[day] === "object" ? group[day] : {};
  return {
    currentMiss: Number(stats.currentMiss || 0),
    currentWin: Number(stats.currentWin || 0),
    maxMiss: Number(stats.maxMiss || 0),
    maxWin: Number(stats.maxWin || 0),
    total: Number(stats.total || 0),
    hit: Number(stats.hit || 0)
  };
}

function writeStormModeFlagFile(storm) {
  try {
    ensureDir(DATA_DIR);
    const payload = {
      active: !!storm.active,
      reason: storm.reason || "",
      enteredAt: storm.enteredAt || "",
      exitedAt: storm.exitedAt || "",
      lastChangedAt: storm.lastChangedAt || "",
      day: storm.day || "",
      autoTop6CurrentMiss: Number(storm.autoTop6CurrentMiss || 0),
      threeDuishuCurrentMiss: Number(storm.threeDuishuCurrentMiss || 0),
      robotsPaused: !!storm.robotsPaused,
      lockFreeze: !!storm.lockFreeze,
      updatedAt: isoNow()
    };
    fs.writeFileSync(STORM_MODE_FILE, safeJsonStringify(payload));
  } catch (err) {
    addLog("风暴模式旗标写入失败", { message: err && err.message ? err.message : String(err) });
  }
}

function evaluateAndApplyStormMode() {
  if (!state) return createDefaultStormMode();
  refreshBackendDailyStreaksIfNeeded();
  const day = cleanDayKey(state.latestRecord && (state.latestRecord.opentime || state.latestRecord.period)) || currentLocalDayKey();
  const autoTop6 = ledgerDayStats("autoTop6", day);
  const threeDuishu = ledgerDayStats("threeDuishu", day);
  const prev = normalizeStormMode(state.stormMode);
  const next = normalizeStormMode(prev);
  next.day = day;
  next.autoTop6CurrentMiss = autoTop6.currentMiss;
  next.autoTop6CurrentWin = autoTop6.currentWin;
  next.threeDuishuCurrentMiss = threeDuishu.currentMiss;
  next.threeDuishuCurrentWin = threeDuishu.currentWin;
  next.enterAutoTop6Miss = STORM_ENTER_AUTO_TOP6_MISS;
  next.enterThreeDuishuMiss = STORM_ENTER_THREE_DUISHU_MISS;
  next.exitWin = STORM_EXIT_WIN;
  next.exitStableTicks = STORM_EXIT_STABLE_TICKS;

  const enterReasons = [];
  if (autoTop6.currentMiss >= STORM_ENTER_AUTO_TOP6_MISS) {
    enterReasons.push(`六项连败${autoTop6.currentMiss}>=${STORM_ENTER_AUTO_TOP6_MISS}`);
  }
  if (threeDuishu.currentMiss >= STORM_ENTER_THREE_DUISHU_MISS) {
    enterReasons.push(`三对数连败${threeDuishu.currentMiss}>=${STORM_ENTER_THREE_DUISHU_MISS}`);
  }
  const clearNow = autoTop6.currentMiss <= 0 && threeDuishu.currentMiss <= 0;
  const winRecover = autoTop6.currentWin >= STORM_EXIT_WIN && threeDuishu.currentWin >= STORM_EXIT_WIN;

  if (!next.active && enterReasons.length) {
    next.active = true;
    next.reason = enterReasons.join("；");
    next.enteredAt = isoNow();
    next.exitedAt = "";
    next.lastChangedAt = next.enteredAt;
    next.stableClearTicks = 0;
    next.lockFreeze = true;
    next.robotsPaused = true;
    addLog("进入风暴模式", {
      reason: next.reason,
      day,
      autoTop6,
      threeDuishu
    });
  } else if (next.active) {
    if (clearNow || winRecover) next.stableClearTicks += 1;
    else next.stableClearTicks = 0;
    if (next.stableClearTicks >= STORM_EXIT_STABLE_TICKS) {
      next.active = false;
      next.reason = clearNow
        ? `连败归零稳定${next.stableClearTicks}次`
        : `连中恢复(六项${autoTop6.currentWin}/三对数${threeDuishu.currentWin})`;
      next.exitedAt = isoNow();
      next.lastChangedAt = next.exitedAt;
      next.lockFreeze = false;
      next.robotsPaused = false;
      next.stableClearTicks = 0;
      addLog("退出风暴模式", {
        reason: next.reason,
        day,
        autoTop6,
        threeDuishu
      });
    } else {
      next.lockFreeze = true;
      next.robotsPaused = true;
      if (enterReasons.length) next.reason = enterReasons.join("；");
    }
  } else {
    next.lockFreeze = false;
    next.robotsPaused = false;
    next.stableClearTicks = 0;
  }

  state.stormMode = next;
  writeStormModeFlagFile(next);
  return next;
}

function cleanDayKey(value) {
  const text = String(value || "").trim();
  let match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (match) return `${match[1]}-${match[2]}-${match[3]}`;
  match = text.match(/^(\d{4})(\d{2})(\d{2})/);
  if (match) return `${match[1]}-${match[2]}-${match[3]}`;
  match = text.match(/(\d{4})[-\/](\d{1,2})[-\/](\d{1,2})/);
  if (match) return `${match[1]}-${String(match[2]).padStart(2, "0")}-${String(match[3]).padStart(2, "0")}`;
  return "";
}

const PENTA5_DAY_RESET_HOUR = Math.max(0, Math.min(23, Number(process.env.PENTA5_DAY_RESET_HOUR || 5)));

function parseLocalDateTimeValue(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  let match = text.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (match) {
    return new Date(
      Number(match[1]),
      Number(match[2]) - 1,
      Number(match[3]),
      Number(match[4]),
      Number(match[5]),
      Number(match[6] || 0),
      0
    );
  }
  match = text.match(/^(\d{4})(\d{2})(\d{2})(\d{3})$/);
  if (match) {
    const seq = Number(match[4]);
    if (Number.isInteger(seq) && seq >= 1 && seq <= PENTA5_PERIODS_PER_DAY) {
      const minutes = (seq - 1) * 5;
      return new Date(
        Number(match[1]),
        Number(match[2]) - 1,
        Number(match[3]),
        Math.floor(minutes / 60),
        minutes % 60,
        DRAW_SECOND,
        0
      );
    }
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12, 0, 0, 0);
  }
  const parsed = new Date(text);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
}

function businessDayKey(value) {
  const dt = parseLocalDateTimeValue(value);
  if (dt && Number.isFinite(dt.getTime())) {
    const shifted = new Date(dt.getTime() - PENTA5_DAY_RESET_HOUR * 60 * 60 * 1000);
    const y = shifted.getFullYear();
    const m = String(shifted.getMonth() + 1).padStart(2, "0");
    const d = String(shifted.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }
  return cleanDayKey(value);
}

function currentBusinessDayKey() {
  return businessDayKey(new Date());
}

function currentLocalDayKey() {
  return currentBusinessDayKey();
}

function predictionBusinessDayKey(prediction) {
  const ev = prediction && prediction.evaluation ? prediction.evaluation : {};
  const candidates = [
    ev.targetOpenTime,
    ev.evaluatedAtText,
    ev.evaluatedAt,
    ev.targetPeriod,
    prediction && prediction.baseOpenTime,
    prediction && prediction.basePeriod
  ];
  for (let i = 0; i < candidates.length; i += 1) {
    const key = businessDayKey(candidates[i]);
    if (key) return key;
  }
  return "";
}

function filterDedupedEvaluatedPredictions(predictions, options) {
  const opts = options && typeof options === "object" ? options : {};
  const businessDay = opts.businessDay ? cleanDayKey(opts.businessDay) : "";
  const limit = Number.isFinite(Number(opts.limit)) ? Math.max(0, Number(opts.limit)) : null;
  const seenTargets = new Set();
  const rows = [];
  (Array.isArray(predictions) ? predictions : []).forEach((prediction) => {
    if (!prediction || !prediction.evaluated || !prediction.evaluation) return;
    if (opts.requireModelVersion && prediction.modelVersion !== MODEL_VERSION) return;
    if (businessDay && predictionBusinessDayKey(prediction) !== businessDay) return;
    const ev = prediction.evaluation || {};
    const target = cleanPeriodKey(ev.targetUid || ev.targetPeriod || prediction.baseUid || prediction.basePeriod);
    if (target) {
      if (seenTargets.has(target)) return;
      seenTargets.add(target);
    }
    rows.push(prediction);
  });
  return limit === null ? rows : rows.slice(0, limit);
}

function estimateBusinessDayElapsedPeriods(now) {
  const dt = now instanceof Date ? now : new Date();
  if (!Number.isFinite(dt.getTime())) return 0;
  const shifted = new Date(dt.getTime() - PENTA5_DAY_RESET_HOUR * 60 * 60 * 1000);
  const start = new Date(shifted.getFullYear(), shifted.getMonth(), shifted.getDate(), PENTA5_DAY_RESET_HOUR, 0, 0, 0);
  const elapsedMs = Math.max(0, dt.getTime() - start.getTime());
  // 五星约 5 分钟一期
  return Math.floor(elapsedMs / (5 * 60 * 1000));
}

function memoryCanRebuildDailyCounters(predictions, day) {
  const cleanDay = cleanDayKey(day) || currentBusinessDayKey();
  const n = filterDedupedEvaluatedPredictions(predictions, { businessDay: cleanDay }).length;
  const elapsed = estimateBusinessDayElapsedPeriods(new Date());
  // 业务日刚开始：样本少是正常的，允许按天覆盖
  if (elapsed <= 12) return true;
  // 日中/日末若内存评价条数远少于已过期数，说明 tip-only 重启掏空了列表，禁止用几条样本假算成 100%
  const minNeeded = Math.min(40, Math.max(12, Math.floor(elapsed * 0.2)));
  return n >= minNeeded;
}

function summarizeDailyPositionCounters(predictions, day) {
  const cleanDay = cleanDayKey(day) || currentBusinessDayKey();
  const base = createDefaultCounters();
  const keys = ["positionTotal", "wan", "qian", "bai", "shi"];
  const tallies = {};
  keys.forEach((key) => {
    tallies[key] = {
      total: 0,
      match: 0,
      mainMatch: key === "positionTotal" ? undefined : 0
    };
  });
  filterDedupedEvaluatedPredictions(predictions, { businessDay: cleanDay }).forEach((prediction) => {
    const ev = prediction.evaluation || {};
    if (ev.truthEligible === false || ev.voided === true || prediction.counterSkipped) return;
    if (typeof ev.positionTotalSuccess === "boolean") {
      tallies.positionTotal.total += 1;
      if (ev.positionTotalSuccess) tallies.positionTotal.match += 1;
    }
    ["wan", "qian", "bai", "shi"].forEach((key) => {
      const item = ev.independentPosition && ev.independentPosition[key];
      if (!item || typeof item.success !== "boolean") return;
      tallies[key].total += 1;
      if (item.success) tallies[key].match += 1;
      if (item.mainSuccess) tallies[key].mainMatch += 1;
    });
  });
  const out = {};
  keys.forEach((key) => {
    out[key] = Object.assign({}, base[key], tallies[key]);
    const rate = calcRateNumber(out[key].total, out[key].match);
    out[key].rate = rate;
    decorateTruthScoreFields(out[key], key, rate);
    out[key].truthSource = "official-counter-truth-eligible-only-daily";
    out[key].dayScope = cleanDay;
    out[key].dayResetHour = PENTA5_DAY_RESET_HOUR;
  });
  return out;
}

function summarizeDailyThreeDuishuCounter(predictions, day) {
  const cleanDay = cleanDayKey(day) || currentBusinessDayKey();
  const base = createDefaultCounters();
  let total = 0;
  let match = 0;
  filterDedupedEvaluatedPredictions(predictions, { businessDay: cleanDay }).forEach((prediction) => {
    const ev = prediction.evaluation || {};
    const rec = ev.threeDuishu || {};
    if (typeof rec.success !== "boolean") return;
    if (rec.truthEligible !== true || rec.reconstructed === true) return;
    if (rec.version && rec.version !== THREE_DUISHU_SNAPSHOT_VERSION) return;
    total += 1;
    if (rec.success) match += 1;
  });
  const rate = calcRateNumber(total, match);
  const out = Object.assign({}, base.threeDuishu, { total, match, rate });
  decorateTruthScoreFields(out, "threeDuishu", rate);
  out.truthSource = "official-counter-truth-eligible-only-daily";
  out.dayScope = cleanDay;
  out.dayResetHour = PENTA5_DAY_RESET_HOUR;
  return out;
}

function summarizeDailyHeweiHitCounters(predictions, day) {
  const cleanDay = cleanDayKey(day) || currentBusinessDayKey();
  const base = createDefaultCounters();
  const keys = ["anyTwoHeweiHit", "anyThreeHeweiHit"];
  const tallies = {};
  keys.forEach((key) => {
    tallies[key] = { total: 0, match: 0 };
  });
  filterDedupedEvaluatedPredictions(predictions, { businessDay: cleanDay }).forEach((prediction) => {
    const ev = prediction.evaluation || {};
    if (ev.truthEligible === false || ev.voided === true || prediction.counterSkipped) return;
    const extras = ev.independentExtras || {};
    keys.forEach((key) => {
      const item = extras[key];
      if (!item || typeof item.success !== "boolean") return;
      tallies[key].total += 1;
      if (item.success) tallies[key].match += 1;
    });
  });
  const out = {};
  keys.forEach((key) => {
    out[key] = Object.assign({}, base[key], tallies[key]);
    const rate = calcRateNumber(out[key].total, out[key].match);
    out[key].rate = rate;
    decorateTruthScoreFields(out[key], key, rate);
    out[key].truthSource = "official-counter-truth-eligible-only-daily";
    out[key].dayScope = cleanDay;
    out[key].dayResetHour = PENTA5_DAY_RESET_HOUR;
  });
  return out;
}

function resolveSealedThreeDuishuBackupPair(rec) {
  const source = rec && typeof rec === "object" ? rec : {};
  const main = String(source.selectedPair || source.selectedPairCode || "").replace(/\D/g, "").slice(0, 2);
  const pairSelection = source.pairSelection && typeof source.pairSelection === "object" ? source.pairSelection : {};
  const scored = Array.isArray(pairSelection.candidates) ? pairSelection.candidates : [];
  for (let i = 0; i < scored.length; i += 1) {
    const cand = String(scored[i] && scored[i].pair || "").replace(/\D/g, "").slice(0, 2);
    if (/^\d{2}$/.test(cand) && cand !== main) return cand;
  }
  const pairs = Array.isArray(source.pairs) ? source.pairs : [];
  for (let j = 0; j < pairs.length; j += 1) {
    const alt = String(pairs[j] || "").replace(/\D/g, "").slice(0, 2);
    if (/^\d{2}$/.test(alt) && alt !== main) return alt;
  }
  return "";
}

function parsePenta5Period(value) {
  const text = String(value === undefined || value === null ? "" : value).trim();
  const match = text.match(/^(\d{4})(\d{2})(\d{2})(\d{3})$/);
  if (!match) {
    return { ok: false, value: text, reason: "unsupported-period-format" };
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const sequence = Number(match[4]);
  const dateMs = Date.UTC(year, month - 1, day);
  const date = new Date(dateMs);
  if (
    !Number.isFinite(dateMs) ||
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return { ok: false, value: text, reason: "invalid-period-date" };
  }
  if (!Number.isInteger(sequence) || sequence < 1 || sequence > PENTA5_PERIODS_PER_DAY) {
    return {
      ok: false,
      value: text,
      reason: "invalid-period-sequence",
      sequence,
      periodsPerDay: PENTA5_PERIODS_PER_DAY
    };
  }
  return {
    ok: true,
    value: text,
    dateKey: `${match[1]}${match[2]}${match[3]}`,
    dateMs,
    sequence,
    sequenceText: match[4],
    periodsPerDay: PENTA5_PERIODS_PER_DAY
  };
}

function formatPenta5Period(dateMs, sequence) {
  const date = new Date(dateMs);
  const year = String(date.getUTCFullYear()).padStart(4, "0");
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  return `${year}${month}${day}${String(sequence).padStart(3, "0")}`;
}

function expectedNextPenta5Period(basePeriod) {
  const base = parsePenta5Period(basePeriod);
  if (!base.ok) {
    return { ok: false, expectedPeriod: "", reason: base.reason, base };
  }
  if (base.sequence < PENTA5_PERIODS_PER_DAY) {
    return {
      ok: true,
      expectedPeriod: formatPenta5Period(base.dateMs, base.sequence + 1),
      rule: "same-day-next-sequence",
      base
    };
  }
  return {
    ok: true,
    expectedPeriod: formatPenta5Period(base.dateMs + 24 * 60 * 60 * 1000, 1),
    rule: "configured-day-rollover",
    base
  };
}

function immediatePenta5PeriodTransition(basePeriod, targetPeriod) {
  const expected = expectedNextPenta5Period(basePeriod);
  const target = parsePenta5Period(targetPeriod);
  if (!expected.ok || !target.ok) {
    return {
      immediate: null,
      reason: !expected.ok ? expected.reason : target.reason,
      basePeriod: String(basePeriod || ""),
      targetPeriod: String(targetPeriod || ""),
      expectedPeriod: expected.expectedPeriod || "",
      periodsPerDay: PENTA5_PERIODS_PER_DAY
    };
  }
  const immediate = target.value === expected.expectedPeriod;
  return {
    immediate,
    reason: immediate ? expected.rule : "non-adjacent-period",
    basePeriod: expected.base.value,
    targetPeriod: target.value,
    expectedPeriod: expected.expectedPeriod,
    baseSequence: expected.base.sequence,
    targetSequence: target.sequence,
    periodsPerDay: PENTA5_PERIODS_PER_DAY
  };
}

function periodMatchesDay(day, period) {
  const cleanDay = cleanDayKey(day);
  if (!cleanDay) return false;
  return cleanDayKey(period) === cleanDay;
}

function normalizeStreakStats(input) {
  const src = input && typeof input === "object" ? input : {};
  const total = Math.max(0, Number(src.total || 0));
  const hit = Math.max(0, Number(src.hit || 0));
  const out = {
    day: cleanDayKey(src.day) || "",
    total,
    hit,
    rate: total ? Number(((hit / total) * 100).toFixed(2)) : (src.rate !== undefined && src.rate !== null && isFinite(Number(src.rate)) ? Number(src.rate) : null),
    currentWin: Math.max(0, Number(src.currentWin || 0)),
    currentMiss: Math.max(0, Number(src.currentMiss || 0)),
    maxWin: Math.max(0, Number(src.maxWin || 0)),
    maxMiss: Math.max(0, Number(src.maxMiss || 0)),
    lastPeriod: src.lastPeriod === undefined || src.lastPeriod === null ? "" : String(src.lastPeriod),
    updatedAt: src.updatedAt || "",
    source: src.source === undefined || src.source === null ? "" : String(src.source)
  };
  return out;
}

function hasCoherentStreakHitStats(stats) {
  const total = Math.max(0, Number(stats && stats.total || 0));
  const hit = Math.max(0, Number(stats && stats.hit || 0));
  const maxWin = Math.max(0, Number(stats && stats.maxWin || 0));
  const maxMiss = Math.max(0, Number(stats && stats.maxMiss || 0));
  if (hit > total) return false;
  if (maxWin > hit) return false;
  if (maxMiss > total - hit) return false;
  return true;
}

function normalizeDailyStreakLedger(input) {
  const src = input && typeof input === "object" ? input : {};
  const out = {};
  Object.keys(src).forEach((storeName) => {
    if (!storeName || typeof src[storeName] !== "object") return;
    out[storeName] = {};
    Object.keys(src[storeName]).forEach((day) => {
      const cleanDay = cleanDayKey(day);
      if (!cleanDay) return;
      const stats = normalizeStreakStats(Object.assign({}, src[storeName][day] || {}, { day: cleanDay }));
      out[storeName][cleanDay] = stats;
    });
  });
  return out;
}

function mergeDailyStreakStats(storeName, day, stats, source) {
  const name = String(storeName || "").replace(/[^\w:-]/g, "").slice(0, 80);
  const cleanDay = cleanDayKey(day || (stats && stats.day));
  if (!name || !cleanDay) return null;
  state.dailyStreakLedger = normalizeDailyStreakLedger(state.dailyStreakLedger);
  if (!state.dailyStreakLedger[name]) state.dailyStreakLedger[name] = {};
  const incoming = normalizeStreakStats(Object.assign({}, stats || {}, { day: cleanDay }));
  const existing = normalizeStreakStats(state.dailyStreakLedger[name][cleanDay] || { day: cleanDay });
  if (incoming.lastPeriod && !periodMatchesDay(cleanDay, incoming.lastPeriod)) incoming.lastPeriod = "";
  if (existing.lastPeriod && !periodMatchesDay(cleanDay, existing.lastPeriod)) existing.lastPeriod = "";
  const incomingCoherent = hasCoherentStreakHitStats(incoming);
  const existingCoherent = hasCoherentStreakHitStats(existing);
  const incomingSource = String(source || "panel").slice(0, 60);
  const isPanelSource = /^panel/i.test(incomingSource);
  const existingSource = String(existing.source || "");
  const existingIsBackend = existingSource.indexOf("backend-") === 0 || existingSource === "backend-evaluation";
  const incomingSnapshot = incomingSource.indexOf("backend-auto-top6-snapshot") === 0 || incomingSource.indexOf("backend-recommend2-snapshot") === 0 || incomingSource.indexOf("backend-three-ding-framework") === 0 || incomingSource.indexOf("backend-three-ding-two") === 0 || incomingSource.indexOf("backend-three-ding-one") === 0;
  const existingSnapshot = existingSource.indexOf("backend-auto-top6-snapshot") === 0 || existingSource.indexOf("backend-recommend2-snapshot") === 0 || existingSource.indexOf("backend-three-ding-framework") === 0 || existingSource.indexOf("backend-three-ding-two") === 0 || existingSource.indexOf("backend-three-ding-one") === 0;
  const threeDingStore = name === "compactAttack" || name === "threeDingTwo" || name === "threeDingOne";
  const snapshotVersionChanged = (name === "autoTop6" || name === "recommend2" || threeDingStore) && incomingSnapshot && existingSnapshot && incomingSource !== existingSource;
  const replaceAutoTop6Snapshot = (name === "autoTop6" || name === "recommend2" || threeDingStore) && incomingSnapshot && (snapshotVersionChanged || !existingSnapshot || incoming.total >= existing.total || existing.total === 0);
  // A backend rebuild is calculated from sealed predictions for this exact
  // day. It must be able to replace a previously over-counted day ledger.
  const replaceBackendDailyRebuild = !isPanelSource && incomingCoherent && incoming.total > 0 && incoming.lastPeriod &&
    periodMatchesDay(cleanDay, incoming.lastPeriod) &&
    (!existing.lastPeriod || String(incoming.lastPeriod) >= String(existing.lastPeriod));
  let canUseIncomingTotals = replaceAutoTop6Snapshot || replaceBackendDailyRebuild || (incomingCoherent && (incoming.total >= existing.total || !existingCoherent));
  let useIncomingCurrent = replaceAutoTop6Snapshot || replaceBackendDailyRebuild || (incoming.lastPeriod &&
    incomingCoherent &&
    (!existing.lastPeriod || String(incoming.lastPeriod) >= String(existing.lastPeriod)));
  // 面板回写不能覆盖后端账本，也不能抬高今日峰值
  if (isPanelSource && existingIsBackend && existing.total > 0 && !replaceAutoTop6Snapshot) {
    canUseIncomingTotals = false;
    useIncomingCurrent = false;
  }
  const merged = {
    day: cleanDay,
    total: canUseIncomingTotals ? incoming.total : existing.total,
    hit: canUseIncomingTotals ? incoming.hit : existing.hit,
    rate: canUseIncomingTotals ? incoming.rate : existing.rate,
    currentWin: useIncomingCurrent ? incoming.currentWin : existing.currentWin,
    currentMiss: useIncomingCurrent ? incoming.currentMiss : existing.currentMiss,
    maxWin: isPanelSource ? existing.maxWin : (replaceBackendDailyRebuild ? incoming.maxWin : Math.max(existing.maxWin, incoming.maxWin)),
    maxMiss: isPanelSource ? existing.maxMiss : (replaceBackendDailyRebuild ? incoming.maxMiss : Math.max(existing.maxMiss, incoming.maxMiss)),
    lastPeriod: useIncomingCurrent ? incoming.lastPeriod : existing.lastPeriod,
    updatedAt: isoNow(),
    source: (isPanelSource && existingIsBackend && existingSource) ? existingSource : incomingSource
  };
  if (!merged.lastPeriod && (merged.currentWin || merged.currentMiss) && state.latestRecord && periodMatchesDay(cleanDay, state.latestRecord.period)) {
    merged.lastPeriod = String(state.latestRecord.period || "");
  }
  if (merged.total && merged.hit && (merged.rate === null || merged.rate === undefined)) {
    merged.rate = Number(((merged.hit / merged.total) * 100).toFixed(2));
  }
  state.dailyStreakLedger[name][cleanDay] = merged;
  return merged;
}

function predictionDateKey(prediction) {
  const ev = prediction && prediction.evaluation ? prediction.evaluation : {};
  return cleanDayKey(ev.targetOpenTime) ||
    cleanDayKey(ev.evaluatedAtText) ||
    cleanDayKey(ev.evaluatedAt) ||
    cleanDayKey(ev.targetPeriod) ||
    cleanDayKey(prediction && prediction.baseOpenTime) ||
    cleanDayKey(prediction && prediction.basePeriod);
}

function streakStatsFromResults(day, rows) {
  let total = 0;
  let hit = 0;
  let currentWin = 0;
  let currentMiss = 0;
  let maxWin = 0;
  let maxMiss = 0;
  let lastResult = null;
  let lastPeriod = "";
  const seenPeriods = new Set();
  (Array.isArray(rows) ? rows : []).filter((row) => {
    if (!row || typeof row.success !== "boolean") return false;
    const period = cleanPeriodKey(row.period || row.uid);
    if (!period) return true;
    if (seenPeriods.has(period)) return false;
    seenPeriods.add(period);
    return true;
  }).forEach((row) => {
    if (!row || typeof row.success !== "boolean") return;
    total += 1;
    if (row.success) hit += 1;
    lastResult = row.success;
    lastPeriod = String(row.period || lastPeriod || "");
    if (row.success) {
      currentWin += 1;
      currentMiss = 0;
      if (currentWin > maxWin) maxWin = currentWin;
    } else {
      currentMiss += 1;
      currentWin = 0;
      if (currentMiss > maxMiss) maxMiss = currentMiss;
    }
  });
  return {
    day,
    total,
    hit,
    rate: total ? Number(((hit / total) * 100).toFixed(2)) : null,
    currentWin: lastResult === true ? currentWin : 0,
    currentMiss: lastResult === false ? currentMiss : 0,
    maxWin,
    maxMiss,
    lastPeriod
  };
}

function refreshBackendDailyStreaks() {
  if (!state || !Array.isArray(state.predictions)) return;
  const latest = state.latestRecord || {};
  const day = cleanDayKey(latest.opentime) || cleanDayKey(latest.period);
  if (!day) return;
  const autoTop6Rows = state.predictions
    .filter((prediction) => prediction && prediction.evaluated && prediction.evaluation && predictionDateKey(prediction) === day)
    .map((prediction) => {
      const ev = prediction.evaluation || {};
      const top6 = ev.autoTop6 || {};
      if (typeof top6.success !== "boolean") return null;
      if (top6.truthEligible !== true) return null;
      if (top6.reconstructed === true) return null;
      if (top6.version !== AUTO_TOP6_SNAPSHOT_VERSION) return null;
      if (!(Number(top6.total || 0) > 0)) return null;
      return {
        period: ev.targetPeriod || prediction.basePeriod || "",
        success: top6.success
      };
    })
    .filter(Boolean)
    .sort((a, b) => String(a.period).localeCompare(String(b.period)));
  if (autoTop6Rows.length || (state.currentPrediction && state.currentPrediction.autoTop6)) {
    mergeDailyStreakStats("autoTop6", day, streakStatsFromResults(day, autoTop6Rows), AUTO_TOP6_SNAPSHOT_VERSION);
  }
  const recommend2Rows = state.predictions
    .filter((prediction) => prediction && prediction.evaluated && prediction.evaluation && predictionDateKey(prediction) === day)
    .map((prediction) => {
      const ev = prediction.evaluation || {};
      const recommend2 = ev.recommend2 || {};
      if (typeof recommend2.success !== "boolean") return null;
      if (recommend2.truthEligible !== true) return null;
      if (recommend2.reconstructed === true) return null;
      if (recommend2.version && recommend2.version !== RECOMMEND2_SNAPSHOT_VERSION) return null;
      if (Number(recommend2.total || 0) !== 8) return null;
      return {
        period: ev.targetPeriod || prediction.basePeriod || "",
        success: recommend2.success
      };
    })
    .filter(Boolean)
    .sort((a, b) => String(a.period).localeCompare(String(b.period)));
  if (recommend2Rows.length || (state.currentPrediction && state.currentPrediction.recommend2)) {
    mergeDailyStreakStats("recommend2", day, streakStatsFromResults(day, recommend2Rows), RECOMMEND2_SNAPSHOT_VERSION);
  }
  const killFrameworkRows = state.predictions
    .filter((prediction) => prediction && prediction.evaluated && prediction.evaluation && predictionDateKey(prediction) === day)
    .map((prediction) => {
      const ev = prediction.evaluation || {};
      const killFramework = ev.killFramework || {};
      if (typeof killFramework.success !== "boolean") return null;
      if (killFramework.truthEligible !== true) return null;
      if (killFramework.reconstructed === true) return null;
      if (killFramework.version && killFramework.version !== KILL_FRAMEWORK_SNAPSHOT_VERSION) return null;
      if (Number(killFramework.total || 0) !== KILL_FRAMEWORK_ITEM_COUNT) return null;
      return {
        period: ev.targetPeriod || prediction.basePeriod || "",
        success: killFramework.success
      };
    })
    .filter(Boolean)
    .sort((a, b) => String(a.period).localeCompare(String(b.period)));
  if (killFrameworkRows.length || (state.currentPrediction && state.currentPrediction.killFramework)) {
    mergeDailyStreakStats("killFramework", day, streakStatsFromResults(day, killFrameworkRows), KILL_FRAMEWORK_SNAPSHOT_VERSION);
  }
  const fourDingFrameworkRows = state.predictions
    .filter((prediction) => prediction && prediction.evaluated && prediction.evaluation && predictionDateKey(prediction) === day)
    .map((prediction) => {
      const ev = prediction.evaluation || {};
      const fourDingFramework = ev.fourDingFramework || {};
      if (typeof fourDingFramework.success !== "boolean") return null;
      if (fourDingFramework.truthEligible !== true) return null;
      if (fourDingFramework.reconstructed === true) return null;
      if (fourDingFramework.version && fourDingFramework.version !== FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION) return null;
      if (Number(fourDingFramework.total || 0) !== FOUR_DING_FRAMEWORK_ITEM_COUNT) return null;
      return {
        period: ev.targetPeriod || prediction.basePeriod || "",
        success: fourDingFramework.success
      };
    })
    .filter(Boolean)
    .sort((a, b) => String(a.period).localeCompare(String(b.period)));
  if (fourDingFrameworkRows.length || (state.currentPrediction && state.currentPrediction.fourDingFramework)) {
    mergeDailyStreakStats("fourDingFramework", day, streakStatsFromResults(day, fourDingFrameworkRows), FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION);
  }
  const compactAttackRows = state.predictions
    .filter((prediction) => prediction && prediction.evaluated && prediction.evaluation && predictionDateKey(prediction) === day)
    .map((prediction) => {
      const ev = prediction.evaluation || {};
      const compactAttack = ev.compactAttack || {};
      if (typeof compactAttack.success !== "boolean") return null;
      if (compactAttack.truthEligible !== true) return null;
      if (compactAttack.reconstructed === true) return null;
      if (compactAttack.version && compactAttack.version !== COMPACT_ATTACK_SNAPSHOT_VERSION) return null;
      if (Number(compactAttack.total || 0) !== COMPACT_ATTACK_ITEM_COUNT) return null;
      return {
        period: ev.targetPeriod || prediction.basePeriod || "",
        success: compactAttack.success
      };
    })
    .filter(Boolean)
    .sort((a, b) => String(a.period).localeCompare(String(b.period)));
  if (compactAttackRows.length || (state.currentPrediction && state.currentPrediction.compactAttack)) {
    mergeDailyStreakStats("compactAttack", day, streakStatsFromResults(day, compactAttackRows), COMPACT_ATTACK_SNAPSHOT_VERSION);
  }
  const threeDingTwoRows = state.predictions
    .filter((item) => item && item.evaluated && item.evaluation && predictionDateKey(item) === day)
    .map((item) => {
      const ev = item && item.evaluation || {};
      const rec = ev.threeDingTwo || {};
      if (typeof rec.success !== "boolean") return null;
      if (rec.truthEligible !== true) return null;
      if (rec.reconstructed === true) return null;
      if (rec.version && rec.version !== THREE_DING_TWO_SNAPSHOT_VERSION) return null;
      if (Number(rec.total || 0) !== THREE_DING_TWO_ITEM_COUNT) return null;
      return {
        period: ev.targetPeriod || ev.targetUid || item.basePeriod || "",
        success: rec.success
      };
    })
    .filter(Boolean)
    .sort((a, b) => String(a.period).localeCompare(String(b.period)));
  if (threeDingTwoRows.length || (state.currentPrediction && state.currentPrediction.threeDingTwo)) {
    mergeDailyStreakStats("threeDingTwo", day, streakStatsFromResults(day, threeDingTwoRows), THREE_DING_TWO_SNAPSHOT_VERSION);
  }
  const threeDingOneRows = state.predictions
    .filter((item) => item && item.evaluated && item.evaluation && predictionDateKey(item) === day)
    .map((item) => {
      const ev = item && item.evaluation || {};
      const rec = ev.threeDingOne || {};
      if (typeof rec.success !== "boolean") return null;
      if (rec.truthEligible !== true) return null;
      if (rec.reconstructed === true) return null;
      if (rec.version && rec.version !== THREE_DING_ONE_SNAPSHOT_VERSION) return null;
      if (Number(rec.total || 0) !== THREE_DING_ONE_ITEM_COUNT) return null;
      return {
        period: ev.targetPeriod || ev.targetUid || item.basePeriod || "",
        success: rec.success
      };
    })
    .filter(Boolean)
    .sort((a, b) => String(a.period).localeCompare(String(b.period)));
  if (threeDingOneRows.length || (state.currentPrediction && state.currentPrediction.threeDingOne)) {
    mergeDailyStreakStats("threeDingOne", day, streakStatsFromResults(day, threeDingOneRows), THREE_DING_ONE_SNAPSHOT_VERSION);
  }
  const threePairRows = state.predictions
    .filter((prediction) => prediction && prediction.evaluated && prediction.evaluation && predictionDateKey(prediction) === day)
    .map((prediction) => {
      const ev = prediction.evaluation || {};
      if (ev.voided === true || ev.truthEligible === false) return null;
      const extras = ev.independentExtras || {};
      const keys = ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"];
      const results = keys.map((key) => {
        const item = extras[key];
        return item && typeof item.success === "boolean" ? item.success : null;
      });
      if (results.some((value) => typeof value !== "boolean")) return null;
      return {
        period: ev.targetPeriod || prediction.basePeriod || "",
        success: results.every(Boolean)
      };
    })
    .filter(Boolean)
    .sort((a, b) => String(a.period).localeCompare(String(b.period)));
  if (threePairRows.length) {
    mergeDailyStreakStats("twoDigitAvoidAllThree", day, streakStatsFromResults(day, threePairRows), "backend-evaluation");
  }
  const threeDuishuRows = state.predictions
    .filter((prediction) => prediction && prediction.evaluated && prediction.evaluation && predictionDateKey(prediction) === day)
    .map((prediction) => {
      const ev = prediction.evaluation || {};
      const threeDuishu = ev.threeDuishu || {};
      if (typeof threeDuishu.success !== "boolean") return null;
      if (threeDuishu.truthEligible !== true) return null;
      if (threeDuishu.reconstructed === true) return null;
      if (threeDuishu.version && threeDuishu.version !== THREE_DUISHU_SNAPSHOT_VERSION) return null;
      return {
        period: ev.targetPeriod || prediction.basePeriod || "",
        success: threeDuishu.success
      };
    })
    .filter(Boolean)
    .sort((a, b) => String(a.period).localeCompare(String(b.period)));
  if (threeDuishuRows.length || (state.currentPrediction && state.currentPrediction.threeDuishu)) {
    mergeDailyStreakStats("threeDuishu", day, streakStatsFromResults(day, threeDuishuRows), THREE_DUISHU_SNAPSHOT_VERSION);
  }
}

let backendDailyStreakRefreshKey = "";

function backendDailyStreakStateKey() {
  const latest = state && state.latestRecord ? state.latestRecord : {};
  const current = state && state.currentPrediction ? state.currentPrediction : {};
  const currentEval = current && current.evaluation ? current.evaluation : {};
  const firstPrediction = state && Array.isArray(state.predictions) && state.predictions[0] ? state.predictions[0] : {};
  const firstEval = firstPrediction && firstPrediction.evaluation ? firstPrediction.evaluation : {};
  return [
    cleanDayKey(latest.opentime) || cleanDayKey(latest.period) || currentLocalDayKey(),
    latest.uid || "",
    latest.period || "",
    current.id || "",
    current.evaluated ? "1" : "0",
    currentEval.evaluatedAt || "",
    firstPrediction.id || "",
    firstPrediction.evaluated ? "1" : "0",
    firstEval.evaluatedAt || "",
    Array.isArray(state && state.predictions) ? state.predictions.length : 0
  ].join("|");
}

function refreshBackendDailyStreaksIfNeeded() {
  const key = backendDailyStreakStateKey();
  if (backendDailyStreakRefreshKey === key) return;
  refreshBackendDailyStreaks();
  backendDailyStreakRefreshKey = key;
}

function addLog(message, detail) {
  if (!state) return;
  const item = {
    at: isoNow(),
    text: nowText(),
    message,
    detail: detail === undefined ? null : detail
  };
  state.logs = Array.isArray(state.logs) ? state.logs : [];
  state.logs.unshift(item);
  state.logs = state.logs.slice(0, 200);
  console.log(`[${item.text}] ${message}`, detail === undefined ? "" : detail);
}

function createDefaultState() {
  return {
    version: BACKEND_STATE_VERSION,
    createdAt: isoNow(),
    updatedAt: "",
    serverStartedAt: isoNow(),
    sourceApiUrl: SOURCE_API_URL,
    port: PORT,
    autoRefreshMs: AUTO_REFRESH_INTERVAL,
    requestTimeoutMs: REQUEST_TIMEOUT,
    latestRecord: null,
    currentPrediction: null,
    rawJson: null,
    lastSync: null,
    lastError: null,
    tipReady: false,
    structureReady: false,
    truthReady: false,
    qualityReady: false,
    operationalReady: false,
    duishuCaptureAlarm: null,
    history: [],
    predictions: [],
    counters: createDefaultCounters(),
    modelWatchdog: createDefaultModelWatchdog(),
    stormMode: createDefaultStormMode(),
    algorithmProjectHealth: normalizeAlgorithmProjectHealth({}),
    dailyStreakLedger: {},
    pendingOfficialDraws: [],
    rdsReadOnly: false,
    rdsLag: null,
    logs: []
  };
}

function loadState() {
  ensureDir(DATA_DIR);
  ensureDir(BACKUP_DIR);
  if (!fs.existsSync(STATE_FILE)) {
    const fresh = createDefaultState();
    fs.writeFileSync(STATE_FILE, safeJsonStringify(fresh));
    return fresh;
  }
  try {
    const raw = fs.readFileSync(STATE_FILE, "utf8");
    const parsed = JSON.parse(raw);
    const fresh = createDefaultState();
    const merged = Object.assign(fresh, parsed || {});
    merged.version = BACKEND_STATE_VERSION;
    merged.serverStartedAt = isoNow();
    merged.sourceApiUrl = SOURCE_API_URL;
    merged.port = PORT;
    merged.autoRefreshMs = AUTO_REFRESH_INTERVAL;
    merged.requestTimeoutMs = REQUEST_TIMEOUT;
    merged.history = [];
    merged.sharedHistorySource = merged.sharedHistorySource || null;
    merged.rawJson = null;
    merged.predictions = Array.isArray(merged.predictions) ? merged.predictions.slice(0, MAX_PREDICTION_LOG) : [];
    merged.counters = normalizeCounters(merged.counters);
    merged.modelWatchdog = normalizeModelWatchdog(merged.modelWatchdog);
    merged.stormMode = normalizeStormMode(merged.stormMode);
    merged.algorithmProjectHealth = normalizeAlgorithmProjectHealth(merged.algorithmProjectHealth);
    merged.dailyStreakLedger = normalizeDailyStreakLedger(merged.dailyStreakLedger);
    merged.pendingOfficialDraws = normalizePendingOfficialDraws(merged.pendingOfficialDraws);
    merged.rdsReadOnly = !!merged.rdsReadOnly;
    merged.logs = Array.isArray(merged.logs) ? merged.logs.slice(0, 200) : [];
    stripRetiredFourCodeState(merged);
    // 临时挂到 state 以便回填新增账本键（合尾三项/三对数），随后由启动流程覆盖
    state = merged;
    restoreFrameworkItemAliases(state.currentPrediction);
    if (Array.isArray(state.predictions)) {
      state.predictions.forEach((prediction) => restoreFrameworkItemAliases(prediction));
    }
    rebuildMissingModelCountersFromPredictions();
    normalizeStableMathFiltersInState(merged);
    return state;
  } catch (err) {
    const brokenName = path.join(BACKUP_DIR, `broken-state-${Date.now()}.json`);
    try {
      fs.copyFileSync(STATE_FILE, brokenName);
    } catch (_) {}
    const fresh = createDefaultState();
    fresh.lastError = { at: isoNow(), message: `旧 state.json 读取失败，已备份到 ${brokenName}`, error: err.message };
    return fresh;
  }
}

/** 模型共享历史优先读 RDS（8787）；算法账本仍在本进程独立，不把 history 按模型复制。 */
const RDS_API_BASE = String(process.env.RDS_API_BASE || "http://127.0.0.1:8787").replace(/\/$/, "");
const HISTORY_FROM_RDS = String(process.env.PENTA5_HISTORY_FROM_RDS || "1") !== "0";
const RDS_PRIMARY_WRITE = String(process.env.PENTA5_RDS_PRIMARY_WRITE || "1") !== "0";
const PENDING_OFFICIAL_DRAWS_LIMIT = Math.max(50, Number(process.env.PENDING_OFFICIAL_DRAWS_LIMIT || 240));

let upsertOfficialDraw = null;
try {
  upsertOfficialDraw = require(path.join(__dirname, "api", "draw_store.js")).upsertOfficialDraw;
} catch (err) {
  console.warn("[RDS draw_store] load failed:", err && err.message ? err.message : err);
}

function isRdsWriteLockError(err) {
  const msg = String(err && err.message ? err.message : err || "");
  return /LOCK_WRITE|read[_ ]?only|ER_OPTION_PREVENTS_STATEMENT|1290/i.test(msg);
}

function slimPendingOfficialDraw(record) {
  if (!record || typeof record !== "object") return null;
  const period = String(record.period || record.uid || "");
  const uid = String(record.uid || record.period || "");
  if (!period) return null;
  let numbers = Array.isArray(record.numbers) ? record.numbers.map((n) => String(n)) : [];
  if (numbers.length < 4) return null;
  return {
    period,
    uid: uid || period,
    numbers,
    opentime: record.opentime || record.openTime || "",
    capturedAt: record.capturedAt || isoNow(),
    metrics: record.metrics || null
  };
}

function normalizePendingOfficialDraws(list) {
  const out = [];
  const seen = new Set();
  const rows = Array.isArray(list) ? list : [];
  for (const row of rows) {
    const slim = slimPendingOfficialDraw(row);
    if (!slim || seen.has(slim.period)) continue;
    seen.add(slim.period);
    out.push(slim);
  }
  out.sort((a, b) => String(b.period).localeCompare(String(a.period)));
  return out.slice(0, PENDING_OFFICIAL_DRAWS_LIMIT);
}

function queuePendingOfficialDraw(record, reason) {
  if (!state) return [];
  const slim = slimPendingOfficialDraw(record);
  if (!slim) return state.pendingOfficialDraws || [];
  const merged = normalizePendingOfficialDraws([slim, ...(Array.isArray(state.pendingOfficialDraws) ? state.pendingOfficialDraws : [])]);
  state.pendingOfficialDraws = merged;
  state.rdsReadOnly = true;
  state.rdsLag = {
    at: isoNow(),
    reason: String(reason || "pending-write"),
    localPeriod: slim.period,
    pendingCount: merged.length,
    readOnly: true
  };
  return merged;
}

function mergePendingDrawsIntoHistory(targetState) {
  if (!targetState) return { merged: 0 };
  const pending = normalizePendingOfficialDraws(targetState.pendingOfficialDraws);
  targetState.pendingOfficialDraws = pending;
  if (!pending.length) return { merged: 0 };
  const existing = Array.isArray(targetState.history) ? targetState.history.slice() : [];
  const seen = new Set(existing.map((row) => String(row && (row.period || row.uid) || "")));
  let merged = 0;
  for (const row of pending) {
    const period = String(row.period || "");
    if (!period || seen.has(period)) continue;
    existing.unshift({
      period,
      uid: row.uid || period,
      numbers: row.numbers,
      opentime: row.opentime || "",
      capturedAt: row.capturedAt || "",
      metrics: row.metrics || undefined
    });
    seen.add(period);
    merged += 1;
  }
  existing.sort((a, b) => String(b.period || "").localeCompare(String(a.period || "")));
  targetState.history = existing.slice(0, MAX_HISTORY);
  if (targetState.history[0]) {
    targetState.latestRecord = targetState.history[0];
    targetState.sharedHistoryLatestPeriod = String(targetState.history[0].period || "");
    targetState.sharedHistoryCount = targetState.history.length;
  }
  return { merged, pendingCount: pending.length };
}

async function writeOfficialDrawToRds(record) {
  if (!RDS_PRIMARY_WRITE) return { ok: true, skipped: true, reason: "disabled" };
  if (typeof upsertOfficialDraw !== "function") {
    throw new Error("RDS draw_store unavailable (api/draw_store.js / mysql2)");
  }
  try {
    const wrote = await upsertOfficialDraw(record);
    if (state) {
      state.rdsReadOnly = false;
      if (state.rdsLag && state.rdsLag.readOnly) {
        state.rdsLag = Object.assign({}, state.rdsLag, { readOnly: false, clearedAt: isoNow() });
      }
    }
    return wrote && typeof wrote === "object" ? wrote : { ok: true, period: record && record.period };
  } catch (err) {
    if (isRdsWriteLockError(err)) {
      const pending = queuePendingOfficialDraw(record, "lock-write");
      return {
        ok: false,
        readOnly: true,
        reason: "lock-write",
        error: String(err && err.message ? err.message : err),
        period: record && record.period,
        pendingCount: pending.length
      };
    }
    throw err;
  }
}

let rdsCatchupTimer = null;
let rdsCatchupRunning = false;

function scheduleRdsCatchupRetry(reason) {
  if (rdsCatchupTimer) return;
  const delayMs = Math.max(15000, Number(process.env.RDS_CATCHUP_RETRY_MS || 60000));
  rdsCatchupTimer = setTimeout(() => {
    rdsCatchupTimer = null;
    flushPendingOfficialDrawsToRds(reason || "timer").catch((err) => {
      console.error("[RDS catchup] failed", err && err.message ? err.message : err);
    });
  }, delayMs);
}

async function flushPendingOfficialDrawsToRds(reason) {
  if (!RDS_PRIMARY_WRITE || typeof upsertOfficialDraw !== "function") {
    return { ok: false, reason: "unavailable" };
  }
  if (rdsCatchupRunning) return { ok: true, skipped: true, reason: "running" };
  rdsCatchupRunning = true;
  try {
    const pending = normalizePendingOfficialDraws(state && state.pendingOfficialDraws);
    const memoryExtra = [];
    if (state && Array.isArray(state.history) && state.sharedHistoryRdsLatestPeriod) {
      const rdsLatest = String(state.sharedHistoryRdsLatestPeriod || "");
      for (const row of state.history) {
        const period = String(row && row.period || "");
        if (!period || !rdsLatest) continue;
        if (period > rdsLatest) memoryExtra.push(row);
      }
    }
    const queue = normalizePendingOfficialDraws([...pending, ...memoryExtra]);
    if (!queue.length) {
      if (state) state.rdsReadOnly = false;
      return { ok: true, flushed: 0, reason: reason || "empty" };
    }
    const remain = [];
    let flushed = 0;
    let hitReadOnly = false;
    // 从旧到新追写，避免中间空洞
    const ordered = queue.slice().sort((a, b) => String(a.period).localeCompare(String(b.period)));
    for (const row of ordered) {
      try {
        await upsertOfficialDraw(row);
        flushed += 1;
      } catch (err) {
        if (isRdsWriteLockError(err)) {
          hitReadOnly = true;
          remain.push(row);
          remain.push(...ordered.slice(ordered.indexOf(row) + 1));
          break;
        }
        console.error("[RDS catchup] upsert failed", row && row.period, err && err.message ? err.message : err);
        remain.push(row);
      }
    }
    if (state) {
      state.pendingOfficialDraws = normalizePendingOfficialDraws(remain);
      state.rdsReadOnly = hitReadOnly;
      state.rdsLag = {
        at: isoNow(),
        reason: hitReadOnly ? "lock-write" : "catchup",
        flushed,
        pendingCount: state.pendingOfficialDraws.length,
        readOnly: hitReadOnly,
        trigger: reason || "catchup"
      };
    }
    if (hitReadOnly) scheduleRdsCatchupRetry("still-readonly");
    else if (flushed > 0) {
      addLog("RDS 待写入开奖已追平", { flushed, reason: reason || "catchup", pending: (state && state.pendingOfficialDraws || []).length });
      try {
        await hydrateSharedHistoryFromRds(state);
      } catch (_) {}
    }
    return { ok: !hitReadOnly, flushed, pending: remain.length, readOnly: hitReadOnly };
  } finally {
    rdsCatchupRunning = false;
  }
}

function fetchJsonUrlSync(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: timeoutMs || 12000 }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        try {
          const text = Buffer.concat(chunks).toString("utf8");
          resolve({ status: res.statusCode || 0, body: JSON.parse(text || "{}") });
        } catch (err) {
          reject(err);
        }
      });
    });
    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("timeout"));
    });
  });
}

async function hydrateSharedHistoryFromRds(targetState) {
  if (!HISTORY_FROM_RDS || !targetState) return { ok: false, reason: "disabled" };
  try {
    const health = await fetchJsonUrlSync(`${RDS_API_BASE}/health`, 8000);
    if (!health.body || health.body.ok !== true) return { ok: false, reason: "rds-unhealthy" };
    const limit = Math.min(Math.max(Number(MAX_HISTORY || 1000), 1), 5000);
    const hist = await fetchJsonUrlSync(`${RDS_API_BASE}/history?limit=${limit}`, 20000);
    if (!hist.body || hist.body.ok !== true) return { ok: false, reason: "history-fail", status: hist.status };
    const rows = hist.body.history || hist.body.rows || hist.body.items;
    if (!Array.isArray(rows) || !rows.length) return { ok: false, reason: "empty" };
    const mapped = [];
    const seen = new Set();
    for (const row of rows) {
      if (!row || typeof row !== "object") continue;
      const period = String(row.period || row.uid || "");
      if (!period || seen.has(period)) continue;
      let numbers = row.numbers;
      if (!Array.isArray(numbers) && Array.isArray(row.openCode)) numbers = row.openCode;
      if (!Array.isArray(numbers)) continue;
      numbers = numbers.map((n) => String(n)).filter((n) => /^[0-9]$/.test(n));
      if (numbers.length < 4) continue;
      seen.add(period);
      mapped.push({
        ...row,
        period,
        uid: row.uid || period,
        numbers,
        opentime: row.opentime || row.openTime || row.open_time || ""
      });
    }
    if (!mapped.length) return { ok: false, reason: "unmap" };
    mapped.sort((a, b) => String(b.period || b.uid || "").localeCompare(String(a.period || a.uid || "")));
    const rdsLatest = health.body.latestPeriod || (mapped[0] && mapped[0].period);
    const rdsLatestStr = String(rdsLatest || "");
    const localExtras = [];
    const priorHistory = Array.isArray(targetState.history) ? targetState.history : [];
    for (const row of priorHistory) {
      const period = String(row && (row.period || row.uid) || "");
      if (!period || !rdsLatestStr) continue;
      if (period > rdsLatestStr) localExtras.push(row);
    }
    // 主库以 RDS 为准；LOCK_WRITE 期间本地超前的期合并进 pending，禁止回滚丢期。
    targetState.history = mapped.slice(0, MAX_HISTORY);
    targetState.sharedHistorySource = "rds";
    targetState.sharedHistoryRdsLatestPeriod = rdsLatest || null;
    if (localExtras.length) {
      targetState.pendingOfficialDraws = normalizePendingOfficialDraws([
        ...localExtras,
        ...(Array.isArray(targetState.pendingOfficialDraws) ? targetState.pendingOfficialDraws : [])
      ]);
      targetState.rdsReadOnly = true;
    }
    const pendingMerge = mergePendingDrawsIntoHistory(targetState);
    targetState.sharedHistoryLatestPeriod = (targetState.history[0] && targetState.history[0].period) || rdsLatest || null;
    targetState.sharedHistoryCount = targetState.history.length;
    targetState.sharedHistorySyncedAt = isoNow();
    if (pendingMerge.merged > 0 || (targetState.pendingOfficialDraws || []).length > 0) {
      targetState.sharedHistorySource = "rds+pending";
    }
    return {
      ok: true,
      mode: pendingMerge.merged > 0 ? "rds+pending" : "rds",
      count: targetState.history.length,
      latestPeriod: targetState.sharedHistoryLatestPeriod,
      rdsLatest,
      pendingMerged: pendingMerge.merged || 0,
      preservedLocal: localExtras.length
    };
  } catch (err) {
    return { ok: false, reason: String(err && err.message ? err.message : err) };
  }
}

async function hydrateAlgoDurableFromRds(targetState) {
  if (!targetState) return { ok: false, reason: "no-state" };
  if (!ALGO_RDS_DURABLE && !LOCAL_TIP_ONLY) return { ok: false, reason: "disabled" };
  try {
    const resp = await fetchJsonUrlSync(`${RDS_API_BASE}/state`, 60000);
    if (!resp.body || resp.body.ok !== true) {
      return { ok: false, reason: "state-fail", status: resp.status };
    }
    const body = resp.body;
    const modules = body.modelModules && typeof body.modelModules === "object" ? body.modelModules : {};
    const take = (key) => {
      const row = modules[key];
      return row && Object.prototype.hasOwnProperty.call(row, "state") ? row.state : undefined;
    };
    let applied = 0;
    const preferRds = DATA_READ_MODE === "rds-primary" || LOCAL_TIP_ONLY;

    if (body.counters && typeof body.counters === "object") {
      const localTotal = Object.keys(targetState.counters || {}).reduce(
        (sum, k) => sum + Number((targetState.counters[k] && targetState.counters[k].total) || 0),
        0
      );
      const rdsTotal = Object.keys(body.counters).reduce(
        (sum, k) => sum + Number((body.counters[k] && body.counters[k].total) || 0),
        0
      );
      // tip-only 本地已落盘 counters 时不要被陈旧 RDS 账本冲掉（否则按天覆盖又只能靠重启后几条样本）
      let takeCounters = false;
      if (localTotal === 0) takeCounters = true;
      else if (LOCAL_TIP_ONLY) takeCounters = false;
      else if (preferRds || rdsTotal >= localTotal) takeCounters = true;
      if (takeCounters) {
        targetState.counters = normalizeCounters(Object.assign({}, body.counters));
        applied += 1;
      }
    }
    if (body.dailyStreakLedger && typeof body.dailyStreakLedger === "object") {
      const localLedgerKeys = Object.keys(targetState.dailyStreakLedger || {}).length;
      const takeLedger = localLedgerKeys === 0 || (!LOCAL_TIP_ONLY && (preferRds || !localLedgerKeys));
      if (takeLedger) {
        targetState.dailyStreakLedger = normalizeDailyStreakLedger(body.dailyStreakLedger);
        applied += 1;
      }
    }

    const dictKeys = [
      "modelWatchdog",
      "modelSelectionLocks",
      "positionStrategyLocks",
      "stable120Locks",
      "threeDuishuSelectionLock",
      "algorithmProjectHealth",
      "lastSync",
      "lastError"
    ];
    dictKeys.forEach((key) => {
      const value = take(key);
      if (value === undefined) return;
      if (key === "modelWatchdog") {
        targetState.modelWatchdog = normalizeModelWatchdog(value);
      } else if (key === "algorithmProjectHealth") {
        targetState.algorithmProjectHealth = normalizeAlgorithmProjectHealth(value);
      } else {
        targetState[key] = value;
      }
      applied += 1;
    });

    const tipReadyMod = take("tipReady");
    if (typeof tipReadyMod === "boolean") {
      targetState.tipReady = tipReadyMod;
      applied += 1;
    }

    const rdsCurrent = take("currentPrediction");
    if (rdsCurrent && typeof rdsCurrent === "object") {
      const localCp = targetState.currentPrediction;
      const rdsUid = String(rdsCurrent.baseUid || rdsCurrent.basePeriod || "");
      const localUid = String((localCp && (localCp.baseUid || localCp.basePeriod)) || "");
      if (preferRds || !localCp || (rdsUid && rdsUid >= localUid)) {
        targetState.currentPrediction = rdsCurrent;
        applied += 1;
      }
    }

    const snapshots = {};
    Object.keys(modules).forEach((key) => {
      if (!String(key).startsWith("algoProject:")) return;
      const id = String(key).slice("algoProject:".length);
      const value = take(key);
      if (value !== undefined) snapshots[id] = value;
    });
    if (Object.keys(snapshots).length) {
      targetState.algorithmProjectSnapshots = snapshots;
      applied += 1;
    }

    targetState.algoDurableHydratedAt = isoNow();
    targetState.algoDurableSource = "rds";
    return {
      ok: true,
      applied,
      moduleCount: Object.keys(modules).length,
      algoProjectCount: Object.keys(snapshots).length,
      counterKeys: Object.keys(targetState.counters || {}).length
    };
  } catch (err) {
    return { ok: false, reason: String(err && err.message ? err.message : err) };
  }
}

function scrubReconstructedTruthFlags() {
  let changed = 0;
  (Array.isArray(state && state.predictions) ? state.predictions : []).forEach((prediction) => {
    const ev = prediction && prediction.evaluation;
    if (!ev || typeof ev !== "object") return;
    ["autoTop6", "recommend2", "killFramework", "fourDingFramework", "threeDuishu"].forEach((storeName) => {
      const rec = ev[storeName];
      if (!rec || rec.reconstructed !== true) return;
      if (rec.truthEligible === true) {
        rec.truthEligible = false;
        rec.reason = rec.reason || "reconstructed-historical-backfill-not-for-truth";
        changed += 1;
      }
    });
  });
  return changed;
}

function restoreFrameworkItemAliases(prediction) {
  if (!prediction || typeof prediction !== "object") return prediction;
  FRAMEWORK_SNAPSHOT_STORE_KEYS.forEach((key) => {
    const snapshot = prediction[key];
    const itemsKey = `${key}Items`;
    const reportKey = `${key}RepairReport`;
    if (snapshot && typeof snapshot === "object") {
      if ((!Array.isArray(prediction[itemsKey]) || !prediction[itemsKey].length) && Array.isArray(snapshot.items)) {
        prediction[itemsKey] = snapshot.items;
      }
      if (prediction[reportKey] == null && snapshot.repairReport != null) {
        prediction[reportKey] = snapshot.repairReport;
      }
    }
  });
  return prediction;
}

function predictionKeepRichForPersist(prediction, currentId, currentUid) {
  if (!prediction || typeof prediction !== "object") return false;
  if (currentId && String(prediction.id || "") === String(currentId)) return true;
  if (!prediction.evaluated && currentUid && String(prediction.baseUid || "") === String(currentUid)) return true;
  if (!prediction.evaluated) return true;
  return false;
}

function compactPredictionsInMemory() {
  if (!state || typeof state !== "object") return 0;
  const currentId = state.currentPrediction && state.currentPrediction.id ? String(state.currentPrediction.id) : "";
  const currentUid = state.currentPrediction && state.currentPrediction.baseUid ? String(state.currentPrediction.baseUid) : "";
  let changed = 0;
  if (Array.isArray(state.predictions)) {
    state.predictions = state.predictions.slice(0, MAX_PREDICTION_LOG).map((prediction) => {
      const keepRich = predictionKeepRichForPersist(prediction, currentId, currentUid);
      const next = slimPredictionForDisk(prediction, keepRich);
      if (keepRich) restoreFrameworkItemAliases(next);
      changed += 1;
      return next;
    });
  }
  if (state.currentPrediction) {
    state.currentPrediction = slimPredictionForDisk(state.currentPrediction, true);
    restoreFrameworkItemAliases(state.currentPrediction);
    changed += 1;
  }
  return changed;
}

function slimFrameworkEvaluationForPersist(block) {
  if (!block || typeof block !== "object") return block || null;
  const out = {};
  [
    "ok",
    "version",
    "kind",
    "label",
    "basePeriod",
    "success",
    "truthEligible",
    "sealedAt",
    "sealedBaseUid",
    "sealHash",
    "passed",
    "total",
    "hit",
    "judged",
    "rate",
    "positions",
    "items",
    "shapeItems",
    "complete",
    "code",
    "shot",
    "pairs",
    "pairCodes",
    "selectedPair",
    "selectedPairCode",
    "reconstructed",
    "reconstructedAt",
    "reason",
    "voided",
    "voidReason"
  ].forEach((key) => {
    if (block[key] !== undefined) out[key] = block[key];
  });
  return out;
}

function slimExtraKillForPersist(entry) {
  if (!entry || typeof entry !== "object") return entry || null;
  const out = {};
  [
    "key",
    "label",
    "value",
    "code",
    "primary",
    "backup",
    "algorithm",
    "hitMode",
    "matchMode",
    "hardnessScore",
    "rate30",
    "rate60",
    "rate120",
    "rate300",
    "maxMiss120",
    "maxMissLong",
    "lower120",
    "stage",
    "confidence",
    "recommendable",
    "preferOverlap"
  ].forEach((key) => {
    if (entry[key] !== undefined) out[key] = entry[key];
  });
  return out;
}

function slimExtraKillsForPersist(extraKills, keepCodes) {
  if (!extraKills || typeof extraKills !== "object") return {};
  const out = {};
  Object.keys(extraKills).forEach((key) => {
    out[key] = slimExtraKillForPersist(extraKills[key], keepCodes);
  });
  return out;
}

function slimEvaluationForPersist(evaluation) {
  const base = slimEvaluation(evaluation);
  if (!base || typeof base !== "object") return base || null;
  // fourDingFramework 已在 FRAMEWORK_SNAPSHOT_STORE_KEYS
  FRAMEWORK_SNAPSHOT_STORE_KEYS.concat(["threeDuishu"]).forEach((key) => {
    if (base[key]) base[key] = slimFrameworkEvaluationForPersist(base[key]);
  });
  return base;
}

function slimPredictionForDisk(prediction, keepRich) {
  if (!prediction || typeof prediction !== "object") return prediction || null;
  const out = {
    id: prediction.id,
    modelVersion: prediction.modelVersion,
    baseUid: prediction.baseUid,
    basePeriod: prediction.basePeriod,
    baseOpenTime: prediction.baseOpenTime,
    targetPeriod: prediction.targetPeriod,
    targetPeriodRule: prediction.targetPeriodRule,
    periodsPerDay: prediction.periodsPerDay,
    generatedAt: prediction.generatedAt,
    generatedAtText: prediction.generatedAtText,
    totalHeweiKill: prediction.totalHeweiKill,
    totalHeweiBackupDigit: prediction.totalHeweiBackupDigit,
    threeFourHeweiKillDigit: prediction.threeFourHeweiKillDigit,
    threeFourHeweiBackupDigit: prediction.threeFourHeweiBackupDigit,
    evaluated: !!prediction.evaluated,
    counterApplied: !!prediction.counterApplied,
    counterSkipped: !!prediction.counterSkipped,
    counterSkipReason: prediction.counterSkipReason || "",
    // The aggregate seal is evidence, not a presentation-only flag.  This
    // function is used both for disk serialization and in-memory compaction;
    // dropping these fields made a perfectly sealed tip appear mutable after
    // every save and allowed later refresh work to touch the current period.
    snapshotsSealedAt: prediction.snapshotsSealedAt || "",
    snapshotsSealedBaseUid: prediction.snapshotsSealedBaseUid || "",
    allSnapshotsSealed: prediction.allSnapshotsSealed === true
  };
  if (prediction.evaluation) out.evaluation = slimEvaluationForPersist(prediction.evaluation);
  out.positionKills = slimPositionKills(prediction.positionKills, !!keepRich);
  out.independentHeweiKills = slimHeweiKills(prediction.independentHeweiKills, !!keepRich);
  if (prediction.extraKills) out.extraKills = slimExtraKillsForPersist(prediction.extraKills, !!keepRich);
  if (keepRich) {
    // 当前 tip 必须保留完整封存快照（含三对数），禁止再削字段，否则 sealHash 校验会失败
    if (prediction.threeDuishu) out.threeDuishu = prediction.threeDuishu;
    if (prediction.fourPosExclude) out.fourPosExclude = prediction.fourPosExclude;
    FRAMEWORK_SNAPSHOT_STORE_KEYS.forEach((key) => {
      if (prediction[key]) out[key] = prediction[key];
      const itemsKey = `${key}Items`;
      const reportKey = `${key}RepairReport`;
      if (prediction[itemsKey]) out[itemsKey] = prediction[itemsKey];
      if (prediction[reportKey]) out[reportKey] = prediction[reportKey];
    });
    if (prediction.recommendationMeta) out.recommendationMeta = prediction.recommendationMeta;
    if (prediction.detail) out.detail = slimThreeFourDetail(prediction.detail, true);
    if (prediction.tipStructurePerfect !== undefined) out.tipStructurePerfect = prediction.tipStructurePerfect;
    if (prediction.tipReady !== undefined) out.tipReady = prediction.tipReady;
    if (prediction.stableMathFilter) {
      out.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(prediction.stableMathFilter);
    }
    // L0 门道字段：禁止被 tip-only 瘦身抹掉
    if (prediction.hewei && typeof prediction.hewei === "object") out.hewei = prediction.hewei;
    if (prediction.codexStable && typeof prediction.codexStable === "object") {
      out.codexStable = prediction.codexStable;
    }
    if (prediction.orchestrateMeta && typeof prediction.orchestrateMeta === "object") {
      out.orchestrateMeta = prediction.orchestrateMeta;
    }
  } else {
    if (prediction.threeDuishu) out.threeDuishu = slimThreeDuishuSnapshot(prediction.threeDuishu);
    if (prediction.fourPosExclude) out.fourPosExclude = slimFourPosExcludeSnapshot(prediction.fourPosExclude);
  }
  return out;
}

function buildPersistedStateForDisk(source) {
  // 开奖 history / rawJson 只存在 RDS + 内存。
  // LOCAL_STATE_MODE=tip-only：不落开奖主库；但必须保留近期 predictions + counters + 日账，
  // 否则重启后按天百分比只能用几条内存样本重算，会假显示 100%。
  const sourceState = source || {};
  if (LOCAL_TIP_ONLY) {
    const currentId = sourceState.currentPrediction && sourceState.currentPrediction.id
      ? String(sourceState.currentPrediction.id)
      : "";
    const currentUid = sourceState.currentPrediction && sourceState.currentPrediction.baseUid
      ? String(sourceState.currentPrediction.baseUid)
      : "";
    return {
      version: sourceState.version || BACKEND_STATE_VERSION,
      updatedAt: sourceState.updatedAt || isoNow(),
      tipReady: !!sourceState.tipReady,
      currentPrediction: slimPredictionForDisk(sourceState.currentPrediction, true),
      latestRecord: sourceState.latestRecord || null,
      history: [],
      rawJson: null,
      predictions: (Array.isArray(sourceState.predictions) ? sourceState.predictions : [])
        .slice(0, MAX_PREDICTION_LOG)
        .map((prediction) => slimPredictionForDisk(
          prediction,
          predictionKeepRichForPersist(prediction, currentId, currentUid)
        )),
      counters: normalizeCounters(sourceState.counters),
      dailyStreakLedger: normalizeDailyStreakLedger(sourceState.dailyStreakLedger),
      historyPersistedInRds: true,
      dataReadMode: DATA_READ_MODE,
      algoDurableStore: ALGO_DURABLE_STORE,
      localStateMode: "tip-only",
      sharedHistorySource: sourceState.sharedHistorySource || "rds",
      sharedHistoryLatestPeriod: sourceState.sharedHistoryLatestPeriod || null,
      sharedHistoryCount: sourceState.sharedHistoryCount || null
    };
  }
  return buildMirrorFreezeState(sourceState);
}

function buildMirrorFreezeState(source) {
  // 镜像用全量内存快照（含 counters/locks/algoProject），与 tip-only 本地文件分离
  const sourceState = source || {};
  const disk = Object.assign({}, sourceState);
  disk.history = [];
  disk.rawJson = null;
  disk.historyPersistedInRds = true;
  disk.dataReadMode = String(process.env.DATA_READ_MODE || "rds-primary");
  disk.algoDurableStore = ALGO_DURABLE_STORE;
  disk.localStateMode = LOCAL_STATE_MODE;
  const currentId = sourceState.currentPrediction && sourceState.currentPrediction.id
    ? String(sourceState.currentPrediction.id)
    : "";
  const currentUid = sourceState.currentPrediction && sourceState.currentPrediction.baseUid
    ? String(sourceState.currentPrediction.baseUid)
    : "";
  disk.predictions = (Array.isArray(sourceState.predictions) ? sourceState.predictions : [])
    .slice(0, MAX_PREDICTION_LOG)
    .map((prediction) => slimPredictionForDisk(
      prediction,
      predictionKeepRichForPersist(prediction, currentId, currentUid)
    ));
  disk.currentPrediction = slimPredictionForDisk(sourceState.currentPrediction, true);
  if (disk.stableMathFilter) {
    disk.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(disk.stableMathFilter);
  }
  delete disk.frameworkSimLedger;
  delete disk.frameworkSimHistoryBackfill;
  return disk;
}

function saveState(options) {
  const opts = options && typeof options === "object" ? options : {};
  ensureDir(DATA_DIR);
  state.updatedAt = isoNow();
  stripRetiredFourCodeState(state);
  state.counters = normalizeCounters(state.counters);
  state.modelWatchdog = normalizeModelWatchdog(state.modelWatchdog);
  state.dailyStreakLedger = normalizeDailyStreakLedger(state.dailyStreakLedger);
  refreshBackendDailyStreaksIfNeeded();
  state.stormMode = evaluateAndApplyStormMode();
  // save 路径只刷新 tipReady 标记，避免在落盘时递归重生
  applyTipReadyState(
    state.currentPrediction,
    state.latestRecord || (Array.isArray(state.history) && state.history[0]) || null
  );
  const tmp = path.join(
    DATA_DIR,
    `.state.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`
  );
  try {
    fs.writeFileSync(tmp, safeJsonStringify(buildPersistedStateForDisk(state)));
    fs.renameSync(tmp, STATE_FILE);
    scheduleAlgorithmProjectStateSync("save-state");
    if (opts.skipMemoryCompact !== true) {
      try { compactPredictionsInMemory(); } catch (compactErr) {
        console.error("[saveState] compact memory failed", compactErr && compactErr.message ? compactErr.message : compactErr);
      }
    }
    // A periodic sync writes a new timestamp even when the current prediction
    // and its evaluated history have not changed.  Keep the expensive audit
    // cache in that case; its key below is based on actual audit inputs.
    invalidatePanelStateCache({ invalidateModelAudit: false });
    // tip-only / RDS 持久化：禁止被 skipMirror 饿死
    const mustMirror = LOCAL_TIP_ONLY || ALGO_RDS_DURABLE || opts.forceMirror === true;
    if (opts.skipMirror !== true || mustMirror) scheduleRdsMirrorAfterSave();
  } catch (err) {
    try {
      if (fs.existsSync(tmp)) fs.unlinkSync(tmp);
    } catch (_) {}
    throw err;
  }
}

let rdsMirrorTimer = null;
let rdsMirrorRunning = false;
let rdsMirrorPending = false;
let rdsHydrateTimer = null;
let rdsHydrateRunning = false;
const RDS_MIRROR_ENABLED = String(process.env.RDS_MIRROR_ENABLED || "1") !== "0";
const RDS_MIRROR_DEBOUNCE_MS = Math.max(30 * 1000, Number(process.env.RDS_MIRROR_DEBOUNCE_MS || 120 * 1000));
const RDS_HYDRATE_AFTER_MIRROR_MS = Math.max(1000, Number(process.env.RDS_HYDRATE_AFTER_MIRROR_MS || 5000));
const DATA_READ_MODE = String(process.env.DATA_READ_MODE || "rds-primary");
const ALGO_DURABLE_STORE = String(process.env.ALGO_DURABLE_STORE || "local").toLowerCase();
const LOCAL_STATE_MODE = String(process.env.LOCAL_STATE_MODE || "full").toLowerCase();
const ALGO_RDS_DURABLE = ALGO_DURABLE_STORE === "rds";
const LOCAL_TIP_ONLY = LOCAL_STATE_MODE === "tip-only";

function scheduleSharedHistoryHydrate(reason) {
  if (!HISTORY_FROM_RDS || !state) return;
  if (rdsHydrateTimer) clearTimeout(rdsHydrateTimer);
  rdsHydrateTimer = setTimeout(() => {
    rdsHydrateTimer = null;
    if (rdsHydrateRunning) return;
    rdsHydrateRunning = true;
    const prevSource = state.sharedHistorySource || null;
    const prevPeriod = state.sharedHistoryLatestPeriod || null;
    hydrateSharedHistoryFromRds(state).then((result) => {
      rdsHydrateRunning = false;
      if (result && result.ok) {
        addLog("共享历史已从 RDS 灌注", { reason: reason || "schedule", ...result });
        const catchup = settleMissedPredictionEvaluations({ reason: "hydrate-" + String(reason || "schedule"), save: false });
        const tip = alignTipPredictionAfterHydrate("hydrate-" + String(reason || "schedule"));
        const changed =
          prevSource !== "rds" ||
          String(prevPeriod || "") !== String(result.latestPeriod || "") ||
          Number(catchup && catchup.settled || 0) > 0 ||
          !!(tip && tip.ok && tip.created);
        if (changed) {
          try { saveState(); } catch (err) {
            console.error("[RDS hydrate] save failed", err && err.message ? err.message : err);
          }
        }
      } else {
        addLog("RDS 历史灌注跳过/失败", { reason: reason || "schedule", ...(result || {}) });
      }
    }).catch((err) => {
      rdsHydrateRunning = false;
      console.error("[RDS hydrate] failed", err && err.message ? err.message : err);
    });
  }, RDS_HYDRATE_AFTER_MIRROR_MS);
}

function scheduleRdsMirrorAfterSave() {
  if (!RDS_MIRROR_ENABLED) return;
  if (rdsMirrorTimer) clearTimeout(rdsMirrorTimer);
  rdsMirrorTimer = setTimeout(() => {
    rdsMirrorTimer = null;
    if (rdsMirrorRunning) {
      rdsMirrorPending = true;
      return;
    }
    const script = path.join(__dirname, "api", "mirror_incremental.js");
    if (!fs.existsSync(script)) return;
    rdsMirrorRunning = true;
    rdsMirrorPending = false;
    try {
      const logDir = path.join(DATA_DIR, "logs");
      try { fs.mkdirSync(logDir, { recursive: true }); } catch (_) {}
      const backupDir = path.join(DATA_DIR, "backups");
      try { fs.mkdirSync(backupDir, { recursive: true }); } catch (_) {}
      const logFile = path.join(logDir, "rds-mirror.log");
      const logFd = fs.openSync(logFile, "a");
      const mirrorHeapMb = Math.max(512, Number(process.env.RDS_MIRROR_HEAP_MB || 1536) || 1536);
      // tip-only 本地文件不含账本：从内存打全量 freeze 再导入 RDS
      const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const freezePath = path.join(backupDir, `state_mirror_${stamp}.json`);
      try {
        // 确保项目快照最新再冻结
        try {
          const syncResult = algorithmProjectIsolation.syncProjectStates({
            dataDir: DATA_DIR,
            currentPrediction: state.currentPrediction,
            predictions: state.predictions,
            latestRecord: state.latestRecord,
            modelVersion: MODEL_VERSION,
            projectHealth: state.algorithmProjectHealth,
            historyLimit: 120,
            reason: "pre-mirror"
          });
          if (syncResult && syncResult.snapshots) state.algorithmProjectSnapshots = syncResult.snapshots;
        } catch (syncErr) {
          console.error("[RDS mirror] project sync failed", syncErr && syncErr.message ? syncErr.message : syncErr);
        }
        fs.writeFileSync(freezePath, safeJsonStringify(buildMirrorFreezeState(state)));
      } catch (freezeErr) {
        try { fs.closeSync(logFd); } catch (_) {}
        rdsMirrorRunning = false;
        console.error("[RDS mirror] freeze failed", freezeErr && freezeErr.message ? freezeErr.message : freezeErr);
        return;
      }
      fs.writeSync(logFd, `\n==== mirror schedule ${new Date().toISOString()} heap=${mirrorHeapMb} freeze=${path.basename(freezePath)} ====\n`);
      try {
        addLog("RDS 镜像已调度", {
          heapMb: mirrorHeapMb,
          debounceMs: RDS_MIRROR_DEBOUNCE_MS,
          freeze: path.basename(freezePath),
          tipOnly: LOCAL_TIP_ONLY
        });
      } catch (_) {}
      const child = spawn(
        process.execPath,
        [`--max-old-space-size=${mirrorHeapMb}`, script, "--file", freezePath],
        {
          cwd: path.join(__dirname, "api"),
          detached: true,
          stdio: ["ignore", logFd, logFd],
          env: process.env
        }
      );
      child.on("exit", (code) => {
        try {
          fs.writeSync(logFd, `==== mirror exit code=${code} at ${new Date().toISOString()} ====\n`);
          fs.closeSync(logFd);
        } catch (_) {}
        rdsMirrorRunning = false;
        if (Number(code) === 0) {
          scheduleSharedHistoryHydrate("after-mirror");
        }
        if (rdsMirrorPending) {
          rdsMirrorPending = false;
          scheduleRdsMirrorAfterSave();
        }
      });
      child.on("error", (err) => {
        try {
          fs.writeSync(logFd, `==== mirror error ${err && err.message} ====\n`);
          fs.closeSync(logFd);
        } catch (_) {}
        rdsMirrorRunning = false;
        if (rdsMirrorPending) {
          rdsMirrorPending = false;
          scheduleRdsMirrorAfterSave();
        }
      });
      child.unref();
    } catch (_) {
      rdsMirrorRunning = false;
    }
  }, RDS_MIRROR_DEBOUNCE_MS);
}

function buildFreshApiUrl(apiUrl) {
  try {
    const url = new URL(apiUrl);
    url.searchParams.set("_", Date.now());
    return url.toString();
  } catch (err) {
    const joiner = String(apiUrl).includes("?") ? "&" : "?";
    return apiUrl + joiner + "_=" + Date.now();
  }
}

function fetchWithTimeout(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const target = new URL(url);
    const client = target.protocol === "https:" ? require("https") : require("http");

    const req = client.request(target, {
      method: "GET",
      headers: { "Accept": "application/json,text/plain,*/*" },
      timeout: timeoutMs
    }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
      });
      res.on("end", () => {
        resolve({
          ok: res.statusCode >= 200 && res.statusCode < 300,
          status: res.statusCode,
          statusText: res.statusMessage || "",
          headers: {
            get(name) {
              return res.headers[String(name).toLowerCase()] || "";
            }
          },
          text() {
            return Promise.resolve(body);
          }
        });
      });
    });

    req.on("timeout", () => {
      req.destroy();
      const err = new Error("请求超时，超过 " + timeoutMs + "ms 未响应");
      err.name = "AbortError";
      reject(err);
    });

    req.on("error", reject);
    req.end();
  });
}

function parseJsonSafely(rawText, contentType) {
  if (!rawText || !String(rawText).trim()) {
    throw new Error("接口返回为空");
  }
  const text = String(rawText).trim();
  if (text.startsWith("<!DOCTYPE") || text.startsWith("<html") || text.includes("<body") || text.includes("</html>")) {
    throw new Error("接口返回 HTML，不是 JSON。可能是旧代理失败、目标站拦截或接口地址错误。");
  }
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    throw new Error(`JSON 解析失败。Content-Type: ${contentType || "unknown"}。返回片段: ${text.slice(0, 300)}`);
  }
  if (parsed && typeof parsed.contents === "string") {
    try {
      parsed = JSON.parse(parsed.contents);
    } catch (err) {
      throw new Error("检测到 contents 字段，但 contents 不是合法 JSON");
    }
  }
  if (!parsed || typeof parsed !== "object") {
    throw new Error("JSON 顶层结构无效，不是 object 或 array");
  }
  return parsed;
}

function isPlainObject(value) {
  return Object.prototype.toString.call(value) === "[object Object]";
}

function deepFindFirstValue(input, keys) {
  const visited = new WeakSet();
  const keySet = keys.map((k) => String(k).toLowerCase());
  function walk(value) {
    if (value === null || value === undefined) return "";
    if (typeof value !== "object") return "";
    if (visited.has(value)) return "";
    visited.add(value);
    if (Array.isArray(value)) {
      for (const item of value) {
        const found = walk(item);
        if (found !== "") return found;
      }
      return "";
    }
    const objKeys = Object.keys(value);
    for (const key of objKeys) {
      if (keySet.includes(key.toLowerCase())) {
        const candidate = value[key];
        if (candidate !== null && candidate !== undefined && typeof candidate !== "object") {
          return String(candidate).trim();
        }
      }
    }
    for (const key of objKeys) {
      const found = walk(value[key]);
      if (found !== "") return found;
    }
    return "";
  }
  return walk(input);
}

function deepFindFirstArray(input, keys) {
  const visited = new WeakSet();
  const keySet = keys.map((k) => String(k).toLowerCase());
  function walk(value) {
    if (value === null || value === undefined) return null;
    if (typeof value !== "object") return null;
    if (visited.has(value)) return null;
    visited.add(value);
    if (Array.isArray(value)) {
      for (const item of value) {
        const found = walk(item);
        if (found) return found;
      }
      return null;
    }
    const objKeys = Object.keys(value);
    for (const key of objKeys) {
      if (keySet.includes(key.toLowerCase()) && Array.isArray(value[key])) {
        return value[key];
      }
    }
    for (const key of objKeys) {
      const found = walk(value[key]);
      if (found) return found;
    }
    return null;
  }
  return walk(input);
}

function findLikelyLotteryObject(input) {
  const visited = new WeakSet();
  const periodKeys = [
    "period", "periodNo", "period_no", "issue", "issueNo", "issue_no", "expect", "expectNo", "expect_no",
    "qihao", "qishu", "drawNo", "draw_no", "drawNumber", "round", "term", "no"
  ];
  const numberKeys = [
    "numbers", "number", "nums", "balls", "openCode", "opencode", "code", "result", "results", "drawCode",
    "drawResult", "winningNumbers", "winningNumber", "awardNumber", "awardCode"
  ];
  function hasAnyKey(obj, keys) {
    const lowerKeys = Object.keys(obj).map((k) => k.toLowerCase());
    return keys.some((key) => lowerKeys.includes(key.toLowerCase()));
  }
  function walk(value) {
    if (!value || typeof value !== "object") return null;
    if (visited.has(value)) return null;
    visited.add(value);
    if (Array.isArray(value)) {
      for (const item of value) {
        const found = walk(item);
        if (found) return found;
      }
      return null;
    }
    if (isPlainObject(value)) {
      const hasPeriod = hasAnyKey(value, periodKeys);
      const hasNumbers = hasAnyKey(value, numberKeys);
      if (hasPeriod || hasNumbers) return value;
    }
    for (const key of Object.keys(value)) {
      const found = walk(value[key]);
      if (found) return found;
    }
    return null;
  }
  return walk(input) || input;
}

function normalizeNumberList(value) {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (item === null || item === undefined) return "";
      if (typeof item === "number" || typeof item === "string") return String(item).replace(/[^\d]/g, "").trim();
      if (typeof item === "object") {
        const candidate = item.num || item.number || item.value || item.code || item.ball || item.name || "";
        return String(candidate).replace(/[^\d]/g, "").trim();
      }
      return "";
    }).filter(Boolean);
  }
  if (typeof value === "string" || typeof value === "number") {
    const text = String(value).trim();
    if (!text) return [];
    const splitNums = text.split(/[,，\s|\/\-]+/).map((item) => item.replace(/[^\d]/g, "").trim()).filter(Boolean);
    if (splitNums.length > 1) return splitNums;
    if (/^\d{5}$/.test(text)) return text.split("");
    return text.match(/\d+/g) || [];
  }
  if (typeof value === "object") {
    const candidate = value.numbers || value.number || value.nums || value.balls || value.openCode || value.opencode || value.code || value.result || value.results || value.drawCode || value.drawResult || value.winningNumbers || value.winningNumber || value.awardNumber || value.awardCode || "";
    return normalizeNumberList(candidate);
  }
  return [];
}

function extractNumbersFromJson(json, likelyObj) {
  const numberKeys = [
    "numbers", "number", "nums", "balls", "openCode", "opencode", "code", "result", "results", "drawCode",
    "drawResult", "winningNumbers", "winningNumber", "awardNumber", "awardCode"
  ];
  const lowerNumberKeys = numberKeys.map((k) => k.toLowerCase());
  for (const key of Object.keys(likelyObj || {})) {
    if (lowerNumberKeys.includes(key.toLowerCase())) {
      const nums = normalizeNumberList(likelyObj[key]);
      if (nums.length) return nums;
    }
  }
  const arr = deepFindFirstArray(json, numberKeys);
  const arrNums = normalizeNumberList(arr);
  if (arrNums.length) return arrNums;
  const value = deepFindFirstValue(json, numberKeys);
  const valueNums = normalizeNumberList(value);
  if (valueNums.length) return valueNums;
  return [];
}

function normalizeOpenTime(value) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "number") {
    const ms = value < 10000000000 ? value * 1000 : value;
    const d = new Date(ms);
    if (!Number.isNaN(d.getTime())) return d.toLocaleString("zh-CN", { hour12: false });
  }
  const text = String(value).trim();
  if (/^\d{10}$/.test(text) || /^\d{13}$/.test(text)) {
    const num = Number(text);
    const ms = text.length === 10 ? num * 1000 : num;
    const d = new Date(ms);
    if (!Number.isNaN(d.getTime())) return d.toLocaleString("zh-CN", { hour12: false });
  }
  return text.replace(/[年月]/g, "-").replace(/日/g, "").replace("T", " ").replace(/\.\d+Z$/, "").trim();
}

function calculateMetrics(numbers) {
  const nums = normalizeNumberList(numbers).slice(0, 5);
  const n0 = Number(nums[0] || 0); // 球一
  const n1 = Number(nums[1] || 0); // 球二
  const n2 = Number(nums[2] || 0); // 球三
  const n3 = Number(nums[3] || 0); // 球四

  const totalSum = n0 + n1 + n2 + n3;

  return {
    totalSum: totalSum,
    totalHewei: String(totalSum % 10),

    // 新综合合尾口径
    wanQianBaiHewei: String((n0 + n1 + n2) % 10),
    qianBaiShiHewei: String((n1 + n2 + n3) % 10),
    wanQianBaiShiHewei: String((n0 + n1 + n2 + n3) % 10),
    // 千百个 / 千十个（独立三合尾）
    wanQianGeHewei: String((n0 + n1 + n3) % 10),
    wanBaiShiHewei: String((n0 + n2 + n3) % 10),

    // 保留旧字段名兼容旧页面，但计算口径严格限制在球一到球四。
    baiShiGeHewei: String((n1 + n2 + n3) % 10),
    qianBaiShiGeHewei: String(totalSum % 10),
    ball5ExcludedFromModel: true
  };
}

function getCompositeHeweiGroup(record) {
  // 合尾一律按开奖号码现算，避免 RDS/代理 metrics 脏数据污染结算
  const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
  const fallback = calculateMetrics(nums);
  return [
    String(fallback.wanQianBaiHewei),
    String(fallback.qianBaiShiHewei),
    String(fallback.wanQianBaiShiHewei)
  ];
}

function buildPositions(numbers) {
  return {
    wan: String(numbers[0] || ""),
    qian: String(numbers[1] || ""),
    bai: String(numbers[2] || ""),
    shi: String(numbers[3] || ""),
    ge: String(numbers[4] || "")
  };
}

function buildRecordUid(period, opentime, numbers) {
  if (period && period !== "--") return `period:${period}`;
  return "";
}

function parseLotteryJsonToRecord(json) {
  const likelyObj = findLikelyLotteryObject(json);
  const periodKeys = [
    "period", "periodNo", "period_no", "issue", "issueNo", "issue_no", "expect", "expectNo", "expect_no",
    "qihao", "qishu", "drawNo", "draw_no", "drawNumber", "round", "term", "no"
  ];
  const openTimeKeys = [
    "opentime", "openTime", "open_time", "time", "date", "drawTime", "draw_time", "lotteryTime", "lottery_time",
    "awardTime", "award_time", "createdAt", "createTime", "updateTime", "timestamp"
  ];
  let period = "";
  const lowerPeriodKeys = periodKeys.map((k) => k.toLowerCase());
  for (const key of Object.keys(likelyObj || {})) {
    if (lowerPeriodKeys.includes(key.toLowerCase())) {
      period = String(likelyObj[key]).trim();
      break;
    }
  }
  if (!period) period = deepFindFirstValue(json, periodKeys);
  if (!period) throw new Error("JSON 已解析成功，但缺少可追溯的真实开奖期号");
  let opentime = "";
  const lowerOpenTimeKeys = openTimeKeys.map((k) => k.toLowerCase());
  for (const key of Object.keys(likelyObj || {})) {
    if (lowerOpenTimeKeys.includes(key.toLowerCase())) {
      opentime = normalizeOpenTime(likelyObj[key]);
      break;
    }
  }
  if (!opentime) opentime = normalizeOpenTime(deepFindFirstValue(json, openTimeKeys));
  let numbers = extractNumbersFromJson(json, likelyObj).map((n) => String(n).replace(/[^\d]/g, "").trim()).filter(Boolean);
  if (numbers.length < 5 && numbers.length === 1 && /^\d{5}$/.test(numbers[0])) numbers = numbers[0].split("");
  if (numbers.length < 5) throw new Error("JSON 已解析成功，但开奖号码不足 5 位");
  numbers = numbers.slice(0, 5);
  const metrics = calculateMetrics(numbers);
  const positions = buildPositions(numbers);
  const uid = buildRecordUid(period, opentime || "--", numbers);
  // 不把完整源站 JSON 挂到每条 history：5000 期会把常驻堆撑爆；调试用 raw 只保留在 state.rawJson（最新一期）
  return { uid, period, opentime: opentime || "--", numbers, positions, metrics, rawJson: null, capturedAt: isoNow() };
}

function getDigitMissingByGetter(history, getter) {
  const missing = createZeroMap();
  DIGIT_POOL.forEach((digit) => {
    let count = 0;
    for (let i = 0; i < history.length; i++) {
      const value = String(getter(history[i]));
      if (value === digit) break;
      count++;
    }
    missing[digit] = count;
  });
  return missing;
}

function countConsecutiveFromLatest(history, getter, targetDigit) {
  let count = 0;
  for (let i = 0; i < history.length; i++) {
    if (String(getter(history[i])) === String(targetDigit)) count++;
    else break;
  }
  return count;
}

function killListFromValue(value) {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (value && typeof value === "object") {
    const list = [];
    if (value.main !== undefined) list.push(value.main);
    if (value.backup !== undefined) list.push(value.backup);
    if (value.mainDigit !== undefined) list.push(value.mainDigit);
    if (value.backupDigit !== undefined) list.push(value.backupDigit);
    if (value.killDigit !== undefined) list.push(value.killDigit);
    if (Array.isArray(value.killDigits)) value.killDigits.forEach((x) => list.push(x));
    return list.map(String).filter((x) => x && x !== "--");
  }
  const digit = String(value === undefined || value === null ? "" : value);
  return digit && digit !== "--" ? [digit] : [];
}

function addModelStat(stats, key, success) {
  if (typeof success !== "boolean") return;
  if (!stats[key]) stats[key] = { total: 0, match: 0, rate: null, lastSuccess: null };
  stats[key].total += 1;
  if (success) stats[key].match += 1;
  if (stats[key].lastSuccess === null) stats[key].lastSuccess = success;
}

function finalizeModelStats(stats) {
  Object.keys(stats).forEach((key) => {
    if (key === "extraPair") return;
    const item = stats[key];
    item.rate = item.total ? Number(((item.match / item.total) * 100).toFixed(2)) : null;
    decorateTruthScoreFields(item, key, item.rate);
    if (item.predictivePower === false) {
      // 高基线：相对基线下滑才算弱，禁止用 85% 原始通过率误判
      item.low = item.total >= MODEL_MIN_SAMPLE && item.rate !== null && item.rate < (Number(item.randomBaseline || 0) - 3);
    } else {
      item.low = item.total >= MODEL_MIN_SAMPLE && item.rate !== null && item.rate < modelLowRateForKey(key);
    }
  });
  return stats;
}

function firstFourNumbers(record) {
  return (record && Array.isArray(record.numbers) ? record.numbers : []).slice(0, 4).map(String);
}

function firstFourSum(record) {
  return firstFourNumbers(record).reduce((sum, n) => sum + Number(n || 0), 0);
}

function normalizeNumberRange(value) {
  const nums = String(value === undefined || value === null ? "" : value)
    .match(/\d+/g);
  if (!nums || nums.length < 2) return null;
  const a = Number(nums[0]);
  const b = Number(nums[1]);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  const start = Math.max(0, Math.min(36, Math.min(a, b)));
  const end = Math.max(0, Math.min(36, Math.max(a, b)));
  return { start, end, code: `${start}-${end}` };
}

function firstFourSumInRange(record, value) {
  const range = normalizeNumberRange(value);
  if (!range) return false;
  const sum = firstFourSum(record);
  return sum >= range.start && sum <= range.end;
}

function firstFourSpan(record) {
  const nums = firstFourNumbers(record).map((value) => Number(value));
  if (nums.length < 4 || nums.some((value) => !Number.isFinite(value))) return null;
  return Math.max(...nums) - Math.min(...nums);
}

function firstFourRepeatShape(record) {
  const nums = firstFourNumbers(record);
  if (nums.length < 4) return "";
  const counts = {};
  nums.forEach((digit) => { counts[String(digit)] = Number(counts[String(digit)] || 0) + 1; });
  const values = Object.keys(counts).map((key) => counts[key]).sort((a, b) => b - a);
  if (!values.length) return "";
  if (values[0] >= 4) return "四重";
  if (values[0] >= 3) return "三重";
  const pairCount = values.filter((count) => count === 2).length;
  if (pairCount >= 2) return "双重";
  if (pairCount === 1) return "一对";
  return "无重";
}

function firstFourHasRepeat(record) {
  const shape = firstFourRepeatShape(record);
  return !!shape && shape !== "无重";
}

function firstFourMaxConsecutiveRun(record) {
  const nums = firstFourNumbers(record).map((value) => Number(value));
  if (nums.length < 4 || nums.some((value) => !Number.isFinite(value))) return 0;
  const sorted = Array.from(new Set(nums)).sort((a, b) => a - b);
  let best = sorted.length ? 1 : 0;
  let current = sorted.length ? 1 : 0;
  for (let index = 1; index < sorted.length; index += 1) {
    if (sorted[index] === sorted[index - 1] + 1) current += 1;
    else current = 1;
    if (current > best) best = current;
  }
  return best;
}

function firstFourConsecutiveShape(record) {
  const run = firstFourMaxConsecutiveRun(record);
  if (run >= 4) return "四连";
  if (run >= 3) return "三连";
  if (run >= 2) return "二连";
  return "无连";
}

function firstFourHasConsecutive(record) {
  return firstFourMaxConsecutiveRun(record) >= 2;
}

function firstFourParityRatio(record) {
  const nums = firstFourNumbers(record).map((value) => Number(value));
  if (nums.length < 4 || nums.some((value) => !Number.isFinite(value))) return "";
  const odd = nums.filter((value) => Math.abs(value) % 2 === 1).length;
  return `${odd}单${4 - odd}双`;
}

function firstFourSizeRatio(record) {
  const nums = firstFourNumbers(record).map((value) => Number(value));
  if (nums.length < 4 || nums.some((value) => !Number.isFinite(value))) return "";
  const big = nums.filter((value) => value >= 5).length;
  return `${big}大${4 - big}小`;
}

function firstFourCode(record) {
  const nums = firstFourNumbers(record);
  return nums.length >= 4 ? nums.slice(0, 4).map(String).join("") : "";
}

function extraStructureMatched(record, key, candidate) {
  const value = String(candidate || "");
  if (key === "sumRangeAvoid") return firstFourSumInRange(record, value);
  if (key === "spanAvoid") return firstFourSpan(record) !== null && String(firstFourSpan(record)) === value;
  if (key === "repeatAvoid") {
    if (value === "重号") return firstFourHasRepeat(record);
    return firstFourRepeatShape(record) === value;
  }
  if (key === "consecutiveAvoid") {
    if (value === "连号") return firstFourHasConsecutive(record);
    return firstFourConsecutiveShape(record) === value;
  }
  if (key === "parityRatioAvoid") return firstFourParityRatio(record) === value;
  if (key === "sizeRatioAvoid") return firstFourSizeRatio(record) === value;
  return false;
}

function threeDigitCooccurs(record, candidate) {
  const actual = firstFourNumbers(record);
  return String(candidate).split("").every((digit) => actual.includes(String(digit)));
}

function uniqueDigits(value) {
  const out = [];
  String(value === undefined || value === null ? "" : value).replace(/\D/g, "").split("").forEach((digit) => {
    if (digit !== "" && !out.includes(digit)) out.push(digit);
  });
  return out;
}

function heweiCombinationTailMaskFromNumbers(numbers, size) {
  const actual = (Array.isArray(numbers) ? numbers : []).map((value) => Number(value));
  const need = Math.max(1, Number(size || 1));
  if (actual.length < need || actual.some((value) => !Number.isFinite(value))) return 0;
  let mask = 0;
  function walk(start, picked, sum) {
    if (picked === need) {
      mask |= (1 << (Math.abs(sum) % 10));
      return;
    }
    for (let index = start; index < actual.length; index += 1) {
      walk(index + 1, picked + 1, sum + actual[index]);
    }
  }
  walk(0, 0, 0);
  return mask;
}

function heweiCombinationTailMask(record, size) {
  return heweiCombinationTailMaskFromNumbers(firstFourNumbers(record), size);
}

function heweiCombinationTails(record, size) {
  const mask = heweiCombinationTailMask(record, size);
  if (!mask) return [];
  const out = [];
  for (let digit = 0; digit <= 9; digit += 1) {
    if (mask & (1 << digit)) out.push(String(digit));
  }
  return out;
}

function anyHeweiCombinationHitOnNumbers(numbers, candidate, size, minMatched) {
  const selected = uniqueDigits(candidate).slice(0, 10);
  const required = Math.max(1, Number(minMatched || 1));
  if (selected.length < required) return false;
  const tailMask = heweiCombinationTailMaskFromNumbers(numbers, size);
  if (!tailMask) return false;
  let matched = 0;
  for (let index = 0; index < selected.length; index += 1) {
    const digit = Number(String(selected[index]));
    if (Number.isFinite(digit) && digit >= 0 && digit <= 9 && (tailMask & (1 << digit))) matched += 1;
  }
  return matched >= required;
}

function anyHeweiCombinationHit(record, candidate, size, minMatched) {
  return anyHeweiCombinationHitOnNumbers(firstFourNumbers(record), candidate, size, minMatched);
}

function heweiScopeIndexesFromItems(items) {
  // 与 AUTO_TOP6_POSITIONS 对齐：wan/qian/bai/shi -> 千百十个
  const keyToIndex = { wan: 0, qian: 1, bai: 2, shi: 3 };
  const indexes = [];
  (Array.isArray(items) ? items : []).forEach((item) => {
    if (!item || item.kind !== "position") return;
    const index = keyToIndex[String(item.key || "")];
    if (!Number.isInteger(index) || index < 0 || index > 3) return;
    if (!indexes.includes(index)) indexes.push(index);
  });
  return indexes.sort((left, right) => left - right);
}

function numbersFromRecordByIndexes(record, indexes) {
  const nums = Array.isArray(record && record.numbers) ? record.numbers : [];
  const out = [];
  (Array.isArray(indexes) ? indexes : []).forEach((index) => {
    if (!Number.isInteger(index) || index < 0 || index >= nums.length) return;
    if (nums[index] === undefined || nums[index] === null) return;
    out.push(String(nums[index]));
  });
  return out;
}

function firstFourDigitMask(record) {
  const actual = firstFourNumbers(record);
  if (actual.length < 4) return 0;
  let mask = 0;
  actual.forEach((digit) => {
    const value = Number(String(digit));
    if (Number.isFinite(value) && value >= 0 && value <= 9) mask |= (1 << value);
  });
  return mask;
}

function threeDuishuPairMask(pair) {
  let mask = 0;
  (Array.isArray(pair && pair.digits) ? pair.digits : []).forEach((digit) => {
    const value = Number(String(digit));
    if (Number.isFinite(value) && value >= 0 && value <= 9) mask |= (1 << value);
  });
  return mask;
}

function resolveThreeDuishuPairs(pairsOrKeys) {
  return (Array.isArray(pairsOrKeys) ? pairsOrKeys : []).map((item) => {
    if (item && Array.isArray(item.digits)) return item;
    const key = String(item && item.key || item || "").replace(/[^\d]/g, "").slice(0, 2);
    if (!/^\d{2}$/.test(key)) return null;
    return THREE_DUISHU_PAIRS.find((pair) => pair.key === key) || {
      key,
      digits: [key[0], key[1]],
      codes: [key[0], key[1]]
    };
  }).filter(Boolean);
}

function threeDuishuShotText(pairs) {
  const keys = resolveThreeDuishuPairs(pairs).map((pair) => pair.key);
  return keys.map((key) => `除对数${key}`).join("");
}

function threeDuishuRecordHits(record, pairs) {
  const recordMask = typeof record === "number" ? record : firstFourDigitMask(record);
  if (!recordMask) return false;
  return resolveThreeDuishuPairs(pairs).some((pair) => {
    const pairMask = threeDuishuPairMask(pair);
    return pairMask > 0 && (recordMask & pairMask) === pairMask;
  });
}

function selectThreeDuishuPair(records, candidate, options) {
  const opts = options && typeof options === "object" ? options : {};
  const applyLock = opts.applyLock === true;
  const period = String(opts.period || "");
  const pairs = resolveThreeDuishuPairs(candidate && candidate._pairs ? candidate._pairs : candidate && candidate.pairs);
  if (!pairs.length) return null;
  const scored = pairs.map((pair) => {
    const windows = threeDuishuComboRateMultiWindow(records, [pair], [30, MODEL_NEAR_WINDOW, 120, MODEL_STABLE_WINDOW]);
    const r30 = windows[30] || { total: 0, hit: 0, fail: 0, rate: null };
    const r40 = windows[MODEL_NEAR_WINDOW] || r30;
    const r120 = windows[120] || r40;
    const r300 = windows[MODEL_STABLE_WINDOW] || r120;
    const lower30 = wilsonLowerPercent(r30.hit, r30.total);
    const lower40 = wilsonLowerPercent(r40.hit, r40.total);
    const lower120 = wilsonLowerPercent(r120.hit, r120.total);
    const lower300 = wilsonLowerPercent(r300.hit, r300.total);
    const score = lower30 * 0.2 + lower40 * 0.25 + lower120 * 0.3 + lower300 * 0.15 + Number(r40.rate || 0) * 0.1;
    return {
      pair: pair.key,
      pairCode: pair.codes.join(""),
      rate30: r30.rate,
      rate40: r40.rate,
      rate120: r120.rate,
      rate300: r300.rate,
      lower30,
      lower40,
      lower120,
      lower300,
      sample30: r30.total,
      sample40: r40.total,
      sample120: r120.total,
      sample300: r300.total,
      fail30: r30.fail,
      fail40: r40.fail,
      score: Number(score.toFixed(4))
    };
  }).sort((left, right) => {
    if (right.score !== left.score) return right.score - left.score;
    if (right.lower120 !== left.lower120) return right.lower120 - left.lower120;
    if (Number(right.rate120 || 0) !== Number(left.rate120 || 0)) return Number(right.rate120 || 0) - Number(left.rate120 || 0);
    return left.pair.localeCompare(right.pair);
  });
  const primary = scored[0];
  if (!primary) return null;
  let selected = primary;
  let selectionReason = "pair-primary-initial";
  let holdCount = 1;
  if (applyLock) {
    if (!state.threeDuishuPairSelectionLock || typeof state.threeDuishuPairSelectionLock !== "object") {
      state.threeDuishuPairSelectionLock = {};
    }
    const lock = state.threeDuishuPairSelectionLock;
    const oldKey = String(lock.pairKey || "");
    const old = oldKey ? scored.find((item) => item.pair === oldKey) : null;
    const oldHold = Math.max(0, Number(lock.holdCount || 0));
    const samePeriod = String(lock.period || "") === period;
    if (old) {
      if (oldHold < THREE_DUISHU_MIN_HOLD) {
        selected = old;
        selectionReason = "pair-min-hold-keep-current";
      } else if (old.pair !== primary.pair) {
        const scoreMargin = Number(primary.score || 0) - Number(old.score || 0);
        if (scoreMargin >= THREE_DUISHU_SWITCH_MARGIN) {
          selected = primary;
          selectionReason = "pair-score-margin-switch";
        } else if (oldHold >= THREE_DUISHU_PAIR_MAX_HOLD) {
          selected = primary;
          selectionReason = "pair-max-hold-switch-primary";
        } else {
          selected = old;
          selectionReason = "pair-stable-keep-current";
        }
      } else if (oldHold >= THREE_DUISHU_PAIR_MAX_HOLD && scored.length > 1) {
        selected = scored[1];
        selectionReason = "pair-max-hold-rotate-runner-up";
      } else {
        selected = old;
        selectionReason = "pair-current-primary";
      }
    }
    holdCount = oldKey && selected.pair === oldKey
      ? (samePeriod ? Math.max(1, oldHold) : oldHold + 1)
      : 1;
    state.threeDuishuPairSelectionLock = {
      pairKey: selected.pair,
      pairCode: selected.pairCode,
      period,
      holdCount,
      reason: selectionReason,
      selectedScore: Number(selected.score || 0),
      primaryPair: primary.pair,
      primaryScore: Number(primary.score || 0),
      switchMargin: THREE_DUISHU_SWITCH_MARGIN,
      minHold: THREE_DUISHU_MIN_HOLD,
      maxHold: THREE_DUISHU_PAIR_MAX_HOLD,
      updatedAt: isoNow()
    };
  }
  return {
    pair: selected.pair,
    pairCode: selected.pairCode,
    score: selected.score,
    reason: `对子择一：${selected.pair}（每期复评，持有${holdCount}/${THREE_DUISHU_PAIR_MAX_HOLD}期）`,
    selectionReason,
    holdCount,
    minHold: THREE_DUISHU_MIN_HOLD,
    maxHold: THREE_DUISHU_PAIR_MAX_HOLD,
    locked: selected.pair !== primary.pair,
    primaryPair: primary.pair,
    primaryScore: primary.score,
    rate30: selected.rate30,
    rate40: selected.rate40,
    rate120: selected.rate120,
    rate300: selected.rate300,
    lower30: selected.lower30,
    lower40: selected.lower40,
    lower120: selected.lower120,
    lower300: selected.lower300,
    sample30: selected.sample30,
    sample40: selected.sample40,
    sample120: selected.sample120,
    sample300: selected.sample300,
    fail30: selected.fail30,
    fail40: selected.fail40,
    candidates: scored
  };
}

function preprocessThreeDuishuHistoryMasks(records, limit) {
  return (Array.isArray(records) ? records : [])
    .filter((record) => firstFourNumbers(record).length >= 4)
    .slice(0, Math.max(1, Number(limit || MODEL_RATE_WINDOW)))
    .map((record) => firstFourDigitMask(record))
    .filter((mask) => mask > 0);
}

function threeDuishuComboRateFromMasks(masks, pairMasks, limit) {
  const rows = (Array.isArray(masks) ? masks : []).slice(0, Math.max(1, Number(limit || MODEL_RATE_WINDOW)));
  if (!rows.length) return { total: 0, hit: 0, fail: 0, rate: null, streak: 0 };
  let hit = 0;
  let fail = 0;
  rows.forEach((mask) => {
    const recordHits = pairMasks.some((pairMask) => (mask & pairMask) === pairMask);
    if (recordHits) fail += 1;
    else hit += 1;
  });
  let streak = 0;
  for (const mask of rows) {
    if (pairMasks.some((pairMask) => (mask & pairMask) === pairMask)) break;
    streak += 1;
  }
  return {
    total: rows.length,
    hit,
    fail,
    rate: Number(((hit / rows.length) * 100).toFixed(2)),
    streak
  };
}

function threeDuishuComboRateMultiWindow(records, pairs, limits) {
  const uniqueLimits = [...new Set((Array.isArray(limits) ? limits : [limits]).map((limit) => Math.max(1, Number(limit || MODEL_RATE_WINDOW))))];
  const maxLimit = Math.max(...uniqueLimits);
  const masks = preprocessThreeDuishuHistoryMasks(records, maxLimit);
  const pairMasks = (Array.isArray(pairs) ? pairs : []).map((pair) => threeDuishuPairMask(pair)).filter((mask) => mask > 0);
  const out = {};
  uniqueLimits.forEach((limit) => {
    out[limit] = threeDuishuComboRateFromMasks(masks, pairMasks, limit);
  });
  return out;
}

function threeDuishuComboRate(records, pairs, limit) {
  const masks = preprocessThreeDuishuHistoryMasks(records, limit);
  if (!masks.length) return { total: 0, hit: 0, fail: 0, rate: null, streak: 0 };
  const pairMasks = (Array.isArray(pairs) ? pairs : []).map((pair) => threeDuishuPairMask(pair)).filter((mask) => mask > 0);
  return threeDuishuComboRateFromMasks(masks, pairMasks, masks.length);
}

function wilsonLowerPercent(hit, total, z) {
  if (!total) return 0;
  const confidence = Number(z || 1.281551565545);
  const p = hit / total;
  const z2 = confidence * confidence;
  const denom = 1 + z2 / total;
  const center = p + z2 / (2 * total);
  const margin = confidence * Math.sqrt((p * (1 - p) + z2 / (4 * total)) / total);
  return Number((Math.max(0, (center - margin) / denom) * 100).toFixed(2));
}

function threeDuishuComboBlockStats(records, pairs, windowSize, blockSize) {
  const rows = (Array.isArray(records) ? records : [])
    .filter((record) => firstFourNumbers(record).length >= 4)
    .slice(0, Math.max(1, Number(windowSize || THREE_DUISHU_BLOCK_WINDOW)));
  const size = Math.max(1, Number(blockSize || THREE_DUISHU_BLOCK_SIZE));
  const blocks = [];
  for (let start = 0; start < rows.length; start += size) {
    const blockRows = rows.slice(start, start + size);
    if (!blockRows.length) continue;
    const result = threeDuishuComboRate(blockRows, pairs, blockRows.length);
    if (result.total) {
      blocks.push({
        index: blocks.length + 1,
        total: result.total,
        hit: result.hit,
        fail: result.fail,
        rate: result.rate
      });
    }
  }
  if (!blocks.length) {
    return {
      window: rows.length,
      blockSize: size,
      blockCount: 0,
      minRate: null,
      avgRate: null,
      maxRate: null,
      variance: 0,
      weakBlocks: 0,
      blocks: []
    };
  }
  const rates = blocks.map((block) => Number(block.rate || 0));
  const sum = rates.reduce((acc, rate) => acc + rate, 0);
  const avg = sum / rates.length;
  const variance = rates.reduce((acc, rate) => acc + Math.pow(rate - avg, 2), 0) / rates.length;
  return {
    window: rows.length,
    blockSize: size,
    blockCount: blocks.length,
    minRate: Number(Math.min(...rates).toFixed(2)),
    avgRate: Number(avg.toFixed(2)),
    maxRate: Number(Math.max(...rates).toFixed(2)),
    variance: Number(Math.sqrt(variance).toFixed(2)),
    weakBlocks: blocks.filter((block) => Number(block.rate || 0) < THREE_DUISHU_BLOCK_FLOOR).length,
    floor: THREE_DUISHU_BLOCK_FLOOR,
    blocks
  };
}

function threeDuishuPairHealth(records, pairs, limit) {
  const rows = (Array.isArray(records) ? records : [])
    .filter((record) => firstFourNumbers(record).length >= 4)
    .slice(0, Math.max(1, Number(limit || 120)));
  const details = (Array.isArray(pairs) ? pairs : []).map((pair) => {
    const result = threeDuishuComboRate(rows, [pair], rows.length || 1);
    return {
      pair: pair.key,
      rate: result.rate,
      lower: wilsonLowerPercent(result.hit, result.total),
      fail: result.fail,
      total: result.total
    };
  });
  if (!details.length) {
    return { limit: rows.length, minRate: null, avgRate: null, minLower: null, details: [] };
  }
  const rates = details.map((item) => Number(item.rate || 0));
  const lowers = details.map((item) => Number(item.lower || 0));
  return {
    limit: rows.length,
    minRate: Number(Math.min(...rates).toFixed(2)),
    avgRate: Number((rates.reduce((sum, rate) => sum + rate, 0) / rates.length).toFixed(2)),
    minLower: Number(Math.min(...lowers).toFixed(2)),
    details
  };
}

function chooseThreeDuishuDisplayCode(records, pair) {
  const codes = Array.isArray(pair && pair.codes) ? pair.codes.map(String) : [];
  if (codes.length >= 2) return codes[1];
  return codes[0] || "";
}

function publicThreeDuishuCandidate(candidate) {
  if (!candidate) return null;
  const out = Object.assign({}, candidate);
  delete out._pairs;
  return out;
}

const scoreThreeDuishuCandidatesLru = new Map();
const SCORE_THREE_DUISHU_LRU_MAX = Number(process.env.SCORE_THREE_DUISHU_LRU_MAX || 48);

function scoreThreeDuishuCandidatesCacheKey(history) {
  const rows = (Array.isArray(history) ? history : []).filter((record) => firstFourNumbers(record).length >= 4);
  const head = rows[0];
  const tail = rows[rows.length - 1];
  const recentPeriods = rows.slice(0, 3).map((row) => (row && row.period != null ? String(row.period) : "")).join(",");
  const stableSig = Array.from(getStableMathExcludeDigitSet()).sort().join("");
  return [
    rows.length,
    head && head.uid ? head.uid : "",
    head && Array.isArray(head.numbers) ? head.numbers.slice(0, 4).join("") : "",
    tail && tail.uid ? tail.uid : "",
    tail && Array.isArray(tail.numbers) ? tail.numbers.slice(0, 4).join("") : "",
    recentPeriods,
    stableSig
  ].join("|");
}

function scoreThreeDuishuCandidatesCore(history) {
  const candidates = [];
  const stableExclude = getStableMathExcludeDigitSet();
  for (let a = 0; a < THREE_DUISHU_PAIRS.length - 2; a += 1) {
    for (let b = a + 1; b < THREE_DUISHU_PAIRS.length - 1; b += 1) {
      for (let c = b + 1; c < THREE_DUISHU_PAIRS.length; c += 1) {
        const pairs = [THREE_DUISHU_PAIRS[a], THREE_DUISHU_PAIRS[b], THREE_DUISHU_PAIRS[c]];
        const rateWindows = threeDuishuComboRateMultiWindow(history, pairs, [30, MODEL_NEAR_WINDOW, 60, 120, MODEL_STABLE_WINDOW]);
        const r30 = rateWindows[30];
        const r40 = rateWindows[MODEL_NEAR_WINDOW];
        const r60 = rateWindows[60];
        const r120 = rateWindows[120];
        const r300 = rateWindows[MODEL_STABLE_WINDOW];
        const block = threeDuishuComboBlockStats(history, pairs, THREE_DUISHU_BLOCK_WINDOW, THREE_DUISHU_BLOCK_SIZE);
        const pairHealth = threeDuishuPairHealth(history, pairs, 120);
        const lower30 = wilsonLowerPercent(r30.hit, r30.total);
        const lower40 = wilsonLowerPercent(r40.hit, r40.total);
        const lower60 = wilsonLowerPercent(r60.hit, r60.total);
        const lower120 = wilsonLowerPercent(r120.hit, r120.total);
        const lower300 = wilsonLowerPercent(r300.hit, r300.total);
        const shortRate = Number(r40.rate != null ? r40.rate : r30.rate || 0);
        const midRate = Number(r120.rate || r60.rate || 0);
        const longRate = Number(r300.rate || midRate || 0);
        const blockMinRate = Number(block.minRate || midRate || 0);
        const blockAvgRate = Number(block.avgRate || midRate || 0);
        const pairMinLower = Number(pairHealth.minLower || 0);
        const instability = Math.max(
          Math.abs(shortRate - Number(r60.rate || shortRate)),
          Math.abs(Number(r60.rate || shortRate) - midRate),
          Math.abs(midRate - longRate)
        );
        const displayDigits = pairs.map((pair) => chooseThreeDuishuDisplayCode(history, pair)).sort((left, right) => Number(left) - Number(right));
        const code = displayDigits.join("");
        const stableMathOverlap = displayDigits.reduce((count, digit) => count + (stableExclude.has(String(digit)) ? 1 : 0), 0);
        let softRiskScore = 0;
        let softRiskLevel = "low";
        if (typeof stableMathFilterBridge.scoreCombination === "function") {
          const risk = stableMathFilterBridge.scoreCombination(String(code).slice(0, 4));
          softRiskScore = Number(risk && risk.riskScore || 0);
          softRiskLevel = (risk && risk.riskLevel) || "low";
        }
        const recentFailPenalty = Number((r40.fail != null ? r40.fail : r30.fail) || 0) * 0.28 + Math.max(0, 3 - Number((r40.streak != null ? r40.streak : r30.streak) || 0)) * 0.6;
        const weakBlockPenalty = Number(block.weakBlocks || 0) * 0.9;
        const softRiskPenalty = softRiskScore * 0.45 + (softRiskLevel === "high" ? 1.2 : softRiskLevel === "mid" ? 0.4 : 0);
        const score =
          lower30 * 0.12 +
          lower40 * 0.12 +
          lower60 * 0.14 +
          lower120 * 0.22 +
          lower300 * 0.12 +
          blockMinRate * 0.16 +
          blockAvgRate * 0.07 +
          pairMinLower * 0.05 -
          instability * 0.22 -
          Number(block.variance || 0) * 0.03 -
          recentFailPenalty -
          weakBlockPenalty +
          stableMathOverlap * 1.25 -
          softRiskPenalty +
          Math.min(Number((r40.streak != null ? r40.streak : r30.streak) || 0), 10) * 0.1;
        const pairKey = pairs.map((pair) => pair.key).join("|");
        candidates.push({
          _pairs: pairs,
          value: pairKey,
          pairKey,
          code,
          shot: threeDuishuShotText(pairs),
          pairs: pairs.map((pair) => pair.key),
          pairCodes: pairs.map((pair) => pair.codes.join("")),
          rate30: r30.rate,
          rate40: r40.rate,
          rate60: r60.rate,
          rate120: r120.rate,
          rate300: r300.rate,
          lower30,
          lower40,
          lower60,
          lower120,
          lower300,
          sample30: r30.total,
          sample40: r40.total,
          sample120: r120.total,
          fail30: r30.fail,
          fail40: r40.fail,
          streak30: r30.streak,
          streak40: r40.streak,
          instability: Number(instability.toFixed(2)),
          block,
          pairHealth,
          stableMathOverlap,
          recentFailPenalty: Number(recentFailPenalty.toFixed(2)),
          weakBlockPenalty: Number(weakBlockPenalty.toFixed(2)),
          score: Number(score.toFixed(4))
        });
      }
    }
  }
  candidates.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (Number(b.block && b.block.minRate || 0) !== Number(a.block && a.block.minRate || 0)) return Number(b.block && b.block.minRate || 0) - Number(a.block && a.block.minRate || 0);
    if (Number(b.lower120 || 0) !== Number(a.lower120 || 0)) return Number(b.lower120 || 0) - Number(a.lower120 || 0);
    if (Number(b.rate30 || 0) !== Number(a.rate30 || 0)) return Number(b.rate30 || 0) - Number(a.rate30 || 0);
    if (Number(b.rate120 || 0) !== Number(a.rate120 || 0)) return Number(b.rate120 || 0) - Number(a.rate120 || 0);
    return a.code.localeCompare(b.code);
  });
  return candidates;
}

function scoreThreeDuishuCandidates(history) {
  const cacheKey = scoreThreeDuishuCandidatesCacheKey(history);
  if (scoreThreeDuishuCandidatesLru.has(cacheKey)) {
    return scoreThreeDuishuCandidatesLru.get(cacheKey);
  }
  const candidates = scoreThreeDuishuCandidatesCore(history);
  scoreThreeDuishuCandidatesLru.set(cacheKey, candidates);
  if (scoreThreeDuishuCandidatesLru.size > SCORE_THREE_DUISHU_LRU_MAX) {
    const oldest = scoreThreeDuishuCandidatesLru.keys().next().value;
    scoreThreeDuishuCandidatesLru.delete(oldest);
  }
  return candidates;
}

function walkForwardThreeDuishu(history) {
  const rows = (Array.isArray(history) ? history : []).filter((record) => firstFourNumbers(record).length >= 4);
  const maxTests = Math.max(0, Math.min(THREE_DUISHU_WALK_WINDOW, rows.length - THREE_DUISHU_MIN_TRAIN));
  let hit = 0;
  let fail = 0;
  let streak = 0;
  const recent = [];
  for (let index = 0; index < maxTests; index += 1) {
    // 只用当期开奖前的更早数据训练，禁止偷看当期/未来
    const train = rows.slice(index + 1);
    if (train.length < THREE_DUISHU_MIN_TRAIN) continue;
    const selected = scoreThreeDuishuCandidates(train)[0];
    if (!selected) continue;
    const pairSelection = selectThreeDuishuPair(train, selected);
    if (!pairSelection) continue;
    const success = !threeDuishuRecordHits(rows[index], [pairSelection.pair]);
    if (success) {
      hit += 1;
    } else {
      fail += 1;
    }
    recent.push({
      period: rows[index].period || "",
      uid: rows[index].uid || "",
      code: selected.code,
      pairs: selected.pairs,
      shot: selected.shot,
      selectedPair: pairSelection.pair,
      selectedPairCode: pairSelection.pairCode,
      success
    });
  }
  for (const item of recent) {
    if (!item.success) break;
    streak += 1;
  }
  const total = hit + fail;
  const insufficient = total < MODEL_MIN_SAMPLE;
  // walk-forward 会按不同 train 长度灌满 LRU；算完立刻清掉，避免跨请求残留大候选数组
  try { scoreThreeDuishuCandidatesLru.clear(); } catch (_) {}
  return {
    total,
    hit,
    fail,
    rate: insufficient ? null : (total ? Number(((hit / total) * 100).toFixed(2)) : null),
    rateText: insufficient ? "数据不足" : (total ? `${Number(((hit / total) * 100).toFixed(2))}%` : "暂无命中记录"),
    lower: wilsonLowerPercent(hit, total),
    streak,
    minTrain: THREE_DUISHU_MIN_TRAIN,
    window: THREE_DUISHU_WALK_WINDOW,
    recent: recent.slice(0, 40),
    sampleInsufficient: insufficient
  };
}

function selectStableThreeDuishuCandidate(candidates, period, options) {
  const opts = options && typeof options === "object" ? options : {};
  const applyLock = opts.applyLock !== false;
  const list = (Array.isArray(candidates) ? candidates : []).filter(Boolean);
  const primary = list[0] || null;
  if (!primary) {
    return { selected: null, primary: null, locked: false, reason: "empty", holdCount: 0 };
  }
  const keyOf = (candidate) => String(candidate && (candidate.pairKey || candidate.value || candidate.code) || "");
  const scoreOf = (candidate) => {
    const score = Number(candidate && candidate.score);
    return Number.isFinite(score) ? score : 0;
  };
  if (!state.threeDuishuSelectionLock || typeof state.threeDuishuSelectionLock !== "object") {
    state.threeDuishuSelectionLock = {};
  }
  const lock = applyLock ? state.threeDuishuSelectionLock : Object.assign({}, state.threeDuishuSelectionLock);
  const primaryKey = keyOf(primary);
  const oldKey = String(lock.pairKey || "");
  const old = oldKey ? list.find((candidate) => keyOf(candidate) === oldKey) : null;
  const oldHold = Number(lock.holdCount || 0);
  const samePeriod = String(lock.period || "") === String(period || "");
  let selected = primary;
  let reason = oldKey ? "primary-clearly-better" : "primary-initial";

  if (old && oldKey !== primaryKey) {
    const primaryScore = scoreOf(primary);
    const oldScore = scoreOf(old);
    const scoreMargin = primaryScore - oldScore;
    const primaryBlockMin = Number(primary.block && primary.block.minRate || 0);
    const oldBlockMin = Number(old.block && old.block.minRate || 0);
    const primaryLower120 = Number(primary.lower120 || 0);
    const oldLower120 = Number(old.lower120 || 0);
    const primaryWeakBlocks = Number(primary.block && primary.block.weakBlocks || 0);
    const oldWeakBlocks = Number(old.block && old.block.weakBlocks || 0);
    const blockImproved = primaryBlockMin - oldBlockMin >= THREE_DUISHU_SWITCH_MARGIN && primaryWeakBlocks <= oldWeakBlocks;
    const lowerImproved = primaryLower120 - oldLower120 >= THREE_DUISHU_SWITCH_MARGIN;
    const forceSwitch = scoreMargin >= THREE_DUISHU_SWITCH_MARGIN || (blockImproved && lowerImproved);
    if (oldHold < THREE_DUISHU_MIN_HOLD && scoreMargin < THREE_DUISHU_SWITCH_MARGIN * 1.6) {
      selected = old;
      reason = "min-hold-keep-current";
    } else if (!forceSwitch) {
      selected = old;
      reason = "stable-lock-keep-current";
    }
  } else if (old && oldKey === primaryKey) {
    selected = old;
    reason = "stable-current-primary";
  }

  const selectedKey = keyOf(selected);
  const holdCount = oldKey && selectedKey === oldKey ? (samePeriod ? Math.max(1, oldHold) : oldHold + 1) : 1;
  const nextLock = {
    pairKey: selectedKey,
    code: selected.code,
    period: String(period || ""),
    holdCount,
    reason,
    selectedScore: scoreOf(selected),
    primaryKey,
    primaryCode: primary.code,
    primaryScore: scoreOf(primary),
    switchMargin: THREE_DUISHU_SWITCH_MARGIN,
    minHold: THREE_DUISHU_MIN_HOLD,
    updatedAt: isoNow()
  };
  if (applyLock) state.threeDuishuSelectionLock = nextLock;
  return {
    selected,
    primary,
    locked: selectedKey !== primaryKey,
    reason,
    holdCount
  };
}

function threeDuishuQualityLabel(candidate, walkForward) {
  if (!candidate) return "无数据";
  const walkRate = Number(walkForward && walkForward.rate || 0);
  const blockMin = Number(candidate.block && candidate.block.minRate || 0);
  const lower120 = Number(candidate.lower120 || 0);
  const weakBlocks = Number(candidate.block && candidate.block.weakBlocks || 0);
  if (walkRate >= 70 && blockMin >= 60 && lower120 >= 58 && weakBlocks === 0) return "稳固";
  if (walkRate >= 62 && blockMin >= THREE_DUISHU_BLOCK_FLOOR && lower120 >= 52) return "可用";
  return "观察";
}

function threeDuishuHistorySignature(history, period) {
  const rows = Array.isArray(history) ? history : [];
  const first = rows[0];
  return [
    THREE_DUISHU_SNAPSHOT_VERSION,
    String(period || ""),
    rows.length,
    first && first.uid ? first.uid : "",
    first && Array.isArray(first.numbers) ? first.numbers.slice(0, 4).join("") : ""
  ].join("|");
}

function buildThreeDuishuRecommendationPayload(history, period) {
  const candidates = scoreThreeDuishuCandidates(history);
  const selected = selectStableThreeDuishuCandidate(candidates, period, { applyLock: true });
  const best = selected.selected || candidates[0];
  if (!best) {
    return { ok: false, code: "", shot: "", pairs: [], error: "no candidate" };
  }
  const pairSelection = selectThreeDuishuPair(history, best, { applyLock: true, period });
  if (!pairSelection) {
    return { ok: false, code: "", shot: "", pairs: [], error: "no pair candidate" };
  }
  const walkForward = walkForwardThreeDuishu(history);
  const quality = threeDuishuQualityLabel(best, walkForward);
  const recommendable = quality !== "观察";
  const confidence = quality === "稳固" ? "high" : (quality === "可用" ? "normal" : "low");
  return {
    ok: true,
    period,
    latestNumbers: state.latestRecord && Array.isArray(state.latestRecord.numbers) ? state.latestRecord.numbers.slice(0, 5) : [],
    code: best.code,
    shot: threeDuishuShotText([pairSelection.pair]),
    pairs: best.pairs,
    pairCodes: best.pairCodes,
    selectedPair: pairSelection.pair,
    selectedPairCode: pairSelection.pairCode,
    pairSelection,
    rate30: best.rate30,
    rate40: best.rate40,
    rate60: best.rate60,
    rate120: best.rate120,
    rate300: best.rate300,
    sample30: best.sample30,
    sample40: best.sample40,
    sample120: best.sample120,
    fail30: best.fail30,
    fail40: best.fail40,
    streak30: best.streak30,
    streak40: best.streak40,
    lower30: best.lower30,
    lower40: best.lower40,
    lower120: best.lower120,
    instability: best.instability,
    block: best.block,
    pairHealth: best.pairHealth,
    selectedReason: selected.reason,
    selectedLocked: selected.locked,
    selectedHoldCount: selected.holdCount,
    primaryCode: selected.primary && selected.primary.code,
    primaryPairKey: selected.primary && selected.primary.pairKey,
    score: best.score,
    walkForward,
    quality,
    confidence,
    recommendable,
    stableMathOverlap: Number(best.stableMathOverlap || 0),
    candidates: candidates.slice(0, 5).map(publicThreeDuishuCandidate)
  };
}

function recomputeThreeDuishuRecommendation() {
  const history = (Array.isArray(state && state.history) ? state.history : []).filter((record) => firstFourNumbers(record).length >= 4);
  const period = state.latestRecord && state.latestRecord.period ? state.latestRecord.period : "";
  const signature = threeDuishuHistorySignature(history, period);
  if (!history.length) {
    const empty = { ok: false, code: "", shot: "", pairs: [], error: "history is empty", signature, computedAt: isoNow() };
    state.threeDuishuRecommendationCache = empty;
    return empty;
  }
  const payload = buildThreeDuishuRecommendationPayload(history, period);
  state.threeDuishuRecommendationCache = Object.assign({ signature, computedAt: isoNow() }, payload);
  invalidatePanelStateCache();
  return state.threeDuishuRecommendationCache;
}

function getThreeDuishuRecommendation() {
  const history = (Array.isArray(state && state.history) ? state.history : []).filter((record) => firstFourNumbers(record).length >= 4);
  const period = state.latestRecord && state.latestRecord.period ? state.latestRecord.period : "";
  const signature = threeDuishuHistorySignature(history, period);
  const cached = state && state.threeDuishuRecommendationCache;
  if (cached && cached.signature === signature) {
    const out = Object.assign({}, cached);
    delete out.signature;
    delete out.computedAt;
    return out;
  }
  const fresh = recomputeThreeDuishuRecommendation();
  const out = Object.assign({}, fresh);
  delete out.signature;
  delete out.computedAt;
  return out;
}

function buildThreeDuishuRecommendation() {
  return getThreeDuishuRecommendation();
}

function threeDuishuSnapshotIsComplete(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return false;
  if (String(snapshot.version || "") !== THREE_DUISHU_SNAPSHOT_VERSION) return false;
  const pairs = (Array.isArray(snapshot.pairs) ? snapshot.pairs : []).map(String).filter((key) => /^\d{2}$/.test(key));
  const code = String(snapshot.code || "");
  const selectedPair = String(snapshot.selectedPair || "");
  return pairs.length === 3 && /^\d{3}$/.test(code) && /^\d{2}$/.test(selectedPair) && pairs.includes(selectedPair);
}

function buildThreeDuishuSnapshot(prediction) {
  const rec = getThreeDuishuRecommendation();
  if (!rec || !rec.ok || !rec.code) return null;
  const pairs = (Array.isArray(rec.pairs) ? rec.pairs : []).map(String).filter((key) => /^\d{2}$/.test(key)).slice(0, 3);
  if (pairs.length !== 3) return null;
  const selectedPair = String(rec.selectedPair || "");
  if (!/^\d{2}$/.test(selectedPair) || !pairs.includes(selectedPair)) return null;
  const pairSelection = rec.pairSelection && typeof rec.pairSelection === "object" ? rec.pairSelection : {};
  return {
    version: THREE_DUISHU_SNAPSHOT_VERSION,
    kind: "threeDuishu",
    key: "threeDuishu",
    side: "recommend",
    name: "三对数排除",
    code: String(rec.code || ""),
    shot: threeDuishuShotText([selectedPair]),
    pairs,
    pairCodes: (Array.isArray(rec.pairCodes) ? rec.pairCodes : pairs.slice()).map(String).slice(0, 3),
    selectedPair,
    selectedPairCode: String(rec.selectedPairCode || selectedPair),
    quality: rec.quality || "",
    confidence: rec.confidence || "normal",
    recommendable: rec.recommendable !== false,
    rate30: pairSelection.rate30,
    rate40: pairSelection.rate40,
    rate120: pairSelection.rate120,
    rate300: pairSelection.rate300,
    sample40: pairSelection.sample40,
    walkForwardRate: rec.walkForward && rec.walkForward.rate,
    stableMathOverlap: Number(rec.stableMathOverlap || 0),
    selectedReason: rec.selectedReason || "",
    pairSelectionReason: pairSelection.reason || "",
    pairSelectionMode: "min3-max8-periodic-rotation",
    pairSelectionHoldCount: Number(pairSelection.holdCount || 0),
    pairSelectionMaxHold: Number(pairSelection.maxHold || THREE_DUISHU_PAIR_MAX_HOLD),
    selectedLocked: !!rec.selectedLocked,
    sealed: false
  };
}

function ensurePredictionThreeDuishuSnapshot(prediction) {
  if (!prediction || typeof prediction !== "object") return null;
  // 一旦三对数码生成完整就冻结：只允许后续 seal，禁止未封存时被重算覆盖
  if (threeDuishuSnapshotIsComplete(prediction.threeDuishu)) {
    return prediction.threeDuishu;
  }
  const snapshot = buildThreeDuishuSnapshot(prediction);
  if (!snapshot) return prediction.threeDuishu || null;
  prediction.threeDuishu = snapshot;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.threeDuishuSnapshotVersion = THREE_DUISHU_SNAPSHOT_VERSION;
  return prediction.threeDuishu;
}

const DUISHU_PAIR_AVOID_KEYS = ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"];

function frameworkHasDuishuPair(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  return items.some((item) => {
    if (!item || item.side === "backup" || item.kind !== "extra") return false;
    if (!DUISHU_PAIR_AVOID_KEYS.includes(String(item.key || ""))) return false;
    return /^\d{2}$/.test(String(item.code || ""));
  });
}

function resolveDuishuFallbackPair(prediction) {
  const fromThree = String(prediction && prediction.threeDuishu && prediction.threeDuishu.selectedPair || "");
  if (/^\d{2}$/.test(fromThree)) return fromThree;
  const extras = (prediction && prediction.extraKills) ||
    (prediction && prediction.detail && prediction.detail.extraKills) ||
    {};
  for (let index = 0; index < DUISHU_PAIR_AVOID_KEYS.length; index += 1) {
    const item = extras[DUISHU_PAIR_AVOID_KEYS[index]];
    const value = item && typeof item === "object"
      ? String(item.value || item.code || item.killDigit || "")
      : String(item || "");
    if (/^\d{2}$/.test(value)) return value;
  }
  const cache = state && state.threeDuishuRecommendationCache;
  const cachedPair = String(cache && cache.selectedPair || "");
  return /^\d{2}$/.test(cachedPair) ? cachedPair : "";
}

function buildDuishuFallbackItem(pair, displayName) {
  return {
    kind: "extra",
    key: "twoDigitAvoid",
    side: "recommend",
    name: displayName || "对数避开1组",
    code: String(pair),
    mode: "avoid",
    derivedFrom: "duishu-auto-repair-fallback",
    repairedIntoTop: true
  };
}

function collectDuishuCaptureIssues(prediction) {
  const issues = [];
  const current = prediction && typeof prediction === "object" ? prediction : null;
  if (!current) return issues;
  if (!threeDuishuSnapshotIsComplete(current.threeDuishu)) {
    issues.push({
      type: "duishu-capture-missing",
      key: "snapshot.threeDuishu",
      label: "三对数未抓取",
      action: "repair-duishu-capture",
      severity: "alarm",
      detail: current.threeDuishu ? "incomplete" : "missing"
    });
  }
  [
    ["recommend2", "推荐2缺对数"],
    ["threeDingTwo", "三定A缺对数"],
    ["threeDingOne", "三定B缺对数"]
  ].forEach(([name, label]) => {
    const snap = current[name];
    if (!snap || typeof snap !== "object") return;
    if (frameworkHasDuishuPair(snap)) return;
    issues.push({
      type: "duishu-capture-missing",
      key: `snapshot.${name}.duishu`,
      label,
      action: "repair-duishu-capture",
      severity: "alarm",
      detail: "pair-missing"
    });
  });
  return issues;
}

function forceOpenPredictionSealGate(prediction) {
  if (!prediction || typeof prediction !== "object") return;
  prediction.snapshotsSealedAt = "";
  prediction.snapshotsSealedBaseUid = "";
  prediction.allSnapshotsSealed = false;
}

function injectDuishuPairIntoSnapshot(prediction, snapshot, displayName) {
  if (!snapshot || !Array.isArray(snapshot.items) || frameworkHasDuishuPair(snapshot)) return false;
  const pair = resolveDuishuFallbackPair(prediction);
  if (!/^\d{2}$/.test(pair)) return false;
  snapshot.items = snapshot.items.concat([buildDuishuFallbackItem(pair, displayName)]);
  snapshot.repairReport = Array.isArray(snapshot.repairReport) ? snapshot.repairReport.slice() : [];
  snapshot.repairReport.push(`自动修复：补抓对数${pair}`);
  snapshot.sealed = false;
  snapshot.sealHash = "";
  return true;
}

function repairDuishuCapture(actions) {
  const list = Array.isArray(actions) ? actions : [];
  const prediction = state && state.currentPrediction;
  const history = state && Array.isArray(state.history) ? state.history : [];
  if (!prediction || typeof prediction !== "object") {
    list.push({ type: "repair-duishu-skip", reason: "no-prediction" });
    return false;
  }
  if (prediction.evaluated === true) {
    list.push({ type: "repair-duishu-skip", reason: "already-evaluated" });
    return false;
  }
  if (!history.length) {
    list.push({ type: "repair-duishu-skip", reason: "history-empty" });
    return false;
  }

  let beforeIssues = collectDuishuCaptureIssues(prediction);
  const tipDuishuIssues = collectPredictionIntegrityIssues(prediction, history[0] || state.latestRecord)
    .filter((issue) => issue && issue.action === "repair-duishu-capture");
  tipDuishuIssues.forEach((issue) => {
    if (!beforeIssues.some((row) => row && row.key === issue.key)) {
      beforeIssues.push(Object.assign({ severity: "alarm" }, issue));
    }
  });
  if (!beforeIssues.length) return false;

  addLog("【报警】对数抓取缺失，开始自动修复", {
    baseUid: prediction.baseUid,
    basePeriod: prediction.basePeriod,
    targetPeriod: prediction.targetPeriod,
    issues: beforeIssues.slice(0, 12)
  });

  const repairs = [];
  let changed = false;

  if (!threeDuishuSnapshotIsComplete(prediction.threeDuishu)) {
    if (prediction.threeDuishu && typeof prediction.threeDuishu === "object") {
      prediction.threeDuishu.sealed = false;
      prediction.threeDuishu.sealHash = "";
    }
    prediction.threeDuishu = null;
    forceOpenPredictionSealGate(prediction);
    recomputeThreeDuishuRecommendation();
    ensurePredictionThreeDuishuSnapshot(prediction);
    if (!threeDuishuSnapshotIsComplete(prediction.threeDuishu)) {
      recomputeThreeDuishuRecommendation();
      ensurePredictionThreeDuishuSnapshot(prediction);
    }
    const ok = threeDuishuSnapshotIsComplete(prediction.threeDuishu);
    repairs.push({
      target: "threeDuishu",
      ok,
      pair: ok ? String(prediction.threeDuishu.selectedPair || "") : ""
    });
    changed = true;
  }

  const extras = prediction.extraKills || (prediction.detail && prediction.detail.extraKills);
  if (extras && typeof extras === "object") {
    const missingAll = DUISHU_PAIR_AVOID_KEYS.every((key) => {
      const item = extras[key];
      const value = item && typeof item === "object"
        ? String(item.value || item.code || "")
        : String(item || "");
      return !/^\d{2}$/.test(value);
    });
    if (missingAll) {
      try {
        const modelPerformance = calculateModelPerformance(Array.isArray(state.predictions) ? state.predictions : []);
        const fresh = calculateExtraAvoidKills(history, modelPerformance);
        DUISHU_PAIR_AVOID_KEYS.forEach((key) => {
          if (fresh && fresh[key]) extras[key] = fresh[key];
        });
        if (prediction.detail && typeof prediction.detail === "object") prediction.detail.extraKills = extras;
        prediction.extraKills = extras;
        repairs.push({ target: "extraKills.twoDigit*", ok: true });
        changed = true;
      } catch (err) {
        repairs.push({
          target: "extraKills.twoDigit*",
          ok: false,
          error: err && err.message ? err.message : String(err)
        });
      }
    }
  }

  function rebuildFramework(name, ensureFn, displayName) {
    const snap = prediction[name];
    if (snap && frameworkHasDuishuPair(snap)) return;

    forceOpenPredictionSealGate(prediction);
    if (snap && typeof snap === "object" && snap.sealed === true) {
      snap.sealed = false;
      snap.sealHash = "";
    }
    prediction[name] = null;
    ensureFn(prediction, history);
    let rebuilt = prediction[name];
    if (rebuilt && !frameworkHasDuishuPair(rebuilt)) {
      injectDuishuPairIntoSnapshot(prediction, rebuilt, displayName);
      if (name === "recommend2") prediction.recommend2Items = rebuilt.items;
      if (name === "threeDingTwo") prediction.threeDingTwoItems = rebuilt.items;
      if (name === "threeDingOne") prediction.threeDingOneItems = rebuilt.items;
    }
    const ok = frameworkHasDuishuPair(prediction[name]);
    const pairItem = ok
      ? (prediction[name].items || []).find((item) => item && DUISHU_PAIR_AVOID_KEYS.includes(String(item.key || "")))
      : null;
    repairs.push({
      target: name,
      ok,
      pair: pairItem ? String(pairItem.code || "") : ""
    });
    changed = true;
  }

  const needsRecommend2 = beforeIssues.some((issue) => String(issue.key || "").indexOf("recommend2") >= 0);
  const needsThreeDingTwo = false; // retired
  const needsThreeDingOne = false; // retired
  if (needsRecommend2 || (prediction.recommend2 && !frameworkHasDuishuPair(prediction.recommend2))) {
    rebuildFramework("recommend2", ensurePredictionRecommend2Snapshot, "对数2位避开");
  }
  if (needsThreeDingTwo || (prediction.threeDingTwo && !frameworkHasDuishuPair(prediction.threeDingTwo))) {
    rebuildFramework("threeDingTwo", ensurePredictionThreeDingTwoSnapshot, "对数避开1组");
  }
  if (needsThreeDingOne || (prediction.threeDingOne && !frameworkHasDuishuPair(prediction.threeDingOne))) {
    rebuildFramework("threeDingOne", ensurePredictionThreeDingOneSnapshot, "对数避开1组");
  }

  if (!changed) {
    list.push({ type: "repair-duishu-skip", reason: "no-mutable-target", issues: beforeIssues.slice(0, 8) });
    return false;
  }

  sealPredictionSnapshots(prediction, history[0]);
  invalidatePanelStateCache();
  const remaining = collectDuishuCaptureIssues(prediction);
  const resolved = remaining.length === 0;
  state.duishuCaptureAlarm = {
    at: isoNow(),
    baseUid: String(prediction.baseUid || ""),
    basePeriod: String(prediction.basePeriod || ""),
    beforeCount: beforeIssues.length,
    before: beforeIssues.slice(0, 12),
    repairs: repairs.slice(0, 12),
    remaining: remaining.slice(0, 12),
    resolved
  };
  addLog(resolved ? "【报警】对数抓取已自动修复" : "【报警】对数自动修复未完全成功，继续观察", {
    baseUid: prediction.baseUid,
    basePeriod: prediction.basePeriod,
    repairs,
    remaining: remaining.slice(0, 12),
    resolved
  });
  list.push({
    type: "repair-duishu-capture",
    repairs,
    remaining: remaining.length,
    resolved
  });
  return resolved;
}

function evaluateThreeDuishuSnapshot(prediction, actualRecord) {
  const snapshot = prediction && prediction.threeDuishu;
  const truthEligible = threeDuishuSnapshotIsComplete(snapshot) &&
    snapshotSealIsValid(snapshot, prediction) &&
    snapshotSealPredatesTarget(snapshot, actualRecord) &&
    predictionPredatesTarget(prediction, actualRecord);
  if (!truthEligible) {
    return {
      ok: false,
      success: null,
      passed: 0,
      total: 0,
      version: snapshot && snapshot.version || THREE_DUISHU_SNAPSHOT_VERSION,
      reason: "unsealed-or-incomplete-three-duishu-snapshot",
      truthEligible: false
    };
  }
  const selectedPair = String(snapshot.selectedPair || "");
  const success = !threeDuishuRecordHits(actualRecord, [selectedPair]);
  return {
    ok: true,
    success,
    passed: success ? 1 : 0,
    total: 1,
    version: snapshot.version,
    code: snapshot.code,
    shot: snapshot.shot,
    pairs: snapshot.pairs,
    selectedPair,
    truthEligible: true,
    sealedAt: snapshot.sealedAt,
    sealedBaseUid: snapshot.sealedBaseUid,
    sealHash: snapshot.sealHash,
    actualFirstFourDigits: firstFourNumbers(actualRecord)
  };
}

function calculateModelPerformance(predictions) {
  const stats = {};
  const businessDay = currentBusinessDayKey();
  const evaluatedToday = filterDedupedEvaluatedPredictions(predictions, {
    requireModelVersion: true,
    businessDay
  });
  const evaluated = filterDedupedEvaluatedPredictions(predictions, {
    requireModelVersion: true,
    limit: MODEL_RATE_WINDOW
  });

  let latestExtraPairChecked = false;
  let latestExtraPairMissed = false;

  evaluatedToday.forEach((prediction) => {
    const ev = prediction.evaluation || {};
    const actualNumbers = Array.isArray(ev.actualNumbers) ? ev.actualNumbers.map(String) : [];
    ["wan", "qian", "bai", "shi"].forEach((key, index) => {
      const kills = killListFromValue(ev.previousPositionKills && ev.previousPositionKills[key]);
      const actual = actualNumbers[index];
      if (actual !== undefined && kills[0] !== undefined) addModelStat(stats, `position.${key}.main`, actual !== String(kills[0]));
      if (actual !== undefined && kills[1] !== undefined) addModelStat(stats, `position.${key}.backup`, actual !== String(kills[1]));
      if (actual !== undefined && kills.length) addModelStat(stats, `position.${key}.dual`, !kills.includes(String(actual)));
    });
    const threeDuishu = ev.threeDuishu || {};
    if (
      threeDuishu.truthEligible === true &&
      typeof threeDuishu.success === "boolean" &&
      (!threeDuishu.version || threeDuishu.version === THREE_DUISHU_SNAPSHOT_VERSION)
    ) {
      const backupPair = resolveSealedThreeDuishuBackupPair(threeDuishu);
      if (/^\d{2}$/.test(backupPair)) {
        const actualRecord = actualNumbers.length >= 4
          ? { numbers: actualNumbers.slice(0, 4) }
          : (prediction && prediction.actualRecord ? prediction.actualRecord : null);
        if (actualRecord) {
          addModelStat(stats, "threeDuishu.backup", !threeDuishuRecordHits(actualRecord, [backupPair]));
        }
      }
    }
  });

  evaluated.forEach((prediction) => {
    const ev = prediction.evaluation || {};
    addModelStat(stats, "hewei.total", ev.independentTotalHeweiSuccess);
    addModelStat(stats, "hewei.unified", ev.independentThreeFourSuccess);
    ["wanQianBai", "qianBaiShi", "wanQianBaiShi"].forEach((key) => {
      const item = ev.independentHewei && ev.independentHewei[key];
      if (item && typeof item.success === "boolean") addModelStat(stats, `hewei.${key}`, item.success);
    });

    const extras = ev.independentExtras || {};
    const sum = extras.sumAvoid;
    const sumRange = extras.sumRangeAvoid;
    const span = extras.spanAvoid;
    const repeat = extras.repeatAvoid;
    const consecutive = extras.consecutiveAvoid;
    const parityRatio = extras.parityRatioAvoid;
    const sizeRatio = extras.sizeRatioAvoid;
    const three = extras.threeDigitAvoid;
    const two = extras.twoDigitAvoid;
    const two2 = extras.twoDigitAvoid2;
    const two3 = extras.twoDigitAvoid3;
    const twoHit = extras.twoDigitHit;
    const threeHit = extras.threeDigitHit;
    const threeAvoid2 = extras.threeDigitAvoid2;
    const anyTwoHewei = extras.anyTwoHeweiHit;
    const anyThreeHewei = extras.anyThreeHeweiHit;
    if (sum && typeof sum.success === "boolean") addModelStat(stats, "extra.sumAvoid", sum.success);
    if (sumRange && typeof sumRange.success === "boolean") addModelStat(stats, "extra.sumRangeAvoid", sumRange.success);
    if (span && typeof span.success === "boolean") addModelStat(stats, "extra.spanAvoid", span.success);
    if (repeat && typeof repeat.success === "boolean") addModelStat(stats, "extra.repeatAvoid", repeat.success);
    if (consecutive && typeof consecutive.success === "boolean") addModelStat(stats, "extra.consecutiveAvoid", consecutive.success);
    if (parityRatio && typeof parityRatio.success === "boolean") addModelStat(stats, "extra.parityRatioAvoid", parityRatio.success);
    if (sizeRatio && typeof sizeRatio.success === "boolean") addModelStat(stats, "extra.sizeRatioAvoid", sizeRatio.success);
    if (three && typeof three.success === "boolean") addModelStat(stats, "extra.threeDigitAvoid", three.success);
    if (two && typeof two.success === "boolean") addModelStat(stats, "extra.twoDigitAvoid", two.success);
    if (two2 && typeof two2.success === "boolean") addModelStat(stats, "extra.twoDigitAvoid2", two2.success);
    if (two3 && typeof two3.success === "boolean") addModelStat(stats, "extra.twoDigitAvoid3", two3.success);
    if (twoHit && typeof twoHit.success === "boolean") addModelStat(stats, "extra.twoDigitHit", twoHit.success);
    if (threeHit && typeof threeHit.success === "boolean") addModelStat(stats, "extra.threeDigitHit", threeHit.success);
    if (threeAvoid2 && typeof threeAvoid2.success === "boolean") addModelStat(stats, "extra.threeDigitAvoid2", threeAvoid2.success);
    if (anyTwoHewei && typeof anyTwoHewei.success === "boolean") addModelStat(stats, "extra.anyTwoHeweiHit", anyTwoHewei.success);
    if (anyThreeHewei && typeof anyThreeHewei.success === "boolean") addModelStat(stats, "extra.anyThreeHeweiHit", anyThreeHewei.success);
    if (!latestExtraPairChecked && sum && three && typeof sum.success === "boolean" && typeof three.success === "boolean") {
      latestExtraPairChecked = true;
      latestExtraPairMissed = !sum.success && !three.success;
    }
  });

  stats.extraPair = {
    total: latestExtraPairChecked ? 1 : 0,
    lastBothMissed: latestExtraPairMissed
  };
  return finalizeModelStats(stats);
}

function getModelStat(performance, key) {
  const raw = performance && performance[key] ? performance[key] : { total: 0, match: 0, rate: null, low: false, lastSuccess: null };
  const out = Object.assign({ key }, raw);
  decorateTruthScoreFields(out, key, out.rate);
  if (out.rate !== null && out.rate !== undefined && Number(out.total || 0) >= MODEL_MIN_SAMPLE) {
    if (out.predictivePower === false) {
      out.low = Number(out.rate) < (Number(out.randomBaseline || 0) - 3);
    } else {
      out.low = Number(out.rate) < modelLowRateForKey(key);
    }
  }
  return out;
}

function shouldUseBackupAlgorithm(performance, key) {
  // 高基线模型不按「原始高通过率=强」切主备；三数共现已改为避开，走普通避开阈值
  if (isHighBaselineScoreKey(key)) return false;
  const stat = getModelStat(performance, key);
  return !!stat.low;
}

function modelPerformanceSummary(performance, key) {
  const stat = getModelStat(performance, key);
  const meta = highBaselineScoreMeta(key);
  return {
    key,
    total: Number(stat.total || 0),
    match: Number(stat.match || 0),
    rate: stat.rate,
    low: !!stat.low,
    threshold: meta ? Number(meta.baseline) - 3 : modelLowRateForKey(key),
    minSample: MODEL_MIN_SAMPLE,
    scoreClass: stat.scoreClass || "",
    randomBaseline: stat.randomBaseline,
    edgeOverBaseline: stat.edgeOverBaseline,
    predictivePower: stat.predictivePower !== false,
    scoreHint: stat.scoreHint || "",
    truthDisplay: stat.truthDisplay || "predictive"
  };
}

function modelKeyLabel(key) {
  const labels = {
    "position.wan.main": "千位单定顶1",
    "position.qian.main": "百位单定顶1",
    "position.bai.main": "十位单定顶1",
    "position.shi.main": "个位单定顶1",
    "position.wan.dual": "千位定位双码（观察）",
    "position.qian.dual": "百位定位双码（观察）",
    "position.bai.dual": "十位定位双码（观察）",
    "position.shi.dual": "个位定位双码（观察）",
    "hewei.total": "总合尾",
    "hewei.unified": "三个合尾统一",
    "hewei.wanQianBai": "千百十合尾",
    "hewei.qianBaiShi": "百十个合尾",
    "hewei.wanQianBaiShi": "千百十个合尾",
    "extra.sumAvoid": "单点和值过滤（高基线）",
    "extra.sumRangeAvoid": "和值极端区间过滤（高基线）",
    "extra.spanAvoid": "跨度极罕形态过滤（高基线）",
    "extra.repeatAvoid": "重号极罕形态过滤（高基线）",
    "extra.consecutiveAvoid": "连号极罕形态过滤（高基线）",
    "extra.parityRatioAvoid": "极端奇偶比过滤（高基线）",
    "extra.sizeRatioAvoid": "极端大小比过滤（高基线）",
    "extra.threeDigitAvoid": "三数共现避开1组",
    "extra.twoDigitAvoid": "四定对数避开1组",
    "extra.twoDigitAvoid2": "四定对数避开2组",
    "extra.twoDigitAvoid3": "四定对数避开3组",
    "extra.twoDigitHit": "两数共现避开（基线参考）",
    "extra.threeDigitHit": "三数共现避开2组",
    "extra.threeDigitAvoid2": "三数共现避开3组",
    "extra.anyTwoHeweiHit": "二数合尾02468排1码命中（高基线参考）",
    "extra.anyThreeHeweiHit": "三数合尾宽命中（高基线参考）",
    "threeDuishu.backup": "三对数备用对"
  };
  return labels[key] || key;
}

function watchdogThresholdForModel(key) {
  if (!key) return null;
  if (/^position\.(wan|qian|bai|shi)\.main$/.test(key)) return POSITION_MAIN_LOW_RATE;
  if (/^position\.(wan|qian|bai|shi)\.dual$/.test(key)) return POSITION_DUAL_LOW_RATE;
  if (key === "hewei.total") return MODEL_WATCHDOG_LOW_RATE;
  if (key === "hewei.unified") return HEWEI_UNIFIED_GUARD_RATE;
  if (/^hewei\.(wanQianBai|qianBaiShi|wanQianBaiShi|wanQianGe|wanBaiShi)$/.test(key)) return MODEL_WATCHDOG_LOW_RATE;
  if (/^extra\.(sumAvoid|sumRangeAvoid|spanAvoid|repeatAvoid|consecutiveAvoid|parityRatioAvoid|sizeRatioAvoid|threeDigitAvoid|threeDigitAvoid2|twoDigitAvoid|twoDigitAvoid2|twoDigitAvoid3|twoDigitHit|threeDigitHit)$/.test(key)) return MODEL_WATCHDOG_LOW_RATE;
  if (/^extra\.(anyTwoHeweiHit|anyThreeHeweiHit)$/.test(key)) return MODEL_WATCHDOG_LOW_RATE;
  return null;
}

function officialCounterKeyForWatchdog(watchdogKey) {
  const key = String(watchdogKey || "");
  const mapped = {
    "position.wan.main": "position.wan.main",
    "position.qian.main": "position.qian.main",
    "position.bai.main": "position.bai.main",
    "position.shi.main": "position.shi.main",
    "position.wan.dual": "position.wan.dual",
    "position.qian.dual": "position.qian.dual",
    "position.bai.dual": "position.bai.dual",
    "position.shi.dual": "position.shi.dual",
    "hewei.total": "totalHewei",
    "hewei.unified": "threeFour",
    "hewei.wanQianBai": "wanQianBai",
    "hewei.qianBaiShi": "qianBaiShi",
    "hewei.wanQianBaiShi": "wanQianBaiShi",
    "hewei.wanQianGe": "wanQianGe",
    "hewei.wanBaiShi": "wanBaiShi"
  };
  if (mapped[key]) return mapped[key];
  if (key.startsWith("extra.") && key !== "extraPair") return key.slice("extra.".length);
  return null;
}

function collectPerformanceIssues(performance) {
  const issues = [];
  const stats = performance && typeof performance === "object" ? performance : {};
  const counters = normalizeCounters(state && state.counters);
  // 合尾/extra：短窗易误报；官方长账本样本充足且达标时，不以近窗判定 degraded
  const counterPreferMin = Math.max(Number(MODEL_WATCHDOG_MIN_SAMPLE || 8), 60);
  Object.keys(stats).forEach((key) => {
    if (key === "extraPair") return;
    const threshold = watchdogThresholdForModel(key);
    if (threshold === null) return;
    let total = Number((stats[key] && stats[key].total) || 0);
    let rate = stats[key] && stats[key].rate !== null && stats[key].rate !== undefined
      ? Number(stats[key].rate)
      : null;
    const counterKey = officialCounterKeyForWatchdog(key);
    if (counterKey && counters[counterKey]) {
      const cTotal = Number(counters[counterKey].total || 0);
      const cRate = counters[counterKey].rate === null || counters[counterKey].rate === undefined
        ? null
        : Number(counters[counterKey].rate);
      if (cTotal >= counterPreferMin && cRate !== null && Number.isFinite(cRate)) {
        // 用长账本覆盖短窗统计，避免 15 期噪声误报
        total = cTotal;
        rate = cRate;
      }
    }
    if (total >= MODEL_WATCHDOG_MIN_SAMPLE && rate !== null && Number.isFinite(rate) && rate < threshold) {
      // 高基线 / 宽命中：只观察。threeDigitHit 已改避开逻辑，允许清锁换码（历史曾被 123 粘死）
      const observeOnly = isHighBaselineScoreKey(key) ||
        /^extra\.(anyTwoHeweiHit|anyThreeHeweiHit)$/.test(key) ||
        /^position\.(wan|qian|bai|shi)\.(dual|backup)$/.test(key);
      issues.push({
        type: "low-performance",
        key,
        label: modelKeyLabel(key),
        total,
        rate: Number(rate.toFixed(2)),
        threshold,
        scoreClass: (highBaselineScoreMeta(key) && highBaselineScoreMeta(key).scoreClass) || "",
        action: observeOnly ? "observe-only" : "clear-lock-and-regenerate",
        truthSource: counterKey && Number(counters[counterKey] && counters[counterKey].total || 0) >= counterPreferMin
          ? "official-counter"
          : "recent-window"
      });
    }
  });
  if (stats.extraPair && stats.extraPair.lastBothMissed) {
    issues.push({
      type: "paired-extra-miss",
      key: "extraPair",
      label: "和值/共现组合",
      total: Number(stats.extraPair.total || 0),
      rate: null,
      threshold: MODEL_WATCHDOG_LOW_RATE,
      action: "clear-extra-locks-and-regenerate"
    });
  }
  return issues;
}

function collectStabilityIssue(issues, key, label, entry, options) {
  if (!entry || typeof entry !== "object") return;
  const opts = options && typeof options === "object" ? options : {};
  const stability = entry.stability || {};
  const block = stability.block || {};
  const blockCount = Number(block.blockCount || 0);
  if (blockCount < 3) return;
  const floor = Number(opts.floor || stability.guardRate || MODEL_WATCHDOG_BLOCK_FLOOR);
  const lowerFloor = Number(opts.lowerFloor || Math.max(52, floor - 12));
  const blockMin = Number(block.minRate || 0);
  const weakBlocks = Number(block.weakBlocks || 0);
  const lower120 = Number(stability.lower120 || 0);
  if (blockMin < floor || weakBlocks > 0 || lower120 < lowerFloor) {
    issues.push({
      type: "weak-stability",
      key,
      label,
      lower120: Number(lower120.toFixed(2)),
      blockMin: Number(blockMin.toFixed(2)),
      weakBlocks,
      floor,
      lowerFloor,
      action: "clear-lock-and-regenerate"
    });
  }
}

function collectCurrentPredictionStabilityIssues(prediction) {
  const issues = [];
  const current = prediction && typeof prediction === "object" ? prediction : {};
  const positionKills = current.positionKills || {};
  ["wan", "qian", "bai", "shi"].forEach((key) => {
    collectStabilityIssue(issues, `position.${key}.dual`, modelKeyLabel(`position.${key}.dual`), positionKills[key], {
      floor: POSITION_BLOCK_FLOOR,
      lowerFloor: 72
    });
  });
  const detail = current.detail || {};
  collectStabilityIssue(issues, "hewei.total", modelKeyLabel("hewei.total"), detail.totalHeweiKill, {
    floor: HEWEI_BLOCK_FLOOR,
    lowerFloor: 72
  });
  collectStabilityIssue(issues, "hewei.unified", modelKeyLabel("hewei.unified"), detail.threeFourHeweiKill, {
    floor: HEWEI_UNIFIED_BLOCK_FLOOR,
    lowerFloor: 56
  });
  const independent = current.independentHeweiKills || {};
  ["wanQianBai", "qianBaiShi", "wanQianBaiShi"].forEach((key) => {
    collectStabilityIssue(issues, `hewei.${key}`, modelKeyLabel(`hewei.${key}`), independent[key], {
      floor: HEWEI_BLOCK_FLOOR,
      lowerFloor: 72
    });
  });
  return issues;
}

function predictionExtraValue(item) {
  if (!item || typeof item !== "object") return "";
  return String(item.value || item.code || item.killValue || item.digit || item.pair || "").trim();
}

function extraModelIntegrityReason(key, item) {
  const value = predictionExtraValue(item);
  if (!item || typeof item !== "object") return "missing-object";
  if (!value) return "missing-value";
  if (["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3", "twoDigitHit"].includes(key)) {
    if (!/^\d{2}$/.test(value) || new Set(value.split("")).size !== 2) return "invalid-two-digit-code";
  }
  if (["threeDigitAvoid", "threeDigitHit", "threeDigitAvoid2"].includes(key)) {
    if (!/^\d{3}$/.test(value) || new Set(value.split("")).size !== 3) return "invalid-three-digit-code";
  }
  if (key === "anyTwoHeweiHit") {
    if (!isAnyTwoHeweiCode(value)) return "invalid-two-hewei-code";
  }
  if (key === "anyThreeHeweiHit") {
    if (!/^\d{5}$/.test(value) || new Set(value.split("")).size !== ANY_THREE_HEWEI_CODE_SIZE) return "invalid-three-hewei-code";
    if (isUniformParityDigitSet(value)) return "invalid-three-hewei-uniform-parity";
  }
  if (key === "sumRangeAvoid" && !/^\d{1,2}-\d{1,2}$/.test(value)) return "invalid-sum-range";
  if (key === "spanAvoid" && !/^\d$/.test(value)) return "invalid-span";
  if (key === "sumAvoid") {
    const n = Number(value);
    if (!/^\d{1,2}$/.test(value) || !Number.isFinite(n) || n < 10 || n > 26) return "invalid-sum-avoid";
  }
  if (key === "repeatAvoid" && !["重号", "一对", "双重", "三重", "四重"].includes(value)) return "invalid-repeat-avoid";
  if (key === "consecutiveAvoid" && !["连号", "二连", "三连", "四连"].includes(value)) return "invalid-consecutive-avoid";
  if (key === "parityRatioAvoid" && !["0单4双", "1单3双", "2单2双", "3单1双", "4单0双"].includes(value)) return "invalid-parity-ratio";
  if (key === "sizeRatioAvoid" && !["0大4小", "1大3小", "2大2小", "3大1小", "4大0小"].includes(value)) return "invalid-size-ratio";
  return "";
}

const TIP_STRUCTURE_SNAPSHOT_SPECS = [
  ["autoTop6", "自动推荐快照", () => autoTop6SnapshotIsComplete],
  ["recommend2", "推荐2快照", () => recommend2SnapshotIsComplete],
  ["killFramework", "杀码框架快照", () => killFrameworkSnapshotIsComplete],
  ["fourDingFramework", "四定框架快照", () => fourDingFrameworkSnapshotIsComplete],
  ["threeDuishu", "三对数快照", () => threeDuishuSnapshotIsComplete]
];


function collectPredictionIntegrityIssues(prediction, latest) {
  const issues = [];
  const current = prediction && typeof prediction === "object" ? prediction : null;
  if (!current) {
    issues.push({
      type: "prediction-missing",
      key: "prediction.current",
      label: "当前预测",
      action: "regenerate-current-prediction"
    });
    return issues;
  }

  if (latest && latest.uid && current.baseUid !== latest.uid) {
    issues.push({
      type: "prediction-base-mismatch",
      key: "prediction.baseUid",
      label: "当前预测期号",
      expected: latest.uid,
      actual: current.baseUid || "",
      action: "regenerate-current-prediction"
    });
  }
  if (current.modelVersion !== MODEL_VERSION) {
    issues.push({
      type: "prediction-version-mismatch",
      key: "prediction.modelVersion",
      label: "模型版本",
      expected: MODEL_VERSION,
      actual: current.modelVersion || "",
      action: "regenerate-current-prediction"
    });
  }

  const positionKills = current.positionKills || {};
  ["wan", "qian", "bai", "shi"].forEach((key) => {
    const entry = normalizePositionKillEntry(positionKills[key]);
    if (!Array.isArray(entry.digits) || !entry.digits.length || entry.digits[0] === "--" || !/^\d$/.test(String(entry.digits[0]))) {
      issues.push({
        type: "position-model-missing",
        key: `position.${key}.dual`,
        label: modelKeyLabel(`position.${key}.dual`),
        action: "isolate-algorithm-project"
      });
    }
  });

  const hewei = current.independentHeweiKills || {};
  ["wanQianBai", "qianBaiShi", "wanQianBaiShi"].forEach((key) => {
    const item = hewei[key];
    const value = item && typeof item === "object" ? String(item.killDigit || item.primaryDigit || item.value || "") : String(item || "");
    if (!/^\d$/.test(value)) {
      issues.push({
        type: "hewei-model-missing",
        key: `hewei.${key}`,
        label: modelKeyLabel(`hewei.${key}`),
        action: "isolate-algorithm-project"
      });
    }
  });
  const totalHeweiValue = String(current.totalHeweiKill == null ? "" : current.totalHeweiKill);
  const unifiedHeweiValue = String(current.threeFourHeweiKillDigit == null ? "" : current.threeFourHeweiKillDigit);
  if (!/^\d$/.test(totalHeweiValue)) {
    issues.push({ type: "hewei-model-missing", key: "hewei.total", label: "四位总合尾", action: "isolate-algorithm-project" });
  }
  if (!/^\d$/.test(unifiedHeweiValue)) {
    issues.push({ type: "hewei-model-missing", key: "hewei.unified", label: "三个合尾统一", action: "isolate-algorithm-project" });
  }

  const extraKills = current.extraKills || (current.detail && current.detail.extraKills) || {};
  REQUIRED_EXTRA_KILL_KEYS.forEach((key) => {
    const item = extraKills[key];
    const reason = extraModelIntegrityReason(key, item);
    if (reason) {
      issues.push({
        type: "extra-model-missing",
        key: `extra.${key}`,
        label: modelKeyLabel(`extra.${key}`),
        reason,
        action: "isolate-algorithm-project"
      });
    }
  });

  TIP_STRUCTURE_SNAPSHOT_SPECS.forEach(([name, label, getComplete]) => {
    const snapshot = current[name];
    const isComplete = getComplete();
    if (!isComplete(snapshot)) {
      const duishuFamily = name === "threeDuishu" ||
        ((name === "recommend2" || name === "threeDingTwo" || name === "threeDingOne") &&
          snapshot && !frameworkHasDuishuPair(snapshot));
      issues.push({
        type: `${name}-snapshot-incomplete`,
        key: `snapshot.${name}`,
        label,
        expectedVersion: name === "threeDuishu" ? THREE_DUISHU_SNAPSHOT_VERSION : (snapshot && snapshot.version) || "",
        actualVersion: snapshot && snapshot.version || "",
        sealed: !!(snapshot && snapshot.sealed),
        action: duishuFamily ? "repair-duishu-capture" : "regenerate-current-prediction"
      });
      return;
    }
    // 结构完美：完整且必须有效封存；未封存/哈希失效一律阻断
    if (!snapshotSealIsValid(snapshot, current)) {
      issues.push({
        type: "snapshot-unsealed-or-tampered",
        key: `snapshot.${name}.seal`,
        label: `${label}开奖前封存`,
        sealed: !!(snapshot && snapshot.sealed),
        action: "isolate-algorithm-project"
      });
    }
  });

  collectDuishuCaptureIssues(current).forEach((issue) => {
    const already = issues.some((row) => row && row.key === issue.key && row.action === issue.action);
    if (!already) issues.push(issue);
  });

  // tip 更稳期号必须对齐 basePeriod，否则滤网是摆设
  if (tipStableMathFilterLagging(current)) {
    issues.push({
      type: "stable-math-filter-lag",
      key: "prediction.stableMathFilter.latest_period",
      label: "更稳数学排除期号",
      expected: current.basePeriod || "",
      actual: current.stableMathFilter && current.stableMathFilter.latest_period || "",
      action: "regenerate-current-prediction"
    });
  }

  return issues;
}

function tipStructureIsPerfect(prediction, latest) {
  return collectPredictionIntegrityIssues(prediction, latest).length === 0;
}

function applyTipReadyState(prediction, latest, issues) {
  const list = Array.isArray(issues) ? issues : collectPredictionIntegrityIssues(prediction, latest);
  const coreIssues = list.filter(isCorePredictionIssue);
  const projectIssues = list.filter((issue) => !isCorePredictionIssue(issue));
  const projectHealthSummary = summarizeAlgorithmProjectHealth(state && state.algorithmProjectHealth);
  const stabilityIssues = prediction ? collectCurrentPredictionStabilityIssues(prediction) : [];
  const structureReady = coreIssues.length === 0;
  const truthReady = structureReady && projectIssues.length === 0;
  const qualityReady = truthReady && projectHealthSummary.degradedProjectCount === 0 && stabilityIssues.length === 0;
  const operationalReady = qualityReady;
  if (prediction && typeof prediction === "object") {
    prediction.tipStructurePerfect = structureReady;
    prediction.tipReady = structureReady;
    prediction.structureReady = structureReady;
    prediction.truthReady = truthReady;
    prediction.qualityReady = qualityReady;
    prediction.operationalReady = operationalReady;
    prediction.algorithmProjectIssueCount = projectIssues.length;
    prediction.algorithmProjectIssues = projectIssues.slice(0, 40);
    prediction.algorithmProjectDegradedCount = projectHealthSummary.degradedProjectCount;
    prediction.algorithmProjectDegradedIssueCount = projectHealthSummary.degradedIssueCount;
    prediction.algorithmProjectDegradedIds = projectHealthSummary.degradedProjectIds;
  }
  if (state) {
    state.tipReady = structureReady;
    state.structureReady = structureReady;
    state.truthReady = truthReady;
    state.qualityReady = qualityReady;
    state.operationalReady = operationalReady;
    state.tipReadyCheckedAt = isoNow();
    state.tipReadyIssueCount = coreIssues.length;
    state.tipReadyIssues = coreIssues.slice(0, 40);
    state.algorithmProjectIssueCount = projectIssues.length;
    state.algorithmProjectIssues = projectIssues.slice(0, 80);
    // Keep this separate from algorithmProjectIssueCount: that field is for
    // current prediction-integrity issues, whereas this summarizes persistent
    // isolated-project health across the current base period.
    state.algorithmProjectDegradedCount = projectHealthSummary.degradedProjectCount;
    state.algorithmProjectDegradedIssueCount = projectHealthSummary.degradedIssueCount;
    state.algorithmProjectDegradedIds = projectHealthSummary.degradedProjectIds;
    if (structureReady) state.tipReadyFailedAt = "";
    else if (!state.tipReadyFailedAt) state.tipReadyFailedAt = isoNow();
  }
  return structureReady;
}

function collectRuntimeIssues() {
  const issues = [];
  const memory = process.memoryUsage();
  const heapStats = v8.getHeapStatistics();
  const heapLimit = Number(heapStats.heap_size_limit || 0);
  const ratio = heapLimit > 0 ? memory.heapUsed / heapLimit : 0;
  if (ratio >= MODEL_WATCHDOG_MEMORY_LIMIT) {
    issues.push({
      type: "memory-pressure",
      key: "runtime.memory",
      label: "后台内存",
      heapUsedMb: Number((memory.heapUsed / 1024 / 1024).toFixed(2)),
      heapLimitMb: Number((heapLimit / 1024 / 1024).toFixed(2)),
      ratio: Number(ratio.toFixed(4)),
      threshold: MODEL_WATCHDOG_MEMORY_LIMIT,
      action: "trim-cache-and-save"
    });
  }
  return issues;
}

function deleteLockIfPresent(container, key, actions, issueKey, reason) {
  if (!container || typeof container !== "object" || !key || container[key] === undefined) return;
  delete container[key];
  actions.push({ type: "delete-lock", issueKey, lockKey: key, reason });
}

function clearLocksForIssue(issue, actions) {
  if (!issue || !issue.key) return;
  if (!state.stable120Locks || typeof state.stable120Locks !== "object") state.stable120Locks = {};
  if (!state.modelSelectionLocks || typeof state.modelSelectionLocks !== "object") state.modelSelectionLocks = {};
  const key = String(issue.key);
  if (key.startsWith("position.")) {
    const pos = key.split(".")[1];
    deleteLockIfPresent(state.stable120Locks, `v42:position:${pos}`, actions, key, issue.type);
    deleteLockIfPresent(state.stable120Locks, `v43:position:${pos}`, actions, key, issue.type);
  } else if (key === "hewei.total") {
    deleteLockIfPresent(state.stable120Locks, "v43:hewei:total", actions, key, issue.type);
  } else if (key === "hewei.unified") {
    deleteLockIfPresent(state.stable120Locks, "v43:hewei:unified", actions, key, issue.type);
  } else if (key.startsWith("hewei.")) {
    const heweiKey = key.split(".")[1];
    deleteLockIfPresent(state.stable120Locks, `v43:hewei:${heweiKey}`, actions, key, issue.type);
  } else if (key === "extraPair") {
    Object.keys(state.modelSelectionLocks).forEach((lockKey) => {
      if (lockKey.indexOf("v28:extra.") >= 0) deleteLockIfPresent(state.modelSelectionLocks, lockKey, actions, key, issue.type);
    });
  } else if (key.startsWith("extra.")) {
    Object.keys(state.modelSelectionLocks).forEach((lockKey) => {
      if (lockKey.indexOf(`v28:${key}:`) >= 0) deleteLockIfPresent(state.modelSelectionLocks, lockKey, actions, key, issue.type);
    });
  }
}

function trimRuntimeCaches(actions) {
  const beforePredictions = Array.isArray(state.predictions) ? state.predictions.length : 0;
  const beforeLogs = Array.isArray(state.logs) ? state.logs.length : 0;
  const stormActive = !!(state.stormMode && state.stormMode.active);
  const keepPredictions = stormActive
    ? Math.min(MAX_PREDICTION_LOG, 280)
    : Math.min(MAX_PREDICTION_LOG, 500);
  const keepLogs = stormActive ? 80 : 120;
  state.predictions = Array.isArray(state.predictions) ? state.predictions.slice(0, keepPredictions) : [];
  state.logs = Array.isArray(state.logs) ? state.logs.slice(0, keepLogs) : [];
  // 压力场景下必须清算法 LRU；否则候选大对象会跨期残留
  try { scoreThreeDuishuCandidatesLru.clear(); } catch (_) {}
  try { buildStableHeweiCandidatesLru.clear(); } catch (_) {}
  if (Array.isArray(state.history)) {
    state.history.forEach((row) => {
      if (row && row.rawJson != null) row.rawJson = null;
    });
  }
  if (typeof global.gc === "function") {
    try { global.gc(); } catch (_) {}
  }
  invalidatePanelStateCache();
  actions.push({
    type: "trim-runtime-cache",
    stormActive,
    predictionsBefore: beforePredictions,
    predictionsAfter: state.predictions.length,
    logsBefore: beforeLogs,
    logsAfter: state.logs.length,
    clearedAlgorithmLru: true
  });
}

function regenerateCurrentPredictionForWatchdog(actions) {
  if (!Array.isArray(state.history) || !state.history.length) return false;
  if (predictionBuildRunning) {
    actions.push({ type: "regenerate-skip", reason: "prediction-build-running" });
    return false;
  }
  predictionBuildRunning = true;
  try {
    const duishuBefore = collectDuishuCaptureIssues(state.currentPrediction);
    const latest = state.history[0];
    const regenerated = fillPredictionDetail(generateBigDataPrediction(state.history), state.history);
    state.predictions = [
      regenerated,
      ...(Array.isArray(state.predictions) ? state.predictions : []).filter((item) => {
        return !(item && item.baseUid === latest.uid && item.modelVersion === MODEL_VERSION && !item.evaluated);
      })
    ].slice(0, MAX_PREDICTION_LOG);
    state.currentPrediction = regenerated;
    resetAlgorithmProjectHealthForPrediction(regenerated);
    ensurePredictionFrameworkSnapshots(regenerated, state.history);
    sealPredictionSnapshots(regenerated, latest);
    invalidatePanelStateCache();
    if (duishuBefore.length) {
      const remaining = collectDuishuCaptureIssues(regenerated);
      state.duishuCaptureAlarm = {
        at: isoNow(),
        baseUid: String(regenerated.baseUid || ""),
        basePeriod: String(regenerated.basePeriod || ""),
        beforeCount: duishuBefore.length,
        before: duishuBefore.slice(0, 12),
        repairs: [{ target: "watchdog-regenerate", ok: remaining.length === 0 }],
        remaining: remaining.slice(0, 12),
        resolved: remaining.length === 0,
        via: "watchdog-regenerate"
      };
      addLog(remaining.length === 0
        ? "【报警】对数抓取缺失，巡检整包重生已补齐"
        : "【报警】对数抓取缺失，巡检整包重生后仍有残留", {
        before: duishuBefore.slice(0, 12),
        remaining: remaining.slice(0, 12)
      });
    }
    actions.push({
      type: "regenerate-current-prediction",
      baseUid: regenerated.baseUid,
      basePeriod: regenerated.basePeriod,
      modelVersion: regenerated.modelVersion,
      repairedDuishu: duishuBefore.length > 0
    });
    return true;
  } finally {
    predictionBuildRunning = false;
  }
}

function snapshotPositionKillDigits(prediction) {
  const pk = prediction && prediction.positionKills && typeof prediction.positionKills === "object"
    ? prediction.positionKills
    : {};
  const out = {};
  ["wan", "qian", "bai", "shi"].forEach((key) => {
    const entry = pk[key] && typeof pk[key] === "object" ? pk[key] : {};
    out[key] = {
      killDigits: Array.isArray(entry.killDigits) ? entry.killDigits.slice(0, 2) : [],
      main: entry.main != null ? entry.main : entry.recommendDigit,
      backup: entry.backup != null ? entry.backup : entry.originalBackupDigit,
      reason: entry.reason || "",
      algorithm: entry.algorithm || ""
    };
  });
  return out;
}

function clearPositionStabilityLocks(actions) {
  if (!state.stable120Locks || typeof state.stable120Locks !== "object") state.stable120Locks = {};
  const removed = [];
  Object.keys(state.stable120Locks).forEach((key) => {
    if (!/^v4[23]:position:(wan|qian|bai|shi)$/.test(String(key))) return;
    deleteLockIfPresent(state.stable120Locks, key, actions, key, "force-regenerate-tip");
    removed.push(key);
  });
  ["wan", "qian", "bai", "shi"].forEach((pos) => {
    deleteLockIfPresent(state.stable120Locks, `v42:position:${pos}`, actions, `position.${pos}`, "force-regenerate-tip");
    deleteLockIfPresent(state.stable120Locks, `v43:position:${pos}`, actions, `position.${pos}`, "force-regenerate-tip");
  });
  return removed;
}

function forceSwitchPositionKills(bannedPairs) {
  const positionKillsAlgo = require("./algorithms/position-kills");
  const history = Array.isArray(state.history) ? state.history : [];
  const modelPerformance = calculateModelPerformance(Array.isArray(state.predictions) ? state.predictions : []);
  const result = positionKillsAlgo.compute({
    history,
    modelPerformance,
    forceUnlock: true,
    bannedPairs: bannedPairs || {},
    lockApi: {
      getStable120Locks: () => ({}),
      setStable120Locks: () => {}
    }
  });
  return result && result.payload && typeof result.payload === "object" ? result.payload : null;
}

/** Admin-only: clear position locks + rebuild sealed tip so定位排除可换号 */
function forceRegenerateTipForAdmin(options) {
  const opts = options && typeof options === "object" ? options : {};
  const actions = [];
  if (syncRunning) {
    return { ok: false, error: "sync-running", actions };
  }
  if (predictionBuildRunning) {
    return { ok: false, error: "prediction-build-running", actions };
  }
  if (!Array.isArray(state.history) || !state.history.length) {
    return { ok: false, error: "history-empty", actions };
  }
  const storm = normalizeStormMode(state.stormMode);
  if (storm && storm.active && storm.lockFreeze) {
    return { ok: false, error: "storm-lock-freeze", actions };
  }
  const before = snapshotPositionKillDigits(state.currentPrediction);
  const bannedPairs = {};
  ["wan", "qian", "bai", "shi"].forEach((key) => {
    bannedPairs[key] = (before[key] && before[key].killDigits) || [];
  });
  if (opts.clearPositionLocks !== false) {
    clearPositionStabilityLocks(actions);
  }
  const regenerated = regenerateCurrentPredictionForWatchdog(actions);
  if (!regenerated) {
    return { ok: false, error: "regenerate-failed-or-busy", before, actions };
  }
  // 内联 generate 会先写回锁，L0 又 keep-min-hold；这里强制换掉旧对
  if (opts.clearPositionLocks !== false) {
    clearPositionStabilityLocks(actions);
  }
  const switched = forceSwitchPositionKills(bannedPairs);
  if (switched && state.currentPrediction) {
    state.currentPrediction.positionKills = switched;
    if (!state.currentPrediction.detail || typeof state.currentPrediction.detail !== "object") {
      state.currentPrediction.detail = {};
    }
    state.currentPrediction.detail.positionKills = switched;
    // 写入新锁（hold=1），避免下一轮又粘回旧对；不沿用 keep 逻辑
    if (!state.stable120Locks || typeof state.stable120Locks !== "object") state.stable120Locks = {};
    const latestPeriod = state.history[0] ? String(state.history[0].period || state.history[0].uid || "") : "";
    ["wan", "qian", "bai", "shi"].forEach((pos) => {
      const entry = switched[pos] || {};
      const pair = Array.isArray(entry.killDigits) ? entry.killDigits.slice(0, 2) : [];
      if (pair.length !== 2) return;
      state.stable120Locks[`v42:position:${pos}`] = {
        pair,
        hold: 1,
        period: latestPeriod,
        reason: entry.reason || "force-unlock-switch-pair",
        updatedAt: isoNow()
      };
    });
    actions.push({ type: "force-switch-position-kills", bannedPairs });
    invalidatePanelStateCache();
  }
  // 重新封存，避免面板仍读旧 immutable 快照
  try {
    const latest = state.history[0];
    if (state.currentPrediction && latest) {
      state.currentPrediction.allSnapshotsSealed = false;
      if (state.currentPrediction.recommendationMeta) {
        delete state.currentPrediction.recommendationMeta.snapshotsSealed;
      }
      sealPredictionSnapshots(state.currentPrediction, latest);
    }
  } catch (err) {
    actions.push({ type: "reseal-error", error: String(err && err.message || err) });
  }
  saveState();
  const after = snapshotPositionKillDigits(state.currentPrediction);
  addLog("管理员强制整包重生 tip", {
    clearedLocks: actions.filter((item) => item && item.type === "delete-lock").length,
    tipReady: state.tipReady === true,
    baseUid: state.currentPrediction && state.currentPrediction.baseUid,
    before,
    after
  });
  return {
    ok: true,
    tipReady: state.tipReady === true,
    tipStructurePerfect: !!(state.currentPrediction && state.currentPrediction.tipStructurePerfect),
    baseUid: state.currentPrediction && state.currentPrediction.baseUid || "",
    basePeriod: state.currentPrediction && state.currentPrediction.basePeriod || "",
    targetPeriod: state.currentPrediction && state.currentPrediction.targetPeriod || "",
    before,
    after,
    changed: JSON.stringify(before) !== JSON.stringify(after),
    actions: actions.slice(0, 40)
  };
}

let modelWatchdogRunning = false;

function runModelWatchdog(trigger, options) {
  const opts = options && typeof options === "object" ? options : {};
  state.modelWatchdog = normalizeModelWatchdog(state.modelWatchdog);
  if (!state.modelWatchdog.enabled) {
    return { ok: true, skipped: true, reason: "watchdog-disabled" };
  }
  if (modelWatchdogRunning) {
    return { ok: true, skipped: true, reason: "watchdog-running" };
  }
  modelWatchdogRunning = true;
  const startedAt = isoNow();
  try {
  const performance = calculateModelPerformance(state && Array.isArray(state.predictions) ? state.predictions : []);
  const latest = state && Array.isArray(state.history) && state.history.length ? state.history[0] : (state && state.latestRecord);
  const integrityIssues = collectPredictionIntegrityIssues(state.currentPrediction, latest);
  const qualityIssues = [
    ...collectPerformanceIssues(performance),
    ...collectCurrentPredictionStabilityIssues(state.currentPrediction)
  ];
  const runtimeIssues = collectRuntimeIssues();
  const issues = [...integrityIssues, ...qualityIssues, ...runtimeIssues];
  const actions = [];
  const coreIntegrityIssues = integrityIssues.filter(isCorePredictionIssue);
  const projectIntegrityIssues = integrityIssues.filter((issue) => !isCorePredictionIssue(issue));
  // Quality and per-algorithm integrity failures are isolated in their own
  // project state.  They are observed and repaired independently; they never
  // clear shared locks or regenerate every other algorithm's prediction.
  markAlgorithmProjectIssues([...projectIntegrityIssues, ...qualityIssues], actions);
  const storm = normalizeStormMode(state.stormMode);
  const triggerText = String(trigger || "");
  const syncOwned = triggerText.indexOf("sync") >= 0 || triggerText === "manual-sync";
  // 开奖后延后巡检 / 启动巡检：默认只修完整性，禁止因低胜率整包重生（会堵面板）
  // qualityLockReleaseOnly：允许只清算法锁、禁止 regenerate（老化及时更新）
  const qualityLockReleaseOnly = opts.qualityLockReleaseOnly === true;
  const deferSettleSafe = !qualityLockReleaseOnly && (
    triggerText.indexOf("deferred") >= 0 ||
    triggerText === "startup" ||
    opts.integrityOnly === true
  );
  const baseUid = String(
    state.currentPrediction && state.currentPrediction.baseUid ||
    latest && latest.uid ||
    ""
  );
  const qualityRepairKeys = state.modelWatchdog.qualityRepairKeys && typeof state.modelWatchdog.qualityRepairKeys === "object"
    ? state.modelWatchdog.qualityRepairKeys
    : (state.modelWatchdog.qualityRepairKeys = {});
  const actionableQualityIssues = qualityIssues.filter((issue) => issue && issue.action && issue.action !== "observe-only");
  const activeQualityRepairKeys = new Set(actionableQualityIssues.map((issue) => {
    return `${String(issue.type || "")}:${String(issue.key || "")}`;
  }));
  Object.keys(qualityRepairKeys).forEach((key) => {
    if (!activeQualityRepairKeys.has(key)) delete qualityRepairKeys[key];
  });
  // A quality repair only releases locks owned by the affected algorithm.
  // It deliberately does not rebuild the shared current prediction: that
  // output is sealed for this draw and other algorithms remain independent.
  // Same tip baseUid: clear once. New tip period: allow clear again (aging refresh).
  const effectiveQualityIssues = !deferSettleSafe
    ? actionableQualityIssues.filter((issue) => {
      const repairKey = `${String(issue.type || "")}:${String(issue.key || "")}`;
      const prev = qualityRepairKeys[repairKey];
      if (prev && String(prev.baseUid || "") === baseUid) return false;
      return true;
    })
    : [];
  const duishuIssues = integrityIssues.filter((issue) => issue && issue.action === "repair-duishu-capture");
  const repairableIssues = (qualityLockReleaseOnly
    ? [...effectiveQualityIssues]
    : [...coreIntegrityIssues, ...runtimeIssues, ...effectiveQualityIssues]
  ).filter((issue) => issue && issue.action && issue.action !== "observe-only");
  const urgentPeriodRepair = !qualityLockReleaseOnly && coreIntegrityIssues.length > 0;
  const urgentDuishuRepair = !qualityLockReleaseOnly && duishuIssues.length > 0;
  const lastRepairMs = state.modelWatchdog.lastRepairAt ? Date.parse(state.modelWatchdog.lastRepairAt) : 0;
  const cooldownMs = Number(state.modelWatchdog.repairCooldownMs || MODEL_WATCHDOG_REPAIR_COOLDOWN);
  const inCooldown = (repairableIssues.length > 0 || urgentDuishuRepair) &&
    !urgentPeriodRepair &&
    !urgentDuishuRepair &&
    lastRepairMs > 0 &&
    Number.isFinite(lastRepairMs) &&
    Date.now() - lastRepairMs < cooldownMs &&
    String(trigger || "").indexOf("manual") < 0 &&
    String(trigger || "") !== "startup";

  if (!inCooldown) {
    if (urgentDuishuRepair) {
      repairDuishuCapture(actions);
    }
    repairableIssues.forEach((issue) => {
      if (issue.type === "memory-pressure") {
        if (!qualityLockReleaseOnly) trimRuntimeCaches(actions);
      } else clearLocksForIssue(issue, actions);
    });

    const clearedModelLock = actions.some((action) => action && action.type === "delete-lock");
    const forcedRegenerate = !qualityLockReleaseOnly &&
      coreIntegrityIssues.some((issue) => issue && issue.action === "regenerate-current-prediction");
    // 质量清锁只影响下一期选码，禁止整包重生当期已封存预测
    const needsRegenerate = !qualityLockReleaseOnly && (forcedRegenerate || (clearedModelLock && repairableIssues.some((issue) => {
      return issue.type !== "memory-pressure" && !effectiveQualityIssues.includes(issue);
    })));
    if (needsRegenerate) {
      if (syncRunning && !syncOwned) {
        actions.push({ type: "regenerate-skip", reason: "sync-running" });
      } else if (predictionBuildRunning) {
        actions.push({ type: "regenerate-skip", reason: "prediction-build-running" });
      } else {
        regenerateCurrentPredictionForWatchdog(actions);
      }
    }
  }

  const qualityRepairAttempted = effectiveQualityIssues.length > 0;
  if (qualityRepairAttempted) {
    state.modelWatchdog.lastQualityRepairBaseUid = baseUid;
    state.modelWatchdog.lastQualityRepairAt = isoNow();
    effectiveQualityIssues.forEach((issue) => {
      const repairKey = `${String(issue.type || "")}:${String(issue.key || "")}`;
      qualityRepairKeys[repairKey] = {
        baseUid,
        at: state.modelWatchdog.lastQualityRepairAt
      };
    });
    actions.push({
      type: "quality-lock-release",
      baseUid,
      issueKeys: effectiveQualityIssues.map((issue) => String(issue.key || "")).filter(Boolean),
      nextPredictionOnly: true
    });
  }

  state.modelWatchdog.totalChecks += 1;
  state.modelWatchdog.lastCheckAt = startedAt;
  state.modelWatchdog.lastTrigger = String(trigger || "manual");
  state.modelWatchdog.lastIssues = issues.slice(0, 80);
  state.modelWatchdog.lastActions = actions.slice(0, 80);
  if (inCooldown) {
    state.modelWatchdog.lastActions = [{
      type: "cooldown-skip",
      issueCount: repairableIssues.length,
      cooldownMs,
      lastRepairAt: state.modelWatchdog.lastRepairAt
    }];
  } else if (actionableQualityIssues.length > 0 && !effectiveQualityIssues.length && !actions.length) {
    state.modelWatchdog.lastActions = [{
      type: "active-quality-repair-skip",
      issueCount: actionableQualityIssues.length,
      issueKeys: actionableQualityIssues.map((issue) => `${String(issue.type || "")}:${String(issue.key || "")}`),
      lastQualityRepairAt: state.modelWatchdog.lastQualityRepairAt
    }];
  }
  const actualRepairActions = actions.filter((action) => action && [
    "delete-lock",
    "trim-runtime-cache",
    "regenerate-current-prediction",
    "quality-lock-release",
    "repair-duishu-capture"
  ].includes(action.type));
  if (actualRepairActions.length) {
    state.modelWatchdog.totalRepairs += 1;
    state.modelWatchdog.lastRepairAt = isoNow();
    addLog("模型巡检已自动处理", {
      trigger,
      issueCount: issues.length,
      actionCount: actualRepairActions.length,
      issues: issues.slice(0, 20),
      actions: actions.slice(0, 20)
    });
  } else if (issues.length && !inCooldown && !effectiveQualityIssues.length) {
    addLog("模型巡检发现观察项，暂不切换", {
      trigger,
      issueCount: issues.length,
      issues: issues.slice(0, 20)
    });
  }

  // 定时/常规巡检：命中率偏低时打醒目偏差告警（不改账本数字）
  const driftIssues = actionableQualityIssues.filter((issue) => {
    const key = String(issue && issue.key || "");
    if (!issue || issue.type !== "low-performance") return false;
    if (/^position\.(wan|qian|bai|shi)\.main$/.test(key)) return true;
    if (key.indexOf("hewei.") === 0 || key.indexOf("extra.") === 0) return true;
    return false;
  });
  if (driftIssues.length) {
    addLog("【单定顶1偏差告警】命中率低于阈值，已按规则清锁/观察", {
      trigger: String(trigger || ""),
      count: driftIssues.length,
      items: driftIssues.slice(0, 12).map((issue) => ({
        key: issue.key,
        rate: issue.rate,
        total: issue.total,
        threshold: issue.threshold,
        action: issue.action,
        truthSource: issue.truthSource || ""
      })),
      repaired: actualRepairActions.filter((a) => a && (a.type === "delete-lock" || a.type === "quality-lock-release")).length,
      nextPredictionOnly: true
    });
  }

  ensureTipStructurePerfect({
    trigger: String(trigger || "watchdog"),
    fromSync: String(trigger || "").indexOf("sync") >= 0,
    allowRegenerate: true,
    log: false
  });
  if (opts.save !== false) saveState();
  else state.stormMode = evaluateAndApplyStormMode();
  return { ok: true, issueCount: issues.length, actionCount: actions.length, issues, actions, tipReady: !!state.tipReady };
  } catch (err) {
    state.modelWatchdog.lastCheckAt = startedAt;
    state.modelWatchdog.lastTrigger = String(trigger || "manual");
    state.modelWatchdog.lastIssues = [{
      type: "watchdog-error",
      key: "watchdog",
      message: err && err.message ? err.message : String(err)
    }];
    addLog("模型巡检异常，后台继续运行", state.modelWatchdog.lastIssues[0]);
    if (opts.save !== false) saveState();
    return { ok: false, error: state.modelWatchdog.lastIssues[0] };
  } finally {
    modelWatchdogRunning = false;
  }
}

function stableWindowStage(total) {
  const n = Number(total || 0);
  if (n >= MODEL_STABLE_WINDOW) return "stable-1000";
  if (n >= 120) return "watch-120-1000";
  return "observe-under-120";
}

function stableWindowScore(r30, r60, r120, r300, rLong, missPenalty) {
  const total = Math.max(
    Number((rLong && rLong.total) || 0),
    Number((r300 && r300.total) || 0),
    Number((r120 && r120.total) || 0),
    Number((r60 && r60.total) || 0),
    Number((r30 && r30.total) || 0)
  );
  const stage = stableWindowStage(total);
  const v30 = Number((r30 && r30.rate) || 0);
  const v60 = Number((r60 && r60.rate) || v30);
  const v120 = Number((r120 && r120.rate) || v60);
  const v300 = Number((r300 && r300.rate) || v120);
  const vLong = Number((rLong && rLong.rate) || v300);
  const miss30 = Number((r30 && r30.missStreak) || 0);
  const maxMiss120 = Number((r120 && r120.maxMissStreak) || (r120 && r120.missStreak) || 0);
  const maxMiss300 = Number((r300 && r300.maxMissStreak) || (r300 && r300.missStreak) || 0);
  const maxMissLong = Number((rLong && rLong.maxMissStreak) || (rLong && rLong.missStreak) || 0);
  const longMiss = Math.max(maxMiss300, maxMissLong);
  let score;
  if (stage === "stable-1000") {
    score = v30 * 0.04 + v60 * 0.08 + v120 * 0.18 + v300 * 0.35 + vLong * 0.35;
  } else if (stage === "watch-120-1000") {
    score = v30 * 0.08 + v60 * 0.16 + v120 * 0.46 + v300 * 0.30;
  } else {
    score = v30 * 0.18 + v60 * 0.34 + v120 * 0.48;
  }
  score -= miss30 * Number(missPenalty || 0);
  score -= Math.max(0, maxMiss120 - 3) * 0.42;
  score -= Math.max(0, longMiss - 5) * 0.24;
  score -= Math.max(0, v120 - v30 - 8) * 0.16;
  score -= Math.max(0, v300 - v60 - 6) * 0.10;
  return { score: Number(score.toFixed(4)), stage };
}

function wilsonLowerRate(hit, total, z) {
  const n = Number(total || 0);
  if (!n) return 0;
  const successes = Math.max(0, Math.min(n, Number(hit || 0)));
  const zz = Number(z || 1.44);
  const phat = successes / n;
  const denom = 1 + (zz * zz) / n;
  const center = phat + (zz * zz) / (2 * n);
  const margin = zz * Math.sqrt((phat * (1 - phat) + (zz * zz) / (4 * n)) / n);
  return Math.max(0, Math.min(100, ((center - margin) / denom) * 100));
}

function rateVariance(rates) {
  const values = (Array.isArray(rates) ? rates : []).map(Number).filter((n) => Number.isFinite(n));
  if (!values.length) return 0;
  const avg = values.reduce((sum, n) => sum + n, 0) / values.length;
  const variance = values.reduce((sum, n) => sum + Math.pow(n - avg, 2), 0) / values.length;
  return Math.sqrt(variance);
}

function normalizeActualValues(value) {
  if (Array.isArray(value)) {
    return value
      .map((item) => item === undefined || item === null ? "" : String(item))
      .filter((item) => item !== "");
  }
  if (value === undefined || value === null || value === "") return [];
  return [String(value)];
}

function emptyStabilityBlock(window, blockSize, floor) {
  return {
    window,
    blockSize,
    blockCount: 0,
    minRate: null,
    avgRate: null,
    maxRate: null,
    variance: null,
    weakBlocks: 0,
    floor,
    blocks: []
  };
}

function preprocessHeweiActualSeries(records, actualGetter, limit) {
  const rows = (Array.isArray(records) ? records : []).filter(Boolean).slice(0, Math.max(1, Number(limit || MODEL_LONG_WINDOW)));
  return rows.map((record) => normalizeActualValues(actualGetter(record)));
}

function avoidDigitRateMultiWindowFromSeries(series, digit, limits, streakLimit) {
  const lims = [...new Set((Array.isArray(limits) ? limits : [limits]).map((limit) => Math.max(1, Number(limit || MODEL_RATE_WINDOW))))];
  const target = String(digit);
  const maxStreak = streakLimit === undefined ? 8 : Number(streakLimit || 0);
  const results = {};
  lims.forEach((limit) => {
    results[limit] = { total: 0, hit: 0, missStreak: 0, maxMissStreak: 0, rate: 0, _currentRun: 0, _leading: true };
  });
  const rows = Array.isArray(series) ? series : [];
  for (let i = 0; i < rows.length; i += 1) {
    const values = rows[i];
    if (!values || !values.length) continue;
    const ok = values.indexOf(target) < 0;
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
    result.rate = result.total ? result.hit * 100 / result.total : 0;
    delete result._currentRun;
    delete result._leading;
  });
  return results;
}

function avoidDigitRateRows(records, digit, actualGetter, limit, streakLimit) {
  const key = Math.max(1, Number(limit || MODEL_RATE_WINDOW));
  const series = preprocessHeweiActualSeries(records, actualGetter, key);
  const results = avoidDigitRateMultiWindowFromSeries(series, digit, [key], streakLimit);
  return results[key] || { total: 0, hit: 0, rate: 0, missStreak: 0, maxMissStreak: 0 };
}

function avoidDigitBlockStats(records, digit, actualGetter, options) {
  const opts = options && typeof options === "object" ? options : {};
  const window = Number(opts.window || HEWEI_BLOCK_WINDOW);
  const blockSize = Math.max(1, Number(opts.blockSize || HEWEI_BLOCK_SIZE));
  const floor = Number(opts.floor || HEWEI_BLOCK_FLOOR);
  const rows = (Array.isArray(records) ? records : []).slice(0, window);
  const blocks = [];
  for (let start = 0; start < rows.length; start += blockSize) {
    const blockRows = rows.slice(start, start + blockSize);
    if (!blockRows.length) continue;
    const result = avoidDigitRateRows(blockRows, digit, actualGetter, blockRows.length, 0);
    if (result.total > 0) {
      blocks.push({
        index: blocks.length + 1,
        total: result.total,
        hit: result.hit,
        fail: result.total - result.hit,
        rate: Number(result.rate.toFixed(2))
      });
    }
  }
  if (!blocks.length) return emptyStabilityBlock(window, blockSize, floor);
  const rates = blocks.map((block) => Number(block.rate || 0));
  const avg = rates.reduce((sum, n) => sum + n, 0) / rates.length;
  return {
    window,
    blockSize,
    blockCount: blocks.length,
    minRate: Math.min(...rates),
    avgRate: Number(avg.toFixed(2)),
    maxRate: Math.max(...rates),
    variance: Number(rateVariance(rates).toFixed(2)),
    weakBlocks: blocks.filter((block) => Number(block.rate || 0) < floor).length,
    floor,
    blocks
  };
}

function sortStableDigitCandidates(a, b) {
  if (b.score !== a.score) return b.score - a.score;
  if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
  if ((b.block && b.block.minRate || 0) !== (a.block && a.block.minRate || 0)) return (b.block && b.block.minRate || 0) - (a.block && a.block.minRate || 0);
  if (b.r300.rate !== a.r300.rate) return b.r300.rate - a.r300.rate;
  if (b.rLong.rate !== a.rLong.rate) return b.rLong.rate - a.rLong.rate;
  if (b.r120.rate !== a.r120.rate) return b.r120.rate - a.r120.rate;
  if (b.r60.rate !== a.r60.rate) return b.r60.rate - a.r60.rate;
  if (b.r30.rate !== a.r30.rate) return b.r30.rate - a.r30.rate;
  return Number(a.digit) - Number(b.digit);
}

const buildStableHeweiCandidatesLru = new Map();
const BUILD_STABLE_HEWEI_LRU_MAX = Number(process.env.BUILD_STABLE_HEWEI_LRU_MAX || 24);

function buildStableHeweiCandidatesCacheKey(records, getterId, options) {
  const rows = Array.isArray(records) ? records : [];
  const first = rows[0];
  const opts = options && typeof options === "object" ? options : {};
  const recentPeriods = rows.slice(0, 3).map((row) => (row && row.period != null ? String(row.period) : "")).join(",");
  const excludeSig = Array.isArray(opts.excludeDigits) ? opts.excludeDigits.slice().sort().join("") : "";
  return [
    String(getterId || "default"),
    rows.length,
    first && first.uid ? first.uid : "",
    first && Array.isArray(first.numbers) ? first.numbers.slice(0, 5).join("") : "",
    recentPeriods,
    excludeSig,
    Number(opts.floor || HEWEI_BLOCK_FLOOR),
    Number(opts.blockWindow || HEWEI_BLOCK_WINDOW),
    Number(opts.blockSize || HEWEI_BLOCK_SIZE)
  ].join("|");
}

function buildStableHeweiCandidatesCore(records, actualGetter, options) {
  const opts = options && typeof options === "object" ? options : {};
  const floor = Number(opts.floor || HEWEI_BLOCK_FLOOR);
  const missPenalty = Number(opts.missPenalty || 2.5);
  const excludeSet = new Set(
    (Array.isArray(opts.excludeDigits) ? opts.excludeDigits : [])
      .map((digit) => String(digit))
      .filter((digit) => /^\d$/.test(digit))
  );
  const heweiWindows = [30, 60, 120, MODEL_STABLE_WINDOW, MODEL_LONG_WINDOW];
  const series = preprocessHeweiActualSeries(records, actualGetter, MODEL_LONG_WINDOW);
  const candidates = DIGIT_POOL.map((digit) => {
    const digitText = String(digit);
    const rateWindows = avoidDigitRateMultiWindowFromSeries(series, digit, heweiWindows);
    const rLong = rateWindows[MODEL_LONG_WINDOW];
    const r300 = rateWindows[MODEL_STABLE_WINDOW];
    const r120 = rateWindows[120];
    const r60 = rateWindows[60];
    const r30 = rateWindows[30];
    const weighted = stableWindowScore(r30, r60, r120, r300, rLong, missPenalty);
    const block = avoidDigitBlockStats(records, digit, actualGetter, {
      floor,
      window: opts.blockWindow || HEWEI_BLOCK_WINDOW,
      blockSize: opts.blockSize || HEWEI_BLOCK_SIZE
    });
    const lower120 = wilsonLowerRate(r120.hit, r120.total);
    const lower300 = wilsonLowerRate(r300.hit, r300.total);
    const recentPenalty = Number(r30.missStreak || 0) * 1.45;
    const weakPenalty = Number(block.weakBlocks || 0) * 2.2;
    const variancePenalty = Number(block.variance || 0) * 0.08;
    const stableMathPenalty = excludeSet.has(digitText) ? 120 : 0;
    const score = (
      weighted.score * 0.55 +
      lower120 * 0.13 +
      lower300 * 0.08 +
      Number(block.minRate || 0) * 0.12 +
      Number(block.avgRate || 0) * 0.08 -
      recentPenalty -
      weakPenalty -
      variancePenalty -
      stableMathPenalty
    );
    return {
      digit: digitText,
      rLong,
      r300,
      r120,
      r60,
      r30,
      score: Number(score.toFixed(4)),
      stage: weighted.stage,
      block,
      lower120: Number(lower120.toFixed(2)),
      lower300: Number(lower300.toFixed(2)),
      repairFlags: {
        recentMiss: Number(r30.missStreak || 0),
        weakBlocks: Number(block.weakBlocks || 0),
        blockMinRate: Number(block.minRate || 0),
        lower120: Number(lower120.toFixed(2)),
        stableMathExcluded: stableMathPenalty > 0
      }
    };
  });
  candidates.sort(sortStableDigitCandidates);
  return candidates;
}

function buildStableHeweiCandidates(records, actualGetter, options) {
  const opts = Object.assign({}, options && typeof options === "object" ? options : {});
  if (!Array.isArray(opts.excludeDigits) || !opts.excludeDigits.length) {
    const stableExclude = getStableMathExcludeDigitSet();
    if (stableExclude.size) opts.excludeDigits = Array.from(stableExclude);
  }
  const cacheKey = buildStableHeweiCandidatesCacheKey(records, opts.getterId || "default", opts);
  if (buildStableHeweiCandidatesLru.has(cacheKey)) {
    return buildStableHeweiCandidatesLru.get(cacheKey);
  }
  const candidates = buildStableHeweiCandidatesCore(records, actualGetter, opts);
  buildStableHeweiCandidatesLru.set(cacheKey, candidates);
  if (buildStableHeweiCandidatesLru.size > BUILD_STABLE_HEWEI_LRU_MAX) {
    const oldest = buildStableHeweiCandidatesLru.keys().next().value;
    buildStableHeweiCandidatesLru.delete(oldest);
  }
  return candidates;
}

function heweiCandidateWarning(candidate, options) {
  if (!candidate) return true;
  const opts = options && typeof options === "object" ? options : {};
  const guardRate = Number(opts.guardRate || HEWEI_GUARD_RATE);
  const floor = Number(opts.floor || HEWEI_BLOCK_FLOOR);
  const lowerFloor = Number(opts.lowerFloor || Math.max(54, guardRate - 13));
  const block = candidate.block || emptyStabilityBlock(HEWEI_BLOCK_WINDOW, HEWEI_BLOCK_SIZE, floor);
  return Number(candidate.r30 && candidate.r30.missStreak || 0) >= 2 ||
    Number(candidate.r30 && candidate.r30.rate || 0) < guardRate ||
    Number(candidate.r60 && candidate.r60.rate || 0) < guardRate - 5 ||
    Number(candidate.r120 && candidate.r120.rate || 0) < guardRate - 8 ||
    Number(candidate.lower120 || 0) < lowerFloor ||
    Number(block.minRate || 0) < floor ||
    Number(block.weakBlocks || 0) > 0;
}

function selectStableHeweiCandidate(candidates, lockKey, performance, performanceKey, options) {
  const opts = options && typeof options === "object" ? options : {};
  const floor = Number(opts.floor || HEWEI_BLOCK_FLOOR);
  const guardRate = Number(opts.guardRate || HEWEI_GUARD_RATE);
  const lowerFloor = Number(opts.lowerFloor || Math.max(54, guardRate - 13));
  const emptyBlock = emptyStabilityBlock(HEWEI_BLOCK_WINDOW, HEWEI_BLOCK_SIZE, floor);
  const best = candidates[0] || {
    digit: "--",
    score: 0,
    rLong: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r300: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r120: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r60: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r30: { total: 0, hit: 0, rate: null, missStreak: 0 },
    score: 0,
    stage: stableWindowStage(0),
    block: emptyBlock,
    lower120: null,
    lower300: null,
    repairFlags: {},
    truthAvailable: false
  };
  let selected = best;
  let reason = "best-stability-window";
  const perf = getModelStat(performance, performanceKey);
  const perfFloor = Number(opts.performanceFloor || Math.max(68, guardRate - 7));
  const weakPerf = perf.total >= 8 && Number(perf.rate || 0) < perfFloor;

  if (typeof state !== "undefined" && state) {
    if (!state.stable120Locks || typeof state.stable120Locks !== "object") state.stable120Locks = {};
  }
  const locks = (typeof state !== "undefined" && state && state.stable120Locks) ? state.stable120Locks : {};
  const lock = locks[lockKey] || {};
  const oldDigit = lock.digit ? String(lock.digit) : "";
  if (oldDigit) {
    const old = candidates.find((item) => item.digit === oldDigit);
    if (old) {
      const oldHold = Number(lock.hold || 0);
      const oldWarning = heweiCandidateWarning(old, { guardRate, floor, lowerFloor });
      const bestClearlyBetter = best.score > old.score + HEWEI_SWITCH_MARGIN;
      if (!weakPerf && oldHold > 0 && oldHold < HEWEI_MIN_HOLD && !oldWarning && best.score <= old.score + HEWEI_SWITCH_MARGIN * 1.5) {
        selected = old;
        reason = "keep-min-hold-stable-hewei";
      } else if (!weakPerf && !oldWarning && !bestClearlyBetter) {
        selected = old;
        reason = "keep-locked-stable-hewei";
      } else if (!weakPerf && old.r120.rate >= best.r120.rate - 2 && old.r30.missStreak < 2 && Number(old.block && old.block.minRate || 0) >= floor && best.score <= old.score + HEWEI_SWITCH_MARGIN * 1.25) {
        selected = old;
        reason = "keep-close-stable-hewei";
      } else {
        reason = weakPerf ? "weak-hewei-switch" : "warning-switch-hewei-block";
      }
    }
  }

  const selectedWarning = heweiCandidateWarning(selected, { guardRate, floor, lowerFloor });
  const guarded = candidates
    .filter((item) => item &&
      item.digit !== "--" &&
      item.r30.rate >= guardRate &&
      item.r60.rate >= guardRate - 5 &&
      item.r120.rate >= guardRate - 8 &&
      item.r30.missStreak < 2 &&
      Number(item.lower120 || 0) >= lowerFloor &&
      item.block &&
      Number(item.block.minRate || 0) >= floor &&
      Number(item.block.weakBlocks || 0) === 0)
    .sort(sortStableDigitCandidates)[0];
  if (selectedWarning && guarded && guarded.digit !== selected.digit) {
    selected = guarded;
    reason = "hewei-stability-guard";
  }

  const backup = candidates.find((item) => item.digit !== selected.digit) || best;
  const oldValue = oldDigit || "";
  const hold = oldValue && oldValue === selected.digit ? Number(lock.hold || 0) + 1 : 1;
  if (locks && lockKey) {
    locks[lockKey] = {
      digit: selected.digit,
      hold,
      score: Number((selected.score || 0).toFixed(4)),
      rateLong: Number(selected.rLong.rate.toFixed(2)),
      rate300: Number(selected.r300.rate.toFixed(2)),
      rate120: Number(selected.r120.rate.toFixed(2)),
      rate60: Number(selected.r60.rate.toFixed(2)),
      rate30: Number(selected.r30.rate.toFixed(2)),
      lower120: Number((selected.lower120 || 0).toFixed(2)),
      lower300: Number((selected.lower300 || 0).toFixed(2)),
      blockMinRate: Number(((selected.block && selected.block.minRate) || 0).toFixed(2)),
      blockAvgRate: Number(((selected.block && selected.block.avgRate) || 0).toFixed(2)),
      blockWeakBlocks: Number((selected.block && selected.block.weakBlocks) || 0),
      blockVariance: Number(((selected.block && selected.block.variance) || 0).toFixed(2)),
      missStreak30: selected.r30.missStreak,
      stage: selected.stage,
      reason,
      updatedAt: (new Date()).toISOString()
    };
  }
  return { selected, backup, reason, weakPerf };
}

function stableHeweiDetail(candidates) {
  return (Array.isArray(candidates) ? candidates : []).slice(0, 10).map((item) => ({
    digit: item.digit,
    rateLong: Number(item.rLong.rate.toFixed(2)),
    rate300: Number(item.r300.rate.toFixed(2)),
    rate120: Number(item.r120.rate.toFixed(2)),
    rate60: Number(item.r60.rate.toFixed(2)),
    rate30: Number(item.r30.rate.toFixed(2)),
    lower120: Number((item.lower120 || 0).toFixed(2)),
    lower300: Number((item.lower300 || 0).toFixed(2)),
    blockMinRate: Number(((item.block && item.block.minRate) || 0).toFixed(2)),
    blockAvgRate: Number(((item.block && item.block.avgRate) || 0).toFixed(2)),
    blockWeakBlocks: Number((item.block && item.block.weakBlocks) || 0),
    blockVariance: Number(((item.block && item.block.variance) || 0).toFixed(2)),
    missStreak30: item.r30.missStreak,
    score: item.score,
    stage: item.stage
  }));
}

function stableHeweiStability(candidate, options) {
  const opts = options && typeof options === "object" ? options : {};
  const floor = Number(opts.floor || HEWEI_BLOCK_FLOOR);
  return {
    lower120: Number((candidate && candidate.lower120 || 0).toFixed(2)),
    lower300: Number((candidate && candidate.lower300 || 0).toFixed(2)),
    block: (candidate && candidate.block) || emptyStabilityBlock(HEWEI_BLOCK_WINDOW, HEWEI_BLOCK_SIZE, floor),
    repairFlags: (candidate && candidate.repairFlags) || {},
    minHold: HEWEI_MIN_HOLD,
    switchMargin: HEWEI_SWITCH_MARGIN,
    guardRate: Number(opts.guardRate || HEWEI_GUARD_RATE)
  };
}

function pickByRank(scored, useBackup, modelKey, options) {
  const list = Array.isArray(scored) ? scored.filter(Boolean) : [];
  const primary = list[0] || null;
  if (!primary) return { selected: null, primary: null, backup: null, rank: 0, poolSize: 0, stable: true, reason: "empty" };

  const valueOf = (item) => {
    if (!item) return "";
    const keys = ["digit", "value", "sum", "shape", "key", "code", "pair", "label"];
    for (const key of keys) {
      if (item[key] !== undefined && item[key] !== null) return String(item[key]);
    }
    if (Array.isArray(item.digits)) return item.digits.map(String).join("");
    if (Array.isArray(item.killDigits)) return item.killDigits.map(String).join("");
    try { return JSON.stringify(item); } catch (err) { return String(item); }
  };

  const scoreOf = (item) => {
    if (!item) return 0;
    const raw = item.score !== undefined ? item.score : (item.rate !== undefined ? item.rate : (item.weight !== undefined ? item.weight : 0));
    const num = Number(raw);
    return Number.isFinite(num) ? num : 0;
  };

  const distinct = [];
  const seen = {};
  list.forEach((item) => {
    const value = valueOf(item);
    if (value && value !== "--" && !seen[value]) {
      seen[value] = true;
      distinct.push(item);
    }
  });
  if (!distinct.length) distinct.push(primary);

  const cfg = Object.assign({ poolSize: 5, keepGap: 12, unlockGap: 18, backupKeepGap: 6, minHold: 8 }, options || {});
  const poolSize = Math.max(1, Math.min(distinct.length, cfg.poolSize || 5));
  const pool = distinct.slice(0, poolSize);
  const primaryValue = valueOf(primary);
  const backup = distinct.find((item) => valueOf(item) !== primaryValue) || primary;

  let caller = "unknown";
  try {
    caller = String((new Error()).stack || "").split("\n").slice(2, 6).join("|").replace(/\s+/g, " ").slice(0, 220);
  } catch (err) {}
  const keyBase = String(modelKey || caller);
  const side = useBackup ? "backup" : "main";
  // 锁 key 只认模型+主备侧；旧版把候选池签名拼进 key，池子一抖就丢锁，对数每期乱换
  const lockKey = `v28:${keyBase}:${side}`;
  const latest = (typeof state !== "undefined" && state && state.latestRecord) || (typeof state !== "undefined" && state && Array.isArray(state.history) && state.history[0]) || {};
  const period = String(latest.period || latest.uid || "");

  if (typeof state !== "undefined" && state) {
    if (!state.modelSelectionLocks || typeof state.modelSelectionLocks !== "object") state.modelSelectionLocks = {};
  }
  const locks = (typeof state !== "undefined" && state && state.modelSelectionLocks) ? state.modelSelectionLocks : {};
  let lock = locks[lockKey] || {};
  if (!lock.value) {
    // 从旧版「池签名锁」里回收最近一条，避免升级瞬间全员清零
    const prefix = `v28:${keyBase}:`;
    const suffix = `:${side}`;
    let recovered = null;
    Object.keys(locks).forEach((key) => {
      if (key === lockKey) return;
      if (!key.startsWith(prefix) || !key.endsWith(suffix)) return;
      const candidate = locks[key];
      if (!candidate || !candidate.value) return;
      if (!recovered || String(candidate.updatedAt || "") > String(recovered.updatedAt || "")) recovered = candidate;
    });
    if (recovered) lock = recovered;
  }

  const oldValue = lock.value ? String(lock.value) : "";
  const oldItem = oldValue ? (pool.find((item) => valueOf(item) === oldValue) || distinct.find((item) => valueOf(item) === oldValue)) : null;
  const oldScore = oldItem ? scoreOf(oldItem) : -Infinity;
  const primaryScore = scoreOf(primary);
  const backupScore = scoreOf(backup);
  const holdCount = Number(lock.holdCount || 0);

  let selected = primary;
  let reason = "primary-best";

  if (useBackup) {
    if (oldItem && holdCount < cfg.minHold && (backupScore - oldScore) <= cfg.unlockGap) {
      selected = oldItem;
      reason = "low-hit-min-hold";
    } else if (oldItem && (backupScore - oldScore) <= cfg.backupKeepGap) {
      selected = oldItem;
      reason = "low-hit-keep-current-backup";
    } else {
      selected = backup;
      reason = oldItem ? "low-hit-switch-backup" : "low-hit-initial-backup";
    }
  } else if (oldItem && holdCount < cfg.minHold && (primaryScore - oldScore) <= cfg.unlockGap) {
    selected = oldItem;
    reason = "min-hold-lock";
  } else if (oldItem && (primaryScore - oldScore) <= cfg.keepGap) {
    selected = oldItem;
    reason = "stable-lock";
  } else {
    selected = primary;
    reason = oldItem ? "primary-clearly-better" : "primary-initial";
  }

  const selectedValue = valueOf(selected);
  const newHoldCount = oldValue && oldValue === selectedValue ? holdCount + 1 : 1;
  if (locks && lockKey) {
    locks[lockKey] = {
      value: selectedValue,
      period,
      holdCount: newHoldCount,
      reason,
      primaryValue,
      primaryScore,
      selectedScore: scoreOf(selected),
      updatedAt: (new Date()).toISOString()
    };
    // 清掉同模型旧版池签名锁，避免占满 160 配额把有效锁挤掉
    Object.keys(locks).forEach((key) => {
      if (key === lockKey) return;
      if (key.startsWith(`v28:${keyBase}:`) && (key.endsWith(":main") || key.endsWith(":backup"))) {
        delete locks[key];
      }
    });
    const keys = Object.keys(locks);
    if (keys.length > 160) {
      keys.slice(0, keys.length - 160).forEach((key) => { delete locks[key]; });
    }
  }

  const rank = Math.max(0, list.findIndex((item) => valueOf(item) === selectedValue));
  return {
    selected,
    primary,
    backup,
    rank,
    poolSize,
    stable: true,
    reason,
    holdCount: newHoldCount,
    scoreGap: Number((primaryScore - scoreOf(selected)).toFixed ? (primaryScore - scoreOf(selected)).toFixed(2) : (primaryScore - scoreOf(selected)))
  };
}

function sumAvoidCandidates() {
  const out = [];
  for (let i = 10; i <= 26; i++) out.push(String(i));
  return out;
}

function sumRangeAvoidCandidates() {
  const out = [];
  for (let width = 3; width <= 7; width += 1) {
    for (let start = 0; start <= 36 - width + 1; start += 1) {
      const end = start + width - 1;
      out.push(`${start}-${end}`);
    }
  }
  return out;
}

function spanAvoidCandidates() {
  const out = [];
  for (let i = 0; i <= 9; i += 1) out.push(String(i));
  return out;
}

function repeatAvoidCandidates() {
  return ["重号", "一对", "双重", "三重", "四重"];
}

function consecutiveAvoidCandidates() {
  return ["连号", "二连", "三连", "四连"];
}

function parityRatioCandidates() {
  return ["0单4双", "1单3双", "2单2双", "3单1双", "4单0双"];
}

function sizeRatioCandidates() {
  return ["0大4小", "1大3小", "2大2小", "3大1小", "4大0小"];
}

function threeDigitAvoidCandidates() {
  const out = [];
  for (let a = 0; a < 8; a++) {
    for (let b = a + 1; b < 9; b++) {
      for (let c = b + 1; c < 10; c++) out.push(`${a}${b}${c}`);
    }
  }
  return out;
}

function twoDigitAvoidCandidates() {
  const out = [];
  for (let a = 0; a <= 8; a++) {
    for (let b = a + 1; b <= 9; b++) {
      out.push(`${a}${b}`);
    }
  }
  return out;
}

function digitSetCandidates(size) {
  const digits = DIGIT_POOL.slice();
  const out = [];
  function walk(start, picked) {
    if (picked.length === size) {
      out.push(picked.join(""));
      return;
    }
    for (let index = start; index < digits.length; index += 1) {
      picked.push(digits[index]);
      walk(index + 1, picked);
      picked.pop();
    }
  }
  walk(0, []);
  return out;
}

// 合尾5码：禁止全单(13579)/全双(02468)，否则「沾任一合尾即命中」近乎恒真、等于没筛
function isUniformParityDigitSet(value) {
  const digits = uniqueDigits(value);
  if (digits.length < 2) return false;
  let seenOdd = false;
  let seenEven = false;
  for (let index = 0; index < digits.length; index += 1) {
    if (Number(digits[index]) % 2 === 0) seenEven = true;
    else seenOdd = true;
    if (seenOdd && seenEven) return false;
  }
  return seenOdd || seenEven;
}

function digitSetCandidatesMixedParity(size) {
  return digitSetCandidates(size).filter((code) => !isUniformParityDigitSet(code));
}

function digitSetCandidatesFromDigits(sourceDigits, size) {
  const digits = uniqueDigits(Array.isArray(sourceDigits) ? sourceDigits.join("") : sourceDigits);
  const out = [];
  function walk(start, picked) {
    if (picked.length === size) {
      out.push(picked.join(""));
      return;
    }
    for (let index = start; index < digits.length; index += 1) {
      picked.push(digits[index]);
      walk(index + 1, picked);
      picked.pop();
    }
  }
  walk(0, []);
  return out;
}

function anyTwoHeweiCandidates() {
  return digitSetCandidatesFromDigits(ANY_TWO_HEWEI_SOURCE_DIGITS, ANY_TWO_HEWEI_CODE_SIZE);
}

function isAnyTwoHeweiCode(value) {
  const code = String(value || "").replace(/\D/g, "");
  if (code.length !== ANY_TWO_HEWEI_CODE_SIZE) return false;
  if (new Set(code.split("")).size !== ANY_TWO_HEWEI_CODE_SIZE) return false;
  return code.split("").every((digit) => ANY_TWO_HEWEI_SOURCE_DIGITS.includes(digit));
}

function anyTwoHeweiFiveMixedCandidates() {
  return digitSetCandidatesMixedParity(5);
}

function isAnyTwoHeweiFiveMixedCode(value) {
  const code = String(value || "").replace(/\D/g, "");
  if (!/^\d{5}$/.test(code)) return false;
  if (new Set(code.split("")).size !== 5) return false;
  return !isUniformParityDigitSet(code);
}

function anyTwoMaskFromDigitCode(code) {
  let mask = 0;
  String(code || "").replace(/\D/g, "").split("").forEach((digit) => {
    const n = Number(digit);
    if (Number.isFinite(n) && n >= 0 && n <= 9) mask |= (1 << n);
  });
  return mask;
}

function threeDingPairTailsTemplate1000() {
  const out = [];
  for (let m = 0; m < 1000; m += 1) {
    const a = Math.floor(m / 100) % 10;
    const b = Math.floor(m / 10) % 10;
    const c = m % 10;
    out.push([(a + b) % 10, (a + c) % 10, (b + c) % 10]);
  }
  return out;
}

function threeDingAnyTwoStructBasePercent(code) {
  const mask = anyTwoMaskFromDigitCode(code);
  if (!mask) return 100;
  const tripleTails = threeDingPairTailsTemplate1000();
  let structHit = 0;
  for (let i = 0; i < 1000; i += 1) {
    const t = tripleTails[i];
    if (((mask >> t[0]) & 1) || ((mask >> t[1]) & 1) || ((mask >> t[2]) & 1)) structHit += 1;
  }
  return structHit / 10;
}

function compareCandidateValue(left, right) {
  const a = String(left && left.value !== undefined ? left.value : left);
  const b = String(right && right.value !== undefined ? right.value : right);
  const na = Number(a);
  const nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return na - nb;
  return a.localeCompare(b, "zh-CN");
}

function candidateSuccessStats(history, candidate, matcher, limit, hitMode) {
  const stats = candidateSuccessStatsMultiWindow(history, candidate, matcher, [Math.max(1, Number(limit || MODEL_RATE_WINDOW))], hitMode);
  return stats[Math.max(1, Number(limit || MODEL_RATE_WINDOW))] || {
    total: 0,
    hit: 0,
    rate: null,
    missStreak: 0,
    maxMissStreak: 0
  };
}

function candidateSuccessStatsMultiWindow(history, candidate, matcher, limits, hitMode) {
  const lims = [...new Set((Array.isArray(limits) ? limits : [limits]).map((limit) => Math.max(1, Number(limit || MODEL_RATE_WINDOW))))];
  const maxLimit = Math.max(...lims);
  const rows = (Array.isArray(history) ? history : []).slice(0, maxLimit);
  const results = {};
  lims.forEach((limit) => {
    results[limit] = { total: 0, hit: 0, missStreak: 0, maxMissStreak: 0, rate: null, _leading: true, _currentRun: 0 };
  });
  rows.forEach((record, index) => {
    let matched = false;
    try {
      matched = !!matcher(record, candidate);
    } catch (err) {
      matched = false;
    }
    const ok = hitMode ? matched : !matched;
    lims.forEach((limit) => {
      if (index >= limit) return;
      const stat = results[limit];
      stat.total += 1;
      if (ok) {
        stat.hit += 1;
        stat._currentRun = 0;
        stat._leading = false;
      } else {
        stat._currentRun += 1;
        if (stat._currentRun > stat.maxMissStreak) stat.maxMissStreak = stat._currentRun;
        if (stat._leading) stat.missStreak += 1;
      }
    });
  });
  lims.forEach((limit) => {
    const stat = results[limit];
    stat.rate = stat.total ? stat.hit * 100 / stat.total : null;
    delete stat._leading;
    delete stat._currentRun;
  });
  return results;
}

function scoreModelCandidate(history, candidate, matcher, prior, hitMode) {
  const stats = candidateSuccessStatsMultiWindow(history, candidate, matcher, [30, 60, 120, MODEL_STABLE_WINDOW, MODEL_LONG_WINDOW], hitMode);
  const s30 = stats[30];
  const s60 = stats[60];
  const s120 = stats[120];
  const s300 = stats[MODEL_STABLE_WINDOW];
  const sLong = stats[MODEL_LONG_WINDOW];
  const weighted = stableWindowScore(s30, s60, s120, s300, sLong, hitMode ? 0.9 : 1.8);
  const lower120 = s120.total ? wilsonLowerRate(s120.hit, s120.total) : 0;
  const lowerLong = sLong.total ? wilsonLowerRate(sLong.hit, sLong.total) : 0;
  const recentRecoverBonus = Math.max(0, Number(s30.rate || 0) - Number(s120.rate || 0) - 3) * 0.08;
  const missPenalty = Math.max(0, Number(s120.maxMissStreak || 0) - 3) * 0.35 +
    Math.max(0, Number(sLong.maxMissStreak || 0) - 5) * 0.22;
  const score = weighted.score * 0.78 +
    Number(lower120 || 0) * 0.10 +
    Number(lowerLong || 0) * 0.07 +
    Math.min(Number(s60.rate || 0), Number(s120.rate || 0)) * 0.05 +
    recentRecoverBonus -
    missPenalty;
  return {
    s30,
    s60,
    s120,
    s300,
    sLong,
    score: Number(score.toFixed(4)),
    stage: weighted.stage,
    lower120: Number(lower120.toFixed(2)),
    lowerLong: Number(lowerLong.toFixed(2))
  };
}

function scoreAvoidCandidates(history, candidates, matcher, prior) {
  const scored = candidates.map((candidate) => {
    const hard = scoreModelCandidate(history, candidate, matcher, prior, false);
    const longHits = Math.max(0, Number((hard.sLong && hard.sLong.total) || 0) - Number((hard.sLong && hard.sLong.hit) || 0));
    const hasLongSample = Number(hard.sLong && hard.sLong.total || 0) > 0;
    const appearRate = hasLongSample ? 100 - Number(hard.sLong.rate) : null;
    return {
      value: String(candidate),
      sampleTotal: Number(hard.sLong && hard.sLong.total || 0),
      longHits,
      estimatedAppearRate: appearRate === null ? null : Number(appearRate.toFixed(2)),
      score: hard.score,
      rate30: hard.s30.total ? Number(hard.s30.rate.toFixed(2)) : null,
      rate60: hard.s60.total ? Number(hard.s60.rate.toFixed(2)) : null,
      rate120: hard.s120.total ? Number(hard.s120.rate.toFixed(2)) : null,
      rate300: hard.s300.total ? Number(hard.s300.rate.toFixed(2)) : null,
      maxMiss120: Number(hard.s120.maxMissStreak || 0),
      maxMissLong: Number(hard.sLong.maxMissStreak || 0),
      lower120: hard.lower120,
      stage: hard.stage
    };
  });
  scored.sort((a, b) => b.score !== a.score ? b.score - a.score : a.maxMissLong !== b.maxMissLong ? a.maxMissLong - b.maxMissLong : a.longHits !== b.longHits ? a.longHits - b.longHits : compareCandidateValue(a, b));
  return scored;
}

function scoreHitCandidates(history, candidates, matcher, prior) {
  const scored = candidates.map((candidate) => {
    const hard = scoreModelCandidate(history, candidate, matcher, prior, true);
    const longHits = Number((hard.sLong && hard.sLong.hit) || 0);
    return {
      value: String(candidate),
      sampleTotal: Number(hard.sLong && hard.sLong.total || 0),
      longHits,
      estimatedAppearRate: hard.sLong.total ? Number(hard.sLong.rate.toFixed(2)) : null,
      score: hard.score,
      rate30: hard.s30.total ? Number(hard.s30.rate.toFixed(2)) : null,
      rate60: hard.s60.total ? Number(hard.s60.rate.toFixed(2)) : null,
      rate120: hard.s120.total ? Number(hard.s120.rate.toFixed(2)) : null,
      rate300: hard.s300.total ? Number(hard.s300.rate.toFixed(2)) : null,
      maxMiss120: Number(hard.s120.maxMissStreak || 0),
      maxMissLong: Number(hard.sLong.maxMissStreak || 0),
      lower120: hard.lower120,
      stage: hard.stage
    };
  });
  scored.sort((a, b) => b.score !== a.score ? b.score - a.score : a.maxMissLong !== b.maxMissLong ? a.maxMissLong - b.maxMissLong : a.longHits !== b.longHits ? b.longHits - a.longHits : compareCandidateValue(a, b));
  return scored;
}

function calculateExtraAvoidKills(history, performance) {
  function stableDynamicIndex(records, size) {
    if (!size) return 0;
    const latest = records && records[0] ? records[0] : {};
    const periodTail = Number(String(latest.period || latest.uid || "0").replace(/\D/g, "").slice(-4) || 0);
    const nums = Array.isArray(latest.numbers) ? latest.numbers : [];
    const numberSeed = nums.reduce((sum, n, index) => sum + Number(n || 0) * (index + 3), 0);
    return Math.abs(periodTail + numberSeed) % size;
  }

  function calculateOne(key, label, candidates, matcher, prior, mode, options) {
    const opts = options && typeof options === "object" ? options : {};
    const hitMode = mode === "hit";
    const blockedValues = new Set((Array.isArray(opts.excludeValues) ? opts.excludeValues : []).map((value) => String(value)));
    const preferDigits = new Set((Array.isArray(opts.preferDigits) ? opts.preferDigits : []).map((digit) => String(digit)).filter((digit) => /^\d$/.test(digit)));
    const overlapCount = (value) => {
      if (!preferDigits.size) return 0;
      const text = String(value == null ? "" : value).replace(/\D/g, "");
      if (!text) return 0;
      let n = 0;
      const seen = new Set();
      for (const ch of text) {
        if (seen.has(ch)) continue;
        seen.add(ch);
        if (preferDigits.has(ch)) n += 1;
      }
      return n;
    };
    let scored = hitMode ? scoreHitCandidates(history, candidates, matcher, prior) : scoreAvoidCandidates(history, candidates, matcher, prior);
    if (blockedValues.size) {
      scored = scored.filter((item) => !blockedValues.has(String(item && item.value !== undefined ? item.value : "")));
    }
    scored = scored.filter((item) => Number(item && item.sampleTotal || 0) > 0);
    if (preferDigits.size) {
      scored = scored.map((item) => {
        const overlap = overlapCount(item && item.value);
        const bonus = hitMode ? overlap * 0.22 : overlap * 0.85;
        return Object.assign({}, item, {
          preferOverlap: overlap,
          score: Number((Number(item.score || 0) + bonus).toFixed(4))
        });
      });
      scored.sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
    }
    if (!scored.length) {
      return {
        key,
        label,
        value: "",
        code: "",
        primary: "",
        backup: "",
        algorithm: "unavailable-no-real-sample",
        hardnessScore: null,
        rate30: null,
        rate60: null,
        rate120: null,
        rate300: null,
        maxMiss120: null,
        maxMissLong: null,
        lower120: null,
        stage: "暂无真实样本",
        hitMode,
        matchMode: "unavailable",
        jointSwitchAllowed: false,
        dynamicEveryPrediction: false,
        rotationPool: [],
        rotationIndex: null,
        rotationMaxGap: null,
        performance: null,
        truthAvailable: false,
        confidence: null,
        recommendable: false,
        preferOverlap: 0
      };
    }
    const lowRaw = shouldUseBackupAlgorithm(performance, `extra.${key}`);
    // 账本若被历史烂码拖低，但当前候选 rate120 已很高：禁止强制 backup（否则会粘死次优，如 threeDigitHit 长期锁 123/backup）
    const topRate120 = Number(scored[0] && scored[0].rate120);
    const low = !!(lowRaw && !(Number.isFinite(topRate120) && topRate120 >= 90));
    let picked = pickByRank(scored, low, `extra.${key}`, opts.rankOptions || null);
    const firstRealCandidate = scored[0];
    let selected = picked.selected || firstRealCandidate;
    let primary = picked.primary || firstRealCandidate;
    let backup = picked.backup || primary;
    let algorithm = hitMode ? (low ? "dynamic-cooccur-hit-backup-low-hit-rate" : "dynamic-cooccur-hit-primary") : (key === "sumAvoid" ? (low ? "dynamic-practical-sum-backup" : "dynamic-practical-sum-rotation") : (low ? "independent-backup-low-hit-rate" : "dynamic-big-data-primary"));
    let rotationPool = [];
    let rotationIndex = null;
    let rotationMaxGap = null;
    if (!hitMode && key === "sumAvoid" && scored.length) {
      const dynamicPool = scored.slice(0, Math.min(6, scored.length));
      const dynamicPick = dynamicPool[stableDynamicIndex(history, dynamicPool.length)] || selected;
      selected = low ? (dynamicPool[1] || dynamicPick) : dynamicPick;
      primary = dynamicPool[0] || primary;
      backup = dynamicPool[1] || primary;
    }
  if (hitMode && (key === "anyTwoHeweiHit" || key === "anyThreeHeweiHit") && scored.length) {
      // 二/三数合：尽量锁住上一期数字；仅当旧码掉出近顶分差才换（不要换数字）。
      const prevExtra = ((((state || {}).currentPrediction || {}).extraKills || {})[key]) || {};
      const prevCode = String(prevExtra.code || prevExtra.value || "").replace(/\D/g, "");
      const bestScore = Number(scored[0] && scored[0].score || 0);
      const keepGap = 1.0;
      const prevItem = prevCode
        ? scored.find((item) => String(item && item.value || "").replace(/\D/g, "") === prevCode)
        : null;
      const prevScore = prevItem ? Number(prevItem.score || 0) : NaN;
      const keepPrev = !!(prevItem && Number.isFinite(prevScore) && Number.isFinite(bestScore) && (bestScore - prevScore) <= keepGap);
      selected = keepPrev ? prevItem : scored[0];
      primary = scored[0];
      backup = scored[1] || scored[0];
      rotationPool = [];
      rotationIndex = null;
      rotationMaxGap = null;
      algorithm = keepPrev
        ? (key === "anyTwoHeweiHit" ? "sticky-hold-two-hewei" : "sticky-hold-three-hewei")
        : (key === "anyTwoHeweiHit" ? "sticky-top1-two-hewei" : "sticky-top1-three-hewei");
    }
    const selectedValue = String(selected.value !== undefined ? selected.value : (selected.candidate !== undefined ? selected.candidate : ""));
    const primaryValue = String(primary.value !== undefined ? primary.value : (primary.candidate !== undefined ? primary.candidate : selectedValue));
    const backupValue = String(backup.value !== undefined ? backup.value : (backup.candidate !== undefined ? backup.candidate : primaryValue));
    const baselineMeta = highBaselineScoreMeta(key);
    const recommendThreshold = hitMode ? 52 : 78;
    const recommendable = Number(selected.rate120 || selected.rate60 || selected.rate30 || 0) >= recommendThreshold
      && Number(selected.lower120 || 0) >= (hitMode ? 45 : 65);
    const confidence = recommendable ? "normal" : "low";
    return {
      key,
      label,
      value: selectedValue,
      code: selectedValue,
      primary: primaryValue,
      backup: backupValue,
      algorithm,
      hardnessScore: Number(selected.score || 0),
      rate30: selected.rate30,
      rate60: selected.rate60,
      rate120: selected.rate120,
      rate300: selected.rate300,
      maxMiss120: selected.maxMiss120,
      maxMissLong: selected.maxMissLong,
      lower120: selected.lower120,
      stage: selected.stage,
      hitMode,
      matchMode: key === "anyTwoHeweiHit" ? "hit-if-any-two-hewei-tail-in-02468-exclude-one" : (key === "anyThreeHeweiHit" ? "hit-if-any-three-hewei-tail-in-selected-five" : (hitMode ? "hit-if-all-digits-in-first-four" : "avoid-if-not-matched")),
      scoreClass: baselineMeta ? baselineMeta.scoreClass : (hitMode ? "predictive-hit" : "predictive-avoid"),
      randomBaseline: baselineMeta ? baselineMeta.baseline : null,
      scoreHint: baselineMeta ? `${baselineMeta.hint}，非预测力` : "",
      predictivePower: !baselineMeta,
      jointSwitchAllowed: false,
      dynamicEveryPrediction: true,
      rotationPool,
      rotationIndex,
      rotationMaxGap,
      performance: getModelStat(performance, `extra.${key}`) || null,
      confidence,
      recommendable,
      preferOverlap: Number(selected.preferOverlap || 0)
    };
  }

  const stablePreferDigits = Array.from(getStableMathExcludeDigitSet());
  const twoAvoidCandidates = twoDigitAvoidCandidates();
  const twoAvoidPicked = [];
  const twoAvoidRankOptions = { poolSize: 8, keepGap: 10, unlockGap: 16, backupKeepGap: 8, minHold: 12 };
  const twoAvoid1 = calculateOne("twoDigitAvoid", "四定对数避开1组", twoAvoidCandidates, (record, value) => threeDigitCooccurs(record, value), 0.45, null, { excludeValues: twoAvoidPicked, rankOptions: twoAvoidRankOptions, preferDigits: stablePreferDigits });
  if (twoAvoid1.value) twoAvoidPicked.push(twoAvoid1.value);
  const twoAvoid2 = calculateOne("twoDigitAvoid2", "四定对数避开2组", twoAvoidCandidates, (record, value) => threeDigitCooccurs(record, value), 0.45, null, { excludeValues: twoAvoidPicked, rankOptions: twoAvoidRankOptions, preferDigits: stablePreferDigits });
  if (twoAvoid2.value) twoAvoidPicked.push(twoAvoid2.value);
  const twoAvoid3 = calculateOne("twoDigitAvoid3", "四定对数避开3组", twoAvoidCandidates, (record, value) => threeDigitCooccurs(record, value), 0.45, null, { excludeValues: twoAvoidPicked, rankOptions: twoAvoidRankOptions, preferDigits: stablePreferDigits });
  const threeCandidates = threeDigitAvoidCandidates();
  const threePicked = [];
  const threeAvoid1 = calculateOne("threeDigitAvoid", "三数共现避开1组", threeCandidates, (record, value) => threeDigitCooccurs(record, value), 0.25, null, { excludeValues: threePicked, rankOptions: twoAvoidRankOptions, preferDigits: stablePreferDigits });
  if (threeAvoid1.value) threePicked.push(threeAvoid1.value);
  const threeAvoid2 = calculateOne("threeDigitHit", "三数共现避开2组", threeCandidates, (record, value) => threeDigitCooccurs(record, value), 0.25, null, { excludeValues: threePicked, rankOptions: twoAvoidRankOptions, preferDigits: stablePreferDigits });
  if (threeAvoid2.value) threePicked.push(threeAvoid2.value);
  const threeAvoid3 = calculateOne("threeDigitAvoid2", "三数共现避开3组", threeCandidates, (record, value) => threeDigitCooccurs(record, value), 0.25, null, { excludeValues: threePicked, rankOptions: twoAvoidRankOptions, preferDigits: stablePreferDigits });
  return {
    sumAvoid: calculateOne("sumAvoid", "单点和值过滤（高基线）", sumAvoidCandidates(), (record, value) => firstFourSum(record) === Number(value), 0.8),
    sumRangeAvoid: calculateOne("sumRangeAvoid", "和值极端区间过滤（高基线）", sumRangeAvoidCandidates(), (record, value) => firstFourSumInRange(record, value), 0.55),
    spanAvoid: calculateOne("spanAvoid", "跨度极罕形态过滤（高基线）", spanAvoidCandidates(), (record, value) => extraStructureMatched(record, "spanAvoid", value), 0.55),
    repeatAvoid: calculateOne("repeatAvoid", "重号极罕形态过滤（高基线）", repeatAvoidCandidates(), (record, value) => extraStructureMatched(record, "repeatAvoid", value), 0.55),
    consecutiveAvoid: calculateOne("consecutiveAvoid", "连号极罕形态过滤（高基线）", consecutiveAvoidCandidates(), (record, value) => extraStructureMatched(record, "consecutiveAvoid", value), 0.55),
    parityRatioAvoid: calculateOne("parityRatioAvoid", "极端奇偶比过滤（高基线）", parityRatioCandidates(), (record, value) => extraStructureMatched(record, "parityRatioAvoid", value), 0.55),
    sizeRatioAvoid: calculateOne("sizeRatioAvoid", "极端大小比过滤（高基线）", sizeRatioCandidates(), (record, value) => extraStructureMatched(record, "sizeRatioAvoid", value), 0.55),
    threeDigitAvoid: threeAvoid1,
    twoDigitAvoid: twoAvoid1,
    twoDigitAvoid2: twoAvoid2,
    twoDigitAvoid3: twoAvoid3,
    twoDigitHit: calculateOne("twoDigitHit", "两数共现避开（基线参考）", twoDigitAvoidCandidates(), (record, value) => threeDigitCooccurs(record, value), 0.45, null, { rankOptions: twoAvoidRankOptions, preferDigits: stablePreferDigits }),
    threeDigitHit: threeAvoid2,
    threeDigitAvoid2: threeAvoid3,
    anyTwoHeweiHit: calculateOne("anyTwoHeweiHit", "二数合尾02468排1码命中（高基线参考）", anyTwoHeweiCandidates(), (record, value) => anyHeweiCombinationHit(record, value, 2, 1), 0.55, "hit"),
    anyThreeHeweiHit: calculateOne("anyThreeHeweiHit", "三数合尾宽命中（高基线参考）", digitSetCandidatesMixedParity(ANY_THREE_HEWEI_CODE_SIZE), (record, value) => anyHeweiCombinationHit(record, value, 3, 1), 0.62, "hit")
  };
}

const AUTO_TOP6_POSITIONS = [
  { key: "wan", label: "千位", index: 0 },
  { key: "qian", label: "百位", index: 1 },
  { key: "bai", label: "十位", index: 2 },
  { key: "shi", label: "个位", index: 3 }
];
const AUTO_TOP6_HEWEI = [
  { key: "total", label: "总合尾综合", index: -2 },
  { key: "wanQianBai", label: "千百十合尾", index: 0 },
  { key: "qianBaiShi", label: "百十个合尾", index: 1 },
  { key: "wanQianGe", label: "千百个合尾", index: -3 },
  { key: "wanBaiShi", label: "千十个合尾", index: -4 },
  { key: "wanQianBaiShi", label: "千百十个合尾", index: 2 },
  { key: "unified", label: "三个合尾统一", index: -1 }
];
const AUTO_TOP6_EXTRAS = [
  { key: "sumAvoid", label: "单点和值过滤（高基线）", mode: "avoid", kind: "sum", scoreClass: "structural-rare-avoid" },
  { key: "sumRangeAvoid", label: "和值极端区间过滤（高基线）", mode: "avoid", kind: "sumRange", scoreClass: "structural-rare-avoid" },
  { key: "spanAvoid", label: "跨度极罕形态过滤（高基线）", mode: "avoid", kind: "span", scoreClass: "structural-rare-avoid" },
  { key: "repeatAvoid", label: "重号极罕形态过滤（高基线）", mode: "avoid", kind: "structure", scoreClass: "structural-rare-avoid" },
  { key: "consecutiveAvoid", label: "连号极罕形态过滤（高基线）", mode: "avoid", kind: "structure", scoreClass: "structural-rare-avoid" },
  { key: "parityRatioAvoid", label: "极端奇偶比过滤（高基线）", mode: "avoid", kind: "ratio", scoreClass: "structural-rare-avoid" },
  { key: "sizeRatioAvoid", label: "极端大小比过滤（高基线）", mode: "avoid", kind: "ratio", scoreClass: "structural-rare-avoid" },
  { key: "threeDigitAvoid", label: "三数共现避开1组", mode: "avoid", kind: "digits" },
  { key: "twoDigitAvoid", label: "四定对数避开1组", mode: "avoid", kind: "digits" },
  { key: "twoDigitAvoid2", label: "四定对数避开2组", mode: "avoid", kind: "digits" },
  { key: "twoDigitAvoid3", label: "四定对数避开3组", mode: "avoid", kind: "digits" },
  { key: "twoDigitHit", label: "两数共现避开（基线参考）", mode: "avoid", kind: "digits", scoreClass: "pair-avoid-baseline" },
  { key: "threeDigitHit", label: "三数共现避开2组", mode: "avoid", kind: "digits" },
  { key: "threeDigitAvoid2", label: "三数共现避开3组", mode: "avoid", kind: "digits" },
  { key: "anyTwoHeweiHit", label: "二数合尾02468排1码命中（高基线参考）", mode: "hit", kind: "heweiTails", scoreClass: "wide-hit-baseline" },
  { key: "anyThreeHeweiHit", label: "三数合尾宽命中（高基线参考）", mode: "hit", kind: "heweiTails", scoreClass: "wide-hit-baseline" }
];

function finiteNumberOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function autoTop6SafetyThreshold(item) {
  const key = String(item && item.key || "");
  if (item && item.kind === "hewei" && key === "unified") return HEWEI_UNIFIED_GUARD_RATE;
  if (item && item.kind === "hewei") return HEWEI_GUARD_RATE;
  // 高基线模型不用 85% 原始通过率冒充「健康/强预测」
  if (item && item.kind === "extra" && isHighBaselineScoreKey(key)) {
    const meta = highBaselineScoreMeta(key);
    return Math.max(0, Number(meta && meta.baseline || 0) - 3);
  }
  return MODEL_LOW_HIT_RATE;
}

function autoTop6LongFloor(item, threshold) {
  if (item && item.kind === "hewei" && item.key === "unified") return Math.max(55, Number(threshold || 0) - 6);
  return Math.max(70, Number(threshold || 0) - AUTO_TOP6_LONG_FLOOR_OFFSET);
}

function autoTop6LowerFloor(item, threshold) {
  if (item && item.kind === "hewei" && item.key === "unified") return Math.max(50, Number(threshold || 0) - 14);
  return Math.max(68, Number(threshold || 0) - AUTO_TOP6_LOWER_FLOOR_OFFSET);
}

function autoTop6RateFromResults(results) {
  const vals = (Array.isArray(results) ? results : []).filter((value) => typeof value === "boolean");
  if (!vals.length) return null;
  return Number(((vals.filter(Boolean).length / vals.length) * 100).toFixed(2));
}

function autoTop6ItemId(item) {
  return [item && item.kind, item && item.key, item && item.side || "recommend", String(item && item.code)].join(":");
}

// 合尾单码撞号：两个合尾都推同一个数字时，错就一起死。选框内禁止合尾同码并推。
function frameworkHeweiDigitToken(item) {
  if (!item || item.kind !== "hewei") return "";
  if ((item.side || "recommend") === "backup") return "";
  const digits = String(item.code == null ? "" : item.code).replace(/\D/g, "");
  return digits.length === 1 ? digits : "";
}

function frameworkConflictsSameHeweiDigit(chosen, item) {
  const token = frameworkHeweiDigitToken(item);
  if (!token) return false;
  return (Array.isArray(chosen) ? chosen : []).some((picked) => frameworkHeweiDigitToken(picked) === token);
}

// 定位/合尾单码：各算法独立选码；同框禁止同码并推，跨框尽量错开。
function frameworkSingleExcludeDigitToken(item) {
  if (!item) return "";
  if ((item.side || "recommend") === "backup" && item.kind === "position") return "";
  if (item.kind !== "position" && item.kind !== "hewei") return "";
  const digits = String(item.code == null ? "" : item.code).replace(/\D/g, "");
  return digits.length === 1 ? digits : "";
}

function frameworkConflictsSameExcludeDigit(chosen, item) {
  const token = frameworkSingleExcludeDigitToken(item);
  if (!token) return false;
  return (Array.isArray(chosen) ? chosen : []).some((picked) => frameworkSingleExcludeDigitToken(picked) === token);
}

function frameworkSingleExcludeDigits(items) {
  const out = new Set();
  (Array.isArray(items) ? items : []).forEach((item) => {
    const token = frameworkSingleExcludeDigitToken(item);
    if (token) out.add(token);
  });
  return out;
}

// 两数共现/对数码与已选定位/合尾单码重叠次数；重叠越多越容易同死，选框内优先错开。
function frameworkTwoDigitOverlapWithSingles(chosen, item) {
  if (!item) return 0;
  const code = String(item.code == null ? "" : item.code).replace(/\D/g, "");
  if (code.length < 2) return 0;
  const singles = frameworkSingleExcludeDigits(chosen);
  if (!singles.size) return 0;
  let overlap = 0;
  const seen = new Set();
  for (const ch of code) {
    if (seen.has(ch)) continue;
    seen.add(ch);
    if (singles.has(ch)) overlap += 1;
  }
  return overlap;
}

function cleanAutoTop6Code(value) {
  const text = String(value === undefined || value === null ? "" : value).trim();
  if (!text || text === "--" || text === "undefined" || text === "null") return "";
  return text;
}

function autoTop6InferShapeKey(code) {
  const text = String(code || "");
  if (!/^[大小单双]{3,4}$/.test(text)) return "";
  const hasSize = /[大小]/.test(text);
  const hasParity = /[单双]/.test(text);
  if (hasSize && hasParity) return "shapeMixed";
  if (hasSize) return "shapeSize";
  if (hasParity) return "shapeParity";
  return "";
}

function autoTop6PatternOf(record, shapeKey, templateCode) {
  const nums = Array.isArray(record && record.numbers) ? record.numbers.slice(0, 4).map(Number) : [];
  if (nums.length < 4 || nums.some((value) => !Number.isFinite(value))) return "";
  const template = String(templateCode || "");
  const key = shapeKey || autoTop6InferShapeKey(template);
  if (key === "shapeMixed" || (template && autoTop6InferShapeKey(template) === "shapeMixed")) {
    const depth = template.length >= 3 ? Math.min(4, template.length) : 4;
    const parts = [];
    for (let i = 0; i < depth; i += 1) {
      const n = nums[i];
      const ch = template.charAt(i);
      if (ch === "大" || ch === "小") parts.push(n >= 5 ? "大" : "小");
      else if (ch === "单" || ch === "双") parts.push(n % 2 ? "单" : "双");
      else return "";
    }
    return parts.join("");
  }
  if (key === "shapeParity") return nums.map((value) => value % 2 ? "单" : "双").join("");
  if (key === "shapeSize") return nums.map((value) => value >= 5 ? "大" : "小").join("");
  return "";
}

function autoTop6IsThreeWithOneShape(code) {
  const text = String(code || "");
  if (!/^[大小单双]{4}$/.test(text)) return false;
  const counts = {};
  text.split("").forEach((ch) => { counts[ch] = Number(counts[ch] || 0) + 1; });
  const values = Object.keys(counts).map((key) => counts[key]).sort((a, b) => a - b);
  return values.length === 2 && values[0] === 1 && values[1] === 3;
}

function autoTop6ShapePool(shapeKey) {
  const pool = shapeKey === "shapeSize" ? ["大", "小"] : ["单", "双"];
  const out = [];
  pool.forEach((a) => {
    pool.forEach((b) => {
      pool.forEach((c) => {
        pool.forEach((d) => {
          const code = `${a}${b}${c}${d}`;
          if (autoTop6IsThreeWithOneShape(code)) out.push(code);
        });
      });
    });
  });
  return out;
}

function autoTop6ForceThreeWithOne(code, shapeKey) {
  const text = String(code || "");
  if (autoTop6IsThreeWithOneShape(text)) return text;
  if (!/^[大小单双]{4}$/.test(text)) return text;
  const pool = autoTop6ShapePool(shapeKey);
  function distance(candidate) {
    let diff = 0;
    for (let index = 0; index < 4; index += 1) if (candidate[index] !== text[index]) diff += 1;
    return diff;
  }
  return pool.slice().sort((a, b) => {
    const da = distance(a);
    const db = distance(b);
    if (da !== db) return da - db;
    return a.localeCompare(b, "zh-CN");
  })[0] || text;
}

function autoTop6ReverseShapeCandidatesFromPositions(positionItems, history) {
  const locks = {
    shapeParity: [null, null, null, null],
    shapeSize: [null, null, null, null]
  };
  let changed = false;
  (Array.isArray(positionItems) ? positionItems : []).forEach((item) => {
    const pos = AUTO_TOP6_POSITIONS.find((entry) => entry.key === item.key);
    const digitText = cleanAutoTop6Code(item.code).replace(/\D/g, "").charAt(0);
    const digit = Number(digitText);
    if (!pos || !Number.isFinite(digit)) return;
    locks.shapeParity[pos.index] = digit % 2 ? "双" : "单";
    locks.shapeSize[pos.index] = digit >= 5 ? "小" : "大";
    changed = true;
  });
  if (!changed) return [];
  const rows = (Array.isArray(history) ? history : []).filter((record) => record && Array.isArray(record.numbers)).slice(0, 120);
  function complete(shapeKey, fallback) {
    const pool = autoTop6ShapePool(shapeKey);
    const locked = locks[shapeKey];
    const matches = pool.filter((candidate) => {
      for (let index = 0; index < 4; index += 1) {
        if (locked[index] && candidate[index] !== locked[index]) return false;
      }
      return true;
    });
    if (matches.length) {
      function recentSeenScore(candidate) {
        let score = 0;
        rows.forEach((record, index) => {
          if (autoTop6PatternOf(record, shapeKey) === candidate) score += Math.pow(0.94, index);
        });
        return score;
      }
      return matches.slice().sort((a, b) => {
        const sa = recentSeenScore(a);
        const sb = recentSeenScore(b);
        if (sa !== sb) return sa - sb;
        return a.localeCompare(b, "zh-CN");
      })[0] || fallback;
    }
    const out = String(fallback || "").split("");
    for (let index = 0; index < 4; index += 1) {
      if (locked[index]) out[index] = locked[index];
      else if (!/^[大小单双]$/.test(out[index] || "")) out[index] = String(fallback || "单单单双").charAt(index) || "单";
    }
    return autoTop6ForceThreeWithOne(out.join(""), shapeKey);
  }
  const parityCode = autoTop6ForceThreeWithOne(complete("shapeParity", "双双双单"), "shapeParity");
  const sizeCode = autoTop6ForceThreeWithOne(complete("shapeSize", "大大大小"), "shapeSize");
  const out = [];
  if (/^[大小单双]{4}$/.test(parityCode)) {
    out.push({
      kind: "derivedReverseShape",
      key: "reverseParity",
      side: "recommend",
      name: "单双反向形态",
      code: parityCode,
      shapeKey: "shapeParity",
      mode: "avoid",
      derivedFrom: "position-reverse"
    });
  }
  if (/^[大小单双]{4}$/.test(sizeCode)) {
    out.push({
      kind: "derivedReverseShape",
      key: "reverseSize",
      side: "recommend",
      name: "大小反向形态",
      code: sizeCode,
      shapeKey: "shapeSize",
      mode: "avoid",
      derivedFrom: "position-reverse"
    });
  }
  return out;
}

function autoTop6RecordSuccess(item, record) {
  if (!item || !record) return null;
  const code = cleanAutoTop6Code(item.code);
  if (!code) return null;
  if (item.kind === "position") {
    const pos = AUTO_TOP6_POSITIONS.find((entry) => entry.key === item.key);
    const nums = Array.isArray(record.numbers) ? record.numbers.map(String) : [];
    if (!pos || nums[pos.index] === undefined) return null;
    return String(nums[pos.index]) !== code;
  }
  if (item.kind === "hewei") {
    if (item.key === "total" || item.key === "totalHewei") {
      const nums = Array.isArray(record.numbers) ? record.numbers : [];
      if (nums.length < 4) return null;
      const actual = String(calculateMetrics(nums).totalHewei);
      if (!/^\d$/.test(actual)) return null;
      return actual !== code;
    }
    // 后加的千百个/千十个：不走三位组合下标，直接用真实合尾字段。
    if (item.key === "wanQianGe" || item.key === "wanQianGeFreq") {
      const nums = Array.isArray(record.numbers) ? record.numbers : [];
      if (nums.length < 4) return null;
      const actual = String(calculateMetrics(nums).wanQianGeHewei);
      if (!/^\d$/.test(actual)) return null;
      return actual !== code;
    }
    if (item.key === "wanBaiShi" || item.key === "wanBaiShiFreq") {
      const nums = Array.isArray(record.numbers) ? record.numbers : [];
      if (nums.length < 4) return null;
      const actual = String(calculateMetrics(nums).wanBaiShiHewei);
      if (!/^\d$/.test(actual)) return null;
      return actual !== code;
    }
    const group = getCompositeHeweiGroup(record).map(String);
    if (item.key === "unified") return group.length >= 3 ? !group.includes(code) : null;
    const meta = AUTO_TOP6_HEWEI.find((entry) => entry.key === item.key);
    if (!meta || group[meta.index] === undefined) return null;
    return String(group[meta.index]) !== code;
  }
  if (item.kind === "extra") {
    if (item.key === "sumAvoid") return String(firstFourSum(record)) !== code;
    if (item.key === "sumRangeAvoid") return !firstFourSumInRange(record, code);
    if (
      item.key === "spanAvoid" ||
      item.key === "repeatAvoid" ||
      item.key === "consecutiveAvoid" ||
      item.key === "parityRatioAvoid" ||
      item.key === "sizeRatioAvoid"
    ) return !extraStructureMatched(record, item.key, code);
    let matched = false;
    if (item.key === "anyTwoHeweiHit" || item.key === "anyThreeHeweiHit") {
      const size = item.key === "anyTwoHeweiHit" ? 2 : 3;
      const isValidCode = item.key === "anyTwoHeweiHit"
        ? (isAnyTwoHeweiCode(code) || isAnyTwoHeweiFiveMixedCode(code))
        : (/^\d{5}$/.test(code) && new Set(code.split("")).size === ANY_THREE_HEWEI_CODE_SIZE);
      if (!isValidCode) return null;
      const scope = (Array.isArray(item.heweiScopeIndexes) ? item.heweiScopeIndexes : [])
        .map((value) => Number(value))
        .filter((value) => Number.isInteger(value) && value >= 0 && value <= 3);
      const uniqueScope = [...new Set(scope)].sort((left, right) => left - right);
      if (uniqueScope.length >= size) {
        matched = anyHeweiCombinationHitOnNumbers(
          numbersFromRecordByIndexes(record, uniqueScope),
          code,
          size,
          1
        );
      } else {
        matched = anyHeweiCombinationHit(record, code, size, 1);
      }
    } else matched = threeDigitCooccurs(record, code);
    return item.mode === "hit" ? matched : !matched;
  }
  if (item.kind === "derivedReverseShape") {
    const codeText = String(code || "");
    const shapeKey = item.shapeKey
      || autoTop6InferShapeKey(codeText)
      || (item.key === "reverseSize" ? "shapeSize" : "shapeParity");
    const scopeLen = Number(item.shapeScopeLength || 0) === 3 ? 3 : 4;
    if (scopeLen === 3) {
      if (!/^[大小单双]{3,4}$/.test(codeText)) return null;
    } else if (!/^[大小单双]{4}$/.test(codeText)) {
      return null;
    }
    const actualShape = autoTop6PatternOf(record, shapeKey, codeText);
    if (!actualShape) return null;
    if (scopeLen === 3) {
      const scopeIndexes = (Array.isArray(item.shapeScopeIndexes) ? item.shapeScopeIndexes : [])
        .map((value) => Number(value))
        .filter((value) => Number.isInteger(value) && value >= 0 && value <= 3);
      if (scopeIndexes.length === 3) {
        const actualScoped = scopeIndexes.map((index) => actualShape.charAt(index)).join("");
        const codeScoped = scopeIndexes.map((index) => codeText.charAt(index)).join("");
        if (/^[大小单双]{3}$/.test(codeScoped)) return actualScoped !== codeScoped;
      }
      return actualShape.slice(0, 3) !== codeText.slice(0, 3);
    }
    return actualShape !== codeText;
  }
  if (item.kind === "brotherNumber") {
    const actualCode = firstFourCode(record) || firstFourNumbers(record).join("");
    const actual = brotherNumberResultFromCode(actualCode);
    if (actual === null) return null;
    return actual === String(item.code);
  }
  if (item.kind === "shape") {
    const shapeKey = item.key || "shapeParity";
    if (!/^[大小单双]{4}$/.test(code)) return null;
    const avoidCode = autoTop6ForceThreeWithOne(code, shapeKey);
    const actualShape = autoTop6PatternOf(record, shapeKey);
    if (!actualShape) return null;
    return actualShape !== avoidCode;
  }
  return null;
}

function autoTop6Rate(history, item, limit) {
  const rows = (Array.isArray(history) ? history : []).slice(0, Math.max(1, Number(limit || MODEL_RATE_WINDOW)));
  return autoTop6RateFromResults(rows.map((record) => autoTop6RecordSuccess(item, record)));
}

function autoTop6RateStatsFromSuccesses(successes) {
  const vals = (Array.isArray(successes) ? successes : []).filter((value) => typeof value === "boolean");
  let missStreak = 0;
  let currentRun = 0;
  let maxMissStreak = 0;
  for (let i = 0; i < vals.length; i += 1) {
    if (vals[i] === true) break;
    missStreak += 1;
  }
  vals.forEach((ok) => {
    if (ok) {
      currentRun = 0;
    } else {
      currentRun += 1;
      if (currentRun > maxMissStreak) maxMissStreak = currentRun;
    }
  });
  const hit = vals.filter(Boolean).length;
  return {
    total: vals.length,
    hit,
    rate: vals.length ? Number(((hit / vals.length) * 100).toFixed(2)) : null,
    missStreak,
    maxMissStreak
  };
}

function autoTop6RateStats(history, item, limit) {
  const rows = (Array.isArray(history) ? history : []).slice(0, Math.max(1, Number(limit || MODEL_RATE_WINDOW)));
  return autoTop6RateStatsFromSuccesses(rows.map((record) => autoTop6RecordSuccess(item, record)));
}

function autoTop6RateStatsMultiWindow(history, item, limits) {
  const lims = [...new Set((Array.isArray(limits) ? limits : [limits]).map((limit) => Math.max(1, Number(limit || MODEL_RATE_WINDOW))))];
  const maxLimit = Math.max(...lims);
  const rows = (Array.isArray(history) ? history : []).slice(0, maxLimit);
  const successes = rows.map((record) => autoTop6RecordSuccess(item, record));
  const out = {};
  lims.forEach((limit) => {
    out[limit] = autoTop6RateStatsFromSuccesses(successes.slice(0, limit));
  });
  return out;
}

function resolveHeweiAlgorithmMeta(prediction, heweiKey) {
  const detail = prediction && prediction.detail && typeof prediction.detail === "object" ? prediction.detail : {};
  if (heweiKey === "unified") {
    const src = detail.threeFourHeweiKill || {};
    return {
      recommend: prediction && prediction.threeFourHeweiKillDigit || src.killDigit || "",
      main: src.primaryDigit || prediction && prediction.threeFourHeweiKillDigit || src.killDigit || "",
      backup: prediction && prediction.threeFourHeweiBackupDigit || src.backupDigit || "",
      stability: src.stability || null,
      confidence: src.confidence || null,
      recommendable: src.recommendable,
      reason: src.reason || "",
      algorithm: src.algorithm || ""
    };
  }
  if (heweiKey === "total" || heweiKey === "totalHewei") {
    const src = detail.totalHeweiKill || {};
    return {
      recommend: prediction && prediction.totalHeweiKill || src.killHewei || "",
      main: src.primaryDigit || prediction && prediction.totalHeweiKill || src.killHewei || "",
      backup: prediction && prediction.totalHeweiBackupDigit || src.backupDigit || "",
      stability: src.stability || null,
      confidence: src.confidence || null,
      recommendable: src.recommendable,
      reason: src.reason || "",
      algorithm: src.algorithm || ""
    };
  }
  const item = ((prediction && prediction.independentHeweiKills) || {})[heweiKey] || {};
  const detailItem = ((detail.independentHeweiKills) || {})[heweiKey] || item;
  return {
    recommend: item.killDigit || detailItem.killDigit || "",
    main: item.primaryDigit || detailItem.primaryDigit || item.killDigit || "",
    backup: item.backupDigit || detailItem.backupDigit || "",
    stability: detailItem.stability || item.stability || null,
    confidence: detailItem.confidence || item.confidence || null,
    recommendable: detailItem.recommendable !== undefined ? detailItem.recommendable : item.recommendable,
    reason: detailItem.reason || item.reason || "",
    algorithm: detailItem.algorithm || item.algorithm || ""
  };
}

function autoTop6DecorateItem(history, item, evaluatedCount) {
  const stats = autoTop6RateStatsMultiWindow(history, item, [30, 60, 120, MODEL_STABLE_WINDOW]);
  const s30 = stats[30];
  const s60 = stats[60];
  const s120 = stats[120];
  const s300 = stats[MODEL_STABLE_WINDOW];
  const r30 = s30.rate;
  let r60 = s60.rate;
  let r120 = s120.rate;
  let r300 = s300.rate;
  if (r60 === null) r60 = r30;
  if (r120 === null) r120 = r60;
  if (r300 === null) r300 = r120;
  const hard = stableWindowScore(s30, s60, s120, s300, s300, item && item.mode === "hit" ? 1.0 : 2.2);
  let score = hard.score;
  score -= Math.max(0, Number(s120.maxMissStreak || 0) - 3) * 0.28;
  score -= Math.max(0, Number(s300.maxMissStreak || 0) - 5) * 0.18;
  const safetyValues = [r30, r120].map(finiteNumberOrNull).filter((value) => value !== null);
  const safetyRate = safetyValues.length ? Math.min(...safetyValues) : null;
  const threshold = autoTop6SafetyThreshold(item);
  const longFloor = autoTop6LongFloor(item, threshold);
  const lowerFloor = autoTop6LowerFloor(item, threshold);
  const lower120 = s120.total ? Number(wilsonLowerRate(s120.hit, s120.total).toFixed(2)) : null;
  const stableReasons = [];
  if (s30.total >= 20 && r30 !== null && r30 < threshold) stableReasons.push(`30期${Number(r30).toFixed(2)}低于${threshold}`);
  if (s120.total >= 60 && r120 !== null && r120 < threshold) stableReasons.push(`120期${Number(r120).toFixed(2)}低于${threshold}`);
  if (s300.total >= 120 && r300 !== null && r300 < longFloor) stableReasons.push(`1000期${Number(r300).toFixed(2)}低于${longFloor}`);
  if (lower120 !== null && s120.total >= 80 && lower120 < lowerFloor) stableReasons.push(`120期下界${Number(lower120).toFixed(2)}低于${lowerFloor}`);
  if (s30.missStreak >= AUTO_TOP6_RECENT_MISS_LIMIT) stableReasons.push(`当前连错${s30.missStreak}手`);
  if (s120.total >= 80 && s120.maxMissStreak >= 5) stableReasons.push(`120期最大连错${s120.maxMissStreak}手`);
  if (s300.total >= 300 && s300.maxMissStreak >= 7) stableReasons.push(`1000期最大连错${s300.maxMissStreak}手`);

  // 合尾：优先对齐算法层 block stability / recommendable，避免「算法稳定、框架观察」分叉
  const algoStability = item && item.algorithmStability && typeof item.algorithmStability === "object"
    ? item.algorithmStability
    : null;
  if (item && item.kind === "hewei" && algoStability) {
    const block = algoStability.block || {};
    const floor = Number(block.floor || (item.key === "unified" ? HEWEI_UNIFIED_BLOCK_FLOOR : HEWEI_BLOCK_FLOOR));
    if (Number(block.weakBlocks || 0) > 0) stableReasons.push(`算法弱块${Number(block.weakBlocks)}个`);
    if (block.minRate != null && Number(block.minRate) < floor) {
      stableReasons.push(`算法块最低${Number(block.minRate).toFixed(2)}低于${floor}`);
    }
    if (algoStability.lower120 != null && Number(algoStability.lower120) < Number(algoStability.guardRate || threshold) - 13) {
      stableReasons.push(`算法下界${Number(algoStability.lower120).toFixed(2)}不足`);
    }
  }
  if (item && item.kind === "hewei" && item.recommendable === false) {
    stableReasons.push("算法标记观察合尾");
  }

  const stableGate = stableReasons.length === 0;
  return Object.assign({}, item, {
    code: cleanAutoTop6Code(item.code),
    rate: Number(score.toFixed(4)),
    rate30: r30,
    rate60: r60,
    rate120: r120,
    rate300: r300,
    stableScore: Number(score.toFixed(4)),
    hardnessScore: Number(score.toFixed(4)),
    safetyRate,
    safetyThreshold: threshold,
    longFloor,
    lower120,
    recentMissStreak: s30.missStreak,
    maxMissStreak120: s120.maxMissStreak,
    maxMissStreak1000: s300.maxMissStreak,
    stabilityGate: stableGate,
    stabilityReason: stableReasons.join("；"),
    lowConfidence: !stableGate,
    stageLabel: evaluatedCount >= MODEL_STABLE_WINDOW ? "稳定期" : (evaluatedCount >= 120 ? "小修期" : "观察期")
  });
}

function autoTop6CandidateItems(prediction) {
  const list = [];
  const seen = new Set();
  function add(item) {
    const code = cleanAutoTop6Code(item && item.code);
    if (!item || !code) return;
    const clean = Object.assign({}, item, { code });
    const id = [clean.kind, clean.key, code].join(":");
    if (seen.has(id)) return;
    seen.add(id);
    list.push(clean);
  }

  const positionKills = prediction && prediction.positionKills || {};
  AUTO_TOP6_POSITIONS.forEach((pos) => {
    const entry = positionKills[pos.key] || {};
    // 组码主链路只用单定顶1（recommend/main）；dual/backup 仍记账，不进候选池
    const recommend = entry.recommendDigit !== undefined ? entry.recommendDigit : (entry.selectedDigit !== undefined ? entry.selectedDigit : (entry.main !== undefined ? entry.main : (entry.killDigits && entry.killDigits[0])));
    const main = entry.primaryDigit !== undefined ? entry.primaryDigit : (entry.mainAlgorithmDigit !== undefined ? entry.mainAlgorithmDigit : (entry.mainDigit !== undefined ? entry.mainDigit : entry.main));
    add({ kind: "position", key: pos.key, side: "recommend", name: `${pos.label}推荐`, code: recommend });
    add({ kind: "position", key: pos.key, side: "main", name: `${pos.label}主攻`, code: main });
  });

  const independentHeweiKills = prediction && prediction.independentHeweiKills || {};
  AUTO_TOP6_HEWEI.forEach((hewei) => {
    const meta = resolveHeweiAlgorithmMeta(prediction, hewei.key);
    const recommend = meta.recommend;
    const main = meta.main;
    const backup = meta.backup;
    const shared = {
      algorithmStability: meta.stability || null,
      confidence: meta.confidence || null,
      recommendable: meta.recommendable,
      algorithmReason: meta.reason || "",
      algorithm: meta.algorithm || ""
    };
    add(Object.assign({ kind: "hewei", key: hewei.key, side: "recommend", name: `${hewei.label}推荐`, code: recommend }, shared));
    add(Object.assign({ kind: "hewei", key: hewei.key, side: "main", name: `${hewei.label}主攻`, code: main }, shared));
    add(Object.assign({ kind: "hewei", key: hewei.key, side: "backup", name: `${hewei.label}备用`, code: backup }, shared));
  });

  const extraKills = prediction && prediction.extraKills || {};
  AUTO_TOP6_EXTRAS.forEach((extra) => {
    const saved = extraKills[extra.key];
    const code = saved && typeof saved === "object" ? (saved.value !== undefined ? saved.value : saved.code) : saved;
    const extraItem = { kind: "extra", key: extra.key, side: "recommend", name: extra.label, code, mode: extra.mode, extraKind: extra.kind };
    add(extraItem);
  });
  return list;
}

function buildAutoTop6Snapshot(prediction, history) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const candidates = autoTop6CandidateItems(prediction).map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount));
  const healthy = candidates.filter((item) => item && item.stabilityGate === true);
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  const comboRows = safeHistory.slice(0, Math.min(MODEL_STABLE_WINDOW, safeHistory.length));
  let targetItemCount = 6;

  function order(a, b) {
    if (Number(b.stableScore || 0) !== Number(a.stableScore || 0)) return Number(b.stableScore || 0) - Number(a.stableScore || 0);
    if (Number(b.rate300 || 0) !== Number(a.rate300 || 0)) return Number(b.rate300 || 0) - Number(a.rate300 || 0);
    if (Number(b.rate60 || 0) !== Number(a.rate60 || 0)) return Number(b.rate60 || 0) - Number(a.rate60 || 0);
    if (Number(b.rate120 || 0) !== Number(a.rate120 || 0)) return Number(b.rate120 || 0) - Number(a.rate120 || 0);
    return String(a.name || "").localeCompare(String(b.name || ""));
  }

  function comboStats(items) {
    let total = 0;
    let hit = 0;
    let nearHit = 0;
    let validChecks = 0;
    let successChecks = 0;
    comboRows.forEach((record) => {
      const results = (items || []).map((item) => autoTop6RecordSuccess(item, record)).filter((value) => typeof value === "boolean");
      if (results.length !== (items || []).length || !results.length) return;
      total += 1;
      const wins = results.filter(Boolean).length;
      validChecks += results.length;
      successChecks += wins;
      if (wins === results.length) hit += 1;
      if (results.length > 1 && wins >= results.length - 1) nearHit += 1;
    });
    return {
      total,
      hit,
      nearHit,
      rate: total ? hit * 100 / total : null,
      nearRate: total ? nearHit * 100 / total : null,
      avgRate: validChecks ? successChecks * 100 / validChecks : null
    };
  }

  function comboPickScore(item) {
    const stats = comboStats(chosen.concat([item]));
    const full = stats.rate === null ? -1 : Number(stats.rate || 0);
    const near = stats.nearRate === null ? full : Number(stats.nearRate || 0);
    const avg = stats.avgRate === null ? 0 : Number(stats.avgRate || 0);
    return full * 10000 + near * 100 + avg + Number(item.stableScore || 0) / 100;
  }

  function pickBest(pool, maxPerSameModel, allowSameExcludeDigit) {
    const modelCount = {};
    chosen.forEach((item) => {
      const modelKey = `${item.kind}:${item.key}`;
      modelCount[modelKey] = Number(modelCount[modelKey] || 0) + 1;
    });
    let best = null;
    let bestScore = -Infinity;
    (pool || []).forEach((item) => {
      const id = autoTop6ItemId(item);
      const modelKey = `${item.kind}:${item.key}`;
      if (used.has(id)) return;
      if (Number(modelCount[modelKey] || 0) >= Number(maxPerSameModel || 2)) return;
      if (frameworkConflictsSameHeweiDigit(chosen, item)) return;
      // 定位/合尾单码同框禁止相碰；无替代时才允许放宽
      if (!allowSameExcludeDigit && frameworkConflictsSameExcludeDigit(chosen, item)) return;
      const score = comboPickScore(item);
      if (score > bestScore || (score === bestScore && best && order(item, best) < 0)) {
        best = item;
        bestScore = score;
      }
    });
    if (!best) return false;
    used.add(autoTop6ItemId(best));
    chosen.push(best);
    return true;
  }

  function noteLow(pool, label) {
    (pool || []).forEach((item) => {
      if (item.lowConfidence) {
        const rate = item.safetyRate === null ? "样本不足" : `${Number(item.safetyRate).toFixed(2)}%`;
        const reason = item.stabilityReason ? `，原因 ${item.stabilityReason}` : "";
        repairReport.push(`${label}暂停低稳：${item.name} ${item.code}，安全率 ${rate}${reason}`);
      }
    });
  }

  const positionPool = candidates.filter((item) => item.kind === "position");
  const heweiPool = candidates.filter((item) => item.kind === "hewei");
  noteLow(positionPool, "定位池");
  noteLow(heweiPool, "合尾池");

  let before = chosen.length;
  const formalPositionHealthy = healthy.filter((item) => item.kind === "position" && item.side !== "backup");
  const formalPositionPool = positionPool.filter((item) => item.side !== "backup");
  while (chosen.length - before < 3 && pickBest(formalPositionHealthy.slice().sort(order), 2, false)) {}
  while (chosen.length - before < 3 && pickBest(formalPositionPool.slice().sort(order), 2, false)) {
    const item = chosen[chosen.length - 1];
    item.repairedIntoTop = true;
    repairReport.push(`定位池安全阈值内不足，明确加入观察项：${item.name} ${item.code}。`);
  }
  while (chosen.length - before < 3 && pickBest(formalPositionPool.slice().sort(order), 2, true)) {
    const item = chosen[chosen.length - 1];
    item.repairedIntoTop = true;
    repairReport.push(`定位池无错开同码替代，被迫保留同码：${item.name} ${item.code}。`);
  }
  if (chosen.length - before < 3) repairReport.push(`定位池结构不完整：需要3项正式排除（不含替补），实际${chosen.length - before}项。`);

  const reversePool = autoTop6ReverseShapeCandidatesFromPositions(chosen.filter((item) => item.kind === "position"), safeHistory)
    .map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount))
    .sort(order);
  noteLow(reversePool, "反向形态池");
  const healthyReverse = reversePool.filter((item) => item && item.stabilityGate === true);
  if (healthyReverse.length) {
    const picked = healthyReverse.slice().sort((a, b) => {
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    })[0];
    if (!used.has(autoTop6ItemId(picked))) {
      // 反向形态只作参考（side=backup）：不进正式六项全过、不进攻击语；正式结构恒为 3+2+1=6
      picked.side = "backup";
      picked.name = String(picked.name || "反向形态") + "（参考）";
      used.add(autoTop6ItemId(picked));
      chosen.push(picked);
      targetItemCount = 7;
      repairReport.push(`反向形态作参考项保留：${picked.code}；不参与六项全过统计。`);
    }
  } else if (reversePool.length) {
    repairReport.push("反向形态池两路均未达安全阈值，暂不计入本期自动推荐。");
  }

  before = chosen.length;
  const formalHeweiHealthy = healthy.filter((item) => item.kind === "hewei" && item.side !== "backup");
  const formalHeweiPool = heweiPool.filter((item) => item.side !== "backup");
  while (chosen.length - before < 2 && pickBest(formalHeweiHealthy.slice().sort(order), 2, false)) {}
  while (chosen.length - before < 2 && pickBest(formalHeweiPool.slice().sort(order), 2, false)) {
    const item = chosen[chosen.length - 1];
    item.repairedIntoTop = true;
    repairReport.push(`合尾池安全阈值内不足，明确加入观察项：${item.name} ${item.code}。`);
  }
  while (chosen.length - before < 2 && pickBest(formalHeweiPool.slice().sort(order), 2, true)) {
    const item = chosen[chosen.length - 1];
    item.repairedIntoTop = true;
    repairReport.push(`合尾与定位同码相碰无替代，被迫保留同码：${item.name} ${item.code}。`);
  }
  if (chosen.length - before < 2) repairReport.push(`合尾池结构不完整：需要2项正式排除（不含替补），实际${chosen.length - before}项。`);

  // 第6项固定为两数共现避开，绝不写成含对数；也不用对数避开1/2/3组冒充
  const cooccur = candidates
    .filter((item) => item.kind === "extra" && item.key === "twoDigitHit")
    .sort(order);
  noteLow(cooccur, "两数共现池");
  const healthyCooccur = cooccur.filter((item) => item && item.stabilityGate === true);
  if (cooccur.length) {
    const source = healthyCooccur.length ? healthyCooccur : cooccur;
    const picked = source.slice().sort((a, b) => {
      const overlapDelta = frameworkTwoDigitOverlapWithSingles(chosen, a) - frameworkTwoDigitOverlapWithSingles(chosen, b);
      if (overlapDelta) return overlapDelta;
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    })[0];
    picked.name = "两数共现";
    picked.mode = "avoid";
    const overlap = frameworkTwoDigitOverlapWithSingles(chosen, picked);
    if (overlap > 0) {
      repairReport.push(`两数共现 ${picked.code} 与已选单码重叠${overlap}位，候选池无更优错开项时保留。`);
    }
    if (!picked.stabilityGate) {
      picked.repairedIntoTop = true;
      repairReport.push(`两数共现未达安全阈值，仍保留第6项除对数：${picked.code}；不改写成含对数。`);
    }
    if (!used.has(autoTop6ItemId(picked))) {
      used.add(autoTop6ItemId(picked));
      chosen.push(picked);
    }
  } else {
    repairReport.push("两数共现模型缺失，当前快照保持不完整并交由完整性巡检重建，不用对数避开1/2/3组冒充补位。");
  }
  if (chosen.length < targetItemCount) repairReport.push(`框架结构不足${targetItemCount}项，当前仅生成 ${chosen.length} 项；完整性巡检会拒绝并重建，不以无关模型补位。`);

  return {
    version: AUTO_TOP6_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(chosen.length, targetItemCount),
    baseItemCount: 6,
    reverseShapeCount: chosen.some((item) => item.kind === "derivedReverseShape") ? 1 : 0,
    formalItemCount: 6,
    framework: {
      positionCount: 3,
      heweiCount: 2,
      pairHitCount: 1,
      reverseShapeCount: chosen.some((item) => item.kind === "derivedReverseShape") ? 1 : 0,
      semantics: "formal-6=3pos+2hewei+1cooccur-avoid; reverse-shape=backup-reference-only"
    },
    items: chosen.slice(0, targetItemCount).map((item, index) => ({
      rank: index + 1,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      mode: item.mode || "avoid",
      rate: item.rate,
      stableScore: item.stableScore,
      rate30: item.rate30,
      rate60: item.rate60,
      rate120: item.rate120,
      rate300: item.rate300,
      safetyRate: item.safetyRate,
      safetyThreshold: item.safetyThreshold,
      longFloor: item.longFloor,
      lower120: item.lower120,
      recentMissStreak: item.recentMissStreak,
      maxMissStreak120: item.maxMissStreak120,
      maxMissStreak1000: item.maxMissStreak1000,
      hardnessScore: item.hardnessScore,
      stabilityGate: !!item.stabilityGate,
      stabilityReason: item.stabilityReason || "",
      stageLabel: item.stageLabel,
      lowConfidence: !!item.lowConfidence,
      repairedIntoTop: !!item.repairedIntoTop,
      shapeKey: item.shapeKey || "",
      derivedFrom: item.derivedFrom || ""
    })),
    repairReport
  };
}

const AUTO_TOP6_SNAPSHOT_VERSION = "backend-auto-top6-snapshot-v10-formal6-unique-exclude-digit";
const RECOMMEND2_SNAPSHOT_VERSION = "backend-recommend2-snapshot-v16-formal-anytwo4-pool02468-label-v2";
const KILL_FRAMEWORK_SNAPSHOT_VERSION = "backend-kill-framework-snapshot-v8-formal-anytwo4-pool02468-label-v2";
// FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION / ITEM_COUNT 见顶部 fourDingFrameworkAlgo

const KILL_FRAMEWORK_ITEM_COUNT = 8;
const COMPACT_ATTACK_SNAPSHOT_VERSION = "backend-three-ding-framework-v12-pos-aligned-hewei";
const COMPACT_ATTACK_ITEM_COUNT = 4;
const THREE_DING_TWO_POSITION_KEYS = ["wan", "qian", "bai"]; // 千百十
const THREE_DING_ONE_POSITION_KEYS = ["qian", "bai", "shi"]; // 百十个
const THREE_DING_TWO_POSITION_KEY_SET = new Set(THREE_DING_TWO_POSITION_KEYS);
const THREE_DING_ONE_POSITION_KEY_SET = new Set(THREE_DING_ONE_POSITION_KEYS);
const THREE_DING_QBS_KEYS = THREE_DING_TWO_POSITION_KEY_SET; // 兼容旧名：框架A=千百十
const THREE_DING_ALL_POSITION_KEYS = new Set(["wan", "qian", "bai", "shi"]);
const THREE_DING_TWO_HEWEI_KEY = "wanQianBai";
const THREE_DING_ONE_HEWEI_KEY = "qianBaiShi";
const THREE_DING_TWO_SCOPE_INDEXES = [0, 1, 2];
const THREE_DING_ONE_SCOPE_INDEXES = [1, 2, 3];
const THREE_DING_TWO_COMBO_LABEL = "千百十";
const THREE_DING_ONE_COMBO_LABEL = "百十个";
const THREE_DING_POSITION_ORDER = ["wan", "qian", "bai", "shi"];
const THREE_DING_INDEPENDENT_HEWEI_KEYS = new Set(["wanQianBai", "qianBaiShi", "wanQianGe", "wanBaiShi", "wanQianBaiShi"]);

/** 三位位置组合 → 对应合尾模型。 */
function threeDingHeweiKeyForPositions(positionKeys) {
  const set = new Set((Array.isArray(positionKeys) ? positionKeys : []).map(String));
  const has = (key) => set.has(key);
  if (has("wan") && has("qian") && has("bai") && set.size === 3) return "wanQianBai"; // 千百十
  if (has("qian") && has("bai") && has("shi") && set.size === 3) return "qianBaiShi"; // 百十个
  if (has("wan") && has("qian") && has("shi") && set.size === 3) return "wanQianGe"; // 千百个
  if (has("wan") && has("bai") && has("shi") && set.size === 3) return "wanBaiShi"; // 千十个
  return "";
}

function threeDingShapeScopeIndexesFromPositionKeys(positionKeys) {
  return (Array.isArray(positionKeys) ? positionKeys : [])
    .map((key) => {
      const pos = AUTO_TOP6_POSITIONS.find((entry) => entry.key === key);
      return pos ? pos.index : null;
    })
    .filter((value) => Number.isInteger(value) && value >= 0 && value <= 3);
}

function threeDingShapeCodeForPositionKeys(code, positionKeys) {
  const text = String(code || "");
  const indexes = threeDingShapeScopeIndexesFromPositionKeys(positionKeys);
  if (indexes.length !== 3 || !/^[大小单双]{3,4}$/.test(text)) return text.slice(0, 3);
  return indexes.map((index) => text.charAt(index)).join("");
}
const THREE_DING_TWO_SNAPSHOT_VERSION = "backend-three-ding-two-v14-anytwo5-mixed-v1";
const THREE_DING_ANYTWO_MAX_BASE = Number(process.env.THREE_DING_ANYTWO_MAX_BASE || 92);
const THREE_DING_TWO_ITEM_COUNT = 5;
const THREE_DING_ONE_SNAPSHOT_VERSION = "backend-three-ding-one-v14-pos-derived-shape-v1";
const THREE_DING_ONE_ITEM_COUNT = 5;
const BROTHER_NUMBER_PAIRS = ["01", "12", "23", "34", "45", "56", "67", "78", "89", "09"];

function autoTop6SnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const positionCount = formal.filter((item) => item && item.kind === "position").length;
  const heweiCount = formal.filter((item) => item && item.kind === "hewei").length;
  const pairHitCount = formal.filter((item) => item && item.kind === "extra" && item.key === "twoDigitHit").length;
  const reverseCount = items.filter((item) => item && item.kind === "derivedReverseShape").length;
  const reverseAsBackup = items.filter((item) => item && item.kind === "derivedReverseShape" && item.side === "backup").length;
  return !!(
    snapshot &&
    snapshot.version === AUTO_TOP6_SNAPSHOT_VERSION &&
    positionCount === 3 &&
    heweiCount === 2 &&
    pairHitCount === 1 &&
    formal.length === 6 &&
    reverseCount <= 1 &&
    reverseCount === reverseAsBackup &&
    items.length === formal.length + reverseCount
  );
}

function recommend2SnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  // 正式项不含 side=backup；替补只进定位看板，不能冒充 2定位/2合尾
  const formal = items.filter((item) => item && item.side !== "backup");
  const positionCount = formal.filter((item) => item && item.kind === "position").length;
  const heweiCount = formal.filter((item) => item && item.kind === "hewei").length;
  const reverseCount = formal.filter((item) => item && item.kind === "derivedReverseShape").length;
  const anyTwoCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  // 二数合固定为 02468 中排除 1 个的 4 码；三数合为混奇偶 5 码。
  const anyThreeCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyThreeHeweiHit" && /^\d{5}$/.test(String(item.code || ""))).length;
  const pairAvoidCount = formal.filter((item) => item && item.kind === "extra" && ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"].includes(item.key)).length;
  return !!(
    snapshot &&
    snapshot.version === RECOMMEND2_SNAPSHOT_VERSION &&
    formal.length === 8 &&
    items.length === 8 &&
    positionCount === 2 &&
    heweiCount === 2 &&
    reverseCount === 1 &&
    anyTwoCount === 1 &&
    anyThreeCount === 1 &&
    pairAvoidCount === 1
  );
}

function killFrameworkSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  // 正式项不含 side=backup；替补只进定位看板，不能冒充 2定位/2合尾
  const formal = items.filter((item) => item && item.side !== "backup");
  const positionCount = formal.filter((item) => item && item.kind === "position").length;
  const heweiCount = formal.filter((item) => item && item.kind === "hewei").length;
  const reverseCount = formal.filter((item) => item && item.kind === "derivedReverseShape").length;
  const anyTwoCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiCode(item.code)).length;
  const anyThreeCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyThreeHeweiHit" && /^\d{5}$/.test(String(item.code || ""))).length;
  return !!(
    snapshot &&
    snapshot.version === KILL_FRAMEWORK_SNAPSHOT_VERSION &&
    formal.length === KILL_FRAMEWORK_ITEM_COUNT &&
    items.length === KILL_FRAMEWORK_ITEM_COUNT &&
    positionCount === 2 &&
    heweiCount === 2 &&
    reverseCount === 2 &&
    anyTwoCount === 1 &&
    anyThreeCount === 1
  );
}

function fourDingFrameworkSnapshotIsComplete(snapshot) {
  return fourDingFrameworkAlgo.fourDingFrameworkSnapshotIsComplete(snapshot);
}

function ensurePredictionFourDingFrameworkSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  return fourDingFrameworkAlgo.ensurePredictionFourDingFrameworkSnapshot(prediction, history);
}


function brotherNumberResultFromCode(code) {
  const text = String(code || "").replace(/\D/g, "");
  if (text.length < 4) return null;
  for (let i = 0; i < BROTHER_NUMBER_PAIRS.length; i += 1) {
    const pair = BROTHER_NUMBER_PAIRS[i];
    if (text.indexOf(pair.charAt(0)) >= 0 && text.indexOf(pair.charAt(1)) >= 0) return "有";
  }
  return "否";
}

function compactAttackSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const positions = formal.filter((item) => item && item.kind === "position" && THREE_DING_ALL_POSITION_KEYS.has(String(item.key || "")));
  const heweiCount = formal.filter((item) => item && item.kind === "hewei" && THREE_DING_INDEPENDENT_HEWEI_KEYS.has(String(item.key || "")) && /^\d$/.test(String(item.code || ""))).length;
  const uniquePositionKeys = new Set(positions.map((item) => String(item.key || "")));
  return !!(
    snapshot &&
    snapshot.version === COMPACT_ATTACK_SNAPSHOT_VERSION &&
    formal.length === COMPACT_ATTACK_ITEM_COUNT &&
    items.length === COMPACT_ATTACK_ITEM_COUNT &&
    positions.length === 3 &&
    uniquePositionKeys.size === 3 &&
    Array.from(uniquePositionKeys).every((key) => THREE_DING_ALL_POSITION_KEYS.has(key)) &&
    heweiCount === 1 &&
    formal.every((item) => !(item && item.kind === "derivedReverseShape")) &&
    formal.every((item) => !(item && item.kind === "shape"))
  );
}

function threeDingTwoSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const positions = formal.filter((item) => item && item.kind === "position" && THREE_DING_QBS_KEYS.has(String(item.key || "")));
  const uniquePositionKeys = new Set(positions.map((item) => String(item.key || "")));
  const heweiCount = formal.filter((item) => item && item.kind === "hewei" && item.key === "wanQianBai" && /^\d$/.test(String(item.code || ""))).length;
  const reverseCount = formal.filter((item) => item && item.kind === "derivedReverseShape" && /^[大小]{3,4}$/.test(String(item.code || "")) && !/^[单双]{3,4}$/.test(String(item.code || ""))).length;
  const anyTwoCount = formal.filter((item) => item && item.kind === "extra" && item.key === "anyTwoHeweiHit" && isAnyTwoHeweiFiveMixedCode(item.code)).length;
  const pairCount = formal.filter((item) => item && item.kind === "extra" && ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"].includes(String(item.key || "")) && /^\d{2}$/.test(String(item.code || ""))).length;
  return !!(
    snapshot &&
    snapshot.version === THREE_DING_TWO_SNAPSHOT_VERSION &&
    formal.length === THREE_DING_TWO_ITEM_COUNT &&
    items.length === THREE_DING_TWO_ITEM_COUNT &&
    positions.length === 1 &&
    uniquePositionKeys.size === 1 &&
    heweiCount === 1 &&
    reverseCount === 1 &&
    anyTwoCount === 1 &&
    pairCount === 1
  );
}

function threeDingOneSnapshotIsComplete(snapshot) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const formal = items.filter((item) => item && item.side !== "backup");
  const positions = formal.filter((item) => item && item.kind === "position" && THREE_DING_ONE_POSITION_KEY_SET.has(String(item.key || "")));
  const uniquePositionKeys = new Set(positions.map((item) => String(item.key || "")));
  const heweiCount = formal.filter((item) => item && item.kind === "hewei" && item.key === THREE_DING_ONE_HEWEI_KEY && /^\d$/.test(String(item.code || ""))).length;
  const reverseCount = formal.filter((item) => item && item.kind === "derivedReverseShape" && /^[大小]{3,4}$/.test(String(item.code || "")) && !/^[单双]{3,4}$/.test(String(item.code || ""))).length;
  const pairCount = formal.filter((item) => item && item.kind === "extra" && ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"].includes(String(item.key || "")) && /^\d{2}$/.test(String(item.code || ""))).length;
  return !!(
    snapshot &&
    snapshot.version === THREE_DING_ONE_SNAPSHOT_VERSION &&
    formal.length === THREE_DING_ONE_ITEM_COUNT &&
    items.length === THREE_DING_ONE_ITEM_COUNT &&
    positions.length === 2 &&
    uniquePositionKeys.size === 2 &&
    heweiCount === 1 &&
    reverseCount === 1 &&
    pairCount === 1
  );
}


function snapshotSealPayload(snapshot, prediction) {
  const items = snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
  const payload = {
    version: String(snapshot && snapshot.version || ""),
    baseUid: String(prediction && prediction.baseUid || ""),
    basePeriod: String(prediction && prediction.basePeriod || ""),
    items: items.map((item) => {
      let codes = [];
      if (Array.isArray(item && item.codes) && item.codes.length) {
        codes = item.codes.map(String);
      } else if (item && item.codesOmitted) {
        codes = ["omitted:" + String(Number(item.codeCount || 0))];
      }
      return {
        rank: Number(item && item.rank || 0),
        kind: String(item && item.kind || ""),
        key: String(item && item.key || ""),
        side: String(item && item.side || ""),
        code: String(item && item.code || ""),
        mode: String(item && item.mode || ""),
        shapeKey: String(item && item.shapeKey || ""),
        codes
      };
    }),
    txtSelection: snapshot && snapshot.txtSelection || null
  };
  if (snapshot && String(snapshot.version || "") === THREE_DUISHU_SNAPSHOT_VERSION) {
    payload.threeDuishu = {
      code: String(snapshot.code || ""),
      shot: String(snapshot.shot || ""),
      pairs: (Array.isArray(snapshot.pairs) ? snapshot.pairs : []).map(String),
      pairCodes: (Array.isArray(snapshot.pairCodes) ? snapshot.pairCodes : []).map(String),
      selectedPair: String(snapshot.selectedPair || ""),
      selectedPairCode: String(snapshot.selectedPairCode || "")
    };
  }
  return payload;
}

function snapshotSealHash(snapshot, prediction) {
  return crypto.createHash("sha256").update(JSON.stringify(snapshotSealPayload(snapshot, prediction))).digest("hex");
}

function predictionSnapshotsAreImmutable(prediction) {
  return !!(
    prediction &&
    prediction.evaluated !== true &&
    prediction.snapshotsSealedAt &&
    prediction.snapshotsSealedBaseUid &&
    String(prediction.snapshotsSealedBaseUid) === String(prediction.baseUid || "")
  );
}

function sealSnapshot(snapshot, prediction) {
  if (!snapshot || !prediction || prediction.evaluated) return snapshot;
  // 已封存对象不可重签；哈希失效必须作为独立算法异常暴露，不能覆盖证据。
  if (snapshot.sealed === true) return snapshot;
  snapshot.sealed = false;
  snapshot.sealedAt = isoNow();
  snapshot.predictionGeneratedAt = String(prediction.generatedAt || "");
  snapshot.sealedBaseUid = String(prediction.baseUid || "");
  snapshot.sealedBasePeriod = String(prediction.basePeriod || "");
  snapshot.sealHash = snapshotSealHash(snapshot, prediction);
  snapshot.sealed = true;
  return snapshot;
}

function snapshotSealIsValid(snapshot, prediction) {
  if (!snapshot || !prediction || snapshot.sealed !== true) return false;
  if (!snapshot.sealedAt || !snapshot.sealHash) return false;
  if (String(snapshot.sealedBaseUid || "") !== String(prediction.baseUid || "")) return false;
  if (String(snapshot.sealedBasePeriod || "") !== String(prediction.basePeriod || "")) return false;
  if (String(snapshot.predictionGeneratedAt || "") !== String(prediction.generatedAt || "")) return false;
  const generatedMs = Date.parse(String(snapshot.predictionGeneratedAt || ""));
  const sealedMs = Date.parse(String(snapshot.sealedAt || ""));
  if (!Number.isFinite(generatedMs) || !Number.isFinite(sealedMs) || generatedMs > sealedMs) return false;
  return snapshot.sealHash === snapshotSealHash(snapshot, prediction);
}

function snapshotSealPredatesTarget(snapshot, actualRecord) {
  const sealedMs = Date.parse(String(snapshot && snapshot.sealedAt || ""));
  const targetMs = Date.parse(String(actualRecord && actualRecord.opentime || ""));
  if (!Number.isFinite(sealedMs) || !Number.isFinite(targetMs)) return false;
  return sealedMs <= targetMs;
}

function predictionPredatesTarget(prediction, actualRecord) {
  const generatedMs = Date.parse(String(prediction && prediction.generatedAt || ""));
  const targetMs = Date.parse(String(actualRecord && actualRecord.opentime || ""));
  if (!Number.isFinite(generatedMs) || !Number.isFinite(targetMs)) return false;
  return generatedMs <= targetMs;
}

function sealPredictionSnapshots(prediction, latest) {
  if (!prediction || prediction.evaluated) return false;
  if (!latest || String(prediction.baseUid || "") !== String(latest.uid || "")) return false;
  const frameworkSeals = [
    ["fourPosExclude", (snap) => fourPosExcludeAlgo.snapshotIsComplete(snap)],
    ["autoTop6", autoTop6SnapshotIsComplete],
    ["recommend2", recommend2SnapshotIsComplete],
    ["killFramework", killFrameworkSnapshotIsComplete],
    ["fourDingFramework", fourDingFrameworkSnapshotIsComplete],
    ["threeDuishu", threeDuishuSnapshotIsComplete]
  ];

  let sealedCount = 0;
  frameworkSeals.forEach(([key, isComplete]) => {
    const snapshot = prediction[key];
    if (!isComplete(snapshot)) return;
    if (snapshot.sealed === true) {
      if (snapshotSealIsValid(snapshot, prediction)) {
        sealedCount += 1;
        return;
      }
      // 半成品封存（缺 hash / 期号字段）允许重签一次，否则 allSnapshotsSealed 永久卡住
      snapshot.sealed = false;
      snapshot.sealHash = "";
    }
    if (key === "fourPosExclude") {
      fourPosExcludeAlgo.sealSnapshot(snapshot, prediction);
      // 四位排除本地 seal 不写 hash：统一补齐宿主封签字段
      snapshot.sealed = false;
      sealSnapshot(snapshot, prediction);
    } else {
      sealSnapshot(snapshot, prediction);
    }
    if (snapshotSealIsValid(snapshot, prediction)) sealedCount += 1;
  });
  const allSealed = sealedCount === frameworkSeals.length;
  if (sealedCount > 0) {
    prediction.snapshotsSealedAt = isoNow();
    prediction.snapshotsSealedBaseUid = String(prediction.baseUid || "");
    prediction.allSnapshotsSealed = allSealed;
  } else {
    prediction.allSnapshotsSealed = false;
  }
  return allSealed;
}

function ensurePredictionAutoTop6Snapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (autoTop6SnapshotIsComplete(prediction.autoTop6)) return prediction.autoTop6;
  prediction.autoTop6 = buildAutoTop6Snapshot(prediction, history);
  prediction.autoTop6Items = prediction.autoTop6.items;
  prediction.autoTop6RepairReport = prediction.autoTop6.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.autoTop6SnapshotVersion = prediction.autoTop6.version;
  return prediction.autoTop6;
}

function buildRecommend2Snapshot(prediction, history) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const candidates = autoTop6CandidateItems(prediction).map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount));
  const healthy = candidates.filter((item) => item && item.stabilityGate === true);
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  const comboRows = safeHistory.slice(0, Math.min(MODEL_STABLE_WINDOW, safeHistory.length));

  function order(a, b) {
    if (Number(b.stableScore || 0) !== Number(a.stableScore || 0)) return Number(b.stableScore || 0) - Number(a.stableScore || 0);
    if (Number(b.rate300 || 0) !== Number(a.rate300 || 0)) return Number(b.rate300 || 0) - Number(a.rate300 || 0);
    if (Number(b.rate60 || 0) !== Number(a.rate60 || 0)) return Number(b.rate60 || 0) - Number(a.rate60 || 0);
    if (Number(b.rate120 || 0) !== Number(a.rate120 || 0)) return Number(b.rate120 || 0) - Number(a.rate120 || 0);
    return String(a.name || "").localeCompare(String(b.name || ""));
  }

  function comboStats(items) {
    let total = 0;
    let hit = 0;
    let validChecks = 0;
    let successChecks = 0;
    comboRows.forEach((record) => {
      const results = (items || []).map((item) => autoTop6RecordSuccess(item, record)).filter((value) => typeof value === "boolean");
      if (results.length !== (items || []).length || !results.length) return;
      total += 1;
      const wins = results.filter(Boolean).length;
      validChecks += results.length;
      successChecks += wins;
      if (wins === results.length) hit += 1;
    });
    return {
      total,
      hit,
      rate: total ? hit * 100 / total : null,
      avgRate: validChecks ? successChecks * 100 / validChecks : null
    };
  }

  function comboPickScore(item) {
    const stats = comboStats(chosen.concat([item]));
    const full = stats.rate === null ? -1 : Number(stats.rate || 0);
    const avg = stats.avgRate === null ? 0 : Number(stats.avgRate || 0);
    return full * 10000 + avg * 100 + Number(item.stableScore || 0);
  }

  function pickBest(pool, maxPerSameModel, allowSameExcludeDigit) {
    const modelCount = {};
    chosen.forEach((item) => {
      const modelKey = `${item.kind}:${item.key}`;
      modelCount[modelKey] = Number(modelCount[modelKey] || 0) + 1;
    });
    let best = null;
    let bestScore = -Infinity;
    (pool || []).forEach((item) => {
      const id = autoTop6ItemId(item);
      const modelKey = `${item.kind}:${item.key}`;
      if (used.has(id)) return;
      if (Number(modelCount[modelKey] || 0) >= Number(maxPerSameModel || 1)) return;
      if (frameworkConflictsSameHeweiDigit(chosen, item)) return;
      if (!allowSameExcludeDigit && frameworkConflictsSameExcludeDigit(chosen, item)) return;
      const score = comboPickScore(item);
      if (score > bestScore || (score === bestScore && best && order(item, best) < 0)) {
        best = item;
        bestScore = score;
      }
    });
    if (!best) return false;
    used.add(autoTop6ItemId(best));
    chosen.push(best);
    return true;
  }

  function noteLow(pool, label) {
    (pool || []).forEach((item) => {
      if (item.lowConfidence) {
        const rate = item.safetyRate === null ? "样本不足" : `${Number(item.safetyRate).toFixed(2)}%`;
        const reason = item.stabilityReason ? `，原因 ${item.stabilityReason}` : "";
        repairReport.push(`${label}暂停低稳：${item.name} ${item.code}，安全率 ${rate}${reason}`);
      }
    });
  }

  const positionPool = candidates.filter((item) => item.kind === "position" && item.side !== "backup").sort(order);
  const heweiPool = candidates.filter((item) => item.kind === "hewei" && item.side !== "backup").sort(order);
  noteLow(positionPool, "推荐2定位池");
  noteLow(heweiPool, "推荐2合尾池");

  const positionHealthy = healthy.filter((item) => item.kind === "position" && item.side !== "backup").sort(order);
  let before = chosen.length;
  while (chosen.length - before < 2 && pickBest(positionHealthy, 1, false)) {}
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionHealthy, 2, false)) {}
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionPool, 2, false)) {
      const item = chosen[chosen.length - 1];
      item.repairedIntoTop = true;
      repairReport.push(`推荐2定位健康项不足，已用正式观察项补齐：${item.name} ${item.code}。`);
    }
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionPool, 2, true)) {
      const item = chosen[chosen.length - 1];
      item.repairedIntoTop = true;
      repairReport.push(`推荐2定位同码相碰无替代，被迫保留：${item.name} ${item.code}。`);
    }
  }
  if (chosen.length - before < 2) repairReport.push(`推荐2定位正式项不足2个（不含替补），当前定位启用 ${chosen.length - before} 个。`);

  const heweiHealthy = healthy.filter((item) => item.kind === "hewei" && item.side !== "backup").sort(order);
  before = chosen.length;
  while (chosen.length - before < 2 && pickBest(heweiHealthy, 1, false)) {}
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(heweiHealthy, 2, false)) {}
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(heweiPool, 2, false)) {
      const item = chosen[chosen.length - 1];
      item.repairedIntoTop = true;
      repairReport.push(`推荐2合尾健康项不足，已用正式观察项补齐：${item.name} ${item.code}。`);
    }
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(heweiPool, 2, true)) {
      const item = chosen[chosen.length - 1];
      item.repairedIntoTop = true;
      repairReport.push(`推荐2合尾与定位同码相碰无替代，被迫保留：${item.name} ${item.code}。`);
    }
  }
  if (chosen.length - before < 2) repairReport.push(`推荐2合尾正式项不足2个（不含替补），当前合尾启用 ${chosen.length - before} 个。`);

  const reverseBase = chosen.filter((item) => item.kind === "position");
  const reversePool = autoTop6ReverseShapeCandidatesFromPositions(reverseBase, safeHistory)
    .map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount))
    .sort(order);
  noteLow(reversePool, "推荐2反向形态池");
  const reverseHealthy = reversePool.filter((item) => item && item.stabilityGate === true);
  let reversePicked = null;
  if (reverseHealthy.length) {
    reversePicked = reverseHealthy.slice().sort((a, b) => {
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    })[0];
  } else if (reversePool.length) {
    reversePicked = reversePool.slice().sort((a, b) => {
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    })[0];
    if (reversePicked) {
      reversePicked.repairedIntoTop = true;
      repairReport.push(`推荐2反向形态未达安全阈值，暂用最优观察项：${reversePicked.name} ${reversePicked.code}。`);
    }
  }
  if (reversePicked && !used.has(autoTop6ItemId(reversePicked))) {
    used.add(autoTop6ItemId(reversePicked));
    chosen.push(reversePicked);
  } else if (!reversePicked) {
    repairReport.push("推荐2反向形态暂缺，等待定位项稳定后自动恢复。");
  }

  ["anyTwoHeweiHit", "anyThreeHeweiHit"].forEach((extraKey) => {
    const pool = candidates.filter((item) => item && item.kind === "extra" && item.key === extraKey).sort((a, b) => {
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      return order(a, b);
    });
    const picked = pool[0] || null;
    if (!picked) {
      repairReport.push(`推荐2固定模型缺失：${extraKey}`);
      return;
    }
    if (picked.lowConfidence) {
      const rate = picked.safetyRate === null ? "样本不足" : `${Number(picked.safetyRate).toFixed(2)}%`;
      repairReport.push(`推荐2固定加入观察项：${picked.name} ${picked.code}，安全率 ${rate}${picked.stabilityReason ? "，原因 " + picked.stabilityReason : ""}`);
    }
    if (!used.has(autoTop6ItemId(picked))) {
      used.add(autoTop6ItemId(picked));
      chosen.push(picked);
    }
  });

  const pairAvoidKeys = ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"];
  const pairAvoidPool = candidates
    .filter((item) => item && item.kind === "extra" && pairAvoidKeys.includes(item.key))
    .sort((a, b) => {
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    });
  let pairAvoidSource = pairAvoidPool[0] || null;
  if (!pairAvoidSource) {
    const fallbackPair = resolveDuishuFallbackPair(prediction);
    if (/^\d{2}$/.test(fallbackPair)) {
      pairAvoidSource = buildDuishuFallbackItem(fallbackPair, "对数2位避开");
      repairReport.push(`推荐2对数池空，已从三对数/独立组补抓 ${fallbackPair}`);
    } else {
      repairReport.push("推荐2固定模型缺失：对数2位避开");
    }
  }
  if (pairAvoidSource) {
    const pairAvoidPicked = Object.assign({}, pairAvoidSource, { name: "对数2位避开" });
    if (pairAvoidPicked.lowConfidence) {
      const rate = pairAvoidPicked.safetyRate === null ? "样本不足" : `${Number(pairAvoidPicked.safetyRate).toFixed(2)}%`;
      repairReport.push(`推荐2固定加入观察项：${pairAvoidPicked.name} ${pairAvoidPicked.code}，安全率 ${rate}${pairAvoidPicked.stabilityReason ? "，原因 " + pairAvoidPicked.stabilityReason : ""}`);
    }
    if (!used.has(autoTop6ItemId(pairAvoidPicked))) {
      used.add(autoTop6ItemId(pairAvoidPicked));
      chosen.push(pairAvoidPicked);
    }
  }

  return {
    version: RECOMMEND2_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(chosen.length, 8),
    framework: {
      positionCount: 2,
      heweiCount: 2,
      reverseShapeCount: 1,
      heweiComboCount: 2,
      pairAvoidCount: 1
    },
    items: chosen.slice(0, 8).map((item, index) => ({
      rank: index + 1,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      mode: item.mode || "avoid",
      rate: item.rate,
      stableScore: item.stableScore,
      rate30: item.rate30,
      rate60: item.rate60,
      rate120: item.rate120,
      rate300: item.rate300,
      safetyRate: item.safetyRate,
      safetyThreshold: item.safetyThreshold,
      longFloor: item.longFloor,
      lower120: item.lower120,
      recentMissStreak: item.recentMissStreak,
      maxMissStreak120: item.maxMissStreak120,
      maxMissStreak1000: item.maxMissStreak1000,
      hardnessScore: item.hardnessScore,
      stabilityGate: !!item.stabilityGate,
      stabilityReason: item.stabilityReason || "",
      stageLabel: item.stageLabel,
      lowConfidence: !!item.lowConfidence,
      repairedIntoTop: !!item.repairedIntoTop,
      shapeKey: item.shapeKey || "",
      derivedFrom: item.derivedFrom || ""
    })),
    repairReport
  };
}

function ensurePredictionRecommend2Snapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (recommend2SnapshotIsComplete(prediction.recommend2)) return prediction.recommend2;
  prediction.recommend2 = buildRecommend2Snapshot(prediction, history);
  prediction.recommend2Items = prediction.recommend2.items;
  prediction.recommend2RepairReport = prediction.recommend2.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.recommend2SnapshotVersion = prediction.recommend2.version;
  return prediction.recommend2;
}

function buildKillFrameworkSnapshot(prediction, history, options) {
  // 杀码框架：2定位 + 2合尾 + 2反向形态 + 二数合02468排1码 + 三数合5码（无对数避开）
  const opts = options && typeof options === "object" ? options : {};
  const comboWindow = Math.max(30, Math.min(MODEL_STABLE_WINDOW, Number(opts.comboWindow || MODEL_STABLE_WINDOW)));
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const candidates = autoTop6CandidateItems(prediction).map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount));
  const healthy = candidates.filter((item) => item && item.stabilityGate === true);
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  const comboRows = safeHistory.slice(0, Math.min(comboWindow, safeHistory.length));

  function order(a, b) {
    if (Number(b.stableScore || 0) !== Number(a.stableScore || 0)) return Number(b.stableScore || 0) - Number(a.stableScore || 0);
    if (Number(b.rate300 || 0) !== Number(a.rate300 || 0)) return Number(b.rate300 || 0) - Number(a.rate300 || 0);
    if (Number(b.rate60 || 0) !== Number(a.rate60 || 0)) return Number(b.rate60 || 0) - Number(a.rate60 || 0);
    if (Number(b.rate120 || 0) !== Number(a.rate120 || 0)) return Number(b.rate120 || 0) - Number(a.rate120 || 0);
    return String(a.name || "").localeCompare(String(b.name || ""));
  }

  function comboStats(items) {
    let total = 0;
    let hit = 0;
    let validChecks = 0;
    let successChecks = 0;
    comboRows.forEach((record) => {
      const results = (items || []).map((item) => autoTop6RecordSuccess(item, record)).filter((value) => typeof value === "boolean");
      if (results.length !== (items || []).length || !results.length) return;
      total += 1;
      const wins = results.filter(Boolean).length;
      validChecks += results.length;
      successChecks += wins;
      if (wins === results.length) hit += 1;
    });
    return {
      total,
      hit,
      rate: total ? hit * 100 / total : null,
      avgRate: validChecks ? successChecks * 100 / validChecks : null
    };
  }

  function comboPickScore(item) {
    const stats = comboStats(chosen.concat([item]));
    const full = stats.rate === null ? -1 : Number(stats.rate || 0);
    const avg = stats.avgRate === null ? 0 : Number(stats.avgRate || 0);
    return full * 10000 + avg * 100 + Number(item.stableScore || 0);
  }

  function pickBest(pool, maxPerSameModel, allowSameExcludeDigit) {
    const modelCount = {};
    chosen.forEach((item) => {
      const modelKey = `${item.kind}:${item.key}`;
      modelCount[modelKey] = Number(modelCount[modelKey] || 0) + 1;
    });
    let best = null;
    let bestScore = -Infinity;
    (pool || []).forEach((item) => {
      const id = autoTop6ItemId(item);
      const modelKey = `${item.kind}:${item.key}`;
      if (used.has(id)) return;
      if (Number(modelCount[modelKey] || 0) >= Number(maxPerSameModel || 1)) return;
      if (frameworkConflictsSameHeweiDigit(chosen, item)) return;
      if (!allowSameExcludeDigit && frameworkConflictsSameExcludeDigit(chosen, item)) return;
      const score = comboPickScore(item);
      if (score > bestScore || (score === bestScore && best && order(item, best) < 0)) {
        best = item;
        bestScore = score;
      }
    });
    if (!best) return false;
    used.add(autoTop6ItemId(best));
    chosen.push(best);
    return true;
  }

  function noteLow(pool, label) {
    (pool || []).forEach((item) => {
      if (item.lowConfidence) {
        const rate = item.safetyRate === null ? "样本不足" : `${Number(item.safetyRate).toFixed(2)}%`;
        const reason = item.stabilityReason ? `，原因 ${item.stabilityReason}` : "";
        repairReport.push(`${label}暂停低稳：${item.name} ${item.code}，安全率 ${rate}${reason}`);
      }
    });
  }

  const positionPool = candidates.filter((item) => item.kind === "position" && item.side !== "backup").sort(order);
  const heweiPool = candidates.filter((item) => item.kind === "hewei" && item.side !== "backup").sort(order);
  noteLow(positionPool, "杀码框架定位池");
  noteLow(heweiPool, "杀码框架合尾池");

  // 正式 2定位/2合尾：始终只从非 backup 池补齐；allowUnhealthy 仅影响是否允许观察项（已默认允许）
  void opts.allowUnhealthy;
  const positionHealthy = healthy.filter((item) => item.kind === "position" && item.side !== "backup").sort(order);
  let before = chosen.length;
  while (chosen.length - before < 2 && pickBest(positionHealthy, 1, false)) {}
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionHealthy, 2, false)) {}
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionPool, 2, false)) {
      const item = chosen[chosen.length - 1];
      item.repairedIntoTop = true;
    }
    if (chosen.length - before >= 2) repairReport.push("杀码框架定位健康项不足，已用正式观察项补齐（不含替补）。");
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(positionPool, 2, true)) {
      chosen[chosen.length - 1].repairedIntoTop = true;
      repairReport.push(`杀码框架定位同码相碰无替代，被迫保留：${chosen[chosen.length - 1].name} ${chosen[chosen.length - 1].code}。`);
    }
  }
  if (chosen.length - before < 2) repairReport.push(`杀码框架定位正式项不足2个（不含替补），当前定位启用 ${chosen.length - before} 个。`);

  const heweiHealthy = healthy.filter((item) => item.kind === "hewei" && item.side !== "backup").sort(order);
  before = chosen.length;
  while (chosen.length - before < 2 && pickBest(heweiHealthy, 1, false)) {}
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(heweiHealthy, 2, false)) {}
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(heweiPool, 2, false)) {
      const item = chosen[chosen.length - 1];
      item.repairedIntoTop = true;
    }
    if (chosen.length - before >= 2) repairReport.push("杀码框架合尾健康项不足，已用正式观察项补齐（不含替补）。");
  }
  if (chosen.length - before < 2) {
    while (chosen.length - before < 2 && pickBest(heweiPool, 2, true)) {
      chosen[chosen.length - 1].repairedIntoTop = true;
      repairReport.push(`杀码框架合尾与定位同码相碰无替代，被迫保留：${chosen[chosen.length - 1].name} ${chosen[chosen.length - 1].code}。`);
    }
  }
  if (chosen.length - before < 2) repairReport.push(`杀码框架合尾正式项不足2个（不含替补），当前合尾启用 ${chosen.length - before} 个。`);

  const reverseBase = chosen.filter((item) => item.kind === "position");
  // reverse 候选可能为 null（无定位锁）；缺项时必须空数组，避免 .map 抛错堵死事件循环
  const reversePool = (autoTop6ReverseShapeCandidatesFromPositions(reverseBase, safeHistory) || [])
    .map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount))
    .sort(order);
  noteLow(reversePool, "杀码框架反向形态池");
  const reverseHealthy = reversePool.filter((item) => item && item.stabilityGate === true);
  // 先吃健康项，不足 2 再用全池观察项补齐。
  // 旧逻辑 `healthy.length ? healthy : pool` 在只过闸 1 路时会永久缺第 2 项，导致 tipReady 卡死。
  function pushKillReverse(item, forceObserve) {
    if (!item || used.has(autoTop6ItemId(item))) return false;
    if (chosen.filter((row) => row && row.kind === "derivedReverseShape").length >= 2) return false;
    if (forceObserve || !item.stabilityGate) {
      item.repairedIntoTop = true;
      repairReport.push(`杀码框架反向形态未达安全阈值，暂用观察项：${item.name} ${item.code}。`);
    }
    used.add(autoTop6ItemId(item));
    chosen.push(item);
    return true;
  }
  reverseHealthy.slice().sort((a, b) => {
    const delta = comboPickScore(b) - comboPickScore(a);
    return delta || order(a, b);
  }).forEach((item) => pushKillReverse(item, false));
  if (chosen.filter((row) => row && row.kind === "derivedReverseShape").length < 2) {
    reversePool.slice().sort((a, b) => {
      const delta = comboPickScore(b) - comboPickScore(a);
      return delta || order(a, b);
    }).forEach((item) => pushKillReverse(item, true));
  }
  if (chosen.filter((row) => row && row.kind === "derivedReverseShape").length < 2) {
    repairReport.push("杀码框架反向形态不足2个，等待定位项稳定后自动恢复。");
  }

  const killAnyTwo = pickStructuralAnyTwoHeweiHit(
    safeHistory,
    evaluatedCount,
    heweiScopeIndexesFromItems(chosen),
    chosen,
    prediction,
    new Set(),
    resolveStableMathAssist(prediction)
  );
  if (!killAnyTwo) {
    repairReport.push("杀码框架固定模型缺失：anyTwoHeweiHit");
  } else if (!used.has(autoTop6ItemId(killAnyTwo))) {
    repairReport.push(`杀码框架二数合结构对齐：${killAnyTwo.code}`);
    used.add(autoTop6ItemId(killAnyTwo));
    chosen.push(killAnyTwo);
  }

  const killFrameworkAnyThree = digitSetCandidatesMixedParity(ANY_THREE_HEWEI_CODE_SIZE)
    .map((code) => autoTop6DecorateItem(safeHistory, {
      kind: "extra",
      key: "anyThreeHeweiHit",
      side: "recommend",
      name: "全码池三数合尾5码命中",
      code,
      mode: "hit",
      algorithm: "kill-framework-any-three-hewei-selected-five",
      hitMode: true,
      matchMode: "hit-if-any-three-hewei-tail-in-selected-five",
      dynamicEveryPrediction: true
    }, evaluatedCount))
    .sort((a, b) => {
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      return order(a, b);
    })[0] || null;
  if (killFrameworkAnyThree) {
    if (killFrameworkAnyThree.lowConfidence) {
      const rate = killFrameworkAnyThree.safetyRate === null ? "样本不足" : `${Number(killFrameworkAnyThree.safetyRate).toFixed(2)}%`;
      repairReport.push(`杀码框架三数合5码观察加入：${killFrameworkAnyThree.code}，安全率 ${rate}`);
      killFrameworkAnyThree.repairedIntoTop = true;
    }
    if (!used.has(autoTop6ItemId(killFrameworkAnyThree))) {
      used.add(autoTop6ItemId(killFrameworkAnyThree));
      chosen.push(killFrameworkAnyThree);
    }
  } else {
    repairReport.push("杀码框架固定模型缺失：anyThreeHeweiHit五码");
  }

  return {
    version: KILL_FRAMEWORK_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(chosen.length, KILL_FRAMEWORK_ITEM_COUNT),
    framework: {
      positionCount: 2,
      heweiCount: 2,
      reverseShapeCount: 2,
      heweiComboCount: 2,
      pairAvoidCount: 0
    },
    items: chosen.slice(0, KILL_FRAMEWORK_ITEM_COUNT).map((item, index) => ({
      rank: index + 1,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      mode: item.mode || "avoid",
      rate: item.rate,
      stableScore: item.stableScore,
      rate30: item.rate30,
      rate60: item.rate60,
      rate120: item.rate120,
      rate300: item.rate300,
      safetyRate: item.safetyRate,
      safetyThreshold: item.safetyThreshold,
      longFloor: item.longFloor,
      lower120: item.lower120,
      recentMissStreak: item.recentMissStreak,
      maxMissStreak120: item.maxMissStreak120,
      maxMissStreak1000: item.maxMissStreak1000,
      hardnessScore: item.hardnessScore,
      stabilityGate: !!item.stabilityGate,
      stabilityReason: item.stabilityReason || "",
      stageLabel: item.stageLabel,
      lowConfidence: !!item.lowConfidence,
      repairedIntoTop: !!item.repairedIntoTop,
      shapeKey: item.shapeKey || "",
      derivedFrom: item.derivedFrom || ""
    })),
    repairReport
  };
}

function ensurePredictionKillFrameworkSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (killFrameworkSnapshotIsComplete(prediction.killFramework)) return prediction.killFramework;
  prediction.killFramework = buildKillFrameworkSnapshot(prediction, history);
  prediction.killFrameworkItems = prediction.killFramework.items;
  prediction.killFrameworkRepairReport = prediction.killFramework.repairReport;
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.killFrameworkSnapshotVersion = prediction.killFramework.version;
  return prediction.killFramework;
}

function compactAttackParityShapeItem(history) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const rows = safeHistory.slice(0, 120);
  const pool = autoTop6ShapePool("shapeParity");
  function recentSeenScore(candidate) {
    let score = 0;
    rows.forEach((record, index) => {
      if (autoTop6PatternOf(record, "shapeParity") === candidate) score += Math.pow(0.94, index);
    });
    return score;
  }
  const rawCode = pool.slice().sort((a, b) => {
    const sa = recentSeenScore(a);
    const sb = recentSeenScore(b);
    if (sa !== sb) return sa - sb;
    return a.localeCompare(b, "zh-CN");
  })[0] || "双双双单";
  const code = autoTop6ForceThreeWithOne(rawCode, "shapeParity");
  const evaluatedCount = Array.isArray(state && state.predictions)
    ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length
    : safeHistory.length;
  return autoTop6DecorateItem(safeHistory, {
    kind: "shape",
    key: "shapeParity",
    side: "recommend",
    name: "单双形态推荐",
    code,
    mode: "avoid"
  }, evaluatedCount);
}

function buildCompactAttackSnapshot(prediction, history, options) {
  // 三定框架 v11：千百十个择优取3位、每位排1码；合尾独立取1项，互不干预。
  const opts = options && typeof options === "object" ? options : {};
  const comboWindow = Math.max(30, Math.min(MODEL_STABLE_WINDOW, Number(opts.comboWindow || MODEL_STABLE_WINDOW)));
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const candidates = autoTop6CandidateItems(prediction).map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount));
  const healthy = candidates.filter((item) => item && item.stabilityGate === true);
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  const comboRows = safeHistory.slice(0, Math.min(comboWindow, safeHistory.length));
  const assist = resolveStableMathAssist(prediction);
  const preferExcludeDigits = new Set((assist && assist.ok && Array.isArray(assist.excludeDigits)) ? assist.excludeDigits.map(String) : []);

  function order(a, b) {
    if (Number(b.stableScore || 0) !== Number(a.stableScore || 0)) return Number(b.stableScore || 0) - Number(a.stableScore || 0);
    if (Number(b.hardnessScore || 0) !== Number(a.hardnessScore || 0)) return Number(b.hardnessScore || 0) - Number(a.hardnessScore || 0);
    if (Number(b.rate300 || 0) !== Number(a.rate300 || 0)) return Number(b.rate300 || 0) - Number(a.rate300 || 0);
    if (Number(b.rate60 || 0) !== Number(a.rate60 || 0)) return Number(b.rate60 || 0) - Number(a.rate60 || 0);
    if (Number(b.rate120 || 0) !== Number(a.rate120 || 0)) return Number(b.rate120 || 0) - Number(a.rate120 || 0);
    return String(a.name || "").localeCompare(String(b.name || ""));
  }

  function comboStats(items) {
    let total = 0;
    let hit = 0;
    let validChecks = 0;
    let successChecks = 0;
    comboRows.forEach((record) => {
      const results = (items || []).map((item) => autoTop6RecordSuccess(item, record)).filter((value) => typeof value === "boolean");
      if (results.length !== (items || []).length || !results.length) return;
      total += 1;
      const wins = results.filter(Boolean).length;
      validChecks += results.length;
      successChecks += wins;
      if (wins === results.length) hit += 1;
    });
    return {
      total,
      hit,
      rate: total ? hit * 100 / total : null,
      avgRate: validChecks ? successChecks * 100 / validChecks : null
    };
  }

  function comboPickScore(item) {
    const stats = comboStats(chosen.concat([item]));
    const full = stats.rate === null ? -1 : Number(stats.rate || 0);
    const avg = stats.avgRate === null ? 0 : Number(stats.avgRate || 0);
    return full * 10000 + avg * 100 + Number(item.stableScore || 0);
  }

  function positionStrengthScore(item) {
    // 定位加强：健康门 + 硬度 + 更稳排除码优先 + 近窗命中 + 无动态单码时形态/跨度优先
    const digit = frameworkSingleExcludeDigitToken(item);
    const gateBoost = item && item.stabilityGate ? 12000 : 0;
    const hardnessBoost = Number(item && item.hardnessScore || 0) * 40;
    const preferBoost = digit && preferExcludeDigits.has(digit) ? 18000 : 0;
    const stableBoost = stableMathAssistPickBoost(assist, item);
    const safetyBoost = Number(item && item.safetyRate || 0) * 20;
    const missPenalty = Number(item && item.recentMissStreak || 0) * 80;
    return gateBoost + hardnessBoost + preferBoost + stableBoost + safetyBoost - missPenalty;
  }

  function noteLow(pool, label) {
    (pool || []).forEach((item) => {
      if (item.lowConfidence) {
        const rate = item.safetyRate === null ? "样本不足" : `${Number(item.safetyRate).toFixed(2)}%`;
        const reason = item.stabilityReason ? `，原因 ${item.stabilityReason}` : "";
        repairReport.push(`${label}暂停低稳：${item.name} ${item.code}，安全率 ${rate}${reason}`);
      }
    });
  }

  function promoteIfBackup(item, kindLabel) {
    if (!item) return item;
    if ((item.side || "recommend") !== "backup") return item;
    const next = Object.assign({}, item, {
      side: "recommend",
      repairedIntoTop: true,
      derivedFrom: item.derivedFrom || (item.kind === "hewei" ? "hewei-backup-algorithm" : "position-backup-algorithm")
    });
    if (item.kind === "hewei") {
      next.name = String(item.name || "").replace(/备用$/, "推荐") || "合尾推荐";
    } else {
      next.name = String(item.name || "").replace(/备用$/, "推荐") || `${kindLabel || "定位"}备用算法`;
    }
    return next;
  }

  function commitItem(item) {
    if (!item) return false;
    const id = autoTop6ItemId(item);
    if (used.has(id)) return false;
    used.add(id);
    chosen.push(item);
    return true;
  }

  // 1) 先择优三位定位（位置排除）；合尾必须跟这三位对齐。
  const positionPool = candidates.filter((item) => item.kind === "position" && THREE_DING_ALL_POSITION_KEYS.has(String(item.key || ""))).sort(order);
  noteLow(positionPool, "三定框架定位池");
  void opts.allowUnhealthy;
  const positionHealthy = healthy.filter((item) => item.kind === "position" && THREE_DING_ALL_POSITION_KEYS.has(String(item.key || ""))).sort(order);
  // 组码只认单定顶1（main/recommend）；dual/backup 不进 compact 主链路
  const sideOrder = ["main", "recommend"];
  if (preferExcludeDigits.size) {
    repairReport.push(`三定框架定位加强：更稳排除码优先 ${Array.from(preferExcludeDigits).join("")}`);
  } else if ((assist.excludeShapes || []).length || (assist.excludeSpans || []).length) {
    repairReport.push(`三定框架定位加强：更稳形态/跨度辅助（单码空：形态${(assist.excludeShapes || []).slice(0, 2).join("、") || "无"}，跨度${(assist.excludeSpans || []).slice(0, 3).join("、") || "无"}）`);
  } else {
    repairReport.push("三定框架定位加强：优先主攻/推荐（单定顶1）");
  }

  function bestCandidateForKey(key) {
    const pools = [
      positionHealthy.filter((item) => String(item.key) === key),
      positionPool.filter((item) => String(item.key) === key)
    ];
    let selected = null;
    let selectedScore = -Infinity;
    for (let p = 0; p < pools.length; p += 1) {
      for (let s = 0; s < sideOrder.length; s += 1) {
        const side = sideOrder[s];
        const pool = pools[p].filter((item) => (item.side || "recommend") === side).sort(order);
        pool.forEach((raw) => {
          const item = promoteIfBackup(raw, "定位");
          const id = autoTop6ItemId(item);
          if (used.has(id)) return;
          const score = comboPickScore(item) + positionStrengthScore(item) - s * 20 - p * 1000;
          if (score > selectedScore || (score === selectedScore && selected && order(item, selected) < 0)) {
            selected = item;
            selectedScore = score;
          }
        });
      }
    }
    return selected ? { key, item: selected, score: selectedScore } : null;
  }
  function forceFillMissingKey(key) {
    let digit = "0";
    const preferList = Array.from(preferExcludeDigits).concat(["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]);
    for (let i = 0; i < preferList.length; i += 1) {
      const text = String(preferList[i]);
      if (/^\d$/.test(text)) {
        digit = text;
        break;
      }
    }
    const forced = autoTop6DecorateItem(safeHistory, {
      kind: "position",
      key,
      side: "recommend",
      name: ({ wan: "千位", qian: "百位", bai: "十位", shi: "个位" }[key] || key) + "强制推荐",
      code: digit,
      derivedFrom: "compact-force-position-digit",
      repairedIntoTop: true
    }, evaluatedCount);
    if (!commitItem(forced)) return false;
    repairReport.push(`三定框架${forced.name}补齐：${digit}`);
    return true;
  }

  const rankedPositionCandidates = THREE_DING_POSITION_ORDER
    .map((key) => bestCandidateForKey(key))
    .filter(Boolean)
    .sort((a, b) => b.score - a.score || THREE_DING_POSITION_ORDER.indexOf(a.key) - THREE_DING_POSITION_ORDER.indexOf(b.key));

  let selectedPositionKeys = [];
  // 合尾必须真有模型/候选，否则会选到 wanQianGe 等空键 → compactAttack 永远封存不了
  const heweiModelKeys = new Set(
    candidates
      .filter((item) => item && item.kind === "hewei" && THREE_DING_INDEPENDENT_HEWEI_KEYS.has(String(item.key || "")))
      .map((item) => String(item.key || ""))
  );
  const ihk = prediction && prediction.independentHeweiKills && typeof prediction.independentHeweiKills === "object"
    ? prediction.independentHeweiKills
    : {};
  Object.keys(ihk).forEach((key) => {
    const base = String(key || "").replace(/Freq$/, "");
    if (THREE_DING_INDEPENDENT_HEWEI_KEYS.has(base) && ihk[key] && (ihk[key].killDigit != null || ihk[key].primaryDigit != null)) {
      const dig = String(ihk[key].killDigit != null ? ihk[key].killDigit : ihk[key].primaryDigit).replace(/\D/g, "").slice(0, 1);
      if (/^\d$/.test(dig)) heweiModelKeys.add(base);
    }
  });
  const capableCombos = [];
  for (let i = 0; i < rankedPositionCandidates.length; i += 1) {
    for (let j = i + 1; j < rankedPositionCandidates.length; j += 1) {
      for (let k = j + 1; k < rankedPositionCandidates.length; k += 1) {
        const trio = [rankedPositionCandidates[i], rankedPositionCandidates[j], rankedPositionCandidates[k]];
        const keys = trio.map((entry) => entry.key);
        const heweiKey = threeDingHeweiKeyForPositions(keys);
        if (!heweiKey || !heweiModelKeys.has(heweiKey)) continue;
        const score = trio.reduce((sum, entry) => sum + Number(entry.score || 0), 0);
        capableCombos.push({ keys, trio, heweiKey, score });
      }
    }
  }
  capableCombos.sort((a, b) => b.score - a.score);
  if (capableCombos.length) {
    capableCombos[0].trio.forEach((entry) => {
      if (commitItem(entry.item)) selectedPositionKeys.push(entry.key);
    });
  } else {
    rankedPositionCandidates.slice(0, 3).forEach((entry) => {
      if (commitItem(entry.item)) selectedPositionKeys.push(entry.key);
    });
  }
  THREE_DING_POSITION_ORDER.forEach((key) => {
    if (selectedPositionKeys.length >= 3 || selectedPositionKeys.includes(key)) return;
    if (forceFillMissingKey(key)) selectedPositionKeys.push(key);
  });
  if (!threeDingHeweiKeyForPositions(selectedPositionKeys)) {
    for (let i = chosen.length - 1; i >= 0; i -= 1) {
      const row = chosen[i];
      if (row && row.kind === "position") {
        used.delete(autoTop6ItemId(row));
        chosen.splice(i, 1);
      }
    }
    selectedPositionKeys = [];
    ["wan", "qian", "bai"].forEach((key) => {
      const best = bestCandidateForKey(key);
      if (best && commitItem(best.item)) selectedPositionKeys.push(key);
      else if (forceFillMissingKey(key)) selectedPositionKeys.push(key);
    });
    repairReport.push("三定框架位置组合无合尾模型，已回退千百十对齐。");
  }
  const posCount = chosen.filter((item) => item && item.kind === "position").length;
  const alignedHeweiKey = threeDingHeweiKeyForPositions(selectedPositionKeys) || "wanQianBai";
  if (posCount < 3) repairReport.push("三定框架定位正式项不足三项。");
  else {
    const labels = { wan: "千", qian: "百", bai: "十", shi: "个" };
    const pickedLabels = THREE_DING_POSITION_ORDER.filter((key) => selectedPositionKeys.includes(key)).map((key) => labels[key]);
    repairReport.push(`三定框架系统择优位置：${pickedLabels.join("、")}；每位独立排除1码。`);
  }

  // 2) 合尾跟随已选三位（位置排除对齐），禁止乱挂其它段合尾。
  const heweiPool = candidates.filter((item) => item.kind === "hewei" && THREE_DING_INDEPENDENT_HEWEI_KEYS.has(String(item.key || ""))).sort(order);
  noteLow(heweiPool, "三定框架合尾池");
  // 合尾组码也只用 recommend/main；backup 不进 compact 主链路
  const heweiSideOrder = ["recommend", "main"];
  let heweiPicked = false;
  function tryPickAlignedHewei(requireGate) {
    for (let s = 0; s < heweiSideOrder.length; s += 1) {
      const side = heweiSideOrder[s];
      const pool = heweiPool
        .filter((item) => String(item.key || "") === alignedHeweiKey && (item.side || "recommend") === side && (!requireGate || item.stabilityGate))
        .sort(order);
      let best = null;
      let bestScore = -Infinity;
      pool.forEach((raw) => {
        const item = raw;
        const id = autoTop6ItemId(item);
        if (used.has(id)) return;
        const score = comboPickScore(item) + positionStrengthScore(item) - s * 30;
        if (score > bestScore || (score === bestScore && best && order(item, best) < 0)) {
          best = item;
          bestScore = score;
        }
      });
      if (best && commitItem(best)) {
        heweiPicked = true;
        return true;
      }
    }
    return false;
  }
  if (!tryPickAlignedHewei(true)) tryPickAlignedHewei(false);
  if (!heweiPicked) repairReport.push(`三定框架对齐合尾缺失：${alignedHeweiKey}`);
  else {
    const hw = chosen.find((item) => item && item.kind === "hewei");
    if (hw) repairReport.push(`三定框架位置对齐合尾：${hw.name || hw.key} ${hw.code}${hw.stabilityGate ? "" : "（观察项）"}`);
  }

  repairReport.push("三定框架：3项定位排除 + 1项同位置合尾；合尾跟随三位位置");

  const ordered = [];
  THREE_DING_POSITION_ORDER.forEach((key) => {
    const item = chosen.find((row) => row && row.kind === "position" && String(row.key) === key);
    if (item) ordered.push(item);
  });
  chosen.forEach((item) => {
    if (item && item.kind === "hewei" && THREE_DING_INDEPENDENT_HEWEI_KEYS.has(String(item.key || ""))) ordered.push(item);
  });
  chosen.forEach((item) => {
    if (ordered.indexOf(item) < 0) ordered.push(item);
  });

  return {
    version: COMPACT_ATTACK_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(ordered.length, COMPACT_ATTACK_ITEM_COUNT),
    framework: {
      positionCount: 3,
      heweiComboCount: 1,
      shapeParityCount: 0,
      heweiCount: 1,
      pool: "any3Of4",
      pickOrder: ["position-any3of4-aligned", "hewei-follow-positions"],
      selectedPositionKeys: THREE_DING_POSITION_ORDER.filter((key) => selectedPositionKeys.includes(key)),
      alignedHeweiKey,
      positionSidePreference: sideOrder.slice()
    },
    items: ordered.slice(0, COMPACT_ATTACK_ITEM_COUNT).map((item, index) => ({
      rank: index + 1,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      mode: item.mode || (item.kind === "extra" && /Hit$/.test(String(item.key || "")) ? "hit" : "avoid"),
      rate: item.rate,
      stableScore: item.stableScore,
      rate30: item.rate30,
      rate60: item.rate60,
      rate120: item.rate120,
      rate300: item.rate300,
      safetyRate: item.safetyRate,
      safetyThreshold: item.safetyThreshold,
      longFloor: item.longFloor,
      lower120: item.lower120,
      recentMissStreak: item.recentMissStreak,
      maxMissStreak120: item.maxMissStreak120,
      maxMissStreak1000: item.maxMissStreak1000,
      hardnessScore: item.hardnessScore,
      stabilityGate: !!item.stabilityGate,
      stabilityReason: item.stabilityReason || "",
      stageLabel: item.stageLabel,
      lowConfidence: !!item.lowConfidence,
      repairedIntoTop: !!item.repairedIntoTop,
      shapeKey: item.shapeKey || "",
      derivedFrom: item.derivedFrom || ""
    })),
    repairReport
  };
}

function ensurePredictionCompactAttackSnapshot(prediction, history) {
  // retired: 三定框架已删除
  if (prediction && typeof prediction === "object") {
    delete prediction.compactAttack;
    delete prediction.compactAttackItems;
    delete prediction.compactAttackRepairReport;
    if (prediction.recommendationMeta && typeof prediction.recommendationMeta === "object") {
      delete prediction.recommendationMeta.compactAttackSnapshotVersion;
    }
  }
  return null;
}

function mapFrameworkSnapshotItems(chosen, heweiScopeIndexes, itemCount) {
  return chosen.slice(0, itemCount).map((item, index) => ({
    rank: index + 1,
    kind: item.kind,
    key: item.key,
    side: item.side || "recommend",
    name: item.name,
    code: item.code,
    mode: item.mode || (item.kind === "extra" && /Hit$/.test(String(item.key || "")) ? "hit" : "avoid"),
    rate: item.rate,
    stableScore: item.stableScore,
    rate30: item.rate30,
    rate60: item.rate60,
    rate120: item.rate120,
    rate300: item.rate300,
    safetyRate: item.safetyRate,
    safetyThreshold: item.safetyThreshold,
    longFloor: item.longFloor,
    lower120: item.lower120,
    recentMissStreak: item.recentMissStreak,
    maxMissStreak120: item.maxMissStreak120,
    maxMissStreak1000: item.maxMissStreak1000,
    hardnessScore: item.hardnessScore,
    stabilityGate: !!item.stabilityGate,
    stabilityReason: item.stabilityReason || "",
    stageLabel: item.stageLabel,
    lowConfidence: !!item.lowConfidence,
    repairedIntoTop: !!item.repairedIntoTop,
    shapeKey: item.shapeKey || "",
    shapeScopeLength: item.shapeScopeLength || undefined,
    shapeScopeIndexes: item.kind === "derivedReverseShape" && Array.isArray(item.shapeScopeIndexes) && item.shapeScopeIndexes.length === 3
      ? item.shapeScopeIndexes.slice()
      : undefined,
    derivedFrom: item.derivedFrom || "",
    heweiScopeIndexes: (item.key === "anyTwoHeweiHit" || item.key === "anyThreeHeweiHit") && Array.isArray(heweiScopeIndexes) && heweiScopeIndexes.length >= 2
      ? heweiScopeIndexes.slice()
      : undefined
  }));
}

function frameworkAnyTwoCodesFromItems(items) {
  const out = new Set();
  (Array.isArray(items) ? items : []).forEach((item) => {
    if (!item || item.kind !== "extra" || item.key !== "anyTwoHeweiHit") return;
    const code = String(item.code || "");
    if (isAnyTwoHeweiCode(code)) out.add(code);
  });
  return out;
}

function frameworkTwoDigitCodesFromItems(items) {
  const out = new Set();
  (Array.isArray(items) ? items : []).forEach((item) => {
    if (!item || item.kind !== "extra") return;
    if (!["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"].includes(String(item.key || ""))) return;
    const code = String(item.code || "");
    if (/^\d{2}$/.test(code)) out.add(code);
  });
  return out;
}

function promoteFrameworkBackupItem(item, label) {
  if (!item) return item;
  if ((item.side || "recommend") !== "backup") return item;
  const next = Object.assign({}, item, {
    side: "recommend",
    repairedIntoTop: true,
    derivedFrom: item.derivedFrom || (item.kind === "hewei" ? "hewei-backup-algorithm" : "position-backup-algorithm")
  });
  if (item.kind === "hewei") {
    next.name = String(item.name || "").replace(/备用$/, "推荐") || "千百十合尾推荐";
  } else if (item.kind === "position") {
    next.name = String(item.name || "").replace(/备用$/, "推荐") || `${label || "定位"}备用算法`;
  }
  return next;
}

function rankAnyTwoHeweiHitPool(history, evaluatedCount, scopeIndexes, baseCandidates) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const codes = anyTwoHeweiCandidates();
  const byCode = new Map();
  (Array.isArray(baseCandidates) ? baseCandidates : []).forEach((item) => {
    if (!item || item.kind !== "extra" || item.key !== "anyTwoHeweiHit") return;
    const code = String(item.code || "");
    if (isAnyTwoHeweiCode(code)) byCode.set(code, item);
  });
  const pool = [];
  codes.forEach((code) => {
    let item = byCode.get(code);
    if (item) {
      item = Object.assign({}, item);
    } else {
      item = {
        kind: "extra",
        key: "anyTwoHeweiHit",
        side: "recommend",
        name: "二数合尾02468排1码命中",
        code,
        mode: "hit",
        derivedFrom: "anyTwo-alt-rank"
      };
    }
    if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) {
      item.heweiScopeIndexes = scopeIndexes.slice();
    } else {
      delete item.heweiScopeIndexes;
    }
    // 作用位绑定后必须重算全部窗口；禁止沿用未限定前的虚高胜率做选型
    item = autoTop6DecorateItem(safeHistory, item, evaluatedCount);
    if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) {
      item.heweiScopeIndexes = scopeIndexes.slice();
    }
    item._anyTwoRankScore = item.stableScore != null ? Number(item.stableScore) : -1;
    pool.push(item);
  });
  return pool.sort((a, b) => {
    if (Number(b._anyTwoRankScore || -1) !== Number(a._anyTwoRankScore || -1)) {
      return Number(b._anyTwoRankScore || -1) - Number(a._anyTwoRankScore || -1);
    }
    if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
    if (Number(b.rate120 || 0) !== Number(a.rate120 || 0)) return Number(b.rate120 || 0) - Number(a.rate120 || 0);
    return String(a.code || "").localeCompare(String(b.code || ""));
  });
}

function pickRankedAnyTwoHeweiHit(history, evaluatedCount, scopeIndexes, baseCandidates, avoidCodes) {
  const avoid = avoidCodes instanceof Set ? avoidCodes : new Set(Array.isArray(avoidCodes) ? avoidCodes : []);
  const pool = rankAnyTwoHeweiHitPool(history, evaluatedCount, scopeIndexes, baseCandidates);
  const fresh = pool.find((item) => item && isAnyTwoHeweiCode(item.code) && !avoid.has(String(item.code))) || null;
  if (fresh) return fresh;
  return pool.find((item) => item && isAnyTwoHeweiCode(item.code)) || null;
}

function structuralAnyTwoAlignment(chosenItems, prediction, assist) {
  const kills = new Set(frameworkSingleExcludeDigits(chosenItems));
  const keep = new Set(DIGIT_POOL.filter((digit) => !kills.has(digit)));
  const prefer = new Set();
  const pk = prediction && prediction.positionKills ? prediction.positionKills : {};
  ["wan", "qian", "bai", "shi"].forEach((key) => {
    const row = pk[key] || {};
    // 只认单定顶1 main；不用 backup / killDigits 双码搅进结构对齐
    if (row.codexStableRecommended && /^\d$/.test(String(row.codexStableDigit || ""))) {
      const digit = String(row.codexStableDigit);
      if (!kills.has(digit)) prefer.add(digit);
    }
    const mainDigit = row.main != null ? row.main
      : (row.mainDigit != null ? row.mainDigit
        : (row.primaryDigit != null ? row.primaryDigit
          : (row.recommendDigit != null ? row.recommendDigit : null)));
    if (/^\d$/.test(String(mainDigit || ""))) kills.add(String(mainDigit));
  });
  if (assist && assist.ok) {
    (assist.excludeDigits || []).forEach((digit) => {
      if (/^\d$/.test(String(digit))) kills.add(String(digit));
    });
  }
  DIGIT_POOL.forEach((digit) => {
    if (!kills.has(digit)) keep.add(digit);
  });
  return { keep, prefer, kills };
}

function scoreStructuralAnyTwoCode(code, alignment) {
  const digits = String(code || "").split("");
  const { keep, prefer, kills } = alignment;
  let score = 0;
  // 软筛选：命中更稳排除单码按风险扣分，不再一次性 -100 硬杀
  digits.forEach((digit) => {
    if (keep.has(digit)) score += 14;
    else if (kills.has(digit)) score -= 28; // was -100 hard kill
    if (prefer.has(digit)) score += 30;
  });
  score += digits.filter((digit) => keep.has(digit)).length * 3;
  score -= digits.filter((digit) => kills.has(digit)).length * 12; // was *40
  // 组合级 Risk Score：多重违规额外降权（Fuzzy Zone 对齐）
  if (typeof stableMathFilterBridge.scoreCombination === "function") {
    const risk = stableMathFilterBridge.scoreCombination(String(code || "").slice(0, 4));
    if (risk && risk.riskScore != null) {
      score -= Number(risk.riskScore) * 18;
      if (risk.riskLevel === "high") score -= 25;
      else if (risk.riskLevel === "mid") score -= 8;
    }
  }
  return score;
}

function pickStructuralAnyTwoHeweiHit(history, evaluatedCount, scopeIndexes, chosenItems, prediction, avoidCodes, assist) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const avoid = avoidCodes instanceof Set ? avoidCodes : new Set(Array.isArray(avoidCodes) ? avoidCodes : []);
  const alignment = structuralAnyTwoAlignment(chosenItems, prediction, assist || resolveStableMathAssist(prediction));
  const codes = anyTwoHeweiCandidates();
  let bestCode = null;
  let bestScore = -Infinity;
  codes.forEach((code) => {
    if (avoid.has(code)) return;
    const score = scoreStructuralAnyTwoCode(code, alignment);
    if (score > bestScore || (score === bestScore && bestCode && code.localeCompare(bestCode) < 0)) {
      bestScore = score;
      bestCode = code;
    }
  });
  if (!bestCode) bestCode = codes.find((code) => !avoid.has(code)) || anyTwoHeweiCandidates()[0];
  let item = {
    kind: "extra",
    key: "anyTwoHeweiHit",
    side: "recommend",
    name: "二数合尾02468排1码命中",
    code: bestCode,
    mode: "hit",
    derivedFrom: "structural-align-position-hewei",
    structuralPick: true,
    algorithm: "structural-any-two-hewei-02468-exclude-one",
    hitMode: true,
    matchMode: "hit-if-any-two-hewei-tail-in-02468-exclude-one",
    dynamicEveryPrediction: true
  };
  if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) item.heweiScopeIndexes = scopeIndexes.slice();
  item = autoTop6DecorateItem(safeHistory, item, evaluatedCount);
  item.structuralPick = true;
  item.derivedFrom = "structural-align-position-hewei";
  if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) item.heweiScopeIndexes = scopeIndexes.slice();
  return item;
}

function pickThreeDingTwoAnyTwoFiveMixed(history, evaluatedCount, scopeIndexes, avoidCodes) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const avoid = avoidCodes instanceof Set ? avoidCodes : new Set(Array.isArray(avoidCodes) ? avoidCodes : []);
  const scope = (Array.isArray(scopeIndexes) ? scopeIndexes : [])
    .map((value) => Number(value))
    .filter((value) => Number.isInteger(value) && value >= 0 && value <= 3);
  const uniqueScope = [...new Set(scope)].sort((left, right) => left - right);
  if (uniqueScope.length < 3) return null;

  const pairTails = safeHistory.map((row) => {
    const nums = numbersFromRecordByIndexes(row, uniqueScope).map(Number);
    if (nums.length < 3 || nums.some((value) => !Number.isFinite(value))) return null;
    return [
      (nums[0] + nums[1]) % 10,
      (nums[0] + nums[2]) % 10,
      (nums[1] + nums[2]) % 10
    ];
  }).filter(Boolean);

  const near = Math.max(1, Math.min(120, pairTails.length));
  let best = null;
  anyTwoHeweiFiveMixedCandidates().forEach((code) => {
    if (avoid.has(code)) return;
    const structBase = threeDingAnyTwoStructBasePercent(code);
    if (structBase > THREE_DING_ANYTWO_MAX_BASE) return;
    const mask = anyTwoMaskFromDigitCode(code);
    let nearHit = 0;
    let longHit = 0;
    for (let i = 0; i < pairTails.length; i += 1) {
      const t = pairTails[i];
      const hit = ((mask >> t[0]) & 1) || ((mask >> t[1]) & 1) || ((mask >> t[2]) & 1);
      if (hit) {
        longHit += 1;
        if (i < near) nearHit += 1;
      }
    }
    const lowerNear = wilsonLowerPercent(nearHit, near);
    const lowerLong = wilsonLowerPercent(longHit, pairTails.length);
    const score = lowerNear * 0.65 + lowerLong * 0.35;
    if (!best || score > best.score || (score === best.score && String(code).localeCompare(String(best.code)) < 0)) {
      best = { code, score, structBase, nearHit, longHit };
    }
  });

  if (!best) {
    anyTwoHeweiFiveMixedCandidates().forEach((code) => {
      if (avoid.has(code)) return;
      const structBase = threeDingAnyTwoStructBasePercent(code);
      if (!best || structBase < best.structBase || (structBase === best.structBase && String(code).localeCompare(String(best.code)) < 0)) {
        best = { code, score: -1, structBase, nearHit: 0, longHit: 0 };
      }
    });
  }
  if (!best) return null;

  let item = {
    kind: "extra",
    key: "anyTwoHeweiHit",
    side: "recommend",
    name: "三定二数合尾五码混奇偶",
    code: best.code,
    mode: "hit",
    derivedFrom: "three-ding-anytwo-five-mixed-optimized",
    structuralPick: true,
    algorithm: "three-ding-anytwo-five-mixed-struct-filter",
    structBase: Number(best.structBase.toFixed(1)),
    hitMode: true,
    matchMode: "hit-if-any-two-hewei-tail-in-five-mixed",
    dynamicEveryPrediction: true
  };
  if (uniqueScope.length >= 2) item.heweiScopeIndexes = uniqueScope.slice();
  item = autoTop6DecorateItem(safeHistory, item, evaluatedCount);
  item.derivedFrom = "three-ding-anytwo-five-mixed-optimized";
  item.algorithm = "three-ding-anytwo-five-mixed-struct-filter";
  item.structBase = Number(best.structBase.toFixed(1));
  if (uniqueScope.length >= 2) item.heweiScopeIndexes = uniqueScope.slice();
  return item;
}

function pickThreeDingTwoAnyTwoFromSeed(history, evaluatedCount, scopeIndexes, chosenItems, prediction, avoidCodes) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const avoid = avoidCodes instanceof Set ? avoidCodes : new Set(Array.isArray(avoidCodes) ? avoidCodes : []);
  const seedArr = uniqueDigits(ANY_TWO_HEWEI_SOURCE_DIGITS).sort();
  const kills = frameworkSingleExcludeDigits(chosenItems);
  const heweiItem = (Array.isArray(chosenItems) ? chosenItems : []).find((item) => item && item.kind === "hewei" && item.key === "wanQianBai");
  const heweiKill = frameworkSingleExcludeDigitToken(heweiItem);
  let excludedDigit = null;
  if (heweiKill && seedArr.includes(heweiKill)) excludedDigit = heweiKill;
  if (!excludedDigit) {
    for (let index = 0; index < seedArr.length; index += 1) {
      const digit = seedArr[index];
      if (kills.has(digit)) {
        excludedDigit = digit;
        break;
      }
    }
  }
  if (!excludedDigit) excludedDigit = seedArr[seedArr.length - 1] || "8";
  let digits = seedArr.filter((digit) => digit !== excludedDigit);
  let bestCode = digits.sort().join("");
  if (!isAnyTwoHeweiCode(bestCode)) bestCode = anyTwoHeweiCandidates()[0];
  if (avoid.has(bestCode)) {
    const fallback = anyTwoHeweiCandidates().find((code) => !avoid.has(code) && code.split("").filter((digit) => seedArr.includes(digit)).length === ANY_TWO_HEWEI_CODE_SIZE);
    if (fallback) bestCode = fallback;
  }
  let item = {
    kind: "extra",
    key: "anyTwoHeweiHit",
    side: "recommend",
    name: "二数合尾02468排1码命中",
    code: bestCode,
    mode: "hit",
    derivedFrom: "three-ding-two-seed-02468-exclude-one",
    structuralPick: true,
    algorithm: "three-ding-two-any-two-seed-02468-exclude-one",
    seedCode: ANY_TWO_HEWEI_SOURCE_DIGITS,
    excludedSeedDigit: excludedDigit,
    hitMode: true,
    matchMode: "hit-if-any-two-hewei-tail-in-02468-exclude-one",
    dynamicEveryPrediction: true
  };
  if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) item.heweiScopeIndexes = scopeIndexes.slice();
  item = autoTop6DecorateItem(safeHistory, item, evaluatedCount);
  item.derivedFrom = "three-ding-two-seed-02468-exclude-one";
  item.seedCode = ANY_TWO_HEWEI_SOURCE_DIGITS;
  item.excludedSeedDigit = excludedDigit;
  if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) item.heweiScopeIndexes = scopeIndexes.slice();
  return item;
}

function buildThreeDingVariantHelpers(prediction, history, options) {
  const opts = options && typeof options === "object" ? options : {};
  const comboWindow = Math.max(30, Math.min(MODEL_STABLE_WINDOW, Number(opts.comboWindow || MODEL_STABLE_WINDOW)));
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  const evaluatedCount = Array.isArray(state && state.predictions) ? state.predictions.filter((item) => item && item.evaluated && item.evaluation).length : safeHistory.length;
  const candidates = autoTop6CandidateItems(prediction).map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount));
  const healthy = candidates.filter((item) => item && item.stabilityGate === true);
  const chosen = [];
  const used = new Set();
  const repairReport = [];
  const avoidDigits = opts.avoidDigits instanceof Set ? opts.avoidDigits : new Set(Array.isArray(opts.avoidDigits) ? opts.avoidDigits : []);
  const avoidAnyTwoCodes = opts.avoidAnyTwoCodes instanceof Set
    ? opts.avoidAnyTwoCodes
    : new Set(Array.isArray(opts.avoidAnyTwoCodes) ? opts.avoidAnyTwoCodes : []);
  const avoidTwoDigitCodes = opts.avoidTwoDigitCodes instanceof Set
    ? opts.avoidTwoDigitCodes
    : new Set(Array.isArray(opts.avoidTwoDigitCodes) ? opts.avoidTwoDigitCodes : []);
  const preferredPositionSides = Array.isArray(opts.preferredPositionSides) && opts.preferredPositionSides.length
    ? opts.preferredPositionSides.slice()
    : ["main", "recommend"];
  const positionKeys = Array.isArray(opts.positionKeys) && opts.positionKeys.length === 3
    ? opts.positionKeys.slice()
    : THREE_DING_TWO_POSITION_KEYS.slice();
  const positionKeySet = new Set(positionKeys);
  const heweiKey = String(opts.heweiKey || THREE_DING_TWO_HEWEI_KEY);
  const comboLabel = String(opts.comboLabel || THREE_DING_TWO_COMBO_LABEL);
  const positionSlotLabels = { wan: "千位", qian: "百位", bai: "十位", shi: "个位" };
  const shapeFromPositionsFirst = opts.shapeFromPositionsFirst === true;
  const comboRows = safeHistory.slice(0, Math.min(comboWindow, safeHistory.length));

  function order(a, b) {
    if (Number(b.stableScore || 0) !== Number(a.stableScore || 0)) return Number(b.stableScore || 0) - Number(a.stableScore || 0);
    if (Number(b.rate300 || 0) !== Number(a.rate300 || 0)) return Number(b.rate300 || 0) - Number(a.rate300 || 0);
    if (Number(b.rate60 || 0) !== Number(a.rate60 || 0)) return Number(b.rate60 || 0) - Number(a.rate60 || 0);
    if (Number(b.rate120 || 0) !== Number(a.rate120 || 0)) return Number(b.rate120 || 0) - Number(a.rate120 || 0);
    return String(a.name || "").localeCompare(String(b.name || ""));
  }

  function comboStats(items) {
    let total = 0;
    let hit = 0;
    let validChecks = 0;
    let successChecks = 0;
    comboRows.forEach((record) => {
      const results = (items || []).map((item) => autoTop6RecordSuccess(item, record)).filter((value) => typeof value === "boolean");
      if (results.length !== (items || []).length || !results.length) return;
      total += 1;
      const wins = results.filter(Boolean).length;
      validChecks += results.length;
      successChecks += wins;
      if (wins === results.length) hit += 1;
    });
    return {
      total,
      hit,
      rate: total ? hit * 100 / total : null,
      avgRate: validChecks ? successChecks * 100 / validChecks : null
    };
  }

  function comboPickScore(item) {
    const stats = comboStats(chosen.concat([item]));
    const full = stats.rate === null ? -1 : Number(stats.rate || 0);
    const avg = stats.avgRate === null ? 0 : Number(stats.avgRate || 0);
    const digit = frameworkSingleExcludeDigitToken(item);
    const avoidPenalty = digit && avoidDigits.has(digit) ? 500000 : 0;
    return full * 10000 + avg * 100 + Number(item.stableScore || 0) - avoidPenalty;
  }

  function commitItem(item) {
    if (!item) return false;
    const promoted = promoteFrameworkBackupItem(item, item.kind === "position" ? "定位" : "合尾");
    const id = autoTop6ItemId(promoted);
    if (used.has(id)) return false;
    if (frameworkConflictsSameExcludeDigit(chosen, promoted)) return false;
    if (frameworkConflictsSameHeweiDigit(chosen, promoted)) return false;
    used.add(id);
    chosen.push(promoted);
    return true;
  }

  function pickBest(pool, maxPerSameModel, allowAvoidDigit) {
    const modelCount = {};
    chosen.forEach((item) => {
      const modelKey = `${item.kind}:${item.key}`;
      modelCount[modelKey] = Number(modelCount[modelKey] || 0) + 1;
    });
    let best = null;
    let bestScore = -Infinity;
    (pool || []).forEach((item) => {
      if (!item) return;
      const trial = promoteFrameworkBackupItem(item, item.kind === "position" ? "定位" : "合尾");
      const id = autoTop6ItemId(trial);
      const modelKey = `${trial.kind}:${trial.key}`;
      if (used.has(id)) return;
      if (Number(modelCount[modelKey] || 0) >= Number(maxPerSameModel || 1)) return;
      if (frameworkConflictsSameExcludeDigit(chosen, trial)) return;
      if (frameworkConflictsSameHeweiDigit(chosen, trial)) return;
      const digit = frameworkSingleExcludeDigitToken(trial);
      if (!allowAvoidDigit && digit && avoidDigits.has(digit)) return;
      const score = comboPickScore(trial);
      if (score > bestScore || (score === bestScore && best && order(trial, best) < 0)) {
        best = trial;
        bestScore = score;
      }
    });
    if (!best) return false;
    return commitItem(best);
  }

  function noteLow(pool, label) {
    (pool || []).forEach((item) => {
      if (item.lowConfidence) {
        const rate = item.safetyRate === null ? "样本不足" : `${Number(item.safetyRate).toFixed(2)}%`;
        const reason = item.stabilityReason ? `，原因 ${item.stabilityReason}` : "";
        repairReport.push(`${label}暂停低稳：${item.name} ${item.code}，安全率 ${rate}${reason}`);
      }
    });
  }

  function sidePool(source, side) {
    return (source || []).filter((item) => (item.side || "recommend") === side);
  }

  function pickPositions(needCount, label) {
    const positionPool = candidates.filter((item) => item.kind === "position" && positionKeySet.has(String(item.key || ""))).sort(order);
    noteLow(positionPool, `${label}定位池`);
    const positionHealthy = healthy.filter((item) => item.kind === "position" && positionKeySet.has(String(item.key || ""))).sort(order);
    let before = chosen.length;
    preferredPositionSides.forEach((side) => {
      while (chosen.length - before < needCount && pickBest(sidePool(positionHealthy, side), 1, false)) {}
    });
    preferredPositionSides.forEach((side) => {
      while (chosen.length - before < needCount && pickBest(sidePool(positionHealthy, side), 1, true)) {}
    });
    if (chosen.length - before < needCount) {
      preferredPositionSides.forEach((side) => {
        while (chosen.length - before < needCount && pickBest(sidePool(positionPool, side), 1, true)) {
          chosen[chosen.length - 1].repairedIntoTop = true;
        }
      });
      if (chosen.length - before >= needCount) repairReport.push(`${label}定位健康项不足，已用观察项补齐（仅单定顶1，不含 dual/backup；仅${comboLabel}）。`);
    }
    // 候选同码/跨框避码抽干时，对齐原三定：强制补独立码，避免 B 快照 intermittent incomplete
    function forceFillMissingPosition() {
      const keyOrder = positionKeys.slice();
      const usedKeys = new Set(
        chosen
          .filter((item) => item && item.kind === "position" && positionKeySet.has(String(item.key || "")))
          .map((item) => String(item.key))
      );
      let filled = 0;
      while (chosen.length - before < needCount) {
        const missingKey = keyOrder.find((key) => !usedKeys.has(key));
        if (!missingKey) break;
        const taken = frameworkSingleExcludeDigits(chosen);
        // 优先错开跨框 avoidDigits，再回退任意未占用单码（同框仍禁同码）
        const digitOrder = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];
        const preferList = digitOrder.filter((digit) => !avoidDigits.has(digit)).concat(digitOrder);
        let digit = null;
        for (let i = 0; i < preferList.length; i += 1) {
          const text = String(preferList[i]);
          if (/^\d$/.test(text) && !taken.has(text)) {
            digit = text;
            break;
          }
        }
        if (digit == null) break;
        const forced = autoTop6DecorateItem(safeHistory, {
          kind: "position",
          key: missingKey,
          side: "recommend",
          name: (positionSlotLabels[missingKey] || missingKey) + "强制独立码",
          code: digit,
          derivedFrom: "three-ding-force-unique-digit",
          repairedIntoTop: true
        }, evaluatedCount);
        if (!commitItem(forced)) break;
        usedKeys.add(missingKey);
        filled += 1;
        const avoided = avoidDigits.has(digit) ? "（仍触碰跨框避码）" : "";
        repairReport.push(`${label}${missingKey}定位候选撞码/不足，已强制错开为 ${digit}${avoided}`);
      }
      return filled;
    }
    if (chosen.length - before < needCount) {
      const filled = forceFillMissingPosition();
      if (filled > 0 && chosen.length - before >= needCount) {
        repairReport.push(`${label}定位已用强制独立码补齐至${needCount}项（仅${comboLabel}）。`);
      }
    }
    if (chosen.length - before < needCount) repairReport.push(`${label}定位正式项不足${needCount}个（仅${comboLabel}）。`);
    else {
      const sidesUsed = chosen.slice(before).filter((item) => item.kind === "position").map((item) => item.derivedFrom || item.side || "recommend");
      repairReport.push(`${label}定位算法侧：${sidesUsed.join("/") || preferredPositionSides[0]}`);
    }
  }

  function pickComboHewei(label, preferredSides) {
    const sideOrder = (Array.isArray(preferredSides) && preferredSides.length ? preferredSides.slice() : ["recommend", "main"])
      .filter((side) => side !== "backup");
    const heweiPool = candidates.filter((item) => item.kind === "hewei" && item.key === heweiKey && item.side !== "backup").sort((a, b) => {
      const sa = sideOrder.indexOf(a.side || "recommend");
      const sb = sideOrder.indexOf(b.side || "recommend");
      if (sa !== sb) return (sa < 0 ? 99 : sa) - (sb < 0 ? 99 : sb);
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      return order(a, b);
    });
    noteLow(heweiPool, `${label}合尾池`);
    const before = chosen.length;
    let ok = false;
    sideOrder.forEach((side) => {
      if (ok) return;
      if (pickBest(sidePool(heweiPool, side).filter((item) => item.stabilityGate), 1, false)) ok = true;
    });
    sideOrder.forEach((side) => {
      if (ok) return;
      if (pickBest(sidePool(heweiPool, side), 1, false)) ok = true;
    });
    if (!ok && pickBest(heweiPool, 1, true)) ok = true;
    if (!ok) {
      repairReport.push(`${label}${comboLabel}合尾缺失`);
      return;
    }
    if (chosen.length > before) {
      const picked = chosen[chosen.length - 1];
      if (String(picked.derivedFrom || "").includes("backup")) {
        repairReport.push(`${label}合尾改用备用算法码入正式项：${picked.name} ${picked.code}`);
      } else if (!picked.stabilityGate) {
        picked.repairedIntoTop = true;
        repairReport.push(`${label}合尾使用观察项：${picked.name} ${picked.code}`);
      }
    }
  }

  function pickAnyTwo(label, scopeIndexes) {
    const picked = pickStructuralAnyTwoHeweiHit(
      safeHistory,
      evaluatedCount,
      scopeIndexes,
      chosen,
      prediction,
      avoidAnyTwoCodes,
      resolveStableMathAssist(prediction)
    );
    if (!picked) {
      repairReport.push(`${label}固定模型缺失：anyTwoHeweiHit`);
      return;
    }
    if (avoidAnyTwoCodes.has(String(picked.code || ""))) {
      repairReport.push(`${label}二数合无可错开候选，回退 ${picked.code}`);
    } else {
      repairReport.push(`${label}二数合结构对齐：${picked.code}`);
    }
    const id = autoTop6ItemId(picked);
    if (!used.has(id)) {
      used.add(id);
      if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) picked.heweiScopeIndexes = scopeIndexes.slice();
      chosen.push(picked);
    }
  }

  function pickThreeDingTwoAnyTwo(label, scopeIndexes) {
    const picked = pickThreeDingTwoAnyTwoFiveMixed(
      safeHistory,
      evaluatedCount,
      scopeIndexes,
      avoidAnyTwoCodes
    );
    if (!picked) {
      repairReport.push(`${label}固定模型缺失：anyTwoHeweiHit五码混奇偶`);
      return;
    }
    repairReport.push(`${label}二数合五码混奇偶（结构基线${picked.structBase != null ? picked.structBase : "--"}%≤${THREE_DING_ANYTWO_MAX_BASE}）：${picked.code}`);
    const id = autoTop6ItemId(picked);
    if (!used.has(id)) {
      used.add(id);
      if (Array.isArray(scopeIndexes) && scopeIndexes.length >= 2) picked.heweiScopeIndexes = scopeIndexes.slice();
      chosen.push(picked);
    }
  }

  function pickTwoDigitAvoid(label) {
    const keys = ["twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"];
    const pool = candidates.filter((item) => item && item.kind === "extra" && keys.includes(String(item.key || "")) && /^\d{2}$/.test(String(item.code || ""))).sort((a, b) => {
      const ka = keys.indexOf(String(a.key || ""));
      const kb = keys.indexOf(String(b.key || ""));
      if (ka !== kb) return ka - kb;
      if (!!b.stabilityGate !== !!a.stabilityGate) return (b.stabilityGate ? 1 : 0) - (a.stabilityGate ? 1 : 0);
      return order(a, b);
    });
    let picked = null;
    for (let index = 0; index < pool.length; index += 1) {
      const item = pool[index];
      const code = String(item.code || "");
      if (avoidTwoDigitCodes.has(code)) continue;
      const digits = code.split("");
      const overlap = digits.filter((digit) => avoidDigits.has(digit)).length;
      if (overlap >= 2) continue;
      picked = item;
      break;
    }
    if (!picked) {
      for (let index = 0; index < pool.length; index += 1) {
        const item = pool[index];
        const digits = String(item.code || "").split("");
        const overlap = digits.filter((digit) => avoidDigits.has(digit)).length;
        if (overlap >= 2) continue;
        picked = item;
        break;
      }
    }
    if (!picked) picked = pool[0] || null;
    if (!picked) {
      const fallbackPair = resolveDuishuFallbackPair(prediction);
      if (/^\d{2}$/.test(fallbackPair)) {
        picked = buildDuishuFallbackItem(fallbackPair, "对数避开1组");
        repairReport.push(`${label}对数池空，已从三对数/独立组补抓 ${fallbackPair}`);
      }
    }
    if (!picked) {
      repairReport.push(`${label}固定模型缺失：twoDigitAvoid`);
      return;
    }
    if (avoidTwoDigitCodes.has(String(picked.code || ""))) {
      repairReport.push(`${label}对数无可错开候选，回退 ${picked.code}`);
    }
    if (picked.lowConfidence) {
      const rate = picked.safetyRate === null ? "样本不足" : `${Number(picked.safetyRate).toFixed(2)}%`;
      repairReport.push(`${label}对数避开观察项：${picked.name} ${picked.code}，安全率 ${rate}`);
      picked.repairedIntoTop = true;
    }
    if (picked.key !== "twoDigitAvoid") {
      const originalKey = String(picked.key || "");
      repairReport.push(`${label}对数改用独立组：${originalKey}=${picked.code}`);
      // 完整性口径统一认 twoDigitAvoid；保留原 key 到 derivedFrom 便于追溯
      picked = Object.assign({}, picked, {
        key: "twoDigitAvoid",
        name: "对数避开1组",
        derivedFrom: picked.derivedFrom || originalKey
      });
    } else {
      repairReport.push(`${label}一对数：${picked.code}`);
    }
    const id = autoTop6ItemId(picked);
    if (!used.has(id)) {
      used.add(id);
      chosen.push(picked);
    }
  }

  function pickReverseShape(label) {
    const assist = resolveStableMathAssist(prediction);
    const scopeLen = 3;
    const shapeScopeIndexes = threeDingShapeScopeIndexesFromPositionKeys(positionKeys);

    function commitReverseShape(reversePicked, sourceLabel) {
      if (!reversePicked) return false;
      const comboShape = threeDingShapeCodeForPositionKeys(reversePicked.code, positionKeys);
      const normalized = Object.assign({}, reversePicked, {
        code: comboShape,
        shapeScopeLength: 3,
        shapeScopeIndexes: shapeScopeIndexes.slice()
      });
      if (!normalized.stabilityGate) {
        normalized.repairedIntoTop = true;
        repairReport.push(`${label}排除形态未达安全阈值，暂用最优观察项：${normalized.name} ${comboShape}。`);
      } else {
        repairReport.push(`${label}排除形态：${normalized.name} ${comboShape}（${comboLabel}三字攻击语，仅大小形态；${sourceLabel}）`);
      }
      const id = autoTop6ItemId(normalized);
      if (used.has(id)) return false;
      used.add(id);
      chosen.push(normalized);
      return true;
    }

    function tryStableMathShape() {
      const stableFirst = pickStableMathShapeCandidate(assist, safeHistory, evaluatedCount, scopeLen);
      if (!stableFirst || isParityOnlyShapeCode(stableFirst.code)) return false;
      stableFirst.shapeScopeIndexes = shapeScopeIndexes.slice();
      repairReport.push(`${label}更稳排除形态：${stableFirst.name} ${String(stableFirst.code || "").slice(0, scopeLen)}`);
      return commitReverseShape(stableFirst, "更稳算法");
    }

    function tryPositionDerivedShape() {
      const reverseBase = chosen.filter((item) => item && item.kind === "position");
      const reversePool = autoTop6ReverseShapeCandidatesFromPositions(reverseBase, safeHistory)
        .map((item) => autoTop6DecorateItem(safeHistory, item, evaluatedCount))
        .filter((item) => item && !isParityOnlyShapeCode(item.code) && (item.shapeKey === "shapeSize" || item.key === "reverseSize" || /^[大小]{3,4}$/.test(String(item.code || ""))))
        .sort(order);
      noteLow(reversePool, `${label}反向形态池`);
      const reverseHealthy = reversePool.filter((item) => item && item.stabilityGate === true);
      const source = reverseHealthy.length ? reverseHealthy : reversePool;
      let reversePicked = source.slice().sort((a, b) => {
        const delta = comboPickScore(b) - comboPickScore(a);
        return delta || order(a, b);
      })[0] || null;
      if (!reversePicked) {
        reversePicked = autoTop6DecorateItem(safeHistory, {
          kind: "derivedReverseShape",
          key: "reverseSize",
          side: "recommend",
          name: "排除形态(大小)",
          code: "大大小",
          shapeKey: "shapeSize",
          shapeScopeLength: 3,
          mode: "avoid",
          derivedFrom: "three-ding-size-shape-fallback",
          repairedIntoTop: true
        }, evaluatedCount);
        repairReport.push(`${label}排除形态暂缺，已强制大小形态大大小（禁用单双）。`);
      }
      return commitReverseShape(reversePicked, "定位推导") ? true : false;
    }

    if (shapeFromPositionsFirst) {
      if (tryPositionDerivedShape()) return;
      if (tryStableMathShape()) return;
      return;
    }
    if (tryStableMathShape()) return;
    tryPositionDerivedShape();
  }

  return { chosen, repairReport, pickPositions, pickComboHewei, pickAnyTwo, pickThreeDingTwoAnyTwo, pickTwoDigitAvoid, pickReverseShape };
}

function buildThreeDingTwoSnapshot(prediction, history, options) {
  // 三定框架A：千百十合尾 + 千百十挑1位除1码 + 二数合(千百十) + 排除形态1 + 一对数避开
  const ctx = buildThreeDingVariantHelpers(prediction, history, Object.assign({
    preferredPositionSides: ["main", "recommend"],
    positionKeys: THREE_DING_TWO_POSITION_KEYS,
    heweiKey: THREE_DING_TWO_HEWEI_KEY,
    comboLabel: THREE_DING_TWO_COMBO_LABEL,
    shapeFromPositionsFirst: true
  }, options || {}));
  ctx.pickComboHewei("三定框架A", ["recommend", "main"]);
  // 合尾入框后再选定位；定位只用单定顶1，不让 dual/backup 占码
  ctx.pickPositions(1, "三定框架A");
  ctx.pickReverseShape("三定框架A");
  ctx.chosen.forEach((item) => {
    if (item && item.kind === "derivedReverseShape") {
      item.shapeScopeLength = 3;
      if (!Array.isArray(item.shapeScopeIndexes) || item.shapeScopeIndexes.length !== 3) {
        item.shapeScopeIndexes = threeDingShapeScopeIndexesFromPositionKeys(THREE_DING_TWO_POSITION_KEYS);
      }
    }
  });
  const heweiScopeIndexes = THREE_DING_TWO_SCOPE_INDEXES.slice();
  ctx.repairReport.push("三定框架A二数合作用位：千百十（五码混奇偶，禁结构基线>" + THREE_DING_ANYTWO_MAX_BASE + "%）");
  ctx.pickThreeDingTwoAnyTwo("三定框架A", heweiScopeIndexes);
  ctx.pickTwoDigitAvoid("三定框架A");
  return {
    version: THREE_DING_TWO_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(ctx.chosen.length, THREE_DING_TWO_ITEM_COUNT),
    framework: {
      positionCount: 1,
      heweiCount: 1,
      heweiComboCount: 1,
      reverseShapeCount: 1,
      pairAvoidCount: 1,
      pool: "wanQianBai",
      comboLabel: THREE_DING_TWO_COMBO_LABEL,
      positionKeys: THREE_DING_TWO_POSITION_KEYS.slice(),
      pickOrder: ["hewei.wanQianBai", "positionx1", "derivedReverseShape", "anyTwoHeweiHit-fiveMixed", "twoDigitAvoid"],
      anyTwoMode: "five-mixed-struct-filter",
      positionSidePreference: ["main", "recommend"],
      heweiScopeIndexes: heweiScopeIndexes.slice()
    },
    heweiScopeIndexes: heweiScopeIndexes.slice(),
    items: mapFrameworkSnapshotItems(ctx.chosen, heweiScopeIndexes, THREE_DING_TWO_ITEM_COUNT),
    repairReport: ctx.repairReport
  };
}

function buildThreeDingOneSnapshot(prediction, history, options) {
  // 三定框架B：百十个合尾 + 百十个挑2位 + 大小形态排除 + 对数避开（无二数合）
  const heweiScopeIndexes = THREE_DING_ONE_SCOPE_INDEXES.slice();
  const ctx = buildThreeDingVariantHelpers(prediction, history, Object.assign({
    preferredPositionSides: ["main", "recommend"],
    positionKeys: THREE_DING_ONE_POSITION_KEYS,
    heweiKey: THREE_DING_ONE_HEWEI_KEY,
    comboLabel: THREE_DING_ONE_COMBO_LABEL,
    shapeFromPositionsFirst: true
  }, options || {}));
  ctx.pickComboHewei("三定框架B", ["recommend", "main"]);
  ctx.pickPositions(2, "三定框架B");
  ctx.pickReverseShape("三定框架B");
  ctx.chosen.forEach((item) => {
    if (item && item.kind === "derivedReverseShape") {
      item.shapeScopeLength = 3;
      if (!Array.isArray(item.shapeScopeIndexes) || item.shapeScopeIndexes.length !== 3) {
        item.shapeScopeIndexes = threeDingShapeScopeIndexesFromPositionKeys(THREE_DING_ONE_POSITION_KEYS);
      }
    }
  });
  ctx.pickTwoDigitAvoid("三定框架B");
  return {
    version: THREE_DING_ONE_SNAPSHOT_VERSION,
    generatedAt: isoNow(),
    basePeriod: prediction && prediction.basePeriod || "",
    itemCount: Math.min(ctx.chosen.length, THREE_DING_ONE_ITEM_COUNT),
    framework: {
      positionCount: 2,
      heweiCount: 1,
      heweiComboCount: 0,
      reverseShapeCount: 1,
      pairAvoidCount: 1,
      pool: "qianBaiShi",
      comboLabel: THREE_DING_ONE_COMBO_LABEL,
      positionKeys: THREE_DING_ONE_POSITION_KEYS.slice(),
      pickOrder: ["hewei.qianBaiShi", "positionx2", "derivedReverseShape", "twoDigitAvoid"],
      heweiSidePreference: ["recommend", "main"],
      positionSidePreference: ["main", "recommend"],
      heweiScopeIndexes: heweiScopeIndexes.slice()
    },
    heweiScopeIndexes: heweiScopeIndexes.slice(),
    items: mapFrameworkSnapshotItems(ctx.chosen, heweiScopeIndexes, THREE_DING_ONE_ITEM_COUNT),
    repairReport: ctx.repairReport
  };
}

function normalizeStableMathFilterOnPrediction(prediction) {
  if (!prediction || typeof prediction !== "object") return prediction;
  if (prediction.stableMathFilter) {
    prediction.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(prediction.stableMathFilter);
  }
  if (prediction.detail && prediction.detail.stableMathFilter) {
    prediction.detail.stableMathFilter = prediction.stableMathFilter
      || stableMathFilterBridge.slimStableMathFilter(prediction.detail.stableMathFilter);
  }
  return prediction;
}

function normalizeStableMathFiltersInState(stateObj) {
  if (!stateObj || typeof stateObj !== "object") return stateObj;
  if (stateObj.stableMathFilter) {
    stateObj.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(stateObj.stableMathFilter);
  }
  if (stateObj.currentPrediction) normalizeStableMathFilterOnPrediction(stateObj.currentPrediction);
  if (Array.isArray(stateObj.predictions)) {
    stateObj.predictions.forEach((prediction) => normalizeStableMathFilterOnPrediction(prediction));
  }
  return stateObj;
}

function applyStableMathFilterToPrediction(prediction, filter) {
  if (!prediction || typeof prediction !== "object" || !filter || typeof filter !== "object") return prediction;
  // A sealed prediction is immutable evidence.  The next prediction will use a
  // refreshed filter, but the current prediction must retain the filter that
  // existed when its snapshots were sealed.
  if (predictionSnapshotsAreImmutable(prediction)) return prediction;
  const slim = stableMathFilterBridge.slimStableMathFilter(filter);
  if (!slim) return prediction;
  prediction.stableMathFilter = slim;
  if (!prediction.detail || typeof prediction.detail !== "object") prediction.detail = {};
  prediction.detail.stableMathFilter = slim;
  return prediction;
}

function attachCachedStableMathFilter(prediction) {
  if (!prediction || typeof prediction !== "object") return prediction;
  const cached = (state && state.stableMathFilter)
    || stableMathFilterBridge.readCachedFilter()
    || null;
  if (cached) applyStableMathFilterToPrediction(prediction, cached);
  return prediction;
}

function scheduleStableMathFilterRefresh(reason) {
  const trigger = String(reason || "manual");
  if (stableMathFilterRunning) {
    stableMathFilterQueued = true;
    return { ok: true, deferred: true, reason: "already-running", trigger };
  }
  const history = state && Array.isArray(state.history) ? state.history : [];
  if (!history.length) {
    return { ok: false, error: "empty-history", trigger };
  }
  stableMathFilterRunning = true;
  stableMathFilterQueued = false;
  setImmediate(() => {
    Promise.resolve(stableMathFilterBridge.runStableMathFilterAsync(history, { timeoutMs: 25000 }))
      .then((outcome) => {
        const filter = outcome && outcome.result ? outcome.result : null;
        // 假成功禁令：仅 stdout JSON 真成功才写入；失败不得用旧缓存冒充已刷新
        if (outcome && outcome.ok && filter && typeof filter === "object") {
          state.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(filter);
          invalidatePanelStateCache();
          addLog("更稳数学排除已刷新", {
            trigger,
            ok: true,
            latestPeriod: filter.latest_period || null,
            historyCount: filter.history_count || null
          });
          // 若 tip 已封存但更稳仍落后，立刻解封重算（异步刷新追上后的补救）
          try {
            const catchup = rebuildTipAfterStableMathCatchup("async-smf-" + trigger);
            if (catchup && catchup.rebuilt) {
              addLog("异步更稳追上后已重算 tip", catchup);
            }
          } catch (err) {
            addLog("异步更稳追上后重算 tip 失败", {
              trigger,
              error: String(err && err.message || err)
            });
          }
        } else {
          addLog("更稳数学排除刷新失败", {
            trigger,
            error: String((outcome && outcome.error) || "empty-result"),
            cachePeriod: (state.stableMathFilter && state.stableMathFilter.latest_period) || null
          });
        }
      })
      .catch((err) => {
        addLog("更稳数学排除刷新失败", { trigger, error: String(err && err.message || err) });
      })
      .finally(() => {
        stableMathFilterRunning = false;
        if (stableMathFilterQueued) {
          stableMathFilterQueued = false;
          scheduleStableMathFilterRefresh("queued");
        }
      });
  });
  return { ok: true, started: true, trigger };
}

async function handleStableMathFilterRequest(req, res, parsedUrl) {
  const refresh = parsedUrl.searchParams.get("refresh") === "1"
    || parsedUrl.searchParams.get("refresh") === "true";
  const wantFull = parsedUrl.searchParams.get("full") === "1"
    || parsedUrl.searchParams.get("full") === "true";
  if (refresh) {
    const history = state && Array.isArray(state.history) ? state.history : [];
    const outcome = await stableMathFilterBridge.runStableMathFilterAsync(history, { timeoutMs: 25000 });
    const filter = outcome && outcome.result ? outcome.result : null;
    if (filter && typeof filter === "object") {
      state.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(filter);
      invalidatePanelStateCache();
    }
    sendJson(res, outcome && outcome.ok ? 200 : 500, {
      ok: !!(outcome && outcome.ok),
      refreshed: true,
      error: outcome && outcome.ok ? null : String((outcome && outcome.error) || "refresh-failed"),
      filter: wantFull ? (filter || state.stableMathFilter || null) : (state.stableMathFilter || stableMathFilterBridge.slimStableMathFilter(filter))
    });
    return;
  }
  const filter = wantFull
    ? (stableMathFilterBridge.readCachedFilter() || state.stableMathFilter || null)
    : (state.stableMathFilter
      || stableMathFilterBridge.slimStableMathFilter(stableMathFilterBridge.readCachedFilter())
      || null);
  sendJson(res, filter ? 200 : 404, {
    ok: !!filter,
    refreshed: false,
    running: stableMathFilterRunning,
    filter
  });
}

function buildExclusionModelPayload(prediction, options) {
  const opts = options && typeof options === "object" ? options : {};
  const includeFullFilter = opts.includeFullFilter === true;
  const p = prediction && typeof prediction === "object"
    ? prediction
    : (state && state.currentPrediction && typeof state.currentPrediction === "object" ? state.currentPrediction : {});
  const filterRaw = includeFullFilter
    ? (stableMathFilterBridge.readCachedFilter() || p.stableMathFilter || state.stableMathFilter || null)
    : (p.stableMathFilter
      || state.stableMathFilter
      || stableMathFilterBridge.slimStableMathFilter(stableMathFilterBridge.readCachedFilter())
      || null);
  const assist = resolveStableMathAssist(p);
  const independent = p.independentHeweiKills && typeof p.independentHeweiKills === "object" ? p.independentHeweiKills : {};
  const detail = p.detail && typeof p.detail === "object" ? p.detail : {};

  function positionSlot(entry) {
    if (!entry || typeof entry !== "object") {
      return { main: "", backup: "", killDigits: [], confidence: null, recommendable: null, reason: "" };
    }
    const main = String(entry.main != null ? entry.main : (entry.recommendDigit != null ? entry.recommendDigit : (entry.killDigits && entry.killDigits[0]) || ""));
    const backup = String(entry.backup != null ? entry.backup : (entry.backupDigit != null ? entry.backupDigit : (entry.killDigits && entry.killDigits[1]) || ""));
    const killDigits = Array.isArray(entry.killDigits)
      ? entry.killDigits.map(String).filter((d) => /^\d$/.test(d)).slice(0, 2)
      : [main, backup].filter((d) => /^\d$/.test(d));
    return {
      main: /^\d$/.test(main) ? main : "",
      backup: /^\d$/.test(backup) ? backup : "",
      killDigits,
      confidence: entry.confidence != null ? entry.confidence : null,
      recommendable: entry.recommendable,
      reason: entry.reason ? String(entry.reason) : ""
    };
  }

  function heweiSlot(entry, digitField) {
    if (!entry || typeof entry !== "object") {
      return { killDigit: "", backupDigit: "", confidence: null, recommendable: null, reason: "" };
    }
    const killDigit = String(entry[digitField] != null ? entry[digitField] : (entry.killDigit != null ? entry.killDigit : (entry.killHewei != null ? entry.killHewei : "")));
    const backupDigit = String(entry.backupDigit != null ? entry.backupDigit : "");
    return {
      killDigit: /^\d$/.test(killDigit) ? killDigit : "",
      backupDigit: /^\d$/.test(backupDigit) ? backupDigit : "",
      confidence: entry.confidence != null ? entry.confidence : null,
      recommendable: entry.recommendable,
      reason: entry.reason ? String(entry.reason) : ""
    };
  }

  const posWan = positionSlot(p.positionKills && p.positionKills.wan);
  const posQian = positionSlot(p.positionKills && p.positionKills.qian);
  const posBai = positionSlot(p.positionKills && p.positionKills.bai);
  const posShi = positionSlot(p.positionKills && p.positionKills.shi);
  const heweiTotal = heweiSlot(detail.totalHeweiKill || { killHewei: p.totalHeweiKill, backupDigit: p.totalHeweiBackupDigit }, "killHewei");
  const heweiUnified = heweiSlot(detail.threeFourHeweiKill || { killDigit: p.threeFourHeweiKillDigit, backupDigit: p.threeFourHeweiBackupDigit }, "killDigit");
  const heweiWqb = heweiSlot(independent.wanQianBai, "killDigit");
  const heweiQbs = heweiSlot(independent.qianBaiShi, "killDigit");
  const heweiWqbs = heweiSlot(independent.wanQianBaiShi, "killDigit");

  return {
    model: "Exclusion Model",
    version: "exclusion-model-v2",
    generatedAt: isoNow(),
    basePeriod: p.basePeriod || "",
    latestPeriod: assist.latestPeriod || p.basePeriod || "",
    tipReady: !!(p.tipReady === true || (typeof state !== "undefined" && state && state.tipReady === true)),
    source: {
      stableMath: assist.source || "cache",
      predictionId: p.id || ""
    },
    exclusions: {
      stableMath: {
        excludeDigits: assist.excludeDigits || [],
        excludeShapes: assist.excludeShapes || [],
        excludeSpans: assist.excludeSpans || [],
        excludeSumRanges: assist.excludeSumRanges || [],
        excludeRepeats: assist.excludeRepeats || [],
        excludeConsecutives: assist.excludeConsecutives || [],
        excludeParityRatios: assist.excludeParityRatios || [],
        excludeSizeRatios: assist.excludeSizeRatios || [],
        summary: assist.summary || null
      },
      // 兼容旧字段：仍给单码字符串；完整双码/置信度见 *Detail
      hewei: {
        total: heweiTotal.killDigit,
        unified: heweiUnified.killDigit,
        wanQianBai: heweiWqb.killDigit,
        qianBaiShi: heweiQbs.killDigit,
        wanQianBaiShi: heweiWqbs.killDigit,
        reconcileNotes: Array.isArray(detail.heweiReconcileNotes) ? detail.heweiReconcileNotes.slice(0, 20) : []
      },
      heweiDetail: {
        total: heweiTotal,
        unified: heweiUnified,
        wanQianBai: heweiWqb,
        qianBaiShi: heweiQbs,
        wanQianBaiShi: heweiWqbs
      },
      position: {
        wan: posWan.main,
        qian: posQian.main,
        bai: posBai.main,
        shi: posShi.main
      },
      positionDetail: {
        wan: posWan,
        qian: posQian,
        bai: posBai,
        shi: posShi
      }
    },
    stableMathFilter: filterRaw
  };
}

async function handleExclusionModelRequest(req, res, parsedUrl) {
  const refresh = parsedUrl.searchParams.get("refresh") === "1"
    || parsedUrl.searchParams.get("refresh") === "true";
  const wantFull = parsedUrl.searchParams.get("full") === "1"
    || parsedUrl.searchParams.get("full") === "true";

  if (refresh) {
    const history = state && Array.isArray(state.history) ? state.history : [];
    const outcome = await stableMathFilterBridge.runStableMathFilterAsync(history, { timeoutMs: 25000 });
    const filter = outcome && outcome.result ? outcome.result : null;
    if (filter && typeof filter === "object") {
      state.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(filter);
      if (state.currentPrediction) {
        applyStableMathFilterToPrediction(state.currentPrediction, filter);
        ensurePredictionFrameworkSnapshots(state.currentPrediction, state.history);
      }
      invalidatePanelStateCache();
    }
    const payload = buildExclusionModelPayload(state && state.currentPrediction, {
      includeFullFilter: wantFull
    });
    sendJson(res, outcome && outcome.ok ? 200 : 500, {
      ok: !!(outcome && outcome.ok),
      refreshed: true,
      running: stableMathFilterRunning,
      error: outcome && outcome.ok ? null : String((outcome && outcome.error) || "refresh-failed"),
      model: payload
    });
    return;
  }

  const payload = buildExclusionModelPayload(state && state.currentPrediction, {
    includeFullFilter: wantFull
  });
  sendJson(res, 200, {
    ok: true,
    refreshed: false,
    running: stableMathFilterRunning,
    model: payload
  });
}

function stableMathAssistSignature(assist) {
  if (!assist || typeof assist !== "object" || !assist.ok) return "";
  const join = (arr) => (Array.isArray(arr) ? arr.slice().map(String).sort().join("|") : "");
  return [
    assist.version || "",
    assist.latestPeriod || "",
    join(assist.excludeDigits),
    join(assist.excludeShapes),
    join(assist.excludeSpans),
    join(assist.excludeSumRanges),
    join(assist.excludeRepeats),
    join(assist.excludeConsecutives),
    join(assist.excludeParityRatios),
    join(assist.excludeSizeRatios)
  ].join(";");
}

function snapshotNeedsStableMathAssistRefresh(snapshot, assist) {
  if (!snapshot || typeof snapshot !== "object" || !snapshot.stableMathAssist) return true;
  const cached = snapshot.stableMathAssist;
  const cachedAssist = {
    ok: true,
    version: cached.version,
    latestPeriod: cached.latestPeriod,
    excludeDigits: cached.excludeDigits,
    excludeShapes: cached.excludeShapes,
    excludeSpans: cached.excludeSpans,
    excludeSumRanges: cached.excludeSumRanges,
    excludeRepeats: cached.excludeRepeats,
    excludeConsecutives: cached.excludeConsecutives,
    excludeParityRatios: cached.excludeParityRatios,
    excludeSizeRatios: cached.excludeSizeRatios
  };
  return stableMathAssistSignature(cachedAssist) !== stableMathAssistSignature(assist);
}

function resolveStableMathAssist(prediction) {
  const raw = (prediction && prediction.stableMathFilter)
    || (prediction && prediction.detail && prediction.detail.stableMathFilter)
    || (state && state.stableMathFilter)
    || stableMathFilterBridge.slimStableMathFilter(stableMathFilterBridge.readCachedFilter())
    || null;
  if (!raw || typeof raw !== "object") {
    return {
      ok: false,
      excludeDigits: [],
      excludeShapes: [],
      excludeSpans: [],
      excludeSumRanges: [],
      excludeRepeats: [],
      excludeConsecutives: [],
      excludeParityRatios: [],
      excludeSizeRatios: [],
      summary: null,
      source: "missing"
    };
  }
  const digits = (Array.isArray(raw.exclude_digits) ? raw.exclude_digits : [])
    .map((value) => String(value))
    .filter((value) => /^\d$/.test(value));
  const shapes = Array.isArray(raw.exclude_shapes) ? raw.exclude_shapes.map(String) : [];
  const spans = Array.isArray(raw.exclude_spans) ? raw.exclude_spans.map(Number).filter((n) => Number.isFinite(n)) : [];
  const sums = Array.isArray(raw.exclude_sum_ranges) ? raw.exclude_sum_ranges.map(String) : [];
  const fw = raw.framework && typeof raw.framework === "object" ? raw.framework : {};
  const repeats = Array.isArray(raw.exclude_repeats)
    ? raw.exclude_repeats.map(String)
    : (Array.isArray(fw.repeatAvoid) ? fw.repeatAvoid.map(String) : []);
  const consecutives = Array.isArray(raw.exclude_consecutives)
    ? raw.exclude_consecutives.map(String)
    : (Array.isArray(fw.consecutiveAvoid) ? fw.consecutiveAvoid.map(String) : []);
  const parityRatios = Array.isArray(raw.exclude_parity_ratios)
    ? raw.exclude_parity_ratios.map(String)
    : (Array.isArray(fw.parityRatioAvoid) ? fw.parityRatioAvoid.map(String) : []);
  const sizeRatios = Array.isArray(raw.exclude_size_ratios)
    ? raw.exclude_size_ratios.map(String)
    : (Array.isArray(fw.sizeRatioAvoid) ? fw.sizeRatioAvoid.map(String) : []);
  return {
    ok: true,
    excludeDigits: digits,
    excludeShapes: shapes,
    excludeSpans: spans,
    excludeSumRanges: sums,
    excludeRepeats: repeats,
    excludeConsecutives: consecutives,
    excludeParityRatios: parityRatios,
    excludeSizeRatios: sizeRatios,
    summary: raw.summary || null,
    framework: raw.framework || null,
    latestPeriod: raw.latest_period || null,
    version: raw.version || "stable-math-filter",
    source: prediction && prediction.stableMathFilter ? "prediction" : "cache",
    // 软筛选：exclude_* 为建议维度；组合筛选请走 bridge.softFilterCombinations
    softRisk: raw.softRisk && typeof raw.softRisk === "object"
      ? raw.softRisk
      : (stableMathFilterBridge.softRiskMeta ? stableMathFilterBridge.softRiskMeta() : { mode: "soft_risk" })
  };
}

function mergeStableMathAvoidDigits(baseAvoid, assist) {
  // 软筛选语义：更稳 exclude_digits 是「建议避开」维度，仍并入 avoid 集合供排序/换码，
  // 但下游打分已改为 Risk Scoring 扣分（不再 -100 硬杀）。勿在此对组合池做绝对 filter。
  const out = baseAvoid instanceof Set
    ? new Set(baseAvoid)
    : new Set(Array.isArray(baseAvoid) ? baseAvoid : []);
  ((assist && assist.excludeDigits) || []).forEach((digit) => out.add(String(digit)));
  return out;
}

function attachStableMathAssistToSnapshot(snapshot, assist, label) {
  if (!snapshot || typeof snapshot !== "object" || !assist || !assist.ok) return snapshot;
  const lines = [];
  const digits = (assist.excludeDigits || []).length ? assist.excludeDigits.join("、") : "无";
  lines.push(`${label}挂载更稳排除单码：${digits}`);
  if ((assist.excludeShapes || []).length) lines.push(`${label}挂载更稳排除形态：${assist.excludeShapes.join("、")}`);
  if ((assist.excludeSpans || []).length) lines.push(`${label}挂载更稳排除跨度：${assist.excludeSpans.join("、")}`);
  if ((assist.excludeSumRanges || []).length) lines.push(`${label}挂载更稳排除和值：${assist.excludeSumRanges.join("、")}`);
  if ((assist.excludeRepeats || []).length) lines.push(`${label}挂载更稳排除重号：${assist.excludeRepeats.join("、")}`);
  if ((assist.excludeConsecutives || []).length) lines.push(`${label}挂载更稳排除连号：${assist.excludeConsecutives.join("、")}`);
  if ((assist.excludeParityRatios || []).length) lines.push(`${label}挂载更稳排除奇偶比：${assist.excludeParityRatios.join("、")}`);
  if ((assist.excludeSizeRatios || []).length) lines.push(`${label}挂载更稳排除大小比：${assist.excludeSizeRatios.join("、")}`);
  snapshot.stableMathAssist = {
    version: assist.version,
    latestPeriod: assist.latestPeriod,
    source: assist.source,
    excludeDigits: assist.excludeDigits.slice(),
    excludeShapes: assist.excludeShapes.slice(),
    excludeSpans: assist.excludeSpans.slice(),
    excludeSumRanges: assist.excludeSumRanges.slice(),
    excludeRepeats: (assist.excludeRepeats || []).slice(),
    excludeConsecutives: (assist.excludeConsecutives || []).slice(),
    excludeParityRatios: (assist.excludeParityRatios || []).slice(),
    excludeSizeRatios: (assist.excludeSizeRatios || []).slice(),
    summary: assist.summary
  };
  if (!Array.isArray(snapshot.repairReport)) snapshot.repairReport = [];
  lines.forEach((line) => {
    if (snapshot.repairReport.indexOf(line) < 0) snapshot.repairReport.push(line);
  });
  if (!snapshot.framework || typeof snapshot.framework !== "object") snapshot.framework = {};
  snapshot.framework.stableMathAssist = true;
  snapshot.framework.excludeAlgorithm = "model-aligned-stable-exclude";
  return snapshot;
}

const STABLE_MATH_SHAPE_CODE_MAP = {
  "全奇": "单单单单",
  "全偶": "双双双双",
  "全大": "大大大大",
  "全小": "小小小小",
  "全0路": "小小小小",
  "全1路": "单单单单",
  "全2路": "双双双双"
};

const STABLE_MATH_PARITY_SHAPE_LABELS = new Set(["全奇", "全偶", "全1路", "全2路"]);

function isParityOnlyShapeCode(code) {
  return /^[单双]{3,4}$/.test(String(code || ""));
}

function stableMathShapeFrameworkItem(shapeLabel, scopeLength) {
  const label = String(shapeLabel || "").trim();
  // 三定系列禁用单双形态挂载（全奇/全偶等）
  if (STABLE_MATH_PARITY_SHAPE_LABELS.has(label)) return null;
  const full = STABLE_MATH_SHAPE_CODE_MAP[label];
  if (!full) return null;
  const len = Math.max(3, Math.min(4, Number(scopeLength || 3)));
  const code = len >= 4 ? full : full.slice(0, len);
  if (!/^[大小单双]{3,4}$/.test(code)) return null;
  if (isParityOnlyShapeCode(code)) return null;
  const isSize = /^[大小]{3,4}$/.test(code);
  return {
    kind: "derivedReverseShape",
    key: isSize ? "reverseSize" : "stableMathShape",
    side: "recommend",
    name: `更稳排除形态(${label})`,
    code,
    shapeKey: isSize ? "shapeSize" : "shapeParity",
    shapeScopeLength: len,
    mode: "avoid",
    derivedFrom: "stable-math-filter"
  };
}

function stableMathSpanFrameworkItem(span) {
  const value = Number(span);
  if (!Number.isFinite(value)) return null;
  return {
    kind: "extra",
    key: "spanAvoid",
    side: "recommend",
    name: "更稳排除跨度",
    code: String(value),
    mode: "avoid",
    derivedFrom: "stable-math-filter"
  };
}

function stableMathAssistPickBoost(assist, item) {
  if (!assist || !assist.ok || !item) return 0;
  let boost = 0;
  const digit = frameworkSingleExcludeDigitToken(item);
  if (digit && (assist.excludeDigits || []).includes(String(digit))) boost += 18000;
  const noDynamicDigits = !(assist.excludeDigits || []).length;
  if (noDynamicDigits) {
    if ((assist.excludeShapes || []).length && item.kind === "derivedReverseShape") boost += 15000;
    if ((assist.excludeSpans || []).length && item.key === "spanAvoid") boost += 12000;
    if ((assist.excludeSumRanges || []).length && item.key === "sumRangeAvoid") boost += 10000;
    if (item.derivedFrom === "stable-math-filter") boost += 8000;
  }
  return boost;
}

function pickStableMathShapeCandidate(assist, history, evaluatedCount, scopeLength) {
  if (!assist || !assist.ok || !(assist.excludeShapes || []).length) return null;
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers)) : [];
  for (let i = 0; i < assist.excludeShapes.length; i += 1) {
    const raw = stableMathShapeFrameworkItem(assist.excludeShapes[i], scopeLength);
    if (!raw) continue;
    const item = autoTop6DecorateItem(safeHistory, raw, evaluatedCount);
    item.shapeScopeLength = scopeLength || 3;
    return item;
  }
  return null;
}

function ensurePredictionThreeDingTwoSnapshot(prediction, history) {
  // retired: 三定框架已删除
  if (prediction && typeof prediction === "object") {
    delete prediction.threeDingTwo;
    delete prediction.threeDingTwoItems;
    delete prediction.threeDingTwoRepairReport;
    if (prediction.recommendationMeta && typeof prediction.recommendationMeta === "object") {
      delete prediction.recommendationMeta.threeDingTwoSnapshotVersion;
    }
  }
  return null;
}

function ensurePredictionThreeDingOneSnapshot(prediction, history) {
  // retired: 三定框架已删除
  if (prediction && typeof prediction === "object") {
    delete prediction.threeDingOne;
    delete prediction.threeDingOneItems;
    delete prediction.threeDingOneRepairReport;
    if (prediction.recommendationMeta && typeof prediction.recommendationMeta === "object") {
      delete prediction.recommendationMeta.threeDingOneSnapshotVersion;
    }
  }
  return null;
}


function forceSealKillFrameworkSnapshot(snapshot, prediction, sealedAt) {
  if (!snapshot || !prediction) return null;
  const at = String(sealedAt || prediction.generatedAt || isoNow());
  snapshot.sealedAt = at;
  snapshot.predictionGeneratedAt = String(prediction.generatedAt || at);
  snapshot.sealedBaseUid = String(prediction.baseUid || "");
  snapshot.sealedBasePeriod = String(prediction.basePeriod || "");
  snapshot.sealHash = snapshotSealHash(snapshot, prediction);
  snapshot.sealed = true;
  return snapshot;
}

function backfillKillFrameworkEvaluations(options) {
  const opts = options && typeof options === "object" ? options : {};
  const limit = Math.max(1, Math.min(Number(opts.limit || 1000), MODEL_STABLE_WINDOW, 2000));
  const comboWindow = Math.max(30, Math.min(Number(opts.comboWindow || 120), MODEL_STABLE_WINDOW));
  const allowUnhealthy = opts.allowUnhealthy !== false;
  const history = Array.isArray(state && state.history) ? state.history : [];
  if (history.length < 40) {
    return { ok: false, reason: "history-too-short", done: 0, skipped: 0, failed: 0, limit };
  }
  const byUid = new Map();
  history.forEach((row) => {
    if (row && row.uid) byUid.set(String(row.uid), row);
  });
  let done = 0;
  let skipped = 0;
  let failed = 0;
  const predictions = (Array.isArray(state.predictions) ? state.predictions : []).filter((item) => item && item.evaluated && item.evaluation);
  for (let i = 0; i < predictions.length && done < limit; i += 1) {
    const prediction = predictions[i];
    const existing = prediction.evaluation && prediction.evaluation.killFramework;
    // 已有完整核算结果（含 reconstructed）就跳过；否则会每轮重算同一批，启动后持续占满 CPU
    if (
      existing &&
      existing.version === KILL_FRAMEWORK_SNAPSHOT_VERSION &&
      Number(existing.total || 0) === KILL_FRAMEWORK_ITEM_COUNT &&
      typeof existing.success === "boolean"
    ) {
      skipped += 1;
      continue;
    }
    const targetUid = String(prediction.evaluation.targetUid || "");
    const actual = targetUid ? byUid.get(targetUid) : null;
    if (!actual || !Array.isArray(actual.numbers)) {
      failed += 1;
      continue;
    }
    if (!prediction.positionKills || typeof prediction.positionKills !== "object") {
      failed += 1;
      continue;
    }
    const baseIdx = history.findIndex((row) => String(row && row.uid || "") === String(prediction.baseUid || ""));
    const trainHistory = baseIdx >= 0 ? history.slice(baseIdx) : history.slice(1);
    if (trainHistory.length < 30) {
      failed += 1;
      continue;
    }
    const targetMs = Date.parse(String(actual.opentime || ""));
    if (!Number.isFinite(targetMs)) {
      failed += 1;
      continue;
    }
    let sealedAt = String(prediction.generatedAt || "");
    let sealedMs = Date.parse(sealedAt);
    // 历史回填：保证 generatedAt/sealedAt 都早于开奖时间，且 generatedAt <= sealedAt
    if (!Number.isFinite(sealedMs) || sealedMs > targetMs) {
      sealedAt = new Date(targetMs - 1000).toISOString();
      prediction.generatedAt = sealedAt;
      sealedMs = targetMs - 1000;
    } else if (!prediction.generatedAt) {
      prediction.generatedAt = sealedAt;
    }
    try {
      const snapshot = buildKillFrameworkSnapshot(prediction, trainHistory, { comboWindow, allowUnhealthy });
      if (!killFrameworkSnapshotIsComplete(snapshot)) {
        failed += 1;
        continue;
      }
      forceSealKillFrameworkSnapshot(snapshot, prediction, sealedAt);
      prediction.killFramework = snapshot;
      prediction.killFrameworkItems = snapshot.items;
      prediction.killFrameworkRepairReport = snapshot.repairReport;
      const evaluated = evaluateKillFrameworkSnapshot(prediction, actual);
      if (!evaluated || evaluated.truthEligible !== true || typeof evaluated.success !== "boolean") {
        failed += 1;
        continue;
      }
      evaluated.reconstructed = true;
      evaluated.reconstructedAt = isoNow();
      evaluated.truthEligible = false;
      evaluated.reason = "reconstructed-historical-backfill-not-for-truth";
      prediction.evaluation.killFramework = evaluated;
      done += 1;
    } catch (err) {
      failed += 1;
    }
  }
  refreshBackendDailyStreaks();
  invalidatePanelStateCache();
  if (!state.killFrameworkBackfill || typeof state.killFrameworkBackfill !== "object") state.killFrameworkBackfill = {};
  state.killFrameworkBackfill.lastAt = isoNow();
  state.killFrameworkBackfill.lastDone = done;
  state.killFrameworkBackfill.lastSkipped = skipped;
  state.killFrameworkBackfill.lastFailed = failed;
  state.killFrameworkBackfill.totalDone = Number(state.killFrameworkBackfill.totalDone || 0) + done;
  return { ok: true, done, skipped, failed, limit, comboWindow, history: history.length, predictions: predictions.length };
}

let killFrameworkBackfillRunning = false;

function scheduleKillFrameworkHistoryBackfill() {
  if (!FRAMEWORK_BACKFILL_ENABLED) return;
  if (killFrameworkBackfillRunning) return;
  if (!state || typeof state !== "object") return;
  if (!state.killFrameworkBackfill || typeof state.killFrameworkBackfill !== "object") state.killFrameworkBackfill = {};
  if (state.killFrameworkBackfill.completed === true) return;
  killFrameworkBackfillRunning = true;
  const chunkLimit = FRAMEWORK_BACKFILL_CHUNK;
  const maxRounds = FRAMEWORK_BACKFILL_MAX_ROUNDS;
  let round = 0;
  const runChunk = () => {
    try {
      if (syncRunning || predictionBuildRunning) {
        setTimeout(runChunk, FRAMEWORK_BACKFILL_GAP_MS);
        return;
      }
      round += 1;
      const result = backfillKillFrameworkEvaluations({ limit: chunkLimit, comboWindow: 80, allowUnhealthy: true });
      if (result && result.done > 0 && (round % 5 === 0 || round >= maxRounds)) {
        saveState({ skipMemoryCompact: true, skipMirror: true });
      }
      const metrics = savedSnapshotTruthMetrics("killFramework", KILL_FRAMEWORK_SNAPSHOT_VERSION, KILL_FRAMEWORK_ITEM_COUNT, MODEL_STABLE_WINDOW);
      const eligible = Number(metrics && metrics.total || 0);
      addLog("杀码框架历史回填分片", { round, done: result && result.done, skipped: result && result.skipped, failed: result && result.failed, eligible });
      const noMoreWork = !result || Number(result.done || 0) === 0;
      if (noMoreWork || eligible >= MODEL_STABLE_WINDOW || round >= maxRounds) {
        // 达轮次上限也标记完成，避免每次重启重复烧 CPU；eligible 不足不阻塞主链路
        state.killFrameworkBackfill.completed = true;
        state.killFrameworkBackfill.eligible = eligible;
        state.killFrameworkBackfill.finishedAt = isoNow();
        state.killFrameworkBackfill.stopReason = noMoreWork
          ? "no-more-work"
          : (eligible >= MODEL_STABLE_WINDOW ? "eligible-met" : "max-rounds");
        saveState();
        killFrameworkBackfillRunning = false;
        return;
      }
      setTimeout(runChunk, FRAMEWORK_BACKFILL_GAP_MS);
    } catch (err) {
      addLog("杀码框架历史回填失败", { error: String(err && err.message || err) });
      killFrameworkBackfillRunning = false;
    }
  };
  setTimeout(runChunk, 12000);
}

function forceSealCompactAttackSnapshot(snapshot, prediction, sealedAt) {
  if (!snapshot || !prediction) return null;
  const at = String(sealedAt || prediction.generatedAt || isoNow());
  snapshot.sealedAt = at;
  snapshot.predictionGeneratedAt = String(prediction.generatedAt || at);
  snapshot.sealedBaseUid = String(prediction.baseUid || "");
  snapshot.sealedBasePeriod = String(prediction.basePeriod || "");
  snapshot.sealHash = snapshotSealHash(snapshot, prediction);
  snapshot.sealed = true;
  return snapshot;
}

function backfillCompactAttackEvaluations(options) {
  const opts = options && typeof options === "object" ? options : {};
  const limit = Math.max(1, Math.min(Number(opts.limit || 1000), MODEL_STABLE_WINDOW, 2000));
  const comboWindow = Math.max(30, Math.min(Number(opts.comboWindow || 120), MODEL_STABLE_WINDOW));
  const allowUnhealthy = opts.allowUnhealthy !== false;
  const history = Array.isArray(state && state.history) ? state.history : [];
  if (history.length < 40) {
    return { ok: false, reason: "history-too-short", done: 0, skipped: 0, failed: 0, limit };
  }
  const byUid = new Map();
  history.forEach((row) => {
    if (row && row.uid) byUid.set(String(row.uid), row);
  });
  let done = 0;
  let skipped = 0;
  let failed = 0;
  const predictions = (Array.isArray(state.predictions) ? state.predictions : []).filter((item) => item && item.evaluated && item.evaluation);
  for (let i = 0; i < predictions.length && done < limit; i += 1) {
    const prediction = predictions[i];
    const existing = prediction.evaluation && prediction.evaluation.compactAttack;
    if (
      existing &&
      existing.version === COMPACT_ATTACK_SNAPSHOT_VERSION &&
      Number(existing.total || 0) === COMPACT_ATTACK_ITEM_COUNT &&
      typeof existing.success === "boolean"
    ) {
      skipped += 1;
      continue;
    }
    const targetUid = String(prediction.evaluation.targetUid || "");
    const actual = targetUid ? byUid.get(targetUid) : null;
    if (!actual || !Array.isArray(actual.numbers)) {
      failed += 1;
      continue;
    }
    if (!prediction.positionKills || typeof prediction.positionKills !== "object") {
      failed += 1;
      continue;
    }
    const baseIdx = history.findIndex((row) => String(row && row.uid || "") === String(prediction.baseUid || ""));
    const trainHistory = baseIdx >= 0 ? history.slice(baseIdx) : history.slice(1);
    if (trainHistory.length < 30) {
      failed += 1;
      continue;
    }
    const targetMs = Date.parse(String(actual.opentime || ""));
    if (!Number.isFinite(targetMs)) {
      failed += 1;
      continue;
    }
    let sealedAt = String(prediction.generatedAt || "");
    let sealedMs = Date.parse(sealedAt);
    if (!Number.isFinite(sealedMs) || sealedMs > targetMs) {
      sealedAt = new Date(targetMs - 1000).toISOString();
      prediction.generatedAt = sealedAt;
      sealedMs = targetMs - 1000;
    } else if (!prediction.generatedAt) {
      prediction.generatedAt = sealedAt;
    }
    try {
      const snapshot = buildCompactAttackSnapshot(prediction, trainHistory, { comboWindow, allowUnhealthy });
      if (!compactAttackSnapshotIsComplete(snapshot)) {
        failed += 1;
        continue;
      }
      forceSealCompactAttackSnapshot(snapshot, prediction, sealedAt);
      prediction.compactAttack = snapshot;
      prediction.compactAttackItems = snapshot.items;
      prediction.compactAttackRepairReport = snapshot.repairReport;
      const evaluated = evaluateCompactAttackSnapshot(prediction, actual);
      if (!evaluated || evaluated.truthEligible !== true || typeof evaluated.success !== "boolean") {
        failed += 1;
        continue;
      }
      evaluated.reconstructed = true;
      evaluated.reconstructedAt = isoNow();
      evaluated.truthEligible = false;
      evaluated.reason = "reconstructed-historical-backfill-not-for-truth";
      prediction.evaluation.compactAttack = evaluated;
      done += 1;
    } catch (err) {
      failed += 1;
    }
  }
  refreshBackendDailyStreaks();
  invalidatePanelStateCache();
  if (!state.compactAttackBackfill || typeof state.compactAttackBackfill !== "object") state.compactAttackBackfill = {};
  state.compactAttackBackfill.lastAt = isoNow();
  state.compactAttackBackfill.lastDone = done;
  state.compactAttackBackfill.lastSkipped = skipped;
  state.compactAttackBackfill.lastFailed = failed;
  state.compactAttackBackfill.totalDone = Number(state.compactAttackBackfill.totalDone || 0) + done;
  return { ok: true, done, skipped, failed, limit, comboWindow, history: history.length, predictions: predictions.length };
}

let compactAttackBackfillRunning = false;

function scheduleCompactAttackHistoryBackfill() {
  if (!FRAMEWORK_BACKFILL_ENABLED) return;
  if (compactAttackBackfillRunning) return;
  if (!state || typeof state !== "object") return;
  if (!state.compactAttackBackfill || typeof state.compactAttackBackfill !== "object") state.compactAttackBackfill = {};
  const targetKey = `${COMPACT_ATTACK_SNAPSHOT_VERSION}:sim${FRAMEWORK_SIM_SAMPLE_TARGET}`;
  if (state.compactAttackBackfill.targetKey !== targetKey) {
    state.compactAttackBackfill = {
      version: COMPACT_ATTACK_SNAPSHOT_VERSION,
      targetKey,
      simTarget: FRAMEWORK_SIM_SAMPLE_TARGET,
      completed: false,
      resetAt: isoNow()
    };
  }
  if (state.compactAttackBackfill.completed === true) return;
  compactAttackBackfillRunning = true;
  const chunkLimit = FRAMEWORK_BACKFILL_CHUNK;
  const maxRounds = FRAMEWORK_BACKFILL_MAX_ROUNDS;
  let round = 0;
  const runChunk = () => {
    try {
      if (syncRunning || predictionBuildRunning) {
        setTimeout(runChunk, FRAMEWORK_BACKFILL_GAP_MS);
        return;
      }
      round += 1;
      const result = backfillCompactAttackEvaluations({ limit: chunkLimit, comboWindow: 80, allowUnhealthy: true });
      if (result && result.done > 0 && (round % 5 === 0 || round >= maxRounds)) {
        saveState({ skipMemoryCompact: true, skipMirror: true });
      }
      const sim = savedSnapshotSimMetrics("compactAttack", COMPACT_ATTACK_SNAPSHOT_VERSION, COMPACT_ATTACK_ITEM_COUNT, FRAMEWORK_SIM_SAMPLE_TARGET);
      const simTotal = Number(sim && sim.total || 0);
      addLog("精简进攻框架历史回填分片", {
        round,
        done: result && result.done,
        skipped: result && result.skipped,
        failed: result && result.failed,
        simTotal,
        simTarget: FRAMEWORK_SIM_SAMPLE_TARGET
      });
      const noMoreWork = !result || Number(result.done || 0) === 0;
      if (noMoreWork || simTotal >= FRAMEWORK_SIM_SAMPLE_TARGET || round >= maxRounds) {
        state.compactAttackBackfill.completed = true;
        state.compactAttackBackfill.simTotal = simTotal;
        state.compactAttackBackfill.finishedAt = isoNow();
        state.compactAttackBackfill.stopReason = noMoreWork
          ? "no-more-work"
          : (simTotal >= FRAMEWORK_SIM_SAMPLE_TARGET ? "sim-target-met" : "max-rounds");
        saveState();
        compactAttackBackfillRunning = false;
        return;
      }
      setTimeout(runChunk, FRAMEWORK_BACKFILL_GAP_MS);
    } catch (err) {
      addLog("精简进攻框架历史回填失败", { error: String(err && err.message || err) });
      compactAttackBackfillRunning = false;
    }
  };
  setTimeout(runChunk, 15000);
}

function forceSealThreeDingSnapshot(snapshot, prediction, sealedAt) {
  if (!snapshot || !prediction) return null;
  const at = String(sealedAt || prediction.generatedAt || isoNow());
  snapshot.sealedAt = at;
  snapshot.predictionGeneratedAt = String(prediction.generatedAt || at);
  snapshot.sealedBaseUid = String(prediction.baseUid || "");
  snapshot.sealedBasePeriod = String(prediction.basePeriod || "");
  snapshot.sealHash = snapshotSealHash(snapshot, prediction);
  snapshot.sealed = true;
  return snapshot;
}

function backfillThreeDingEvaluations(storeName, options) {
  const opts = options && typeof options === "object" ? options : {};
  const isTwo = storeName === "threeDingTwo";
  const version = isTwo ? THREE_DING_TWO_SNAPSHOT_VERSION : THREE_DING_ONE_SNAPSHOT_VERSION;
  const itemCount = isTwo ? THREE_DING_TWO_ITEM_COUNT : THREE_DING_ONE_ITEM_COUNT;
  const isComplete = isTwo ? threeDingTwoSnapshotIsComplete : threeDingOneSnapshotIsComplete;
  const buildFn = isTwo ? buildThreeDingTwoSnapshot : buildThreeDingOneSnapshot;
  const evaluateFn = isTwo ? evaluateThreeDingTwoSnapshot : evaluateThreeDingOneSnapshot;
  const limit = Math.max(1, Math.min(Number(opts.limit || FRAMEWORK_BACKFILL_CHUNK), FRAMEWORK_SIM_SAMPLE_TARGET, 2000));
  const comboWindow = Math.max(30, Math.min(Number(opts.comboWindow || 120), MODEL_STABLE_WINDOW));
  const history = Array.isArray(state && state.history) ? state.history : [];
  if (history.length < 40) {
    return { ok: false, reason: "history-too-short", done: 0, skipped: 0, failed: 0, limit, storeName };
  }
  const byUid = new Map();
  history.forEach((row) => {
    if (row && row.uid) byUid.set(String(row.uid), row);
  });
  let done = 0;
  let skipped = 0;
  let failed = 0;
  const predictions = (Array.isArray(state.predictions) ? state.predictions : []).filter((item) => item && item.evaluated && item.evaluation);
  for (let i = 0; i < predictions.length && done < limit; i += 1) {
    const prediction = predictions[i];
    const existing = prediction.evaluation && prediction.evaluation[storeName];
    if (
      existing &&
      existing.version === version &&
      Number(existing.total || 0) === itemCount &&
      typeof existing.success === "boolean"
    ) {
      skipped += 1;
      continue;
    }
    const targetUid = String(prediction.evaluation.targetUid || "");
    const actual = targetUid ? byUid.get(targetUid) : null;
    if (!actual || !Array.isArray(actual.numbers)) {
      failed += 1;
      continue;
    }
    if (!prediction.positionKills || typeof prediction.positionKills !== "object") {
      failed += 1;
      continue;
    }
    const baseIdx = history.findIndex((row) => String(row && row.uid || "") === String(prediction.baseUid || ""));
    const trainHistory = baseIdx >= 0 ? history.slice(baseIdx) : history.slice(1);
    if (trainHistory.length < 30) {
      failed += 1;
      continue;
    }
    const targetMs = Date.parse(String(actual.opentime || ""));
    if (!Number.isFinite(targetMs)) {
      failed += 1;
      continue;
    }
    let sealedAt = String(prediction.generatedAt || "");
    let sealedMs = Date.parse(sealedAt);
    if (!Number.isFinite(sealedMs) || sealedMs > targetMs) {
      sealedAt = new Date(targetMs - 1000).toISOString();
      prediction.generatedAt = sealedAt;
      sealedMs = targetMs - 1000;
    } else if (!prediction.generatedAt) {
      prediction.generatedAt = sealedAt;
    }
    try {
      const avoidDigits = new Set([
        ...frameworkSingleExcludeDigits(prediction.compactAttack && prediction.compactAttack.items),
        ...(isTwo ? [] : [...frameworkSingleExcludeDigits(prediction.threeDingTwo && prediction.threeDingTwo.items)])
      ]);
      const avoidAnyTwoCodes = new Set([
        ...frameworkAnyTwoCodesFromItems(prediction.compactAttack && prediction.compactAttack.items),
        ...(isTwo ? [] : [...frameworkAnyTwoCodesFromItems(prediction.threeDingTwo && prediction.threeDingTwo.items)])
      ]);
      const avoidTwoDigitCodes = isTwo
        ? new Set()
        : frameworkTwoDigitCodesFromItems(prediction.threeDingTwo && prediction.threeDingTwo.items);
      const snapshot = buildFn(prediction, trainHistory, {
        comboWindow,
        allowUnhealthy: true,
        avoidDigits,
        avoidAnyTwoCodes,
        avoidTwoDigitCodes
      });
      if (!isComplete(snapshot)) {
        failed += 1;
        continue;
      }
      forceSealThreeDingSnapshot(snapshot, prediction, sealedAt);
      prediction[storeName] = snapshot;
      prediction[storeName + "Items"] = snapshot.items;
      prediction[storeName + "RepairReport"] = snapshot.repairReport;
      const evaluated = evaluateFn(prediction, actual);
      if (!evaluated || evaluated.truthEligible !== true || typeof evaluated.success !== "boolean") {
        failed += 1;
        continue;
      }
      evaluated.reconstructed = true;
      evaluated.reconstructedAt = isoNow();
      evaluated.truthEligible = false;
      evaluated.reason = "reconstructed-historical-backfill-not-for-truth";
      prediction.evaluation[storeName] = evaluated;
      done += 1;
    } catch (err) {
      failed += 1;
    }
  }
  refreshBackendDailyStreaks();
  invalidatePanelStateCache();
  const stateKey = storeName + "Backfill";
  if (!state[stateKey] || typeof state[stateKey] !== "object") state[stateKey] = {};
  state[stateKey].lastAt = isoNow();
  state[stateKey].lastDone = done;
  state[stateKey].lastSkipped = skipped;
  state[stateKey].lastFailed = failed;
  state[stateKey].totalDone = Number(state[stateKey].totalDone || 0) + done;
  return { ok: true, done, skipped, failed, limit, comboWindow, history: history.length, predictions: predictions.length, storeName };
}

let threeDingTwoBackfillRunning = false;
let threeDingOneBackfillRunning = false;

function scheduleThreeDingHistoryBackfill(storeName) {
  if (!FRAMEWORK_BACKFILL_ENABLED) return;
  const isTwo = storeName === "threeDingTwo";
  if (isTwo ? threeDingTwoBackfillRunning : threeDingOneBackfillRunning) return;
  if (!state || typeof state !== "object") return;
  const stateKey = storeName + "Backfill";
  if (!state[stateKey] || typeof state[stateKey] !== "object") state[stateKey] = {};
  const version = isTwo ? THREE_DING_TWO_SNAPSHOT_VERSION : THREE_DING_ONE_SNAPSHOT_VERSION;
  const itemCount = isTwo ? THREE_DING_TWO_ITEM_COUNT : THREE_DING_ONE_ITEM_COUNT;
  const targetKey = `${version}:sim${FRAMEWORK_SIM_SAMPLE_TARGET}`;
  if (state[stateKey].targetKey !== targetKey) {
    state[stateKey] = {
      version,
      targetKey,
      simTarget: FRAMEWORK_SIM_SAMPLE_TARGET,
      completed: false,
      resetAt: isoNow()
    };
  }
  if (state[stateKey].completed === true) return;
  if (isTwo) threeDingTwoBackfillRunning = true;
  else threeDingOneBackfillRunning = true;
  const chunkLimit = FRAMEWORK_BACKFILL_CHUNK;
  const maxRounds = FRAMEWORK_BACKFILL_MAX_ROUNDS;
  let round = 0;
  const label = isTwo ? "三定框架A" : "三定框架B";
  const startDelay = isTwo ? 18000 : 22000;
  const runChunk = () => {
    try {
      if (syncRunning || predictionBuildRunning) {
        setTimeout(runChunk, FRAMEWORK_BACKFILL_GAP_MS);
        return;
      }
      round += 1;
      const result = backfillThreeDingEvaluations(storeName, { limit: chunkLimit, comboWindow: 80, allowUnhealthy: true });
      if (result && result.done > 0 && (round % 5 === 0 || round >= maxRounds)) {
        saveState({ skipMemoryCompact: true, skipMirror: true });
      }
      const sim = savedSnapshotSimMetrics(storeName, version, itemCount, FRAMEWORK_SIM_SAMPLE_TARGET);
      const simTotal = Number(sim && sim.total || 0);
      addLog(label + "历史回填分片", {
        round,
        done: result && result.done,
        skipped: result && result.skipped,
        failed: result && result.failed,
        simTotal,
        simTarget: FRAMEWORK_SIM_SAMPLE_TARGET
      });
      const noMoreWork = !result || Number(result.done || 0) === 0;
      if (noMoreWork || simTotal >= FRAMEWORK_SIM_SAMPLE_TARGET || round >= maxRounds) {
        state[stateKey].completed = true;
        state[stateKey].simTotal = simTotal;
        state[stateKey].finishedAt = isoNow();
        state[stateKey].stopReason = noMoreWork
          ? "no-more-work"
          : (simTotal >= FRAMEWORK_SIM_SAMPLE_TARGET ? "sim-target-met" : "max-rounds");
        saveState();
        if (isTwo) threeDingTwoBackfillRunning = false;
        else threeDingOneBackfillRunning = false;
        return;
      }
      setTimeout(runChunk, FRAMEWORK_BACKFILL_GAP_MS);
    } catch (err) {
      addLog(label + "历史回填失败", { error: String(err && err.message || err) });
      if (isTwo) threeDingTwoBackfillRunning = false;
      else threeDingOneBackfillRunning = false;
    }
  };
  setTimeout(runChunk, startDelay);
}

function withFrozenSelectionLocks(fn) {
  const prevStable = state && state.stable120Locks;
  const prevModel = state && state.modelSelectionLocks;
  try {
    if (state) {
      state.stable120Locks = JSON.parse(JSON.stringify(prevStable && typeof prevStable === "object" ? prevStable : {}));
      state.modelSelectionLocks = JSON.parse(JSON.stringify(prevModel && typeof prevModel === "object" ? prevModel : {}));
    }
    return fn();
  } finally {
    if (state) {
      state.stable120Locks = prevStable;
      state.modelSelectionLocks = prevModel;
    }
  }
}

function frameworkSimHistorySignature(history) {
  const rows = Array.isArray(history) ? history : [];
  const latest = rows[0] && rows[0].period ? String(rows[0].period) : "";
  return `${rows.length}:${latest}`;
}

// 参考回测：只读 RDS 灌注的共享 history，内存聚合，不落 state.json / 不复制 history
let frameworkSimRuntime = null;
let frameworkSimHistoryBackfillRunning = false;

function ensureFrameworkSimRuntime(forceReset) {
  const history = Array.isArray(state && state.history) ? state.history : [];
  const targetKey = `history-sim-${FRAMEWORK_SIM_SAMPLE_TARGET}`;
  const signature = frameworkSimHistorySignature(history);
  if (
    forceReset ||
    !frameworkSimRuntime ||
    frameworkSimRuntime.targetKey !== targetKey ||
    frameworkSimRuntime.historySignature !== signature
  ) {
    frameworkSimRuntime = {
      targetKey,
      historySignature: signature,
      target: FRAMEWORK_SIM_SAMPLE_TARGET,
      cursor: 0,
      completed: false,
      finishedAt: "",
      stopReason: "",
      stores: {
        compactAttack: {
          version: COMPACT_ATTACK_SNAPSHOT_VERSION,
          expectedTotal: COMPACT_ATTACK_ITEM_COUNT,
          seenPeriods: new Set(),
          total: 0,
          hit: 0,
          nearHit: 0,
          passedItems: 0,
          checkedItems: 0,
          latestSettledPeriod: ""
        },
        threeDingTwo: {
          version: THREE_DING_TWO_SNAPSHOT_VERSION,
          expectedTotal: THREE_DING_TWO_ITEM_COUNT,
          seenPeriods: new Set(),
          total: 0,
          hit: 0,
          nearHit: 0,
          passedItems: 0,
          checkedItems: 0,
          latestSettledPeriod: ""
        },
        threeDingOne: {
          version: THREE_DING_ONE_SNAPSHOT_VERSION,
          expectedTotal: THREE_DING_ONE_ITEM_COUNT,
          seenPeriods: new Set(),
          total: 0,
          hit: 0,
          nearHit: 0,
          passedItems: 0,
          checkedItems: 0,
          latestSettledPeriod: ""
        }
      }
    };
  }
  return frameworkSimRuntime;
}

function accumulateFrameworkSimResult(storeName, period, evaluated) {
  const runtime = ensureFrameworkSimRuntime(false);
  const bucket = runtime && runtime.stores && runtime.stores[storeName];
  if (!bucket || !period || !evaluated || typeof evaluated.success !== "boolean") return false;
  if (bucket.seenPeriods.has(period)) return false;
  bucket.seenPeriods.add(period);
  bucket.total += 1;
  if (evaluated.success) bucket.hit += 1;
  const totalItems = Number(evaluated.total || bucket.expectedTotal || 0);
  const passedItems = Number(evaluated.passed || 0);
  if (totalItems > 1 && passedItems >= totalItems - 1) bucket.nearHit += 1;
  if (totalItems > 0) {
    bucket.passedItems += passedItems;
    bucket.checkedItems += totalItems;
  }
  if (!bucket.latestSettledPeriod || String(period).localeCompare(String(bucket.latestSettledPeriod)) > 0) {
    bucket.latestSettledPeriod = period;
  }
  return true;
}

function frameworkSimRuntimeHas(storeName, period) {
  const runtime = frameworkSimRuntime;
  const bucket = runtime && runtime.stores && runtime.stores[storeName];
  return !!(bucket && bucket.seenPeriods && bucket.seenPeriods.has(period));
}

function metricsFromFrameworkSimBucket(bucket, expectedVersion, expectedTotal, limit) {
  if (!bucket || Number(bucket.total || 0) <= 0) return null;
  const total = Math.min(Number(bucket.total || 0), Math.max(1, Number(limit || FRAMEWORK_SIM_SAMPLE_TARGET)));
  const hit = Number(bucket.hit || 0);
  const nearHit = Number(bucket.nearHit || 0);
  const passedItems = Number(bucket.passedItems || 0);
  const checkedItems = Number(bucket.checkedItems || 0);
  return {
    method: "reconstructed-walkforward-reference",
    version: expectedVersion || bucket.version || "",
    expectedTotal: Number(expectedTotal || bucket.expectedTotal || 0),
    total,
    hit,
    rate: total ? Number(((hit / total) * 100).toFixed(2)) : null,
    nearHit,
    nearRate: total ? Number(((nearHit / total) * 100).toFixed(2)) : null,
    passedItems,
    checkedItems,
    avgPassRate: checkedItems ? Number(((passedItems / checkedItems) * 100).toFixed(2)) : null,
    reconstructedCount: total,
    sealedCount: 0,
    latestSettledPeriod: bucket.latestSettledPeriod || "",
    target: FRAMEWORK_SIM_SAMPLE_TARGET,
    note: "Reference walk-forward from shared RDS history only; computed in memory, not persisted to state.json."
  };
}

function backfillFrameworkSimFromHistory(options) {
  const opts = options && typeof options === "object" ? options : {};
  const limit = Math.max(1, Math.min(Number(opts.limit || FRAMEWORK_BACKFILL_CHUNK), 80));
  const comboWindow = Math.max(30, Math.min(Number(opts.comboWindow || 80), MODEL_STABLE_WINDOW));
  const minTrain = Math.max(40, Number(opts.minTrain || 80));
  const history = Array.isArray(state && state.history) ? state.history.filter((row) => row && Array.isArray(row.numbers)) : [];
  if (history.length < minTrain + 5) {
    return { ok: false, reason: "history-too-short", done: 0, skipped: 0, failed: 0, history: history.length };
  }
  const runtime = ensureFrameworkSimRuntime(false);
  const stores = [
    {
      name: "compactAttack",
      version: COMPACT_ATTACK_SNAPSHOT_VERSION,
      itemCount: COMPACT_ATTACK_ITEM_COUNT,
      isComplete: compactAttackSnapshotIsComplete,
      build: (prediction, trainHistory) => buildCompactAttackSnapshot(prediction, trainHistory, { comboWindow, allowUnhealthy: true }),
      evaluate: evaluateCompactAttackSnapshot,
      forceSeal: forceSealCompactAttackSnapshot
    },
    {
      name: "threeDingTwo",
      version: THREE_DING_TWO_SNAPSHOT_VERSION,
      itemCount: THREE_DING_TWO_ITEM_COUNT,
      isComplete: threeDingTwoSnapshotIsComplete,
      build: (prediction, trainHistory, avoid) => buildThreeDingTwoSnapshot(prediction, trainHistory, {
        comboWindow,
        allowUnhealthy: true,
        avoidDigits: avoid && avoid.digits,
        avoidAnyTwoCodes: avoid && avoid.anyTwo
      }),
      evaluate: evaluateThreeDingTwoSnapshot,
      forceSeal: forceSealThreeDingSnapshot
    },
    {
      name: "threeDingOne",
      version: THREE_DING_ONE_SNAPSHOT_VERSION,
      itemCount: THREE_DING_ONE_ITEM_COUNT,
      isComplete: threeDingOneSnapshotIsComplete,
      build: (prediction, trainHistory, avoid) => buildThreeDingOneSnapshot(prediction, trainHistory, {
        comboWindow,
        allowUnhealthy: true,
        avoidDigits: avoid && avoid.digits,
        avoidAnyTwoCodes: avoid && avoid.anyTwo,
        avoidTwoDigitCodes: avoid && avoid.twoDigit
      }),
      evaluate: evaluateThreeDingOneSnapshot,
      forceSeal: forceSealThreeDingSnapshot
    }
  ];

  let cursor = Number(runtime.cursor || 0);
  if (!Number.isFinite(cursor) || cursor < 0) cursor = 0;
  let done = 0;
  let skipped = 0;
  let failed = 0;
  const maxOffset = Math.min(history.length - minTrain - 1, FRAMEWORK_SIM_SAMPLE_TARGET + 50);

  while (done < limit && cursor <= maxOffset) {
    const offset = cursor;
    cursor += 1;
    const actual = history[offset];
    const trainHistory = history.slice(offset + 1);
    if (!actual || !Array.isArray(actual.numbers) || trainHistory.length < minTrain) {
      failed += 1;
      continue;
    }
    const period = String(actual.period || "");
    if (!period) {
      failed += 1;
      continue;
    }
    if (stores.every((store) => frameworkSimRuntimeHas(store.name, period))) {
      skipped += 1;
      continue;
    }
    const targetMs = Date.parse(String(actual.opentime || ""));
    const sealedAt = Number.isFinite(targetMs)
      ? new Date(targetMs - 1000).toISOString()
      : new Date(Date.now() - 1000).toISOString();
    try {
      const prediction = withFrozenSelectionLocks(() => generateBigDataPrediction(trainHistory));
      prediction.generatedAt = sealedAt;
      prediction.baseUid = String(trainHistory[0] && trainHistory[0].uid || prediction.baseUid || "");
      prediction.basePeriod = String(trainHistory[0] && trainHistory[0].period || prediction.basePeriod || "");

      let compactItems = [];
      let twoItems = [];
      for (const store of stores) {
        if (frameworkSimRuntimeHas(store.name, period)) continue;
        const avoid = {
          digits: new Set([
            ...frameworkSingleExcludeDigits(compactItems),
            ...(store.name === "threeDingOne" ? frameworkSingleExcludeDigits(twoItems) : [])
          ]),
          anyTwo: new Set([
            ...frameworkAnyTwoCodesFromItems(compactItems),
            ...(store.name === "threeDingOne" ? frameworkAnyTwoCodesFromItems(twoItems) : [])
          ]),
          twoDigit: store.name === "threeDingOne" ? frameworkTwoDigitCodesFromItems(twoItems) : new Set()
        };
        const snapshot = store.build(prediction, trainHistory, avoid);
        if (!store.isComplete(snapshot)) {
          failed += 1;
          continue;
        }
        store.forceSeal(snapshot, prediction, sealedAt);
        prediction[store.name] = snapshot;
        const evaluated = store.evaluate(prediction, actual);
        if (!evaluated || typeof evaluated.success !== "boolean") {
          failed += 1;
          continue;
        }
        accumulateFrameworkSimResult(store.name, period, evaluated);
        if (store.name === "compactAttack") compactItems = snapshot.items || [];
        if (store.name === "threeDingTwo") twoItems = snapshot.items || [];
      }
      done += 1;
    } catch (err) {
      failed += 1;
    }
  }

  runtime.cursor = cursor;
  invalidatePanelStateCache();
  return {
    ok: true,
    done,
    skipped,
    failed,
    cursor,
    maxOffset,
    limit,
    history: history.length
  };
}

function scheduleFrameworkSimHistoryBackfill() {
  if (!FRAMEWORK_SIM_ENABLED) return;
  if (frameworkSimHistoryBackfillRunning) return;
  if (!state || typeof state !== "object") return;
  ensureFrameworkSimRuntime(true);
  if (frameworkSimRuntime.completed === true) return;
  frameworkSimHistoryBackfillRunning = true;
  // 轻量分片，避免与官方封存回填抢事件循环导致 /panel /state 超时
  const chunkLimit = Math.min(2, FRAMEWORK_BACKFILL_CHUNK);
  const maxRounds = Math.max(FRAMEWORK_BACKFILL_MAX_ROUNDS, 80);
  let round = 0;
  const runChunk = async () => {
    try {
      // 开奖 sync 进行中让出 CPU，避免 /panel /state 假死
      if (syncRunning || predictionBuildRunning) {
        setTimeout(runChunk, FRAMEWORK_BACKFILL_GAP_MS);
        return;
      }
      round += 1;
      const result = backfillFrameworkSimFromHistory({ limit: chunkLimit, comboWindow: 80, minTrain: 80 });
      const totals = {
        compactAttack: Number((frameworkSimRuntime && frameworkSimRuntime.stores.compactAttack.total) || 0),
        threeDingTwo: Number((frameworkSimRuntime && frameworkSimRuntime.stores.threeDingTwo.total) || 0),
        threeDingOne: Number((frameworkSimRuntime && frameworkSimRuntime.stores.threeDingOne.total) || 0)
      };
      const minTotal = Math.min(totals.compactAttack, totals.threeDingTwo, totals.threeDingOne);
      addLog("框架参考回测(RDS内存walk)", {
        round,
        done: result && result.done,
        skipped: result && result.skipped,
        failed: result && result.failed,
        cursor: result && result.cursor,
        totals,
        target: FRAMEWORK_SIM_SAMPLE_TARGET,
        dataSource: state.sharedHistorySource || "memory"
      });
      invalidatePanelStateCache();
      const noMoreWork = !result || (Number(result.done || 0) === 0 && Number(result.skipped || 0) === 0) ||
        (result && Number(result.cursor || 0) > Number(result.maxOffset || 0));
      if (minTotal >= FRAMEWORK_SIM_SAMPLE_TARGET || noMoreWork || round >= maxRounds) {
        frameworkSimRuntime.completed = true;
        frameworkSimRuntime.finishedAt = isoNow();
        frameworkSimRuntime.stopReason = minTotal >= FRAMEWORK_SIM_SAMPLE_TARGET
          ? "sim-target-met"
          : (noMoreWork ? "no-more-work" : "max-rounds");
        frameworkSimHistoryBackfillRunning = false;
        return;
      }
      try { await yieldEventLoop(); } catch (_) { /* ignore */ }
      setTimeout(runChunk, FRAMEWORK_BACKFILL_GAP_MS);
    } catch (err) {
      addLog("框架参考回测(RDS内存walk)失败", { error: String(err && err.message || err) });
      frameworkSimHistoryBackfillRunning = false;
    }
  };
  setTimeout(runChunk, 45000);
}





















function evaluateAutoTop6Snapshot(prediction, actualRecord) {
  const snapshot = prediction && prediction.autoTop6 && Array.isArray(prediction.autoTop6.items)
    ? prediction.autoTop6
    : (prediction && Array.isArray(prediction.autoTop6Items) ? { version: "legacy-autoTop6Items", items: prediction.autoTop6Items, repairReport: prediction.autoTop6RepairReport || [] } : null);
  const truthEligible = autoTop6SnapshotIsComplete(snapshot) && snapshotSealIsValid(snapshot, prediction) && snapshotSealPredatesTarget(snapshot, actualRecord);
  if (!truthEligible) {
    return { ok: false, success: null, passed: 0, total: 0, items: [], reason: "unsealed-or-incomplete-auto-top6-snapshot", truthEligible: false };
  }
  const items = snapshot.items.map((item) => {
    const success = autoTop6RecordSuccess(item, actualRecord);
    return {
      rank: item.rank,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      shapeKey: item.shapeKey || "",
      success,
      actualNumbers: actualRecord && Array.isArray(actualRecord.numbers) ? actualRecord.numbers.slice(0, 5).map(String) : [],
      actualFirstFourDigits: firstFourNumbers(actualRecord),
      actualCompositeHeweiGroup: getCompositeHeweiGroup(actualRecord),
      actualFirstFourSum: String(firstFourSum(actualRecord))
    };
  });
  // 替补 side（定位替补 / 反向形态参考）不参与正式六项全过
  const formal = items.filter((item) => item && item.side !== "backup");
  const valid = formal.filter((item) => typeof item.success === "boolean");
  const passed = valid.filter((item) => item.success).length;
  return {
    ok: valid.length === formal.length && formal.length > 0,
    version: snapshot.version || "backend-auto-top6-snapshot",
    basePeriod: prediction && prediction.basePeriod || "",
    success: valid.length === formal.length && formal.length > 0 ? passed === formal.length : null,
    truthEligible: true,
    sealedAt: snapshot.sealedAt,
    sealedBaseUid: snapshot.sealedBaseUid,
    sealHash: snapshot.sealHash,
    passed,
    total: formal.length,
    items: formal
  };
}

function evaluateRecommend2Snapshot(prediction, actualRecord) {
  const snapshot = prediction && prediction.recommend2 && Array.isArray(prediction.recommend2.items)
    ? prediction.recommend2
    : (prediction && Array.isArray(prediction.recommend2Items) ? { version: "legacy-recommend2Items", items: prediction.recommend2Items, repairReport: prediction.recommend2RepairReport || [] } : null);
  const truthEligible = recommend2SnapshotIsComplete(snapshot) && snapshotSealIsValid(snapshot, prediction) && snapshotSealPredatesTarget(snapshot, actualRecord);
  if (!truthEligible) {
    return { ok: false, success: null, passed: 0, total: 0, items: [], reason: "unsealed-or-incomplete-recommend2-snapshot", truthEligible: false };
  }
  const items = snapshot.items.map((item) => {
    const success = autoTop6RecordSuccess(item, actualRecord);
    return {
      rank: item.rank,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      shapeKey: item.shapeKey || "",
      success,
      actualNumbers: actualRecord && Array.isArray(actualRecord.numbers) ? actualRecord.numbers.slice(0, 5).map(String) : [],
      actualFirstFourDigits: firstFourNumbers(actualRecord),
      actualCompositeHeweiGroup: getCompositeHeweiGroup(actualRecord),
      actualFirstFourSum: String(firstFourSum(actualRecord))
    };
  });
  const valid = items.filter((item) => typeof item.success === "boolean");
  const passed = valid.filter((item) => item.success).length;
  return {
    ok: valid.length === items.length && items.length > 0,
    version: snapshot.version || "backend-recommend2-snapshot",
    basePeriod: prediction && prediction.basePeriod || "",
    success: valid.length === items.length && items.length > 0 ? passed === items.length : null,
    truthEligible: true,
    sealedAt: snapshot.sealedAt,
    sealedBaseUid: snapshot.sealedBaseUid,
    sealHash: snapshot.sealHash,
    passed,
    total: items.length,
    items
  };
}

function evaluateKillFrameworkSnapshot(prediction, actualRecord) {
  const snapshot = prediction && prediction.killFramework && Array.isArray(prediction.killFramework.items)
    ? prediction.killFramework
    : (prediction && Array.isArray(prediction.killFrameworkItems) ? { version: "legacy-killFrameworkItems", items: prediction.killFrameworkItems, repairReport: prediction.killFrameworkRepairReport || [] } : null);
  const truthEligible = killFrameworkSnapshotIsComplete(snapshot) && snapshotSealIsValid(snapshot, prediction) && snapshotSealPredatesTarget(snapshot, actualRecord);
  if (!truthEligible) {
    return { ok: false, success: null, passed: 0, total: 0, items: [], reason: "unsealed-or-incomplete-kill-framework-snapshot", truthEligible: false };
  }
  const items = snapshot.items.map((item) => {
    const success = autoTop6RecordSuccess(item, actualRecord);
    return {
      rank: item.rank,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      shapeKey: item.shapeKey || "",
      success,
      actualNumbers: actualRecord && Array.isArray(actualRecord.numbers) ? actualRecord.numbers.slice(0, 5).map(String) : [],
      actualFirstFourDigits: firstFourNumbers(actualRecord),
      actualCompositeHeweiGroup: getCompositeHeweiGroup(actualRecord),
      actualFirstFourSum: String(firstFourSum(actualRecord))
    };
  });
  const valid = items.filter((item) => typeof item.success === "boolean");
  const passed = valid.filter((item) => item.success).length;
  return {
    ok: valid.length === items.length && items.length > 0,
    version: snapshot.version || KILL_FRAMEWORK_SNAPSHOT_VERSION,
    basePeriod: prediction && prediction.basePeriod || "",
    success: valid.length === items.length && items.length > 0 ? passed === items.length : null,
    truthEligible: true,
    sealedAt: snapshot.sealedAt,
    sealedBaseUid: snapshot.sealedBaseUid,
    sealHash: snapshot.sealHash,
    passed,
    total: items.length,
    items
  };
}

function evaluateFourDingFrameworkSnapshot(prediction, actualRecord) {
  const snapshot = prediction && prediction.fourDingFramework && Array.isArray(prediction.fourDingFramework.items)
    ? prediction.fourDingFramework
    : (prediction && Array.isArray(prediction.fourDingFrameworkItems)
      ? { version: "legacy-fourDingFrameworkItems", items: prediction.fourDingFrameworkItems, repairReport: prediction.fourDingFrameworkRepairReport || [] }
      : null);
  const truthEligible = fourDingFrameworkSnapshotIsComplete(snapshot)
    && snapshotSealIsValid(snapshot, prediction)
    && snapshotSealPredatesTarget(snapshot, actualRecord);
  if (!truthEligible) {
    return {
      ok: false,
      success: null,
      passed: 0,
      total: 0,
      items: [],
      reason: "unsealed-or-incomplete-four-ding-framework-snapshot",
      truthEligible: false
    };
  }
  const items = snapshot.items.map((item) => {
    const success = autoTop6RecordSuccess(item, actualRecord);
    return {
      rank: item.rank,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      shapeKey: item.shapeKey || "",
      success,
      actualNumbers: actualRecord && Array.isArray(actualRecord.numbers) ? actualRecord.numbers.slice(0, 5).map(String) : [],
      actualFirstFourDigits: firstFourNumbers(actualRecord),
      actualCompositeHeweiGroup: getCompositeHeweiGroup(actualRecord),
      actualFirstFourSum: String(firstFourSum(actualRecord))
    };
  });
  const valid = items.filter((item) => typeof item.success === "boolean");
  const passed = valid.filter((item) => item.success).length;
  return {
    ok: valid.length === items.length && items.length > 0,
    version: snapshot.version || FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION,
    basePeriod: prediction && prediction.basePeriod || "",
    success: valid.length === items.length && items.length > 0 ? passed === items.length : null,
    truthEligible: true,
    sealedAt: snapshot.sealedAt,
    sealedBaseUid: snapshot.sealedBaseUid,
    sealHash: snapshot.sealHash,
    shot: snapshot.shot || "",
    passed,
    total: items.length,
    items
  };
}

function evaluateCompactAttackSnapshot(prediction, actualRecord) {
  const snapshot = prediction && prediction.compactAttack && Array.isArray(prediction.compactAttack.items)
    ? prediction.compactAttack
    : (prediction && Array.isArray(prediction.compactAttackItems) ? { version: "legacy-compactAttackItems", items: prediction.compactAttackItems, repairReport: prediction.compactAttackRepairReport || [] } : null);
  const truthEligible = compactAttackSnapshotIsComplete(snapshot) && snapshotSealIsValid(snapshot, prediction) && snapshotSealPredatesTarget(snapshot, actualRecord);
  if (!truthEligible) {
    return { ok: false, success: null, passed: 0, total: 0, items: [], reason: "unsealed-or-incomplete-compact-attack-snapshot", truthEligible: false };
  }
  const items = snapshot.items.map((item) => {
    const success = autoTop6RecordSuccess(item, actualRecord);
    return {
      rank: item.rank,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      shapeKey: item.shapeKey || "",
      success,
      actualNumbers: actualRecord && Array.isArray(actualRecord.numbers) ? actualRecord.numbers.slice(0, 5).map(String) : [],
      actualFirstFourDigits: firstFourNumbers(actualRecord),
      actualCompositeHeweiGroup: getCompositeHeweiGroup(actualRecord),
      actualFirstFourSum: String(firstFourSum(actualRecord))
    };
  });
  const valid = items.filter((item) => typeof item.success === "boolean");
  const passed = valid.filter((item) => item.success).length;
  return {
    ok: valid.length === items.length && items.length > 0,
    version: snapshot.version || COMPACT_ATTACK_SNAPSHOT_VERSION,
    basePeriod: prediction && prediction.basePeriod || "",
    success: valid.length === items.length && items.length > 0 ? passed === items.length : null,
    truthEligible: true,
    sealedAt: snapshot.sealedAt,
    sealedBaseUid: snapshot.sealedBaseUid,
    sealHash: snapshot.sealHash,
    passed,
    total: items.length,
    items
  };
}

function evaluateScopedThreeDingSnapshot(prediction, actualRecord, storeName, isComplete, versionConst, defaultScope) {
  const snapshot = prediction && prediction[storeName] && Array.isArray(prediction[storeName].items)
    ? prediction[storeName]
    : (prediction && Array.isArray(prediction[storeName + "Items"]) ? { version: "legacy-" + storeName + "Items", items: prediction[storeName + "Items"], repairReport: prediction[storeName + "RepairReport"] || [] } : null);
  const truthEligible = isComplete(snapshot) && snapshotSealIsValid(snapshot, prediction) && snapshotSealPredatesTarget(snapshot, actualRecord);
  if (!truthEligible) {
    return { ok: false, success: null, passed: 0, total: 0, items: [], reason: "unsealed-or-incomplete-" + storeName + "-snapshot", truthEligible: false };
  }
  let heweiScopeIndexes = heweiScopeIndexesFromItems(snapshot.items);
  if (heweiScopeIndexes.length < 2 && Array.isArray(defaultScope) && defaultScope.length >= 2) {
    heweiScopeIndexes = defaultScope.slice();
  }
  if (Array.isArray(snapshot.heweiScopeIndexes) && snapshot.heweiScopeIndexes.length >= 2) {
    heweiScopeIndexes = snapshot.heweiScopeIndexes.map(Number).filter((value) => Number.isInteger(value) && value >= 0 && value <= 3);
  }
  const items = snapshot.items.map((item) => {
    let evaluateItem = item;
    if (item && (item.key === "anyTwoHeweiHit" || item.key === "anyThreeHeweiHit") && heweiScopeIndexes.length >= 2) {
      evaluateItem = Object.assign({}, item, { heweiScopeIndexes });
    } else if (item && item.kind === "derivedReverseShape") {
      const patch = { shapeScopeLength: 3 };
      if (Array.isArray(item.shapeScopeIndexes) && item.shapeScopeIndexes.length === 3) {
        patch.shapeScopeIndexes = item.shapeScopeIndexes.slice();
      }
      evaluateItem = Object.assign({}, item, patch);
    }
    const success = autoTop6RecordSuccess(evaluateItem, actualRecord);
    return {
      rank: item.rank,
      kind: item.kind,
      key: item.key,
      side: item.side || "recommend",
      name: item.name,
      code: item.code,
      shapeKey: item.shapeKey || "",
      heweiScopeIndexes: (item && (item.key === "anyTwoHeweiHit" || item.key === "anyThreeHeweiHit") && heweiScopeIndexes.length >= 2)
        ? heweiScopeIndexes.slice()
        : undefined,
      success,
      actualNumbers: actualRecord && Array.isArray(actualRecord.numbers) ? actualRecord.numbers.slice(0, 5).map(String) : [],
      actualFirstFourDigits: firstFourNumbers(actualRecord),
      actualCompositeHeweiGroup: getCompositeHeweiGroup(actualRecord),
      actualFirstFourSum: String(firstFourSum(actualRecord))
    };
  });
  const valid = items.filter((item) => typeof item.success === "boolean");
  const passed = valid.filter((item) => item.success).length;
  return {
    ok: valid.length === items.length && items.length > 0,
    version: snapshot.version || versionConst,
    basePeriod: prediction && prediction.basePeriod || "",
    success: valid.length === items.length && items.length > 0 ? passed === items.length : null,
    truthEligible: true,
    sealedAt: snapshot.sealedAt,
    sealedBaseUid: snapshot.sealedBaseUid,
    sealHash: snapshot.sealHash,
    heweiScopeIndexes: heweiScopeIndexes.length >= 2 ? heweiScopeIndexes.slice() : undefined,
    passed,
    total: items.length,
    items
  };
}

function evaluateThreeDingTwoSnapshot(prediction, actualRecord) {
  return evaluateScopedThreeDingSnapshot(prediction, actualRecord, "threeDingTwo", threeDingTwoSnapshotIsComplete, THREE_DING_TWO_SNAPSHOT_VERSION, null);
}

function evaluateThreeDingOneSnapshot(prediction, actualRecord) {
  return evaluateScopedThreeDingSnapshot(prediction, actualRecord, "threeDingOne", threeDingOneSnapshotIsComplete, THREE_DING_ONE_SNAPSHOT_VERSION, THREE_DING_ONE_SCOPE_INDEXES);
}



function getStableMathExcludeDigitSet() {
  const slim = (typeof state !== "undefined" && state && state.stableMathFilter)
    || stableMathFilterBridge.slimStableMathFilter(stableMathFilterBridge.readCachedFilter())
    || null;
  if (!slim || typeof slim !== "object") return new Set();
  return new Set(
    (Array.isArray(slim.exclude_digits) ? slim.exclude_digits : [])
      .map((digit) => String(digit))
      .filter((digit) => /^\d$/.test(digit))
  );
}

function heweiPrimaryDigit(obj, preferField) {
  if (!obj || typeof obj !== "object") return "";
  if (preferField && obj[preferField] != null && obj[preferField] !== "") return String(obj[preferField]);
  if (obj.killHewei != null && obj.killHewei !== "") return String(obj.killHewei);
  if (obj.killDigit != null && obj.killDigit !== "") return String(obj.killDigit);
  if (obj.primaryDigit != null && obj.primaryDigit !== "") return String(obj.primaryDigit);
  return "";
}

function pickAlternateHeweiDigit(heweiResult, avoidDigits, preferField) {
  if (!heweiResult || typeof heweiResult !== "object") return null;
  const avoid = avoidDigits instanceof Set ? avoidDigits : new Set((avoidDigits || []).map(String));
  const current = heweiPrimaryDigit(heweiResult, preferField);
  if (current && !avoid.has(current)) return null;
  const detail = Array.isArray(heweiResult.detail) ? heweiResult.detail : [];
  for (let i = 0; i < detail.length; i += 1) {
    const digit = String(detail[i].digit || "");
    if (/^\d$/.test(digit) && !avoid.has(digit)) return digit;
  }
  const backup = String(heweiResult.backupDigit || "");
  if (/^\d$/.test(backup) && !avoid.has(backup)) return backup;
  for (let i = 0; i <= 9; i += 1) {
    const digit = String(i);
    if (!avoid.has(digit)) return digit;
  }
  return current || null;
}

function applyHeweiConfidenceFields(selected, picked, options) {
  const opts = options && typeof options === "object" ? options : {};
  const guardRate = Number(opts.guardRate || HEWEI_GUARD_RATE);
  const recommendable = Number(selected.r30 && selected.r30.rate || 0) >= MODEL_LOW_HIT_RATE
    || Number(selected.r60 && selected.r60.rate || 0) >= MODEL_LOW_HIT_RATE
    || Number(selected.r120 && selected.r120.rate || 0) >= guardRate - 8;
  const confidence = recommendable ? "normal" : "low";
  let reason = picked && picked.reason ? picked.reason : "best-stability-window";
  if (!recommendable && reason === "best-stability-window") reason = "low-confidence-hewei";
  return { recommendable, confidence, reason };
}

function reconcileHeweiKillBundle(bundle) {
  const total = bundle && bundle.totalHeweiKill;
  const unified = bundle && bundle.threeFourKill;
  const independent = bundle && bundle.independentHeweiKills;
  const notes = [];
  if (!total || !independent || typeof independent !== "object") {
    return { notes };
  }

  const wqbs = independent.wanQianBaiShi;
  const totalDigit = heweiPrimaryDigit(total, "killHewei");
  if (wqbs && /^\d$/.test(totalDigit) && heweiPrimaryDigit(wqbs, "killDigit") !== totalDigit) {
    wqbs.killDigit = totalDigit;
    wqbs.primaryDigit = total.primaryDigit || totalDigit;
    wqbs.backupDigit = total.backupDigit || wqbs.backupDigit;
    wqbs.reason = `aligned-with-total-hewei:${String(total.reason || "sync")}`;
    notes.push(`wanQianBaiShi->${totalDigit}`);
    if (typeof state !== "undefined" && state) {
      if (!state.stable120Locks || typeof state.stable120Locks !== "object") state.stable120Locks = {};
      const totalLock = state.stable120Locks["v43:hewei:total"];
      if (totalLock) state.stable120Locks["v43:hewei:wanQianBaiShi"] = Object.assign({}, totalLock);
    }
  }

  const used = new Set();
  const slots = [
    {
      name: "total",
      result: total,
      preferField: "killHewei",
      backupField: "backupDigit",
      getDigit: () => heweiPrimaryDigit(total, "killHewei"),
      setDigit: (digit, backup) => {
        total.killHewei = digit;
        total.primaryDigit = digit;
        if (backup) total.backupDigit = backup;
      }
    },
    {
      name: "unified",
      result: unified,
      preferField: "killDigit",
      backupField: "backupDigit",
      getDigit: () => heweiPrimaryDigit(unified, "killDigit"),
      setDigit: (digit, backup) => {
        if (!unified) return;
        unified.killDigit = digit;
        unified.primaryDigit = digit;
        if (backup) unified.backupDigit = backup;
      }
    },
    {
      name: "wanQianBai",
      result: independent.wanQianBai,
      preferField: "killDigit",
      backupField: "backupDigit",
      getDigit: () => heweiPrimaryDigit(independent.wanQianBai, "killDigit"),
      setDigit: (digit, backup) => {
        independent.wanQianBai.killDigit = digit;
        independent.wanQianBai.primaryDigit = digit;
        if (backup) independent.wanQianBai.backupDigit = backup;
      }
    },
    {
      name: "qianBaiShi",
      result: independent.qianBaiShi,
      preferField: "killDigit",
      backupField: "backupDigit",
      getDigit: () => heweiPrimaryDigit(independent.qianBaiShi, "killDigit"),
      setDigit: (digit, backup) => {
        independent.qianBaiShi.killDigit = digit;
        independent.qianBaiShi.primaryDigit = digit;
        if (backup) independent.qianBaiShi.backupDigit = backup;
      }
    }
  ];

  slots.forEach((slot) => {
    if (!slot.result) return;
    let digit = slot.getDigit();
    if (!/^\d$/.test(digit)) return;
    if (!used.has(digit)) {
      used.add(digit);
      return;
    }
    const alt = pickAlternateHeweiDigit(slot.result, used, slot.preferField);
    if (alt && alt !== digit) {
      slot.setDigit(alt, slot.result[slot.backupField]);
      slot.result.reason = `dedup-shift:${digit}->${alt}`;
      notes.push(`${slot.name}:${digit}->${alt}`);
      digit = alt;
    }
    used.add(digit);
  });

  return { notes };
}

function calculateTotalHeweiKill(history, performance) {
  const safeHistory = Array.isArray(history) ? history.filter((record) => record && Array.isArray(record.numbers) && record.numbers.length >= 4) : [];
  function actual(record) {
    return String(calculateMetrics(record.numbers).totalHewei);
  }
  const candidates = buildStableHeweiCandidates(safeHistory, actual, {
    floor: HEWEI_BLOCK_FLOOR,
    guardRate: HEWEI_GUARD_RATE,
    missPenalty: 2.5,
    getterId: "hewei:total"
  });
  const picked = selectStableHeweiCandidate(candidates, "v43:hewei:total", performance, "hewei.total", {
    floor: HEWEI_BLOCK_FLOOR,
    guardRate: HEWEI_GUARD_RATE,
    lowerFloor: 72,
    performanceFloor: 78
  });
  const selected = picked.selected;
  const backup = picked.backup;
  const confidenceMeta = applyHeweiConfidenceFields(selected, picked, {
    guardRate: HEWEI_GUARD_RATE
  });
  return {
    killHewei: selected.digit,
    primaryDigit: selected.digit,
    backupDigit: backup.digit,
    algorithm: "v43-total-hewei-stability-blocks",
    performance: modelPerformanceSummary(performance, "hewei.total"),
    detail: stableHeweiDetail(candidates),
    stage: selected.stage,
    stability: stableHeweiStability(selected, { floor: HEWEI_BLOCK_FLOOR, guardRate: HEWEI_GUARD_RATE }),
    confidence: confidenceMeta.confidence,
    recommendable: confidenceMeta.recommendable,
    reason: confidenceMeta.reason
  };
}
function calculateThreeFourHeweiKillDigit(history, performance) {
  const safeHistory = Array.isArray(history) ? history.filter(Boolean) : [];
  function group(record) { const g = getCompositeHeweiGroup(record); return Array.isArray(g) ? g.map(String) : []; }
  const candidates = buildStableHeweiCandidates(safeHistory, group, {
    floor: HEWEI_UNIFIED_BLOCK_FLOOR,
    guardRate: HEWEI_UNIFIED_GUARD_RATE,
    missPenalty: 2.5,
    getterId: "hewei:unified"
  });
  const picked = selectStableHeweiCandidate(candidates, "v43:hewei:unified", performance, "hewei.unified", {
    floor: HEWEI_UNIFIED_BLOCK_FLOOR,
    guardRate: HEWEI_UNIFIED_GUARD_RATE,
    lowerFloor: 56,
    performanceFloor: 65
  });
  const selected = picked.selected;
  const backup = picked.backup;
  const confidenceMeta = applyHeweiConfidenceFields(selected, picked, {
    guardRate: HEWEI_UNIFIED_GUARD_RATE
  });
  const reason = !confidenceMeta.recommendable && picked.reason === "best-stability-window"
    ? "low-confidence-unified-hewei"
    : confidenceMeta.reason;
  return {
    killDigit: selected.digit,
    primaryDigit: selected.digit,
    backupDigit: backup.digit,
    algorithm: "v43-unified-hewei-stability-blocks",
    performance: modelPerformanceSummary(performance, "hewei.unified"),
    detail: stableHeweiDetail(candidates),
    currentCompositeHeweiGroup: safeHistory[0] ? group(safeHistory[0]) : [],
    confidence: confidenceMeta.confidence,
    recommendable: confidenceMeta.recommendable,
    stage: selected.stage,
    stability: stableHeweiStability(selected, { floor: HEWEI_UNIFIED_BLOCK_FLOOR, guardRate: HEWEI_UNIFIED_GUARD_RATE }),
    reason
  };
}
function calculateIndependentHeweiKills(history, performance) {
  const safeHistory = Array.isArray(history) ? history.filter(Boolean) : [];
  function group(record) { const g = getCompositeHeweiGroup(record); return Array.isArray(g) ? g.map(String) : []; }
  function calculateOne(index, label, key) {
    const actual = (record) => {
      const g = group(record);
      return !g.length || g[index] === undefined ? "" : String(g[index]);
    };
    const candidates = buildStableHeweiCandidates(safeHistory, actual, {
      floor: HEWEI_BLOCK_FLOOR,
      guardRate: HEWEI_GUARD_RATE,
      missPenalty: 2.5,
      getterId: `hewei:${key}`
    });
    const picked = selectStableHeweiCandidate(candidates, `v43:hewei:${key}`, performance, `hewei.${key}`, {
      floor: HEWEI_BLOCK_FLOOR,
      guardRate: HEWEI_GUARD_RATE,
      lowerFloor: 72,
      performanceFloor: 78
    });
    const selected = picked.selected;
    const backup = picked.backup;
    const confidenceMeta = applyHeweiConfidenceFields(selected, picked, {
      guardRate: HEWEI_GUARD_RATE
    });
    return {
      label,
      key,
      killDigit: selected.digit,
      primaryDigit: selected.digit,
      backupDigit: backup.digit,
      algorithm: `v43-independent-hewei-${key}-stability-blocks`,
      performance: modelPerformanceSummary(performance, `hewei.${key}`),
      detail: stableHeweiDetail(candidates),
      stage: selected.stage,
      stability: stableHeweiStability(selected, { floor: HEWEI_BLOCK_FLOOR, guardRate: HEWEI_GUARD_RATE }),
      confidence: confidenceMeta.confidence,
      recommendable: confidenceMeta.recommendable,
      reason: confidenceMeta.reason
    };
  }
  return {
    wanQianBai: calculateOne(0, "千百十合尾", "wanQianBai"),
    qianBaiShi: calculateOne(1, "百十个合尾", "qianBaiShi"),
    wanQianBaiShi: calculateOne(2, "千百十个合尾", "wanQianBaiShi"),
    wanQianGe: (function () {
      try {
        const mod = require("./algorithms/hewei-qian-bai-ge-stable/formula");
        if (typeof mod.bindRuntime === "function") {
          mod.bindRuntime({
            getStable120Locks: () => (state && state.stable120Locks) || {},
            setStable120Locks: (value) => {
              if (state) state.stable120Locks = value && typeof value === "object" ? value : {};
            },
            getStableMathFilter: () => (state && state.stableMathFilter) || null,
            readCachedStableMathFilter: () => {
              try {
                return stableMathFilterBridge && typeof stableMathFilterBridge.readCachedFilter === "function"
                  ? stableMathFilterBridge.readCachedFilter()
                  : null;
              } catch (_) {
                return null;
              }
            }
          });
        }
        return mod.calculate(safeHistory.slice(0, 300), performance);
      } catch (err) {
        return { label: "千百个合尾(稳定)", key: "wanQianGe", killDigit: "--", reason: String(err && err.message || err) };
      }
    })(),
    wanQianGeFreq: (function () {
      try {
        return require("./algorithms/hewei-qian-bai-ge-freq/formula").calculate(safeHistory.slice(0, 300), performance);
      } catch (err) {
        return { label: "千百个合尾(频次)", key: "wanQianGeFreq", killDigit: "--", reason: String(err && err.message || err) };
      }
    })(),
    wanBaiShi: (function () {
      try {
        const mod = require("./algorithms/hewei-qian-shi-ge-stable/formula");
        if (typeof mod.bindRuntime === "function") {
          mod.bindRuntime({
            getStable120Locks: () => (state && state.stable120Locks) || {},
            setStable120Locks: (value) => {
              if (state) state.stable120Locks = value && typeof value === "object" ? value : {};
            },
            getStableMathFilter: () => (state && state.stableMathFilter) || null,
            readCachedStableMathFilter: () => {
              try {
                return stableMathFilterBridge && typeof stableMathFilterBridge.readCachedFilter === "function"
                  ? stableMathFilterBridge.readCachedFilter()
                  : null;
              } catch (_) {
                return null;
              }
            }
          });
        }
        return mod.calculate(safeHistory.slice(0, 300), performance);
      } catch (err) {
        return { label: "千十个合尾(稳定)", key: "wanBaiShi", killDigit: "--", reason: String(err && err.message || err) };
      }
    })(),
    wanBaiShiFreq: (function () {
      try {
        return require("./algorithms/hewei-qian-shi-ge-freq/formula").calculate(safeHistory.slice(0, 300), performance);
      } catch (err) {
        return { label: "千十个合尾(频次)", key: "wanBaiShiFreq", killDigit: "--", reason: String(err && err.message || err) };
      }
    })()
  };
}
function calculatePositionKill(history, position, performance) {
  const safeHistory = Array.isArray(history)
    ? history
      .filter((record) => record && Array.isArray(record.numbers))
      .slice()
      .sort((a, b) => {
        const aKey = String(a && (a.period || a.uid) || "");
        const bKey = String(b && (b.period || b.uid) || "");
        return bKey.localeCompare(aKey);
      })
    : [];
  const index = Number(position && position.index);
  const posKey = String(position && position.key || "pos");
  const posLabel = String(position && position.label || posKey);

  function actualDigit(record) {
    const nums = record && Array.isArray(record.numbers) ? record.numbers : [];
    return nums[index] === undefined || nums[index] === null ? "" : String(nums[index]);
  }

  function pairRateMultiWindow(pair, records, limits) {
    const lims = [...new Set((Array.isArray(limits) ? limits : [limits]).map((limit) => Math.max(1, Number(limit || 120))))];
    const maxLimit = Math.max(...lims);
    const sliced = (records || []).slice(0, maxLimit);
    const results = {};
    lims.forEach((limit) => {
      results[limit] = { total: 0, hit: 0, rate: 0, missStreak: 0, maxMissStreak: 0, _currentRun: 0, _leading: true };
    });
    for (let i = 0; i < sliced.length; i += 1) {
      const actual = actualDigit(sliced[i]);
      if (!actual) continue;
      const ok = pair.indexOf(actual) < 0;
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
      result.rate = result.total ? result.hit * 100 / result.total : 0;
      delete result._currentRun;
      delete result._leading;
    });
    return results;
  }

  function pairRate(pair, records, limit) {
    const key = Math.max(1, Number(limit || 120));
    const results = pairRateMultiWindow(pair, records, [key]);
    return results[key] || { total: 0, hit: 0, rate: 0, missStreak: 0, maxMissStreak: 0 };
  }

  function pairBlockStats(pair) {
    const rows = safeHistory.slice(0, POSITION_BLOCK_WINDOW);
    const blocks = [];
    for (let start = 0; start < rows.length; start += POSITION_BLOCK_SIZE) {
      const blockRows = rows.slice(start, start + POSITION_BLOCK_SIZE);
      if (!blockRows.length) continue;
      const result = pairRate(pair, blockRows, blockRows.length);
      if (result.total > 0) {
        blocks.push({
          index: blocks.length + 1,
          total: result.total,
          hit: result.hit,
          fail: result.total - result.hit,
          rate: Number(result.rate.toFixed(2))
        });
      }
    }
    if (!blocks.length) {
      return {
        window: POSITION_BLOCK_WINDOW,
        blockSize: POSITION_BLOCK_SIZE,
        blockCount: 0,
        minRate: null,
        avgRate: null,
        maxRate: null,
        variance: null,
        weakBlocks: 0,
        floor: POSITION_BLOCK_FLOOR,
        blocks: []
      };
    }
    const rates = blocks.map((block) => Number(block.rate || 0));
    const avg = rates.reduce((sum, n) => sum + n, 0) / rates.length;
    return {
      window: POSITION_BLOCK_WINDOW,
      blockSize: POSITION_BLOCK_SIZE,
      blockCount: blocks.length,
      minRate: Math.min(...rates),
      avgRate: Number(avg.toFixed(2)),
      maxRate: Math.max(...rates),
      variance: Number(rateVariance(rates).toFixed(2)),
      weakBlocks: blocks.filter((block) => Number(block.rate || 0) < POSITION_BLOCK_FLOOR).length,
      floor: POSITION_BLOCK_FLOOR,
      blocks
    };
  }

  function scorePositionPair(pair, rLong, r300, r120, r60, r30) {
    const weighted = stableWindowScore(r30, r60, r120, r300, rLong, 3.5);
    const block = pairBlockStats(pair);
    const lower120 = wilsonLowerRate(r120.hit, r120.total);
    const lower300 = wilsonLowerRate(r300.hit, r300.total);
    const recentPenalty = Number((r30 && r30.missStreak) || 0) * 1.8;
    const weakPenalty = Number(block.weakBlocks || 0) * 2.4;
    const variancePenalty = Number(block.variance || 0) * 0.08;
    const score = (
      weighted.score * 0.54 +
      lower120 * 0.14 +
      lower300 * 0.10 +
      Number(block.minRate || 0) * 0.12 +
      Number(block.avgRate || 0) * 0.08 -
      recentPenalty -
      weakPenalty -
      variancePenalty
    );
    return {
      score: Number(score.toFixed(4)),
      stage: weighted.stage,
      block,
      lower120: Number(lower120.toFixed(2)),
      lower300: Number(lower300.toFixed(2)),
      repairFlags: {
        recentMiss: Number((r30 && r30.missStreak) || 0),
        weakBlocks: Number(block.weakBlocks || 0),
        blockMinRate: Number(block.minRate || 0),
        lower120: Number(lower120.toFixed(2))
      }
    };
  }

  function uniquePair(a, b) {
    const out = [];
    [a, b].forEach((digit) => {
      digit = String(digit);
      if (digit !== "" && digit !== "--" && out.indexOf(digit) < 0) out.push(digit);
    });
    DIGIT_POOL.forEach((digit) => { if (out.indexOf(digit) < 0) out.push(digit); });
    return out.slice(0, 2);
  }

  const candidates = [];
  const positionRateWindows = [MODEL_LONG_WINDOW, MODEL_STABLE_WINDOW, 120, 60, 30];
  for (let i = 0; i < DIGIT_POOL.length; i += 1) {
    for (let j = i + 1; j < DIGIT_POOL.length; j += 1) {
      const pair = uniquePair(DIGIT_POOL[i], DIGIT_POOL[j]);
      const rateWindows = pairRateMultiWindow(pair, safeHistory, positionRateWindows);
      const rLong = rateWindows[MODEL_LONG_WINDOW];
      const r300 = rateWindows[MODEL_STABLE_WINDOW];
      const r120 = rateWindows[120];
      const r30 = rateWindows[30];
      const r60 = rateWindows[60];
      const weighted = scorePositionPair(pair, rLong, r300, r120, r60, r30);
      candidates.push({ pair, rLong, r300, r120, r60, r30, score: weighted.score, stage: weighted.stage, block: weighted.block, lower120: weighted.lower120, lower300: weighted.lower300, repairFlags: weighted.repairFlags });
    }
  }
  candidates.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
    if ((b.block && b.block.minRate || 0) !== (a.block && a.block.minRate || 0)) return (b.block && b.block.minRate || 0) - (a.block && a.block.minRate || 0);
    if (b.r300.rate !== a.r300.rate) return b.r300.rate - a.r300.rate;
    if (b.rLong.rate !== a.rLong.rate) return b.rLong.rate - a.rLong.rate;
    if (b.r120.rate !== a.r120.rate) return b.r120.rate - a.r120.rate;
    if (b.r60.rate !== a.r60.rate) return b.r60.rate - a.r60.rate;
    return a.pair.join("").localeCompare(b.pair.join(""));
  });

  const emptyBlock = {
    window: POSITION_BLOCK_WINDOW,
    blockSize: POSITION_BLOCK_SIZE,
    blockCount: 0,
    minRate: null,
    avgRate: null,
    maxRate: null,
    variance: null,
    weakBlocks: 0,
    floor: POSITION_BLOCK_FLOOR,
    blocks: []
  };
  const best = candidates[0] || {
    pair: ["--", "--"],
    rLong: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r300: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r120: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r60: { total: 0, hit: 0, rate: null, missStreak: 0 },
    r30: { total: 0, hit: 0, rate: null, missStreak: 0 },
    score: 0,
    stage: stableWindowStage(0),
    block: emptyBlock,
    lower120: null,
    lower300: null,
    repairFlags: {},
    truthAvailable: false
  };
  let selected = best;
  let reason = "best-stability-window";
  const perf = getModelStat(performance, `position.${posKey}.dual`);
  const weak = perf.total >= 8 && Number(perf.rate || 0) < 78;

  if (typeof state !== "undefined" && state) {
    if (!state.stable120Locks || typeof state.stable120Locks !== "object") state.stable120Locks = {};
  }
  const locks = (typeof state !== "undefined" && state && state.stable120Locks) ? state.stable120Locks : {};
  const lockKey = `v42:position:${posKey}`;
  const lock = locks[lockKey] || {};
  const oldPair = Array.isArray(lock.pair) ? lock.pair.map(String).slice(0, 2) : [];

  if (oldPair.length === 2) {
    const oldRateWindows = pairRateMultiWindow(oldPair, safeHistory, positionRateWindows);
    const old = {
      pair: oldPair,
      rLong: oldRateWindows[MODEL_LONG_WINDOW],
      r300: oldRateWindows[MODEL_STABLE_WINDOW],
      r120: oldRateWindows[120],
      r60: oldRateWindows[60],
      r30: oldRateWindows[30]
    };
    const oldWeighted = scorePositionPair(old.pair, old.rLong, old.r300, old.r120, old.r60, old.r30);
    old.score = oldWeighted.score;
    old.stage = oldWeighted.stage;
    old.block = oldWeighted.block;
    old.lower120 = oldWeighted.lower120;
    old.lower300 = oldWeighted.lower300;
    old.repairFlags = oldWeighted.repairFlags;
    const oldHold = Number(lock.hold || 0);
    const oldBlock = old.block || emptyBlock;
    const bestClearlyBetter = best.score > old.score + POSITION_SWITCH_MARGIN;
    const dualPerf = modelPerformanceSummary(performance, `position.${posKey}.dual`);
    const dualLow = !!(dualPerf && dualPerf.low);
    const oldWarning = dualLow ||
      old.r30.missStreak >= 2 ||
      old.r30.rate < MODEL_LOW_HIT_RATE ||
      old.r60.rate < 80 ||
      old.r120.rate < 78 ||
      Number(old.lower120 || 0) < 72 ||
      Number(oldBlock.minRate || 0) < POSITION_BLOCK_FLOOR ||
      Number(oldBlock.weakBlocks || 0) > 0;
    // dual 官方绩效已 low：禁止再强留旧锁对（老化及时换码）；仍受 weak/best 比较约束
    if (!weak && !dualLow && oldHold > 0 && oldHold < POSITION_MIN_HOLD && !oldWarning && best.score <= old.score + POSITION_SWITCH_MARGIN * 1.5) {
      selected = old;
      reason = "keep-min-hold-stable-pair";
    } else if (!weak && !dualLow && !oldWarning && !bestClearlyBetter) {
      selected = old;
      reason = "keep-locked-1000-stable";
    } else if (!weak && !dualLow && old.r120.rate >= best.r120.rate - 2 && old.r30.missStreak < 2 && Number(oldBlock.minRate || 0) >= POSITION_BLOCK_FLOOR && best.score <= old.score + POSITION_SWITCH_MARGIN * 1.25) {
      selected = old;
      reason = "keep-close-stable-pair";
    } else {
      reason = weak ? "weak-position-switch-1000" : (dualLow ? "aging-dual-low-switch" : "warning-switch-stability-block");
    }
  }

  if (selected && selected.r30) {
    const selectedBlock = selected.block || emptyBlock;
    const selectedWarning = selected.r30.rate < MODEL_LOW_HIT_RATE ||
      selected.r60.rate < 80 ||
      selected.r120.rate < 78 ||
      selected.r30.missStreak >= 2 ||
      Number(selected.lower120 || 0) < 72 ||
      Number(selectedBlock.minRate || 0) < POSITION_BLOCK_FLOOR ||
      Number(selectedBlock.weakBlocks || 0) > 0;
    const guarded = candidates
      .filter((item) => item && item.r30 &&
        item.r30.rate >= MODEL_LOW_HIT_RATE &&
        item.r60.rate >= 80 &&
        item.r120.rate >= 78 &&
        item.r30.missStreak < 2 &&
        Number(item.lower120 || 0) >= 72 &&
        item.block &&
        Number(item.block.minRate || 0) >= POSITION_BLOCK_FLOOR &&
        Number(item.block.weakBlocks || 0) === 0)
      .sort((a, b) => {
        if (b.score !== a.score) return b.score - a.score;
        if (b.lower120 !== a.lower120) return b.lower120 - a.lower120;
        if ((b.block && b.block.minRate || 0) !== (a.block && a.block.minRate || 0)) return (b.block && b.block.minRate || 0) - (a.block && a.block.minRate || 0);
        if (b.r300.rate !== a.r300.rate) return b.r300.rate - a.r300.rate;
        if (b.r120.rate !== a.r120.rate) return b.r120.rate - a.r120.rate;
        if (b.r60.rate !== a.r60.rate) return b.r60.rate - a.r60.rate;
        if (b.r30.rate !== a.r30.rate) return b.r30.rate - a.r30.rate;
        if (b.rLong.rate !== a.rLong.rate) return b.rLong.rate - a.rLong.rate;
        return a.pair.join("").localeCompare(b.pair.join(""));
      })[0];
    if (selectedWarning && guarded && guarded.pair.join("") !== selected.pair.join("")) {
      selected = guarded;
      reason = "position-stability-guard";
    }
  }

  const selectedValue = selected.pair.join("");
  const oldValue = oldPair.join("");
  const latestPeriod = safeHistory[0] ? String(safeHistory[0].period || safeHistory[0].uid || "") : "";
  const samePeriod = String(lock.period || "") === latestPeriod;
  const hold = oldValue && oldValue === selectedValue
    ? (samePeriod ? Math.max(1, Number(lock.hold || 0)) : Number(lock.hold || 0) + 1)
    : 1;
  if (locks && lockKey) {
    locks[lockKey] = {
      pair: selected.pair.slice(0, 2),
      hold,
      period: latestPeriod,
      score: selected.score,
      rateLong: Number(selected.rLong.rate.toFixed(2)),
      rate300: Number(selected.r300.rate.toFixed(2)),
      rate120: Number(selected.r120.rate.toFixed(2)),
      rate60: Number(selected.r60.rate.toFixed(2)),
      rate30: Number(selected.r30.rate.toFixed(2)),
      lower120: Number((selected.lower120 || 0).toFixed(2)),
      lower300: Number((selected.lower300 || 0).toFixed(2)),
      blockMinRate: Number(((selected.block && selected.block.minRate) || 0).toFixed(2)),
      blockAvgRate: Number(((selected.block && selected.block.avgRate) || 0).toFixed(2)),
      blockWeakBlocks: Number((selected.block && selected.block.weakBlocks) || 0),
      blockVariance: Number(((selected.block && selected.block.variance) || 0).toFixed(2)),
      missStreak30: selected.r30.missStreak,
      stage: selected.stage,
      reason,
      updatedAt: (new Date()).toISOString()
    };
  }

  return {
    label: `${posLabel}位定位双码排除`,
    key: posKey,
    mainDigit: selected.pair[0] || "--",
    backupDigit: selected.pair[1] || "--",
    primaryDigit: selected.pair[0] || "--",
    originalBackupDigit: selected.pair[1] || "--",
    killDigits: selected.pair.filter((digit) => digit !== "--"),
    algorithm: "v42-position-stability-blocks",
    performance: modelPerformanceSummary(performance, `position.${posKey}.dual`),
    detail: candidates.slice(0, 10).map((item) => ({ pair: item.pair, rateLong: Number(item.rLong.rate.toFixed(2)), rate300: Number(item.r300.rate.toFixed(2)), rate120: Number(item.r120.rate.toFixed(2)), rate60: Number(item.r60.rate.toFixed(2)), rate30: Number(item.r30.rate.toFixed(2)), lower120: Number((item.lower120 || 0).toFixed(2)), lower300: Number((item.lower300 || 0).toFixed(2)), blockMinRate: Number(((item.block && item.block.minRate) || 0).toFixed(2)), blockAvgRate: Number(((item.block && item.block.avgRate) || 0).toFixed(2)), blockWeakBlocks: Number((item.block && item.block.weakBlocks) || 0), blockVariance: Number(((item.block && item.block.variance) || 0).toFixed(2)), missStreak30: item.r30.missStreak, score: item.score, stage: item.stage })),
    stage: selected.stage,
    stability: {
      lower120: Number((selected.lower120 || 0).toFixed(2)),
      lower300: Number((selected.lower300 || 0).toFixed(2)),
      block: selected.block || emptyBlock,
      repairFlags: selected.repairFlags || {},
      minHold: POSITION_MIN_HOLD,
      switchMargin: POSITION_SWITCH_MARGIN
    },
    reason
  };
}
function normalizePositionKillEntry(entry) {
  if (Array.isArray(entry)) {
    return { main: String(entry[0] || "--"), backup: String(entry[1] || "--"), digits: entry.map(String).filter(Boolean) };
  }
  if (entry && typeof entry === "object") {
    const main = String(entry.main || entry.mainDigit || entry.primary || entry.killDigit || (entry.killDigits && entry.killDigits[0]) || "--");
    const backup = String(entry.backup || entry.backupDigit || entry.secondary || (entry.killDigits && entry.killDigits[1]) || "--");
    const digits = Array.isArray(entry.killDigits) ? entry.killDigits.map(String) : [main, backup].filter((digit) => digit !== "--");
    return { main, backup, digits };
  }
  const digit = String(entry || "--");
  return { main: digit, backup: "--", digits: digit === "--" ? [] : [digit] };
}

function hasCompletePredictionModels(prediction) {
  if (!prediction || typeof prediction !== "object") return false;
  const positionKills = prediction.positionKills || {};
  const requiredPositions = ["wan", "qian", "bai", "shi"];
  const hasPositions = requiredPositions.every((key) => {
    const entry = normalizePositionKillEntry(positionKills[key]);
    return Array.isArray(entry.digits) && entry.digits.length > 0 && /^\d$/.test(String(entry.digits[0]));
  });
  const hewei = prediction.independentHeweiKills || {};
  const requiredHewei = ["wanQianBai", "qianBaiShi", "wanQianBaiShi"];
  const hasHewei = requiredHewei.every((key) => {
    const item = hewei[key];
    const value = item && typeof item === "object" ? String(item.killDigit || item.primaryDigit || item.value || "") : String(item || "");
    return /^\d$/.test(value);
  });
  const hasTotalHewei = /^\d$/.test(String(prediction.totalHeweiKill == null ? "" : prediction.totalHeweiKill));
  const hasUnifiedHewei = /^\d$/.test(String(prediction.threeFourHeweiKillDigit == null ? "" : prediction.threeFourHeweiKillDigit));
  const extraKills = prediction.extraKills || (prediction.detail && prediction.detail.extraKills) || {};
  const hasExtras = REQUIRED_EXTRA_KILL_KEYS.every((key) => {
    const item = extraKills[key];
    return extraModelIntegrityReason(key, item) === "";
  });
  const hasSnapshots = autoTop6SnapshotIsComplete(prediction.autoTop6)
    && recommend2SnapshotIsComplete(prediction.recommend2)
    && killFrameworkSnapshotIsComplete(prediction.killFramework)
    && threeDuishuSnapshotIsComplete(prediction.threeDuishu);
  return hasPositions && hasHewei && hasTotalHewei && hasUnifiedHewei && hasExtras && hasSnapshots;
}

function isPredictionCompatible(prediction, latest) {
  // 兼容性看结构完整（可再封存）；tipReady/完美封存由 ensureTipStructurePerfect 硬门闩负责
  return !!(prediction && latest && prediction.baseUid === latest.uid && prediction.modelVersion === MODEL_VERSION && hasCompletePredictionModels(prediction));
}

function ensurePredictionFourPosExcludeSnapshot(prediction, history) {
  if (!prediction || typeof prediction !== "object") return null;
  if (predictionSnapshotsAreImmutable(prediction) && fourPosExcludeAlgo.snapshotIsComplete(prediction.fourPosExclude)) {
    return prediction.fourPosExclude;
  }
  if (fourPosExcludeAlgo.snapshotIsComplete(prediction.fourPosExclude) && prediction.fourPosExclude.sealed === true) {
    return prediction.fourPosExclude;
  }
  const snapshot = fourPosExcludeAlgo.buildSnapshot(history);
  prediction.fourPosExclude = snapshot;
  if (!prediction.detail || typeof prediction.detail !== "object") prediction.detail = {};
  prediction.detail.fourPosExclude = {
    version: snapshot.version,
    itemCount: snapshot.itemCount,
    shapeCount: snapshot.shapeCount,
    items: (snapshot.items || []).map((item) => ({
      key: item.key,
      label: item.label,
      code: item.code,
      backup: item.backup
    })),
    shapeItems: (snapshot.shapeItems || []).map((item) => ({
      key: item.key,
      label: item.label,
      shapeKey: item.shapeKey,
      code: item.code,
      scopeLabel: item.scopeLabel
    }))
  };
  if (!prediction.recommendationMeta || typeof prediction.recommendationMeta !== "object") prediction.recommendationMeta = {};
  prediction.recommendationMeta.fourPosExcludeSnapshotVersion = snapshot.version;
  return snapshot;
}

function evaluateFourPosExcludeSnapshot(prediction, actualRecord) {
  const snapshot = prediction && prediction.fourPosExclude;
  if (!fourPosExcludeAlgo.positionsComplete(snapshot)) {
    return {
      version: FOUR_POS_EXCLUDE_SNAPSHOT_VERSION,
      kind: "fourPosExclude",
      label: fourPosExcludeAlgo.LABEL || "四位各排1码",
      truthEligible: false,
      success: null,
      reason: "snapshot-incomplete"
    };
  }
  return fourPosExcludeAlgo.evaluatePositions(snapshot, actualRecord);
}

function evaluateFourPosShapesSnapshot(prediction, actualRecord) {
  const snapshot = prediction && prediction.fourPosExclude;
  if (!fourPosExcludeAlgo.shapesComplete(snapshot)) {
    return {
      version: FOUR_POS_EXCLUDE_SNAPSHOT_VERSION,
      kind: "fourPosShapes",
      label: "三定形态排除",
      truthEligible: false,
      success: null,
      reason: "snapshot-incomplete"
    };
  }
  return fourPosExcludeAlgo.evaluateShapes(snapshot, actualRecord);
}

function slimFourPosExcludeSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return snapshot || null;
  const out = {
    version: snapshot.version,
    kind: snapshot.kind || "fourPosExclude",
    label: snapshot.label || "四位各排1码",
    itemCount: Number(snapshot.itemCount || (Array.isArray(snapshot.items) ? snapshot.items.length : 0)) || 0,
    shapeCount: Number(snapshot.shapeCount || (Array.isArray(snapshot.shapeItems) ? snapshot.shapeItems.length : 0)) || 0,
    sealed: snapshot.sealed === true,
    sealedAt: snapshot.sealedAt || null,
    sealedBaseUid: snapshot.sealedBaseUid || null,
    note: snapshot.note || null,
    source: snapshot.source && typeof snapshot.source === "object"
      ? {
          officialHistory: snapshot.source.officialHistory || "rds-shared-read-only",
          historyCount: snapshot.source.historyCount,
          builtAt: snapshot.source.builtAt
        }
      : { officialHistory: "rds-shared-read-only" },
    items: Array.isArray(snapshot.items)
      ? snapshot.items.map((item) => ({
          key: item.key,
          label: item.label,
          index: item.index,
          code: item.code,
          backup: item.backup,
          score: item.score,
          stage: item.stage,
          rate30: item.rate30,
          rate60: item.rate60,
          rate120: item.rate120,
          rate300: item.rate300,
          rateLong: item.rateLong,
          lower120: item.lower120,
          sample120: item.sample120,
          sampleLong: item.sampleLong,
          missStreak30: item.missStreak30,
          algorithm: item.algorithm,
          reason: item.reason
        }))
      : [],
    shapeItems: Array.isArray(snapshot.shapeItems)
      ? snapshot.shapeItems.map((item) => ({
          key: item.key,
          rank: item.rank,
          label: item.label,
          shapeKey: item.shapeKey,
          code: item.code,
          scopeIndexes: item.scopeIndexes,
          scopeLabel: item.scopeLabel,
          score: item.score,
          stage: item.stage,
          rate30: item.rate30,
          rate60: item.rate60,
          rate120: item.rate120,
          rate300: item.rate300,
          rateLong: item.rateLong,
          lower120: item.lower120,
          sample120: item.sample120,
          sampleLong: item.sampleLong,
          missStreak30: item.missStreak30,
          algorithm: item.algorithm,
          reason: item.reason
        }))
      : []
  };
  return out;
}

function bindAlgorithmOrchestrators() {
  if (algorithmOrchestratorsBound) return;
  // 门道主链路默认 in-process；避免 .env 里遗留 PROCESS_MODE=1 走未验收的 worker 路径
  if (String(process.env.PENTA5_ORCHESTRATE_FORCE_INPROCESS || "1") !== "0") {
    process.env.PENTA5_ALGO_PROCESS_MODE = "0";
  }
  const host = {
    getState: () => state || {},
    getStable120Locks: () => (state && state.stable120Locks) || {},
    setStable120Locks: (locks) => {
      if (state) state.stable120Locks = locks && typeof locks === "object" ? locks : {};
    },
    getCachedFilter: () => {
      if (state && state.stableMathFilter && typeof state.stableMathFilter === "object") {
        return state.stableMathFilter;
      }
      return stableMathFilterBridge.readCachedFilter();
    },
    setCachedFilter: (filter) => {
      if (!state) return;
      state.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(filter) || filter || null;
    },
    readCachedFilter: () => stableMathFilterBridge.readCachedFilter(),
    getStableMathExcludeDigitSet,
    invalidatePanelStateCache: (options) => {
      try { return invalidatePanelStateCache(options); } catch (_) { return null; }
    },
    // L1 create_snapshot_algorithm 回退：本地 formula 缺失时走 runner ensure*
    ensurePredictionAutoTop6Snapshot,
    ensurePredictionRecommend2Snapshot,
    ensurePredictionKillFrameworkSnapshot,
    ensurePredictionFourDingFrameworkSnapshot,
    ensurePredictionCompactAttackSnapshot,
    ensurePredictionThreeDingTwoSnapshot,
    ensurePredictionThreeDingOneSnapshot,
    ensurePredictionThreeDuishuSnapshot,
    ensurePredictionFourPosExcludeSnapshot
  };
  algoOrchestrateL0.bindRunnerHost(host);
  algoOrchestrateL1.bindRunnerHost(host);
  algorithmOrchestratorsBound = true;
}

function applyOrchestrateL0ToPrediction(prediction, l0) {
  if (!prediction || typeof prediction !== "object" || !l0 || typeof l0 !== "object") return prediction;
  if (!prediction.detail || typeof prediction.detail !== "object") prediction.detail = {};

  if (l0.positionKills && typeof l0.positionKills === "object") {
    prediction.positionKills = l0.positionKills;
    prediction.detail.positionKills = l0.positionKills;
  }
  if (l0.codexStable && typeof l0.codexStable === "object") {
    prediction.codexStable = l0.codexStable;
    prediction.detail.codexStable = l0.codexStable;
  }

  const hewei = l0.hewei && typeof l0.hewei === "object" ? l0.hewei : null;
  if (hewei) {
    // 门道字段 + 现网消费字段双向对齐
    prediction.hewei = hewei;
    if (hewei.totalHeweiKill != null) prediction.totalHeweiKill = hewei.totalHeweiKill;
    if (hewei.totalHeweiBackupDigit != null) prediction.totalHeweiBackupDigit = hewei.totalHeweiBackupDigit;
    if (hewei.threeFourHeweiKillDigit != null) prediction.threeFourHeweiKillDigit = hewei.threeFourHeweiKillDigit;
    if (hewei.threeFourHeweiBackupDigit != null) prediction.threeFourHeweiBackupDigit = hewei.threeFourHeweiBackupDigit;
    if (hewei.independentHeweiKills && typeof hewei.independentHeweiKills === "object") {
      prediction.independentHeweiKills = hewei.independentHeweiKills;
      prediction.detail.independentHeweiKills = hewei.independentHeweiKills;
    }
    const heweiDetail = hewei.detail && typeof hewei.detail === "object" ? hewei.detail : null;
    if (heweiDetail) {
      if (heweiDetail.totalHeweiKill) prediction.detail.totalHeweiKill = heweiDetail.totalHeweiKill;
      if (heweiDetail.threeFourHeweiKill) prediction.detail.threeFourHeweiKill = heweiDetail.threeFourHeweiKill;
      if (heweiDetail.independentHeweiKills) {
        prediction.detail.independentHeweiKills = heweiDetail.independentHeweiKills;
      }
      if (Array.isArray(heweiDetail.heweiReconcileNotes)) {
        prediction.detail.heweiReconcileNotes = heweiDetail.heweiReconcileNotes.slice(0, 20);
      }
    }
  }

  if (l0.stableMathFilter && typeof l0.stableMathFilter === "object") {
    applyStableMathFilterToPrediction(prediction, l0.stableMathFilter);
    if (state) {
      state.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(l0.stableMathFilter)
        || state.stableMathFilter
        || null;
    }
  }
  if (l0.extraKills && typeof l0.extraKills === "object") {
    prediction.extraKills = l0.extraKills;
    prediction.detail.extraKills = l0.extraKills;
  }
  if (l0.fourPosExclude && typeof l0.fourPosExclude === "object") {
    prediction.fourPosExclude = l0.fourPosExclude;
    prediction.detail.fourPosExclude = l0.fourPosExclude;
  }

  prediction.orchestrateMeta = prediction.orchestrateMeta && typeof prediction.orchestrateMeta === "object"
    ? prediction.orchestrateMeta
    : {};
  prediction.orchestrateMeta.l0 = l0.meta || null;
  prediction.orchestrateMeta.l0ProcessMode = !!l0.processMode;
  return prediction;
}

function tipStableMathFilterLagging(prediction) {
  if (!prediction || typeof prediction !== "object") return false;
  const tipBase = prediction.basePeriod != null ? String(prediction.basePeriod) : "";
  const tipSmf = prediction.stableMathFilter && prediction.stableMathFilter.latest_period != null
    ? String(prediction.stableMathFilter.latest_period)
    : "";
  if (!tipBase) return false;
  // 缺更稳或期号落后 tip base = 摆设
  if (!tipSmf) return true;
  return tipSmf !== tipBase;
}

function unsealTipForStableMathRebuild(prediction, reason) {
  if (!prediction || typeof prediction !== "object") return false;
  prediction.snapshotsSealedAt = "";
  prediction.snapshotsSealedBaseUid = "";
  prediction.allSnapshotsSealed = false;
  [
    "autoTop6", "recommend2", "killFramework", "fourDingFramework", "threeDuishu", "fourPosExclude"
  ].forEach((key) => {
    const snap = prediction[key];
    if (snap && typeof snap === "object" && snap.sealed === true) {
      snap.sealed = false;
      snap.sealHash = "";
    }
  });
  addLog("更稳落后 tip，已解封准备重算", {
    tipBase: prediction.basePeriod || null,
    tipSmfPeriod: prediction.stableMathFilter && prediction.stableMathFilter.latest_period || null,
    reason: reason || "smf-lag"
  });
  return true;
}

function ensureStableMathFilterCurrentForTip(history, reason) {
  const safeHistory = Array.isArray(history) ? history : [];
  if (!safeHistory.length) return { ok: false, error: "empty-history" };
  const latestPeriod = safeHistory[0] && safeHistory[0].period != null
    ? String(safeHistory[0].period)
    : "";
  const cached = (state && state.stableMathFilter)
    || stableMathFilterBridge.readCachedFilter()
    || null;
  if (cached && latestPeriod && String(cached.latest_period || "") === latestPeriod) {
    if (state) {
      state.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(cached) || cached;
    }
    return { ok: true, skipped: true, latestPeriod, trigger: reason || "cache-hit" };
  }
  const outcome = stableMathFilterBridge.runStableMathFilterSync(safeHistory, { timeoutMs: 25000 });
  const filter = outcome && outcome.result ? outcome.result : null;
  // 仅在真成功时写入；失败绝不把旧缓存冒充“已刷新”
  if (outcome && outcome.ok && filter && typeof filter === "object") {
    const slim = stableMathFilterBridge.slimStableMathFilter(filter) || filter;
    if (state) state.stableMathFilter = slim;
    addLog("更稳数学排除已同步刷新(封存前)", {
      trigger: reason || "pre-tip-build",
      ok: true,
      latestPeriod: filter.latest_period || null,
      historyLatest: latestPeriod || null
    });
    return {
      ok: true,
      skipped: false,
      latestPeriod: filter.latest_period || null,
      trigger: reason || "pre-tip-build"
    };
  }
  addLog("更稳数学排除同步刷新失败", {
    trigger: reason || "pre-tip-build",
    error: String((outcome && outcome.error) || "empty-result"),
    historyLatest: latestPeriod || null,
    cachePeriod: cached && cached.latest_period || null
  });
  return { ok: false, error: String((outcome && outcome.error) || "empty-result"), latestPeriod };
}

function rebuildTipAfterStableMathCatchup(reason) {
  const prediction = state && state.currentPrediction;
  const history = state && Array.isArray(state.history) ? state.history : [];
  const latest = history[0] || (state && state.latestRecord) || null;
  if (!prediction || !latest || !history.length) return { ok: false, reason: "no-tip" };
  if (prediction.evaluated === true) return { ok: false, reason: "already-evaluated" };
  if (!tipStableMathFilterLagging(prediction)) return { ok: true, skipped: true };
  // 仅当更稳已追上 tip base 才重算，避免用旧缓存再封一次
  const filterPeriod = state.stableMathFilter && state.stableMathFilter.latest_period != null
    ? String(state.stableMathFilter.latest_period)
    : "";
  const tipBase = prediction.basePeriod != null ? String(prediction.basePeriod) : "";
  if (!filterPeriod || !tipBase || filterPeriod !== tipBase) {
    return { ok: false, reason: "filter-not-caught-up", filterPeriod, tipBase };
  }
  unsealTipForStableMathRebuild(prediction, reason || "async-smf-catchup");
  attachCachedStableMathFilter(prediction);
  ensurePredictionFrameworkSnapshots(prediction, history);
  sealPredictionSnapshots(prediction, latest);
  try {
    ensureTipStructurePerfect({
      trigger: reason || "async-smf-catchup",
      log: true,
      fromSync: false,
      allowRegenerate: false
    });
  } catch (_) {}
  try { saveState({ skipMirror: true }); } catch (_) {}
  return {
    ok: true,
    rebuilt: true,
    tipBase,
    tipSmfPeriod: prediction.stableMathFilter && prediction.stableMathFilter.latest_period || null
  };
}

function ensureTipTargetPeriod(prediction) {
  if (!prediction || typeof prediction !== "object") return prediction;
  if (prediction.targetPeriod) return prediction;
  const base = prediction.basePeriod || (Array.isArray(state && state.history) && state.history[0] && state.history[0].period) || "";
  if (!base) return prediction;
  const nextPeriod = expectedNextPenta5Period(base);
  if (nextPeriod && nextPeriod.ok) {
    prediction.targetPeriod = nextPeriod.expectedPeriod;
    prediction.targetPeriodRule = prediction.targetPeriodRule || nextPeriod.rule || "tip-target-repair";
    if (prediction.periodsPerDay == null) prediction.periodsPerDay = PENTA5_PERIODS_PER_DAY;
  }
  return prediction;
}

function ensurePredictionFrameworkSnapshotsViaOrchestrate(prediction, history) {
  bindAlgorithmOrchestrators();
  const safeHistory = Array.isArray(history) ? history : [];
  // 封存前强制更稳与最新开奖期对齐，避免 tip 吃上期滤网
  ensureStableMathFilterCurrentForTip(safeHistory, "orchestrate-pre-l0");
  attachCachedStableMathFilter(prediction);
  ensureTipTargetPeriod(prediction);
  const modelPerformance = (state && state.modelPerformance) || {};
  const l0 = algoOrchestrateL0.computeL0({
    history: safeHistory,
    modelPerformance,
    prediction,
    getState: () => state || {},
    getStableMathExcludeDigitSet
  });
  applyOrchestrateL0ToPrediction(prediction, l0);
  const l1 = algoOrchestrateL1.computeL1({
    history: safeHistory,
    prediction,
    modelPerformance,
    getState: () => state || {},
    recomputeThreeDuishuRecommendation
  });
  prediction.orchestrateMeta = prediction.orchestrateMeta && typeof prediction.orchestrateMeta === "object"
    ? prediction.orchestrateMeta
    : {};
  prediction.orchestrateMeta.l1 = l1 && l1.meta ? l1.meta : null;
  prediction.orchestrateMeta.l1ProcessMode = !!(l1 && l1.processMode);
  prediction.orchestrateMeta.at = isoNow();
  prediction.orchestrateMeta.path = "orchestrate-l0-l1";
  addLog("orchestrate L0/L1 完成", {
    basePeriod: prediction.basePeriod || null,
    baseUid: prediction.baseUid || null,
    hasHewei: !!(prediction.hewei && prediction.independentHeweiKills),
    hasCodex: !!prediction.codexStable,
    hasExtra: !!prediction.extraKills,
    hasFourPos: !!prediction.fourPosExclude,
    l1Keys: l1 && l1.meta ? Object.keys(l1.meta) : []
  });
  return prediction;
}

/** 内联 ensure* 补洞 / orchestrate 失败回退（已完整的快照会自行 no-op） */
function ensurePredictionFrameworkSnapshotsLegacy(prediction, history) {
  if (!prediction || typeof prediction !== "object") return prediction;
  if (predictionSnapshotsAreImmutable(prediction)) return prediction;
  attachCachedStableMathFilter(prediction);
  ensurePredictionFourPosExcludeSnapshot(prediction, history);
  ensurePredictionAutoTop6Snapshot(prediction, history);
  ensurePredictionRecommend2Snapshot(prediction, history);
  ensurePredictionKillFrameworkSnapshot(prediction, history);
  ensurePredictionFourDingFrameworkSnapshot(prediction, history);
  ensurePredictionCompactAttackSnapshot(prediction, history);
  ensurePredictionThreeDingTwoSnapshot(prediction, history);
  ensurePredictionThreeDingOneSnapshot(prediction, history);
  recomputeThreeDuishuRecommendation();
  ensurePredictionThreeDuishuSnapshot(prediction);
  return prediction;
}

function ensurePredictionFrameworkSnapshots(prediction, history) {
  if (!prediction || typeof prediction !== "object") return prediction;
  if (predictionSnapshotsAreImmutable(prediction)) return prediction;
  try {
    ensurePredictionFrameworkSnapshotsViaOrchestrate(prediction, history);
    // 补洞：orchestrate 未写满时用内联 ensure*（完整快照不会被覆盖）
    ensurePredictionFrameworkSnapshotsLegacy(prediction, history);
    return prediction;
  } catch (err) {
    addLog("orchestrate L0/L1 失败，回退内联 ensure*", {
      error: String(err && err.message || err),
      baseUid: prediction.baseUid || null,
      basePeriod: prediction.basePeriod || null
    });
    prediction.orchestrateMeta = {
      path: "legacy-ensure-fallback",
      error: String(err && err.message || err),
      at: isoNow()
    };
    return ensurePredictionFrameworkSnapshotsLegacy(prediction, history);
  }
}

function ensureTipStructurePerfect(options) {
  const opts = options && typeof options === "object" ? options : {};
  const maxAttempts = Math.max(1, Number(opts.maxAttempts || 3));
  const allowRegenerate = opts.allowRegenerate !== false;
  const latest = state && Array.isArray(state.history) && state.history.length
    ? state.history[0]
    : (state && state.latestRecord);
  let attempts = 0;
  let issues = [];
  while (attempts < maxAttempts) {
    attempts += 1;
    let prediction = state && state.currentPrediction;
    if (!prediction || !latest) {
      issues = collectPredictionIntegrityIssues(prediction, latest);
      applyTipReadyState(prediction, latest, issues);
      break;
    }
    const duishuBefore = collectDuishuCaptureIssues(prediction);
    ensurePredictionFrameworkSnapshots(prediction, state.history);
    sealPredictionSnapshots(prediction, latest);
    issues = collectPredictionIntegrityIssues(prediction, latest);
    const duishuRepairIssues = issues.filter((issue) => issue && issue.action === "repair-duishu-capture");
    if (duishuRepairIssues.length) {
      const repairActions = [];
      repairDuishuCapture(repairActions);
      sealPredictionSnapshots(prediction, latest);
      issues = collectPredictionIntegrityIssues(prediction, latest);
    } else if (duishuBefore.length) {
      const remaining = collectDuishuCaptureIssues(prediction);
      state.duishuCaptureAlarm = {
        at: isoNow(),
        baseUid: String(prediction.baseUid || ""),
        basePeriod: String(prediction.basePeriod || ""),
        beforeCount: duishuBefore.length,
        before: duishuBefore.slice(0, 12),
        repairs: [{ target: "ensure-framework-snapshots", ok: remaining.length === 0 }],
        remaining: remaining.slice(0, 12),
        resolved: remaining.length === 0,
        via: "tip-structure-perfect"
      };
      addLog(remaining.length === 0
        ? "【报警】对数抓取缺失，门闩已自动补齐"
        : "【报警】对数抓取缺失，门闩补齐后仍有残留", {
        baseUid: prediction.baseUid,
        basePeriod: prediction.basePeriod,
        before: duishuBefore.slice(0, 12),
        remaining: remaining.slice(0, 12)
      });
    }
    markAlgorithmProjectIssues(issues);
    const coreIssues = issues.filter(isCorePredictionIssue);
    if (!coreIssues.length) {
      applyTipReadyState(prediction, latest, issues);
      if (opts.log !== false) {
        addLog("核心预测门闩通过", {
          attempts,
          baseUid: prediction.baseUid,
          basePeriod: prediction.basePeriod,
          degradedProjectCount: issues.length
        });
      }
      return { ok: true, attempts, issues };
    }
    if (!allowRegenerate || attempts >= maxAttempts) break;
    if (predictionBuildRunning || (syncRunning && opts.fromSync !== true && String(opts.trigger || "").indexOf("sync") < 0)) {
      break;
    }
    const actions = [];
    const regenerated = regenerateCurrentPredictionForWatchdog(actions);
    if (!regenerated) break;
  }
  const prediction = state && state.currentPrediction;
  issues = collectPredictionIntegrityIssues(prediction, latest);
  markAlgorithmProjectIssues(issues);
  applyTipReadyState(prediction, latest, issues);
  const coreIssues = issues.filter(isCorePredictionIssue);
  if (coreIssues.length) {
    addLog("结构完美门闩未通过，已阻断发布", {
      attempts,
      issueCount: coreIssues.length,
      issues: coreIssues.slice(0, 20),
      tipReady: false
    });
  }
  return { ok: coreIssues.length === 0, attempts, issues };
}

function generateBigDataPrediction(history) {
  const safeHistory = Array.isArray(history) && history.length ? history : [DEFAULT_RECORD];
  const latest = safeHistory[0];
  const nextPeriod = expectedNextPenta5Period(latest && latest.period);
  const modelPerformance = calculateModelPerformance(state && Array.isArray(state.predictions) ? state.predictions : []);
  const totalHeweiKill = calculateTotalHeweiKill(safeHistory, modelPerformance);
  const threeFourKill = calculateThreeFourHeweiKillDigit(safeHistory, modelPerformance);
  const independentHeweiKills = calculateIndependentHeweiKills(safeHistory, modelPerformance);
  const heweiReconcile = reconcileHeweiKillBundle({ totalHeweiKill, threeFourKill, independentHeweiKills });
  if (heweiReconcile && Array.isArray(heweiReconcile.notes) && heweiReconcile.notes.length) {
    addLog("合尾排除已协调去重", { notes: heweiReconcile.notes });
  }
  const extraKills = calculateExtraAvoidKills(safeHistory, modelPerformance);
  const positionKills = {};
  POSITION_LIST.slice(0, 4).forEach((position) => {
    const calculated = calculatePositionKill(safeHistory, position, modelPerformance);
    positionKills[position.key] = {
      main: calculated.mainDigit,
      backup: calculated.backupDigit,
      recommendDigit: calculated.mainDigit,
      selectedDigit: calculated.mainDigit,
      mainAlgorithmDigit: calculated.primaryDigit,
      backupAlgorithmDigit: calculated.originalBackupDigit,
      killDigits: calculated.killDigits,
      algorithm: calculated.algorithm,
      performance: calculated.performance,
      primaryDigit: calculated.primaryDigit,
      originalBackupDigit: calculated.originalBackupDigit,
      reason: calculated.reason,
      stage: calculated.stage,
      stability: calculated.stability,
      detail: calculated.detail
    };
  });
  codexStablePosition.applyCodexStablePositionOverlay(positionKills, safeHistory);
  const recommendationMeta = {
    targetCount: TOP_RECOMMEND_COUNT,
    targetRate: MODEL_LOW_HIT_RATE,
    modelRateWindow: MODEL_RATE_WINDOW,
    modelLongWindow: MODEL_LONG_WINDOW,
    modelStableWindow: MODEL_STABLE_WINDOW,
    panelPredictionLimit: PANEL_PREDICTION_LIMIT,
    lowHitRateThreshold: MODEL_LOW_HIT_RATE,
    minSample: MODEL_MIN_SAMPLE,
    independenceMode: "all-models-calculated-before-selection",
    lowConfidencePolicy: "mark-only"
  };
  return {
    id: `prediction:${latest.uid}:${Date.now()}`,
    modelVersion: MODEL_VERSION,
    baseUid: latest.uid,
    basePeriod: latest.period,
    baseOpenTime: latest.opentime,
    targetPeriod: nextPeriod.ok ? nextPeriod.expectedPeriod : "",
    targetPeriodRule: nextPeriod.ok ? nextPeriod.rule : nextPeriod.reason,
    periodsPerDay: PENTA5_PERIODS_PER_DAY,
    generatedAt: isoNow(),
    generatedAtText: nowText(),
    totalHeweiKill: totalHeweiKill.killHewei,
    totalHeweiBackupDigit: totalHeweiKill.backupDigit,
    threeFourHeweiKillDigit: threeFourKill.killDigit,
    threeFourHeweiBackupDigit: threeFourKill.backupDigit,
    independentHeweiKills: independentHeweiKills,
    positionKills,
    fourPosExclude: fourPosExcludeAlgo.buildSnapshot(safeHistory),
    extraKills,
    modelPerformance,
    recommendationMeta,
    evaluated: false,
    counterApplied: false,
    evaluation: null,
    detail: {
      totalHeweiKill,
      threeFourHeweiKill: threeFourKill,
      independentHeweiKills,
      heweiReconcileNotes: Array.isArray(heweiReconcile && heweiReconcile.notes) ? heweiReconcile.notes : [],
      // 禁止把带 codes 的 extraKills 再复制一份进 detail（否则单 tip 双倍常驻）
      extraKills: slimExtraKillsForPersist(extraKills, false),
      positionKills: {},
      modelPerformance,
      recommendationMeta,
      historyCountUsed: safeHistory.length
    }
  };
}
function fillPredictionDetail(prediction, history) {
  const safeHistory = Array.isArray(history) && history.length ? history : [DEFAULT_RECORD];
  if (predictionSnapshotsAreImmutable(prediction)) return prediction;
  if (!prediction.detail) prediction.detail = {};
  if (!fourPosExcludeAlgo.snapshotIsComplete(prediction.fourPosExclude) || prediction.fourPosExclude.sealed !== true) {
    ensurePredictionFourPosExcludeSnapshot(prediction, safeHistory);
  }
  if (prediction.positionKills && typeof prediction.positionKills === "object" && Object.keys(prediction.positionKills).length) {
    prediction.detail.positionKills = prediction.positionKills;
    codexStablePosition.applyCodexStablePositionOverlay(prediction.positionKills, safeHistory);
  } else if (prediction.detail.positionKills && typeof prediction.detail.positionKills === "object" && Object.keys(prediction.detail.positionKills).length) {
    prediction.positionKills = prediction.detail.positionKills;
    codexStablePosition.applyCodexStablePositionOverlay(prediction.positionKills, safeHistory);
  } else {
    const detailPositionKills = {};
    const performance = calculateModelPerformance(state && Array.isArray(state.predictions) ? state.predictions : []);
    POSITION_LIST.slice(0, 4).forEach((position) => {
      detailPositionKills[position.key] = calculatePositionKill(safeHistory, position, performance);
    });
    prediction.positionKills = detailPositionKills;
    prediction.detail.positionKills = detailPositionKills;
    codexStablePosition.applyCodexStablePositionOverlay(prediction.positionKills, safeHistory);
  }
  attachCachedStableMathFilter(prediction);
  return prediction;
}

function evaluatePrediction(prediction, actualRecord) {
  const actualNumbers = actualRecord.numbers.map(String);
  const computedMetrics = calculateMetrics(actualNumbers);
  const actualTotalHewei = String(computedMetrics.totalHewei);
  const actualCompositeHeweiGroup = getCompositeHeweiGroup(actualRecord);

  const predictedIndependentHeweiKills = prediction.independentHeweiKills || {};
  function independentDigit(key) {
    const item = predictedIndependentHeweiKills[key];
    if (item && typeof item === "object") return String(item.killDigit || "");
    return item === undefined ? "" : String(item);
  }

  const previousIndependentHeweiKills = {
    wanQianBai: independentDigit("wanQianBai"),
    qianBaiShi: independentDigit("qianBaiShi"),
    wanQianBaiShi: independentDigit("wanQianBaiShi"),
    wanQianGe: independentDigit("wanQianGe"),
    wanQianGeFreq: independentDigit("wanQianGeFreq"),
    wanBaiShi: independentDigit("wanBaiShi"),
    wanBaiShiFreq: independentDigit("wanBaiShiFreq")
  };

  const actualWanQianGe = String(computedMetrics.wanQianGeHewei);
  const actualWanBaiShi = String(computedMetrics.wanBaiShiHewei);

  function trioHeweiEval(killDigit, actualDigit) {
    return {
      killDigit,
      actualDigit,
      success: /^\d$/.test(killDigit) && /^\d$/.test(actualDigit) ? killDigit !== actualDigit : null
    };
  }

  const independentHewei = {
    wanQianBai: {
      killDigit: previousIndependentHeweiKills.wanQianBai,
      actualDigit: String(actualCompositeHeweiGroup[0]),
      success: /^\d$/.test(previousIndependentHeweiKills.wanQianBai) && /^\d$/.test(String(actualCompositeHeweiGroup[0])) ? previousIndependentHeweiKills.wanQianBai !== String(actualCompositeHeweiGroup[0]) : null
    },
    qianBaiShi: {
      killDigit: previousIndependentHeweiKills.qianBaiShi,
      actualDigit: String(actualCompositeHeweiGroup[1]),
      success: /^\d$/.test(previousIndependentHeweiKills.qianBaiShi) && /^\d$/.test(String(actualCompositeHeweiGroup[1])) ? previousIndependentHeweiKills.qianBaiShi !== String(actualCompositeHeweiGroup[1]) : null
    },
    wanQianBaiShi: {
      killDigit: previousIndependentHeweiKills.wanQianBaiShi,
      actualDigit: String(actualCompositeHeweiGroup[2]),
      success: /^\d$/.test(previousIndependentHeweiKills.wanQianBaiShi) && /^\d$/.test(String(actualCompositeHeweiGroup[2])) ? previousIndependentHeweiKills.wanQianBaiShi !== String(actualCompositeHeweiGroup[2]) : null
    },
    wanQianGe: trioHeweiEval(previousIndependentHeweiKills.wanQianGe, actualWanQianGe),
    wanQianGeFreq: trioHeweiEval(previousIndependentHeweiKills.wanQianGeFreq, actualWanQianGe),
    wanBaiShi: trioHeweiEval(previousIndependentHeweiKills.wanBaiShi, actualWanBaiShi),
    wanBaiShiFreq: trioHeweiEval(previousIndependentHeweiKills.wanBaiShiFreq, actualWanBaiShi)
  };

  const wanKills = killListFromValue(prediction.positionKills && prediction.positionKills.wan);
  const qianKills = killListFromValue(prediction.positionKills && prediction.positionKills.qian);
  const baiKills = killListFromValue(prediction.positionKills && prediction.positionKills.bai);
  const shiKills = killListFromValue(prediction.positionKills && prediction.positionKills.shi);

  function positionEval(label, kills, actual) {
    return {
      label,
      killDigit: kills.join("/"),
      actualDigit: String(actual),
      mainSuccess: kills[0] === undefined ? null : String(actual) !== String(kills[0]),
      backupSuccess: kills[1] === undefined ? null : String(actual) !== String(kills[1]),
      success: kills.length ? !kills.includes(String(actual)) : null
    };
  }

  const independentPosition = {
    wan: positionEval("千位", wanKills, actualRecord.numbers[0]),
    qian: positionEval("百位", qianKills, actualRecord.numbers[1]),
    bai: positionEval("十位", baiKills, actualRecord.numbers[2]),
    shi: positionEval("个位", shiKills, actualRecord.numbers[3])
  };

  const predictedExtras = prediction.extraKills || {};
  function extraValue(key) {
    const item = predictedExtras[key];
    if (item && typeof item === "object") return String(item.value || item.code || item.killValue || "");
    return item === undefined ? "" : String(item);
  }
  const sumAvoidValue = extraValue("sumAvoid");
  const threeDigitAvoidValue = extraValue("threeDigitAvoid");
  const twoDigitAvoidValue = extraValue("twoDigitAvoid");
  const twoDigitAvoid2Value = extraValue("twoDigitAvoid2");
  const twoDigitAvoid3Value = extraValue("twoDigitAvoid3");
  const twoDigitHitValue = extraValue("twoDigitHit");
  const threeDigitHitValue = extraValue("threeDigitHit");
  const threeDigitAvoid2Value = extraValue("threeDigitAvoid2");
  const anyTwoHeweiHitValue = extraValue("anyTwoHeweiHit");
  const anyThreeHeweiHitValue = extraValue("anyThreeHeweiHit");
  const sumRangeAvoidValue = extraValue("sumRangeAvoid");
  const spanAvoidValue = extraValue("spanAvoid");
  const repeatAvoidValue = extraValue("repeatAvoid");
  const consecutiveAvoidValue = extraValue("consecutiveAvoid");
  const parityRatioAvoidValue = extraValue("parityRatioAvoid");
  const sizeRatioAvoidValue = extraValue("sizeRatioAvoid");
  const actualFirstFourSum = firstFourSum(actualRecord);
  const actualFirstFourDigits = firstFourNumbers(actualRecord);
  const independentExtras = {
    sumAvoid: {
      value: sumAvoidValue,
      actualValue: String(actualFirstFourSum),
      success: sumAvoidValue ? String(sumAvoidValue) !== String(actualFirstFourSum) : null
    },
    sumRangeAvoid: {
      value: sumRangeAvoidValue,
      actualValue: String(actualFirstFourSum),
      success: sumRangeAvoidValue ? !firstFourSumInRange(actualRecord, sumRangeAvoidValue) : null
    },
    spanAvoid: {
      value: spanAvoidValue,
      actualValue: String(firstFourSpan(actualRecord)),
      success: spanAvoidValue ? !extraStructureMatched(actualRecord, "spanAvoid", spanAvoidValue) : null
    },
    repeatAvoid: {
      value: repeatAvoidValue,
      actualValue: firstFourRepeatShape(actualRecord),
      success: repeatAvoidValue ? !extraStructureMatched(actualRecord, "repeatAvoid", repeatAvoidValue) : null
    },
    consecutiveAvoid: {
      value: consecutiveAvoidValue,
      actualValue: firstFourConsecutiveShape(actualRecord),
      success: consecutiveAvoidValue ? !extraStructureMatched(actualRecord, "consecutiveAvoid", consecutiveAvoidValue) : null
    },
    parityRatioAvoid: {
      value: parityRatioAvoidValue,
      actualValue: firstFourParityRatio(actualRecord),
      success: parityRatioAvoidValue ? !extraStructureMatched(actualRecord, "parityRatioAvoid", parityRatioAvoidValue) : null
    },
    sizeRatioAvoid: {
      value: sizeRatioAvoidValue,
      actualValue: firstFourSizeRatio(actualRecord),
      success: sizeRatioAvoidValue ? !extraStructureMatched(actualRecord, "sizeRatioAvoid", sizeRatioAvoidValue) : null
    },
    threeDigitAvoid: {
      value: threeDigitAvoidValue,
      actualDigits: actualFirstFourDigits,
      success: threeDigitAvoidValue ? !threeDigitCooccurs(actualRecord, threeDigitAvoidValue) : null
    },
    twoDigitAvoid: {
      value: twoDigitAvoidValue,
      actualDigits: actualFirstFourDigits,
      success: twoDigitAvoidValue ? !threeDigitCooccurs(actualRecord, twoDigitAvoidValue) : null
    },
    twoDigitAvoid2: {
      value: twoDigitAvoid2Value,
      actualDigits: actualFirstFourDigits,
      success: twoDigitAvoid2Value ? !threeDigitCooccurs(actualRecord, twoDigitAvoid2Value) : null
    },
    twoDigitAvoid3: {
      value: twoDigitAvoid3Value,
      actualDigits: actualFirstFourDigits,
      success: twoDigitAvoid3Value ? !threeDigitCooccurs(actualRecord, twoDigitAvoid3Value) : null
    },
    twoDigitHit: {
      value: twoDigitHitValue,
      actualDigits: actualFirstFourDigits,
      success: twoDigitHitValue ? !threeDigitCooccurs(actualRecord, twoDigitHitValue) : null
    },
    threeDigitHit: {
      value: threeDigitHitValue,
      actualDigits: actualFirstFourDigits,
      success: threeDigitHitValue ? !threeDigitCooccurs(actualRecord, threeDigitHitValue) : null
    },
    threeDigitAvoid2: {
      value: threeDigitAvoid2Value,
      actualDigits: actualFirstFourDigits,
      success: threeDigitAvoid2Value ? !threeDigitCooccurs(actualRecord, threeDigitAvoid2Value) : null
    },
    anyTwoHeweiHit: {
      value: anyTwoHeweiHitValue,
      actualTails: heweiCombinationTails(actualRecord, 2),
      success: isAnyTwoHeweiCode(anyTwoHeweiHitValue)
        ? anyHeweiCombinationHit(actualRecord, anyTwoHeweiHitValue, 2, 1)
        : null
    },
    anyThreeHeweiHit: {
      value: anyThreeHeweiHitValue,
      actualTails: heweiCombinationTails(actualRecord, 3),
      success: (/^\d{5}$/.test(anyThreeHeweiHitValue) && new Set(anyThreeHeweiHitValue.split("")).size === ANY_THREE_HEWEI_CODE_SIZE && !isUniformParityDigitSet(anyThreeHeweiHitValue))
        ? anyHeweiCombinationHit(actualRecord, anyThreeHeweiHitValue, 3, 1)
        : null
    }
  };

  const totalHeweiPrediction = String(prediction.totalHeweiKill == null ? "" : prediction.totalHeweiKill);
  const threeFourPrediction = String(prediction.threeFourHeweiKillDigit == null ? "" : prediction.threeFourHeweiKillDigit);
  const independentTotalHeweiSuccess = /^\d$/.test(totalHeweiPrediction) && /^\d$/.test(actualTotalHewei) ? actualTotalHewei !== totalHeweiPrediction : null;
  const independentThreeFourSuccess = /^\d$/.test(threeFourPrediction) && actualCompositeHeweiGroup.length >= 3 ? !actualCompositeHeweiGroup.map(String).includes(threeFourPrediction) : null;
  const allFourPositionSuccess =
    independentPosition.wan.success === true &&
    independentPosition.qian.success === true &&
    independentPosition.bai.success === true &&
    independentPosition.shi.success === true;
  const positionTotalSuccess = allFourPositionSuccess;

  return {
    targetUid: actualRecord.uid,
    targetPeriod: actualRecord.period,
    targetOpenTime: actualRecord.opentime,
    evaluatedAt: isoNow(),
    evaluatedAtText: nowText(),
    actualNumbers,
    actualTotalHewei,
    actualCompositeHeweiGroup,
    actualFirstFourSum: String(actualFirstFourSum),
    actualFirstFourDigits,
    previousTotalHeweiKill: String(prediction.totalHeweiKill),
    previousThreeFourKillDigit: String(prediction.threeFourHeweiKillDigit),
    previousIndependentHeweiKills,
    previousPositionKills: { wan: wanKills, qian: qianKills, bai: baiKills, shi: shiKills },
    previousExtraKills: {
      sumAvoid: sumAvoidValue,
      threeDigitAvoid: threeDigitAvoidValue,
      twoDigitAvoid: twoDigitAvoidValue,
      twoDigitAvoid2: twoDigitAvoid2Value,
      twoDigitAvoid3: twoDigitAvoid3Value,
      twoDigitHit: twoDigitHitValue,
      threeDigitHit: threeDigitHitValue,
      threeDigitAvoid2: threeDigitAvoid2Value,
      anyTwoHeweiHit: anyTwoHeweiHitValue,
      anyThreeHeweiHit: anyThreeHeweiHitValue,
      sumRangeAvoid: sumRangeAvoidValue,
      spanAvoid: spanAvoidValue,
      repeatAvoid: repeatAvoidValue,
      consecutiveAvoid: consecutiveAvoidValue,
      parityRatioAvoid: parityRatioAvoidValue,
      sizeRatioAvoid: sizeRatioAvoidValue
    },
    independentPosition,
    independentTotalHeweiSuccess,
    independentThreeFourSuccess,
    independentHewei,
    independentExtras,
    autoTop6: evaluateAutoTop6Snapshot(prediction, actualRecord),
    recommend2: evaluateRecommend2Snapshot(prediction, actualRecord),
    killFramework: evaluateKillFrameworkSnapshot(prediction, actualRecord),
    fourDingFramework: evaluateFourDingFrameworkSnapshot(prediction, actualRecord),
    compactAttack: evaluateCompactAttackSnapshot(prediction, actualRecord),
    threeDingTwo: evaluateThreeDingTwoSnapshot(prediction, actualRecord),
    threeDingOne: evaluateThreeDingOneSnapshot(prediction, actualRecord),
    threeDuishu: evaluateThreeDuishuSnapshot(prediction, actualRecord),
    fourPosExclude: evaluateFourPosExcludeSnapshot(prediction, actualRecord),
    fourPosShapes: evaluateFourPosShapesSnapshot(prediction, actualRecord),
    allFourPositionSuccess,
    positionTotalSuccess
  };
}
function addCounterResult(counters, key, isMatch, isMainMatch) {
  if (!counters[key] || typeof isMatch !== "boolean") return;
  counters[key].total += 1;
  if (isMatch) counters[key].match += 1;
  if (["wan", "qian", "bai", "shi"].includes(key)) {
    counters[key].mainMatch = Number(counters[key].mainMatch || 0);
    if (isMainMatch) counters[key].mainMatch += 1;
  }
}

function applyEvaluationToCounters(evaluation) {
  const counters = normalizeCounters(state.counters);
  const targetPeriod = cleanPeriodKey(evaluation && (evaluation.targetPeriod || evaluation.targetUid));
  const cohort = counters.cohort && typeof counters.cohort === "object" ? counters.cohort : null;
  if (targetPeriod && cohort) {
    const applied = Array.isArray(cohort.appliedTargetPeriods) ? cohort.appliedTargetPeriods : [];
    if (applied.includes(targetPeriod)) {
      state.counters = counters;
      return counters;
    }
    cohort.appliedTargetPeriods = applied;
    if (!cohort.startPeriod) cohort.startPeriod = targetPeriod;
    cohort.endPeriod = targetPeriod;
    applied.push(targetPeriod);
    cohort.appliedTargetPeriods = applied.slice(-MAX_HISTORY);
  }
  addCounterResult(counters, "positionTotal", evaluation.positionTotalSuccess);
  addCounterResult(counters, "wan", evaluation.independentPosition.wan.success, evaluation.independentPosition.wan.mainSuccess);
  addCounterResult(counters, "qian", evaluation.independentPosition.qian.success, evaluation.independentPosition.qian.mainSuccess);
  addCounterResult(counters, "bai", evaluation.independentPosition.bai.success, evaluation.independentPosition.bai.mainSuccess);
  addCounterResult(counters, "shi", evaluation.independentPosition.shi.success, evaluation.independentPosition.shi.mainSuccess);
  addCounterResult(counters, "totalHewei", evaluation.independentTotalHeweiSuccess);
  addCounterResult(counters, "threeFour", evaluation.independentThreeFourSuccess);
  if (evaluation.independentHewei) {
    addCounterResult(counters, "wanQianBai", evaluation.independentHewei.wanQianBai && evaluation.independentHewei.wanQianBai.success);
    addCounterResult(counters, "qianBaiShi", evaluation.independentHewei.qianBaiShi && evaluation.independentHewei.qianBaiShi.success);
    addCounterResult(counters, "wanQianBaiShi", evaluation.independentHewei.wanQianBaiShi && evaluation.independentHewei.wanQianBaiShi.success);
    addCounterResult(counters, "wanQianGe", evaluation.independentHewei.wanQianGe && evaluation.independentHewei.wanQianGe.success);
    addCounterResult(counters, "wanQianGeFreq", evaluation.independentHewei.wanQianGeFreq && evaluation.independentHewei.wanQianGeFreq.success);
    addCounterResult(counters, "wanBaiShi", evaluation.independentHewei.wanBaiShi && evaluation.independentHewei.wanBaiShi.success);
    addCounterResult(counters, "wanBaiShiFreq", evaluation.independentHewei.wanBaiShiFreq && evaluation.independentHewei.wanBaiShiFreq.success);
  }
  if (evaluation.threeDuishu && evaluation.threeDuishu.truthEligible === true) {
    addCounterResult(counters, "threeDuishu", evaluation.threeDuishu.success);
  }
  if (evaluation.independentExtras) {
    addCounterResult(counters, "sumAvoid", evaluation.independentExtras.sumAvoid && evaluation.independentExtras.sumAvoid.success);
    addCounterResult(counters, "sumRangeAvoid", evaluation.independentExtras.sumRangeAvoid && evaluation.independentExtras.sumRangeAvoid.success);
    addCounterResult(counters, "spanAvoid", evaluation.independentExtras.spanAvoid && evaluation.independentExtras.spanAvoid.success);
    addCounterResult(counters, "repeatAvoid", evaluation.independentExtras.repeatAvoid && evaluation.independentExtras.repeatAvoid.success);
    addCounterResult(counters, "consecutiveAvoid", evaluation.independentExtras.consecutiveAvoid && evaluation.independentExtras.consecutiveAvoid.success);
    addCounterResult(counters, "parityRatioAvoid", evaluation.independentExtras.parityRatioAvoid && evaluation.independentExtras.parityRatioAvoid.success);
    addCounterResult(counters, "sizeRatioAvoid", evaluation.independentExtras.sizeRatioAvoid && evaluation.independentExtras.sizeRatioAvoid.success);
    addCounterResult(counters, "threeDigitAvoid", evaluation.independentExtras.threeDigitAvoid && evaluation.independentExtras.threeDigitAvoid.success);
    addCounterResult(counters, "twoDigitAvoid", evaluation.independentExtras.twoDigitAvoid && evaluation.independentExtras.twoDigitAvoid.success);
    addCounterResult(counters, "twoDigitAvoid2", evaluation.independentExtras.twoDigitAvoid2 && evaluation.independentExtras.twoDigitAvoid2.success);
    addCounterResult(counters, "twoDigitAvoid3", evaluation.independentExtras.twoDigitAvoid3 && evaluation.independentExtras.twoDigitAvoid3.success);
    addCounterResult(counters, "twoDigitHit", evaluation.independentExtras.twoDigitHit && evaluation.independentExtras.twoDigitHit.success);
    addCounterResult(counters, "threeDigitHit", evaluation.independentExtras.threeDigitHit && evaluation.independentExtras.threeDigitHit.success);
    addCounterResult(counters, "threeDigitAvoid2", evaluation.independentExtras.threeDigitAvoid2 && evaluation.independentExtras.threeDigitAvoid2.success);
    addCounterResult(counters, "anyTwoHeweiHit", evaluation.independentExtras.anyTwoHeweiHit && evaluation.independentExtras.anyTwoHeweiHit.success);
    addCounterResult(counters, "anyThreeHeweiHit", evaluation.independentExtras.anyThreeHeweiHit && evaluation.independentExtras.anyThreeHeweiHit.success);
  }
  counters.updatedAt = isoNow();
  if (cohort) cohort.updatedAt = counters.updatedAt;
  state.counters = counters;
  return counters;
}

function rebuildMissingModelCountersFromPredictions() {
  const counters = normalizeCounters(state && state.counters);
  // 新增合尾账本键（千百个/千十个）即使 cohort 未变也允许从历史评价回填，避免永远 0 样本。
  const fillKeys = [
    "wanQianBai", "qianBaiShi", "wanQianBaiShi",
    "wanQianGe", "wanQianGeFreq", "wanBaiShi", "wanBaiShiFreq",
    "threeDuishu"
  ].filter((key) => counters[key] && Number(counters[key].total || 0) === 0);
  if (!fillKeys.length) {
    state.counters = counters;
    return { ok: true, filled: [], scanned: 0 };
  }
  const tallies = {};
  fillKeys.forEach((key) => {
    tallies[key] = { total: 0, match: 0 };
  });
  const predictions = Array.isArray(state && state.predictions) ? state.predictions : [];
  let scanned = 0;
  predictions.forEach((prediction) => {
    if (!prediction || !prediction.evaluated || !prediction.evaluation || prediction.counterSkipped) return;
    const evaluation = prediction.evaluation;
    if (evaluation.truthEligible === false || evaluation.voided === true) return;
    scanned += 1;
    function takeHewei(key) {
      if (!tallies[key]) return;
      const item = evaluation.independentHewei && evaluation.independentHewei[key];
      if (item && typeof item.success === "boolean") {
        tallies[key].total += 1;
        if (item.success) tallies[key].match += 1;
      }
    }
    takeHewei("wanQianBai");
    takeHewei("qianBaiShi");
    takeHewei("wanQianBaiShi");
    takeHewei("wanQianGe");
    takeHewei("wanQianGeFreq");
    takeHewei("wanBaiShi");
    takeHewei("wanBaiShiFreq");
    if (tallies.threeDuishu) {
      const item = evaluation.threeDuishu;
      if (item && item.truthEligible === true && typeof item.success === "boolean") {
        tallies.threeDuishu.total += 1;
        if (item.success) tallies.threeDuishu.match += 1;
      }
    }
  });
  const filled = [];
  fillKeys.forEach((key) => {
    const tally = tallies[key];
    if (!tally || !tally.total) return;
    counters[key].total = tally.total;
    counters[key].match = tally.match;
    filled.push({ key, total: tally.total, match: tally.match, rate: calcRate(tally.total, tally.match) });
  });
  counters.updatedAt = isoNow();
  state.counters = counters;
  return { ok: true, filled, scanned };
}

function isSameRecord(a, b) {
  if (!a || !b) return false;
  if (a.uid && b.uid && a.uid === b.uid) return true;
  if (a.period && b.period && a.period !== "--" && b.period !== "--" && String(a.period) === String(b.period)) return true;
  return false;
}

function dedupeHistory(history) {
  const seen = new Set();
  const output = [];
  (Array.isArray(history) ? history : []).forEach((record) => {
    if (!record || !Array.isArray(record.numbers) || record.numbers.length < 5) return;
    const key = record.uid || record.period || record.numbers.join("");
    if (!seen.has(key)) {
      seen.add(key);
      output.push(record);
    }
  });
  return output.slice(0, MAX_HISTORY);
}

function voidFrameworkEvaluation(prediction, key, reason) {
  const snapshot = prediction && prediction[key] && Array.isArray(prediction[key].items) ? prediction[key] : null;
  return {
    ok: false,
    success: null,
    passed: 0,
    total: snapshot && Array.isArray(snapshot.items) ? snapshot.items.length : 0,
    version: snapshot && snapshot.version || "",
    reason,
    truthEligible: false,
    items: []
  };
}

function buildVoidedPredictionEvaluation(prediction, actualRecord, reason, transition) {
  const actualNumbers = actualRecord && Array.isArray(actualRecord.numbers) ? actualRecord.numbers.slice(0, 5).map(String) : [];
  const nullPosition = (label) => ({ label, killDigit: "", actualDigit: "", mainSuccess: null, backupSuccess: null, success: null });
  const nullHewei = () => ({ killDigit: "", actualDigit: "", success: null });
  return {
    targetUid: actualRecord && actualRecord.uid || "",
    targetPeriod: actualRecord && actualRecord.period || "",
    targetOpenTime: actualRecord && actualRecord.opentime || "",
    evaluatedAt: isoNow(),
    evaluatedAtText: nowText(),
    actualNumbers,
    voided: true,
    voidReason: reason,
    truthEligible: false,
    periodTransition: transition || null,
    independentPosition: {
      wan: nullPosition("万位"),
      qian: nullPosition("千位"),
      bai: nullPosition("百位"),
      shi: nullPosition("十位")
    },
    independentHewei: {
      wanQianBai: nullHewei(),
      qianBaiShi: nullHewei(),
      wanQianBaiShi: nullHewei()
    },
    independentExtras: {},
    independentTotalHeweiSuccess: null,
    independentThreeFourSuccess: null,
    allFourPositionSuccess: false,
    positionTotalSuccess: false,
    autoTop6: voidFrameworkEvaluation(prediction, "autoTop6", reason),
    recommend2: voidFrameworkEvaluation(prediction, "recommend2", reason),
    killFramework: voidFrameworkEvaluation(prediction, "killFramework", reason),
    fourDingFramework: voidFrameworkEvaluation(prediction, "fourDingFramework", reason),
    compactAttack: voidFrameworkEvaluation(prediction, "compactAttack", reason),
    threeDingTwo: voidFrameworkEvaluation(prediction, "threeDingTwo", reason),
    threeDingOne: voidFrameworkEvaluation(prediction, "threeDingOne", reason),
    threeDuishu: voidFrameworkEvaluation(prediction, "threeDuishu", reason)
  };
}

function cleanPeriodKey(value) {
  return String(value || "").replace(/^period:/, "").trim();
}

function findPendingPredictionForBaseRecord(baseRecord) {
  if (!baseRecord) return null;
  const baseUid = String(baseRecord.uid || "");
  const basePeriod = cleanPeriodKey(baseRecord.period || baseRecord.uid || "");
  return (Array.isArray(state.predictions) ? state.predictions : []).find((item) => {
    if (!item || item.evaluated) return false;
    if (baseUid && String(item.baseUid || "") === baseUid) return true;
    return basePeriod && cleanPeriodKey(item.basePeriod || item.baseUid || "") === basePeriod;
  }) || null;
}

function targetPeriodAlreadyCounterApplied(targetPeriod, exceptPrediction) {
  const key = cleanPeriodKey(targetPeriod);
  if (!key) return false;
  return (Array.isArray(state.predictions) ? state.predictions : []).some((item) => {
    if (!item || item === exceptPrediction) return false;
    if (item.counterApplied !== true) return false;
    if (item.counterSkipped === true) return false;
    const ev = item.evaluation || {};
    if (ev.voided === true || ev.truthEligible === false) return false;
    return cleanPeriodKey(ev.targetPeriod || ev.targetUid) === key;
  });
}

function settleOnePredictionAgainstTarget(prediction, targetRecord, reasonTag) {
  if (!prediction || !targetRecord || prediction.evaluated) return { settled: false, appliedCounters: false };
  const transition = immediatePenta5PeriodTransition(prediction.basePeriod || prediction.baseUid, targetRecord.period || targetRecord.uid);
  prediction.evaluated = true;
  const timeEligible = predictionPredatesTarget(prediction, targetRecord);
  if (transition && transition.immediate === true && timeEligible) {
    prediction.evaluation = evaluatePrediction(prediction, targetRecord);
    prediction.evaluation.periodTransition = transition;
    prediction.evaluation.truthEligible = true;
    prediction.evaluation.settleRepairReason = reasonTag || "settle-missed";
    // 补结算一律回填开奖时间，避免墙钟 evaluatedAt 干扰日序/展示
    const isLiveIntegrate = String(reasonTag || "") === "integrate-new-record";
    if (!isLiveIntegrate && targetRecord.opentime) {
      const openMs = Date.parse(String(targetRecord.opentime));
      if (Number.isFinite(openMs)) {
        prediction.evaluation.evaluatedAt = new Date(openMs + 1000).toISOString();
        prediction.evaluation.evaluatedAtText = String(targetRecord.opentime);
      }
    }
    const targetKey = cleanPeriodKey(targetRecord.period || targetRecord.uid);
    if (targetPeriodAlreadyCounterApplied(targetKey, prediction)) {
      prediction.counterApplied = false;
      prediction.counterSkipped = true;
      prediction.counterSkipReason = "duplicate-target-counter";
      return { settled: true, appliedCounters: false, prediction };
    }
    if (!prediction.counterApplied) {
      applyEvaluationToCounters(prediction.evaluation);
      prediction.counterApplied = true;
    }
    prediction.counterSkipped = false;
    prediction.counterSkipReason = "";
    return { settled: true, appliedCounters: true, prediction };
  }
  const reason = !transition || transition.immediate !== true
    ? `void-${transition && transition.reason || "unprovable-period-transition"}`
    : "void-prediction-not-saved-before-target";
  prediction.evaluation = buildVoidedPredictionEvaluation(prediction, targetRecord, reason, transition);
  prediction.counterApplied = false;
  prediction.counterSkipped = true;
  prediction.counterSkipReason = reason;
  return { settled: true, appliedCounters: false, prediction };
}

// 补结算：历史已进内存但预测仍 unevaluated（开机灌注竞态 / exists 跳过）时，按相邻期回填。
function settleMissedPredictionEvaluations(options) {
  const opts = options && typeof options === "object" ? options : {};
  const history = Array.isArray(state.history) ? state.history : [];
  if (!history.length) return { settled: 0, skipped: 0, appliedCounters: 0 };
  const latestKey = cleanPeriodKey(history[0] && (history[0].period || history[0].uid));
  const byPeriod = new Map();
  history.forEach((row) => {
    const key = cleanPeriodKey(row && (row.period || row.uid));
    if (key && !byPeriod.has(key)) byPeriod.set(key, row);
  });
  const pendingAll = (Array.isArray(state.predictions) ? state.predictions : [])
    .filter((prediction) => prediction && prediction.evaluated !== true)
    .filter((prediction) => cleanPeriodKey(prediction.basePeriod || prediction.baseUid) !== latestKey)
    .sort((a, b) => {
      const baseCmp = cleanPeriodKey(a.basePeriod || a.baseUid).localeCompare(cleanPeriodKey(b.basePeriod || b.baseUid));
      if (baseCmp !== 0) return baseCmp;
      return String(b.generatedAt || "").localeCompare(String(a.generatedAt || ""));
    });
  let settled = 0;
  let skipped = 0;
  let appliedCounters = 0;
  // 同基期只结算最新一条，其余标为重复跳过，避免 counter 双计
  const pending = [];
  const seenBase = new Set();
  pendingAll.forEach((prediction) => {
    const baseKey = cleanPeriodKey(prediction.basePeriod || prediction.baseUid);
    if (!baseKey || seenBase.has(baseKey)) {
      const expectedDup = baseKey ? expectedNextPenta5Period(baseKey) : null;
      const targetDup = expectedDup && expectedDup.ok
        ? byPeriod.get(cleanPeriodKey(expectedDup.expectedPeriod))
        : null;
      prediction.evaluated = true;
      prediction.evaluation = buildVoidedPredictionEvaluation(
        prediction,
        targetDup || history[0],
        "void-duplicate-base-prediction",
        null
      );
      prediction.counterApplied = false;
      prediction.counterSkipped = true;
      prediction.counterSkipReason = "void-duplicate-base-prediction";
      settled += 1;
      skipped += 1;
      return;
    }
    seenBase.add(baseKey);
    pending.push(prediction);
  });
  pending.forEach((prediction) => {
    const basePeriod = cleanPeriodKey(prediction.basePeriod || prediction.baseUid);
    const expected = expectedNextPenta5Period(basePeriod);
    if (!expected || !expected.ok || !expected.expectedPeriod) {
      skipped += 1;
      return;
    }
    const target = byPeriod.get(cleanPeriodKey(expected.expectedPeriod));
    if (!target) {
      skipped += 1;
      return;
    }
    const result = settleOnePredictionAgainstTarget(prediction, target, opts.reason || "settle-missed");
    if (result.settled) {
      settled += 1;
      if (result.appliedCounters) appliedCounters += 1;
    } else {
      skipped += 1;
    }
  });
  if (settled > 0) {
    backendDailyStreakRefreshKey = "";
    refreshBackendDailyStreaks();
    addLog("已补结算漏评预测", {
      reason: opts.reason || "settle-missed",
      settled,
      skipped,
      appliedCounters
    });
    if (opts.save === true) {
      try { saveState(); } catch (err) {
        console.error("[settle-missed] save failed", err && err.message ? err.message : err);
      }
    }
  }
  return { settled, skipped, appliedCounters };
}

// RDS 灌注后对齐 tip：latestRecord / currentPrediction 与 history[0] 同步，并 ensure+seal
function alignTipPredictionAfterHydrate(reason) {
  const history = Array.isArray(state.history) ? state.history : [];
  if (!history.length) return { ok: false, reason: "empty-history" };
  const latest = history[0];
  state.latestRecord = latest;
  let currentPrediction = (Array.isArray(state.predictions) ? state.predictions : [])
    .find((item) => item && item.baseUid === latest.uid && item.modelVersion === MODEL_VERSION);
  if (!currentPrediction && state.currentPrediction && state.currentPrediction.baseUid === latest.uid) {
    currentPrediction = state.currentPrediction;
  }
  let created = false;
  // 必须在整包重生前采样：缺对数会使 hasCompletePredictionModels=false，重生后采样会变成空
  const duishuBeforeAlign = collectDuishuCaptureIssues(currentPrediction);
  if (
    isPredictionCompatible(currentPrediction, latest)
    && (state.tipReady === true || (currentPrediction && currentPrediction.tipStructurePerfect === true))
    && !tipStableMathFilterLagging(currentPrediction)
  ) {
    state.currentPrediction = currentPrediction;
    const lightDuishuIssues = collectDuishuCaptureIssues(currentPrediction);
    if (lightDuishuIssues.length) {
      const repairActions = [];
      repairDuishuCapture(repairActions);
      sealPredictionSnapshots(currentPrediction, latest);
      try { saveState({ skipMirror: true }); } catch (saveErr) {
        console.error("[duishu-repair] light-align save failed", saveErr && saveErr.message ? saveErr.message : saveErr);
      }
      addLog("灌注后轻量对齐触发对数自动修复", {
        reason: reason || "hydrate-align-tip",
        baseUid: currentPrediction && currentPrediction.baseUid || "",
        issueCount: lightDuishuIssues.length,
        resolved: !!(state.duishuCaptureAlarm && state.duishuCaptureAlarm.resolved)
      });
      return {
        ok: true,
        created: false,
        tipReady: !!state.tipReady,
        repairedDuishu: true,
        latestPeriod: latest.period || "",
        baseUid: currentPrediction && currentPrediction.baseUid || ""
      };
    }
    // 轻量对齐也要挂上独立新算法快照（不重算其它框架）
    try {
      let attached = false;
      const existing = currentPrediction.fourPosExclude;
      const needUpgrade = !fourPosExcludeAlgo.snapshotIsComplete(existing)
        || String(existing && existing.version || "") !== String(fourPosExcludeAlgo.VERSION || "");
      // 版本升级允许覆盖已封存的本算法快照（其它框架不动）
      if (needUpgrade) {
        const snap = fourPosExcludeAlgo.buildSnapshot(history);
        fourPosExcludeAlgo.sealSnapshot(snap, currentPrediction);
        currentPrediction.fourPosExclude = snap;
        if (!currentPrediction.detail || typeof currentPrediction.detail !== "object") currentPrediction.detail = {};
        currentPrediction.detail.fourPosExclude = {
          version: snap.version,
          itemCount: snap.itemCount,
          shapeCount: snap.shapeCount,
          items: (snap.items || []).map((item) => ({ key: item.key, label: item.label, code: item.code, backup: item.backup })),
          shapeItems: (snap.shapeItems || []).map((item) => ({
            key: item.key, label: item.label, shapeKey: item.shapeKey, code: item.code, scopeLabel: item.scopeLabel
          }))
        };
        if (!currentPrediction.recommendationMeta || typeof currentPrediction.recommendationMeta !== "object") {
          currentPrediction.recommendationMeta = {};
        }
        currentPrediction.recommendationMeta.fourPosExcludeSnapshotVersion = snap.version;
        attached = true;
      } else if (existing && existing.sealed !== true) {
        fourPosExcludeAlgo.sealSnapshot(existing, currentPrediction);
        attached = true;
      }
      if (attached) {
        try { saveState({ skipMirror: true }); } catch (saveErr) {
          console.error("[four-pos-exclude] light-align save failed", saveErr && saveErr.message ? saveErr.message : saveErr);
        }
      }
    } catch (err) {
      console.error("[four-pos-exclude] light-align failed", err && err.message ? err.message : err);
    }
    addLog("灌注后已对齐当前预测 tip", {
      reason: reason || "hydrate-align-tip",
      latestPeriod: latest.period || "",
      baseUid: currentPrediction && currentPrediction.baseUid || "",
      created: false,
      tipReady: true,
      skippedHeavy: true,
      fourPosExclude: !!(currentPrediction && fourPosExcludeAlgo.snapshotIsComplete(currentPrediction.fourPosExclude))
    });
    return {
      ok: true,
      created: false,
      tipReady: true,
      skippedHeavy: true,
      latestPeriod: latest.period || "",
      baseUid: currentPrediction && currentPrediction.baseUid || ""
    };
  }
  if (!isPredictionCompatible(currentPrediction, latest)) {
    currentPrediction = fillPredictionDetail(generateBigDataPrediction(history), history);
    state.predictions = [currentPrediction].concat(Array.isArray(state.predictions) ? state.predictions : []);
    resetAlgorithmProjectHealthForPrediction(currentPrediction);
    created = true;
  }
  // 统一走 L0→L1 orchestrate；禁止先 legacy ensure+seal，否则 tip 门闩会因不可变跳过编排
  ensurePredictionFrameworkSnapshots(currentPrediction, history);
  sealPredictionSnapshots(currentPrediction, latest);
  state.predictions = state.predictions.slice(0, MAX_PREDICTION_LOG);
  state.currentPrediction = currentPrediction;
  const tipGate = ensureTipStructurePerfect({
    trigger: "hydrate-align-tip",
    fromSync: true,
    allowRegenerate: created || !(state.tipReady === true),
    log: true
  });
  if (duishuBeforeAlign.length && !(state.duishuCaptureAlarm && state.duishuCaptureAlarm.at)) {
    const remaining = collectDuishuCaptureIssues(currentPrediction);
    state.duishuCaptureAlarm = {
      at: isoNow(),
      baseUid: String(currentPrediction.baseUid || ""),
      basePeriod: String(currentPrediction.basePeriod || ""),
      beforeCount: duishuBeforeAlign.length,
      before: duishuBeforeAlign.slice(0, 12),
      repairs: [{
        target: created ? "hydrate-align-regenerate" : "hydrate-align-ensure",
        ok: remaining.length === 0
      }],
      remaining: remaining.slice(0, 12),
      resolved: remaining.length === 0,
      via: "hydrate-align-tip"
    };
    addLog(remaining.length === 0
      ? "【报警】对数抓取缺失，灌注对齐已自动补齐"
      : "【报警】对数抓取缺失，灌注对齐后仍有残留", {
      baseUid: currentPrediction.baseUid,
      created,
      before: duishuBeforeAlign.slice(0, 12),
      remaining: remaining.slice(0, 12)
    });
  }
  addLog("灌注后已对齐当前预测 tip", {
    reason: reason || "hydrate-align-tip",
    latestPeriod: latest.period || "",
    baseUid: currentPrediction && currentPrediction.baseUid || "",
    created,
    tipReady: !!(tipGate && tipGate.ok)
  });
  return {
    ok: true,
    created,
    tipReady: !!(tipGate && tipGate.ok),
    latestPeriod: latest.period || "",
    baseUid: currentPrediction && currentPrediction.baseUid || ""
  };
}

function yieldEventLoop() {
  return new Promise((resolve) => setImmediate(resolve));
}

function integrateNewDrawRecordEarly(record) {
  state.history = Array.isArray(state.history) ? state.history : [];
  state.predictions = Array.isArray(state.predictions) ? state.predictions : [];
  state.counters = normalizeCounters(state.counters);

  const exists = state.history.some((item) => isSameRecord(item, record));
  let didAddNewRecord = false;
  let didEvaluate = false;
  let didApplyCounters = false;
  let evaluatedPrediction = null;

  if (!exists) {
    const previousLatest = Array.isArray(state.history) && state.history.length ? state.history[0] : null;
    const transition = previousLatest ? immediatePenta5PeriodTransition(previousLatest.period, record && record.period) : null;
    const pendingPrediction = findPendingPredictionForBaseRecord(previousLatest);
    if (pendingPrediction) {
      const settleResult = settleOnePredictionAgainstTarget(pendingPrediction, record, "integrate-new-record");
      if (settleResult.settled) {
        didEvaluate = true;
        evaluatedPrediction = pendingPrediction;
        if (settleResult.appliedCounters) didApplyCounters = true;
      } else if (transition && transition.immediate === true) {
        pendingPrediction.evaluated = true;
        pendingPrediction.evaluation = evaluatePrediction(pendingPrediction, record);
        pendingPrediction.evaluation.periodTransition = transition;
        pendingPrediction.evaluation.truthEligible = true;
        applyEvaluationToCounters(pendingPrediction.evaluation);
        pendingPrediction.counterApplied = true;
        didApplyCounters = true;
        didEvaluate = true;
        evaluatedPrediction = pendingPrediction;
      }
    }
    state.history.unshift(record);
    state.history = dedupeHistory(state.history);
    didAddNewRecord = true;
  } else {
    state.history = state.history.map((item) => isSameRecord(item, record) ? Object.assign({}, item, { capturedAt: record.capturedAt }) : item);
  }

  const catchup = settleMissedPredictionEvaluations({ reason: exists ? "integrate-existing-catchup" : "integrate-new-catchup", save: false });
  if (catchup.settled > 0) {
    didEvaluate = true;
    if (catchup.appliedCounters > 0) didApplyCounters = true;
  }

  const latest = state.history[0] || record;
  state.latestRecord = latest;
  if (didAddNewRecord) {
    state.sharedHistoryLatestPeriod = String(latest.period || "");
    state.sharedHistoryCount = Array.isArray(state.history) ? state.history.length : 0;
  }

  return {
    didAddNewRecord,
    didEvaluate,
    didApplyCounters,
    evaluatedPrediction,
    latest,
    exists
  };
}

async function finalizeDrawRecordIntegration(record, earlyResult) {
  const early = earlyResult && typeof earlyResult === "object" ? earlyResult : {};
  const latest = state.history[0] || record;
  let currentPrediction = state.predictions.find((item) => item && item.baseUid === latest.uid && item.modelVersion === MODEL_VERSION);
  const tipAlreadyReady = !!(
    currentPrediction
    && isPredictionCompatible(currentPrediction, latest)
    && (state.tipReady === true || currentPrediction.tipStructurePerfect === true)
    && !tipStableMathFilterLagging(currentPrediction)
  );
  // 开奖窗有多个 offset 同步：无新期且 tip 已就绪时跳过整包快照/门闩，避免反复打满事件循环导致面板假死。
  if (!early.didAddNewRecord && !early.didEvaluate && tipAlreadyReady) {
    state.latestRecord = latest;
    state.currentPrediction = currentPrediction;
    return {
      currentPrediction: state.currentPrediction,
      didAddNewRecord: false,
      didEvaluate: false,
      didApplyCounters: !!early.didApplyCounters,
      evaluatedPrediction: early.evaluatedPrediction || null,
      tipReady: true,
      skippedHeavy: true
    };
  }
  if (!isPredictionCompatible(currentPrediction, latest)) {
    currentPrediction = fillPredictionDetail(generateBigDataPrediction(state.history), state.history);
    state.predictions.unshift(currentPrediction);
    resetAlgorithmProjectHealthForPrediction(currentPrediction);
  }
  await yieldEventLoop();

  // 统一走 L0→L1 orchestrate（含四位/三对数）；避免先 seal 导致门闩跳过编排
  ensurePredictionFrameworkSnapshots(currentPrediction, state.history);
  await yieldEventLoop();
  sealPredictionSnapshots(currentPrediction, latest);
  state.predictions = state.predictions.slice(0, MAX_PREDICTION_LOG);
  state.latestRecord = latest;
  state.currentPrediction = currentPrediction;
  await yieldEventLoop();

  const tipGate = ensureTipStructurePerfect({
    trigger: "integrate-record",
    fromSync: true,
    allowRegenerate: !!early.didAddNewRecord || !!early.didEvaluate,
    log: !!early.didAddNewRecord
  });
  return {
    currentPrediction: state.currentPrediction,
    didAddNewRecord: !!early.didAddNewRecord,
    didEvaluate: !!early.didEvaluate,
    didApplyCounters: !!early.didApplyCounters,
    evaluatedPrediction: early.evaluatedPrediction || null,
    tipReady: !!(tipGate && tipGate.ok)
  };
}

async function integrateRecordIntoBigData(record) {
  const earlyResult = integrateNewDrawRecordEarly(record);
  return finalizeDrawRecordIntegration(record, earlyResult);
}

function buildSummary() {
  refreshBackendDailyStreaksIfNeeded();
  const businessDay = currentBusinessDayKey();
  const canRebuildDaily = memoryCanRebuildDailyCounters(state.predictions, businessDay);
  const dailyPositionCounters = canRebuildDaily
    ? summarizeDailyPositionCounters(state.predictions, businessDay)
    : null;
  const modelPerformance = calculateModelPerformance(state.predictions);
  const c = normalizeCounters(state.counters);
  if (dailyPositionCounters) {
    ["positionTotal", "wan", "qian", "bai", "shi"].forEach((key) => {
      if (!dailyPositionCounters[key]) return;
      c[key] = Object.assign({}, c[key], dailyPositionCounters[key], {
        label: (c[key] && c[key].label) || dailyPositionCounters[key].label || key
      });
    });
  } else {
    // 样本被掏空时不要沿用过期 dayScope，避免面板仍显示「今日」却是旧账/空账假 100%
    ["positionTotal", "wan", "qian", "bai", "shi", "threeDuishu", "anyTwoHeweiHit", "anyThreeHeweiHit"].forEach((key) => {
      if (!c[key] || typeof c[key] !== "object") return;
      if (c[key].dayScope && String(c[key].dayScope) !== String(businessDay)) {
        if (String(c[key].truthSource || "").indexOf("daily") >= 0) {
          c[key].truthSource = "official-counter-truth-eligible-only";
        }
        delete c[key].dayScope;
      }
    });
  }
  if (canRebuildDaily) {
    const dailyThreeDuishuCounter = summarizeDailyThreeDuishuCounter(state.predictions, businessDay);
    if (dailyThreeDuishuCounter) {
      c.threeDuishu = Object.assign({}, c.threeDuishu, dailyThreeDuishuCounter, {
        label: (c.threeDuishu && c.threeDuishu.label) || dailyThreeDuishuCounter.label || "三对数排除"
      });
    }
    const dailyHeweiHitCounters = summarizeDailyHeweiHitCounters(state.predictions, businessDay);
    ["anyTwoHeweiHit", "anyThreeHeweiHit"].forEach((key) => {
      if (!dailyHeweiHitCounters[key]) return;
      c[key] = Object.assign({}, c[key], dailyHeweiHitCounters[key], {
        label: (c[key] && c[key].label) || dailyHeweiHitCounters[key].label || key,
        truthSource: "official-counter-truth-eligible-only-daily",
        dayScope: businessDay,
        dayResetHour: PENTA5_DAY_RESET_HOUR
      });
    });
    Object.keys(modelPerformance).forEach((key) => {
      if (!/^position\.(wan|qian|bai|shi)\.(main|backup|dual)$/.test(String(key)) && String(key) !== "threeDuishu.backup") return;
      c[key] = Object.assign({}, modelPerformance[key], {
        label: modelKeyLabel(key),
        dayScope: businessDay,
        dayResetHour: PENTA5_DAY_RESET_HOUR,
        truthSource: "official-counter-truth-eligible-only-daily"
      });
    });
  }
  const memory = process.memoryUsage();
  const heapStats = v8.getHeapStatistics();
  const rates = {
    positionTotal: counterRateText(c.positionTotal, "positionTotal"),
    wan: counterRateText(c.wan, "wan"),
    wanMain: c.wan.total ? `${calcRate(c.wan.total, c.wan.mainMatch)}%` : "暂无命中记录",
    qian: counterRateText(c.qian, "qian"),
    qianMain: c.qian.total ? `${calcRate(c.qian.total, c.qian.mainMatch)}%` : "暂无命中记录",
    bai: counterRateText(c.bai, "bai"),
    baiMain: c.bai.total ? `${calcRate(c.bai.total, c.bai.mainMatch)}%` : "暂无命中记录",
    shi: counterRateText(c.shi, "shi"),
    shiMain: c.shi.total ? `${calcRate(c.shi.total, c.shi.mainMatch)}%` : "暂无命中记录",
    totalHewei: counterRateText(c.totalHewei, "totalHewei"),
    threeFour: counterRateText(c.threeFour, "threeFour"),
    wanQianBai: counterRateText(c.wanQianBai, "wanQianBai"),
    qianBaiShi: counterRateText(c.qianBaiShi, "qianBaiShi"),
    wanQianBaiShi: counterRateText(c.wanQianBaiShi, "wanQianBaiShi"),
    threeDuishu: counterRateText(c.threeDuishu, "threeDuishu"),
    sumAvoid: counterRateText(c.sumAvoid, "sumAvoid"),
    threeDigitAvoid: counterRateText(c.threeDigitAvoid, "threeDigitAvoid"),
    twoDigitAvoid: counterRateText(c.twoDigitAvoid, "twoDigitAvoid"),
    twoDigitAvoid2: counterRateText(c.twoDigitAvoid2, "twoDigitAvoid2"),
    twoDigitAvoid3: counterRateText(c.twoDigitAvoid3, "twoDigitAvoid3"),
    twoDigitHit: counterRateText(c.twoDigitHit, "twoDigitHit"),
    threeDigitHit: counterRateText(c.threeDigitHit, "threeDigitHit"),
    threeDigitAvoid2: counterRateText(c.threeDigitAvoid2, "threeDigitAvoid2"),
    anyTwoHeweiHit: counterRateText(c.anyTwoHeweiHit, "anyTwoHeweiHit"),
    anyThreeHeweiHit: counterRateText(c.anyThreeHeweiHit, "anyThreeHeweiHit")
  };
  REQUIRED_EXTRA_KILL_KEYS.forEach((key) => {
    const counter = c[key];
    rates[key] = counterRateText(counter, key);
  });
  const rateDetails = buildRateDetailsFromCounters(c);
  const highBaselineScoreGuide = Object.keys(HIGH_BASELINE_SCORE_META).map((key) => {
    const meta = HIGH_BASELINE_SCORE_META[key];
    return {
      key,
      scoreClass: meta.scoreClass,
      baseline: meta.baseline,
      hint: meta.hint,
      note: "原始通过率含高基线，禁止解读为强预测力；请看相对基线超额"
    };
  });
  const modelAudit = getModelAuditSummaryCached();
  const stormMode = normalizeStormMode(state.stormMode);
  const tipReady = state.tipReady === true || !!(state.currentPrediction && state.currentPrediction.tipStructurePerfect);
  return {
    ok: !state.lastError,
    version: state.version,
    sourceApiUrl: state.sourceApiUrl,
    port: state.port,
    autoRefreshMs: state.autoRefreshMs,
    updatedAt: state.updatedAt,
    serverStartedAt: state.serverStartedAt,
    lastSync: state.lastSync,
    lastError: state.lastError,
    latestRecord: state.latestRecord,
    currentPrediction: state.currentPrediction,
    tipReady,
    structureReady: modelAudit.structureReady === true,
    truthReady: modelAudit.truthReady === true,
    qualityReady: modelAudit.qualityReady === true,
    operationalReady: modelAudit.operationalReady === true,
    tipReadyCheckedAt: state.tipReadyCheckedAt || "",
    tipReadyIssueCount: Number(state.tipReadyIssueCount || 0),
    tipReadyIssues: Array.isArray(state.tipReadyIssues) ? state.tipReadyIssues.slice(0, 20) : [],
    threeDuishuRecommendation: buildThreeDuishuRecommendation(),
    duishuCaptureAlarm: state.duishuCaptureAlarm && typeof state.duishuCaptureAlarm === "object"
      ? state.duishuCaptureAlarm
      : null,
    counters: c,
    counterCohort: c.cohort || null,
    positionDayScope: businessDay,
    positionDayResetHour: PENTA5_DAY_RESET_HOUR,
    legacyCounterCohorts: Array.isArray(c.legacyCohorts) ? c.legacyCohorts.slice(-5) : [],
    modelWatchdog: normalizeModelWatchdog(state.modelWatchdog),
    stormMode,
    dailyStreakLedger: normalizeDailyStreakLedger(state.dailyStreakLedger),
    dataReadMode: DATA_READ_MODE,
    sharedHistorySource: state.sharedHistorySource || null,
    sharedHistoryLatestPeriod: state.sharedHistoryLatestPeriod || null,
    sharedHistoryRdsLatestPeriod: state.sharedHistoryRdsLatestPeriod || null,
    sharedHistoryCount: state.sharedHistoryCount || null,
    sharedHistorySyncedAt: state.sharedHistorySyncedAt || null,
    rdsReadOnly: !!state.rdsReadOnly,
    rdsLag: state.rdsLag || null,
    pendingOfficialDrawCount: Array.isArray(state.pendingOfficialDraws) ? state.pendingOfficialDraws.length : 0,
    stableMathFilter: stableMathFilterBridge.slimStableMathFilter(
      state.stableMathFilter || stableMathFilterBridge.readCachedFilter()
    ) || null,
    modelAudit,
    rates,
    rateDetails,
    highBaselineScoreGuide,
    historyCount: Array.isArray(state.history) ? state.history.length : 0,
    maxHistory: MAX_HISTORY,
    predictionCount: Array.isArray(state.predictions) ? state.predictions.length : 0,
    syncing: syncRunning,
    predictionBuildRunning,
    memory: {
      rssMb: Number((memory.rss / 1024 / 1024).toFixed(2)),
      heapUsedMb: Number((memory.heapUsed / 1024 / 1024).toFixed(2)),
      heapTotalMb: Number((memory.heapTotal / 1024 / 1024).toFixed(2)),
      heapLimitMb: Number((heapStats.heap_size_limit / 1024 / 1024).toFixed(2))
    },
    stateFile: STATE_FILE
  };
}

function savedSnapshotTruthMetrics(storeName, expectedVersion, expectedTotal, limit) {
  const rawRows = (Array.isArray(state && state.predictions) ? state.predictions : [])
    .filter((prediction) => prediction && prediction.evaluated && prediction.evaluation)
    .map((prediction) => {
      const evaluation = prediction.evaluation || {};
      const snapshot = evaluation[storeName] || {};
      if (typeof snapshot.success !== "boolean") return null;
      if (snapshot.truthEligible !== true) return null;
      if (snapshot.reconstructed === true) return null;
      if (expectedVersion && snapshot.version !== expectedVersion) return null;
      if (Number(expectedTotal || 0) > 0 && Number(snapshot.total || 0) !== Number(expectedTotal)) return null;
      return {
        targetUid: String(evaluation.targetUid || ""),
        period: String(evaluation.targetPeriod || prediction.basePeriod || ""),
        success: snapshot.success,
        passed: Number(snapshot.passed || 0),
        total: Number(snapshot.total || 0)
      };
    })
    .filter(Boolean)
    .sort((a, b) => String(b.period).localeCompare(String(a.period)));
  const seenTargets = new Set();
  const rows = [];
  rawRows.forEach((row) => {
    const key = row.targetUid || row.period;
    if (!key || seenTargets.has(key) || rows.length >= Math.max(1, Number(limit || MODEL_STABLE_WINDOW))) return;
    seenTargets.add(key);
    rows.push(row);
  });
  let hit = 0;
  let nearHit = 0;
  let passedItems = 0;
  let checkedItems = 0;
  rows.forEach((row) => {
    if (row.success) hit += 1;
    if (row.total > 1 && row.passed >= row.total - 1) nearHit += 1;
    if (row.total > 0) {
      passedItems += row.passed;
      checkedItems += row.total;
    }
  });
  const day = cleanDayKey(state && state.latestRecord && (state.latestRecord.opentime || state.latestRecord.period)) || currentLocalDayKey();
  const todayRows = rows
    .filter((row) => cleanDayKey(row.period) === day)
    .slice()
    .sort((a, b) => String(a.period).localeCompare(String(b.period)));
  const todayStats = streakStatsFromResults(day, todayRows.map((row) => ({ period: row.period, success: row.success })));
  return {
    method: "out-of-sample-saved-snapshot",
    version: expectedVersion || "",
    expectedTotal: Number(expectedTotal || 0),
    total: rows.length,
    hit,
    rate: rows.length ? Number(((hit / rows.length) * 100).toFixed(2)) : null,
    nearHit,
    nearRate: rows.length ? Number(((nearHit / rows.length) * 100).toFixed(2)) : null,
    passedItems,
    checkedItems,
    avgPassRate: checkedItems ? Number(((passedItems / checkedItems) * 100).toFixed(2)) : null,
    latestSettledPeriod: rows.length ? rows[0].period : "",
    cohort: Number(expectedTotal || 0) > 0 ? "current-item-count" : "version-only",
    note: "Counts only sealed snapshots whose hash is valid, whose base is the immediately previous draw, and whose seal time predates the target draw. Reconstructed backfills are excluded. Target draws are deduplicated.",
    today: todayStats,
    todayStats
  };
}

function savedSnapshotSimMetrics(storeName, expectedVersion, expectedTotal, limit) {
  const runtime = frameworkSimRuntime;
  const bucket = runtime && runtime.stores && runtime.stores[storeName];
  const fromRuntime = metricsFromFrameworkSimBucket(bucket, expectedVersion, expectedTotal, limit);
  if (fromRuntime && Number(fromRuntime.total || 0) > 0) return fromRuntime;

  const rawRows = (Array.isArray(state && state.predictions) ? state.predictions : [])
    .filter((prediction) => prediction && prediction.evaluated && prediction.evaluation)
    .map((prediction) => {
      const evaluation = prediction.evaluation || {};
      const snapshot = evaluation[storeName] || {};
      if (typeof snapshot.success !== "boolean") return null;
      if (expectedVersion && snapshot.version !== expectedVersion) return null;
      if (Number(expectedTotal || 0) > 0 && Number(snapshot.total || 0) !== Number(expectedTotal)) return null;
      return {
        targetUid: String(evaluation.targetUid || ""),
        period: String(evaluation.targetPeriod || prediction.basePeriod || ""),
        success: snapshot.success,
        passed: Number(snapshot.passed || 0),
        total: Number(snapshot.total || 0),
        reconstructed: snapshot.reconstructed === true
      };
    })
    .filter(Boolean)
    .sort((a, b) => String(b.period).localeCompare(String(a.period)));
  const seenTargets = new Set();
  const rows = [];
  rawRows.forEach((row) => {
    const key = row.targetUid || row.period;
    if (!key || seenTargets.has(key) || rows.length >= Math.max(1, Number(limit || FRAMEWORK_SIM_SAMPLE_TARGET))) return;
    seenTargets.add(key);
    rows.push(row);
  });
  let hit = 0;
  let nearHit = 0;
  let passedItems = 0;
  let checkedItems = 0;
  let reconstructedCount = 0;
  rows.forEach((row) => {
    if (row.success) hit += 1;
    if (row.total > 1 && row.passed >= row.total - 1) nearHit += 1;
    if (row.total > 0) {
      passedItems += row.passed;
      checkedItems += row.total;
    }
    if (row.reconstructed) reconstructedCount += 1;
  });
  return {
    method: "reconstructed-walkforward-reference",
    version: expectedVersion || "",
    expectedTotal: Number(expectedTotal || 0),
    total: rows.length,
    hit,
    rate: rows.length ? Number(((hit / rows.length) * 100).toFixed(2)) : null,
    nearHit,
    nearRate: rows.length ? Number(((nearHit / rows.length) * 100).toFixed(2)) : null,
    passedItems,
    checkedItems,
    avgPassRate: checkedItems ? Number(((passedItems / checkedItems) * 100).toFixed(2)) : null,
    reconstructedCount,
    sealedCount: rows.length - reconstructedCount,
    latestSettledPeriod: rows.length ? rows[0].period : "",
    target: FRAMEWORK_SIM_SAMPLE_TARGET,
    note: "Reference walk-forward from shared RDS history only; computed in memory, not persisted to state.json."
  };
}

function buildModelAuditSummary() {
  const latest = state && state.latestRecord ? state.latestRecord : null;
  const current = state && state.currentPrediction ? state.currentPrediction : null;
  const integrityIssues = collectPredictionIntegrityIssues(current, latest);
  const coreIntegrityIssues = integrityIssues.filter(isCorePredictionIssue);
  const projectIntegrityIssues = integrityIssues.filter((issue) => !isCorePredictionIssue(issue));
  const stabilityIssues = current ? collectCurrentPredictionStabilityIssues(current) : [];
  const projectHealthSummary = summarizeAlgorithmProjectHealth(state && state.algorithmProjectHealth);
  const structureReady = coreIntegrityIssues.length === 0;
  const truthReady = structureReady && projectIntegrityIssues.length === 0;
  const qualityReady = truthReady && projectHealthSummary.degradedProjectCount === 0 && stabilityIssues.length === 0;
  const operationalReady = qualityReady;
  const extraKills = current && (current.extraKills || (current.detail && current.detail.extraKills)) || {};
  const missingExtraKeys = REQUIRED_EXTRA_KILL_KEYS.filter((key) => extraModelIntegrityReason(key, extraKills[key]) !== "");
  const autoTop6Total = current && current.autoTop6 && Array.isArray(current.autoTop6.items)
    ? current.autoTop6.items.filter((item) => item && item.side !== "backup").length
    : 0;
  return {
    ok: operationalReady,
    tipReady: structureReady,
    tipStructurePerfect: structureReady,
    structureReady,
    truthReady,
    qualityReady,
    operationalReady,
    degradedProjectCount: projectHealthSummary.degradedProjectCount,
    degradedProjectIssueCount: projectHealthSummary.degradedIssueCount,
    degradedProjectIds: projectHealthSummary.degradedProjectIds,
    checkedAt: isoNow(),
    modelVersion: current && current.modelVersion || "",
    requiredExtraModelCount: REQUIRED_EXTRA_KILL_KEYS.length,
    validExtraModelCount: REQUIRED_EXTRA_KILL_KEYS.length - missingExtraKeys.length,
    missingExtraKeys,
    integrityIssueCount: integrityIssues.length,
    coreIntegrityIssueCount: coreIntegrityIssues.length,
    projectIntegrityIssueCount: projectIntegrityIssues.length,
    stabilityIssueCount: stabilityIssues.length,
    truthMetrics: {
      autoTop6: savedSnapshotTruthMetrics("autoTop6", AUTO_TOP6_SNAPSHOT_VERSION, autoTop6Total, MODEL_STABLE_WINDOW),
      recommend2: savedSnapshotTruthMetrics("recommend2", RECOMMEND2_SNAPSHOT_VERSION, 8, MODEL_STABLE_WINDOW),
      killFramework: savedSnapshotTruthMetrics("killFramework", KILL_FRAMEWORK_SNAPSHOT_VERSION, KILL_FRAMEWORK_ITEM_COUNT, MODEL_STABLE_WINDOW),
      fourDingFramework: savedSnapshotTruthMetrics("fourDingFramework", FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION, FOUR_DING_FRAMEWORK_ITEM_COUNT, MODEL_STABLE_WINDOW),
      compactAttack: savedSnapshotTruthMetrics("compactAttack", COMPACT_ATTACK_SNAPSHOT_VERSION, COMPACT_ATTACK_ITEM_COUNT, MODEL_STABLE_WINDOW),
      threeDingTwo: savedSnapshotTruthMetrics("threeDingTwo", THREE_DING_TWO_SNAPSHOT_VERSION, THREE_DING_TWO_ITEM_COUNT, MODEL_STABLE_WINDOW),
      threeDingOne: savedSnapshotTruthMetrics("threeDingOne", THREE_DING_ONE_SNAPSHOT_VERSION, THREE_DING_ONE_ITEM_COUNT, MODEL_STABLE_WINDOW)
    },
    simMetrics: {
      compactAttack: savedSnapshotSimMetrics("compactAttack", COMPACT_ATTACK_SNAPSHOT_VERSION, COMPACT_ATTACK_ITEM_COUNT, FRAMEWORK_SIM_SAMPLE_TARGET),
      threeDingTwo: savedSnapshotSimMetrics("threeDingTwo", THREE_DING_TWO_SNAPSHOT_VERSION, THREE_DING_TWO_ITEM_COUNT, FRAMEWORK_SIM_SAMPLE_TARGET),
      threeDingOne: savedSnapshotSimMetrics("threeDingOne", THREE_DING_ONE_SNAPSHOT_VERSION, THREE_DING_ONE_ITEM_COUNT, FRAMEWORK_SIM_SAMPLE_TARGET),
      killFramework: savedSnapshotSimMetrics("killFramework", KILL_FRAMEWORK_SNAPSHOT_VERSION, KILL_FRAMEWORK_ITEM_COUNT, FRAMEWORK_SIM_SAMPLE_TARGET)
    },
    simSampleTarget: FRAMEWORK_SIM_SAMPLE_TARGET,
    integrityIssues: integrityIssues.slice(0, 50),
    coreIntegrityIssues: coreIntegrityIssues.slice(0, 50),
    projectIntegrityIssues: projectIntegrityIssues.slice(0, 50),
    stabilityIssues: stabilityIssues.slice(0, 50)
  };
}

let modelAuditCacheKey = "";
let modelAuditCacheValue = null;

function modelAuditCacheKeyNow() {
  const latest = state && state.latestRecord ? state.latestRecord : {};
  const current = state && state.currentPrediction ? state.currentPrediction : {};
  const evalAt = current && current.evaluation && current.evaluation.evaluatedAt
    ? current.evaluation.evaluatedAt
    : "";
  return [
    latest.period || "",
    current.id || "",
    current.baseUid || "",
    current.evaluated ? "1" : "0",
    evalAt,
    current.snapshotsSealedAt || "",
    current.snapshotsSealedBaseUid || "",
    current.allSnapshotsSealed ? "1" : "0",
    current.tipStructurePerfect ? "1" : "0",
    Array.isArray(state && state.predictions) ? state.predictions.length : 0,
    state && state.tipReady ? "1" : "0",
    state && state.tipReadyIssueCount || 0,
    state && state.algorithmProjectIssueCount || 0,
    state && state.algorithmProjectDegradedCount || 0,
    state && state.algorithmProjectDegradedIssueCount || 0
  ].join("|");
}

function getModelAuditSummaryCached() {
  const key = modelAuditCacheKeyNow();
  if (modelAuditCacheValue && modelAuditCacheKey === key) return modelAuditCacheValue;
  modelAuditCacheKey = key;
  modelAuditCacheValue = buildModelAuditSummary();
  return modelAuditCacheValue;
}

function invalidateModelAuditCache() {
  modelAuditCacheKey = "";
  modelAuditCacheValue = null;
}

function buildHealthSummary(options) {
  const opts = options && typeof options === "object" ? options : {};
  const lite = opts.lite === true;
  const memory = process.memoryUsage();
  const latest = state && state.latestRecord ? state.latestRecord : {};
  const current = state && state.currentPrediction ? state.currentPrediction : {};
  const stormMode = normalizeStormMode(state && state.stormMode);
  const summary = {
    ok: !state.lastError,
    version: state.version,
    modelVersion: MODEL_VERSION,
    latestPeriod: latest.period || latest.uid || "",
    currentPredictionId: current.id || "",
    currentBasePeriod: current.basePeriod || "",
    syncing: syncRunning,
    predictionBuildRunning,
    lastSync: state.lastSync,
    lastError: state.lastError,
    historyCount: Array.isArray(state.history) ? state.history.length : 0,
    maxHistory: MAX_HISTORY,
    predictionCount: Array.isArray(state.predictions) ? state.predictions.length : 0,
    dataReadMode: DATA_READ_MODE,
    sharedHistorySource: state && state.sharedHistorySource ? state.sharedHistorySource : null,
    sharedHistoryLatestPeriod: state && state.sharedHistoryLatestPeriod ? state.sharedHistoryLatestPeriod : null,
    sharedHistoryRdsLatestPeriod: state && state.sharedHistoryRdsLatestPeriod ? state.sharedHistoryRdsLatestPeriod : null,
    rdsReadOnly: !!(state && state.rdsReadOnly),
    rdsLag: state && state.rdsLag ? state.rdsLag : null,
    pendingOfficialDrawCount: Array.isArray(state && state.pendingOfficialDraws) ? state.pendingOfficialDraws.length : 0,
    stormMode,
    tipReady: state && state.tipReady === true,
    structureReady: state && state.structureReady === true,
    truthReady: state && state.truthReady === true,
    qualityReady: state && state.qualityReady === true,
    operationalReady: state && state.operationalReady === true,
    tipReadyIssueCount: Number(state && state.tipReadyIssueCount || 0),
    algorithmProjectIssueCount: Number(state && state.algorithmProjectIssueCount || 0),
    algorithmProjectDegradedCount: Number(state && state.algorithmProjectDegradedCount || 0),
    algorithmProjectDegradedIssueCount: Number(state && state.algorithmProjectDegradedIssueCount || 0),
    algorithmProjectDegradedIds: Array.isArray(state && state.algorithmProjectDegradedIds)
      ? state.algorithmProjectDegradedIds.slice(0, 32)
      : [],
    algorithmProjects: Object.fromEntries(
      Object.entries(normalizeAlgorithmProjectHealth(state && state.algorithmProjectHealth)).map(([projectId, value]) => [projectId, {
        status: value.status,
        issueCount: value.issueCount,
        basePeriod: value.basePeriod,
        updatedAt: value.updatedAt
      }])
    ),
    tipStructurePerfect: !!(current && current.tipStructurePerfect),
    allSnapshotsSealed: !!(current && current.allSnapshotsSealed),
    rssMb: Number((memory.rss / 1024 / 1024).toFixed(2)),
    heapUsedMb: Number((memory.heapUsed / 1024 / 1024).toFixed(2)),
    frameworkBackfillEnabled: FRAMEWORK_BACKFILL_ENABLED,
    frameworkSimEnabled: FRAMEWORK_SIM_ENABLED
  };
  // 默认可选审计；lite 供 /health 与 sync 返回，避免 10×predictions 扫描堵死探针
  if (!lite) summary.modelAudit = getModelAuditSummaryCached();
  return summary;
}

let panelStateCacheKey = "";
let panelStateCacheText = "";
let panelStateCacheGzip = null;
let panelStateCacheCreatedAt = 0;
let panelHtmlCacheMtimeMs = 0;
let panelHtmlCacheText = "";
let panelHtmlCacheGzip = null;

function invalidatePanelStateCache(options) {
  const opts = options && typeof options === "object" ? options : {};
  panelStateCacheKey = "";
  panelStateCacheText = "";
  panelStateCacheGzip = null;
  panelStateCacheCreatedAt = 0;
  if (opts.invalidateModelAudit !== false) invalidateModelAuditCache();
}

function panelHtmlFilePath() {
  return path.join(__dirname, "panel.html");
}

function getPanelHtmlText() {
  const panelFile = panelHtmlFilePath();
  const stat = fs.statSync(panelFile);
  if (!panelHtmlCacheText || panelHtmlCacheMtimeMs !== stat.mtimeMs) {
    panelHtmlCacheMtimeMs = stat.mtimeMs;
    panelHtmlCacheText = fs.readFileSync(panelFile, "utf8");
    panelHtmlCacheGzip = null;
  }
  return panelHtmlCacheText;
}

function slimPerformance(performance) {
  if (!performance || typeof performance !== "object") return performance || null;
  const out = {};
  [
    "key",
    "total",
    "match",
    "rate",
    "lastSuccess",
    "low",
    "threshold",
    "minSample",
    "scoreClass",
    "randomBaseline",
    "edgeOverBaseline",
    "predictivePower",
    "scoreHint",
    "truthDisplay"
  ].forEach((key) => {
    if (performance[key] !== undefined) out[key] = performance[key];
  });
  decorateTruthScoreFields(out, performance.key || out.key, out.rate);
  return out;
}

function slimPositionKill(entry, includeMeta) {
  if (!entry || typeof entry !== "object") return entry || null;
  const out = {};
  [
    "main",
    "backup",
    "recommendDigit",
    "selectedDigit",
    "mainAlgorithmDigit",
    "backupAlgorithmDigit",
    "primaryDigit",
    "originalBackupDigit",
    "mainDigit",
    "backupDigit"
  ].forEach((key) => {
    if (entry[key] !== undefined) out[key] = entry[key];
  });
  if (Array.isArray(entry.killDigits)) out.killDigits = entry.killDigits.slice(0, 2);
  [
    "codexStableDigit",
    "codexStableRate",
    "codexStableTotal",
    "codexStableHit",
    "codexStableRule",
    "codexStableRuleLabel",
    "codexStableRecommended",
    "codexStableStrict",
    "stabilityBlocksMain",
    "stabilityBlocksBackup"
  ].forEach((key) => {
    if (entry[key] !== undefined) out[key] = entry[key];
  });
  if (includeMeta && entry.algorithm !== undefined) out.algorithm = entry.algorithm;
  if (includeMeta && entry.reason !== undefined) out.reason = entry.reason;
  if (includeMeta && entry.stage !== undefined) out.stage = entry.stage;
  if (includeMeta && entry.stability !== undefined) out.stability = entry.stability;
  if (includeMeta && entry.performance) out.performance = slimPerformance(entry.performance);
  return out;
}

function slimPositionKills(positionKills, includeMeta) {
  if (!positionKills || typeof positionKills !== "object") return {};
  const out = {};
  POSITION_LIST.forEach((pos) => {
    if (positionKills[pos.key]) out[pos.key] = slimPositionKill(positionKills[pos.key], includeMeta);
  });
  return out;
}

function slimHeweiKill(entry, includeMeta) {
  if (!entry || typeof entry !== "object") return entry || null;
  const out = {};
  ["label", "key", "killDigit", "primaryDigit", "backupDigit", "confidence", "recommendable"].forEach((key) => {
    if (entry[key] !== undefined) out[key] = entry[key];
  });
  if (includeMeta && entry.algorithm !== undefined) out.algorithm = entry.algorithm;
  if (includeMeta && entry.reason !== undefined) out.reason = entry.reason;
  if (includeMeta && entry.stage !== undefined) out.stage = entry.stage;
  if (includeMeta && entry.stability !== undefined) out.stability = entry.stability;
  if (includeMeta && entry.performance) out.performance = slimPerformance(entry.performance);
  return out;
}

function slimHeweiKills(independentHeweiKills, includeMeta) {
  if (!independentHeweiKills || typeof independentHeweiKills !== "object") return {};
  const out = {};
  ["wanQianBai", "qianBaiShi", "wanQianBaiShi", "wanQianGe", "wanQianGeFreq", "wanBaiShi", "wanBaiShiFreq"].forEach((key) => {
    if (independentHeweiKills[key]) out[key] = slimHeweiKill(independentHeweiKills[key], includeMeta);
  });
  return out;
}

function slimExtraKill(entry) {
  if (!entry || typeof entry !== "object") return entry || null;
  const out = {};
  [
    "key",
    "label",
    "value",
    "code",
    "primary",
    "backup",
    "algorithm",
    "hitMode",
    "matchMode",
    "jointSwitchAllowed",
    "dynamicEveryPrediction",
    "hardnessScore",
    "rate30",
    "rate60",
    "rate120",
    "rate300",
    "maxMiss120",
    "maxMissLong",
    "lower120",
    "stage",
    "scoreClass",
    "randomBaseline",
    "scoreHint",
    "predictivePower"
  ].forEach((key) => {
    if (entry[key] !== undefined) out[key] = entry[key];
  });
  if (entry.confidence !== undefined) out.confidence = entry.confidence;
  if (entry.recommendable !== undefined) out.recommendable = entry.recommendable;
  if (entry.preferOverlap !== undefined) out.preferOverlap = entry.preferOverlap;
  if (entry.performance) out.performance = slimPerformance(entry.performance);
  // 高基线元数据：即使旧 tip 缺字段，也按 key 补齐，避免面板把高通过率当成强预测
  const baselineMeta = highBaselineScoreMeta(entry.key || out.key);
  if (baselineMeta) {
    out.scoreClass = out.scoreClass || baselineMeta.scoreClass;
    out.randomBaseline = out.randomBaseline != null ? out.randomBaseline : baselineMeta.baseline;
    out.scoreHint = out.scoreHint || `${baselineMeta.hint}，非预测力`;
    out.predictivePower = false;
  }
  return out;
}

function slimExtraKills(extraKills) {
  if (!extraKills || typeof extraKills !== "object") return {};
  const out = {};
  ["sumAvoid", "sumRangeAvoid", "spanAvoid", "repeatAvoid", "consecutiveAvoid", "parityRatioAvoid", "sizeRatioAvoid", "threeDigitAvoid", "twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3", "twoDigitHit", "threeDigitHit", "threeDigitAvoid2", "anyTwoHeweiHit", "anyThreeHeweiHit"].forEach((key) => {
    if (extraKills[key]) out[key] = slimExtraKill(Object.assign({ key }, extraKills[key]));
  });
  return out;
}

function slimThreeFourDetail(detail, includeMeta) {
  const out = {};
  const total = detail && detail.totalHeweiKill;
  if (total && typeof total === "object") {
    const totalOut = {};
    ["killHewei", "primaryDigit", "backupDigit", "confidence", "recommendable", "stage"].forEach((key) => {
      if (total[key] !== undefined) totalOut[key] = total[key];
    });
    if (includeMeta && total.algorithm !== undefined) totalOut.algorithm = total.algorithm;
    if (includeMeta && total.reason !== undefined) totalOut.reason = total.reason;
    if (includeMeta && total.stability !== undefined) totalOut.stability = total.stability;
    if (includeMeta && total.performance) totalOut.performance = slimPerformance(total.performance);
    out.totalHeweiKill = totalOut;
  }

  const src = detail && detail.threeFourHeweiKill;
  if (src && typeof src === "object") {
    const threeFourOut = {};
    ["killDigit", "primaryDigit", "backupDigit", "confidence", "recommendable", "stage"].forEach((key) => {
      if (src[key] !== undefined) threeFourOut[key] = src[key];
    });
    if (includeMeta && src.algorithm !== undefined) threeFourOut.algorithm = src.algorithm;
    if (includeMeta && src.reason !== undefined) threeFourOut.reason = src.reason;
    if (includeMeta && src.stability !== undefined) threeFourOut.stability = src.stability;
    if (includeMeta && src.performance) threeFourOut.performance = slimPerformance(src.performance);
    out.threeFourHeweiKill = threeFourOut;
  }

  if (detail && detail.independentHeweiKills && typeof detail.independentHeweiKills === "object") {
    out.independentHeweiKills = slimHeweiKills(detail.independentHeweiKills, !!includeMeta);
  }
  if (Array.isArray(detail && detail.heweiReconcileNotes)) {
    out.heweiReconcileNotes = detail.heweiReconcileNotes.slice(0, 20);
  }
  if (detail && detail.stableMathFilter) {
    out.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(detail.stableMathFilter);
  }
  if (includeMeta && detail && detail.hewei && typeof detail.hewei === "object") {
    out.hewei = detail.hewei;
  }
  if (includeMeta && detail && detail.codexStable && typeof detail.codexStable === "object") {
    out.codexStable = detail.codexStable;
  }
  if (includeMeta && detail && detail.positionKills && typeof detail.positionKills === "object") {
    out.positionKills = slimPositionKills(detail.positionKills, true);
  }
  if (includeMeta && detail && detail.extraKills && typeof detail.extraKills === "object") {
    out.extraKills = slimExtraKillsForPersist(detail.extraKills, true);
  }
  if (includeMeta && detail && detail.fourPosExclude && typeof detail.fourPosExclude === "object") {
    out.fourPosExclude = detail.fourPosExclude;
  }
  return out;
}

function slimEvaluation(evaluation) {
  if (!evaluation || typeof evaluation !== "object") return evaluation || null;
  const out = {};
  [
    "targetUid",
    "targetPeriod",
    "targetOpenTime",
    "evaluatedAt",
    "evaluatedAtText",
    "period",
    "uid",
    "actualNumbers",
    "actualTotalHewei",
    "actualCompositeHeweiGroup",
    "actualFirstFourSum",
    "actualFirstFourDigits",
    "previousTotalHeweiKill",
    "previousThreeFourKillDigit",
    "previousIndependentHeweiKills",
    "previousPositionKills",
    "previousExtraKills",
    "independentPosition",
    "independentHewei",
    "independentExtras",
    "autoTop6",
    "recommend2",
    "killFramework",
    // 四定框架结算必须保留；漏掉会被 /state 与 tip-only 落盘抹掉，真实回测永远 total=0
    "fourDingFramework",
    "threeDuishu",
    "fourPosExclude",
    "fourPosShapes",
    "independentTotalHeweiSuccess",
    "independentThreeFourSuccess",
    "allFourPositionSuccess",
    "positionTotalSuccess",
    "truthEligible",
    "voided",
    "voidReason",
    "settleRepairReason",
    "periodTransition"
  ].forEach((key) => {
    if (evaluation[key] !== undefined) out[key] = evaluation[key];
  });
  // FRAMEWORK_SNAPSHOT_STORE_KEYS 为权威清单：防止新增框架只进 seal/evaluate 却进不了 slim
  FRAMEWORK_SNAPSHOT_STORE_KEYS.forEach((key) => {
    if (evaluation[key] !== undefined && out[key] === undefined) out[key] = evaluation[key];
  });
  FRAMEWORK_SNAPSHOT_STORE_KEYS.concat(["threeDuishu", "fourPosExclude", "fourPosShapes"]).forEach((key) => {
    if (out[key]) out[key] = slimFrameworkEvaluationForPersist(out[key]);
  });
  return out;
}

function slimPredictionForPanel(prediction, includeMeta) {
  if (!prediction || typeof prediction !== "object") return prediction || null;
  const out = {};
  [
    "id",
    "modelVersion",
    "baseUid",
    "basePeriod",
    "baseOpenTime",
    // 面板审计/对奖依赖目标期；禁止从 /state 瘦身抹掉（否则历史预测看起来缺胳膊少腿）
    "targetPeriod",
    "targetPeriodRule",
    "periodsPerDay",
    "generatedAt",
    "generatedAtText",
    "totalHeweiKill",
    "totalHeweiBackupDigit",
    "threeFourHeweiKillDigit",
    "threeFourHeweiBackupDigit",
    "evaluated",
    "counterApplied",
    "tipReady",
    "tipStructurePerfect",
    "structureReady",
    "truthReady",
    "qualityReady",
    "operationalReady",
    "snapshotsSealedAt",
    "snapshotsSealedBaseUid",
    "allSnapshotsSealed"
  ].forEach((key) => {
    if (prediction[key] !== undefined) out[key] = prediction[key];
  });
  // 内存 tip 偶发缺 targetPeriod（旧 hydrate/瘦身残留）：按 base 即时补齐，禁止面板显示空目标期
  if (!out.targetPeriod && out.basePeriod) {
    const nextPeriod = expectedNextPenta5Period(out.basePeriod);
    if (nextPeriod && nextPeriod.ok) {
      out.targetPeriod = nextPeriod.expectedPeriod;
      out.targetPeriodRule = out.targetPeriodRule || nextPeriod.rule || "panel-slim-repair";
    }
  }
  if (prediction.evaluation) out.evaluation = slimEvaluation(prediction.evaluation);
  if (prediction.positionKills) out.positionKills = slimPositionKills(prediction.positionKills, !!includeMeta);
  if (prediction.independentHeweiKills) out.independentHeweiKills = slimHeweiKills(prediction.independentHeweiKills, !!includeMeta);
  if (includeMeta && prediction.recommendationMeta) out.recommendationMeta = prediction.recommendationMeta;
  if (includeMeta && prediction.extraKills) out.extraKills = slimExtraKills(prediction.extraKills);
  // 历史 predictions 也要带框架 snapshot / 更稳 / 门道，否则面板历史核对像“乱七八糟”
  // 不再重复 *Items/*RepairReport
  FRAMEWORK_SNAPSHOT_STORE_KEYS.forEach((storeName) => {
    if (prediction[storeName]) out[storeName] = slimFrameworkSnapshotForPanel(prediction[storeName]);
  });
  if (prediction.stableMathFilter) {
    out.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(prediction.stableMathFilter);
  }
  if (prediction.hewei && typeof prediction.hewei === "object") out.hewei = prediction.hewei;
  if (prediction.codexStable && typeof prediction.codexStable === "object") {
    out.codexStable = prediction.codexStable;
  }
  if (prediction.orchestrateMeta && typeof prediction.orchestrateMeta === "object") {
    out.orchestrateMeta = prediction.orchestrateMeta;
  }
  if (includeMeta && prediction.detail) {
    out.detail = slimThreeFourDetail(prediction.detail, true);
  } else if (prediction.detail && prediction.detail.threeFourHeweiKill) {
    out.detail = {
      threeFourHeweiKill: {
        killDigit: prediction.detail.threeFourHeweiKill.killDigit,
        primaryDigit: prediction.detail.threeFourHeweiKill.primaryDigit,
        backupDigit: prediction.detail.threeFourHeweiKill.backupDigit
      }
    };
  }
  if (prediction.threeDuishu) out.threeDuishu = slimThreeDuishuSnapshot(prediction.threeDuishu);
  if (prediction.fourPosExclude) out.fourPosExclude = slimFourPosExcludeSnapshot(prediction.fourPosExclude);
  return out;
}

function slimThreeDuishuSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return snapshot || null;
  const out = {};
  [
    "version",
    "kind",
    "key",
    "code",
    "shot",
    "pairs",
    "pairCodes",
    "selectedPair",
    "selectedPairCode",
    "sealed",
    "sealedAt",
    "sealedBaseUid",
    "sealedBasePeriod",
    "sealHash",
    "success",
    "truthEligible",
    "rate30",
    "rate40",
    "rate300",
    "quality",
    "confidence",
    "recommendable",
    "stableMathOverlap",
    "pairSelectionMode",
    "pairSelectionHoldCount",
    "pairSelectionMaxHold"
  ].forEach((key) => {
    if (snapshot[key] !== undefined) out[key] = snapshot[key];
  });
  return out;
}

function slimHistoryRecord(record) {
  if (!record || typeof record !== "object") return record || null;
  const out = {};
  ["uid", "period", "opentime", "numbers"].forEach((key) => {
    if (record[key] !== undefined) out[key] = record[key];
  });
  const metrics = record.metrics && typeof record.metrics === "object" ? record.metrics : null;
  if (metrics) {
    out.metrics = {};
    ["totalHewei", "wanQianBaiHewei", "qianBaiShiHewei", "wanQianGeHewei", "wanBaiShiHewei", "wanQianBaiShiHewei"].forEach((key) => {
      if (metrics[key] !== undefined) out.metrics[key] = metrics[key];
    });
  }
  return out;
}

function slimFrameworkSnapshotForPanel(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return snapshot || null;
  const out = Object.assign({}, snapshot);
  if (Array.isArray(snapshot.items)) {
    out.items = snapshot.items.map((item) => {
      if (!item || typeof item !== "object") return item;
      return Object.assign({}, item);
    });
  }
  if (Array.isArray(out.repairReport) && out.repairReport.length > 12) {
    out.repairReport = out.repairReport.slice(0, 12);
  }
  return out;
}

function slimFrameworkSnapshotForRobot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return snapshot || null;
  const out = {};
  ["version", "sealed", "sealedAt", "sealedBaseUid", "sealedBasePeriod", "sealHash"].forEach((key) => {
    if (snapshot[key] !== undefined) out[key] = snapshot[key];
  });
  if (Array.isArray(snapshot.items)) {
    out.items = snapshot.items.map((item) => {
      if (!item || typeof item !== "object") return item;
      const value = {};
      [
        "rank", "kind", "key", "side", "name", "code", "mode", "shapeKey",
        "rate", "rate30", "rate60", "rate120", "rate300", "stableScore", "stageLabel",
        "confidence", "recommendable", "generatedGroupCount"
      ].forEach((key) => {
        if (item[key] !== undefined) value[key] = item[key];
      });
      return value;
    });
  }
  return out;
}

function buildRobotStatePayload() {
  const current = state && state.currentPrediction && typeof state.currentPrediction === "object"
    ? state.currentPrediction
    : {};
  const prediction = {};
  [
    "id", "modelVersion", "baseUid", "basePeriod", "baseOpenTime", "targetPeriod", "targetPeriodRule",
    "generatedAt", "generatedAtText", "evaluated", "tipReady", "tipStructurePerfect",
    "structureReady", "truthReady", "qualityReady", "operationalReady",
    "snapshotsSealedAt", "snapshotsSealedBaseUid", "allSnapshotsSealed",
    "totalHeweiKill", "totalHeweiBackupDigit", "threeFourHeweiKillDigit", "threeFourHeweiBackupDigit"
  ].forEach((key) => {
    if (current[key] !== undefined) prediction[key] = current[key];
  });
  prediction.positionKills = slimPositionKills(current.positionKills, true);
  prediction.independentHeweiKills = slimHeweiKills(current.independentHeweiKills, true);
  prediction.extraKills = slimExtraKills(current.extraKills);
  prediction.detail = slimThreeFourDetail(current.detail, true);
  FRAMEWORK_SNAPSHOT_STORE_KEYS.forEach((storeName) => {
    if (current[storeName]) prediction[storeName] = slimFrameworkSnapshotForRobot(current[storeName]);
  });
  if (current.threeDuishu) prediction.threeDuishu = slimThreeDuishuSnapshot(current.threeDuishu);
  if (current.fourPosExclude) prediction.fourPosExclude = slimFourPosExcludeSnapshot(current.fourPosExclude);

  const historyLimit = Math.max(30, Math.min(120, MODEL_STABLE_WINDOW, MAX_HISTORY || 120));
  return {
    ok: !state.lastError,
    version: state.version,
    modelVersion: MODEL_VERSION,
    dataReadMode: DATA_READ_MODE,
    historyPersistedInRds: true,
    lastSync: state.lastSync || {},
    latestRecord: slimHistoryRecord(state.latestRecord),
    currentPrediction: prediction,
    tipReady: state.tipReady === true,
    structureReady: state.structureReady === true,
    truthReady: state.truthReady === true,
    qualityReady: state.qualityReady === true,
    operationalReady: state.operationalReady === true,
    tipReadyIssueCount: Number(state.tipReadyIssueCount || 0),
    stormMode: normalizeStormMode(state.stormMode),
    dailyStreakLedger: normalizeDailyStreakLedger(state.dailyStreakLedger),
    modelWatchdog: normalizeModelWatchdog(state.modelWatchdog),
    backdeleteSnapshot: getLatestBackdeleteSnapshot(),
    history: Array.isArray(state.history) ? state.history.slice(0, historyLimit).map(slimHistoryRecord) : []
  };
}

function buildPanelStatePayload() {
  const summary = buildSummary();
  summary.latestRecord = slimHistoryRecord(state.latestRecord);
  summary.currentPrediction = slimPredictionForPanel(state.currentPrediction, true);
  const predictionLimit = Math.max(30, Math.min(PANEL_PREDICTION_LIMIT, PANEL_STATE_PREDICTION_LIMIT));
  const historyLimit = Math.max(60, Math.min(PANEL_HISTORY_LIMIT, MODEL_STABLE_WINDOW, MAX_HISTORY || PANEL_HISTORY_LIMIT));
  return Object.assign({}, summary, {
    backdeleteSnapshot: getLatestBackdeleteSnapshot(),
    history: Array.isArray(state.history) ? state.history.slice(0, historyLimit).map(slimHistoryRecord) : [],
    predictions: Array.isArray(state.predictions) ? state.predictions.slice(0, predictionLimit).map((prediction) => slimPredictionForPanel(prediction, false)) : [],
    logs: Array.isArray(state.logs) ? state.logs.slice(0, PANEL_LOG_LIMIT) : []
  });
}

function panelStateKey() {
  const latestLog = Array.isArray(state.logs) && state.logs[0] ? state.logs[0].at || state.logs[0].text || "" : "";
  const currentId = state.currentPrediction && state.currentPrediction.id ? state.currentPrediction.id : "";
  const latest = state.latestRecord || {};
  const current = state.currentPrediction || {};
  const lastSync = state.lastSync || {};
  const watchdog = state.modelWatchdog || {};
  const backdeleteSnapshot = getLatestBackdeleteSnapshot();
  return [
    state.updatedAt || "",
    latest.uid || "",
    latest.period || "",
    Array.isArray(latest.numbers) ? latest.numbers.join("") : "",
    lastSync.at || "",
    lastSync.latestPeriod || "",
    Array.isArray(lastSync.latestNumbers) ? lastSync.latestNumbers.join("") : "",
    currentId,
    current.baseUid || "",
    current.basePeriod || "",
    current.evaluated ? "1" : "0",
    current.evaluation && current.evaluation.evaluatedAt ? current.evaluation.evaluatedAt : "",
    Array.isArray(state.history) ? state.history.length : 0,
    Array.isArray(state.predictions) ? state.predictions.length : 0,
    watchdog.lastCheckAt || "",
    watchdog.lastRepairAt || "",
    Array.isArray(watchdog.lastIssues) ? watchdog.lastIssues.length : 0,
    Array.isArray(watchdog.lastActions) ? watchdog.lastActions.length : 0,
    backdeleteSnapshot && backdeleteSnapshot.fingerprint ? backdeleteSnapshot.fingerprint : "",
    latestLog
  ].join("|");
}

function getPanelStateJson() {
  const now = Date.now();
  const key = panelStateKey();
  // TTL 内也必须比对 key：新期入库后若只按时间复用旧串，面板会短时看到上一期
  if (
    panelStateCacheText &&
    panelStateCacheKey === key &&
    now - panelStateCacheCreatedAt < PANEL_STATE_CACHE_TTL_MS
  ) {
    return panelStateCacheText;
  }
  panelStateCacheKey = key;
  panelStateCacheText = JSON.stringify(buildPanelStatePayload());
  panelStateCacheGzip = null;
  panelStateCacheCreatedAt = now;
  return panelStateCacheText;
}

function commonJsonHeaders(extra) {
  return Object.assign({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Connection": "close",
    "X-Content-Type-Options": "nosniff"
  }, extra || {});
}

function clientAcceptsGzip(req) {
  return /\bgzip\b/i.test(String((req && req.headers && req.headers["accept-encoding"]) || ""));
}

function sendPanelState(req, res) {
  res.setTimeout(SERVER_REQUEST_TIMEOUT, () => {
    res.destroy();
  });
  const text = getPanelStateJson();
  if (clientAcceptsGzip(req)) {
    if (!panelStateCacheGzip) panelStateCacheGzip = zlib.gzipSync(Buffer.from(text, "utf8"));
    res.writeHead(200, commonJsonHeaders({
      "Content-Encoding": "gzip",
      "Content-Length": String(panelStateCacheGzip.length),
      "Vary": "Accept-Encoding"
    }));
    res.end(panelStateCacheGzip);
    return;
  }
  res.writeHead(200, commonJsonHeaders({
    "Content-Length": String(Buffer.byteLength(text, "utf8")),
    "Vary": "Accept-Encoding"
  }));
  res.end(text);
}

function sendSharedHistory(req, res, parsedUrl) {
  const requestedLimit = Number(parsedUrl.searchParams.get("limit") || 1500);
  const limit = Math.max(1, Math.min(5000, Number.isFinite(requestedLimit) ? Math.floor(requestedLimit) : 1500));
  const sourceHistory = Array.isArray(state && state.history) ? state.history : [];
  const history = sourceHistory.slice(0, limit).map((row) => ({
    period: String((row && (row.period || row.issue || row.uid)) || ""),
    uid: String((row && (row.uid || row.period || row.issue)) || ""),
    opentime: String((row && (row.opentime || row.openTime)) || ""),
    numbers: Array.isArray(row && row.numbers) ? row.numbers.slice(0, 5).map((value) => String(value)) : []
  }));
  sendJson(res, 200, {
    ok: true,
    sharedHistorySource: String((state && state.sharedHistorySource) || ""),
    sharedHistoryLatestPeriod: String((state && state.sharedHistoryLatestPeriod) || (history[0] && history[0].period) || ""),
    sharedHistoryRdsLatestPeriod: String((state && state.sharedHistoryRdsLatestPeriod) || ""),
    availableHistoryCount: sourceHistory.length,
    historyCount: history.length,
    latestRecord: history[0] || null,
    history
  });
}

let converterHistoryHydratePromise = null;
async function ensureConverterHistoryHydrated() {
  if (!HISTORY_FROM_RDS || !state) return { ok: false, reason: "disabled" };
  if (converterHistoryHydratePromise) return converterHistoryHydratePromise;
  converterHistoryHydratePromise = Promise.resolve()
    .then(() => hydrateSharedHistoryFromRds(state))
    .finally(() => {
      converterHistoryHydratePromise = null;
    });
  return converterHistoryHydratePromise;
}

function buildConverterHistoryRows(limit) {
  const rdsPeriod = String((state && state.sharedHistoryRdsLatestPeriod) || "");
  const sourceAll = Array.isArray(state && state.history) ? state.history : [];
  const sourceHistory = rdsPeriod
    ? sourceAll.filter((row) => {
        const period = String((row && (row.period || row.issue || row.uid)) || "");
        return period && period <= rdsPeriod;
      })
    : sourceAll;
  const history = sourceHistory.slice(0, limit).map((row) => {
    const numbers = Array.isArray(row && row.numbers) ? row.numbers.slice(0, 4).map((value) => String(value)) : [];
    return {
      period: String((row && (row.period || row.issue || row.uid)) || ""),
      opentime: String((row && (row.opentime || row.openTime)) || ""),
      numbers
    };
  }).filter((row) => /^\d+$/.test(row.period) && row.numbers.length === 4 && row.numbers.every((value) => /^\d$/.test(value)));
  const sharedPeriod = String((history[0] && history[0].period) || "");
  // ready 看「库内可用期数」，不能看本次 limit 切片长度（否则 limit=200 会被误判 503）
  const poolMin = Math.max(200, Math.min(800, Number(limit) || 200));
  return {
    rdsPeriod,
    sourceHistory,
    history,
    sharedPeriod,
    ready: !!(
      history.length &&
      rdsPeriod &&
      sharedPeriod === rdsPeriod &&
      sourceHistory.length >= poolMin
    )
  };
}

async function sendConverterHistory(req, res, parsedUrl) {
  const requestedLimit = Number(parsedUrl.searchParams.get("limit") || 1500);
  const limit = Math.max(1, Math.min(5000, Number.isFinite(requestedLimit) ? Math.floor(requestedLimit) : 1500));

  // 回删取数：优先每次从阿里云 RDS 拉最新；瞬时失败时若内存已有≥800期纯 RDS 段则降级继续，避免整段回删瘫痪
  let refreshedFromRds = false;
  let hydrateError = "";
  if (HISTORY_FROM_RDS && state) {
    try {
      const hydrated = await ensureConverterHistoryHydrated();
      if (hydrated && hydrated.ok === true) {
        refreshedFromRds = true;
      } else {
        hydrateError = String((hydrated && hydrated.reason) || "hydrate-failed");
      }
    } catch (err) {
      hydrateError = String(err && err.message ? err.message : err);
    }
  }

  const built = buildConverterHistoryRows(limit);
  if (!built.ready) {
    sendJson(res, 503, {
      ok: false,
      error: hydrateError
        ? ("RDS hydrate failed: " + hydrateError + "；且内存历史不可用于回删")
        : "shared RDS history is not ready",
      reason: hydrateError || "not-ready",
      sharedHistorySource: String((state && state.sharedHistorySource) || ""),
      sharedHistoryLatestPeriod: built.sharedPeriod,
      sharedHistoryRdsLatestPeriod: built.rdsPeriod,
      historyCount: built.history.length,
      availableHistoryCount: built.sourceHistory.length
    });
    return;
  }

  sendJson(res, 200, {
    ok: true,
    service: "penta5-converter-history",
    sharedHistorySource: "rds",
    sharedHistoryLatestPeriod: built.sharedPeriod,
    sharedHistoryRdsLatestPeriod: built.rdsPeriod,
    availableHistoryCount: built.sourceHistory.length,
    historyCount: built.history.length,
    latestRecord: built.history[0],
    history: built.history,
    refreshedFromRds: refreshedFromRds,
    hydrateFallback: !refreshedFromRds && !!hydrateError,
    sharedHistorySyncedAt: (state && state.sharedHistorySyncedAt) || null
  });
}

function getLatestBackdeleteSnapshot() {
  try {
    return backdeleteSnapshotStore.latest();
  } catch (_) {
    return null;
  }
}

function currentBackdeletePanelContext() {
  const prediction = state && state.currentPrediction && typeof state.currentPrediction === "object"
    ? state.currentPrediction
    : {};
  const snapshotPair = String(prediction && prediction.threeDuishu && prediction.threeDuishu.selectedPair || "");
  const recommendation = buildThreeDuishuRecommendation();
  const recommendationPair = String(recommendation && recommendation.selectedPair || "");
  const panelPair = /^\d{2}$/.test(snapshotPair)
    ? snapshotPair
    : (/^\d{2}$/.test(recommendationPair) ? recommendationPair : "");
  return {
    panelPair,
    panelBasePeriod: String(prediction.basePeriod || prediction.baseUid || ""),
    panelTargetPeriod: String(prediction.targetPeriod || "")
  };
}

async function buildBackdeleteHistoryContext() {
  let hydrateError = "";
  if (!HISTORY_FROM_RDS) throw new Error("RDS 共享历史读取已关闭，禁止生成回删结果。");
  try {
    const hydrated = await ensureConverterHistoryHydrated();
    if (!hydrated || hydrated.ok !== true) hydrateError = String(hydrated && hydrated.reason || "hydrate-failed");
  } catch (error) {
    hydrateError = String(error && error.message || error);
  }
  const built = buildConverterHistoryRows(200);
  if (!built.ready) {
    throw new Error(hydrateError
      ? ("RDS 刷新失败且共享历史未就绪：" + hydrateError)
      : "RDS 共享历史未就绪。");
  }
  if (String(state && state.sharedHistorySource || "").toLowerCase() !== "rds") {
    throw new Error("当前共享历史来源不是 RDS，已停止回删。");
  }
  const panel = currentBackdeletePanelContext();
  if (!/^\d{2}$/.test(panel.panelPair)) throw new Error("面板本期排除对数尚未就绪。");
  if (panel.panelBasePeriod && panel.panelBasePeriod !== built.sharedPeriod) {
    throw new Error("面板基准期 " + panel.panelBasePeriod + " 与 RDS 最新期 " + built.sharedPeriod + " 不一致，已停止回删。");
  }
  return {
    source: "rds",
    sharedPeriod: built.sharedPeriod,
    rdsPeriod: built.rdsPeriod,
    rows: built.history,
    panelBasePeriod: panel.panelBasePeriod || built.sharedPeriod,
    panelTargetPeriod: panel.panelTargetPeriod,
    panelPair: panel.panelPair,
    refreshedFromRds: !hydrateError,
    hydrateFallback: !!hydrateError
  };
}

function loadQualifiedPicksPayload() {
  if (PANEL_PASSWORD && fs.existsSync(QUALIFIED_PICKS_SEALED_FILE)) {
    try {
      const wrapped = JSON.parse(fs.readFileSync(QUALIFIED_PICKS_SEALED_FILE, "utf8"));
      if (wrapped && wrapped.envelope) {
        return qualifiedPicksSeal.unsealPayload(wrapped.envelope, PANEL_PASSWORD);
      }
    } catch (_) {}
  }
  try {
    if (!fs.existsSync(QUALIFIED_PICKS_FILE)) return null;
    return JSON.parse(fs.readFileSync(QUALIFIED_PICKS_FILE, "utf8"));
  } catch (_) {
    return null;
  }
}

function loadQualifiedPicksLocks() {
  const locks = qualifiedPicksSecureStore.readSealedField({
    sealedFile: QUALIFIED_PICKS_LOCKS_SEALED_FILE,
    metaFile: QUALIFIED_PICKS_LOCKS_FILE,
    password: PANEL_PASSWORD,
    field: "locks",
    legacyReader() {
      try {
        if (!fs.existsSync(QUALIFIED_PICKS_LOCKS_FILE)) return {};
        const raw = JSON.parse(fs.readFileSync(QUALIFIED_PICKS_LOCKS_FILE, "utf8"));
        if (raw && raw.locksSealed === true) return {};
        return raw && raw.locks && typeof raw.locks === "object" ? raw.locks : (raw || {});
      } catch (_) {
        return {};
      }
    }
  });
  return locks && typeof locks === "object" ? locks : {};
}

function loadQualifiedPicksDualLock() {
  return qualifiedPicksSecureStore.readSealedField({
    sealedFile: QUALIFIED_PICKS_DUAL_LOCKS_SEALED_FILE,
    metaFile: QUALIFIED_PICKS_DUAL_LOCKS_FILE,
    password: PANEL_PASSWORD,
    field: "lock",
    legacyReader() {
      try {
        if (!fs.existsSync(QUALIFIED_PICKS_DUAL_LOCKS_FILE)) return null;
        const raw = JSON.parse(fs.readFileSync(QUALIFIED_PICKS_DUAL_LOCKS_FILE, "utf8"));
        if (raw && raw.lockSealed === true) return null;
        return raw && raw.lock ? raw.lock : null;
      } catch (_) {
        return null;
      }
    }
  });
}

const qualifiedPicksRateBuckets = new Map();
function qualifiedPicksRateLimited(req) {
  const ip = String(req.socket && req.socket.remoteAddress || "unknown");
  const now = Date.now();
  let bucket = qualifiedPicksRateBuckets.get(ip);
  if (!bucket || now > bucket.resetAt) {
    bucket = { count: 0, resetAt: now + 60000 };
  }
  bucket.count += 1;
  qualifiedPicksRateBuckets.set(ip, bucket);
  return bucket.count > 60;
}

function sendQualifiedPicksJson(res, statusCode, body) {
  const text = safeJsonStringify(body);
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Expires": "0",
    "Connection": "close",
    "X-Content-Type-Options": "nosniff",
    "Content-Length": String(Buffer.byteLength(text, "utf8"))
  });
  res.end(text);
}

function qualifiedPicksStreakOpts() {
  return {
    maxShowStreak: Number(process.env.QUALIFIED_PICKS_MAX_SHOW_STREAK != null
      ? process.env.QUALIFIED_PICKS_MAX_SHOW_STREAK : 0),
    maxEntryConsecutive: Number(process.env.QUALIFIED_PICKS_MAX_ENTRY_CONSECUTIVE != null
      ? process.env.QUALIFIED_PICKS_MAX_ENTRY_CONSECUTIVE : 1),
    cooldownPeriods: Math.max(0, Number(process.env.QUALIFIED_PICKS_FAIL_COOLDOWN || 3)),
    entryLookback: Math.max(3, Number(process.env.QUALIFIED_PICKS_ENTRY_LOOKBACK || 8))
  };
}

function scanQualifiedPicksForVerify(sourceHistory) {
  const windowN = Math.max(20, Number(process.env.QUALIFIED_PICKS_WINDOW || 100));
  const minRate = Number(process.env.QUALIFIED_PICKS_MIN_RATE || 92);
  const stickyEnabled = String(process.env.QUALIFIED_PICKS_STICKY || "1") !== "0";
  const streakOpts = Object.assign({
    window: windowN,
    minRate,
    exitMinRate: Number(process.env.QUALIFIED_PICKS_EXIT_MIN_RATE || 88),
    minLockPeriods: Number(process.env.QUALIFIED_PICKS_MIN_LOCK_PERIODS || 12),
    now: isoNow()
  }, qualifiedPicksStreakOpts());
  if (!stickyEnabled) {
    return qualifiedPicksScanner.scanQualifiedPicks(sourceHistory, { window: windowN, minRate });
  }
  return qualifiedPicksSticky.scanQualifiedPicksSticky(sourceHistory, streakOpts, loadQualifiedPicksLocks());
}

function scanDualPicksForVerify(sourceHistory) {
  const windowN = Math.max(20, Number(process.env.QUALIFIED_PICKS_WINDOW || 100));
  const dualEnabled = String(process.env.QUALIFIED_PICKS_DUAL_ENABLED || "1") !== "0";
  if (!dualEnabled) return { picks: [] };
  const stickyEnabled = String(process.env.QUALIFIED_PICKS_STICKY || "1") !== "0";
  const streakOpts = Object.assign({
    window: windowN,
    minRate: Number(process.env.QUALIFIED_PICKS_DUAL_MIN_RATE || 89),
    exitMinRate: Number(process.env.QUALIFIED_PICKS_DUAL_EXIT_MIN_RATE || 85),
    minLockPeriods: Number(process.env.QUALIFIED_PICKS_MIN_LOCK_PERIODS || 12),
    now: isoNow()
  }, qualifiedPicksStreakOpts());
  if (!stickyEnabled) {
    return qualifiedPicksDual.scanDualPositionPicks(sourceHistory, streakOpts);
  }
  return qualifiedPicksDual.scanDualPositionSticky(sourceHistory, streakOpts, loadQualifiedPicksDualLock());
}

function sealQualifiedPicksResponse(body) {
  if (!PANEL_PASSWORD) return body;
  return {
    ok: body.ok !== false,
    sealed: true,
    envelope: qualifiedPicksSeal.sealPayload(body, PANEL_PASSWORD)
  };
}

function sendQualifiedPicks(res, authRole, req) {
  if (authRole !== "admin") {
    sendQualifiedPicksJson(res, 403, { ok: false, error: "qualified-picks admin only" });
    return;
  }
  if (req && qualifiedPicksRateLimited(req)) {
    sendQualifiedPicksJson(res, 429, { ok: false, error: "qualified-picks rate limited" });
    return;
  }
  try {
    const payload = loadQualifiedPicksPayload();
    if (!payload) {
      sendQualifiedPicksJson(res, 200, sealQualifiedPicksResponse({
        ok: true, picks: [], dualPicks: [], trusted: false, updatedAt: null, window: 100, minRate: 92
      }));
      return;
    }
    const auditInfo = payload.audit && typeof payload.audit === "object" ? payload.audit : null;
    const trusted = !!(auditInfo && auditInfo.crossCheckOk === true && auditInfo.windowDigest);
    sendQualifiedPicksJson(res, 200, sealQualifiedPicksResponse({
      ok: true,
      trusted,
      picks: trusted && Array.isArray(payload.picks) ? payload.picks : [],
      dualPicks: trusted && Array.isArray(payload.dualPicks) ? payload.dualPicks : [],
      updatedAt: payload.updatedAt || null,
      window: Number(payload.window) || 100,
      minRate: Number(payload.minRate) || 92,
      exitMinRate: Number(payload.exitMinRate) || 88,
      minLockPeriods: Number(payload.minLockPeriods) || 12,
      dualMinRate: Number(payload.dualMinRate) || 89,
      maxShowStreak: Number(payload.maxShowStreak != null ? payload.maxShowStreak : 0),
      failCooldown: Number(payload.failCooldown != null ? payload.failCooldown : 3),
      strategy: payload.strategy || null,
      latestPeriod: payload.latestPeriod || null,
      audit: auditInfo
    }));
  } catch (err) {
    sendQualifiedPicksJson(res, 200, sealQualifiedPicksResponse({
      ok: true, trusted: false, picks: [], dualPicks: [], error: String(err && err.message || err)
    }));
  }
}

function sendQualifiedPicksVerify(res, authRole, req) {
  if (authRole !== "admin") {
    sendQualifiedPicksJson(res, 403, { ok: false, error: "qualified-picks verify admin only" });
    return;
  }
  if (req && qualifiedPicksRateLimited(req)) {
    sendQualifiedPicksJson(res, 429, { ok: false, error: "qualified-picks verify rate limited" });
    return;
  }
  const windowN = Math.max(20, Number(process.env.QUALIFIED_PICKS_WINDOW || 100));
  const sourceHistory = Array.isArray(state && state.history) ? state.history : [];
  const liveScan = scanQualifiedPicksForVerify(sourceHistory);
  const liveDual = scanDualPicksForVerify(sourceHistory);
  const liveNorm = qualifiedPicksScanner.normalizeHistoryRows(sourceHistory);
  const liveDigest = qualifiedPicksAudit.historyWindowDigest(liveNorm, windowN);

  const filePayload = loadQualifiedPicksPayload();
  const fileAudit = filePayload && filePayload.audit ? filePayload.audit : null;
  const filePicks = filePayload && Array.isArray(filePayload.picks) ? filePayload.picks : [];
  const fileDualPicks = filePayload && Array.isArray(filePayload.dualPicks) ? filePayload.dualPicks : [];
  const picksMatch = qualifiedPicksAudit.picksMatch(liveScan.picks, filePicks);
  const dualPicksMatch = qualifiedPicksDual.dualPicksMatch(liveDual.picks || [], fileDualPicks);
  const digestMatch = !!(fileAudit && fileAudit.windowDigest && fileAudit.windowDigest === liveDigest.digest);
  const crossCheckOk = !!(fileAudit && fileAudit.crossCheckOk === true);
  const trusted = crossCheckOk && picksMatch && dualPicksMatch && digestMatch;

  sendQualifiedPicksJson(res, 200, sealQualifiedPicksResponse({
    ok: trusted,
    trusted,
    verifiedAt: isoNow(),
    checks: {
      crossCheckOk,
      picksMatch,
      dualPicksMatch,
      digestMatch,
      historyAvailable: liveNorm.length >= windowN
    },
    live: {
      picks: liveScan.picks,
      pickCount: liveScan.picks.length,
      dualPicks: liveDual.picks || [],
      dualPickCount: (liveDual.picks || []).length,
      latestPeriod: liveScan.latestPeriod,
      windowDigest: liveDigest.digest,
      windowNewestPeriod: liveDigest.newestPeriod,
      windowOldestPeriod: liveDigest.oldestPeriod,
      picksFingerprint: qualifiedPicksAudit.pickFingerprint(liveScan.picks),
      dualPicksFingerprint: qualifiedPicksDual.dualPickFingerprint(liveDual.picks || []),
      sharedHistorySource: String((state && state.sharedHistorySource) || ""),
      historyCount: liveNorm.length
    },
    file: filePayload ? {
      updatedAt: filePayload.updatedAt || null,
      pickCount: filePicks.length,
      dualPickCount: fileDualPicks.length,
      audit: fileAudit
    } : null
  }));
}

function sendBackdeleteState(res) {
  const integrityError = backdeleteSnapshotStore.integrityError();
  if (integrityError) {
    sendJson(res, 500, {
      ok: false,
      error: integrityError,
      converterVersion: backdeleteSnapshotStore.converterVersion,
      snapshot: getLatestBackdeleteSnapshot()
    });
    return;
  }
  sendJson(res, 200, {
    ok: true,
    converterVersion: backdeleteSnapshotStore.converterVersion,
    snapshot: getLatestBackdeleteSnapshot()
  });
}

async function generateBackdeleteSnapshot(req, res) {
  let payload;
  try {
    const raw = await readRequestBody(req);
    payload = JSON.parse(raw || "{}");
  } catch (error) {
    sendJson(res, 400, { ok: false, error: "请求格式无效：" + String(error && error.message || error) });
    return;
  }
  try {
    const historyContext = await buildBackdeleteHistoryContext();
    const suppliedPair = String(payload && payload.panelThreeDuishuPair || "");
    if (suppliedPair && suppliedPair !== historyContext.panelPair) {
      sendJson(res, 409, {
        ok: false,
        error: "页面对数 " + suppliedPair + " 与面板本期对数 " + historyContext.panelPair + " 不一致，请刷新后重试。"
      });
      return;
    }
    const generated = backdeleteSnapshotStore.generate({
      attackText: payload && payload.attackText,
      target: payload && payload.target,
      panelThreeDuishuPair: historyContext.panelPair,
      historyContext
    });
    invalidatePanelStateCache();
    sendJson(res, 200, {
      ok: true,
      reused: generated.reused === true,
      refreshedFromRds: historyContext.refreshedFromRds,
      hydrateFallback: historyContext.hydrateFallback,
      snapshot: generated.snapshot
    });
  } catch (error) {
    sendJson(res, 422, { ok: false, error: String(error && error.message || error) });
  }
}

function sendPanelHtml(req, res) {
  res.setTimeout(SERVER_REQUEST_TIMEOUT, () => {
    res.destroy();
  });
  const text = getPanelHtmlText();
  if (clientAcceptsGzip(req)) {
    if (!panelHtmlCacheGzip) panelHtmlCacheGzip = zlib.gzipSync(Buffer.from(text, "utf8"));
    res.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Pragma": "no-cache",
      "Expires": "0",
      "Connection": "close",
      "X-Content-Type-Options": "nosniff",
      "Clear-Site-Data": "\"cache\"",
      "Content-Encoding": "gzip",
      "Content-Length": String(panelHtmlCacheGzip.length),
      "Vary": "Accept-Encoding"
    });
    res.end(panelHtmlCacheGzip);
    return;
  }
  res.writeHead(200, {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Expires": "0",
    "Connection": "close",
    "X-Content-Type-Options": "nosniff",
    "Clear-Site-Data": "\"cache\"",
    "Content-Length": String(Buffer.byteLength(text, "utf8")),
    "Vary": "Accept-Encoding"
  });
  res.end(text);
}

function attackTxtContentType(pathname) {
  if (pathname.endsWith(".js")) return "application/javascript; charset=utf-8";
  if (pathname.endsWith(".json")) return "application/manifest+json; charset=utf-8";
  if (pathname.endsWith(".svg")) return "image/svg+xml; charset=utf-8";
  return "text/html; charset=utf-8";
}

function attackTxtFileForPath(pathname) {
  if (pathname === "/attack-txt" || pathname === "/gongjiyu") return path.join(__dirname, "attack_txt.html");
  if (pathname === "/txt_attack_convert.js") return path.join(__dirname, "txt_attack_convert.js");
  if (pathname === "/attack-txt-manifest.json") return path.join(__dirname, "attack_txt_manifest.json");
  if (pathname === "/attack-txt-icon.svg") return path.join(__dirname, "attack_txt_icon.svg");
  if (pathname === "/four_throw_urls.json") return path.join(__dirname, "four_throw_urls.json");
  return "";
}

function sendAttackTxtAsset(req, res, pathname) {
  const file = attackTxtFileForPath(pathname);
  if (!file) {
    sendJson(res, 404, { ok: false, error: "Not Found" });
    return;
  }
  try {
    const text = fs.readFileSync(file, "utf8");
    sendTextForRequest(req, res, 200, text, attackTxtContentType(pathname));
  } catch (err) {
    sendJson(res, 500, { ok: false, error: String(err && err.message || err) });
  }
}

function localMidnightMs(dateObj) {
  const d = dateObj instanceof Date ? dateObj : new Date(dateObj);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0, 0).getTime();
}

function nextDrawAlignedSyncAt(nowMs) {
  const now = Number.isFinite(nowMs) ? nowMs : Date.now();
  const baseDay = localMidnightMs(now);
  let best = null;
  for (let day = 0; day <= 1; day += 1) {
    const dayStart = baseDay + day * 24 * 60 * 60 * 1000;
    for (let hour = 0; hour < 24; hour += 1) {
      for (let mi = 0; mi < DRAW_SLOT_MINUTES.length; mi += 1) {
        const drawAt = dayStart + ((hour * 60 + DRAW_SLOT_MINUTES[mi]) * 60 + DRAW_SECOND) * 1000;
        for (let oi = 0; oi < DRAW_SYNC_OFFSETS.length; oi += 1) {
          const at = drawAt + DRAW_SYNC_OFFSETS[oi];
          if (at <= now + 80) continue;
          if (best === null || at < best) best = at;
        }
      }
    }
    if (best !== null) break;
  }
  return best === null ? now + DRAW_FALLBACK_SYNC_MS : best;
}

function clearAutoSyncTimer() {
  if (autoTimer) {
    clearTimeout(autoTimer);
    autoTimer = null;
  }
}

function scheduleDrawAlignedAutoSync(reason) {
  clearAutoSyncTimer();
  const now = Date.now();
  const nextAt = nextDrawAlignedSyncAt(now);
  const delay = Math.max(200, nextAt - now);
  autoTimer = setTimeout(() => {
    Promise.resolve(syncNow("auto-draw"))
      .catch((err) => {
        console.error("[auto-draw sync] failed", err && err.message ? err.message : err);
      })
      .finally(() => {
        scheduleDrawAlignedAutoSync("after-tick");
      });
  }, delay);
  try {
    addLog("已对齐开奖时刻调度同步", {
      reason: reason || "schedule",
      drawSecond: DRAW_SECOND,
      offsetsMs: DRAW_SYNC_OFFSETS.slice(),
      nextAt: new Date(nextAt).toISOString(),
      delayMs: delay
    });
  } catch (_) {}
}

async function syncNow(trigger) {
  if (syncRunning) {
    return {
      ok: true,
      skipped: true,
      reason: "同步正在执行，已跳过重复请求",
      summary: buildHealthSummary({ lite: true })
    };
  }
  syncRunning = true;
  const requestUrl = buildFreshApiUrl(SOURCE_API_URL);
  addLog(trigger === "manual" ? "开始手动同步" : (trigger === "auto-draw" ? "开始开奖时刻对齐同步" : (trigger === "panel-auto" ? "开始面板自动同步" : "开始后台自动同步")), { requestUrl, timeout: REQUEST_TIMEOUT, trigger });
  try {
    const response = await fetchWithTimeout(requestUrl, REQUEST_TIMEOUT);
    const contentType = response.headers.get("content-type") || "";
    const rawText = await response.text();
    if (!response.ok) {
      throw new Error(`旧代理返回 HTTP ${response.status} ${response.statusText}，片段：${rawText.slice(0, 300)}`);
    }
    const rawJson = parseJsonSafely(rawText, contentType);
    // 代理网络失败/超龄缓存会带 allowIntegrate=false；禁止入库，避免乱出数字
    if (rawJson && typeof rawJson === "object") {
      if (rawJson.ok === false) {
        throw new Error(`开奖代理明确失败：${String(rawJson.error || "ok=false").slice(0, 240)}`);
      }
      const remoteMeta = rawJson.remote && typeof rawJson.remote === "object" ? rawJson.remote : null;
      if (remoteMeta && remoteMeta.allowIntegrate === false) {
        throw new Error(`开奖代理禁止入库（陈旧/失败数据）：${String(remoteMeta.lastProxyError || "allowIntegrate=false").slice(0, 240)}`);
      }
    }
    const record = parseLotteryJsonToRecord(rawJson);
    if (!record || !/^\d{8,20}$/.test(String(record.period || "")) || !Array.isArray(record.numbers) || record.numbers.length !== 5 || record.numbers.some((d) => !/^\d$/.test(String(d)))) {
      throw new Error("开奖解析结果非法：期号或号码校验失败，已拒绝入库");
    }
    const earlyResult = integrateNewDrawRecordEarly(record);
    if (earlyResult.didAddNewRecord) {
      state.rawJson = rawJson;
      state.lastSync = {
        at: isoNow(),
        text: nowText(),
        trigger,
        didAddNewRecord: true,
        didEvaluatePreviousPrediction: earlyResult.didEvaluate,
        didApplyCounters: earlyResult.didApplyCounters,
        latestPeriod: record.period,
        latestNumbers: record.numbers,
        latestTotalHewei: record.metrics.totalHewei,
        earlyPublish: true
      };
      state.lastError = null;
      saveState({ skipMirror: true, skipMemoryCompact: true });
      addLog("新开奖已抢先发布顶栏", { period: record.period, numbers: record.numbers, trigger });
      await yieldEventLoop();
    }
    const result = await finalizeDrawRecordIntegration(record, earlyResult);
    state.rawJson = rawJson;
    state.lastSync = {
      at: isoNow(),
      text: nowText(),
      trigger,
      didAddNewRecord: result.didAddNewRecord,
      didEvaluatePreviousPrediction: result.didEvaluate,
      didApplyCounters: result.didApplyCounters,
      latestPeriod: record.period,
      latestNumbers: record.numbers,
      latestTotalHewei: record.metrics.totalHewei,
      earlyPublish: false
    };
    state.lastError = null;
    addLog("后台同步成功", state.lastSync);
    await yieldEventLoop();
    // 开奖结算：先把 /state 让出去。质量换码/整包重生极重，跟在 sync 里会堵死面板，用户一刷就回到登录页。
    if (result.didAddNewRecord || trigger === "manual") {
      const immediateWatchdog = trigger === "manual";
      if (immediateWatchdog) {
        const watchdogResult = runModelWatchdog("manual-sync", { save: false });
        state.lastSync.modelWatchdog = {
          ok: watchdogResult && watchdogResult.ok !== false,
          skipped: !!(watchdogResult && watchdogResult.skipped),
          issueCount: Number(watchdogResult && watchdogResult.issueCount || 0),
          actionCount: Number(watchdogResult && watchdogResult.actionCount || 0)
        };
      } else {
        state.lastSync.modelWatchdog = { deferred: true, reason: "draw-settle-defer", trigger: String(trigger || "") };
        const deferMs = 3500;
        setTimeout(() => {
          try {
            if (syncRunning) return;
            const watchdogResult = runModelWatchdog("sync-new-record-deferred", { save: true, integrityOnly: true });
            addLog("开奖后延后模型巡检完成", {
              ok: watchdogResult && watchdogResult.ok !== false,
              skipped: !!(watchdogResult && watchdogResult.skipped),
              issueCount: Number(watchdogResult && watchdogResult.issueCount || 0),
              actionCount: Number(watchdogResult && watchdogResult.actionCount || 0)
            });
            // 完整性之后：仅质量清锁（禁止整包重生），让下期选码能换掉老化锁
            try {
              const qualityResult = runModelWatchdog("sync-new-record-deferred-quality", {
                save: true,
                qualityLockReleaseOnly: true
              });
              addLog("开奖后延后质量清锁完成", {
                ok: qualityResult && qualityResult.ok !== false,
                skipped: !!(qualityResult && qualityResult.skipped),
                issueCount: Number(qualityResult && qualityResult.issueCount || 0),
                actionCount: Number(qualityResult && qualityResult.actionCount || 0)
              });
            } catch (qualityErr) {
              addLog("开奖后延后质量清锁失败", { error: String(qualityErr && qualityErr.message || qualityErr) });
            }
          } catch (err) {
            addLog("开奖后延后模型巡检失败", { error: String(err && err.message || err) });
          }
        }, deferMs);
      }
    } else {
      state.lastSync.modelWatchdog = { skipped: true, reason: "no-new-record" };
    }
    // 开奖主库：新期先同步写入 RDS，再从 RDS 灌注 history（只读副本）。
    // 若阿里 RDS 进入 read_only/LOCK_WRITE：本机降级保留开奖并排队追写，禁止整次 sync 失败导致面板假死。
    if (result.didAddNewRecord) {
      const wrote = await writeOfficialDrawToRds(record);
      state.lastSync.rdsWrite = wrote;
      if (wrote && wrote.readOnly) {
        addLog("RDS 只读(LOCK_WRITE)，本机保留开奖并排队追写", {
          period: record.period,
          uid: record.uid,
          pendingCount: wrote.pendingCount,
          error: wrote.error
        });
        scheduleRdsCatchupRetry("lock-write");
        settleMissedPredictionEvaluations({ reason: "sync-rds-readonly-degraded", save: false });
        alignTipPredictionAfterHydrate("sync-rds-readonly-degraded");
      } else {
        addLog("开奖已写入 RDS", { period: record.period, uid: record.uid, wrote });
        const catchup = await flushPendingOfficialDrawsToRds("after-write");
        state.lastSync.rdsCatchup = catchup;
        const hydrated = await hydrateSharedHistoryFromRds(state);
        state.lastSync.rdsHydrate = hydrated;
        if (!hydrated || !hydrated.ok) {
          throw new Error("RDS 历史灌注失败: " + String(hydrated && hydrated.reason || "unknown"));
        }
        if (String(hydrated.latestPeriod || "") !== String(record.period || "")) {
          // pending 合并后应能追上；仍落后则记告警但不回滚已发布开奖
          if (!(state.pendingOfficialDraws || []).length) {
            throw new Error(
              "RDS 历史未包含最新期: local=" + record.period + " rds=" + String(hydrated.latestPeriod || "")
            );
          }
          addLog("RDS 灌注后仍落后，已保留 pending", {
            local: record.period,
            hydratedLatest: hydrated.latestPeriod,
            pending: (state.pendingOfficialDraws || []).length
          });
        }
        if (state.history && state.history[0]) {
          state.latestRecord = state.history[0];
        }
        settleMissedPredictionEvaluations({ reason: "sync-after-rds-hydrate", save: false });
        alignTipPredictionAfterHydrate("sync-after-rds-hydrate");
      }
    } else if (trigger === "manual" && HISTORY_FROM_RDS) {
      await flushPendingOfficialDrawsToRds("manual");
      const hydrated = await hydrateSharedHistoryFromRds(state);
      state.lastSync.rdsHydrate = hydrated;
      settleMissedPredictionEvaluations({ reason: "manual-after-rds-hydrate", save: false });
      alignTipPredictionAfterHydrate("manual-after-rds-hydrate");
    }
    saveState();
    // 新开奖后异步刷新更稳排除缓存，禁止 spawnSync 堵事件循环
    if (result.didAddNewRecord || trigger === "manual") {
      try { scheduleStableMathFilterRefresh(result.didAddNewRecord ? "sync-new-record" : "manual-sync"); } catch (_) {}
    }
    return { ok: true, result, summary: buildHealthSummary({ lite: true }) };
  } catch (err) {
    let reason = "未知错误";
    if (err.name === "AbortError") reason = `请求超时，超过 ${REQUEST_TIMEOUT}ms 未响应`;
    else if (err instanceof TypeError) reason = "网络错误、DNS失败、旧代理未运行、3000端口未开放或目标站拒绝";
    else if (err && err.message) reason = err.message;
    state.lastError = { at: isoNow(), text: nowText(), trigger, reason, name: err.name || "Error" };
    addLog("后台同步失败，已保留硬盘历史数据", state.lastError);
    saveState();
    return { ok: false, error: state.lastError, summary: buildHealthSummary({ lite: true }) };
  } finally {
    syncRunning = false;
  }
}

function makeBackup() {
  ensureDir(BACKUP_DIR);
  const name = `state-backup-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  const file = path.join(BACKUP_DIR, name);
  fs.writeFileSync(file, safeJsonStringify(buildPersistedStateForDisk(state)));
  return file;
}

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 5 * 1024 * 1024) {
        reject(new Error("请求体过大"));
        req.destroy();
      }
    });
    req.on("end", () => resolve(body));
    req.on("error", reject);
  });
}

function readAlgorithmProjectStateFile(filePath) {
  try {
    if (!filePath || !fs.existsSync(filePath)) {
      return { ok: false, error: "not-ready", data: null };
    }
    const raw = fs.readFileSync(filePath, "utf8");
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object") {
      return { ok: false, error: "invalid-state", data: null };
    }
    return { ok: true, error: null, data };
  } catch (error) {
    return {
      ok: false,
      error: String(error && error.message ? error.message : error),
      data: null
    };
  }
}

function sendJson(res, statusCode, payload) {
  const text = safeJsonStringify(payload);
  sendJsonText(res, statusCode, text);
}

function sendJsonText(res, statusCode, text) {
  res.setTimeout(SERVER_REQUEST_TIMEOUT, () => {
    res.destroy();
  });
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Connection": "close",
    "X-Content-Type-Options": "nosniff"
  });
  res.end(text);
}

function sendText(res, statusCode, text, contentType) {
  res.setTimeout(SERVER_REQUEST_TIMEOUT, () => {
    res.destroy();
  });
  res.writeHead(statusCode, {
    "Content-Type": contentType || "text/plain; charset=utf-8",
    "Cache-Control": "no-store",
    "Connection": "close",
    "X-Content-Type-Options": "nosniff"
  });
  res.end(text);
}

function sendTextForRequest(req, res, statusCode, text, contentType) {
  res.setTimeout(SERVER_REQUEST_TIMEOUT, () => {
    res.destroy();
  });
  const type = contentType || "text/plain; charset=utf-8";
  const headers = {
    "Content-Type": type,
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Expires": "0",
    "Connection": "close",
    "X-Content-Type-Options": "nosniff",
    "Vary": "Accept-Encoding"
  };
  if (/^text\/html\b/i.test(type)) {
    headers["Clear-Site-Data"] = "\"cache\"";
  }
  if (statusCode !== 204 && clientAcceptsGzip(req) && Buffer.byteLength(String(text || ""), "utf8") > 1024) {
    const zipped = zlib.gzipSync(Buffer.from(String(text || ""), "utf8"));
    headers["Content-Encoding"] = "gzip";
    headers["Content-Length"] = String(zipped.length);
    res.writeHead(statusCode, headers);
    res.end(zipped);
    return;
  }
  headers["Content-Length"] = String(Buffer.byteLength(String(text || ""), "utf8"));
  res.writeHead(statusCode, headers);
  res.end(text);
}


function isAuthEnabled() {
  return (PANEL_USER !== "" && PANEL_PASSWORD !== "") || (PANEL_VIEWER_USER !== "" && PANEL_VIEWER_PASSWORD !== "");
}

function timingSafeEqualText(a, b) {
  const crypto = require("crypto");
  const left = Buffer.from(String(a || ""), "utf8");
  const right = Buffer.from(String(b || ""), "utf8");
  if (left.length !== right.length) return false;
  return crypto.timingSafeEqual(left, right);
}

function basicAuthCredentials(req) {
  const header = req.headers.authorization || "";
  if (!header.startsWith("Basic ")) return null;
  let decoded = "";
  try {
    decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
  } catch (err) {
    return null;
  }
  const splitAt = decoded.indexOf(":");
  if (splitAt < 0) return null;
  return {
    username: decoded.slice(0, splitAt),
    password: decoded.slice(splitAt + 1)
  };
}

function parseCookies(req) {
  const header = String(req && req.headers && req.headers.cookie || "");
  const out = {};
  header.split(";").forEach((part) => {
    const index = part.indexOf("=");
    if (index < 0) return;
    const key = part.slice(0, index).trim();
    const value = part.slice(index + 1).trim();
    if (!key) return;
    try {
      out[key] = decodeURIComponent(value);
    } catch (err) {
      out[key] = value;
    }
  });
  return out;
}

function panelSessionSecret() {
  return [
    "penta5-panel-session-v1",
    PANEL_USER,
    PANEL_PASSWORD,
    PANEL_VIEWER_USER,
    PANEL_VIEWER_PASSWORD,
    STATE_FILE
  ].join("|");
}

function signPanelSession(role, issuedAt) {
  const crypto = require("crypto");
  return crypto.createHmac("sha256", panelSessionSecret()).update(`${role}:${issuedAt}`).digest("hex");
}

function panelSessionCookieValue(role) {
  const safeRole = role === "admin" ? "admin" : "viewer";
  const issuedAt = Date.now();
  return `${safeRole}.${issuedAt}.${signPanelSession(safeRole, issuedAt)}`;
}

function panelSessionCookieHeader(role) {
  const value = encodeURIComponent(panelSessionCookieValue(role));
  const maxAge = Math.max(60, Math.floor(PANEL_SESSION_MAX_AGE_MS / 1000));
  return `${PANEL_SESSION_COOKIE}=${value}; Path=/; Max-Age=${maxAge}; HttpOnly; SameSite=Lax`;
}

function clearPanelSessionCookieHeader() {
  return `${PANEL_SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax`;
}

function authRoleFromPanelCookie(req) {
  if (!isAuthEnabled()) return "admin";
  const cookies = parseCookies(req);
  const raw = cookies[PANEL_SESSION_COOKIE];
  if (!raw) return "";
  const parts = String(raw).split(".");
  if (parts.length !== 3) return "";
  const role = parts[0] === "admin" ? "admin" : (parts[0] === "viewer" ? "viewer" : "");
  const issuedAt = Number(parts[1]);
  const signature = parts[2];
  if (!role || !Number.isFinite(issuedAt)) return "";
  if (Date.now() - issuedAt > PANEL_SESSION_MAX_AGE_MS || issuedAt - Date.now() > 60 * 1000) return "";
  const expected = signPanelSession(role, issuedAt);
  if (!timingSafeEqualText(signature, expected)) return "";
  return role;
}

function passwordOnlyLoginRole(password) {
  const pass = String(password || "");
  if (PANEL_VIEWER_PASSWORD !== "" && timingSafeEqualText(pass, PANEL_VIEWER_PASSWORD)) return "viewer";
  if (PANEL_PASSWORD !== "" && timingSafeEqualText(pass, PANEL_PASSWORD)) return "admin";
  return "";
}

function loginClientKey(req) {
  const forwarded = String(req && req.headers && req.headers["x-forwarded-for"] || "").split(",")[0].trim();
  const socketAddress = String(req && req.socket && req.socket.remoteAddress || "").trim();
  return forwarded || socketAddress || "unknown";
}

function pruneLoginFailureBuckets(nowMs) {
  for (const [key, bucket] of loginFailureBuckets.entries()) {
    if (!bucket || nowMs - Number(bucket.firstAt || 0) > LOGIN_RATE_WINDOW_MS) loginFailureBuckets.delete(key);
  }
}

function loginRateLimited(req) {
  const nowMs = Date.now();
  pruneLoginFailureBuckets(nowMs);
  const bucket = loginFailureBuckets.get(loginClientKey(req));
  return !!(bucket && Number(bucket.count || 0) >= LOGIN_RATE_MAX_FAILURES && nowMs - Number(bucket.firstAt || 0) <= LOGIN_RATE_WINDOW_MS);
}

function recordLoginFailure(req) {
  const nowMs = Date.now();
  const key = loginClientKey(req);
  const previous = loginFailureBuckets.get(key);
  if (!previous || nowMs - Number(previous.firstAt || 0) > LOGIN_RATE_WINDOW_MS) {
    loginFailureBuckets.set(key, { firstAt: nowMs, count: 1 });
    return;
  }
  previous.count = Math.max(0, Number(previous.count || 0) || 0) + 1;
  loginFailureBuckets.set(key, previous);
}

function clearLoginFailures(req) {
  loginFailureBuckets.delete(loginClientKey(req));
}

function authRoleForRequest(req) {
  if (!isAuthEnabled()) return "admin";
  const credentials = basicAuthCredentials(req);
  if (credentials) {
    if (PANEL_USER !== "" && PANEL_PASSWORD !== "" &&
      timingSafeEqualText(credentials.username, PANEL_USER) &&
      timingSafeEqualText(credentials.password, PANEL_PASSWORD)) {
      return "admin";
    }
    if (PANEL_VIEWER_USER !== "" && PANEL_VIEWER_PASSWORD !== "" &&
      timingSafeEqualText(credentials.username, PANEL_VIEWER_USER) &&
      timingSafeEqualText(credentials.password, PANEL_VIEWER_PASSWORD)) {
      return "viewer";
    }
    if (PANEL_VIEWER_PASSWORD !== "" && timingSafeEqualText(credentials.password, PANEL_VIEWER_PASSWORD)) {
      return "viewer";
    }
  }
  return authRoleFromPanelCookie(req);
}

function isAttackTxtPublicPath(pathname) {
  return pathname === "/attack-txt"
    || pathname === "/gongjiyu"
    || pathname === "/txt_attack_convert.js"
    || pathname === "/attack-txt-manifest.json"
    || pathname === "/attack-txt-icon.svg"
    || pathname === "/four_throw_urls.json";
}

function isAccuracyPath(pathname) {
  return pathname === "/accuracy" || pathname === "/accuracy.html";
}

function sendAccuracyHtml(req, res) {
  try {
    const html = fs.readFileSync(path.join(__dirname, "accuracy.html"), "utf8");
    sendTextForRequest(req, res, 200, html, "text/html; charset=utf-8");
  } catch (error) {
    sendJson(res, 500, { ok: false, error: String(error && error.message ? error.message : error) });
  }
}

function isViewerAllowedRequest(method, pathname) {
  if (method === "POST" && pathname === "/backdelete-generate") return true;
  if (method !== "GET") return false;
  if (
    pathname === "/claude"
    || pathname.indexOf("/claude/") === 0
    || pathname === "/claude-stable"
    || pathname.indexOf("/claude-stable/") === 0
    || pathname === "/claude-ensemble"
    || pathname.indexOf("/algorithm-project/") === 0
  ) {
    return true;
  }
  return (
    pathname === "/"
    || pathname === "/panel"
    || isAttackTxtPublicPath(pathname)
    || isAccuracyPath(pathname)
    || pathname === "/converter-history"
    || pathname === "/four-backdelete-opened"
    || pathname === "/backdelete-state"
    || pathname === "/state"
    || pathname === "/robot-state"
    || pathname === "/history"
    || pathname === "/health"
    || pathname === "/stable-math-filter"
    || pathname === "/exclusion-model"
    || pathname === "/shape-avoid-3"
    || pathname === "/four-factor-rank"
    || pathname === "/four-ding-shape-size"
    || pathname === "/four-ding-shape-parity"
    || pathname === "/four-ding-shape-kills"
    || pathname === "/shape-parity"
    || pathname === "/shape-size"
    || pathname === "/shape-parity-4"
    || pathname === "/shape-size-4"
    || pathname === "/claude-ensemble"
  );
}

function proxyLocalUpstream(req, res, upstreamBase, stripPrefix, options) {
  const opts = options && typeof options === "object" ? options : {};
  const silentFail = opts.silentFail === true;
  return new Promise((resolve) => {
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      resolve(ok);
    };
    try {
      const incoming = new URL(req.url || "/", "http://127.0.0.1");
      let suffix = incoming.pathname || "/";
      if (stripPrefix && suffix.indexOf(stripPrefix) === 0) {
        suffix = suffix.slice(stripPrefix.length) || "/";
      }
      if (!suffix.startsWith("/")) suffix = "/" + suffix;
      const target = new URL(upstreamBase + suffix + (incoming.search || ""));
      const headers = Object.assign({}, req.headers || {});
      delete headers.host;
      delete headers["content-length"];
      const proxyReq = http.request({
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port || 80,
        path: target.pathname + target.search,
        method: req.method || "GET",
        headers,
        timeout: Math.min(SERVER_REQUEST_TIMEOUT, 15000)
      }, (proxyRes) => {
        const outHeaders = Object.assign({}, proxyRes.headers || {});
        delete outHeaders.connection;
        delete outHeaders["transfer-encoding"];
        res.writeHead(proxyRes.statusCode || 502, outHeaders);
        proxyRes.pipe(res);
        proxyRes.on("end", () => finish(true));
        proxyRes.on("error", () => finish(false));
      });
      proxyReq.on("timeout", () => {
        try { proxyReq.destroy(new Error("upstream-timeout")); } catch (_) {}
      });
      proxyReq.on("error", () => {
        if (!silentFail && !res.headersSent) {
          sendJson(res, 502, { ok: false, error: "upstream-unreachable", upstream: upstreamBase });
        }
        finish(false);
      });
      if (req.method === "GET" || req.method === "HEAD") {
        proxyReq.end();
      } else {
        req.pipe(proxyReq);
      }
    } catch (error) {
      if (!silentFail && !res.headersSent) {
        sendJson(res, 500, { ok: false, error: String(error && error.message ? error.message : error) });
      }
      finish(false);
    }
  });
}

async function handleClaudeProxyRequest(req, res, pathname) {
  const isStable = pathname === "/claude-stable" || pathname.indexOf("/claude-stable/") === 0;
  const strip = isStable ? "/claude-stable" : "/claude";
  const upstream = isStable ? CLAUDE_STABLE_UPSTREAM : CLAUDE_ENSEMBLE_UPSTREAM;
  const stateFile = isStable ? CLAUDE_STABLE_STATE_FILE : CLAUDE_ENSEMBLE_STATE_FILE;
  const wantState = pathname === strip + "/state" || pathname === strip + "/state.json";
  const ok = await proxyLocalUpstream(req, res, upstream, strip, { silentFail: wantState });
  if (ok) return true;
  if (wantState && !res.headersSent) {
    const loaded = readAlgorithmProjectStateFile(stateFile);
    if (loaded.ok) {
      sendJson(res, 200, Object.assign({ ok: true, source: "state-file-fallback" }, loaded.data));
      return true;
    }
    sendJson(res, 404, { ok: false, error: (isStable ? "claude-stable" : "claude-ensemble") + " not ready" });
    return true;
  }
  if (!res.headersSent) {
    sendJson(res, 502, { ok: false, error: "upstream-unreachable", upstream });
  }
  return true;
}

function safeLoginNextPath(raw) {
  const text = String(raw || "").trim();
  if (text === "/attack-txt" || text === "/gongjiyu" || text === "/panel" || isAccuracyPath(text)) return text;
  return "/panel";
}

function sendPasswordLoginHtml(req, res, message, nextPath) {
  const next = safeLoginNextPath(nextPath || (req && req.url ? new URL(req.url, "http://localhost").searchParams.get("next") : "") || "/panel");
  const errorHtml = message ? `<div class="error">${String(message).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]))}</div>` : "";
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>五路公交数据库</title>
  <style>
    html,body{margin:0;min-height:100%;font-family:"Songti SC","STSong","Noto Serif SC","Microsoft YaHei",sans-serif;background:
      radial-gradient(900px 360px at 20% 0%,rgba(245,158,11,.18),transparent 55%),
      radial-gradient(800px 320px at 90% 10%,rgba(30,58,138,.12),transparent 50%),
      #0b1220;color:#f8fafc;}
    body{display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box;}
    .box{width:100%;max-width:380px;background:linear-gradient(180deg,rgba(255,255,255,.98),#fff);border:1px solid rgba(245,158,11,.45);border-radius:16px;padding:28px 24px;box-shadow:0 18px 48px rgba(0,0,0,.35);}
    h1{font-size:28px;line-height:1.25;margin:0 0 6px;font-weight:900;text-align:center;letter-spacing:.16em;color:#0f172a;}
    h1 .mark{color:#d97706;margin-right:.08em;}
    .sub{text-align:center;font-size:12px;font-weight:700;letter-spacing:.28em;color:#b45309;margin:0 0 18px;font-family:"Microsoft YaHei",sans-serif;}
    label{display:block;font-size:15px;font-weight:800;margin:0 0 8px;color:#0f172a;font-family:"Microsoft YaHei",sans-serif;}
    input{width:100%;height:48px;border:1px solid #f59e0b;border-radius:10px;padding:0 12px;font-size:20px;box-sizing:border-box;outline:none;font-family:"Microsoft YaHei",sans-serif;}
    input:focus{border-color:#d97706;box-shadow:0 0 0 3px rgba(245,158,11,.22);}
    button{width:100%;height:48px;margin-top:14px;border:0;border-radius:10px;background:linear-gradient(180deg,#f59e0b,#d97706);color:#111827;font-size:18px;font-weight:900;cursor:pointer;font-family:"Microsoft YaHei",sans-serif;}
    button:disabled{background:#94a3b8;cursor:default;}
    .error{margin:0 0 12px;padding:10px 12px;border-radius:10px;background:#fee2e2;color:#991b1b;font-weight:800;font-family:"Microsoft YaHei",sans-serif;}
    .hint{margin-top:12px;color:#64748b;font-size:13px;line-height:1.5;text-align:center;font-family:"Microsoft YaHei",sans-serif;}
  </style>
</head>
<body>
  <main class="box">
    <h1><span class="mark">◆</span>五路公交数据库</h1>
    <div class="sub">五路数据 · 公交查询</div>
    ${errorHtml}
    <form id="loginForm" autocomplete="on">
      <label for="panelPassword">密码</label>
      <input id="panelPassword" name="password" type="password" autocomplete="current-password" autofocus required>
      <button id="loginBtn" type="submit">进入</button>
      <div id="loginMsg" class="hint">五路公交数据库，请输入密码进入。</div>
    </form>
  </main>
  <script>
    document.getElementById("loginForm").addEventListener("submit", async function(event){
      event.preventDefault();
      var btn=document.getElementById("loginBtn");
      var msg=document.getElementById("loginMsg");
      var password=document.getElementById("panelPassword").value;
      btn.disabled=true;
      msg.textContent="正在验证...";
      try{
        var res=await fetch("/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password:password})});
        if(!res.ok){ msg.textContent="密码不对"; btn.disabled=false; return; }
        var loginData=await res.json();
        // Defer PBKDF2(120k) to panel boot to avoid UI freeze on Enter
        try{
          if(loginData&&loginData.role==="admin"){
            sessionStorage.setItem("penta5_qp_seal_pw_tmp", password);
          }else{
            sessionStorage.removeItem("penta5_qp_seal_pw_tmp");
            sessionStorage.removeItem("penta5_qp_seal_key");
          }
        }catch(sealStashErr){}
        location.replace(${JSON.stringify(next)});
      }catch(err){
        msg.textContent="网络异常，请重试";
        btn.disabled=false;
      }
    });
  </script>
</body>
</html>`;
  sendTextForRequest(req, res, 200, html, "text/html; charset=utf-8");
}

async function handlePasswordLogin(req, res) {
  try {
    if (loginRateLimited(req)) {
      sendJson(res, 429, { ok: false, error: "too many login attempts" });
      return;
    }
    const raw = await readRequestBody(req);
    let password = "";
    try {
      const parsed = JSON.parse(raw || "{}");
      password = parsed && parsed.password !== undefined ? parsed.password : "";
    } catch (err) {
      const form = new URLSearchParams(raw || "");
      password = form.get("password") || "";
    }
    const role = passwordOnlyLoginRole(password);
    if (!role) {
      recordLoginFailure(req);
      sendJsonText(res, 401, safeJsonStringify({ ok: false, error: "bad password" }));
      return;
    }
    clearLoginFailures(req);
    const text = safeJsonStringify({ ok: true, role });
    res.writeHead(200, commonJsonHeaders({
      "Set-Cookie": panelSessionCookieHeader(role),
      "Content-Length": String(Buffer.byteLength(text, "utf8"))
    }));
    res.end(text);
  } catch (err) {
    sendJson(res, 400, { ok: false, error: "login failed" });
  }
}

function handleLogout(req, res) {
  const html = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>退出</title></head>
<body><script>
try{sessionStorage.removeItem("penta5_qp_seal_key");}catch(e){}
location.replace("/login");
</script></body></html>`;
  res.writeHead(200, {
    "Content-Type": "text/html; charset=utf-8",
    "Set-Cookie": clearPanelSessionCookieHeader(),
    "Cache-Control": "no-store",
    "Connection": "close"
  });
  res.end(html);
}

function sendUnauthorized(res) {
  res.writeHead(401, {
    "Content-Type": "text/plain; charset=utf-8",
    "Cache-Control": "no-store",
    "Connection": "close",
    "X-Content-Type-Options": "nosniff"
  });
  res.end("Unauthorized");
}

function sendForbidden(res) {
  res.writeHead(403, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Connection": "close",
    "X-Content-Type-Options": "nosniff"
  });
  res.end(JSON.stringify({ ok: false, error: "Forbidden: admin permission required" }));
}

async function handleRequest(req, res) {
  req.setTimeout(SERVER_REQUEST_TIMEOUT, () => {
    req.destroy();
  });
  if (req.method === "OPTIONS") {
    sendText(res, 204, "");
    return;
  }
  const parsedUrl = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const pathname = parsedUrl.pathname;
  if (req.method === "GET" && pathname === "/login") {
    sendPasswordLoginHtml(req, res, "");
    return;
  }
  if (req.method === "POST" && pathname === "/login") {
    await handlePasswordLogin(req, res);
    return;
  }
  if (req.method === "GET" && pathname === "/logout") {
    handleLogout(req, res);
    return;
  }
  const authRole = authRoleForRequest(req);
  // /health 免鉴权：监督探针不能依赖面板登录，且必须极轻
  if (req.method === "GET" && pathname === "/health") {
    const latest = state && state.latestRecord ? state.latestRecord : {};
    sendJson(res, 200, {
      ok: !state.lastError,
      time: isoNow(),
      syncing: syncRunning,
      predictionBuildRunning,
      tipReady: state && state.tipReady === true,
      latestPeriod: latest.period || latest.uid || "",
      frameworkBackfillEnabled: FRAMEWORK_BACKFILL_ENABLED,
      frameworkSimEnabled: FRAMEWORK_SIM_ENABLED,
      summary: buildHealthSummary({ lite: true })
    });
    return;
  }
  // 本机救机器人完整包下载（免登录；支持 GET/HEAD，避免浏览器预检无响应）
  if (
    (req.method === "GET" || req.method === "HEAD") &&
    (pathname === "/download/jiu-jiqiren.zip" ||
      pathname === "/jiu-jiqiren.zip" ||
      pathname === "/download/救机器人.zip")
  ) {
    const zipPath = path.join(__dirname, "downloads", "jiu-jiqiren.zip");
    if (!fs.existsSync(zipPath)) {
      sendJson(res, 404, { ok: false, error: "package not found" });
      return;
    }
    const st = fs.statSync(zipPath);
    res.writeHead(200, {
      "Content-Type": "application/zip",
      "Content-Length": st.size,
      "Content-Disposition": 'attachment; filename="jiu-jiqiren.zip"',
      "Cache-Control": "no-store",
      "Accept-Ranges": "bytes"
    });
    if (req.method === "HEAD") {
      res.end();
      return;
    }
    fs.createReadStream(zipPath).pipe(res);
    return;
  }
  // 四定扔页本机包（免登录）
  if (
    (req.method === "GET" || req.method === "HEAD") &&
    (pathname === "/download/four-throw-local.zip" ||
      pathname === "/four-throw-local.zip" ||
      pathname === "/download/四定扔页-本机包.zip" ||
      pathname === "/四定扔页-本机包.zip")
  ) {
    const zipPath = path.join(__dirname, "downloads", "four-throw-local.zip");
    if (!fs.existsSync(zipPath)) {
      sendJson(res, 404, { ok: false, error: "package not found" });
      return;
    }
    const st = fs.statSync(zipPath);
    res.writeHead(200, {
      "Content-Type": "application/zip",
      "Content-Length": st.size,
      "Content-Disposition": 'attachment; filename="four-throw-local.zip"',
      "Cache-Control": "no-store",
      "Accept-Ranges": "bytes"
    });
    if (req.method === "HEAD") {
      res.end();
      return;
    }
    fs.createReadStream(zipPath).pipe(res);
    return;
  }
  if (!authRole) {
    if (req.method === "GET" && (pathname === "/" || pathname === "/panel")) {
      sendPasswordLoginHtml(req, res, "", "/panel");
      return;
    }
    if (req.method === "GET" && isAttackTxtPublicPath(pathname)) {
      // 静态资源需登录后同会话访问；未登录统一进登录页，回跳攻击语页
      if (pathname === "/attack-txt" || pathname === "/gongjiyu") {
        sendPasswordLoginHtml(req, res, "", "/attack-txt");
        return;
      }
      // 三个网址占位配置可匿名读（内容默认全空，不含密钥）
      if (pathname === "/four_throw_urls.json") {
        sendAttackTxtAsset(req, res, pathname);
        return;
      }
      sendUnauthorized(res);
      return;
    }
    if (req.method === "GET" && isAccuracyPath(pathname)) {
      sendPasswordLoginHtml(req, res, "", "/accuracy");
      return;
    }
    sendUnauthorized(res);
    return;
  }
  if (authRole !== "admin" && !isViewerAllowedRequest(req.method, pathname)) {
    sendForbidden(res);
    return;
  }
  try {
    if (req.method === "GET" && pathname === "/health") {
      // 上面已免鉴权返回；此处保留兼容，正常不会走到
      const latest = state && state.latestRecord ? state.latestRecord : {};
      sendJson(res, 200, {
        ok: !state.lastError,
        time: isoNow(),
        syncing: syncRunning,
        predictionBuildRunning,
        tipReady: state && state.tipReady === true,
        latestPeriod: latest.period || latest.uid || "",
        frameworkBackfillEnabled: FRAMEWORK_BACKFILL_ENABLED,
        frameworkSimEnabled: FRAMEWORK_SIM_ENABLED,
        summary: buildHealthSummary({ lite: true })
      });
      return;
    }
    if (req.method === "GET" && pathname === "/state") {
      sendPanelState(req, res);
      return;
    }
    if (req.method === "GET" && pathname === "/robot-state") {
      sendJson(res, 200, buildRobotStatePayload());
      return;
    }
    if (req.method === "GET" && pathname === "/history") {
      sendSharedHistory(req, res, parsedUrl);
      return;
    }
    if (req.method === "GET" && pathname === "/converter-history") {
      try { req.setTimeout(SYNC_REQUEST_TIMEOUT); } catch (_) {}
      try { res.setTimeout(SYNC_REQUEST_TIMEOUT); } catch (_) {}
      await sendConverterHistory(req, res, parsedUrl);
      return;
    }
    if (req.method === "GET" && pathname === "/four-backdelete-opened") {
      // 四定回删：直代理 RDS API 轻量接口，不读 runner 内存历史、不拉 converter-history 大包。
      try { req.setTimeout(20000); } catch (_) {}
      try { res.setTimeout(20000); } catch (_) {}
      try {
        const limit = Math.min(1200, Math.max(1, Number(parsedUrl.searchParams.get("limit") || 1200) || 1200));
        const upstream = await fetchJsonUrlSync(
          `${RDS_API_BASE}/four-backdelete-opened?limit=${limit}&nocache=1&_=${Date.now()}`,
          18000
        );
        const body = upstream && upstream.body ? upstream.body : {};
        const status = Number(upstream && upstream.status || 0) || (body && body.ok === true ? 200 : 503);
        res.writeHead(status, {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "no-store, no-cache, must-revalidate",
          Pragma: "no-cache"
        });
        res.end(JSON.stringify(body));
      } catch (error) {
        sendJson(res, 503, {
          ok: false,
          error: "四定回删直查 RDS 失败：" + String(error && error.message ? error.message : error),
          source: "rds-direct-proxy"
        });
      }
      return;
    }
    if (req.method === "GET" && pathname === "/qualified-picks") {
      sendQualifiedPicks(res, authRole, req);
      return;
    }
    if (req.method === "GET" && pathname === "/qualified-picks/verify") {
      sendQualifiedPicksVerify(res, authRole, req);
      return;
    }
    if (req.method === "GET" && (
      pathname === "/claude"
      || pathname.indexOf("/claude/") === 0
      || pathname === "/claude-stable"
      || pathname.indexOf("/claude-stable/") === 0
    )) {
      await handleClaudeProxyRequest(req, res, pathname);
      return;
    }
    if (req.method === "GET" && (
      pathname === "/shape-avoid-3"
      || pathname === "/four-factor-rank"
      || pathname === "/four-ding-shape-size"
      || pathname === "/four-ding-shape-parity"
      || pathname === "/four-ding-shape-kills"
        || pathname === "/shape-parity"
      || pathname === "/shape-size"
      || pathname === "/shape-parity-4"
      || pathname === "/shape-size-4"
      || pathname === "/claude-ensemble"
      || pathname.indexOf("/algorithm-project/") === 0
    )) {
      try {
        if (pathname === "/four-ding-shape-kills") {
          const size = readAlgorithmProjectStateFile(FOUR_DING_SHAPE_SIZE_STATE_FILE);
          const parity = readAlgorithmProjectStateFile(FOUR_DING_SHAPE_PARITY_STATE_FILE);
          if (!size.ok && !parity.ok) {
            sendJson(res, 404, { ok: false, error: "four-ding-shape-kills not ready" });
            return;
          }
          const latestPeriod = (size.ok && size.data && size.data.latestPeriod)
            || (parity.ok && parity.data && parity.data.latestPeriod)
            || null;
          sendJson(res, 200, {
            ok: true,
            id: "four-ding-shape-kills",
            kind: "fourDingShapeKillBundle",
            latestPeriod,
            size: size.ok ? size.data : null,
            parity: parity.ok ? parity.data : null,
            sizeError: size.ok ? null : size.error,
            parityError: parity.ok ? null : parity.error
          });
          return;
        }
        let projectId = "";
        let stateFile = "";
        if (pathname.indexOf("/algorithm-project/") === 0) {
          projectId = String(pathname.slice("/algorithm-project/".length) || "").replace(/[^a-zA-Z0-9._-]/g, "");
          if (!projectId) {
            sendJson(res, 400, { ok: false, error: "missing-project-id" });
            return;
          }
          stateFile = path.join(DATA_DIR, "algorithm-projects", projectId, "state.json");
        } else {
          const fileMap = {
            "/shape-avoid-3": SHAPE_AVOID_3_STATE_FILE,
            "/four-factor-rank": FOUR_FACTOR_RANK_STATE_FILE,
            "/four-ding-shape-size": FOUR_DING_SHAPE_SIZE_STATE_FILE,
            "/four-ding-shape-parity": FOUR_DING_SHAPE_PARITY_STATE_FILE,
            "/shape-parity": path.join(DATA_DIR, "algorithm-projects", "shape-parity", "state.json"),
            "/shape-size": path.join(DATA_DIR, "algorithm-projects", "shape-size", "state.json"),
            "/shape-parity-4": path.join(DATA_DIR, "algorithm-projects", "shape-parity-4", "state.json"),
            "/shape-size-4": path.join(DATA_DIR, "algorithm-projects", "shape-size-4", "state.json"),
            "/claude-ensemble": CLAUDE_ENSEMBLE_STATE_FILE
          };
          stateFile = fileMap[pathname];
          projectId = pathname.slice(1);
        }
        const loaded = readAlgorithmProjectStateFile(stateFile);
        if (!loaded.ok) {
          sendJson(res, 404, { ok: false, error: loaded.error || (projectId + " not ready") });
          return;
        }
        const runnerLatest = state && state.latestRecord
          ? String(state.latestRecord.period || state.latestRecord.uid || "")
          : "";
        const projectLatest = loaded.data && loaded.data.latestPeriod != null
          ? String(loaded.data.latestPeriod)
          : "";
        const lagging = !!(runnerLatest && projectLatest && runnerLatest !== projectLatest);
        sendJson(res, 200, Object.assign({
          ok: true,
          projectId,
          runnerLatestPeriod: runnerLatest || null,
          periodAligned: !lagging,
          retiredHint: lagging && /^(shape-parity|shape-size|shape-parity-4|shape-size-4)$/.test(projectId)
            ? "legacy-shape-model-no-sidecar"
            : null
        }, loaded.data));
      } catch (error) {
        sendJson(res, 500, { ok: false, error: String(error && error.message ? error.message : error) });
      }
      return;
    }
    if (req.method === "GET" && pathname === "/backdelete-state") {
      sendBackdeleteState(res);
      return;
    }
    if (req.method === "POST" && pathname === "/backdelete-generate") {
      try { req.setTimeout(SYNC_REQUEST_TIMEOUT); } catch (_) {}
      try { res.setTimeout(SYNC_REQUEST_TIMEOUT); } catch (_) {}
      await generateBackdeleteSnapshot(req, res);
      return;
    }
    if (req.method === "GET" && pathname === "/stable-math-filter") {
      if ((parsedUrl.searchParams.get("refresh") === "1" || parsedUrl.searchParams.get("refresh") === "true") && authRole !== "admin") {
        sendForbidden(res);
        return;
      }
      await handleStableMathFilterRequest(req, res, parsedUrl);
      return;
    }
    if (req.method === "GET" && pathname === "/exclusion-model") {
      if ((parsedUrl.searchParams.get("refresh") === "1" || parsedUrl.searchParams.get("refresh") === "true") && authRole !== "admin") {
        sendForbidden(res);
        return;
      }
      await handleExclusionModelRequest(req, res, parsedUrl);
      return;
    }
    if (req.method === "POST" && pathname === "/backup") {
      const file = makeBackup();
      sendJson(res, 200, { ok: true, backupFile: file, createdAt: isoNow() });
      return;
    }
    if (req.method === "POST" && pathname === "/force-regenerate-tip") {
      if (authRole !== "admin") {
        sendForbidden(res);
        return;
      }
      try { req.setTimeout(SYNC_REQUEST_TIMEOUT); } catch (_) {}
      try { res.setTimeout(SYNC_REQUEST_TIMEOUT); } catch (_) {}
      let payload = {};
      try {
        const raw = await readRequestBody(req);
        payload = raw ? JSON.parse(raw || "{}") : {};
      } catch (_) {
        payload = {};
      }
      const result = forceRegenerateTipForAdmin({
        clearPositionLocks: payload.clearPositionLocks !== false
      });
      sendJson(res, result && result.ok ? 200 : (result && result.error === "sync-running" ? 409 : 500), result);
      return;
    }
    if (req.method === "POST" && pathname === "/sync") {
      // 拉长本请求超时，避免开奖窗同步被 15s 硬掐（监督 L2 误报 Remote end closed）
      try { req.setTimeout(SYNC_REQUEST_TIMEOUT); } catch (_) {}
      try { res.setTimeout(SYNC_REQUEST_TIMEOUT); } catch (_) {}
      let syncTrigger = "manual";
      try {
        const raw = await readRequestBody(req);
        const payload = raw ? JSON.parse(raw || "{}") : {};
        const want = String(payload && payload.trigger || "").trim();
        // panel-auto：开奖窗前端自动踢同步；勿当 manual，避免每次都即时整包换码
        if (want === "panel-auto" || want === "auto" || want === "auto-draw") syncTrigger = "panel-auto";
        else if (want === "manual") syncTrigger = "manual";
        else if (want === "supervisor") syncTrigger = "panel-auto";
      } catch (_) {
        syncTrigger = "manual";
      }
      const result = await syncNow(syncTrigger);
      sendJson(res, result.ok === false ? 500 : 200, result);
      return;
    }
    if (req.method === "POST" && pathname === "/daily-streak-ledger") {
      const raw = await readRequestBody(req);
      const payload = JSON.parse(raw || "{}");
      const merged = mergeDailyStreakStats(payload.storeName, payload.day, payload.stats, payload.source || "panel");
      if (!merged) {
        sendJson(res, 400, { ok: false, error: "invalid daily streak payload" });
        return;
      }
      saveState();
      sendJson(res, 200, { ok: true, storeName: payload.storeName, day: merged.day, stats: merged });
      return;
    }
    if (req.method === "POST" && pathname === "/backfill-kill-framework") {
      const raw = await readRequestBody(req);
      const payload = JSON.parse(raw || "{}");
      if (payload && payload.resetCompleted === true && state.killFrameworkBackfill) {
        state.killFrameworkBackfill.completed = false;
      }
      const result = backfillKillFrameworkEvaluations({
        limit: Number(payload && payload.limit || 200),
        comboWindow: Number(payload && payload.comboWindow || 80),
        allowUnhealthy: payload && payload.allowUnhealthy !== false
      });
      if (result && result.ok) saveState();
      const metrics = savedSnapshotTruthMetrics("killFramework", KILL_FRAMEWORK_SNAPSHOT_VERSION, KILL_FRAMEWORK_ITEM_COUNT, MODEL_STABLE_WINDOW);
      sendJson(res, result && result.ok === false ? 500 : 200, { ...result, truthMetrics: metrics || null });
      return;
    }
    if (req.method === "GET" && (pathname === "/" || pathname === "/panel")) {
      sendPanelHtml(req, res);
      return;
    }
    if (req.method === "GET" && isAccuracyPath(pathname)) {
      sendAccuracyHtml(req, res);
      return;
    }
    if (req.method === "GET" && isAttackTxtPublicPath(pathname)) {
      sendAttackTxtAsset(req, res, pathname);
      return;
    }
    sendJson(res, 404, { ok: false, error: "Not Found", endpoints: ["GET /health", "GET /state", "GET /robot-state", "GET /history", "GET /converter-history", "GET /four-backdelete-opened", "GET /backdelete-state", "POST /backdelete-generate", "GET /stable-math-filter", "GET /exclusion-model", "GET /attack-txt", "GET /accuracy", "POST /sync", "POST /force-regenerate-tip", "POST /daily-streak-ledger", "POST /backfill-kill-framework", "POST /backup"] });
  } catch (err) {
    sendJson(res, 500, { ok: false, error: err.message || String(err) });
  }
}

function startServer() {
  state = loadState();
  try { bindAlgorithmOrchestrators(); } catch (err) {
    console.error("[orchestrate] bind failed", err && err.message ? err.message : err);
  }
  addLog("后台常驻服务启动", {
    port: PORT,
    sourceApiUrl: SOURCE_API_URL,
    stateFile: STATE_FILE,
    autoRefreshMs: AUTO_REFRESH_INTERVAL,
    orchestrateBound: algorithmOrchestratorsBound,
    algoProcessMode: String(process.env.PENTA5_ALGO_PROCESS_MODE || "0")
  });
  state.updatedAt = state.updatedAt || isoNow();




// CODEX_SERVER_PERCENT_0400_BEGIN
const CODEX_PERCENT_FILE = path.join(DATA_DIR, "codex_percent_server.json");
const CODEX_PERCENT_VERSION = "CODEX_SERVER_PERCENT_0410_RDS_LIVE";
const CODEX_PERCENT_SOURCE = "rds-live-memory-v1";
const CODEX_PERCENT_HISTORY_LIMIT = 1000;
const CODEX_PERCENT_POSITIONS = [
  { key: "qian", label: "千位", index: 0 },
  { key: "bai", label: "百位", index: 1 },
  { key: "shi", label: "十位", index: 2 },
  { key: "ge", label: "个位", index: 3 },
];
const CODEX_DIGITS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];

function codexPercentReadJson(file, fallback) {
  try {
    if (!fs.existsSync(file)) return fallback;
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    return fallback;
  }
}

function codexPercentWriteJson(file, data) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(data, null, 2), "utf8");
  } catch (error) {
    console.error("[CodexPercent] write failed", error && error.message ? error.message : error);
  }
}

function codexPercentDigit(value) {
  const m = String(value == null ? "" : value).match(/[0-9]/);
  return m ? m[0] : "";
}

function codexPercentNumbers(value) {
  if (Array.isArray(value)) return value.map(codexPercentDigit).filter(Boolean).slice(0, 5);
  if (typeof value === "string") return value.split("").map(codexPercentDigit).filter(Boolean).slice(0, 5);
  return [];
}

function codexPercentRecordFromObject(obj) {
  if (!obj || typeof obj !== "object") return null;
  const rawNumbers = obj.numbers || obj.openCode || obj.code || obj.result || obj.latestNumbers;
  const numbers = codexPercentNumbers(rawNumbers);
  if (numbers.length < 4) return null;
  const period = obj.period || obj.issue || obj.uid || obj.latestPeriod || obj.expect || obj.qihao;
  if (!period) return null;
  return {
    period: String(period),
    openTime: String(obj.openTime || obj.time || obj.datetime || obj.text || obj.at || ""),
    numbers,
  };
}

function codexPercentCollectRecords(value, out, seen, depth) {
  if (!value || depth > 6) return;
  if (Array.isArray(value)) {
    for (const item of value) codexPercentCollectRecords(item, out, seen, depth + 1);
    return;
  }
  if (typeof value !== "object") return;
  const rec = codexPercentRecordFromObject(value);
  if (rec && !seen.has(rec.period)) {
    seen.add(rec.period);
    out.push(rec);
  }
  for (const key of Object.keys(value)) {
    if (key === "numbers" || key === "openCode" || key === "code" || key === "result") continue;
    const child = value[key];
    if (child && (Array.isArray(child) || typeof child === "object")) {
      codexPercentCollectRecords(child, out, seen, depth + 1);
    }
  }
}

function codexPercentSortPeriod(a, b) {
  const na = Number(String(a.period).replace(/\D/g, ""));
  const nb = Number(String(b.period).replace(/\D/g, ""));
  if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return na - nb;
  return String(a.period).localeCompare(String(b.period));
}

function codexPercentGetRecords(state) {
  const officialHistory = state && Array.isArray(state.history) ? state.history : [];
  const out = [];
  const seen = new Set();
  officialHistory.forEach((item) => {
    const record = codexPercentRecordFromObject(item);
    if (!record || !record.period || record.period === "--" || seen.has(record.period)) return;
    seen.add(record.period);
    out.push(record);
  });
  out.sort(codexPercentSortPeriod);
  return out;
}

function codexPercentPickByRule(records, posIndex, rule) {
  const windowSize = rule === "cold24" ? 24 : rule === "hot8" ? 8 : rule === "gap" ? 50 : 16;
  const recent = records.slice(-windowSize);
  if (!recent.length) return null;
  const counts = Object.create(null);
  const lastSeen = Object.create(null);
  for (const digit of CODEX_DIGITS) {
    counts[digit] = 0;
    lastSeen[digit] = 999;
  }
  recent.forEach((record, idx) => {
    const digit = record.numbers[posIndex];
    if (digit == null) return;
    counts[digit] = (counts[digit] || 0) + 1;
    lastSeen[digit] = recent.length - 1 - idx;
  });
  const sorted = CODEX_DIGITS.slice().sort((a, b) => {
    if (rule === "hot8") return (counts[b] - counts[a]) || a.localeCompare(b);
    if (rule === "gap") return (lastSeen[b] - lastSeen[a]) || (counts[a] - counts[b]) || a.localeCompare(b);
    return (counts[a] - counts[b]) || (lastSeen[b] - lastSeen[a]) || a.localeCompare(b);
  });
  return sorted[0] || null;
}

function codexPercentBacktest(records, posIndex, rule) {
  let total = 0;
  let pass = 0;
  for (let i = 30; i < records.length; i += 1) {
    const train = records.slice(Math.max(0, i - 80), i);
    const digit = codexPercentPickByRule(train, posIndex, rule);
    const actual = records[i].numbers[posIndex];
    if (actual === undefined || actual === null || actual === "") continue;
    total += 1;
    if (actual !== digit) pass += 1;
  }
  return { total, pass, rate: total ? pass / total : null };
}

function codexPercentBuildPrediction(records) {
  const latest = records[records.length - 1] || null;
  const train = latest ? records.slice(0, -1) : records.slice();
  const rules = ["cold16", "cold24", "hot8", "gap"];
  const exclusions = { qian: [], bai: [], shi: [], ge: [] };
  const models = [];
  if (!latest || train.length < 30) {
    return {
      available: false,
      reason: "暂无真实回测",
      afterPeriod: latest ? latest.period : "",
      afterOpenTime: latest ? latest.openTime : "",
      exclusions,
      models,
      savedAt: new Date().toISOString()
    };
  }
  for (const pos of CODEX_PERCENT_POSITIONS) {
    const latestDigit = latest && Array.isArray(latest.numbers) ? latest.numbers[pos.index] : null;
    const candidates = rules.map((rule) => {
      const stat = codexPercentBacktest(train, pos.index, rule);
      const digit = codexPercentPickByRule(train.slice(-90), pos.index, rule);
      const sampleTotal = Number(stat.total) || 0;
      const samplePass = Number(stat.pass) || 0;
      const sampleRate = sampleTotal > 0 ? samplePass / sampleTotal : 0;
      const z = 1.96;
      const z2 = z * z;
      const denominator = 1 + z2 / sampleTotal;
      const centre = sampleRate + z2 / (2 * sampleTotal);
      const margin = z * Math.sqrt(
        (sampleRate * (1 - sampleRate) + z2 / (4 * sampleTotal)) /
        sampleTotal
      );
      const confidence = sampleTotal > 0
        ? (centre - margin) / denominator
        : 0;

      return {
        rule,
        digit,
        total: stat.total,
        pass: stat.pass,
        rate: stat.rate,
        confidence
      };
    }).filter((item) =>
        item.digit !== null &&
        item.rate !== null &&
        item.total >= 20
      )
      .sort((a, b) =>
        (b.rate - a.rate) ||
        (b.total - a.total) ||
        a.digit.localeCompare(b.digit)
      );

    const picked =
      candidates.find((item) => item.digit !== latestDigit) ||
      candidates[0] ||
      null;
    if (!picked) continue;
    exclusions[pos.key] = [picked.digit];
    models.push({
      key: "codex-server-" + pos.key,
      posKey: pos.key,
      label: pos.label,
      digit: picked.digit,
      rule: picked.rule,
      total: picked.total,
      pass: picked.pass,
      rate: picked.rate,
    });
  }
  return {
    available: models.length === CODEX_PERCENT_POSITIONS.length,
    reason: models.length === CODEX_PERCENT_POSITIONS.length ? "" : "暂无真实回测",
    afterPeriod: latest ? latest.period : "",
    afterOpenTime: latest ? latest.openTime : "",
    exclusions,
    models,
    savedAt: new Date().toISOString(),
    note: "服务器端自动记录，最新开奖号不参与训练，关电脑不影响",
  };
}

function codexPercentEvaluate(prediction, record) {
  if (!prediction || prediction.available !== true || !prediction.afterPeriod || !record || !record.period) return null;
  const detail = {};
  let overallPass = true;
  for (const pos of CODEX_PERCENT_POSITIONS) {
    const actual = record.numbers[pos.index] || "";
    const excluded = prediction.exclusions && prediction.exclusions[pos.key] ? prediction.exclusions[pos.key].slice() : [];
    const hit = excluded.includes(actual);
    detail[pos.key] = { label: pos.label, actual, excluded, pass: !hit, hit };
    if (hit) overallPass = false;
  }
  return {
    id: String(prediction.afterPeriod || "") + "->" + String(record.period || ""),
    modelAfterPeriod: String(prediction.afterPeriod || ""),
    resultPeriod: String(record.period || ""),
    resultOpenTime: record.openTime || "",
    numbers: record.numbers.slice(0, 5),
    modelNumbers: record.numbers.slice(0, 4),
    ball5Display: record.numbers[4] === undefined ? null : record.numbers[4],
    excludedFromModel: true,
    detail,
    overallPass,
    checkedAt: new Date().toISOString(),
  };
}

function codexPercentStats(history, limit) {
  const items = history.filter((item) => item && item.modelAfterPeriod && item.resultPeriod && typeof item.overallPass === "boolean").slice(0, limit);
  const total = items.length;
  const overallOk = items.filter((item) => item.overallPass).length;
  const positions = {};
  for (const pos of CODEX_PERCENT_POSITIONS) {
    const ok = items.filter((item) => item.detail && item.detail[pos.key] && item.detail[pos.key].pass).length;
    positions[pos.key] = { ok, total, rate: total ? ok / total : null };
  }
  return { total, overallOk, overallRate: total ? overallOk / total : null, positions, truthAvailable: total > 0 };
}

function codexPercentTick() {
  // state.json intentionally omits history.  Read the live in-memory history
  // hydrated from the shared RDS source; never serve a stale disk fallback.
  const liveState = state && Array.isArray(state.history) && state.history.length
    ? state
    : null;
  const records = codexPercentGetRecords(liveState);
  if (!records.length) {
    return {
      ok: false,
      version: CODEX_PERCENT_VERSION,
      source: CODEX_PERCENT_SOURCE,
      error: "shared-rds-history-not-ready",
      history: []
    };
  }
  const latest = records[records.length - 1];
  const store = codexPercentReadJson(CODEX_PERCENT_FILE, { version: CODEX_PERCENT_VERSION, history: [], pending: null });
  if (store.source !== CODEX_PERCENT_SOURCE || store.version !== CODEX_PERCENT_VERSION) {
    store.history = [];
    store.pending = null;
  }
  if (!Array.isArray(store.history)) store.history = [];

  // CODEX_SERVER_PERCENT_BACKFILL_0401_BEGIN
  if (store.history.length < CODEX_PERCENT_HISTORY_LIMIT && records.length >= 36) {
    const startIndex = Math.max(31, records.length - CODEX_PERCENT_HISTORY_LIMIT);
    const backfilled = [];
    for (let i = startIndex; i < records.length; i += 1) {
      const trainRecords = records.slice(0, i);
      if (trainRecords.length < 31) continue;
      const prediction = codexPercentBuildPrediction(trainRecords);
      if (!prediction || prediction.available !== true) continue;
      const evaluated = codexPercentEvaluate(prediction, records[i]);
      if (!evaluated) continue;
      backfilled.unshift(evaluated);
    }
    const mergedHistory = [];
    const seenHistoryIds = new Set();
    for (const item of backfilled.concat(store.history)) {
      if (!item || !item.id || seenHistoryIds.has(item.id)) continue;
      seenHistoryIds.add(item.id);
      mergedHistory.push(item);
    }
    if (mergedHistory.length >= store.history.length) {
      store.history = mergedHistory.slice(0, CODEX_PERCENT_HISTORY_LIMIT);
    }
  }
  // CODEX_SERVER_PERCENT_BACKFILL_0401_END

  if (store.pending && store.pending.available === true && store.pending.afterPeriod && String(store.pending.afterPeriod) !== String(latest.period)) {
    const evaluated = codexPercentEvaluate(store.pending, latest);
    if (evaluated && !store.history.some((item) => item.id === evaluated.id)) {
      store.history.unshift(evaluated);
      store.history = store.history.slice(0, CODEX_PERCENT_HISTORY_LIMIT);
    }
  }

  if (!store.pending || String(store.pending.afterPeriod) !== String(latest.period)) {
    store.pending = codexPercentBuildPrediction(records);
  }

  store.ok = true;
  store.version = CODEX_PERCENT_VERSION;
  store.source = CODEX_PERCENT_SOURCE;
  store.updatedAt = new Date().toISOString();
  store.latestRecord = latest;
  store.latestPeriod = latest.period;
  store.historyCount = store.history.length;
  store.stats20 = codexPercentStats(store.history, 20);
  store.stats50 = codexPercentStats(store.history, 50);
  store.stats100 = codexPercentStats(store.history, 100);
  store.stats200 = codexPercentStats(store.history, 200);
  store.stats500 = codexPercentStats(store.history, 500);
  store.stats1000 = codexPercentStats(store.history, 1000);
  delete store.baseline;
  codexPercentWriteJson(CODEX_PERCENT_FILE, store);
  return store;
}

function codexPercentSendJson(res, data) {
  res.writeHead(200, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store"
  });
  res.end(JSON.stringify(data));
}

function codexPercentSendScript(res, data) {
  const json = JSON.stringify(data).replace(/<\/script/gi, "<\\/script");
  res.writeHead(200, {
    "Content-Type": "application/javascript; charset=utf-8",
    "Cache-Control": "no-store"
  });
  res.end("window.__CODEX_PERCENT_SERVER__=" + json + ";if(window.__CODEX_RENDER_SERVER_PERCENT__){window.__CODEX_RENDER_SERVER_PERCENT__(window.__CODEX_PERCENT_SERVER__);}");
}
// CODEX_SERVER_PERCENT_0400_END

const server = http.createServer((req, res) => {
  req.setTimeout(SERVER_REQUEST_TIMEOUT, () => {
    req.destroy();
  });
  res.setTimeout(SERVER_REQUEST_TIMEOUT, () => {
    res.destroy();
  });
  const requestPath = req.url.split("?")[0];
  const restrictedSpecialPath = requestPath === "/codex-percent-state"
    || requestPath === "/codex-percent-state.js"
    || requestPath === "/enhanced-state.js"
    || requestPath === "/enhanced.html";
  if (restrictedSpecialPath && !authRoleForRequest(req)) {
    sendUnauthorized(res);
    return;
  }

  // CODEX_SERVER_PERCENT_ROUTE_0400_BEGIN
  if (req.method === "GET" && requestPath === "/codex-percent-state") {
    return codexPercentSendJson(res, codexPercentTick());
  }
  if (req.method === "GET" && requestPath === "/codex-percent-state.js") {
    return codexPercentSendScript(res, codexPercentTick());
  }
  // CODEX_SERVER_PERCENT_ROUTE_0400_END

  // CODEX_LIVE_STATE_SCRIPT_0145: JSONP-style live state sync, avoids browser fetch/CORS blocks.
  if (req.method === "GET" && requestPath === "/enhanced-state.js") {
    try {
      // 使用内存态（history 来自 RDS 灌注），禁止把磁盘 state.json 里的旧 history 副本推给浏览器
      const payload = Object.assign({}, state || {}, { ok: true, historyPersistedInRds: true });
      const safeJson = JSON.stringify(payload).replace(/</g, "\\u003c");
      const js = `window.__PENTA5_LIVE_STATE__ = ${safeJson};\nif (typeof window.__PENTA5_APPLY_LIVE_STATE__ === "function") { window.__PENTA5_APPLY_LIVE_STATE__(window.__PENTA5_LIVE_STATE__); }\n`;
      res.writeHead(200, {
        "Content-Type": "application/javascript; charset=utf-8",
        "Cache-Control": "no-store"
      });
      res.end(js);
    } catch (error) {
      res.writeHead(500, { "Content-Type": "application/javascript; charset=utf-8", "Cache-Control": "no-store" });
      res.end(`console.error(${JSON.stringify(String(error && error.message ? error.message : error))});`);
    }
    return;
  }

  // CODEX_BOOTSTRAP_ROUTE_0135: serve enhanced page with in-memory state (history from RDS).
  if (req.method === "GET" && requestPath === "/enhanced.html") {
    try {
      let html = fs.readFileSync(path.join(__dirname, "enhanced.html"), "utf8");
      const payload = Object.assign({}, state || {}, { ok: true, historyPersistedInRds: true });
      const safeJson = JSON.stringify(payload).replace(/</g, "\\u003c");
      const bootScript = `<script id="penta5-bootstrap-state">
window.__PENTA5_BOOTSTRAP_MODE__ = true;
window.__PENTA5_DISABLE_FRONTEND_FETCH__ = true;
window.__PENTA5_BOOTSTRAP_STATE__ = ${safeJson};
</script>`;
      html = html.replace(/<body[^>]*>/i, (tag) => `${tag}\n${bootScript}`);
      res.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store"
      });
      res.end(html);
    } catch (error) {
      res.writeHead(500, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: String(error && error.message ? error.message : error) }));
    }
    return;
  }

    handleRequest(req, res);
  });

// CODEX_SERVER_PERCENT_TIMER_0400_BEGIN
setTimeout(() => {
  try { codexPercentTick(); } catch (error) { console.error("[CodexPercent] startup tick failed", error && error.message ? error.message : error); }
}, 120000);
setInterval(() => {
  try { codexPercentTick(); } catch (error) { console.error("[CodexPercent] interval tick failed", error && error.message ? error.message : error); }
}, 10 * 60 * 1000);
// CODEX_SERVER_PERCENT_TIMER_0400_END

server.requestTimeout = Math.max(SERVER_REQUEST_TIMEOUT, SYNC_REQUEST_TIMEOUT);
server.headersTimeout = Math.max(SERVER_REQUEST_TIMEOUT, SYNC_REQUEST_TIMEOUT) + 5000;
server.keepAliveTimeout = 3000;
server.maxRequestsPerSocket = 100;

server.listen(PORT, BIND_HOST, () => {
    console.log(`Penta5 backend runner listening on http://${BIND_HOST}:${PORT}`);
    if (isAuthEnabled() && PANEL_VIEWER_PASSWORD === "") {
      console.warn("[Security] PANEL_VIEWER_PASSWORD 未设置：只读账号未隔离，请勿把管理员密码给他人");
    }
    if (PANEL_PASSWORD) {
      console.log("[Security] 稳杀单码接口：admin-only + AES密封 + no-store");
    }
    // 启动巡检延后：先让 /health /state 与 RDS 灌注可用，避免一启动就整包重生堵死探针
    setTimeout(() => {
      try { runModelWatchdog("startup", { save: true, integrityOnly: true }); } catch (error) {
        console.error("[ModelWatchdog] deferred startup failed", error && error.message ? error.message : error);
      }
    }, 45000);
    try { scheduleKillFrameworkHistoryBackfill(); } catch (error) {
      console.error("[KillFrameworkBackfill] schedule failed", error && error.message ? error.message : error);
    }
    try { scheduleCompactAttackHistoryBackfill(); } catch (error) {
      console.error("[CompactAttackBackfill] schedule failed", error && error.message ? error.message : error);
    }
    try { scheduleThreeDingHistoryBackfill("threeDingTwo"); } catch (error) {
      console.error("[ThreeDingTwoBackfill] schedule failed", error && error.message ? error.message : error);
    }
    try { scheduleThreeDingHistoryBackfill("threeDingOne"); } catch (error) {
      console.error("[ThreeDingOneBackfill] schedule failed", error && error.message ? error.message : error);
    }
    try { scheduleFrameworkSimHistoryBackfill(); } catch (error) {
      console.error("[FrameworkSimHistoryBackfill] schedule failed", error && error.message ? error.message : error);
    }
    // 启动即从 RDS 灌注共享 history（磁盘不再保留开奖主数据）
    hydrateSharedHistoryFromRds(state).then(async (result) => {
      if (result && result.ok) {
        addLog("共享历史已从 RDS 灌注", { reason: "startup-blocking", ...result });
      }
      try {
        let durable = await hydrateAlgoDurableFromRds(state);
        if ((!durable || !durable.ok) && (ALGO_RDS_DURABLE || LOCAL_TIP_ONLY)) {
          // /state 约 4~5MB，启动高峰易超时；失败后补一次，避免 counters 空账
          await new Promise((r) => setTimeout(r, 3000));
          durable = await hydrateAlgoDurableFromRds(state);
        }
        if (durable && durable.ok) {
          addLog("算法资料已从 RDS 灌注", { reason: "startup-blocking", ...durable });
        } else if (ALGO_RDS_DURABLE || LOCAL_TIP_ONLY) {
          addLog("算法资料 RDS 灌注跳过/失败", { reason: "startup-blocking", ...(durable || {}) });
        }
      } catch (err) {
        console.error("[RDS algo hydrate] failed", err && err.message ? err.message : err);
      }
      if (result && result.ok) {
        try {
          settleMissedPredictionEvaluations({ reason: "startup-hydrate-catchup", save: false });
          alignTipPredictionAfterHydrate("startup-hydrate-align-tip");
          const scrubbed = scrubReconstructedTruthFlags();
          if (scrubbed > 0) addLog("已清除回填样本的 truth 标记", { scrubbed });
          // 启动后先挂缓存；更稳落后或无 orchestrateMeta 时解封重跑真算
          if (state.currentPrediction) attachCachedStableMathFilter(state.currentPrediction);
          if (state.currentPrediction && Array.isArray(state.history) && state.history.length) {
            const smfLagging = tipStableMathFilterLagging(state.currentPrediction);
            if (!state.currentPrediction.orchestrateMeta || smfLagging) {
              if (smfLagging) {
                unsealTipForStableMathRebuild(state.currentPrediction, "startup-smf-lag");
              } else {
                state.currentPrediction.snapshotsSealedAt = "";
                state.currentPrediction.snapshotsSealedBaseUid = "";
                state.currentPrediction.allSnapshotsSealed = false;
              }
            }
            fillPredictionDetail(state.currentPrediction, state.history);
            ensurePredictionFrameworkSnapshots(state.currentPrediction, state.history);
            const latestHydrate = state.history[0] || state.latestRecord;
            if (latestHydrate) sealPredictionSnapshots(state.currentPrediction, latestHydrate);
            try { ensureTipStructurePerfect({ trigger: "startup-orchestrate-align", log: true, fromSync: false }); } catch (_) {}
          }
          state.stableMathFilter = stableMathFilterBridge.slimStableMathFilter(
            state.stableMathFilter || stableMathFilterBridge.readCachedFilter()
          ) || null;
          saveState();
          scheduleStableMathFilterRefresh("startup-hydrate");
        } catch (err) {
          console.error("[RDS hydrate] startup save failed", err && err.message ? err.message : err);
        }
      } else {
        addLog("RDS 历史灌注跳过/失败", { reason: "startup-blocking", ...(result || {}) });
        console.warn("[RDS hydrate] startup", result);
        try { scheduleStableMathFilterRefresh("startup-no-hydrate"); } catch (_) {}
      }
      scheduleRdsCatchupRetry("startup");
    }).catch((err) => {
      console.error("[RDS hydrate] startup failed", err && err.message ? err.message : err);
      scheduleRdsCatchupRetry("startup-hydrate-failed");
    });
  });

  // 按开奖时刻 0/5/10/15…分12秒对齐同步（开奖前约7秒开始密拉，开奖后再多档重试），不再死板每60秒空转。
  scheduleDrawAlignedAutoSync("startup");
  setTimeout(() => {
    Promise.resolve(syncNow("startup")).catch((err) => {
      console.error("[startup sync] failed", err && err.message ? err.message : err);
    });
  }, 1500);
  watchdogTimer = setInterval(() => {
    runModelWatchdog("timer", { save: true });
  }, MODEL_WATCHDOG_INTERVAL);
  setInterval(() => {
    flushPendingOfficialDrawsToRds("interval").catch((err) => {
      console.error("[RDS catchup interval] failed", err && err.message ? err.message : err);
    });
  }, Math.max(30000, Number(process.env.RDS_CATCHUP_INTERVAL_MS || 60000)));
  // 热路径大量 skipMirror；独立定时保证镜像仍会跑（debounce 内合并）
  const mirrorIntervalMs = Math.max(RDS_MIRROR_DEBOUNCE_MS, Number(process.env.RDS_MIRROR_INTERVAL_MS || 10 * 60 * 1000));
  setInterval(() => {
    try { scheduleRdsMirrorAfterSave(); } catch (err) {
      console.error("[RDS mirror interval] failed", err && err.message ? err.message : err);
    }
  }, mirrorIntervalMs);

  process.on("SIGINT", () => {
    clearAutoSyncTimer();
    if (watchdogTimer) clearInterval(watchdogTimer);
    addLog("收到 SIGINT，准备退出");
    saveState();
    process.exit(0);
  });
  process.on("SIGTERM", () => {
    clearAutoSyncTimer();
    if (watchdogTimer) clearInterval(watchdogTimer);
    addLog("收到 SIGTERM，准备退出");
    saveState();
    process.exit(0);
  });
}

startServer();
