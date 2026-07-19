#!/usr/bin/env python3
"""Temporary backup download server on port 3019 (no login)."""
import http.server
import os
import socketserver

PORT = 3019
ROOT = "/tmp/penta5-dl-live"
NAME = "penta5-runner-full-backup.tar.gz"


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/download", "/" + NAME, "/penta5-runner-full-backup.tar.gz"):
            file_path = os.path.join(ROOT, NAME)
            size = os.path.getsize(file_path)
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(size))
            self.send_header(
                "Content-Disposition",
                'attachment; filename="penta5-runner-full-backup.tar.gz"',
            )
            self.end_headers()
            with open(file_path, "rb") as handle:
                while True:
                    chunk = handle.read(262144)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return
        self.send_error(404)


def main():
    os.makedirs(ROOT, exist_ok=True)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print("READY", PORT, flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
