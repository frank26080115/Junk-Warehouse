Use an **IANA time zone name** (a.k.a. “TZ database” name) in `config/appconfig.json`. That works cleanly in **Python** (via `zoneinfo`) and in **Node/Browser** (via `Intl.*` APIs).

# 1) `config/appconfig.json`

```json
{
  "appName": "DIY Organizer",
  "timezone": "America/Los_Angeles"
}
```

* Examples: `"UTC"`, `"America/New_York"`, `"Europe/Berlin"`.
* Avoid ambiguous strings like `"PST"` or raw offsets like `"-07:00"` (DST breaks).

---

# 2) Python (Flask/backend)

```python
# backend/app/config_loader.py
from __future__ import annotations
import json
from pathlib import Path
from zoneinfo import ZoneInfo  # Python 3.9+
import logging
log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "appconfig.json"

def load_app_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Could not read %s; falling back to defaults", CONFIG_PATH, exc_info=True)
        return {}

def get_timezone():
    cfg = load_app_config()
    name = (cfg.get("timezone") or "UTC").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        log.warning("Unknown timezone %r; falling back to UTC", name)
        return ZoneInfo("UTC")
```

Use it:

```python
from datetime import datetime, timezone
from .config_loader import get_timezone

LOCAL_TZ = get_timezone()
now_local = datetime.now(LOCAL_TZ)
now_utc = now_local.astimezone(timezone.utc)
```

> Tip: On Windows or minimal containers, add `tzdata` to your Python deps so `zoneinfo` has the database:
>
> ```
> pip install tzdata
> ```

---

# 3) Frontend / Node (Vite/React)

If you import the JSON directly:

```ts
// frontend/src/app/config.ts
import appconfig from '../../../config/appconfig.json';
export const APP_TZ = (appconfig.timezone ?? 'UTC') as string;

// Example format:
export function formatLocal(d: Date | string) {
  const date = typeof d === 'string' ? new Date(d) : d;
  return new Intl.DateTimeFormat(undefined, {
    timeZone: APP_TZ,
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}
```

If you **serve** `/config.json` from Flask and fetch at runtime:

```ts
export async function loadConfig() {
  const res = await fetch('/config.json');
  const cfg = await res.json();
  return (cfg.timezone as string) || 'UTC';
}
```

---

# 4) Recommended pattern

* **Store in DB as UTC** (e.g., `timestamptz` via UTC datetimes).
* **Convert at the edges**:

  * Backend: for reports/exports, convert using `ZoneInfo(cfg.timezone)`.
  * Frontend: render with `Intl.DateTimeFormat({ timeZone: cfg.timezone })`.

---

## Gotchas (quick)

* Don’t use `"PST"`/`"EST"`; always IANA names.
* Changing the config later updates both sides naturally.
* Node’s `process.env.TZ` exists but is OS-dependent; prefer passing `timeZone` to `Intl` explicitly (as above).

That’s it: put `"timezone": "<IANA name>"` in `appconfig.json`, read it with `zoneinfo` in Python and `Intl` in Node/React.
