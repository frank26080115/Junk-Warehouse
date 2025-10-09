## Setup SSL

```
sudo apt -y install certbot python3-certbot-nginx
sudo certbot --nginx -d junkwarehouse.eleccelerator.com --redirect -m name@email.com --agree-tos
```

## Edit Config

list every enabled site file

```
ls -l /etc/nginx/sites-enabled/
```

make sure only `junkwarehouse.conf` exists, delete `app.conf` if there is one, maybe run `sudo rm -f /etc/nginx/sites-enabled/default`

`junkwarehouse.conf` should be

```
server {
    listen 80;
    server_name junkwarehouse.eleccelerator.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name junkwarehouse.eleccelerator.com;

    ssl_certificate     /etc/letsencrypt/live/junkwarehouse.eleccelerator.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/junkwarehouse.eleccelerator.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;

    location / {
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Upgrade $http_upgrade;   # 🔄 for HMR websockets (harmless otherwise)
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;

        proxy_pass http://127.0.0.1:5173;         # 👈 Vite on loopback:5173
    }
}
```

then run

`sudo nginx -t && sudo systemctl reload nginx`

### 🧱 Firewall sanity 🌵

Keep only 80/443 public:

```
sudo ufw allow 80,443/tcp
sudo ufw deny 5137/tcp   # optional if Vite ever binds 0.0.0.0
sudo ufw status
```
