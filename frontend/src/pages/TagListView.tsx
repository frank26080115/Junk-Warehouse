import React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Alert from "react-bootstrap/Alert";
import Button from "react-bootstrap/Button";
import Form from "react-bootstrap/Form";
import Spinner from "react-bootstrap/Spinner";

interface RawTagEntry {
    id?: string | null;
    word?: string | null;
    vec?: unknown;
    date_updated?: string | null;
    embedding_distance?: number | null;
}

interface TagEntry {
    id: string;
    word: string;
    vec: number[] | null;
    date_updated: string;
    embedding_distance?: number | null;
    canDelete: boolean;
}

interface TagListResponse {
    ok: boolean;
    mode: "page" | "chain";
    page?: number;
    pageSize?: number;
    hasNext?: boolean;
    hasPrevious?: boolean;
    entries?: RawTagEntry[];
    seed?: string;
    error?: string;
}

const PAGE_SIZE = 100;

const TagListView: React.FC = () => {
    // Extract the selector from the route. A numeric value represents pagination, any other text triggers a greedy chain.
    const params = useParams<{ selector?: string }>();
    const navigate = useNavigate();
    const selector = params.selector ?? "1";
    const isPageRequest = React.useMemo(() => /^\d+$/.test(selector), [selector]);
    const pageNumber = React.useMemo(() => {
        if (!isPageRequest) {
            return 1;
        }
        const parsed = Number(selector);
        if (!Number.isInteger(parsed) || parsed < 1) {
            return 1;
        }
        return parsed;
    }, [isPageRequest, selector]);

    const [entries, setEntries] = React.useState<TagEntry[]>([]);
    const [loading, setLoading] = React.useState<boolean>(false);
    const [errorMessage, setErrorMessage] = React.useState<string | null>(null);
    const [hasNext, setHasNext] = React.useState<boolean>(false);
    const [hasPrevious, setHasPrevious] = React.useState<boolean>(false);
    const [mode, setMode] = React.useState<"page" | "chain">(isPageRequest ? "page" : "chain");
    const [seedWord, setSeedWord] = React.useState<string | null>(isPageRequest ? null : selector);
    const [newTagText, setNewTagText] = React.useState<string>("");
    const [isSubmitting, setIsSubmitting] = React.useState<boolean>(false);
    const [pendingDeleteId, setPendingDeleteId] = React.useState<string | null>(null);
    const [reloadToken, setReloadToken] = React.useState<number>(0);

    // Derive a normalized list of entries that always include the identifier when available.
    const normalizeEntries = (rawEntries: RawTagEntry[] | undefined | null): TagEntry[] => {
        if (!rawEntries || rawEntries.length === 0) {
            return [];
        }
        return rawEntries
            .map((entry) => {
                const idValue = typeof entry.id === "string" && entry.id.trim().length > 0 ? entry.id.trim() : null;
                const wordValue = typeof entry.word === "string" && entry.word.trim().length > 0 ? entry.word.trim() : null;
                const canDelete = idValue !== null;
                const rawVec = entry.vec;
                let normalizedVec: number[] | null = null;
                if (Array.isArray(rawVec)) {
                    const numericValues = rawVec
                        .map((value) => {
                            const numeric = typeof value === "number" ? value : Number(value);
                            return Number.isFinite(numeric) ? numeric : null;
                        })
                        .filter((value): value is number => value !== null);
                    normalizedVec = numericValues.length > 0 ? numericValues : null;
                }
                const updatedValue = typeof entry.date_updated === "string" && entry.date_updated.trim().length > 0
                    ? entry.date_updated
                    : "";
                const fallbackId = wordValue ? `word-${wordValue}` : `word-${Math.random().toString(36).slice(2)}`;
                return {
                    id: idValue ?? fallbackId,
                    word: wordValue ?? "(missing word)",
                    vec: normalizedVec,
                    date_updated: updatedValue,
                    embedding_distance: typeof entry.embedding_distance === "number" ? entry.embedding_distance : undefined,
                    canDelete,
                };
            });
    };

    React.useEffect(() => {
        let isActive = true;
        const controller = new AbortController();

        const loadEntries = async () => {
            setLoading(true);
            setErrorMessage(null);
            try {
                const response = await fetch(`/api/metatext/taglist/${encodeURIComponent(selector)}`, {
                    method: "GET",
                    credentials: "include",
                    signal: controller.signal,
                });
                if (!isActive) {
                    return;
                }
                if (response.status !== 200) {
                    const text = await response.text();
                    throw new Error(text || "Unable to load tag entries.");
                }
                const payload = (await response.json()) as TagListResponse;
                if (!payload.ok) {
                    throw new Error(payload.error || "The server returned an error while retrieving tags.");
                }
                const parsedEntries = normalizeEntries(payload.entries);
                if (!isActive) {
                    return;
                }
                setEntries(parsedEntries);
                if (payload.mode === "page") {
                    setMode("page");
                    setHasNext(Boolean(payload.hasNext));
                    setHasPrevious(Boolean(payload.hasPrevious));
                    setSeedWord(null);
                } else {
                    setMode("chain");
                    setHasNext(false);
                    setHasPrevious(false);
                    setSeedWord(typeof payload.seed === "string" ? payload.seed : selector);
                }
            } catch (error) {
                if (!isActive) {
                    return;
                }
                const message = error instanceof Error ? error.message : "Unable to load tag entries.";
                setErrorMessage(message);
                setEntries([]);
                setHasNext(false);
                setHasPrevious(false);
            } finally {
                if (isActive) {
                    setLoading(false);
                }
            }
        };

        loadEntries();

        return () => {
            isActive = false;
            controller.abort();
        };
    }, [selector, reloadToken]);

    const navigateToPage = (targetPage: number) => {
        const safePage = targetPage < 1 ? 1 : targetPage;
        if (safePage === 1) {
            navigate("/taglist/1", { replace: false });
        } else {
            navigate(`/taglist/${safePage}`, { replace: false });
        }
    };

    const handleDelete = async (entry: TagEntry) => {
        if (!entry.id || !entry.canDelete) {
            return;
        }
        setPendingDeleteId(entry.id);
        setErrorMessage(null);
        try {
            const response = await fetch(`/api/metatext/tag/${encodeURIComponent(entry.id)}`, {
                method: "DELETE",
                credentials: "include",
            });
            if (response.status !== 200) {
                const text = await response.text();
                throw new Error(text || "Unable to delete the selected tag.");
            }
            const payload = await response.json();
            if (!payload.ok) {
                throw new Error(payload.error || "The server rejected the delete request.");
            }
            setEntries((previous) => previous.filter((item) => item.id !== entry.id));
        } catch (error) {
            const message = error instanceof Error ? error.message : "Unable to delete the selected tag.";
            setErrorMessage(message);
        } finally {
            setPendingDeleteId(null);
        }
    };

    const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const trimmed = newTagText.trim();
        if (!trimmed) {
            setErrorMessage("Please supply at least one tag before submitting.");
            return;
        }
        setIsSubmitting(true);
        setErrorMessage(null);
        try {
            const response = await fetch("/api/metatext/taglist", {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ text: trimmed }),
            });
            if (response.status !== 200) {
                const text = await response.text();
                throw new Error(text || "Unable to add the requested tags.");
            }
            const payload = await response.json();
            if (!payload.ok) {
                throw new Error(payload.error || "The server could not add the requested tags.");
            }
            setNewTagText("");
            setReloadToken((value) => value + 1);
        } catch (error) {
            const message = error instanceof Error ? error.message : "Unable to add the requested tags.";
            setErrorMessage(message);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="py-3">
            <h1 className="h3 mb-3">Metatext Tag List</h1>
            <p className="text-muted">
                Browse the stored tag words and explore similarity chains. Click a word to focus on related entries or
                delete a tag using the trash icon.
            </p>
            {mode === "chain" && seedWord && (
                <Alert variant="info" className="mb-3">
                    Displaying related tags discovered via greedy chaining starting from <strong>{seedWord}</strong>.
                </Alert>
            )}
            {errorMessage && (
                <Alert variant="danger" className="mb-3">
                    {errorMessage}
                </Alert>
            )}
            <div className="d-flex flex-wrap gap-3 align-items-start">
                {loading && (
                    <div className="text-muted">
                        <Spinner animation="border" role="status" size="sm" className="me-2" /> Loading tags…
                    </div>
                )}
                {!loading && entries.length === 0 && (
                    <div className="text-muted">No tags are currently available for this view.</div>
                )}
                {!loading &&
                    entries.map((entry) => (
                        <div
                            key={entry.id}
                            className="border rounded px-3 py-2 d-inline-flex align-items-center gap-2 bg-light"
                            style={{ whiteSpace: "nowrap" }}
                        >
                            <Link to={`/taglist/${encodeURIComponent(entry.word)}`} className="text-decoration-none fw-semibold">
                                {entry.word}
                            </Link>
                            {typeof entry.embedding_distance === "number" && (
                                <span className="text-muted">
                                    {entry.embedding_distance.toFixed(3)}
                                </span>
                            )}
                            <button
                                type="button"
                                className="btn btn-link text-danger p-0 border-0"
                                aria-label={`Delete ${entry.word}`}
                                onClick={() => handleDelete(entry)}
                                disabled={!entry.canDelete || pendingDeleteId === entry.id}
                            >
                                🗑️
                            </button>
                        </div>
                    ))}
            </div>

            {mode === "page" && (
                <div className="d-flex justify-content-center align-items-center gap-3 mt-4">
                    <Button variant="outline-secondary" onClick={() => navigateToPage(pageNumber - 1)} disabled={!hasPrevious}>
                        Previous
                    </Button>
                    <span className="text-muted">
                        Page {pageNumber} (showing up to {PAGE_SIZE} entries)
                    </span>
                    <Button variant="outline-secondary" onClick={() => navigateToPage(pageNumber + 1)} disabled={!hasNext}>
                        Next
                    </Button>
                </div>
            )}

            <div className="mt-5">
                <h2 className="h5 mb-3">Add Metatext Tags</h2>
                <Form onSubmit={handleSubmit} className="d-flex gap-3">
                    <Form.Control
                        type="text"
                        placeholder="Enter tags separated by spaces"
                        value={newTagText}
                        onChange={(event) => setNewTagText(event.target.value)}
                        disabled={isSubmitting}
                    />
                    <Button type="submit" variant="primary" disabled={isSubmitting}>
                        {isSubmitting ? "Adding…" : "Submit"}
                    </Button>
                </Form>
            </div>
        </div>
    );
};

export default TagListView;
