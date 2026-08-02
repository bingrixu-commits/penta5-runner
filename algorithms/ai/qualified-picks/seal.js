"use strict";

const crypto = require("crypto");

const SEAL_SALT = "penta5-qualified-picks-seal-v1";
const SEAL_ITERATIONS = 120000;

function sealKeyFromPassword(password) {
  return crypto.pbkdf2Sync(String(password || ""), SEAL_SALT, SEAL_ITERATIONS, 32, "sha256");
}

function sealPayload(payload, password) {
  const key = sealKeyFromPassword(password);
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
  const plaintext = JSON.stringify(payload);
  const data = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return {
    alg: "aes-256-gcm-pbkdf2-v1",
    iv: iv.toString("base64"),
    tag: tag.toString("base64"),
    data: data.toString("base64")
  };
}

function unsealPayload(envelope, password) {
  if (!envelope || !envelope.data || !envelope.iv || !envelope.tag) {
    throw new Error("bad envelope");
  }
  const key = sealKeyFromPassword(password);
  const decipher = crypto.createDecipheriv("aes-256-gcm", key, Buffer.from(envelope.iv, "base64"));
  decipher.setAuthTag(Buffer.from(envelope.tag, "base64"));
  const plain = Buffer.concat([
    decipher.update(Buffer.from(envelope.data, "base64")),
    decipher.final()
  ]).toString("utf8");
  return JSON.parse(plain);
}

module.exports = {
  SEAL_SALT,
  SEAL_ITERATIONS,
  sealKeyFromPassword,
  sealPayload,
  unsealPayload
};
