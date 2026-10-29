"use strict";

/**
 * GPU 机回删计算服务（独立于主服务器 runner）
 * 用法: node gpu_backdelete_server.js   （默认 0.0.0.0:8790）
 *
 * 依赖: txt_attack_convert.js + backdelete_snapshot_store.js（零 npm 依赖）
 * 接口:
 *   GET  /health               -> {ok:true, service:"backdelete-gpu", converterVersion}
 *   POST /generate             -> {ok:true, reused, snapshot} | {ok:false,error}
 *        body: { attackText, target, panelThreeDuishuPair, historyContext }
 *   GET  /latest               -> {ok:true, snapshot} | {ok:false}
 */

const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.env.BACKDELETE_PORT || 8790);
const BIND = String(process.env.BACKDELETE_BIND || "0.0.0.0");
const DATA_DIR = String(process.env.BACKDELETE_DATA_DIR || path.join(__dirname, "data"));

const attackTxtConverter = require("./txt_attack_convert.js").Penta5AttackTxtConvert;
const { createBackdeleteSnapshotStore } = require("./backdelete_snapshot_store");

if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

const store = createBackdeleteSnapshotStore({
  dataDir: DATA_DIR,
  converter: attackTxtConverter
});

const converterVersion = String(attackTxtConverter.THREE_DING_BACKDELETE_VERSION || "unknown");

function sendJson(res, status, body) {
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(text, "utf8"),
    "Cache-Control": "no-store"
  });
  res.end(text);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > 5 * 1024 * 1024) {
        reject(new Error("body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, "http://127.0.0.1");
    const pathname = url.pathname;

    if (req.method === "GET" && pathname === "/health") {
      sendJson(res, 200, { ok: true, service: "backdelete-gpu", converterVersion, pid: process.pid });
      return;
    }

    if (req.method === "GET" && pathname === "/latest") {
      const latest = store.latest();
      sendJson(res, 200, latest ? { ok: true, snapshot: latest } : { ok: false, error: "no-snapshot" });
      return;
    }

    if (req.method === "POST" && pathname === "/generate") {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw || "{}"); } catch (_) { payload = {}; }
      const historyContext = payload && typeof payload.historyContext === "object" ? payload.historyContext : {};
      const generated = store.generate({
        attackText: payload.attackText,
        target: payload.target,
        panelThreeDuishuPair: payload.panelThreeDuishuPair,
        historyContext
      });
      sendJson(res, 200, {
        ok: true,
        reused: generated.reused === true,
        converterVersion,
        snapshot: generated.snapshot
      });
      return;
    }

    sendJson(res, 404, { ok: false, error: "Not Found", endpoints: ["GET /health", "POST /generate", "GET /latest"] });
  } catch (error) {
    sendJson(res, 422, { ok: false, error: String(error && error.message || error) });
  }
});

server.listen(PORT, BIND, () => {
  console.log("[backdelete-gpu] listening " + BIND + ":" + PORT + " converter=" + converterVersion);
});
