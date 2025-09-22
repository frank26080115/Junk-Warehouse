import React from "react";
import { useParams } from "react-router-dom";

const InvoicePage: React.FC = () => {
  const { uuid } = useParams();
  return (
    <div>
      <h1>Invoice</h1>
      <p>Invoice UUID: <code>{uuid}</code></p>
      {/* TODO: fetch invoice details from /api/invoice/<uuid> */}
    </div>
  );
};

export default InvoicePage;
