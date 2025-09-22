import React, { Suspense, lazy } from "react";
import { Routes, Route } from "react-router-dom";
import Shell from "./Shell";

// Lazy page imports (code-splitting)
const HomePage         = lazy(() => import("../pages/HomePage"));
const SearchItemsPage  = lazy(() => import("../pages/SearchItemsPage"));
const ItemNewPage      = lazy(() => import("../pages/ItemNewPage"));
const ItemPage         = lazy(() => import("../pages/ItemPage"));
const InvoicePage      = lazy(() => import("../pages/InvoicePage"));
const LedgerSearchPage = lazy(() => import("../pages/LedgerSearchPage"));
const AdminPage        = lazy(() => import("../pages/AdminPage"));
const LoginPage        = lazy(() => import("../pages/LoginPage"));
const LogoutAction     = lazy(() => import("../pages/LogoutAction"));
const HealthPage       = lazy(() => import("../pages/HealthPage"));
const TestPage         = lazy(() => import("../pages/TestPage"));
const NotFoundPage     = lazy(() => import("../pages/NotFoundPage"));

const App: React.FC = () => {
  return (
    <Shell>
      <Suspense fallback={<div className="p-3">Loading…</div>}>
        <Routes>
          {/* Home */}
          <Route path="/" element={<HomePage />} />

          {/* Item search */}
          <Route path="/search" element={<SearchItemsPage />} />
          <Route path="/search/:xyz" element={<SearchItemsPage />} />

          {/* Items */}
          <Route path="/item/new" element={<ItemNewPage />} />
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

          {/* 404 */}
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
    </Shell>
  );
};

export default App;
