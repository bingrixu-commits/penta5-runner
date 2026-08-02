#!/usr/bin/env python3
"""已废弃：penta5-source-proxy 已下线，开奖源直连官网。

本文件仅保留透明转发占位，禁止再注入假命中率/假排除模型。
"""
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen

UPSTREAM_URL = "https://www.uk-wl.net/Penta5Classic/0d2d48772cd48f41e761ef99f40e3ac0/latest"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            req = Request(UPSTREAM_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=10) as response:
                raw_data = response.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw_data)
        except Exception as e:
            logging.error("Error handling request: %s", e)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))


if __name__ == "__main__":
    logging.warning("remote_state_proxy is deprecated; runner should use SOURCE_API_URL official latest")
    server = HTTPServer(("0.0.0.0", 3000), ProxyHandler)
    logging.info("Proxy server running on port 3000 (passthrough only)...")
    server.serve_forever()
