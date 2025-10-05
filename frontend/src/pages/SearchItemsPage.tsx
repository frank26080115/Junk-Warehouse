import React, { useEffect, useMemo, useState } from "react";
import { useLocation, useParams } from "react-router-dom";

import SearchPanel from "../app/components/SearchPanel";
import TreeView from "../app/components/TreeView";

interface PrefillState {
  query: string;
  token: number;
}

const SearchItemsPage: React.FC = () => {
  const { xyz } = useParams<{ xyz?: string }>();
  const location = useLocation();

  const resolvedPrefill = useMemo(() => {
    const pathPrefill = xyz ? decodeURIComponent(xyz) : "";
    const params = new URLSearchParams(location.search);
    const queryPrefill = params.get("q") ?? params.get("query") ?? "";
    // Prefer query-string values so deep links like /search-items?q=red launch searches automatically.
    return queryPrefill || pathPrefill;
  }, [location.search, xyz]);

  const [prefillState, setPrefillState] = useState<PrefillState>(() => ({
    // Persist the latest resolved query alongside a counter so SearchPanel knows when a new automatic search is required.
    query: resolvedPrefill,
    token: resolvedPrefill ? 1 : 0,
  }));

  useEffect(() => {
    setPrefillState((previous) => {
      if (previous.query === resolvedPrefill) {
        return previous;
      }
      // Bump the token whenever the URL changes to ensure the backend request is reissued for the new parameters.
      return {
        query: resolvedPrefill,
        token: previous.token + 1,
      };
    });
  }, [resolvedPrefill]);

  const { query: searchPrefill, token: refreshToken } = prefillState;

  return (
    <div className="container-lg py-4" style={{ maxWidth: "960px" }}>
      <SearchPanel
        displayedTitle="Search Inventory Items"
        prefilledQuery={searchPrefill}
        refreshToken={refreshToken}
        tableName="items"
        allowDelete
      />
      <TreeView />
    </div>
  );
};

export default SearchItemsPage;
