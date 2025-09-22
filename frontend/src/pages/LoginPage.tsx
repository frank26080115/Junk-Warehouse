import React from "react";
import { useLocation, useNavigate } from "react-router-dom";
import LoginPanel from "../app/components/LoginPanel";
import Container from "react-bootstrap/Container";

type LocationState =
  | { from?: string }
  | undefined;

const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const params = new URLSearchParams(location.search);
  const redirectQuery = params.get("redirect");

  const fromState = (location.state as LocationState)?.from;
  const destination = fromState || redirectQuery || "/";

  function handleSuccess() {
    // Replace to avoid stacking /login in history
    navigate(destination, { replace: true });
  }

  return (
    <Container style={{ maxWidth: 720 }}>
      <div className="py-4">
        <LoginPanel title="Junk Warehouse" onSuccess={() => handleSuccess()} />
      </div>
    </Container>
  );
};

export default LoginPage;
