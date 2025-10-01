import React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Alert from "react-bootstrap/Alert";
import Button from "react-bootstrap/Button";
import Modal from "react-bootstrap/Modal";
import Spinner from "react-bootstrap/Spinner";
import Table from "react-bootstrap/Table";

interface HistoryEntry {
    id: string | null;
    date: string;
    username: string;
    itemId1: string | null;
    itemId2: string | null;
    event: string;
    meta: string;
    itemNamePreview: string | null;
    itemNameFull: string | null;
}

interface HistoryResponse {
    ok: boolean;
    page: number;
    pageSize: number;
    hasNext: boolean;
    hasPrevious: boolean;
    entries: HistoryEntry[];
    error?: string;
}

const PAGE_SIZE = 100;

const HistoryLogView: React.FC = () => {
    // Derive the desired page number from the route parameter; default to the first page when absent or invalid.
    const params = useParams<{ page?: string }>();
    const navigate = useNavigate();
    const requestedPage = React.useMemo(() => {
        const rawValue = params.page;
        if (!rawValue) {
            return 1;
        }
        const parsed = Number(rawValue);
        if (!Number.isInteger(parsed) || parsed < 1) {
            return 1;
        }
        return parsed;
    }, [params.page]);

    const [entries, setEntries] = React.useState<HistoryEntry[]>([]);
    const [loading, setLoading] = React.useState<boolean>(false);
    const [errorMessage, setErrorMessage] = React.useState<string | null>(null);
    const [hasNext, setHasNext] = React.useState<boolean>(false);
    const [hasPrevious, setHasPrevious] = React.useState<boolean>(false);
    const [selectedEntry, setSelectedEntry] = React.useState<HistoryEntry | null>(null);

    React.useEffect(() => {
        let isActive = true;
        const controller = new AbortController();

        const loadHistory = async () => {
            setLoading(true);
            setErrorMessage(null);
            try {
                const targetPath = requestedPage <= 1 ? "/api/history" : `/api/history/${requestedPage}`;
                const response = await fetch(targetPath, {
                    method: "GET",
                    credentials: "include",
                    signal: controller.signal,
                });
                if (!isActive) {
                    return;
                }
                if (response.status !== 200) {
                    const text = await response.text();
                    throw new Error(text || "Unable to retrieve history entries.");
                }
                const payload = (await response.json()) as HistoryResponse;
                if (!payload.ok) {
                    throw new Error(payload.error || "The server reported an unknown error.");
                }
                setEntries(payload.entries ?? []);
                setHasNext(Boolean(payload.hasNext));
                setHasPrevious(Boolean(payload.hasPrevious));
            } catch (error) {
                if (!isActive) {
                    return;
                }
                const friendlyMessage = error instanceof Error ? error.message : "Unable to retrieve history entries.";
                setErrorMessage(friendlyMessage);
                setEntries([]);
                setHasNext(false);
                setHasPrevious(requestedPage > 1);
            } finally {
                if (isActive) {
                    setLoading(false);
                }
            }
        };

        loadHistory();

        return () => {
            isActive = false;
            controller.abort();
        };
    }, [requestedPage]);

    // Navigate to the desired page while keeping the first page route concise.
    const navigateToPage = (pageNumber: number) => {
        const safePage = pageNumber < 1 ? 1 : pageNumber;
        if (safePage === 1) {
            navigate("/history", { replace: false });
        } else {
            navigate(`/history/${safePage}`, { replace: false });
        }
    };

    const formatDate = (value: string) => {
        try {
            const date = new Date(value);
            if (!Number.isNaN(date.getTime())) {
                return date.toLocaleString();
            }
        } catch {
            // Ignore conversion failures and fall back to the original text.
        }
        return value;
    };

    const renderLinkedUuid = (label: string, uuidValue: string | null) => {
        if (!uuidValue) {
            return (
                <div>
                    <strong>{label}:</strong> <span>—</span>
                </div>
            );
        }
        return (
            <div>
                <strong>{label}:</strong> <Link to={`/item/${uuidValue}`}>{uuidValue}</Link>
            </div>
        );
    };

    return (
        <div className="py-3">
            <h1 className="h3 mb-3">History Log</h1>
            <p className="text-muted">
                Review the most recent {PAGE_SIZE} recorded events. Click any row to examine the full entry, including
                metadata.
            </p>
            {errorMessage && (
                <Alert variant="danger" className="mt-3">
                    {errorMessage}
                </Alert>
            )}
            <div className="table-responsive">
                <Table striped bordered hover responsive>
                    <thead>
                        <tr>
                            <th style={{ width: "20%" }}>Date and Time</th>
                            <th style={{ width: "50%" }}>Event</th>
                            <th style={{ width: "30%" }}>Linked Item Preview</th>
                        </tr>
                    </thead>
                    <tbody>
                        {loading && (
                            <tr>
                                <td colSpan={3} className="text-center">
                                    <Spinner animation="border" role="status" size="sm" className="me-2" /> Loading
                                    history…
                                </td>
                            </tr>
                        )}
                        {!loading && entries.length === 0 && (
                            <tr>
                                <td colSpan={3} className="text-center text-muted">
                                    No history entries are available.
                                </td>
                            </tr>
                        )}
                        {!loading &&
                            entries.map((entry) => (
                                <tr
                                    key={entry.id ?? `${entry.date}-${entry.event}`}
                                    role="button"
                                    onClick={() => setSelectedEntry(entry)}
                                >
                                    <td>{formatDate(entry.date)}</td>
                                    <td>{entry.event || "(no description provided)"}</td>
                                    <td>{entry.itemNamePreview ?? "—"}</td>
                                </tr>
                            ))}
                    </tbody>
                </Table>
            </div>

            <div className="d-flex justify-content-center align-items-center gap-3 mt-3">
                <Button
                    variant="outline-secondary"
                    disabled={loading || !hasPrevious}
                    onClick={() => navigateToPage(requestedPage - 1)}
                >
                    ⬅️
                </Button>
                <span className="fw-semibold">Page {requestedPage}</span>
                <Button
                    variant="outline-secondary"
                    disabled={loading || !hasNext}
                    onClick={() => navigateToPage(requestedPage + 1)}
                >
                    ➡️
                </Button>
            </div>

            <Modal show={selectedEntry !== null} onHide={() => setSelectedEntry(null)} size="lg" centered>
                <Modal.Header closeButton>
                    <Modal.Title>History Entry Details</Modal.Title>
                </Modal.Header>
                <Modal.Body>
                    {selectedEntry && (
                        <div className="d-flex flex-column gap-2">
                            <div>
                                <strong>Entry ID:</strong> <span>{selectedEntry.id ?? "—"}</span>
                            </div>
                            <div>
                                <strong>Date:</strong> <span>{formatDate(selectedEntry.date)}</span>
                            </div>
                            <div>
                                <strong>Username:</strong> <span>{selectedEntry.username || "—"}</span>
                            </div>
                            {renderLinkedUuid("Item ID 1", selectedEntry.itemId1)}
                            {renderLinkedUuid("Item ID 2", selectedEntry.itemId2)}
                            <div>
                                <strong>Item Name:</strong> <span>{selectedEntry.itemNameFull ?? "—"}</span>
                            </div>
                            <div>
                                <strong>Event:</strong> <span>{selectedEntry.event || "—"}</span>
                            </div>
                            <div>
                                <strong>Meta:</strong>
                                <pre
                                    style={{
                                        maxHeight: "24rem",
                                        minHeight: "12rem",
                                        overflowY: "auto",
                                        backgroundColor: "#f8f9fa",
                                        border: "1px solid #dee2e6",
                                        borderRadius: "0.5rem",
                                        padding: "1rem",
                                        marginTop: "0.5rem",
                                    }}
                                >
                                    <code style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                                        {selectedEntry.meta || "(no metadata provided)"}
                                    </code>
                                </pre>
                            </div>
                        </div>
                    )}
                </Modal.Body>
                <Modal.Footer>
                    <Button variant="secondary" onClick={() => setSelectedEntry(null)}>
                        Close
                    </Button>
                </Modal.Footer>
            </Modal>
        </div>
    );
};

export default HistoryLogView;
