import React, { useEffect, useState } from "react";

interface AutoSummaryEntry {
  id: string;
  text: string;
  url: string;
  selected: boolean;
}

interface AutoInvoiceSummaryPanelProps {
  invoiceUuid: string;
  autoSummaryRaw?: string | null;
}

function generateClientId(seed: number): string {
  const cryptoObj: Crypto | undefined =
    typeof globalThis === "object" && globalThis && "crypto" in globalThis
      ? (globalThis as { crypto?: Crypto }).crypto
      : undefined;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    try {
      return cryptoObj.randomUUID();
    } catch (error) {
      // ignore and fall back to manual id generation
    }
  }
  const random = Math.random().toString(36).slice(2, 10);
  const timestamp = Date.now().toString(36);
  return `auto-${seed}-${timestamp}-${random}`;
}

const AutoInvoiceSummaryPanel: React.FC<AutoInvoiceSummaryPanelProps> = ({ invoiceUuid, autoSummaryRaw }) => {
  const [entries, setEntries] = useState<AutoSummaryEntry[]>([]);
  const [missingMessage, setMissingMessage] = useState<string | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [parseErrorValue, setParseErrorValue] = useState<string>("");
  const [isBusy, setIsBusy] = useState<boolean>(false);
  const [modalError, setModalError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  useEffect(() => {
    setStatusMessage(null);
    setModalError(null);
    setMissingMessage(null);
    setParseError(null);
    setParseErrorValue("");

    const raw = autoSummaryRaw ?? "";
    if (raw.trim() === "") {
      setEntries([]);
      setMissingMessage("Auto summary is blank or missing.");
      return;
    }

    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        throw new Error("Auto summary payload must be an array.");
      }

      const normalized: AutoSummaryEntry[] = parsed.map((item, index) => {
        const base: Record<string, unknown> =
          typeof item === "object" && item !== null ? (item as Record<string, unknown>) : {};
        const textValue = typeof base.text === "string" ? base.text : "";
        const urlValue = typeof base.url === "string" ? base.url : "";
        const rawId = base.client_id;
        const clientId = typeof rawId === "string" && rawId.trim() !== "" ? rawId : generateClientId(index);
        return {
          id: clientId,
          text: textValue,
          url: urlValue,
          selected: false,
        };
      });

      setEntries(normalized);
    } catch (error) {
      setEntries([]);
      setParseError("Failed to parse auto summary.");
      setParseErrorValue(raw);
    }
  }, [autoSummaryRaw]);

  const hasInvoiceUuid = Boolean(invoiceUuid);
  const selectedEntries = entries.filter((entry) => entry.selected);
  const selectedCount = selectedEntries.length;
  const canMutate = hasInvoiceUuid && !isBusy;

  const handleToggleEntry = (id: string) => {
    setEntries((prev) =>
      prev.map((entry) =>
        entry.id === id
          ? { ...entry, selected: !entry.selected }
          : entry
      )
    );
  };

  const handleEntryChange = (id: string, key: "text" | "url", value: string) => {
    setEntries((prev) =>
      prev.map((entry) =>
        entry.id === id
          ? { ...entry, [key]: value }
          : entry
      )
    );
  };

  const handleDeselectAll = () => {
    setEntries((prev) =>
      prev.map((entry) => ({ ...entry, selected: false }))
    );
  };

  const buildErrorSummary = (failures: unknown): string => {
    if (!Array.isArray(failures) || failures.length === 0) {
      return "";
    }
    const parts: string[] = [];
    failures.forEach((failure) => {
      if (!failure || typeof failure !== "object") {
        return;
      }
      const failureRecord = failure as Record<string, unknown>;
      const display = typeof failureRecord.display === "string" && failureRecord.display.trim() !== ""
        ? failureRecord.display
        : "(unnamed entry)";
      const detail = typeof failureRecord.error === "string" && failureRecord.error.trim() !== ""
        ? failureRecord.error
        : "Unknown error";
      parts.push(`${display}: ${detail}`);
    });
    return parts.join("\n");
  };

  const performInsert = async (entriesToInsert: AutoSummaryEntry[]) => {
    if (!hasInvoiceUuid) {
      setModalError("Invoice UUID is unavailable; save the invoice before inserting items.");
      return;
    }
    if (entriesToInsert.length === 0) {
      setModalError("Select at least one row before inserting.");
      return;
    }

    setIsBusy(true);
    setModalError(null);
    setStatusMessage(null);

    try {
      const payloadItems = entriesToInsert.map((entry) => {
        const nameCandidate = entry.text.trim();
        const urlCandidate = entry.url.trim();
        const fallbackName = urlCandidate || "(auto summary item)";
        return {
          client_id: entry.id,
          name: nameCandidate || fallbackName,
          url: urlCandidate,
        };
      });

      const response = await fetch("/api/autogenitems", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          invoice_uuid: invoiceUuid,
          items: payloadItems,
        }),
      });

      let data: any = null;
      try {
        data = await response.json();
      } catch (error) {
        data = null;
      }

      if (!response.ok) {
        const message = (data && (data.error || data.message))
          ? data.error || data.message
          : `Request failed: ${response.status}`;
        throw new Error(message);
      }

      const succeededIds = Array.isArray(data?.succeeded_ids)
        ? data.succeeded_ids.map((value: unknown) => String(value))
        : [];

      if (succeededIds.length > 0) {
        setEntries((prev) => prev.filter((entry) => !succeededIds.includes(entry.id)));
        setStatusMessage(`Inserted ${succeededIds.length} item${succeededIds.length === 1 ? "" : "s"}.`);
      } else {
        setStatusMessage(null);
      }

      const failureSummary = buildErrorSummary(data?.failures);
      const fallbackMessage = typeof data?.message === "string" && data.message.trim() !== ""
        ? data.message
        : typeof data?.error === "string" && data.error.trim() !== ""
          ? data.error
          : "";

      if (failureSummary) {
        setModalError(failureSummary);
      } else if (!succeededIds.length && fallbackMessage) {
        setModalError(fallbackMessage);
      }
    } catch (error: any) {
      const message = error?.message || "Failed to insert items.";
      setModalError(message);
    } finally {
      setIsBusy(false);
    }
  };

  const handleInsertSelected = () => {
    performInsert(selectedEntries.map((entry) => ({ ...entry })));
  };

  const handleRowInsert = (entry: AutoSummaryEntry) => {
    if (!entry.selected) {
      setModalError("Select the row using its checkbox before inserting it.");
      return;
    }
    performInsert([{ ...entry }]);
  };

  return (
    <section className="mt-4">
      <h2 className="h5 mb-3">Auto-generated Summary</h2>
      {!hasInvoiceUuid && (
        <div className="alert alert-info" role="status">
          Save the invoice to activate automatic item insertion.
        </div>
      )}
      {missingMessage && (
        <div className="alert alert-warning" role="alert">{missingMessage}</div>
      )}
      {parseError && (
        <div className="alert alert-danger" role="alert">
          <p className="mb-2">{parseError}</p>
          <pre className="bg-light border rounded p-2 text-break" style={{ maxHeight: "12rem", overflow: "auto" }}>{parseErrorValue}</pre>
        </div>
      )}
      {statusMessage && (
        <div className="alert alert-success" role="status">{statusMessage}</div>
      )}

      {!missingMessage && !parseError && entries.length === 0 && (
        <div className="alert alert-secondary" role="status">
          No auto-generated summary entries are available.
        </div>
      )}

      {!missingMessage && !parseError && entries.length > 0 && (
        <div className="table-responsive">
          <table className="table table-sm align-middle">
            <tbody>
              {entries.map((entry, index) => {
                const checkboxId = `auto-summary-checkbox-${index}`;
                const insertLabel = isBusy ? "⏳" : "🪄➕";
                return (
                  <tr key={entry.id}>
                    <td className="text-center align-top" style={{ width: "1%", whiteSpace: "nowrap" }}>
                      <button
                        type="button"
                        className="btn btn-outline-primary btn-sm"
                        onClick={() => handleRowInsert(entry)}
                        disabled={!canMutate}
                        title="Click to insert selected entries"
                      >
                        {insertLabel}
                      </button>
                    </td>
                    <td>
                      <div className="d-flex justify-content-between align-items-center mb-2">
                        <div className="form-check mb-0">
                          <input
                            id={checkboxId}
                            type="checkbox"
                            className="form-check-input"
                            checked={entry.selected}
                            onChange={() => handleToggleEntry(entry.id)}
                            disabled={isBusy}
                          />
                          <label className="form-check-label" htmlFor={checkboxId}>Use this entry</label>
                        </div>
                      </div>
                      <input
                        type="text"
                        className="form-control form-control-sm mb-2"
                        value={entry.text}
                        placeholder="no text"
                        onChange={(event) => handleEntryChange(entry.id, "text", event.target.value)}
                        disabled={isBusy}
                      />
                      <input
                        type="text"
                        className="form-control form-control-sm"
                        value={entry.url}
                        placeholder="no URL"
                        onChange={(event) => handleEntryChange(entry.id, "url", event.target.value)}
                        disabled={isBusy}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!missingMessage && !parseError && entries.length > 0 && (
        <div className="d-flex justify-content-between align-items-center mt-3">
          <div className="text-muted small">{selectedCount} selected</div>
          <div className="d-flex gap-2">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleInsertSelected}
              disabled={!canMutate || selectedCount === 0}
              title="Click to insert selected entries"
            >
              {isBusy ? "⏳" : "🪄➕"}
            </button>
            <button
              type="button"
              className="btn btn-outline-secondary btn-sm"
              onClick={handleDeselectAll}
              disabled={isBusy || selectedCount === 0}
            >
              ❌☐☐
            </button>
          </div>
        </div>
      )}

      {modalError && (
        <div
          className="position-fixed top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center"
          style={{ backgroundColor: "rgba(0, 0, 0, 0.5)", zIndex: 1050 }}
        >
          <div className="bg-white rounded shadow p-4" style={{ maxWidth: "32rem", width: "90%" }}>
            <h3 className="h5 mb-3">Auto-generated items</h3>
            <p className="mb-3" style={{ whiteSpace: "pre-wrap" }}>{modalError}</p>
            <div className="text-end">
              <button type="button" className="btn btn-primary" onClick={() => setModalError(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
};

export default AutoInvoiceSummaryPanel;
