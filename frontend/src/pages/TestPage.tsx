import React from "react";
import { useParams } from "react-router-dom";

const TestPage: React.FC = () => {
  const { xyz } = useParams();
  const [msg, setMsg] = React.useState<string>("(running…)");

  React.useEffect(() => {
    (async () => {
      try {
        const q = encodeURIComponent(xyz ?? "");
        const res = await fetch(`/api/test?query=${q}`, { credentials: "include" });
        const data = await res.json();
        setMsg(JSON.stringify(data, null, 2));
      } catch (e: any) {
        setMsg(`Error: ${e?.message ?? "unknown"}`);
      }
    })();
  }, [xyz]);

  return (
    <div>
      <h1>Test</h1>
      <p>Query: <code>{xyz ?? ""}</code></p>
      <pre>{msg}</pre>
    </div>
  );
};

export default TestPage;
