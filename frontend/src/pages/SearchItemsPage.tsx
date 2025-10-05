import React, { useMemo } from "react";
import { useLocation, useParams } from "react-router-dom";

import SearchPanel from "../app/components/SearchPanel";
import TreeView from "../app/components/TreeView";

const SearchItemsPage: React.FC = () => {
  const { xyz } = useParams<{ xyz?: string }>();
  const location = useLocation();

  const prefilled = useMemo(() => {
    const pathPrefill = xyz ? decodeURIComponent(xyz) : "";
    const params = new URLSearchParams(location.search);
    const queryPrefill = params.get("q") ?? params.get("query") ?? "";
    // Prefer query-string values so deep links like /search-items?q=red launch searches automatically.
    return queryPrefill || pathPrefill;
  }, [location.search, xyz]);

  return (
    <div className="container-lg py-4" style={{ maxWidth: "960px" }}>
      <SearchPanel
        displayedTitle="Search Inventory Items"
        prefilledQuery={prefilled}
        tableName="items"
        allowDelete
      />
      <TreeView />
    </div>
  );
};

export default SearchItemsPage;
