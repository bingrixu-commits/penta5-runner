"use strict";

const fs = require("fs");
const path = require("path");
const mysql = require("mysql2/promise");

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const text = fs.readFileSync(filePath, "utf8");
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

function resolveEnvPath() {
  const candidates = [
    process.env.PENTA5_ENV_PATH,
    path.join(__dirname, "..", ".env"),
    path.join(__dirname, ".env"),
    "/root/penta5-runner/.env",
  ].filter(Boolean);
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return candidates[0];
}

loadEnvFile(resolveEnvPath());

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing env ${name}`);
  return value;
}

function createPool() {
  return mysql.createPool({
    host: required("RDS_HOST"),
    port: Number(process.env.RDS_PORT || 3306),
    user: required("RDS_USER"),
    password: required("RDS_PASSWORD"),
    database: required("RDS_DATABASE"),
    waitForConnections: true,
    connectionLimit: Number(process.env.RDS_POOL_LIMIT || 8),
    enableKeepAlive: true,
    timezone: "Z",
    dateStrings: true,
  });
}

module.exports = {
  createPool,
  loadEnvFile,
  resolveEnvPath,
};
