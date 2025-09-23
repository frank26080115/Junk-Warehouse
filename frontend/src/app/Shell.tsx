import React from "react";
import { Link } from "react-router-dom";
import Container from "react-bootstrap/Container";
import Navbar from "react-bootstrap/Navbar";
import Nav from "react-bootstrap/Nav";
import "../styles/nav.css";

const Shell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
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
            <Nav.Link as={Link} to="/login">&#128275;Login</Nav.Link>
            <Nav.Link as={Link} to="/logout">&#128274;Logout</Nav.Link>
          </Nav>
        </Navbar.Collapse>
      </Container>
    </Navbar>
    <Container className="pb-5">{children}</Container>
  </>
);

export default Shell;
