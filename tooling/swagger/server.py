#!/usr/bin/env python3
"""WebBlock Platform: Combined Swagger & OpenAPI Portal with Reverse Proxy

Aggregates API specifications across all WebBlock microservices and reverse-proxies
all HTTP requests (GET, POST, PUT, DELETE, PATCH, OPTIONS) so you can test endpoints
seamlessly via the Swagger UI dashboard or direct curl calls:
  - User Service: /api/user/** -> http://localhost:8081
  - Project Service: /api/v1/** -> http://localhost:8080
  - AI Code Service: /v1/projects** -> http://localhost:8000
  - Provisioning Service: /v1/releases**, /v1/admin** -> http://localhost:8084
  - Notification Service: /api/notifications/** -> http://localhost:8085
  - Dedicated Proxy Route: /proxy/<service_key>/** -> http://localhost:<port>/**
"""

import http.server
import json
import os
import socketserver
import sys
import urllib.error
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("SWAGGER_PORT", 8088))
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

SERVICES = {
    "user-service": {
        "name": "User Service (Port 8081)",
        "base_url": "http://127.0.0.1:8081",
        "spec_url": "http://127.0.0.1:8081/v3/api-docs",
        "doc_url": "http://127.0.0.1:8081/swagger-ui.html",
        "port": 8081,
    },
    "project-service": {
        "name": "Project Service (Port 8080)",
        "base_url": "http://127.0.0.1:8080",
        "spec_url": "http://127.0.0.1:8080/v3/api-docs",
        "doc_url": "http://127.0.0.1:8080/swagger-ui.html",
        "port": 8080,
    },
    "aicode-service": {
        "name": "AI Code Service (Port 8000)",
        "base_url": "http://127.0.0.1:8000",
        "spec_url": "http://127.0.0.1:8000/openapi.json",
        "doc_url": "http://127.0.0.1:8000/docs",
        "port": 8000,
    },
    "provisioning-service": {
        "name": "Provisioning Service (Port 8084)",
        "base_url": "http://127.0.0.1:8084",
        "spec_url": "http://127.0.0.1:8084/openapi.json",
        "file_fallback": str(ROOT_DIR / "contracts" / "openapi" / "provisioning-service.v1.yaml"),
        "doc_url": "http://127.0.0.1:8084/docs",
        "port": 8084,
    },
    "notification-service": {
        "name": "Notification Service (Port 8085)",
        "base_url": "http://127.0.0.1:8085",
        "spec_url": "http://127.0.0.1:8085/v3/api-docs",
        "doc_url": "http://127.0.0.1:8085/swagger-ui.html",
        "port": 8085,
    },
}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>WebBlock Platform — Combined API Documentation</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui.css" />
  <link rel="icon" type="image/png" href="https://unpkg.com/swagger-ui-dist@5.11.0/favicon-32x32.png" />
  <style>
    html { box-sizing: border-box; overflow: -moz-scrollbars-vertical; overflow-y: scroll; }
    *, *:before, *:after { box-sizing: inherit; }
    body {
      margin: 0;
      background: #f7f9fc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    .topbar {
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      color: #ffffff;
      padding: 14px 28px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      box-shadow: 0 4px 12px rgba(0,0,0,0.15);
      position: sticky;
      top: 0;
      z-index: 999;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand-logo {
      width: 32px;
      height: 32px;
      background: linear-gradient(135deg, #3b82f6, #8b5cf6);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: bold;
      font-size: 18px;
      color: white;
    }
    .brand-title {
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0.5px;
    }
    .brand-subtitle {
      font-size: 12px;
      color: #94a3b8;
    }
    .service-nav {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .selector-label {
      font-size: 13px;
      color: #cbd5e1;
      font-weight: 500;
    }
    select#service-select {
      background: #334155;
      color: #f8fafc;
      border: 1px solid #475569;
      border-radius: 6px;
      padding: 8px 14px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      outline: none;
      transition: all 0.2s;
    }
    select#service-select:hover, select#service-select:focus {
      border-color: #3b82f6;
      background: #1e293b;
    }
    .status-badges {
      display: flex;
      gap: 8px;
      margin-left: 12px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
      text-decoration: none;
      background: #1e293b;
      color: #94a3b8;
      border: 1px solid #334155;
      transition: all 0.15s;
    }
    .badge:hover {
      border-color: #60a5fa;
      color: #ffffff;
    }
    .dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #94a3b8;
    }
    .dot.online { background: #22c55e; box-shadow: 0 0 6px #22c55e; }
    .dot.offline { background: #ef4444; }
    #swagger-ui {
      max-width: 1400px;
      margin: 0 auto;
      padding: 20px;
    }
    .swagger-ui .topbar { display: none !important; }
  </style>
</head>
<body>
  <div class="topbar">
    <div class="brand">
      <div class="brand-logo">W</div>
      <div>
        <div class="brand-title">WebBlock Platform</div>
        <div class="brand-subtitle">Unified Microservices API Documentation & Gateway</div>
      </div>
    </div>
    <div class="service-nav">
      <label class="selector-label" for="service-select">Select Service:</label>
      <select id="service-select" onchange="changeService(this.value)">
        __OPTIONS__
      </select>
      <div class="status-badges" id="badges-container">
        __BADGES__
      </div>
    </div>
  </div>

  <div id="swagger-ui"></div>

  <script src="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui-bundle.js"></script>
  <script src="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui-standalone-preset.js"></script>
  <script>
    const SERVICES = __SERVICES_JSON__;

    let ui;

    function initSwagger(serviceKey) {
      const specUrl = '/specs/' + serviceKey;
      ui = SwaggerUIBundle({
        url: specUrl,
        dom_id: '#swagger-ui',
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIStandalonePreset
        ],
        plugins: [
          SwaggerUIBundle.plugins.DownloadUrl
        ],
        layout: "BaseLayout",
        displayRequestDuration: true,
        docExpansion: "list",
        filter: true,
        persistAuthorization: true,
        requestInterceptor: (req) => {
          req.headers['ngrok-skip-browser-warning'] = 'true';
          return req;
        }
      });
      window.ui = ui;
    }

    function changeService(key) {
      window.location.hash = '#' + key;
      initSwagger(key);
    }

    window.onload = function() {
      let initialKey = window.location.hash.replace('#', '');
      if (!SERVICES[initialKey]) {
        initialKey = Object.keys(SERVICES)[0];
      }
      document.getElementById('service-select').value = initialKey;
      initSwagger(initialKey);
      checkStatuses();
    };

    function checkStatuses() {
      fetch('/api/statuses')
        .then(r => r.json())
        .then(data => {
          for (const [key, online] of Object.entries(data)) {
            const dot = document.getElementById('dot-' + key);
            if (dot) {
              dot.className = 'dot ' + (online ? 'online' : 'offline');
              const parent = dot.parentElement;
              if (parent) {
                parent.title = (online ? 'Online' : 'Offline') + ' (Port ' + SERVICES[key].port + ')';
              }
            }
          }
        })
        .catch(() => {});
      setTimeout(checkStatuses, 5000);
    }
  </script>
</body>
</html>
"""


def resolve_proxy_target(full_path):
    """Resolves an incoming request path to the appropriate backend microservice URL."""
    path_only = full_path.split("?")[0]
    query = ("?" + full_path.split("?", 1)[1]) if "?" in full_path else ""

    # 1. Explicit proxy routing: /proxy/<service_key>/<subpath>
    if path_only.startswith("/proxy/"):
        after_proxy = path_only[len("/proxy/"):]
        parts = after_proxy.split("/", 1)
        service_key = parts[0]
        subpath = ("/" + parts[1]) if len(parts) > 1 else "/"
        if service_key in SERVICES:
            base = SERVICES[service_key]["base_url"]
            return f"{base}{subpath}{query}"

    # 2. Heuristic path routing for direct API calls:
    # User Service: /api/user/**
    if path_only.startswith("/api/user/"):
        return f"{SERVICES['user-service']['base_url']}{full_path}"

    # Project Service: /api/v1/** or /internal/**
    if path_only.startswith("/api/v1/") or path_only.startswith("/internal/"):
        return f"{SERVICES['project-service']['base_url']}{full_path}"

    # Notification Service: /api/notifications/** or /notifications/**
    if path_only.startswith("/api/notifications/") or path_only.startswith("/notifications/"):
        return f"{SERVICES['notification-service']['base_url']}{full_path}"

    # Provisioning Service: /v1/releases**, /v1/commands**, /v1/admin**, /mcp**
    if (
        path_only.startswith("/v1/releases")
        or path_only.startswith("/v1/commands")
        or path_only.startswith("/v1/deployments")
        or path_only.startswith("/v1/admin")
        or path_only.startswith("/mcp")
    ):
        return f"{SERVICES['provisioning-service']['base_url']}{full_path}"

    # AI Code Service: /v1/projects**, /v1/generate**, /v1/chat**, /v1/preview**
    if (
        path_only.startswith("/v1/projects")
        or path_only.startswith("/v1/generate")
        or path_only.startswith("/v1/chat")
        or path_only.startswith("/v1/preview")
    ):
        return f"{SERVICES['aicode-service']['base_url']}{full_path}"

    return None


class SwaggerPortalHandler(http.server.BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_HEAD(self):
        target = resolve_proxy_target(self.path)
        if target:
            self.forward_request("HEAD", target)
        else:
            self.send_response(200)
            self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            self.serve_index()
            return

        if path == "/api/statuses":
            self.serve_statuses()
            return

        if path.startswith("/specs/"):
            service_key = path[len("/specs/"):]
            self.serve_spec(service_key)
            return

        # Check if this GET request targets a microservice
        target = resolve_proxy_target(self.path)
        if target:
            self.forward_request("GET", target)
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        target = resolve_proxy_target(self.path)
        if target:
            self.forward_request("POST", target)
        else:
            self.send_error(404, f"No target service mapped for path: {self.path}")

    def do_PUT(self):
        target = resolve_proxy_target(self.path)
        if target:
            self.forward_request("PUT", target)
        else:
            self.send_error(404, f"No target service mapped for path: {self.path}")

    def do_DELETE(self):
        target = resolve_proxy_target(self.path)
        if target:
            self.forward_request("DELETE", target)
        else:
            self.send_error(404, f"No target service mapped for path: {self.path}")

    def do_PATCH(self):
        target = resolve_proxy_target(self.path)
        if target:
            self.forward_request("PATCH", target)
        else:
            self.send_error(404, f"No target service mapped for path: {self.path}")

    def forward_request(self, method, target_url):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        headers = {}
        for h, v in self.headers.items():
            if h.lower() not in ("host", "content-length"):
                headers[h] = v

        req = urllib.request.Request(target_url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                for h, v in resp.headers.items():
                    if h.lower() not in ("content-length", "transfer-encoding", "access-control-allow-origin"):
                        self.send_header(h, v)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as err:
            err_body = err.read()
            self.send_response(err.code)
            for h, v in err.headers.items():
                if h.lower() not in ("content-length", "transfer-encoding", "access-control-allow-origin"):
                    self.send_header(h, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as exc:
            err_msg = json.dumps({
                "instance": self.path,
                "status": 502,
                "title": "Bad Gateway / Proxy Error",
                "type": "urn:webblock:problem:gateway-error",
                "detail": str(exc),
                "target": target_url,
            }).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/problem+json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(err_msg)))
            self.end_headers()
            self.wfile.write(err_msg)

    def serve_index(self):
        options_html = []
        badges_html = []
        for key, info in SERVICES.items():
            options_html.append(f'<option value="{key}">{info["name"]}</option>')
            badges_html.append(
                f'<a href="{info["doc_url"]}" target="_blank" class="badge" title="Port {info["port"]}">'
                f'<span class="dot" id="dot-{key}"></span>{key}</a>'
            )

        html = (
            HTML_TEMPLATE.replace("__OPTIONS__", "\n        ".join(options_html))
            .replace("__BADGES__", "\n        ".join(badges_html))
            .replace("__SERVICES_JSON__", json.dumps(SERVICES))
        )

        content = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def serve_statuses(self):
        statuses = {}
        for key, info in SERVICES.items():
            target = info["spec_url"]
            is_up = False
            try:
                req = urllib.request.Request(target, headers={"User-Agent": "SwaggerPortal"})
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status < 500:
                        is_up = True
            except Exception:
                if "file_fallback" in info and Path(info["file_fallback"]).is_file():
                    is_up = True
            statuses[key] = is_up

        body = json.dumps(statuses).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_spec(self, service_key):
        if service_key not in SERVICES:
            self.send_error(404, f"Unknown service: {service_key}")
            return

        info = SERVICES[service_key]
        target = info["spec_url"]
        spec_dict = None

        # 1. Try fetching live spec from service endpoint
        try:
            req = urllib.request.Request(target, headers={"User-Agent": "SwaggerPortal"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = resp.read()
                try:
                    spec_dict = json.loads(data.decode("utf-8"))
                except Exception:
                    pass
        except Exception:
            pass

        # 2. Check local file fallback (e.g. YAML contracts)
        if spec_dict is None and "file_fallback" in info:
            fb_path = Path(info["file_fallback"])
            if fb_path.is_file():
                try:
                    import yaml
                    with open(fb_path, "r", encoding="utf-8") as f:
                        spec_dict = yaml.safe_load(f)
                except Exception:
                    pass

        # 3. Fallback spec if service is still booting
        if spec_dict is None:
            spec_dict = {
                "openapi": "3.0.0",
                "info": {
                    "title": f"{info['name']} (Starting...)",
                    "version": "1.0.0",
                    "description": (
                        f"⚠️ The service at `{target}` is currently starting up or offline.<br>"
                        "Please wait a few seconds and refresh this page once the service completes initialization."
                    ),
                },
                "paths": {},
            }

        # Inject reverse-proxy server URL so Swagger UI "Try it out" works seamlessly
        spec_dict["servers"] = [{"url": f"/proxy/{service_key}", "description": "WebBlock Portal Proxy"}]

        data = json.dumps(spec_dict).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


def run():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), SwaggerPortalHandler) as httpd:
        print(f"✨ Combined Swagger UI & Gateway running at http://localhost:{PORT}")
        httpd.serve_forever()


if __name__ == "__main__":
    run()
