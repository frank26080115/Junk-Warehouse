import React, { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import InvoiceUploaderPanel from "../app/components/InvoiceUploaderPanel";
import SearchPanel from "../app/components/SearchPanel";

const LedgerSearchPage: React.FC = () => {
  const { xyz } = useParams<{ xyz?: string }>();
  const prefilled = useMemo(() => (xyz ? decodeURIComponent(xyz) : ""), [xyz]);
  const [searchPrefill, setSearchPrefill] = useState(prefilled);

  useEffect(() => {
    setSearchPrefill(prefilled);
  }, [prefilled]);

  return (
    <div className="container-lg py-4" style={{ maxWidth: "960px" }}>
      <h1 className="h3 mb-4">Search Invoices</h1>
      <SearchPanel
        displayedTitle="Invoices"
        prefilledQuery={searchPrefill}
        tableName="invoices"
        allowDelete
      />

      <InvoiceUploaderPanel
        onSearchPrefillSuggested={(query) => {
          // Provide the parent search panel with the recommended query so users immediately see fresh results.
          setSearchPrefill(query);
        }}
      />
    </div>
  );
};

export default LedgerSearchPage;
