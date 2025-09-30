import React from "react";
import { Link } from "react-router-dom";
import Container from "react-bootstrap/Container";
import Navbar from "react-bootstrap/Navbar";
import Nav from "react-bootstrap/Nav";
import PinnedItemsIndicator from "./components/PinnedItemsIndicator";
import "../styles/nav.css";

const Shell: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // Track the current authentication state so the navigation can reflect whether a user is logged in.
  const [authStatus, setAuthStatus] = React.useState<"checking" | "loggedIn" | "loggedOut">("checking");
  // Record the name returned from the server; this is displayed to the user when authenticated.
  const [userDisplayName, setUserDisplayName] = React.useState<string | null>(null);

  React.useEffect(() => {
    let isMounted = true;

    // Query the backend once during mount to determine whether a user session already exists.
    const retrieveIdentity = async () => {
      try {
        const response = await fetch("/api/whoami", { credentials: "include" });
        if (!isMounted) {
          return;
        }
        if (response.status !== 200) {
          setAuthStatus("loggedOut");
          setUserDisplayName(null);
          return;
        }
        const payload = (await response.json()) as { ok?: boolean; user_id?: string };
        if (payload && payload.ok && payload.user_id) {
          setUserDisplayName(payload.user_id);
          setAuthStatus("loggedIn");
        } else {
          setAuthStatus("loggedOut");
          setUserDisplayName(null);
        }
      } catch {
        if (!isMounted) {
          return;
        }
        setAuthStatus("loggedOut");
        setUserDisplayName(null);
      }
    };

    retrieveIdentity();

    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <>
      <Navbar
        bg="light"
        expand="lg"
        className="mb-3"
        style={
          {
            ['--bs-navbar-padding-y' as any]: '0.10rem',
            ['--bs-navbar-brand-padding-y' as any]: '0.05rem',
          } as React.CSSProperties
        }
      >
        <Container
          style={{
            marginTop: 0,
            marginBottom: 0,
            paddingTop: 0,
            paddingBottom: 0,
          }}
          className="px-2"
        >
          <Navbar.Brand as={Link} to="/">Junk Warehouse</Navbar.Brand>
          <Navbar.Toggle aria-controls="nav" className="toggler-tight" />
          <Navbar.Collapse id="nav">
            <Nav className="me-auto">
              <Nav.Link as={Link} to="/search">&#128269;Search</Nav.Link>
              <Nav.Link as={Link} to="/item/new">&#9999;&#65039;New&nbsp;Item</Nav.Link>
              <Nav.Link as={Link} to="/ledger">&#128722;Ledger</Nav.Link>
              <Nav.Link as={Link} to="/admin">&#128736;&#65039;Admin</Nav.Link>
              {/* Show a login invitation only when the user is not authenticated. */}
              {authStatus === "loggedOut" && (
                <Nav.Link as={Link} to="/login">&#128275;Login</Nav.Link>
              )}
              {/* When authenticated, surface the user's name so they can confirm which account is active. */}
              {authStatus === "loggedIn" && (
                <Nav.Link as={Link} to="/login">
                  &#128100;{userDisplayName ?? "Account"}
                </Nav.Link>
              )}
              <Nav.Link as={Link} to="/logout">&#128274;Logout</Nav.Link>
            </Nav>
          </Navbar.Collapse>
        </Container>
      </Navbar>
      <Container
        style={{
          marginTop: 0,
          marginBottom: 0,
          paddingTop: 0,
          paddingBottom: 0,
        }}
        className="pb-5"
      >
        {children}
        <footer
          style={{
            marginTop: "1.5rem",
            paddingTop: "0.5rem",
            borderTop: "1px solid rgba(0, 0, 0, 0.08)",
            fontSize: "0.75rem",
            display: "flex",
            gap: "0.75rem",
            flexWrap: "wrap",
            color: "#6c757d",
          }}
        >
          {/* Show a quick summary of pinned entities on the far left. */}
          <PinnedItemsIndicator aria-label="Pinned items summary" />
          <a
            href="http://github.com/frank26080115/Junk-Warehouse"
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "inherit", textDecoration: "none" }}
          >
            Junk-Warehouse <img src="/imgs/icons/github_white_sq.png" height="18" width="18" />
          </a>
          <a href="/help" target="_blank" style={{ color: "inherit", textDecoration: "none" }}>
            🙋ℹ️
          </a>
        </footer>
      </Container>
    </>
  );
};

export default Shell;
