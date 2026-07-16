const http = require("http");
const https = require("https");

const HOST = process.env.SOURCE_PROXY_HOST || "0.0.0.0";
const PORT = Number(process.env.SOURCE_PROXY_PORT || process.env.PORT || 3000);
const TARGET_URL = process.env.SOURCE_PROXY_TARGET_URL || "https://www.uk-wl.net/Penta5Classic/0d2d48772cd48f41e761ef99f40e3ac0/latest";
const REQUEST_TIMEOUT = Number(process.env.SOURCE_PROXY_TIMEOUT || 10000);

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Content-Length": Buffer.byteLength(body)
  });
  res.end(body);
}

function freshUrl(url) {
  const joiner = String(url).indexOf("?") >= 0 ? "&" : "?";
  return `${url}${joiner}_=${Date.now()}`;
}

function fetchTarget(callback) {
  const req = https.get(freshUrl(TARGET_URL), {
    timeout: REQUEST_TIMEOUT,
    headers: {
      "User-Agent": "Mozilla/5.0",
      "Accept": "application/json,text/plain,*/*",
      "Cache-Control": "no-cache"
    }
  }, (upstream) => {
    const chunks = [];
    upstream.on("data", (chunk) => chunks.push(chunk));
    upstream.on("end", () => {
      const body = Buffer.concat(chunks);
      callback(null, {
        statusCode: upstream.statusCode || 200,
        contentType: upstream.headers["content-type"] || "application/json; charset=utf-8",
        body
      });
    });
  });
  req.on("timeout", () => {
    req.destroy(new Error(`source request timeout after ${REQUEST_TIMEOUT}ms`));
  });
  req.on("error", (error) => callback(error));
}

const server = http.createServer((req, res) => {
  if (req.method === "OPTIONS") {
    sendJson(res, 204, {});
    return;
  }
  const path = String(req.url || "/").split("?", 1)[0];
  if (path === "/health") {
    sendJson(res, 200, {
      ok: true,
      time: new Date().toISOString(),
      targetUrl: TARGET_URL,
      port: PORT
    });
    return;
  }
  if (path !== "/" && path !== "/latest") {
    sendJson(res, 404, {
      ok: false,
      error: "not found",
      endpoints: ["GET /latest", "GET /health"]
    });
    return;
  }
  fetchTarget((error, result) => {
    if (error) {
      sendJson(res, 502, {
        ok: false,
        time: new Date().toISOString(),
        error: error.message || String(error),
        targetUrl: TARGET_URL
      });
      return;
    }
    res.writeHead(result.statusCode, {
      "Content-Type": result.contentType,
      "Cache-Control": "no-store",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type,Authorization",
      "Content-Length": result.body.length
    });
    res.end(result.body);
  });
});

server.listen(PORT, HOST, () => {
  console.log(`Penta5 source proxy listening on http://${HOST}:${PORT}/latest`);
  console.log(`Target URL: ${TARGET_URL}`);
});
