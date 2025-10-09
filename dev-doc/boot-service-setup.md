## 🧩 Overview

You’ll have a single boot script **`jw_dev_boot.sh`** that:

* builds the frontend every start ✅
* serves Vite **preview** on `127.0.0.1:5173` ✅
* starts your backend (Flask/Gunicorn/Uvicorn) ✅
* traps stop signals and cleanly kills **both** ✅

And a systemd service **`jw_dev_boot.service`** to run/stop it.

## 🎮 Daily control panel

* ▶️ start: `sudo systemctl start jw_dev_boot`
* ⏹️ stop: `sudo systemctl stop jw_dev_boot`
* 🔁 restart: `sudo systemctl restart jw_dev_boot`
* 📜 logs: `journalctl -u jw_dev_boot -f`
* 🚫 skip on next boot: `touch /root/.jw_dev_disabled && sudo systemctl stop jw_dev_boot`
* 🔁 re-enable on boot: `rm -f /root/.jw_dev_disabled && sudo systemctl start jw_dev_boot`
* ✅ check status: `sudo systemctl status jw_dev_boot`
* 🔁 daemon reload: `sudo systemctl daemon-reload` then `sudo systemctl restart jw_dev_boot`

## 🛠️ `jw_dev_boot.sh` (put in `/root/Junk-Warehouse/scripts/jw_dev_boot.sh`) 🧪

```
code in file
```

```bash
chmod +x /root/Junk-Warehouse/scripts/jw_dev_boot.sh
```

---

## 🧰 `jw_dev_boot.service` (systemd unit) ⚙️

```
code in file under scripts\other\jw_dev_boot.service
```

Activate it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jw_dev_boot
systemctl status jw_dev_boot
```

---

## Directory Permission Issue

```
# show perms/owners for each level
stat -c '%A %U:%G %n' /root /root/Junk-Warehouse

# confirm the script is executable by deploy
stat -c '%A %U:%G %n' /root/Junk-Warehouse/scripts/jw_dev_boot.sh

# wanted:
# drwxr-xr-x root:root /root
# drwxr-xr-x deploy:deploy /root/Junk-Warehouse
# -rwxr-xr-x deploy:deploy /root/Junk-Warehouse/scripts/jw_dev_boot.sh


# Ensure directories are traversable
sudo chmod 755 /root
sudo chmod -R u+rwX,go+rX /root/Junk-Warehouse

# Ensure the boot script is executable
sudo chmod +x /root/Junk-Warehouse/scripts/jw_dev_boot.sh
```

---

## 🧯 Notes & tips

* Frontend **always builds** on start; cached deps make it quick. 🧱
* Nginx should keep proxying `https://your-domain/ → 127.0.0.1:5173`. 🌐
* If your backend entrypoint/module differs, update `BACKEND_CMD` (or set it in `/etc/default/jw_dev_boot`). 🧠
* If either process dies, the script exits → systemd restarts the whole stack for you. ♻️
