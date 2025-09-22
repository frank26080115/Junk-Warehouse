import React from "react";
import { useParams } from "react-router-dom";

const ItemPage: React.FC = () => {
  const { xyz } = useParams(); // id/slug/short-id
  return (
    <div>
      <h1>Item</h1>
      <p>Viewing/editing item: <code>{xyz}</code></p>
      {/* TODO: fetch item details from /api/item/<xyz> */}
    </div>
  );
};

export default ItemPage;
