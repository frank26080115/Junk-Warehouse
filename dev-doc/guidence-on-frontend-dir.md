Great question—here’s a clean, opinionated way to place files and name them for a Vite + React frontend.

# Where files live

```
frontend/
├─ index.html                 # HTML entry (no TSX here)
├─ package.json
├─ tsconfig.json
├─ vite.config.ts
└─ src/                       # <-- all TypeScript/TSX lives here
   ├─ main.tsx                # bootstraps React (lowercase)
   ├─ App.tsx                 # top-level UI shell (PascalCase)
   ├─ app/                    # app-level wiring (runtime “infrastructure”)
   │  ├─ api.ts               # fetch helpers / API client
   │  ├─ router.tsx           # React Router setup (if used)
   │  ├─ providers.tsx        # context providers (theme, query, etc.)
   │  └─ config.ts            # frontend config constants
   ├─ pages/                  # route-level screens (PascalCase files)
   │  ├─ HomePage.tsx
   │  └─ AboutPage.tsx
   ├─ components/             # reusable UI components (PascalCase files)
   │  ├─ Button.tsx
   │  └─ Header.tsx
   ├─ hooks/                  # reusable hooks (camelCase files)
   │  └─ usePing.ts
   ├─ styles/                 # CSS imported by TS/TSX
   │  └─ global.css
   ├─ assets/                 # images/fonts you import from code
   │  └─ logo.png
   └─ lib/                    # non-React utilities (camelCase or kebab-case)
      └─ format-number.ts
```

## What goes where

* **`frontend/` (repo root for the app):** configuration only.
  *No `.tsx` files here.* Keep `index.html`, `vite.config.ts`, `tsconfig.json`, `package.json`.

* **`src/`:** all source code.

  * **`main.tsx`**: bootstrap file that mounts React (lowercase filename).
  * **`App.tsx`**: your top-level component (layout/shell) (PascalCase).

* **`src/app/`:** app-level “plumbing” (things used *by the app as a whole*, not a single page/component): API client, router, global providers, config constants. These can be `.ts` or `.tsx` depending on whether they return JSX.

* **`src/pages/`:** route-level screens you navigate to (each typically a React component). One component per file, often with child components in nearby folder.

* **`src/components/`:** reusable presentational/logic components used across pages.

* **`src/hooks/`:** reusable hooks (`useSomething.ts`), no JSX inside these files usually.

* **`src/lib/` (or `utils/`):** non-React helpers (formatters, validators).

* **`src/styles/` and `src/assets/`:** CSS and images you import from code. (Use `public/` only for files you want served exactly as-is at fixed URLs.)

# Naming rules (when to capitalize)

* **React components (default export or dominant export):** **PascalCase**

  * `App.tsx`, `HomePage.tsx`, `UserCard.tsx`, `Header.tsx`
* **Entry files & infrastructure files:** **lowercase** (or kebab-case)

  * `main.tsx`, `api.ts`, `router.tsx`, `providers.tsx`, `config.ts`
* **Hooks:** **camelCase**, must start with `use`

  * `usePing.ts`, `useAuth.ts`
* **Utilities / non-React modules:** **lowercase** or **kebab-case**

  * `format-number.ts`, `date.ts`, `env.ts`
* **Directories:** commonly **lowercase** or **kebab-case** (`components`, `pages`, `app`, `styles`)

> Rule of thumb: **Files whose primary export is a React component → PascalCase. Everything else → lowercase/kebab-case.**

# Tiny example glue

**`src/main.tsx`**

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/global.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

**`src/App.tsx`**

```tsx
import HomePage from "./pages/HomePage";

export default function App() {
  return <HomePage />;
}
```

**`src/pages/HomePage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { ping } from "../app/api";

export default function HomePage() {
  const [result, setResult] = useState<string>("loading…");

  useEffect(() => {
    ping()
      .then((r) => setResult(JSON.stringify(r)))
      .catch((e) => setResult(`error: ${String(e)}`));
  }, []);

  return (
    <main className="container">
      <h1>Hello, world 👋</h1>
      <p>Backend ping: <code>{result}</code></p>
    </main>
  );
}
```

**`src/app/api.ts`**

```ts
export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5000";

export async function ping(): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/health`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

That’s it—clean separation, predictable names, and easy to scale. If you prefer a **feature-first** layout (grouping by domain instead of type), I can adapt this to that style too.
