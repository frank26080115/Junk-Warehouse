#!/usr/bin/env python3
# scripts/serve_frontend.py

"""
# Build and serve, and if Flask on 5000 isn't running, start it too
python scripts/serve_frontend.py --build --ensure-backend

# Serve existing build and ensure backend
python scripts/serve_frontend.py --ensure-backend

# Custom ports/hosts
python scripts/serve_frontend.py --host 0.0.0.0 --port 8080 \
  --ensure-backend --backend-host 127.0.0.1 --backend-port 5001
"""


from __future__ import annotations

import argparse
import mimetypes
import os
import socket
import subprocess
import sys
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

# MIME types (some systems miss these)
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/wasm", ".wasm")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = REPO_ROOT / "frontend" / "dist"
DEFAULT_FRONTEND_DIR = REPO_ROOT / "frontend"
DEFAULT_BACKEND_DIR = REPO_ROOT / "backend"


class SPAHandler(SimpleHTTPRequestHandler):
    """Static file server with SPA fallback to index.html."""

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(
            "%s - - [%s] %s\n"
            % (self.address_string(), self.log_date_time_string(), format % args)
        )

    def do_GET(self) -> None:
        candidate = Path(self.translate_path(self.path))
        if candidate.is_dir():
            index = candidate / "index.html"
            if index.exists():
                self.path = str(Path(self.path).joinpath("index.html"))
                return SimpleHTTPRequestHandler.do_GET(self)
        if candidate.exists():
            return SimpleHTTPRequestHandler.do_GET(self)
        last = Path(self.path).name
        if "." not in last or self.path == "/" or self.path.endswith("/"):
            self.path = "/index.html"
            return SimpleHTTPRequestHandler.do_GET(self)
        return SimpleHTTPRequestHandler.do_GET(self)

    def do_HEAD(self) -> None:
        candidate = Path(self.translate_path(self.path))
        if candidate.is_dir():
            index = candidate / "index.html"
            if index.exists():
                self.path = str(Path(self.path).joinpath("index.html"))
                return SimpleHTTPRequestHandler.do_HEAD(self)
        if candidate.exists():
            return SimpleHTTPRequestHandler.do_HEAD(self)
        last = Path(self.path).name
        if "." not in last or self.path == "/" or self.path.endswith("/"):
            self.path = "/index.html"
            return SimpleHTTPRequestHandler.do_HEAD(self)
        return SimpleHTTPRequestHandler.do_HEAD(self)


def run_build(frontend_dir: Path) -> None:
    print(f"⏳ Building frontend in {frontend_dir} …")
    try:
        subprocess.run(["npm", "run", "build"], cwd=str(frontend_dir), check=True)
    except FileNotFoundError:
        raise SystemExit("Error: 'npm' not found on PATH.")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"'npm run build' failed with exit code {e.returncode}")


def is_port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def http_ok(url: str, timeout: float = 0.5) -> bool:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except URLError:
        return False


def start_backend_subprocess(
    backend_dir: Path,
    host: str,
    port: int,
    app_path: str = "app.main:app",
    no_reload: bool = True,
    no_debugger: bool = True,
) -> subprocess.Popen:
    """
    Start Flask via CLI as a separate process, working dir at backend/.
    We avoid the auto-reloader to prevent double processes.
    """
    cmd = [
        sys.executable,
        "-m",
        "flask",
        "--app",
        app_path,
        "run",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if no_reload:
        cmd.append("--no-reload")
    if no_debugger:
        cmd.append("--no-debugger")

    env = os.environ.copy()
    # If you rely on backend/.env being loaded by your app code, no need to do anything here.
    # (Your app.main loads dotenv explicitly in our earlier setup.)
    print(f"🚀 Starting backend: {' '.join(cmd)}  (cwd={backend_dir})")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(backend_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError:
        raise SystemExit("Error: Python or Flask CLI not found. Is Flask installed in this interpreter?")
    return proc


def wait_for_backend(host: str, port: int, health_path: str = "/api/health", timeout_s: float = 20.0) -> bool:
    """Poll for backend readiness (first try health endpoint, then plain TCP)."""
    url = f"http://{host}:{port}{health_path}"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if http_ok(url) or is_port_open(host, port):
            return True
        time.sleep(0.3)
    return False


def main():
    ap = argparse.ArgumentParser(description="Serve built frontend (Vite dist) with optional build and backend bootstrap.")
    ap.add_argument("--dist", default=str(DEFAULT_DIST), help="Directory to serve (default: frontend/dist)")
    ap.add_argument("--host", default="127.0.0.1", help="Frontend server host (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=4173, help="Frontend server port (default: 4173)")
    ap.add_argument("--build", action="store_true", help="Run 'npm run build' in frontend/ before serving")

    # Backend integration
    ap.add_argument("--ensure-backend", action="store_true", help="If backend port is not serving, start Flask as a child process")
    ap.add_argument("--backend-host", default="127.0.0.1", help="Backend host to check/start (default: 127.0.0.1)")
    ap.add_argument("--backend-port", type=int, default=5000, help="Backend port to check/start (default: 5000)")
    ap.add_argument("--backend-app", default="app.main:app", help="Flask app path for --app (default: app.main:app)")
    ap.add_argument("--backend-health", default="/api/health", help="Health path to probe (default: /api/health)")

    args = ap.parse_args()

    dist_dir = Path(args.dist).resolve()
    frontend_dir = DEFAULT_FRONTEND_DIR
    backend_dir = DEFAULT_BACKEND_DIR

    if args.build:
        run_build(frontend_dir)

    if not dist_dir.exists():
        raise SystemExit(f"Error: dist directory not found: {dist_dir}\nHint: run with --build or build manually.")

    # Maybe ensure backend is running
    backend_proc: subprocess.Popen | None = None
    be_host, be_port = args.backend_host, args.backend_port
    if args.ensure-backend:
        if http_ok(f"http://{be_host}:{be_port}{args.backend_health}") or is_port_open(be_host, be_port):
            print(f"✅ Backend already running on {be_host}:{be_port}")
        else:
            backend_proc = start_backend_subprocess(
                backend_dir=backend_dir,
                host=be_host,
                port=be_port,
                app_path=args.backend_app,
                no_reload=True,
                no_debugger=True,
            )
            print("⏳ Waiting for backend to become ready…")
            if wait_for_backend(be_host, be_port, args.backend_health, timeout_s=30.0):
                print(f"✅ Backend is up on {be_host}:{be_port}")
            else:
                print("⚠️  Backend did not become ready in time (continuing to serve frontend).")

    # Serve frontend dist
    handler = partial(SPAHandler, directory=str(dist_dir))
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}"
    print(f"✅ Serving {dist_dir}")
    print(f"🌐 Open {url}")
    print("Press Ctrl+C to stop.")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        httpd.server_close()
        if backend_proc and backend_proc.poll() is None:
            print("🛑 Stopping backend…")
            try:
                backend_proc.terminate()
                try:
                    backend_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    backend_proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    main()
