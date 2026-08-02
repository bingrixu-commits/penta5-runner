"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const STORE_SCHEMA_VERSION = 1;
const DEFAULT_MAX_SNAPSHOTS = 200;
const DEFAULT_STORE_FILE = "backdelete_snapshots.json";

function jsonClone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (!value || typeof value !== "object") return value;
  const out = {};
  Object.keys(value).sort().forEach((key) => {
    out[key] = stableValue(value[key]);
  });
  return out;
}

function stableStringify(value) {
  return JSON.stringify(stableValue(value));
}

function sha256(value) {
  return crypto.createHash("sha256").update(String(value), "utf8").digest("hex");
}

function defaultStore() {
  return {
    schemaVersion: STORE_SCHEMA_VERSION,
    updatedAt: "",
    latestKey: "",
    order: [],
    snapshots: {}
  };
}

function normalizeAttackText(converter, attackText, panelPair) {
  let text = String(attackText == null ? "" : attackText).replace(/\r\n?/g, "\n").trim();
  if (converter && typeof converter.applyPanelThreeDuishuPair === "function") {
    text = converter.applyPanelThreeDuishuPair(text, panelPair);
  } else if (converter && typeof converter.canonicalizeAttackText === "function") {
    text = converter.canonicalizeAttackText(text);
  }
  return String(text || "").replace(/\r\n?/g, "\n").trim();
}

function normalizeResultFileNames(result, fileStem) {
  const out = jsonClone(result);
  if (!out || typeof out !== "object") return out;
  out.fileName = fileStem + ".txt";
  if (Array.isArray(out.groups)) {
    out.groups.forEach((group, index) => {
      if (group && typeof group === "object") group.fileName = fileStem + "-g" + String(index + 1) + ".txt";
    });
  }
  return out;
}

function groupResults(result) {
  if (result && result.multiCombo === true && Array.isArray(result.groups)) return result.groups;
  return result ? [result] : [];
}

function validateHistoryContext(context) {
  const source = String(context && context.source || "").toLowerCase();
  const sharedPeriod = String(context && context.sharedPeriod || "");
  const rdsPeriod = String(context && context.rdsPeriod || "");
  const panelBasePeriod = String(context && context.panelBasePeriod || "");
  const rows = Array.isArray(context && context.rows) ? context.rows : [];
  if (source !== "rds") throw new Error("历史来源不是 RDS，已停止回删。");
  if (!/^\d+$/.test(sharedPeriod) || sharedPeriod !== rdsPeriod) {
    throw new Error("RDS 最新期与共享历史首期不一致，已停止回删。");
  }
  if (panelBasePeriod && panelBasePeriod !== sharedPeriod) {
    throw new Error("面板基准期 " + panelBasePeriod + " 与 RDS 最新期 " + sharedPeriod + " 不一致，已停止回删。");
  }
  if (rows.length < 200) throw new Error("RDS 历史不足 200 期，已停止回删。");
  const periods = new Set();
  rows.forEach((row, index) => {
    const period = String(row && row.period || "");
    const numbers = Array.isArray(row && row.numbers) ? row.numbers.map(String) : [];
    if (!/^\d+$/.test(period) || numbers.length !== 4 || numbers.some((value) => !/^\d$/.test(value))) {
      throw new Error("RDS 历史第 " + String(index + 1) + " 条无效，已停止回删。");
    }
    if (periods.has(period)) throw new Error("RDS 历史存在重复期号，已停止回删。");
    periods.add(period);
    if (index > 0 && Number(rows[index - 1].period) <= Number(period)) {
      throw new Error("RDS 历史不是严格从新到旧排序，已停止回删。");
    }
  });
  if (String(rows[0].period) !== sharedPeriod) {
    throw new Error("RDS 历史首期与共享最新期不一致，已停止回删。");
  }
}

function validateDeletedHistory(group, rows) {
  const deleted = Array.isArray(group.deletedHistory) ? group.deletedHistory : [];
  const deletedUnique = Number(group.deletedUnique || 0);
  const positions = Array.isArray(group.positions) ? group.positions.map(Number) : [];
  const scanned = Number(group.scanned || 0);
  if (deleted.length !== deletedUnique) throw new Error("回删来源条数与已删计数不一致。");
  if (positions.length !== 3 || positions.some((value) => value < 0 || value > 3)) {
    throw new Error("回删位置组合无效。");
  }
  const rowByPeriod = new Map(rows.map((row, index) => [String(row.period), { row, index }]));
  const seenCodes = new Set();
  deleted.forEach((item) => {
    const period = String(item && item.period || "");
    const code = String(item && item.code || "");
    const found = rowByPeriod.get(period);
    if (!found || found.index >= scanned) throw new Error("回删来源期号不在实际扫描范围内。");
    const expected = positions.map((position) => String(found.row.numbers[position])).join("");
    if (code !== expected || !/^\d{3}$/.test(code)) throw new Error("回删号码与 RDS 来源期不一致。");
    if (seenCodes.has(code)) throw new Error("同一旧码被重复回删。");
    seenCodes.add(code);
  });
}

function validateBackdeleteResult(result, target, rows, converterVersion) {
  if (!result || result.ok !== true) {
    throw new Error(String(result && result.message || "回删转换失败。"));
  }
  const groups = groupResults(result);
  if (!groups.length) throw new Error("回删结果没有有效组合。");
  groups.forEach((group) => {
    const tokens = Array.isArray(group && group.tokens) ? group.tokens.map(String) : [];
    const count = Number(group && group.count);
    const afterFilter = Number(group && group.afterFilter);
    const deletedUnique = Number(group && group.deletedUnique || 0);
    if (!group || group.ok !== true || group.source !== "three-ding-backdelete-target") {
      throw new Error("回删结果来源无效。");
    }
    if (String(group.backdeleteVersion || "") !== converterVersion) {
      throw new Error("回删转换器版本不一致。");
    }
    if (count !== target || Number(group.targetRemain) !== target || tokens.length !== target) {
      throw new Error("回删没有在目标 " + String(target) + " 组停止。");
    }
    if (!Number.isInteger(afterFilter) || afterFilter < target || deletedUnique !== afterFilter - target) {
      throw new Error("回删前后计数不一致。");
    }
    if (new Set(tokens).size !== tokens.length || tokens.some((token) => !/^\d{3}$/.test(token))) {
      throw new Error("回删结果包含重复或无效号码。");
    }
    validateDeletedHistory(group, rows);
    const deletedCodes = new Set((group.deletedHistory || []).map((item) => String(item.code || "")));
    if (tokens.some((token) => deletedCodes.has(token))) throw new Error("已回删旧码仍存在于结果中。");
  });
  if (result.multiCombo === true) {
    const total = groups.reduce((sum, group) => sum + Number(group.count || 0), 0);
    if (Number(result.count) !== total || Number(result.targetRemain) !== target) {
      throw new Error("多组合回删总计数不一致。");
    }
  }
}

function snapshotSealBody(snapshot) {
  return {
    schemaVersion: snapshot.schemaVersion,
    converterVersion: snapshot.converterVersion,
    requestKey: snapshot.requestKey,
    source: snapshot.source,
    historyLatestPeriod: snapshot.historyLatestPeriod,
    historyRowsHash: snapshot.historyRowsHash,
    panelBasePeriod: snapshot.panelBasePeriod,
    panelTargetPeriod: snapshot.panelTargetPeriod,
    panelThreeDuishuPair: snapshot.panelThreeDuishuPair,
    targetRemain: snapshot.targetRemain,
    normalizedAttackText: snapshot.normalizedAttackText,
    inputHash: snapshot.inputHash,
    result: snapshot.result
  };
}

function verifySnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object" || !/^[a-f0-9]{64}$/.test(String(snapshot.fingerprint || ""))) return false;
  return sha256(stableStringify(snapshotSealBody(snapshot))) === snapshot.fingerprint;
}

function createBackdeleteSnapshotStore(options) {
  const opts = options && typeof options === "object" ? options : {};
  const converter = opts.converter;
  if (!converter || typeof converter.convertThreeDingBackDelete !== "function") {
    throw new Error("backdelete converter is required");
  }
  const converterVersion = String(converter.THREE_DING_BACKDELETE_VERSION || "");
  if (!converterVersion) throw new Error("backdelete converter version is required");
  const dataDir = String(opts.dataDir || "");
  if (!dataDir) throw new Error("backdelete dataDir is required");
  const storeFile = path.join(dataDir, String(opts.fileName || DEFAULT_STORE_FILE));
  const maxSnapshots = Math.max(10, Math.min(1000, Number(opts.maxSnapshots || DEFAULT_MAX_SNAPSHOTS)));
  let loadError = "";
  let store = defaultStore();

  function load() {
    if (!fs.existsSync(storeFile)) return;
    try {
      const parsed = JSON.parse(fs.readFileSync(storeFile, "utf8"));
      if (!parsed || parsed.schemaVersion !== STORE_SCHEMA_VERSION || !parsed.snapshots || typeof parsed.snapshots !== "object") {
        throw new Error("封存文件结构无效");
      }
      const clean = defaultStore();
      const requestedOrder = Array.isArray(parsed.order) ? parsed.order.map(String) : [];
      requestedOrder.forEach((key) => {
        const snapshot = parsed.snapshots[key];
        if (!verifySnapshot(snapshot) || String(snapshot.requestKey || "") !== key) {
          loadError = "封存文件存在校验失败记录，已拒绝覆盖";
          return;
        }
        clean.snapshots[key] = snapshot;
        clean.order.push(key);
      });
      clean.latestKey = clean.snapshots[String(parsed.latestKey || "")]
        ? String(parsed.latestKey)
        : String(clean.order[0] || "");
      clean.updatedAt = String(parsed.updatedAt || "");
      store = clean;
    } catch (error) {
      loadError = "封存文件读取失败：" + String(error && error.message || error);
    }
  }

  function writeStore(nextStore) {
    if (loadError) throw new Error(loadError);
    fs.mkdirSync(dataDir, { recursive: true });
    const tempFile = path.join(
      dataDir,
      "." + path.basename(storeFile) + "." + String(process.pid) + "." + String(Date.now()) + "." + crypto.randomBytes(4).toString("hex") + ".tmp"
    );
    fs.writeFileSync(tempFile, JSON.stringify(nextStore));
    fs.renameSync(tempFile, storeFile);
    store = nextStore;
  }

  function latest() {
    const snapshot = store.snapshots[store.latestKey] || null;
    return jsonClone(snapshot);
  }

  function generate(input) {
    if (loadError) throw new Error(loadError);
    const payload = input && typeof input === "object" ? input : {};
    const attackText = String(payload.attackText == null ? "" : payload.attackText);
    if (!attackText.trim()) throw new Error("请粘贴三定攻击语后再生成。");
    if (Buffer.byteLength(attackText, "utf8") > 100000) throw new Error("攻击语过长，已停止。");
    const target = Number(payload.target);
    if (!Number.isInteger(target) || target < 1 || target > 1000) {
      throw new Error("目标剩余组数必须是 1-1000 的整数。");
    }
    const panelPair = String(payload.panelThreeDuishuPair || "");
    if (!/^\d{2}$/.test(panelPair)) throw new Error("面板本期排除对数未就绪。");
    const context = payload.historyContext && typeof payload.historyContext === "object"
      ? payload.historyContext
      : {};
    validateHistoryContext(context);
    const rows = context.rows.slice(0, 200).map((row) => ({
      period: String(row.period),
      numbers: row.numbers.slice(0, 4).map(String)
    }));
    const normalizedAttackText = normalizeAttackText(converter, attackText, panelPair);
    const inputHash = sha256(normalizedAttackText);
    const requestBody = {
      schemaVersion: STORE_SCHEMA_VERSION,
      converterVersion,
      historyLatestPeriod: String(context.sharedPeriod),
      panelBasePeriod: String(context.panelBasePeriod || context.sharedPeriod),
      panelTargetPeriod: String(context.panelTargetPeriod || ""),
      panelThreeDuishuPair: panelPair,
      targetRemain: target,
      inputHash
    };
    const requestKey = sha256(stableStringify(requestBody));
    const existing = store.snapshots[requestKey];
    if (existing) {
      if (!verifySnapshot(existing)) throw new Error("既有封存结果校验失败，已拒绝覆盖。");
      if (store.latestKey !== requestKey) {
        const next = jsonClone(store);
        next.latestKey = requestKey;
        next.order = [requestKey].concat(next.order.filter((key) => key !== requestKey));
        next.updatedAt = new Date().toISOString();
        writeStore(next);
      }
      return { reused: true, snapshot: jsonClone(existing) };
    }

    const converted = converter.convertThreeDingBackDelete(normalizedAttackText, rows, {
      target,
      panelThreeDuishuPair: panelPair
    });
    validateBackdeleteResult(converted, target, rows, converterVersion);
    const fileStem = "backdelete-" + String(context.sharedPeriod) + "-" + String(target) + "-" + requestKey.slice(0, 10);
    const result = normalizeResultFileNames(converted, fileStem);
    const snapshot = {
      schemaVersion: STORE_SCHEMA_VERSION,
      converterVersion,
      requestKey,
      createdAt: new Date().toISOString(),
      source: "rds",
      historyLatestPeriod: String(context.sharedPeriod),
      historyRowsHash: sha256(stableStringify(rows)),
      panelBasePeriod: String(context.panelBasePeriod || context.sharedPeriod),
      panelTargetPeriod: String(context.panelTargetPeriod || ""),
      panelThreeDuishuPair: panelPair,
      targetRemain: target,
      normalizedAttackText,
      inputHash,
      result
    };
    snapshot.fingerprint = sha256(stableStringify(snapshotSealBody(snapshot)));
    if (!verifySnapshot(snapshot)) throw new Error("封存结果自校验失败。");

    const next = jsonClone(store);
    next.snapshots[requestKey] = snapshot;
    next.order = [requestKey].concat(next.order.filter((key) => key !== requestKey));
    while (next.order.length > maxSnapshots) {
      const removedKey = next.order.pop();
      delete next.snapshots[removedKey];
    }
    next.latestKey = requestKey;
    next.updatedAt = snapshot.createdAt;
    writeStore(next);
    return { reused: false, snapshot: jsonClone(snapshot) };
  }

  load();
  return {
    converterVersion,
    storeFile,
    generate,
    latest,
    integrityError: () => loadError
  };
}

module.exports = {
  STORE_SCHEMA_VERSION,
  createBackdeleteSnapshotStore,
  stableStringify,
  verifySnapshot
};
