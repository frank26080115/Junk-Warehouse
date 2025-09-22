import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";

type WhoAmIResponse =
  | { ok: true; user_id: string }
  | { error: string };

const HomePage: React.FC = () => {
  const [status, setStatus] = useState<"loading" | "loggedout" | "loggedin">("loading");
  const [userId, setUserId] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/whoami", { credentials: "include" });
        if (res.status === 401) {
          setStatus("loggedout");
          return;
        }
        if (!res.ok) {
          throw new Error(`Unexpected ${res.status}`);
        }
        const data: WhoAmIResponse = await res.json();
        if ("ok" in data && data.ok) {
          setUserId(data.user_id);
          setStatus("loggedin");
        } else {
          setStatus("loggedout");
        }
      } catch (err) {
        console.error("whoami check failed:", err);
        setStatus("loggedout");
      }
    })();
  }, []);

  if (status === "loading") {
    return <p>Loading…</p>;
  }

  if (status === "loggedout") {
    return (
      <div>
        <h1>User Not Logged In</h1>
        <p>
          Please <Link to="/login">login here</Link>.
        </p>
      </div>
    );
  }

  // logged in
  return (
    <div>
      <h1>Welcome</h1>
      <p>You are logged in as <code>{userId}</code>.</p>
      <p>Common tasks:</p>
      <ul>
        <li><Link to="/search">Search items</Link></li>
        <li><Link to="/item/new">Add new item</Link></li>
        <li><Link to="/ledger">Browse invoices</Link></li>
        <li><Link to="/admin">Maintenance</Link></li>
      </ul>
    </div>
  );
};

export default HomePage;
