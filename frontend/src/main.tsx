import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./app/App";
import "bootstrap/dist/css/bootstrap.min.css";
import 'bootstrap/dist/js/bootstrap.bundle.min.js';
import "./styles/global.css"; // optional

// The entry point hydrates the DOM node that Vite exposes in index.html and
// wraps the application with helpers that should be global (StrictMode and the
// router).  Keeping this file small makes it obvious what runs before any page
// specific code executes.
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
