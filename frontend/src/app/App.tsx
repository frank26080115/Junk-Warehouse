import { useEffect, useState } from "react";
import { ping } from "./app/api";

export default function App() {
  // const [state, setState] = useState<
  //   { status: "loading" } | { status: "ok"; data: any } | { status: "error"; msg: string }
  // >({ status: "loading" });
  // 1) Three pieces of state for clarity
  const [isLoading, setIsLoading] = useState(true);
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  //useEffect(() => {
  //  let alive = true;
  //  ping()
  //    .then((data) => alive && setState({ status: "ok", data }))
  //    .catch((err) => alive && setState({ status: "error", msg: String(err) }));
  //  return () => {
  //    alive = false;
  //  };
  //}, []);
  // 2) Fetch once after first render
  useEffect(() => {
    let alive = true; // ignore result if component unmounts
    ping()
      .then((res) => { if (!alive) return; setData(res); })
      .catch((err) => { if (!alive) return; setError(String(err)); })
      .finally(() => { if (!alive) return; setIsLoading(false); });
    return () => { alive = false; }; // cleanup
  }, []);

  //return (
  //  <main className="container">
  //    <h1>Hello, world 👋</h1>
  //    {state.status === "loading" && <p>Backend ping: loading…</p>}
  //    {state.status === "ok" && (
  //      <p>Backend ping result: <code>{JSON.stringify(state.data)}</code></p>
  //    )}
  //    {state.status === "error" && (
  //      <p>Backend ping failed: <code>{state.msg}</code></p>
  //    )}
  //  </main>
  //);
  // 3) Render paths (if/else is easiest to read)
  if (isLoading) return <p>Backend ping: loading…</p>;
  if (error)     return <p>Backend ping failed: <code>{error}</code></p>;
  return (
    <main className="container">
      <h1>Hello, world 👋</h1>
      <p>Backend ping result: <code>{JSON.stringify(data)}</code></p>
    </main>
  );
}
