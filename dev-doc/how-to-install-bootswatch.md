You’ve got two options with Bootswatch, depending on whether you’re working with **plain HTML** or an **npm-based project**.

---

## 🔹 1. If you’re just working with plain HTML

On [Bootswatch](https://bootswatch.com/), pick a theme (say *Cosmo*). You’ll see two download links:

* `bootstrap.min.css` (the theme)
* `bootstrap.css` (unminified, easier to read)

👉 Save the file in your project, usually in a folder like:

```
my-project/
  index.html
  css/
    bootstrap.min.css
```

Then in your `index.html`:

```html
<link rel="stylesheet" href="css/bootstrap.min.css">
```

That’s it — everything styled with Bootstrap will now use the Bootswatch look.

---

## 🔹 2. If you’re using npm

You don’t need to download manually — Bootswatch is also published on npm.

```bash
npm install bootswatch
```

That creates:

```
node_modules/
  bootswatch/
    dist/
      cosmo/
        bootstrap.min.css
      ...
```

Then in your HTML (or bundler config), you point to it:

```html
<link rel="stylesheet" href="node_modules/bootswatch/dist/cosmo/bootstrap.min.css">
```

Or if you’re bundling with Webpack/Vite:

```js
import "bootswatch/dist/cosmo/bootstrap.min.css";
```

---

## 🔹 3. Quickest option (CDN)

If you don’t want to store anything locally, just link directly from jsDelivr:

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/cosmo/bootstrap.min.css">
```

No downloads, no setup — just swap the theme name in the URL.

---

✅ **Recommendation for you**:
If you’re experimenting/learning → use the **CDN link**.
If you’re building a project with version control → either save the CSS in a `css/` folder or install via `npm` so teammates can recreate it.

---

If you do

```bash
npm install bootswatch
```

you’ll get **all the Bootswatch themes** inside:

```
node_modules/bootswatch/dist/
  cerulean/
  cosmo/
  cyborg/
  darkly/
  flatly/
  ...
```

Each folder has its own `bootstrap.css` and `bootstrap.min.css`.

---

### 🔹 Why it works this way

* Bootswatch is published as one npm package.
* That way you can easily swap themes during development just by changing the import path, e.g.:

```js
// Cosmo
import "bootswatch/dist/cosmo/bootstrap.min.css";

// Darkly
import "bootswatch/dist/darkly/bootstrap.min.css";
```

---

### 🔹 If you only want one theme

* **Manual download**: Go to [Bootswatch.com](https://bootswatch.com), pick the theme, download just that CSS file → save it into your own `css/` folder.
* **CDN link** (super lightweight):

  ```html
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/cosmo/bootstrap.min.css">
  ```

That way you’re only pulling the single theme you actually use.

---

✅ **So your choices are:**

* **npm install bootswatch** → all themes, easy switching.
* **manual download / CDN** → one theme only, smallest footprint.

---

With CSS imports in a bundler (like Vite, Webpack, CRA, etc.), **the last stylesheet imported wins** if there are overlaps. Bootstrap and Bootswatch themes *do* overlap, since Bootswatch is just “Bootstrap + different variables/colors.”

---

### 🔹 How to do it

You don’t import both. You **replace** the Bootstrap CSS import with the Bootswatch one:

```ts
// main.tsx
// ❌ Not both at once
// import "bootstrap/dist/css/bootstrap.min.css";

import "bootswatch/dist/darkly/bootstrap.min.css";  // ✅ just this
```

That way you get all of Bootstrap’s functionality, styled with Darkly.

---

### 🔹 Why not import both?

* If you import Bootstrap **and then** Bootswatch, it still works (Bootswatch overrides Bootstrap), but you’re downloading two \~200KB CSS files unnecessarily.
* If you import Bootswatch **and then** Bootstrap, the theme gets overridden → you lose the theme.

---

### 🔹 Summary

* **Pick one import line.**
* For Bootstrap default →

  ```ts
  import "bootstrap/dist/css/bootstrap.min.css";
  ```
* For Bootswatch Darkly →

  ```ts
  import "bootswatch/dist/darkly/bootstrap.min.css";
  ```
