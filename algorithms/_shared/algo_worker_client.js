"use strict";

/**
 * Sync client for algo_worker_dispatcher.
 * Reads one JSON request from stdin, writes one JSON response to stdout, exits.
 */

const fs = require("fs");
const net = require("net");
const path = require("path");

function sockPath() {
  return String(process.env.PENTA5_ALGO_DISPATCH_SOCK || path.join(__dirname, "..", "..", "data", "algo-dispatch.sock"));
}

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on("data", (c) => chunks.push(c));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
}

function requestDispatch(req) {
  return new Promise((resolve) => {
    const target = sockPath();
    if (!fs.existsSync(target)) {
      resolve({ ok: false, error: "dispatch-sock-missing:" + target });
      return;
    }
    const timeout = Math.max(5000, Number(process.env.PENTA5_ALGO_DISPATCH_CLIENT_TIMEOUT_MS || 120000) || 120000);
    const socket = net.createConnection(target);
    const out = [];
    let settled = false;
    const finish = (payload) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { socket.destroy(); } catch (_) {}
      resolve(payload);
    };
    const timer = setTimeout(() => finish({ ok: false, error: "dispatch-client-timeout" }), timeout);
    socket.setEncoding("utf8");
    socket.on("connect", () => {
      const payload = JSON.stringify(req);
      socket.write(payload);
      socket.end();
    });
    socket.on("data", (chunk) => out.push(chunk));
    socket.on("end", () => {
      const text = out.join("");
      if (!text) {
        finish({ ok: false, error: "dispatch-empty-response" });
        return;
      }
      try {
        finish(JSON.parse(text));
      } catch (err) {
        finish({
          ok: false,
          error: "invalid-dispatch-json: " + (err && err.message ? err.message : String(err)),
          raw: text.slice(0, 200)
        });
      }
    });
    socket.on("error", (err) => {
      finish({ ok: false, error: "dispatch-connect-failed: " + (err && err.message ? err.message : String(err)) });
    });
  });
}

async function main() {
  let req;
  try {
    const raw = await readStdin();
    req = raw ? JSON.parse(raw) : {};
  } catch (err) {
    process.stdout.write(JSON.stringify({ ok: false, error: "invalid-json: " + (err && err.message) }));
    process.exit(2);
    return;
  }
  const result = await requestDispatch(req);
  process.stdout.write(JSON.stringify(result || { ok: false, error: "empty-result" }));
  process.exit(result && result.ok !== false ? 0 : 1);
}

if (require.main === module) {
  main().catch((err) => {
    process.stdout.write(JSON.stringify({ ok: false, error: err && err.message ? err.message : String(err) }));
    process.exit(1);
  });
}
