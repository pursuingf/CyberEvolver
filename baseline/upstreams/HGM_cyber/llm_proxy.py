#!/usr/bin/env python3
"""
Lightweight HTTP→HTTPS reverse proxy for LLM API endpoints.

Runs on the host so it can access tailscale/VPN networks.
Docker containers connect via plain HTTP to this proxy,
which forwards to the actual HTTPS endpoint.

Usage:
    # Single endpoint
    python llm_proxy.py --upstream https://kimi-k25-fyh-128k.openapi-qb-ai.sii.edu.cn --port 8880

    # Auto-detect from model.yml (proxy all endpoints that need it)
    python llm_proxy.py --model-yml configs/model.yml --base-port 8880
"""

import argparse
import http.server
import json
import os
import ssl
import sys
import threading
import urllib.request
import urllib.error
from urllib.parse import urlparse


class LLMProxyHandler(http.server.BaseHTTPRequestHandler):
    """Forwards HTTP requests to an HTTPS upstream, preserving path and headers."""

    upstream_base = ""  # Set by factory

    def _proxy(self):
        url = self.upstream_base.rstrip("/") + self.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else None

        req = urllib.request.Request(url, data=body, method=self.command)
        # Forward essential headers
        for key in ["Content-Type", "Authorization", "Accept"]:
            val = self.headers.get(key)
            if val:
                req.add_header(key, val)

        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=300, context=ctx) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                for key in ["Content-Type"]:
                    val = resp.headers.get(key)
                    if val:
                        self.send_header(key, val)
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            err_msg = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_msg)))
            self.end_headers()
            self.wfile.write(err_msg)

    do_GET = _proxy
    do_POST = _proxy
    do_PUT = _proxy
    do_DELETE = _proxy
    do_OPTIONS = _proxy

    def log_message(self, fmt, *args):
        # Quieter logging
        sys.stderr.write(f"[proxy] {self.upstream_base} | {fmt % args}\n")


def make_handler(upstream_base):
    """Create a handler class bound to a specific upstream."""
    class Handler(LLMProxyHandler):
        pass
    Handler.upstream_base = upstream_base
    return Handler


def _find_free_port(start, bind="0.0.0.0", max_tries=100):
    """Find a free port starting from `start`."""
    import socket
    for p in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((bind, p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"No free port found in range {start}-{start+max_tries}")


def start_proxy(upstream, port, bind="0.0.0.0"):
    """Start a single proxy in a thread. Auto-selects next free port on conflict."""
    handler = make_handler(upstream)
    actual_port = _find_free_port(port, bind)
    if actual_port != port:
        print(f"  Port {port} in use, using {actual_port}")
    server = http.server.HTTPServer((bind, actual_port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"  Proxy :{actual_port} → {upstream}")
    return thread, server, actual_port


def load_from_model_yml(yml_path, base_port):
    """Read model.yml, start a proxy for each HTTPS endpoint.
    Returns dict: {model_name: proxy_base_url}."""
    import yaml
    with open(yml_path) as f:
        models = yaml.safe_load(f) or {}

    proxies = {}
    port = base_port
    seen_upstreams = {}  # upstream → (port, proxy_url) dedup

    for model_name, cfg in models.items():
        base_url = cfg.get("openai_api_base", "")
        if not base_url:
            continue

        parsed = urlparse(base_url)
        # Only proxy HTTPS endpoints
        if parsed.scheme != "https":
            continue

        # Upstream = scheme + host (without /v1 path)
        upstream = f"{parsed.scheme}://{parsed.netloc}"

        if upstream in seen_upstreams:
            # Reuse same proxy for same upstream
            proxy_port = seen_upstreams[upstream]
        else:
            _, _, actual_port = start_proxy(upstream, port)
            proxy_port = actual_port
            seen_upstreams[upstream] = actual_port
            port = actual_port + 1

        # Reconstruct proxy URL preserving the path (e.g. /v1)
        proxy_url = f"http://{{host}}:{proxy_port}{parsed.path}"
        proxies[model_name] = proxy_url

    return proxies


def main():
    parser = argparse.ArgumentParser(description="LLM API reverse proxy")
    parser.add_argument("--upstream", help="Single upstream URL (e.g. https://example.com)")
    parser.add_argument("--port", type=int, default=8880)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--model-yml", help="Path to model.yml (auto-proxy all endpoints)")
    parser.add_argument("--base-port", type=int, default=8880)
    args = parser.parse_args()

    print("=" * 60)
    print("  LLM API Proxy")
    print("=" * 60)

    if args.model_yml:
        proxies = load_from_model_yml(args.model_yml, args.base_port)
        print(f"\n  Proxied models:")
        for name, url in proxies.items():
            print(f"    {name} → {url}")
        print()

        # Write proxy mapping for consumers
        mapping_path = os.path.join(os.path.dirname(args.model_yml), ".llm_proxy_mapping.json")
        with open(mapping_path, "w") as f:
            json.dump(proxies, f, indent=2)
        print(f"  Mapping saved: {mapping_path}")
    elif args.upstream:
        start_proxy(args.upstream, args.port, args.bind)
    else:
        parser.error("Provide --upstream or --model-yml")

    print("  Proxy running. Ctrl+C to stop.")
    print("=" * 60)
    sys.stdout.flush()

    # Block forever
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nProxy stopped.")


if __name__ == "__main__":
    main()
