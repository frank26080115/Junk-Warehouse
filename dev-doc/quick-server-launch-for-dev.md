# Launching PostgreSQL

On Windows, run pgAdmin

On Windows, open a WSL console to Ubuntu 22.04.5 LTS

In the WSL console, use command `sudo service postgresql status` to make sure postgresql is running, if not, use `sudo service postgresql start`

# Launching Dev Server (long)

Start the PostgreSQL server

On Windows, `cd` into project root

`npm run -w frontend dev`

`cd backend`

`python -m flask --app app.main:app --debug run --host=0.0.0.0 --port=5000`

The `<root>/frontend/package.json` only launches Vite and not the Flask server

# Launching Dev Server (short)

Start the PostgreSQL server

On Windows, `cd` into project root

`npm run dev`

This works because `<root>/package.json` has been setup with a longer script that launches Flask concurrently

# Allow LAN access to the Vite dev server

1. The Vite configuration (`frontend/vite.config.ts`) now binds to `0.0.0.0`. This makes the dev server listen on every network interface so devices on your Wi-Fi can reach it.
2. After running `npm run -w frontend dev`, use the `Network:` URL that Vite prints (for example, `http://192.168.1.174:5173/`).
3. The first time Node.js opens the port, Windows Defender Firewall might prompt for access. Allow the connection on private networks. If the prompt never appeared, create an inbound firewall rule that allows TCP 5173 for the Node.js runtime.
4. Confirm your phone and computer are on the same subnet (both `192.168.1.x`). A quick `ipconfig` on Windows and checking the phone's Wi-Fi details is usually enough.
5. If you still cannot connect, verify no VPN or security suite is blocking local traffic, then retry the LAN URL from the phone.
