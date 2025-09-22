import React from "react";
import { useParams } from "react-router-dom";

const SearchItemsPage: React.FC = () => {
  const { xyz } = useParams(); // optional
  // Prefill your actual search input from xyz if present
  return (
    <div>
      <h1>Search Items</h1>
      <p>Prefilled query: <code>{xyz ?? ""}</code></p>
      {/* TODO: render real search UI */}
    </div>
  );
};

export default SearchItemsPage;
