import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

const ASSOCIATION_TYPES = [
  "containment",
  "alternative",
  "consumable",
  "accessory",
  "power",
  "similar",
  "weak",
] as const;

const ASSOCIATION_OPTIONS = [...ASSOCIATION_TYPES, "unlink"] as const;

type TableName = "items" | "invoices";
type AssociationOption = typeof ASSOCIATION_OPTIONS[number];

interface SearchRow {
  pk: string;
  slug?: string;
  thumbnail?: string;
  [key: string]: unknown;
}

interface SearchPanelProps {
  displayedTitle?: string | null;
  prefilledQuery?: string | null;
  hideTextBox?: boolean;
  targetUuid?: string | null;
  refreshToken?: number;
  tableName?: TableName;
  smallMode?: boolean;
  allowDelete?: boolean;
}

const API_ENDPOINTS: Record<
  TableName,
  { search: string; delete: string; relate: string }
> = {
  items: {
    search: "/api/search",
    delete: "/api/items/bulk-delete",
    relate: "/api/items/associations",
  },
  invoices: {
    search: "/api/ledger/search",
    delete: "/api/ledger/bulk-delete",
    relate: "/api/ledger/associations",
  },
};

function isBlank(value?: string | null): boolean {
  return !value || value.trim().length === 0;
}

function truncateText(value: string, max = 72): string {
  if (value.length <= max) return value;
  if (max <= 1) return value.slice(0, max);
  return `${value.slice(0, max - 1)}…`;
}

function extractInvoiceLines(row: SearchRow): string[] {
  if (Array.isArray(row.lines)) {
    return row.lines.map((line) => String(line));
  }

  const candidates = [
    row.display,
    row.summary,
    row.description,
    row.subject,
    row.text,
    row.details,
  ];

  for (const candidate of candidates) {
    if (candidate == null) {
      continue;
    }

    const raw = Array.isArray(candidate)
      ? (candidate as unknown[]).map((item) => String(item)).join("\n")
      : String(candidate);
    if (isBlank(raw)) {
      continue;
    }

    return raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
  }

  return [String(row.pk)];
}
const SearchPanel: React.FC<SearchPanelProps> = ({
  displayedTitle,
  prefilledQuery,
  hideTextBox = false,
  targetUuid,
  refreshToken,
  tableName = "items",
  smallMode = false,
  allowDelete = false,
}) => {
  const normalizedTable: TableName = tableName ?? "items";
  const [query, setQuery] = useState<string>(prefilledQuery ?? "");
  const [rawResults, setRawResults] = useState<SearchRow[]>([]);
  const [errorMessage, setErrorMessage] = useState<string>("");
  const [isSearching, setIsSearching] = useState<boolean>(false);
  const [isActionBusy, setIsActionBusy] = useState<boolean>(false);
  const [hasQueried, setHasQueried] = useState<boolean>(false);
  const [selectedPks, setSelectedPks] = useState<Set<string>>(new Set());
  const [relationDirection, setRelationDirection] = useState<
    "forward" | "reverse"
  >("forward");
  const [associationType, setAssociationType] = useState<AssociationOption>(
    ASSOCIATION_OPTIONS[0]
  );
  const [modalMessage, setModalMessage] = useState<
    | {
        title: string;
        body: string;
      }
    | null
  >(null);

  const abortRef = useRef<AbortController | null>(null);
  const pinnedRef = useRef<Map<string, SearchRow>>(new Map());
  const lastQueryRef = useRef<string>(prefilledQuery ?? "");
  const refreshTokenRef = useRef<number | undefined>(undefined);
  const lastPrefilledRef = useRef<string | null>(null);
  const lastTargetRef = useRef<string | null | undefined>(undefined);

  const isBusy = isSearching || isActionBusy;
  const selectedCount = selectedPks.size;

  useEffect(() => {
    const styleId = "search-panel-blink-style";
    if (typeof document === "undefined") {
      return;
    }
    if (document.getElementById(styleId)) {
      return;
    }
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = "@keyframes search-panel-blink { from { opacity: 0.5; } to { opacity: 1; } }";
    document.head.appendChild(style);
  }, []);

  const runSearch = useCallback(
    async (forcedQuery?: string) => {
      const q = forcedQuery !== undefined ? forcedQuery : query;
      lastQueryRef.current = q;
      setHasQueried(true);

      const includeThumbnails = normalizedTable === "items" && !smallMode;

      if (!q && !targetUuid) {
        abortRef.current?.abort();
        setRawResults([]);
        setErrorMessage("");
        return;
      }

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setIsSearching(true);
      setErrorMessage("");

      try {
        const endpoint = API_ENDPOINTS[normalizedTable].search;
        const response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            q,
            target_uuid: targetUuid || undefined,
            table: normalizedTable,
            include_thumbnails: includeThumbnails,
          }),
          signal: controller.signal,
        });

        let payload: any = null;
        try {
          payload = await response.json();
        } catch {
          payload = null;
        }

        if (!response.ok) {
          const message =
            payload && typeof payload === "object" && "error" in payload
              ? String(payload.error)
              : `Search failed (${response.status})`;
          throw new Error(message);
        }

        if (payload && typeof payload === "object" && "ok" in payload) {
          if (!payload.ok) {
            throw new Error(
              payload.error ? String(payload.error) : "Search failed",
            );
          }
        }

        const dataArray: unknown =
          payload && typeof payload === "object" && "data" in payload
            ? (payload as any).data
            : payload;

        const rows: unknown[] = Array.isArray(dataArray)
          ? (dataArray as unknown[])
          : Array.isArray(payload)
          ? (payload as unknown[])
          : [];

        const sanitized = rows
          .filter(
            (row): row is Record<string, unknown> =>
              row !== null && typeof row === "object" && typeof (row as any).pk === "string",
          )
          .map((row) => {
            const base = row as Record<string, unknown>;
            const normalized: SearchRow = { pk: String((row as any).pk) };

            Object.entries(base).forEach(([key, value]) => {
              if (key === "pk") {
                return;
              }
              normalized[key] = value;
            });

            const slugValue = base.slug;
            normalized.slug =
              typeof slugValue === "string" && slugValue.trim().length > 0
                ? slugValue
                : undefined;

            const thumbnailValue = base.thumbnail;
            if (typeof thumbnailValue === "string") {
              if (thumbnailValue.trim().length > 0) {
                normalized.thumbnail = thumbnailValue;
              } else if (includeThumbnails) {
                normalized.thumbnail = "";
              } else {
                normalized.thumbnail = undefined;
              }
            } else if (includeThumbnails) {
              normalized.thumbnail = "";
            } else {
              normalized.thumbnail = undefined;
            }

            return normalized;
          });

        setRawResults(sanitized);
      } catch (error: any) {
        if (error?.name === "AbortError") {
          return;
        }
        setErrorMessage(error?.message || "Search failed");
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
          setIsSearching(false);
        } else if (!controller.signal.aborted) {
          setIsSearching(false);
        }
      }
    },
    [normalizedTable, query, smallMode, targetUuid],
  );
  useEffect(() => {
    const normalized =
      prefilledQuery == null ? "" : String(prefilledQuery);
    const previous = lastPrefilledRef.current;

    if (previous === null) {
      lastPrefilledRef.current = normalized;
      if (query !== normalized) {
        setQuery(normalized);
      }
      if (normalized || targetUuid) {
        setErrorMessage("");
        void runSearch(normalized);
      }
      return;
    }

    if (previous !== normalized) {
      lastPrefilledRef.current = normalized;
      if (query !== normalized) {
        setQuery(normalized);
      }
      setErrorMessage("");
      if (normalized || targetUuid) {
        void runSearch(normalized);
      }
    }
  }, [prefilledQuery, query, runSearch, targetUuid]);

  useEffect(() => {
    const nextTarget = targetUuid ?? null;
    if (lastTargetRef.current === undefined) {
      lastTargetRef.current = nextTarget;
      return;
    }

    if (lastTargetRef.current !== nextTarget) {
      lastTargetRef.current = nextTarget;
      const fallback = lastQueryRef.current || query || (lastPrefilledRef.current ?? "");
      if (fallback || targetUuid) {
        void runSearch(fallback);
      }
    }
  }, [query, runSearch, targetUuid]);

  useEffect(() => {
    if (refreshToken === undefined) {
      return;
    }

    if (refreshTokenRef.current === undefined) {
      refreshTokenRef.current = refreshToken;
      return;
    }

    if (refreshTokenRef.current !== refreshToken) {
      refreshTokenRef.current = refreshToken;
      const fallback = lastQueryRef.current || query || (lastPrefilledRef.current ?? "");
      if (fallback || targetUuid) {
        void runSearch(fallback);
      }
    }
  }, [query, refreshToken, runSearch, targetUuid]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    const map = new Map(rawResults.map((row) => [row.pk, row]));
    selectedPks.forEach((pk) => {
      const updated = map.get(pk);
      if (updated) {
        pinnedRef.current.set(pk, updated);
      }
    });
  }, [rawResults, selectedPks]);

  const displayRows = useMemo(() => {
    const base = rawResults.slice();
    const seen = new Set(base.map((row) => row.pk));
    const appended: SearchRow[] = [];

    selectedPks.forEach((pk) => {
      if (!seen.has(pk)) {
        const pinned = pinnedRef.current.get(pk);
        if (pinned) {
          appended.push(pinned);
        }
      }
    });

    return base.concat(appended);
  }, [rawResults, selectedPks]);

  const hasTitle = !isBlank(displayedTitle);
  const hasResults = displayRows.length > 0;
  const columnCount =
    1 +
    (normalizedTable === "items" && !smallMode ? 1 : 0) +
    (targetUuid ? 1 : 0) +
    1;

  const magnifierStyle: React.CSSProperties | undefined = isSearching
    ? { animation: "search-panel-blink 0.5s linear infinite alternate" }
    : undefined;

  const panelStyle: React.CSSProperties | undefined = smallMode
    ? { fontSize: "10pt" }
    : undefined;

  const toggleCheckbox = useCallback(
    (row: SearchRow, checked: boolean) => {
      setSelectedPks((prev) => {
        const next = new Set(prev);
        if (checked) {
          next.add(row.pk);
          pinnedRef.current.set(row.pk, row);
        } else {
          next.delete(row.pk);
          pinnedRef.current.delete(row.pk);
        }
        return next;
      });
    },
    [],
  );

  const handleSelectAll = useCallback(() => {
    if (!displayRows.length) {
      return;
    }
    setSelectedPks(() => {
      const next = new Set<string>();
      displayRows.forEach((row) => {
        next.add(row.pk);
        pinnedRef.current.set(row.pk, row);
      });
      return next;
    });
  }, [displayRows]);

  const handleSelectNone = useCallback(() => {
    setSelectedPks(() => new Set());
    pinnedRef.current.clear();
  }, []);
  const buildItemHref = useCallback(
    (row: SearchRow): string => {
      const directHref = (row as any).href;
      if (typeof directHref === "string" && !isBlank(directHref)) {
        return directHref;
      }
      const url = (row as any).url;
      if (typeof url === "string" && !isBlank(url)) {
        return url;
      }
      if (normalizedTable === "items") {
        if (typeof row.slug === "string" && !isBlank(row.slug)) {
          return `/item/${row.slug}`;
        }
        if (typeof row.pk === "string" && !isBlank(row.pk)) {
          return `/item/${row.pk}`;
        }
      }
      return "#";
    },
    [normalizedTable],
  );

  const renderItemName = useCallback(
    (row: SearchRow) => {
      const candidates = [
        (row as any).display_name,
        (row as any).name,
        (row as any).title,
      ];
      let raw = "";
      for (const candidate of candidates) {
        if (typeof candidate === "string" && !isBlank(candidate)) {
          raw = candidate;
          break;
        }
      }
      if (!raw) {
        raw = row.pk;
      }
      const truncated = truncateText(raw);
      const href = buildItemHref(row);
      return (
        <a href={href} className="text-decoration-none text-break" title={raw}>
          {truncated}
        </a>
      );
    },
    [buildItemHref],
  );

  const resolveRelationIcon = useCallback(
    (row: SearchRow): string => {
      const explicit = (row as any).relation_icon;
      if (typeof explicit === "string" && !isBlank(explicit)) {
        return explicit;
      }
      const isRelated = (row as any).is_related;
      if (typeof isRelated === "boolean") {
        return isRelated ? "🔗" : "⚫";
      }
      const related = (row as any).related;
      if (typeof related === "boolean") {
        return related ? "🔗" : "⚫";
      }
      return normalizedTable === "invoices" ? "⚫" : "";
    },
    [normalizedTable],
  );

  const handleDelete = useCallback(async () => {
    if (!allowDelete || selectedCount === 0) {
      return;
    }
    const ids = Array.from(selectedPks);
    setIsActionBusy(true);
    try {
      const endpoint = API_ENDPOINTS[normalizedTable].delete;
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          table: normalizedTable,
          target_uuid: targetUuid || undefined,
          pks: ids,
        }),
      });
      let payload: any = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      if (!response.ok) {
        const message =
          payload && typeof payload === "object" && "error" in payload
            ? String(payload.error)
            : `Delete failed (${response.status})`;
        throw new Error(message);
      }
      if (payload && typeof payload === "object" && "ok" in payload && !payload.ok) {
        throw new Error(payload.error ? String(payload.error) : "Delete failed");
      }

      pinnedRef.current.clear();
      setSelectedPks(() => new Set());

      await runSearch(lastQueryRef.current || query);

      setModalMessage({
        title: "Delete complete",
        body: ids.length === 1 ? "1 entry removed." : `${ids.length} entries removed.`,
      });
    } catch (error: any) {
      setModalMessage({
        title: "Delete failed",
        body: error?.message || "Unable to delete the selected entries.",
      });
    } finally {
      setIsActionBusy(false);
    }
  }, [allowDelete, normalizedTable, query, runSearch, selectedCount, selectedPks, targetUuid]);

  const handleSetAssociation = useCallback(async () => {
    if (!targetUuid || selectedCount === 0) {
      return;
    }
    const ids = Array.from(selectedPks);
    setIsActionBusy(true);
    try {
      const endpoint = API_ENDPOINTS[normalizedTable].relate;
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          table: normalizedTable,
          target_uuid: targetUuid,
          pks: ids,
          association_type: associationType,
          direction: relationDirection,
        }),
      });
      let payload: any = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      if (!response.ok) {
        const message =
          payload && typeof payload === "object" && "error" in payload
            ? String(payload.error)
            : `Request failed (${response.status})`;
        throw new Error(message);
      }
      if (payload && typeof payload === "object" && "ok" in payload && !payload.ok) {
        throw new Error(payload.error ? String(payload.error) : "Request failed");
      }

      if (associationType === "unlink") {
        pinnedRef.current.clear();
        setSelectedPks(() => new Set());
      }

      await runSearch(lastQueryRef.current || query);

      setModalMessage({
        title: associationType === "unlink" ? "Links removed" : "Relations updated",
        body:
          associationType === "unlink"
            ? "Selected relations have been removed."
            : `Association set to "${associationType}" for the selected entries.`,
      });
    } catch (error: any) {
      setModalMessage({
        title: "Action failed",
        body: error?.message || "Unable to update the selected entries.",
      });
    } finally {
      setIsActionBusy(false);
    }
  }, [associationType, normalizedTable, query, relationDirection, runSearch, selectedCount, selectedPks, targetUuid]);

  const resolveThumbnail = useCallback((row: SearchRow): string => {
    if (smallMode || normalizedTable !== "items") {
      return "";
    }
    const thumbnail = row.thumbnail;
    if (typeof thumbnail === "string" && !isBlank(thumbnail)) {
      return thumbnail;
    }
    return "";
  }, [normalizedTable, smallMode]);

  const handleSearchSubmit = useCallback(() => {
    void runSearch();
  }, [runSearch]);
  return (
    <div className="border rounded-3 p-3 bg-white" style={panelStyle}>
      {hasTitle && (
        <div className="mb-3">
          <h2 className="h5 mb-0">{displayedTitle}</h2>
        </div>
      )}

      {!hideTextBox && (
        <div className="mb-3">
          <div className="input-group">
            <input
              type="text"
              className="form-control"
              placeholder="search query"
              disabled={isBusy}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  handleSearchSubmit();
                }
              }}
            />
            <button
              type="button"
              className="btn btn-outline-secondary"
              disabled={isBusy}
              style={magnifierStyle}
              onClick={handleSearchSubmit}
              aria-label="Run search"
            >
              🔍
            </button>
          </div>
        </div>
      )}

      {errorMessage && (
        <div className="alert alert-danger py-2 px-3" role="status">
          {errorMessage}
        </div>
      )}

      <div className="border rounded-3 mb-3">
        <div className="table-responsive">
          <table className="table table-sm align-middle mb-0">
            <tbody>
              {hasResults &&
                displayRows.map((row, index) => {
                  const rowShade =
                    index % 2 === 0 ? "rgba(0, 0, 0, 0.02)" : "rgba(0, 0, 0, 0.05)";
                  const checked = selectedPks.has(row.pk);
                  const relationIcon = targetUuid ? resolveRelationIcon(row) : "";
                  const thumbnail = resolveThumbnail(row);
                  const invoiceLines =
                    normalizedTable === "invoices" ? extractInvoiceLines(row) : [];

                  return (
                    <tr key={row.pk} style={{ backgroundColor: rowShade }}>
                      <td style={{ width: "2.5rem" }}>
                        <input
                          type="checkbox"
                          className="form-check-input"
                          disabled={isBusy}
                          checked={checked}
                          onChange={(event) => toggleCheckbox(row, event.target.checked)}
                        />
                      </td>

                      {normalizedTable === "items" && !smallMode && (
                        <td style={{ width: "4.5rem" }}>
                          {thumbnail ? (
                            <img
                              src={thumbnail}
                              alt=""
                              className="img-fluid rounded"
                              style={{
                                maxWidth: "64px",
                                maxHeight: "64px",
                                objectFit: "cover",
                              }}
                            />
                          ) : (
                            <div
                              className="border rounded bg-light"
                              style={{ width: "64px", height: "64px" }}
                            />
                          )}
                        </td>
                      )}

                      {targetUuid && (
                        <td
                          className="text-center"
                          style={{ width: "3rem", fontSize: "1.5rem" }}
                        >
                          <span aria-hidden>{relationIcon}</span>
                        </td>
                      )}

                      <td>
                        {normalizedTable === "items" ? (
                          renderItemName(row)
                        ) : (
                          <div className="small text-body">
                            {invoiceLines.map((line, idx) => (
                              <div key={`${row.pk}-line-${idx}`}>{line}</div>
                            ))}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}

              {!hasResults && hasQueried && !isSearching && (
                <tr>
                  <td
                    colSpan={columnCount}
                    className="text-center text-muted py-3"
                    style={{ opacity: 0.6 }}
                  >
                    No results found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div
        className="d-flex flex-wrap justify-content-between align-items-center gap-3 px-3 py-2 rounded-3"
        style={{ backgroundColor: "rgba(0, 0, 0, 0.04)" }}
      >
        {isBusy ? (
          <div className="text-muted small">Working…</div>
        ) : (
          <>
            <div className="d-flex flex-wrap gap-2">
              <button
                type="button"
                className="btn btn-outline-secondary btn-sm"
                disabled={!hasResults}
                onClick={handleSelectAll}
              >
                select all
              </button>
              <button
                type="button"
                className="btn btn-outline-secondary btn-sm"
                disabled={!selectedCount}
                onClick={handleSelectNone}
              >
                select none
              </button>
              {allowDelete && (
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  disabled={!selectedCount}
                  onClick={handleDelete}
                >
                  delete
                </button>
              )}
            </div>

            {targetUuid && (
              <div className="d-flex flex-wrap align-items-center gap-2">
                <button
                  type="button"
                  className="btn btn-outline-secondary btn-sm"
                  onClick={() =>
                    setRelationDirection((prev) =>
                      prev === "forward" ? "reverse" : "forward"
                    )
                  }
                  disabled={isBusy}
                  aria-pressed={relationDirection === "reverse"}
                  title={
                    relationDirection === "forward"
                      ? "Link from selected entries to target"
                      : "Link from target to selected entries"
                  }
                >
                  {relationDirection === "forward" ? "➡️" : "⬅️"}
                </button>

                <select
                  className="form-select form-select-sm"
                  value={associationType}
                  onChange={(event) =>
                    setAssociationType(event.target.value as AssociationOption)
                  }
                  disabled={isBusy}
                >
                  {ASSOCIATION_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>

                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={handleSetAssociation}
                  disabled={isBusy || !selectedCount}
                >
                  set
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {modalMessage && (
        <div
          className="position-fixed top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center"
          style={{ backgroundColor: "rgba(0, 0, 0, 0.35)", zIndex: 1050 }}
        >
          <div className="bg-white border rounded-3 shadow p-4" role="dialog" aria-modal="true">
            <h3 className="h5">{modalMessage.title}</h3>
            <p className="mb-4">{modalMessage.body}</p>
            <div className="text-end">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setModalMessage(null)}
              >
                OK
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SearchPanel;
