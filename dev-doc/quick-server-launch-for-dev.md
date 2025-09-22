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
