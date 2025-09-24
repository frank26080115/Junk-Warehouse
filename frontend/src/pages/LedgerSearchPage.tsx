import React, { useMemo } from "react";
import { useParams } from "react-router-dom";

import SearchPanel from "../app/components/SearchPanel";

const LedgerSearchPage: React.FC = () => {
  const { xyz } = useParams<{ xyz?: string }>();
  const prefilled = useMemo(() => (xyz ? decodeURIComponent(xyz) : ""), [xyz]);

  return (
    <div className="container-lg py-4" style={{ maxWidth: "960px" }}>
      <h1 className="h3 mb-4">Search Invoices</h1>
      <SearchPanel
        displayedTitle="Invoices"
        prefilledQuery={prefilled}
        tableName="invoices"
        allowDelete
      />
    </div>
  );
};

export default LedgerSearchPage;
