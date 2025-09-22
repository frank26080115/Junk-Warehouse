import React from "react";

const HealthPage: React.FC = () => {
  const [msg, setMsg] = React.useState<string>("(checking…)");

  React.useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/health", { credentials: "include" });
        const data = await res.json();
        setMsg(JSON.stringify(data, null, 2));
      } catch (e: any) {
        setMsg(`Error: ${e?.message ?? "unknown"}`);
      }
    })();
  }, []);

  return (
    <div>
      <h1>Health</h1>
      <pre>{msg}</pre>
    </div>
  );
};

export default HealthPage;
