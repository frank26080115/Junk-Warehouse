WARNING: this was never used

## Short answer 🚦

Yep! Run everything on your **GPU desktop at home** and let the tiny droplet act as a **public reverse proxy** (and optionally a CDN for static files). You’ve got a few good patterns—pick the one that fits your comfort level.

---

## Viable patterns 🧭

### 1) Reverse-SSH tunnel via `autossh` + Nginx on the droplet 🚇

* Desktop dials **out** to the droplet and creates a **remote (-R) tunnel**.
* Nginx on the droplet terminates TLS and proxies to the tunnel.
* Easiest to set up, no home port-forwarding, copes with dynamic IPs.

### 2) WireGuard (or Tailscale) + Nginx 🔒

* Create a private VPN between droplet and desktop.
* Nginx upstream points at the **WireGuard IP** of your desktop.
* Great stability; also lets the droplet reach **multiple local services**.

### 3) Cloudflare Tunnel (no droplet needed) ☁️

* Install `cloudflared` on the desktop; Cloudflare terminates TLS and routes to your local ports.
* Free/cheap and dead simple, but you asked about using the droplet—still worth knowing.

> CDN angle: Host your **React build** on a CDN (Cloudflare Pages, Netlify, or DO Spaces+CDN) and only proxy the **Flask API** through the droplet/tunnel. Big bandwidth win.

---

## Fastest working recipe (reverse-SSH tunnel) 🪄

**On your desktop (Linux/WSL):** create a persistent tunnel to the droplet.

```bash
# 1) SSH keys (once)
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@your-droplet

# 2) Install autossh
sudo apt-get install -y autossh
```

**Systemd service (desktop):** forward droplet port `6001` → local Flask `127.0.0.1:5000`

```ini
# /etc/systemd/system/revtunnel-api.service
[Unit]
Description=Reverse SSH tunnel to droplet for Flask API
After=network-online.target
Wants=network-online.target

[Service]
Environment=AUTOSSH_GATETIME=0
ExecStart=/usr/bin/autossh -N -M 0 \
  -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" \
  -R 6001:127.0.0.1:5000 root@your-droplet
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now revtunnel-api
```

**On the droplet (Nginx):** terminate TLS and proxy to the tunnel.

```nginx
# /etc/nginx/conf.d/api.conf
map $http_upgrade $connection_upgrade { default upgrade; '' close; }

server {
  listen 80;
  server_name api.example.com;
  return 301 https://$host$request_uri;
}

server {
  listen 443 ssl http2;
  server_name api.example.com;

  ssl_certificate     /etc/letsencrypt/live/api.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/api.example.com/privkey.pem;

  # Optional: cache small JSON for a few seconds if safe
  # proxy_cache my_cache; proxy_cache_valid 200 1s;

  location / {
    proxy_pass http://127.0.0.1:6001;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade           $http_upgrade;
    proxy_set_header Connection        $connection_upgrade;
  }
}
```

Issue certs once:

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d api.example.com
sudo systemctl reload nginx
```

**React frontend options:**

* Build and host static assets on **Cloudflare Pages / Netlify / DO Spaces+CDN**, pointing API calls to `https://api.example.com`.
* Or serve static on the droplet with another Nginx server block; keep big files on a CDN.

---

## WireGuard variant (stable & flexible) 🛡️

1. **WireGuard desktop ↔ droplet** (desktop initiates; survives NAT).
2. Nginx upstream to your desktop’s WG IP (e.g., `10.66.66.2:5000`).

**Droplet `/etc/wireguard/wg0.conf`:**

```ini
[Interface]
Address = 10.66.66.1/24
ListenPort = 51820
PrivateKey = <droplet_private_key>

[Peer]
PublicKey = <desktop_public_key>
AllowedIPs = 10.66.66.2/32
```

**Desktop `/etc/wireguard/wg0.conf`:**

```ini
[Interface]
Address = 10.66.66.2/24
PrivateKey = <desktop_private_key>

[Peer]
PublicKey = <droplet_public_key>
Endpoint = your-droplet:51820
PersistentKeepalive = 25
AllowedIPs = 10.66.66.1/32
```

**Nginx upstream (droplet):**

```nginx
location / {
  proxy_pass http://10.66.66.2:5000;
  ...
}
```

---

## Where to put PostgreSQL 🐘

Keep Postgres **local on the desktop** and only reachable over the **tunnel/VPN**—never expose it publicly. Your Flask app talks to it via `localhost` as usual; the droplet only forwards HTTP(S) to Flask, not DB connections.

---

## Performance reality check 🚀

* Your **home upload speed** caps API throughput. The droplet can’t fix that—only cache/static offload helps.
* Put your React build on a **CDN** so only API hits traverse your uplink.
* Consider **gzip/br** on Nginx and avoid huge JSON payloads.

---

## Security checklist 🔐

* TLS on the droplet (Let’s Encrypt).
* Firewall on droplet: allow **80/443** only; SSH from your IPs.
* Auth/RBAC in the API (JWT/cookies); never trust `X-Forwarded-*` blindly unless you control the proxy.
* Rate-limit at Nginx for login or heavy endpoints.
* Logs with **correlation IDs** end-to-end (you’re already adding actor context—nice).

---

## TL;DR 🧩

* **Do it now:** Reverse-SSH + Nginx is the quickest path.
* **Slightly fancier:** WireGuard/Tailscale + Nginx.
* **CDN-ify static:** Host React build on Pages/Netlify/Spaces+CDN.
* Keep DB local and unexposed; only the API crosses the internet.

If you paste your domain(s) and desired ports, I’ll hand you the exact Nginx blocks and a systemd service that forwards **both** the Flask API and any websocket endpoints you’ve got. 🎯

---

# Instructions

## 🚦 High-level game plan (in order)

1. **Point your domain** → create an `A` record for your subdomain to the new droplet’s IP. 🌐
2. **Harden the droplet** → new sudo user + SSH keys, firewall, updates. 🛡️
3. **Install nginx + TLS** → get a Let’s Encrypt cert on the droplet for your subdomain. 🔒
4. **Bring up reverse tunnels** from your desktop (WSL) to the droplet with `autossh`. 🔁
5. **Wire nginx → tunnels** → nginx proxies to the loopback ports that your tunnel exposes. 🔌
6. **Run your app normally on desktop** → Flask & Vite keep binding to localhost; Postgres stays local. 🧠
7. **(Optional) Prod-ify later** → gunicorn/uvicorn, Vite build, separate `/api` vs static, etc. 🧱

Little mental map 🎯:

```
User Browser ──▶ https://app.yourdomain.com (nginx @ droplet:443)
                       │
                       ▼
               proxy_pass to 127.0.0.1:6001 (Flask via reverse tunnel)
                                   └─── SSH -R 127.0.0.1:6001→desktop:5000
               (and/or) 127.0.0.1:6002 (Vite dev) → desktop:5173
```

---

## 🌐 Step 1 — DNS first

* In Hostgator’s DNS panel, add an `A` record:

  * **Name:** `app` (or whatever subdomain)
  * **Type:** A
  * **Value:** your DigitalOcean droplet’s public IPv4
  * **TTL:** default is fine
    Wait a few minutes for propagation (often quick).

---

## 🛡️ Step 2 — Secure & prep the droplet (Ubuntu 24.04)

SSH in as `root` (or DO’s default) then:

```bash
# Create a user
adduser deploy
usermod -aG sudo deploy

# Install basics
apt update && apt -y upgrade
apt -y install ufw fail2ban nginx

# Firewall: allow SSH + HTTP/S
ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw --force enable

# SSH key-only (after you copy your key!)
# On your local machine:
ssh-copy-id deploy@YOUR_DROPLET_IP

# Then on droplet:
sudoedit /etc/ssh/sshd_config
# Set/ensure:
#   PasswordAuthentication no
#   PubkeyAuthentication yes
#   PermitRootLogin no
systemctl restart ssh
```

Fail2ban defaults are okay to start; you can tune later. ✅

---

## 🔒 Step 3 — TLS with Let’s Encrypt

```bash
apt -y install certbot python3-certbot-nginx
# Temporary minimal server block (so certbot can find it):
cat >/etc/nginx/sites-available/app.conf <<'EOF'
server {
    listen 80;
    server_name app.yourdomain.com;
    location / { return 200 "temp"; }
}
EOF
ln -s /etc/nginx/sites-available/app.conf /etc/nginx/sites-enabled/app.conf
nginx -t && systemctl reload nginx

# Now get cert and let certbot auto-write SSL config:
certbot --nginx -d app.yourdomain.com --redirect --agree-tos -m you@example.com
```

You’ll refine the nginx proxy block in Step 5.

---

## 🔁 Step 4 — Reverse tunnels from your desktop (WSL)

You said Python + Vite run on **Windows**, Postgres on **WSL**—that’s okay. We’ll run `autossh` **inside WSL** (most convenient), forwarding to Windows apps via `localhost` if they bind there.

### Enable systemd in WSL (if not already)

```bash
# In WSL Ubuntu:
sudo nano /etc/wsl.conf
# Add:
# [boot]
# systemd=true

# Then from Windows PowerShell:
wsl --shutdown
# Reopen WSL Ubuntu
```

### Install autossh & generate a dedicated key

```bash
sudo apt update && sudo apt -y install autossh openssh-client
ssh-keygen -t ed25519 -f ~/.ssh/do_reverse -C "reverse-tunnel"
ssh-copy-id -i ~/.ssh/do_reverse.pub deploy@app.yourdomain.com
```

### Decide your local ports

* Flask (backend): `localhost:5000` (Windows or WSL—just ensure reachable from WSL; if on Windows, use `localhost`)
* Vite dev (frontend): `localhost:5173`

  * (If you serve built static instead, you may not need this tunnel.)
* Add more as needed.

### One-shot test

```bash
autossh -M 0 -N \
  -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" \
  -i ~/.ssh/do_reverse \
  -R 127.0.0.1:6001:localhost:5000 \
  -R 127.0.0.1:6002:localhost:5173 \
  deploy@app.yourdomain.com
```

* On the **droplet**, test locally:

  ```bash
  curl -i http://127.0.0.1:6001/health   # or your Flask root
  curl -i http://127.0.0.1:6002/         # Vite dev page
  ```

If you see your app responses, the tunnels are alive. 🎉

### Make it persistent (systemd user service in WSL)

```bash
mkdir -p ~/.config/systemd/user
cat >~/.config/systemd/user/reverse-tunnel.service <<'EOF'
[Unit]
Description=Reverse SSH tunnels to droplet
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/autossh -M 0 -N \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -i %h/.ssh/do_reverse \
  -R 127.0.0.1:6001:localhost:5000 \
  -R 127.0.0.1:6002:localhost:5173 \
  deploy@app.yourdomain.com
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now reverse-tunnel.service
systemctl --user status reverse-tunnel.service
```

Tip: If your desktop reboots, WSL will bring this up once you open a WSL session. If you want it truly headless, consider the WSL startup task in Task Scheduler that runs `wsl -d Ubuntu -u $USER systemctl --user start reverse-tunnel.service` at logon.

---

## 🔌 Step 5 — Nginx proxy → loopback tunnel ports

Replace the temporary config with a real proxy. Example: all traffic goes to Flask (port 6001); Vite dev served under `/dev` (port 6002) when you need it.

```nginx
# /etc/nginx/sites-available/app.conf
server {
    listen 80;
    listen 443 ssl http2;
    server_name app.yourdomain.com;

    # SSL bits injected by Certbot live here; keep them
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_certificate     /etc/letsencrypt/live/app.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.yourdomain.com/privkey.pem;

    # Increase proxy timeouts for LLM/embedding ops if needed
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;

    # Default route → Flask via tunnel
    location / {
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_pass http://127.0.0.1:6001;
    }

    # Optional: expose Vite dev under /dev/
    location /dev/ {
        rewrite ^/dev/?(.*)$ /$1 break;
        proxy_set_header Host $host;
        proxy_pass http://127.0.0.1:6002;
    }
}
```

```bash
nginx -t && systemctl reload nginx
```

Now open `https://app.yourdomain.com/` and you should see your Flask UI (or API). `https://app.yourdomain.com/dev/` will show the Vite dev server if you left that tunnel up. 🧪

---

## 🧠 Step 6 — Keep everything on the desktop

* **PostgreSQL** remains local in WSL; your Flask app connects to `localhost` as it always has. No DB ports are exposed on the internet—good.
* **LLMs + sentence-transformers** stay local; all heavy lifting remains on your workstation’s GPU/CPU.
* **Vite/Flask**: keep your dev workflow. The droplet just reverse-proxies.

---

## 🧱 Step 7 — (Optional) Prod polish when you’re ready

* Build and serve the frontend as static:

  ```bash
  # on desktop
  npm run build  # vite -> dist/
  ```

  Serve the built assets via Flask or a small local static server; then only tunnel Flask. Or tunnel a second static server if you prefer.

* Run Flask with a proper server (gunicorn/uvicorn) locally:

  ```bash
  pip install gunicorn
  gunicorn -w 4 -b 127.0.0.1:5000 yourapp:wsgi_app
  ```

  (Keep the tunnel target the same.)

* Split routes: nginx `/api` → 6001 (Flask), `/` → 6002 (static dev/build), etc.

---

## 🧯 Troubleshooting & tips

* **Tunnel binds are private**: we used `-R 127.0.0.1:PORT:...` so the droplet only listens on loopback. Safer by default.
* **Keepalives**: `ServerAliveInterval/CountMax` makes `autossh` recover gracefully when your ISP hiccups.
* **Logs**:

  * Droplet: `journalctl -u nginx -e`, `journalctl -u reverse-tunnel -e --user` (inside WSL), `tail -f /var/log/nginx/access.log`.
* **CORS**: If you proxy everything through nginx, you often avoid CORS entirely.
* **Ports**: Pick any free droplet loopback ports (`6001/6002` are just examples).

---

## ✅ TL;DR order

1. DNS → subdomain → droplet IP. 🌐
2. Create sudo user, SSH keys, disable passwords, enable UFW, install nginx. 🔒
3. Get Let’s Encrypt cert for subdomain. 🗝️
4. In WSL: enable systemd, install `autossh`, set up reverse tunnels as a user service. 🔁
5. Configure nginx to proxy to `127.0.0.1:6001` (Flask) and optionally `6002` (Vite). 🔌
6. Run your app as usual on the desktop; verify via the public URL. 🧪
7. Optimize later (build frontend, gunicorn, route splits). 🚀

If you want, tell me your exact subdomain and which local ports your Flask/Vite actually use, and I’ll spit out a copy-paste-ready nginx + autossh config tailored to you. 😸✨
