"use strict";

const fs = require("fs");
const path = require("path");
const seal = require("./seal");

const SECURE_FILE_MODE = 0o600;

function isoNow() {
  return new Date().toISOString();
}

function writeSecureJsonAtomic(file, payload) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), { encoding: "utf8", mode: SECURE_FILE_MODE });
  try {
    fs.chmodSync(tmp, SECURE_FILE_MODE);
  } catch (_) {}
  fs.renameSync(tmp, file);
  try {
    fs.chmodSync(file, SECURE_FILE_MODE);
  } catch (_) {}
}

function readJsonFile(file, fallback) {
  try {
    if (!fs.existsSync(file)) return fallback;
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (_) {
    return fallback;
  }
}

function writeSealedBundle(options) {
  const {
    sealedFile,
    metaFile,
    payload,
    password,
    sealedVersion,
    buildMeta
  } = options;
  if (!password) {
    writeSecureJsonAtomic(metaFile, payload);
    return;
  }
  writeSecureJsonAtomic(sealedFile, {
    version: sealedVersion || "qualified-picks-sealed-bundle-v1",
    updatedAt: isoNow(),
    envelope: seal.sealPayload(payload, password)
  });
  writeSecureJsonAtomic(metaFile, buildMeta(payload));
}

function readSealedField(options) {
  const {
    sealedFile,
    metaFile,
    password,
    field,
    legacyReader
  } = options;
  if (password && fs.existsSync(sealedFile)) {
    try {
      const wrapped = readJsonFile(sealedFile, null);
      if (wrapped && wrapped.envelope) {
        const inner = seal.unsealPayload(wrapped.envelope, password);
        if (field == null) return inner;
        return inner[field];
      }
    } catch (_) {}
  }
  if (typeof legacyReader === "function") return legacyReader();
  const raw = readJsonFile(metaFile, null);
  if (!raw) return field ? null : {};
  if (field && raw[field] != null) return raw[field];
  return raw;
}

module.exports = {
  SECURE_FILE_MODE,
  writeSecureJsonAtomic,
  readJsonFile,
  writeSealedBundle,
  readSealedField
};
