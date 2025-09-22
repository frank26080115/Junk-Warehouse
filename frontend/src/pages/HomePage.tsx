import React from "react";
import { Link } from "react-router-dom";

const HomePage: React.FC = () => (
  <div>
    <h1>Home</h1>
    <p>Common tasks:</p>
    <ul>
      <li><Link to="/search">Search items</Link></li>
      <li><Link to="/item/new">Add new item</Link></li>
      <li><Link to="/ledger">Browse invoices</Link></li>
      <li><Link to="/admin">Maintenance</Link></li>
    </ul>
  </div>
);

export default HomePage;
