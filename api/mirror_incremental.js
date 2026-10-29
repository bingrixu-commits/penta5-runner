"use strict";

/**
 * Incremental RDS mirror: freeze live state.json then upsert into existing schema.
 * Never blocks the 3001 writer; failures only affect the mirror process.
 *
 *   node --max-old-space-size=4096 mirror_incremental.js --from-live
 *   node --max-old-space-size=4096 mirror_incremental.js --file /path/to/freeze.json
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawnSync } = require("child_process");
const { atomicReceipt } = require("./prediction_outbox");

const LOCK_FILE = path.join("/tmp", "penta5-rds-mirror.lock");
const MIRROR_BACKUP_DIR = path.join("/root", "penta5-runner", "data", "backups");
const MIRROR_BACKUP_PREFIX = "state_mirror_";
const MIRROR_BACKUP_KEEP = Math.max(
  1,
  Number(process.env.PENTA5_MIRROR_BACKUP_KEEP || 3) || 3
);

function hasFlag(name) {
  return process.argv.includes(name);
}

function argValue(name, fallback) {
  const idx = process.argv.indexOf(name);
  if (idx < 0) return fallback;
  return process.argv[idx + 1] || fallback;
}

function acquireLock() {
  try {
    const fd = fs.openSync(LOCK_FILE, "wx");
    fs.writeFileSync(fd, String(process.pid));
    fs.closeSync(fd);
    return true;
  } catch (err) {
    if (err && err.code === "EEXIST") {
      try {
        const oldPid = Number(fs.readFileSync(LOCK_FILE, "utf8"));
        if (oldPid && !Number.isNaN(oldPid)) {
          try {
            process.kill(oldPid, 0);
            console.error("mirror already running pid=" + oldPid);
            return false;
          } catch (_) {
            fs.unlinkSync(LOCK_FILE);
            return acquireLock();
          }
        }
      } catch (_) {}
      console.error("mirror lock busy");
      return false;
    }
    throw err;
  }
}

function releaseLock() {
  try {
    if (fs.existsSync(LOCK_FILE)) fs.unlinkSync(LOCK_FILE);
  } catch (_) {}
}

function pruneMirrorBackups(keepCount) {
  try {
    if (!fs.existsSync(MIRROR_BACKUP_DIR)) return;
    const files = fs
      .readdirSync(MIRROR_BACKUP_DIR, { withFileTypes: true })
      .filter(
        (entry) =>
          entry.isFile() &&
          entry.name.startsWith(MIRROR_BACKUP_PREFIX) &&
          entry.name.endsWith(".json")
      )
      .map((entry) => {
        const file = path.join(MIRROR_BACKUP_DIR, entry.name);
        return { file, mtimeMs: fs.statSync(file).mtimeMs };
      })
      .sort((a, b) => b.mtimeMs - a.mtimeMs);
    const remove = files.slice(Math.max(0, keepCount));
    for (const entry of remove) fs.unlinkSync(entry.file);
    if (remove.length > 0) {
      console.log(
        "mirror backup retention removed=" +
          remove.length +
          " keep=" +
          Math.max(0, keepCount)
      );
    }
  } catch (err) {
    console.error(
      "mirror backup retention failed: " +
        (err && err.message ? err.message : String(err))
    );
  }
}

function main() {
  if (!acquireLock()) process.exit(0);
  try {
    const importer = path.join(__dirname, "import_full_existing.js");
    if (!fs.existsSync(importer)) {
      throw new Error("missing import_full_existing.js");
    }
    const args = ["--max-old-space-size=4096", importer];
    const receiptPath = argValue("--receipt", "");
    const receiptNonce = argValue("--receipt-nonce", "");
    const expectedFreezeSha = argValue("--expected-freeze-sha", "");
    const hasReceipt = !!(receiptPath || receiptNonce || expectedFreezeSha);
    let confirmedFreezePath = "", beforeHash = "";
    if (hasReceipt) {
      if (hasFlag("--from-live") || !receiptPath || !/^[a-f0-9]{32,64}$/.test(receiptNonce)
          || !/^[a-f0-9]{64}$/.test(expectedFreezeSha)) throw new Error("invalid-mirror-receipt-request");
      confirmedFreezePath = path.resolve(argValue("--file", ""));
      beforeHash = crypto.createHash("sha256").update(fs.readFileSync(confirmedFreezePath)).digest("hex");
      if (beforeHash !== expectedFreezeSha) throw new Error("mirror-freeze-hash-mismatch-before-import");
    }
    if (hasFlag("--from-live")) {
      args.push("--from-live");
      const freezeTo = argValue("--freeze-to", "");
      if (freezeTo) {
        args.push("--freeze-to", freezeTo);
      } else {
        pruneMirrorBackups(MIRROR_BACKUP_KEEP - 1);
        const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
        args.push(
          "--freeze-to",
          path.join(MIRROR_BACKUP_DIR, MIRROR_BACKUP_PREFIX + stamp + ".json")
        );
      }
    } else {
      const file = argValue("--file", "");
      if (!file) throw new Error("pass --from-live or --file PATH");
      args.push("--file", file);
    }
    if (hasReceipt) args.push("--isolate-prediction-conflicts");
    console.log("mirror start " + new Date().toISOString());
    const result = spawnSync(process.execPath, args, {
      cwd: __dirname,
      env: process.env,
      stdio: "inherit",
    });
    if (result.status !== 0) {
      process.exit(result.status || 1);
    }
    if (hasReceipt) {
      const verifier = path.join(__dirname, "verify_mirror_seals.js");
      if (!fs.existsSync(verifier)) throw new Error("missing verify_mirror_seals.js");
      const verified = spawnSync(process.execPath, [verifier, "--file", confirmedFreezePath,
        "--expected-freeze-sha", beforeHash, "--partial-confirmation"], {
          cwd: __dirname, env: process.env, stdio: ["ignore", "pipe", "pipe"],
          encoding: "utf8", maxBuffer: 1024 * 1024, timeout: 60000});
      if (verified.status !== 0) {
        const reason = verified.error && verified.error.code === "ETIMEDOUT" ? "timeout" : "verifier-failed";
        console.error("mirror seal verification unconfirmed reason=" + reason + " status="
          + (Number.isInteger(verified.status) ? verified.status : "unavailable"));
        process.exit(verified.status || 1);
      }
      const afterHash = crypto.createHash("sha256").update(fs.readFileSync(confirmedFreezePath)).digest("hex");
      if (afterHash !== beforeHash) throw new Error("mirror-freeze-hash-changed-during-import");
      const frozen = JSON.parse(fs.readFileSync(confirmedFreezePath, "utf8"));
      const confirmation = JSON.parse(String(verified.stdout || ""));
      if (!confirmation || confirmation.ok !== true || !Array.isArray(confirmation.verified_outbox_entries)
          || !Array.isArray(confirmation.conflicted_outbox_entries)
          || confirmation.verified_outbox_count !== confirmation.verified_outbox_entries.length) throw new Error("invalid-partial-mirror-confirmation");
      const expectedEntries = new Map(frozen.rdsMirrorOutbox.entries.map(entry => [entry.id, entry.sha256]));
      const seen = new Set();
      for (const entries of [confirmation.verified_outbox_entries, confirmation.conflicted_outbox_entries]) {
        for (const entry of entries) {
          if (!entry || seen.has(entry.id) || expectedEntries.get(entry.id) !== entry.sha256 || !expectedEntries.has(entry.id)) throw new Error("partial-mirror-confirmation-identity-conflict");
          seen.add(entry.id);
        }
      }
      if (seen.size !== expectedEntries.size) throw new Error("incomplete-partial-mirror-confirmation");
      if (confirmation.conflicted_outbox_entries.length) console.error("mirror pending seal conflicts=" + confirmation.conflicted_outbox_entries.length);
      atomicReceipt(receiptPath, {version: 2, ok: true, nonce: receiptNonce,
        freeze_sha256: afterHash, freeze_path: confirmedFreezePath,
        verified_outbox_count: confirmation.verified_outbox_count,
        verified_outbox_entries: confirmation.verified_outbox_entries,
        conflicted_outbox_entries: confirmation.conflicted_outbox_entries});
    }
    pruneMirrorBackups(MIRROR_BACKUP_KEEP);
    console.log("mirror done " + new Date().toISOString());
  } finally {
    releaseLock();
  }
}

main();
