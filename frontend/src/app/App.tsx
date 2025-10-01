import React, { Suspense, lazy } from "react";
import { Routes, Route } from "react-router-dom";
import Shell from "./Shell";

// Lazily load every page-level component so the initial bundle stays lean.
// React and Vite will fetch each page only when the associated route is
// visited, which keeps navigation fast for the majority of users.
const HomePage         = lazy(() => import("../pages/HomePage"));
const SearchItemsPage  = lazy(() => import("../pages/SearchItemsPage"));
const ItemPage         = lazy(() => import("../pages/ItemPage"));
const InvoicePage      = lazy(() => import("../pages/InvoicePage"));
const LedgerSearchPage = lazy(() => import("../pages/LedgerSearchPage"));
const AdminPage        = lazy(() => import("../pages/AdminPage"));
const LoginPage        = lazy(() => import("../pages/LoginPage"));
const LogoutAction     = lazy(() => import("../pages/LogoutAction"));
const HealthPage       = lazy(() => import("../pages/HealthPage"));
const TestPage         = lazy(() => import("../pages/TestPage"));
const NotFoundPage     = lazy(() => import("../pages/NotFoundPage"));
const HelpPage         = lazy(() => import("../pages/HelpPage"));
const HistoryLogView  = lazy(() => import("../pages/HistoryLogView"));

const App: React.FC = () => {
  return (
    <Shell>
      {/* Suspense shows a simple loader whenever a lazily loaded page is still
          downloading.  Using a Bootstrap-friendly padding keeps the content
          from jumping abruptly once the page finishes loading. */}
      <Suspense fallback={<div className="p-3">Loading…</div>}>
        {/* Centralize the route map so changes to the navigation structure are
            easy to review.  The nested comments make it obvious which section
            of the product each route belongs to. */}
        <Routes>
          {/* Home */}
          <Route path="/" element={<HomePage />} />

          {/* Item search */}
          <Route path="/search" element={<SearchItemsPage />} />
          <Route path="/search/:xyz" element={<SearchItemsPage />} />

          {/* Items */}
          <Route path="/item" element={<SearchItemsPage />} />
          <Route path="/item/:xyz" element={<ItemPage />} />

          {/* Invoices */}
          <Route path="/invoice/:uuid" element={<InvoicePage />} />

          {/* Ledger / invoices search */}
          <Route path="/ledger" element={<LedgerSearchPage />} />
          <Route path="/ledger/:xyz" element={<LedgerSearchPage />} />

          {/* Admin & Auth */}
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/logout" element={<LogoutAction />} />

          {/* Utilities */}
          <Route path="/health" element={<HealthPage />} />
          <Route path="/test/:xyz" element={<TestPage />} />

          {/* Historical records */}
          <Route path="/history" element={<HistoryLogView />} />
          <Route path="/history/:page" element={<HistoryLogView />} />
          <Route path="/help" element={<HelpPage />} />

          {/* 404 */}
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
    </Shell>
  );
};

export default App;
