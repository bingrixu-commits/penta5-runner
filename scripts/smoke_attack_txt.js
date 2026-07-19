#!/usr/bin/env node
const fs = require("fs");
const http = require("http");
const path = require("path");

function loadEnv(file) {
  const out = {};
  if (!fs.existsSync(file)) return out;
  fs.readFileSync(file, "utf8").split(/\r?\n/).forEach((line) => {
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!m) return;
    let v = m[2].trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    out[m[1]] = v;
  });
  return out;
}

function request(method, urlPath, { body, cookie } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: "127.0.0.1",
      port: 3001,
      path: urlPath,
      method,
      headers: {
        ...(body ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) } : {}),
        ...(cookie ? { Cookie: cookie } : {})
      }
    }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        const buf = Buffer.concat(chunks);
        const setCookie = res.headers["set-cookie"];
        resolve({
          status: res.statusCode,
          headers: res.headers,
          body: buf.toString("utf8"),
          cookie: Array.isArray(setCookie) ? setCookie.map((x) => String(x).split(";")[0]).join("; ") : ""
        });
      });
    });
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

(async () => {
  const env = loadEnv(path.join("/root/penta5-runner", ".env"));
  const password = env.PANEL_VIEWER_PASSWORD || env.PANEL_PASSWORD || "";
  if (!password) throw new Error("no panel password in .env");
  const login = await request("POST", "/login", { body: JSON.stringify({ password }) });
  if (login.status !== 200) throw new Error("login failed " + login.status + " " + login.body.slice(0, 200));
  const cookie = login.cookie;
  const checks = ["/attack-txt", "/gongjiyu", "/txt_attack_convert.js", "/attack-txt-manifest.json", "/attack-txt-icon.svg", "/converter-history?limit=50"];
  for (const p of checks) {
    const r = await request("GET", p, { cookie });
    console.log(p, r.status, r.body.length);
    if (r.status !== 200) throw new Error("bad status for " + p);
  }
  const html = await request("GET", "/attack-txt", { cookie });
  if (!/攻击语转TXT/.test(html.body)) throw new Error("html missing title");
  if (!/三定转换（回删490-499）/.test(html.body)) throw new Error("html missing three-ding button");
  if (!/txt_attack_convert\.js/.test(html.body)) throw new Error("html missing script");
  const js = await request("GET", "/txt_attack_convert.js", { cookie });
  if (!/Penta5AttackTxtConvert/.test(js.body)) throw new Error("js missing export");
  if (!/convertThreeDingBackDelete/.test(js.body)) throw new Error("js missing three-ding backdelete");
  if (!/百十个/.test(js.body) || !/blankIndex/.test(js.body)) throw new Error("js missing multi-combo three-ding");
  if (!/万十个/.test(js.body)) throw new Error("js missing 万十个 alias");
  const hist = await request("GET", "/converter-history?limit=20", { cookie });
  const histJson = JSON.parse(hist.body);
  if (!histJson.ok || !Array.isArray(histJson.history)) throw new Error("converter-history payload bad");
  console.log("SMOKE_OK history=", histJson.historyCount);
})().catch((err) => {
  console.error("SMOKE_FAIL", err && err.message ? err.message : err);
  process.exit(1);
});
