import React from "react";
import { Link } from "react-router-dom";

const NotFoundPage: React.FC = () => (
  <div>
    <h1>404</h1>
    <p>That page doesn’t exist.</p>
    <p><Link to="/">Back to Home</Link></p>
  </div>
);

export default NotFoundPage;
