import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useParams } from "react-router-dom";

import InvoiceUploaderPanel from "../app/components/InvoiceUploaderPanel";
import SearchPanel from "../app/components/SearchPanel";

interface PrefillState {
  query: string;
  token: number;
}

const LedgerSearchPage: React.FC = () => {
  const { xyz } = useParams<{ xyz?: string }>();
  const location = useLocation();

  const resolvedPrefill = useMemo(() => {
    const pathPrefill = xyz ? decodeURIComponent(xyz) : "";
    const params = new URLSearchParams(location.search);
    const queryPrefill = params.get("q") ?? params.get("query") ?? "";
    // Favor explicit query-string parameters so copied URLs trigger their intended searches immediately.
    if (queryPrefill) {
      return queryPrefill;
    }
    return pathPrefill;
  }, [location.search, xyz]);

  const [prefillState, setPrefillState] = useState<PrefillState>(() => ({
    // Track both the query text and a monotonically increasing token so we can trigger refreshes when needed.
    query: resolvedPrefill,
    token: resolvedPrefill ? 1 : 0,
  }));

  useEffect(() => {
    setPrefillState((previous) => {
      if (previous.query === resolvedPrefill) {
        return previous;
      }
      // Increment the token whenever the URL-derived query changes so SearchPanel reruns the associated backend search.
      return {
        query: resolvedPrefill,
        token: previous.token + 1,
      };
    });
  }, [resolvedPrefill]);

  const handleSearchPrefillSuggested = useCallback((query: string) => {
    setPrefillState((previous) => {
      if (previous.query === query) {
        return previous;
      }
      // Each suggestion updates the refresh token so SearchPanel initiates a new backend request immediately.
      return {
        query,
        token: previous.token + 1,
      };
    });
  }, []);

  const { query: searchPrefill, token: refreshToken } = prefillState;

  return (
    <div className="container-lg py-4" style={{ maxWidth: "960px" }}>
      <SearchPanel
        displayedTitle="Search Ledger"
        prefilledQuery={searchPrefill}
        refreshToken={refreshToken}
        tableName="invoices"
        allowDelete
      />

      <InvoiceUploaderPanel
        onSearchPrefillSuggested={(query) => {
          handleSearchPrefillSuggested(query);
        }}
        // Email polling is now automated, so hide the manual checker panel.
        showCheckEmailPanel={false}
      />
    </div>
  );
};

export default LedgerSearchPage;
