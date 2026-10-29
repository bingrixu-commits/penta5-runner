"use strict";

// Durable first-write evidence. This module never generates or re-signs tips.
const nativeFs = require("fs");
const path = require("path");
const crypto = require("crypto");
const {mergePrediction, predictionId} = require("./prediction_merge");
const KEYS = ["fourPosExclude", "autoTop6", "recommend2", "killFramework", "fourDingFramework", "threeDuishu"];
const MAX_ENTRY_BYTES = 16 * 1024 * 1024;
const MAX_BATCH_BYTES = 64 * 1024 * 1024;
const MAX_BATCH_ENTRIES = 100;
const MAX_PENDING_FILES = 10000;
const MAX_INSPECTED_ENTRIES = 128;
const MAX_QUARANTINED_PER_BATCH = 8;
const hash = value => crypto.createHash("sha256").update(value).digest("hex");
const copy = value => JSON.parse(JSON.stringify(value));

function createPredictionOutbox(options = {}) {
  const fs = options.fs || nativeFs;
  const syncDirectory = options.syncDirectory || (directory => {
    if (process.platform === "win32") throw new Error("prediction-outbox-directory-sync-unsupported");
    const fd = fs.openSync(directory, fs.constants.O_RDONLY | (fs.constants.O_DIRECTORY || 0));
    try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  });
  const rootFor = dataDir => path.join(dataDir, "prediction-outbox");
  function listNames(root) {
    const names = [], directory = fs.opendirSync(root); let scanned = 0;
    try {
      for (let item; (item = directory.readSync());) {
        if (++scanned > MAX_PENDING_FILES + 128) throw new Error("prediction-outbox-directory-capacity-exceeded");
        if (/^prediction-[a-f0-9]{64}\.json$/.test(item.name)) names.push(item.name);
      }
    } finally { directory.closeSync(); }
    return names.sort();
  }
  function ensurePrivateDirectory(directory) {
    const missing = [];
    for (let cursor = directory; !fs.existsSync(cursor); cursor = path.dirname(cursor)) {
      if (path.dirname(cursor) === cursor) throw new Error("prediction-outbox-root-unavailable");
      missing.push(cursor);
    }
    fs.mkdirSync(directory, {recursive: true, mode: 0o700});
    fs.chmodSync(directory, 0o700);
    for (const parent of new Set(missing.reverse().map(created => path.dirname(created)).concat(path.dirname(directory)))) syncDirectory(parent);
  }
  function readBytes(file, limit = MAX_ENTRY_BYTES) {
    const stat = fs.lstatSync(file);
    if (!stat.isFile()) throw new Error("invalid-prediction-outbox-file-type");
    if (stat.size > limit) throw new Error("oversized-prediction-outbox-entry");
    const bytes = fs.readFileSync(file);
    if (bytes.length > limit) throw new Error("oversized-prediction-outbox-entry");
    return bytes;
  }
  function durableRead(file) {
    const fd = fs.openSync(file, "r");
    try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    syncDirectory(path.dirname(file));
  }
  function writeExclusive(file, bytes) {
    if (bytes.length > MAX_ENTRY_BYTES) throw new Error("oversized-prediction-outbox-entry");
    ensurePrivateDirectory(path.dirname(file));
    if (!fs.existsSync(file)) {
      const temporary = path.join(path.dirname(file), ".pending-" + process.pid + "-" + crypto.randomBytes(12).toString("hex"));
      let fd;
      try {
        fd = fs.openSync(temporary, "wx", 0o600);
        fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); fs.closeSync(fd); fd = undefined;
        try { fs.linkSync(temporary, file); }
        catch (error) { if (!error || error.code !== "EEXIST") throw error; }
      } finally {
        if (fd !== undefined) fs.closeSync(fd);
        if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
      }
    }
    durableRead(file); // Also retry the commit if the earlier link fsync failed.
    return readBytes(file);
  }
  function assertOriginal(prediction) {
    if (!prediction || typeof prediction !== "object" || Array.isArray(prediction)
        || prediction.evaluated || prediction.allSnapshotsSealed !== true || !prediction.snapshotsSealedAt
        || !prediction.baseUid || !prediction.basePeriod || !prediction.generatedAt
        || KEYS.some(key => !prediction[key] || prediction[key].sealed !== true || !prediction[key].sealHash
          || prediction[key].reconstructed === true)) throw new Error("incomplete-first-sealed-prediction");
    return predictionId(prediction.id);
  }
  function evidenceIdentity(prediction) {
    return JSON.stringify([predictionId(prediction.id), String(prediction.baseUid), String(prediction.basePeriod),
      String(prediction.generatedAt), String(prediction.snapshotsSealedAt), ...KEYS.map(key => String(prediction[key].sealHash))]);
  }
  function decode(bytes, name) {
    const payload = JSON.parse(bytes.toString("utf8"));
    if (!payload || payload.version !== 1) throw new Error("invalid-prediction-outbox-version");
    const id = assertOriginal(payload.prediction);
    if (name !== "prediction-" + hash(id) + ".json") throw new Error("prediction-outbox-name-identity-conflict");
    return {id, name, sha256: hash(bytes), prediction: payload.prediction, bytes: bytes.length};
  }
  function enqueue(dataDir, prediction) {
    const id = assertOriginal(prediction);
    const root = rootFor(dataDir);
    ensurePrivateDirectory(root);
    const name = "prediction-" + hash(id) + ".json";
    const file = path.join(root, name);
    if (!fs.existsSync(file) && listNames(root).length >= MAX_PENDING_FILES) {
      throw new Error("prediction-outbox-capacity-exceeded");
    }
    const bytes = writeExclusive(file, Buffer.from(JSON.stringify({version: 1, prediction})));
    const saved = decode(bytes, name);
    if (evidenceIdentity(saved.prediction) !== evidenceIdentity(prediction)) throw new Error("prediction-outbox-immutable-identity-conflict");
    return saved;
  }
  function quarantine(dataDir, name, reason) {
    const root = rootFor(dataDir), source = path.join(root, name);
    const directory = path.join(dataDir, "prediction-outbox-quarantine");
    ensurePrivateDirectory(directory);
    const token = crypto.randomBytes(12).toString("hex");
    const preserved = name + "." + token + ".data";
    const stat = fs.lstatSync(source);
    if (stat.isFile()) {
      const fd = fs.openSync(source, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
      try {
        const opened = fs.fstatSync(fd);
        if (!opened.isFile() || opened.ino !== stat.ino || opened.dev !== stat.dev) throw new Error("quarantine-source-identity-changed");
        fs.fsyncSync(fd);
      } finally { fs.closeSync(fd); }
    }
    // Commit the reason before moving the original inode. No parse, truncate,
    // re-sign, or deletion of the offending bytes takes place.
    writeExclusive(path.join(directory, preserved + ".json"), Buffer.from(JSON.stringify({
      version: 1, originalName: name, preservedFile: preserved, reason,
      bytes: stat.size, observedAt: new Date().toISOString()
    })));
    fs.renameSync(source, path.join(directory, preserved));
    syncDirectory(directory); syncDirectory(root);
    return {file: preserved, originalName: name, reason, bytes: stat.size};
  }
  function writeCursor(root, name) {
    const file = path.join(root, ".mirror-cursor.json");
    const temporary = file + "." + process.pid + "." + crypto.randomBytes(8).toString("hex") + ".tmp";
    let fd;
    try {
      fd = fs.openSync(temporary, "wx", 0o600);
      fs.writeFileSync(fd, JSON.stringify({version: 1, after: name})); fs.fsyncSync(fd); fs.closeSync(fd); fd = undefined;
      fs.renameSync(temporary, file); syncDirectory(root);
    } finally {
      if (fd !== undefined) fs.closeSync(fd);
      if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
    }
  }
  function readBatch(dataDir, limits = {}) {
    const root = rootFor(dataDir);
    if (!fs.existsSync(root)) return {entries: [], bytes: 0, count: 0, quarantined: [], remaining: 0};
    const maxEntries = Math.min(MAX_BATCH_ENTRIES, Math.max(1, Number(limits.maxEntries) || MAX_BATCH_ENTRIES));
    const maxBytes = Math.min(MAX_BATCH_BYTES, Math.max(MAX_ENTRY_BYTES, Number(limits.maxBytes) || MAX_BATCH_BYTES));
    const names = listNames(root), quarantined = [];
    const cursorFile = path.join(root, ".mirror-cursor.json"); let after = "";
    if (fs.existsSync(cursorFile)) {
      try {
        const cursor = JSON.parse(readBytes(cursorFile, 4096).toString("utf8"));
        if (!cursor || cursor.version !== 1 || !/^prediction-[a-f0-9]{64}\.json$/.test(cursor.after || "")) throw new SyntaxError("invalid-cursor");
        after = cursor.after;
      } catch (error) {
        if (!(error instanceof SyntaxError) && !/^oversized-|^invalid-prediction-outbox-file-type/.test(error.message || "")) throw error;
        quarantined.push(quarantine(dataDir, ".mirror-cursor.json", "invalid-fairness-cursor"));
      }
    }
    const ordered = names.filter(name => name > after).concat(names.filter(name => name <= after));
    const entries = []; let bytes = 0, inspected = 0, readTotal = 0, last = "";
    for (const name of ordered) {
      if (inspected >= MAX_INSPECTED_ENTRIES || quarantined.length >= MAX_QUARANTINED_PER_BATCH) break;
      if (entries.length >= maxEntries) break;
      const file = path.join(root, name), stat = fs.lstatSync(file);
      if (stat.size <= MAX_ENTRY_BYTES && readTotal + stat.size > maxBytes) break;
      inspected++; last = name;
      try {
        const value = readBytes(file); readTotal += value.length;
        const entry = decode(value, name);
        durableRead(file);
        entries.push(entry); bytes += value.length;
      } catch (error) {
        const reason = error instanceof SyntaxError ? "invalid-json" : String(error.message || "");
        if (!/^(invalid-json|oversized-prediction-outbox-entry|invalid-prediction-outbox-file-type|invalid-prediction-outbox-version|incomplete-first-sealed-prediction|prediction-outbox-name-identity-conflict|Prediction id is required|Prediction id exceeds persistence limit)$/.test(reason)) throw error;
        quarantined.push(quarantine(dataDir, name, reason));
      }
    }
    if (last) writeCursor(root, last);
    return {entries, bytes, count: entries.length, quarantined, inspected, readBytes: readTotal,
      remaining: Math.max(0, names.length - quarantined.filter(item => item.originalName !== ".mirror-cursor.json").length)};
  }
  function mergeIntoState(state, batch) {
    const result = Object.assign({}, state);
    const originals = new Map((batch.entries || []).map(entry => [entry.id, entry.prediction]));
    const ids = new Set();
    result.predictions = (Array.isArray(state.predictions) ? state.predictions : []).map(incoming => {
      if (!incoming || !incoming.id) return incoming;
      const id = predictionId(incoming.id); ids.add(id);
      return originals.has(id) ? mergePrediction(originals.get(id), incoming) : incoming;
    });
    for (const [id, original] of originals) if (!ids.has(id)) result.predictions.push(copy(original));
    return result;
  }
  function acknowledge(dataDir, batch, receipt, expected) {
    if (!receipt || receipt.version !== 2 || receipt.ok !== true
        || !expected || !/^[a-f0-9]{32,64}$/.test(String(expected.nonce))
        || !/^[a-f0-9]{64}$/.test(String(expected.freezeSha256))
        || receipt.nonce !== expected.nonce || receipt.freeze_sha256 !== expected.freezeSha256
        || receipt.freeze_path !== path.resolve(expected.freezePath)
        || !Array.isArray(receipt.verified_outbox_entries) || !Array.isArray(receipt.conflicted_outbox_entries)
        || receipt.verified_outbox_count !== receipt.verified_outbox_entries.length) throw new Error("unverified-prediction-outbox-receipt");
    const exact = new Map((batch.entries || []).map(entry => [entry.id, entry.sha256]));
    const seen = new Set(), verified = new Set();
    for (const [items, accepted] of [[receipt.verified_outbox_entries, true], [receipt.conflicted_outbox_entries, false]]) {
      for (const item of items) {
        if (!item || !exact.has(item.id) || exact.get(item.id) !== item.sha256 || seen.has(item.id)
            || (!accepted && (typeof item.reason !== "string" || !item.reason || item.reason.length > 120))) throw new Error("invalid-partial-outbox-receipt");
        seen.add(item.id); if (accepted) verified.add(item.id);
      }
    }
    if (seen.size !== exact.size) throw new Error("incomplete-partial-outbox-receipt");
    const root = rootFor(dataDir), present = [];
    for (const entry of batch.entries || []) {
      if (!verified.has(entry.id)) continue;
      if (entry.name !== "prediction-" + hash(predictionId(entry.id)) + ".json") throw new Error("invalid-prediction-outbox-batch");
      const file = path.join(root, entry.name);
      if (!fs.existsSync(file)) continue;
      if (hash(readBytes(file)) !== entry.sha256) throw new Error("prediction-outbox-ack-content-conflict");
      present.push(file);
    }
    for (const file of present) fs.unlinkSync(file);
    if (present.length) syncDirectory(root);
    return {deleted: present.length, conflicted: receipt.conflicted_outbox_entries.length};
  }
  function atomicReceipt(file, payload) {
    const bytes = Buffer.from(JSON.stringify(payload));
    const saved = writeExclusive(path.resolve(file), bytes);
    if (!saved.equals(bytes)) throw new Error("immutable-mirror-receipt-conflict");
    return payload;
  }
  const pendingCount = dataDir => fs.existsSync(rootFor(dataDir)) ? listNames(rootFor(dataDir)).length : 0;
  return {enqueue, readBatch, mergeIntoState, acknowledge, atomicReceipt, pendingCount};
}

module.exports = Object.assign({createPredictionOutbox}, createPredictionOutbox());
