import React, { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import HomeStatsPanel from "../app/components/HomeStatsPanel";
import SearchPanel from "../app/components/SearchPanel";

type WhoAmIResponse =
  | { ok: true; user_id: string }
  | { error: string };

const HomePage: React.FC = () => {
  const [status, setStatus] = useState<"loading" | "loggedout" | "loggedin">("loading");
  const [userId, setUserId] = useState<string | null>(null);
  // Keep track of the query that should pre-populate the SearchPanel; start blank so nothing runs until the user asks.
  const [searchPrefill, setSearchPrefill] = useState<string>("");
  // This token increments whenever a statistic-driven search should re-run even if the query string remains identical.
  const [searchRefreshToken, setSearchRefreshToken] = useState<number>(0);
  const navigate = useNavigate();

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

  // React whenever a user taps one of the statistics search buttons. Either trigger the inline search panel or navigate to invoices.
  const handleStatsSearch = useCallback(
    (query: string, endpoint: "items" | "invoices") => {
      const sanitized = query.trim();
      if (endpoint === "items") {
        setSearchPrefill(sanitized);
        setSearchRefreshToken((previous) => previous + 1);
        return;
      }
      const encoded = encodeURIComponent(sanitized);
      navigate(sanitized ? `/ledger/${encoded}` : "/ledger");
    },
    [navigate],
  );

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

  return (
    <div className="container-lg py-4" style={{ maxWidth: "960px" }}>
      <h1>Welcome</h1>
      <p>
        You are logged in as <code>{userId}</code>.
      </p>
      <p>Common tasks:</p>
      <ul>
        <li>
          <Link to="/search">Search items</Link>
        </li>
        <li>
          <Link to="/item/new">Add new item</Link>
        </li>
        <li>
          <Link to="/ledger">Browse invoices</Link>
        </li>
        <li>
          <Link to="/admin">Maintenance</Link>
        </li>
      </ul>
      <div className="mt-4">
        <HomeStatsPanel onItemQuerySelected={handleStatsSearch} />
      </div>
      <div className="mt-4">
        <SearchPanel
          displayedTitle="Search inventory items"
          prefilledQuery={searchPrefill}
          tableName="items"
          allowDelete
          refreshToken={searchRefreshToken}
        />
      </div>
    </div>
  );
};

export default HomePage;
