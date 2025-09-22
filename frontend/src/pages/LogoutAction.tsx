import React, { useEffect } from "react";
import { useNavigate } from "react-router-dom";

const LogoutAction: React.FC = () => {
  const navigate = useNavigate();
  useEffect(() => {
    (async () => {
      try {
        await fetch("/api/logout", { method: "POST", credentials: "include" });
      } catch {
        // ignore
      } finally {
        navigate("/", { replace: true });
      }
    })();
  }, [navigate]);
  return <p>Logging out…</p>;
};

export default LogoutAction;
