import React from "react";
import { useParams } from "react-router-dom";

const LedgerSearchPage: React.FC = () => {
  const { xyz } = useParams(); // optional
  return (
    <div>
      <h1>Ledger</h1>
      <p>Prefilled query: <code>{xyz ?? ""}</code></p>
      {/* TODO: render invoice search UI */}
    </div>
  );
};

export default LedgerSearchPage;
