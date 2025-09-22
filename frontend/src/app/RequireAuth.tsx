import React from "react";
import { Navigate, useLocation } from "react-router-dom";

/** Simple hook to check if the user is authenticated */
function useWhoAmI() {
  const [state, setState] = React.useState<"loading" | "in" | "out">("loading");

  React.useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const res = await fetch("/api/whoami", { credentials: "include" });
        if (res.status === 200) {
          if (mounted) setState("in");
        } else {
          if (mounted) setState("out");
        }
      } catch {
        if (mounted) setState("out");
      }
    })();
    return () => { mounted = false; };
  }, []);

  return state;
}

/** Wrap any protected element in <RequireAuth> … </RequireAuth> */
const RequireAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const state = useWhoAmI();
  const location = useLocation();

  if (state === "loading") {
    return <div className="p-3">Checking authentication…</div>;
  }
  if (state === "out") {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname + location.search + location.hash }}
      />
    );
  }
  return <>{children}</>;
};

export default RequireAuth;
