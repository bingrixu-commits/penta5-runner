"use strict";

const http = require("http");
const https = require("https");
const { URL } = require("url");

global.AbortController = global.AbortController || class {
  constructor() { this.signal = {}; }
  abort() {}
};

global.fetch = global.fetch || function(url, options) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const lib = u.protocol === "https:" ? https : http;
    const req = lib.request({
      method: options && options.method ? options.method : "GET",
      hostname: u.hostname,
      port: u.port || (u.protocol === "https:" ? 443 : 80),
      path: u.pathname + u.search,
      headers: options && options.headers ? options.headers : {}
    }, res => {
      let data = "";
      res.setEncoding("utf8");
      res.on("data", chunk => data += chunk);
      res.on("end", () => resolve({
        ok: res.statusCode >= 200 && res.statusCode < 300,
        status: res.statusCode,
        statusText: res.statusMessage,
        headers: { get: name => res.headers[String(name).toLowerCase()] || "" },
        text: async () => data,
        json: async () => JSON.parse(data)
      }));
    });
    req.on("error", reject);
    req.end();
  });
};

require("./penta5_backend_runner.js");
