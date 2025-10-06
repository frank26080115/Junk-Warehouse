import React from "react";
import Card from "react-bootstrap/Card";
import Button from "react-bootstrap/Button";
import Form from "react-bootstrap/Form";
import Spinner from "react-bootstrap/Spinner";
import Modal from "react-bootstrap/Modal";

type Props = {
  /** Optional title shown at top of the panel (defaults to project name). */
  title?: string;
  /** If provided, panel works in "modal mode": call onSuccess() and let parent close it. */
  onSuccess?: (userId: string) => void;
  /** Optional className to style container */
  className?: string;
  /** Autofocus on username input (default true) */
  autoFocus?: boolean;
};

const LoginPanel: React.FC<Props> = ({
  title = "Junk Warehouse",
  onSuccess,
  className,
  autoFocus = true,
}) => {
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [showErr, setShowErr] = React.useState(false);

  const canSubmit = username.trim().length > 0 && password.length > 0 && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });

      if (!res.ok) {
        let msg = `Login failed (${res.status})`;
        try {
          const data = await res.json();
          if (data?.error) msg = data.error;
        } catch { /* ignore */ }
        throw new Error(msg);
      }

      const data = await res.json();
      const userId = data?.user_id ?? username.trim();

      // Panel used in a modal: let parent handle close
      if (onSuccess) {
        onSuccess(userId);
      } else {
        // When used as a page, parent page decides navigation.
        // We just resolve successfully and let caller redirect.
        // (The page wrapper will pass an onSuccess that redirects.)
      }
    } catch (err: any) {
      const message = err?.message ?? "Invalid username or password.";
      setError(message);
      setShowErr(true);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Card className={className ?? "shadow-sm"} style={{ maxWidth: 520, margin: "0 auto" }}>
        <Card.Body>
          <h2 className="mb-3 text-center">{title}</h2>
          <h5 className="mb-4 text-center">Login</h5>

          <Form onSubmit={handleSubmit}>
            <Form.Group className="mb-3" controlId="login-username">
              <Form.Label>Username</Form.Label>
              <Form.Control
                type="text"
                autoComplete="username"
                value={username}
                autoFocus={autoFocus}
                disabled={submitting}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Enter your username"
              />
            </Form.Group>

            <Form.Group className="mb-4" controlId="login-password">
              <Form.Label>Password</Form.Label>
              <Form.Control
                type="password"
                autoComplete="current-password"
                value={password}
                disabled={submitting}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
              />
            </Form.Group>

            <div className="d-grid gap-2">
              <Button type="submit" variant="primary" disabled={!canSubmit}>
                {submitting ? (
                  <>
                    <Spinner animation="border" size="sm" className="me-2" /> Logging in…
                  </>
                ) : (
                  "Log In"
                )}
              </Button>
            </div>
          </Form>
        </Card.Body>
      </Card>

      {/* Error modal */}
      {/* Explicitly enable keyboard dismissal so the Escape key always closes the modal. */}
      <Modal show={showErr} onHide={() => setShowErr(false)} centered keyboard>
        <Modal.Header closeButton>
          <Modal.Title>Login Error</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <p className="mb-0">{error ?? "Invalid username or password."}</p>
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={() => setShowErr(false)}>
            Close
          </Button>
        </Modal.Footer>
      </Modal>
    </>
  );
};

export default LoginPanel;
