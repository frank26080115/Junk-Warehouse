import React, { useEffect, useMemo, useState } from "react";
import { useLocation, useParams } from "react-router-dom";

import InvoiceUploaderPanel from "../app/components/InvoiceUploaderPanel";
import SearchPanel from "../app/components/SearchPanel";

const LedgerSearchPage: React.FC = () => {
  const { xyz } = useParams<{ xyz?: string }>();
  const location = useLocation();

  const prefilled = useMemo(() => {
    const pathPrefill = xyz ? decodeURIComponent(xyz) : "";
    const params = new URLSearchParams(location.search);
    const queryPrefill = params.get("q") ?? params.get("query") ?? "";
    // Favor explicit query-string parameters so copied URLs trigger their intended searches immediately.
    if (queryPrefill) {
      return queryPrefill;
    }
    return pathPrefill;
  }, [location.search, xyz]);
  const [searchPrefill, setSearchPrefill] = useState(prefilled);

  useEffect(() => {
    // Propagate the resolved prefill value so the search panel can execute matching requests when the URL changes.
    setSearchPrefill(prefilled);
  }, [prefilled]);

  return (
    <div className="container-lg py-4" style={{ maxWidth: "960px" }}>
      <SearchPanel
        displayedTitle="Search Ledger"
        prefilledQuery={searchPrefill}
        tableName="invoices"
        allowDelete
      />

      <InvoiceUploaderPanel
        onSearchPrefillSuggested={(query) => {
          // Provide the parent search panel with the recommended query so users immediately see fresh results.
          setSearchPrefill(query);
        }}
        // Email polling is now automated, so hide the manual checker panel.
        showCheckEmailPanel={false}
      />
    </div>
  );
};

export default LedgerSearchPage;
