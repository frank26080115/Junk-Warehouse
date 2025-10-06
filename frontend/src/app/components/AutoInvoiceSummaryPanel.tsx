import React, { useEffect, useRef, useState } from "react";

interface AutoSummaryEntry {
  id: string;
  text: string;
  image: string;
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

const MIN_FREEFORM_ROWS = 3;
const MAX_DATA_URL_LENGTH = 1_500_000; // ~1.5 MB worth of base64 text

const isEntryBlank = (entry: AutoSummaryEntry): boolean =>
  entry.text.trim() === "" && entry.image.trim() === "";

const createBlankEntry = (seed: number): AutoSummaryEntry => ({
  id: generateClientId(seed),
  text: "",
  image: "",
});

const PUNCTUATION_FOR_NAME = /[!"#$%&'()*+,\-./:;<=>?@[\]^_`{|}~]/g;

const extractTaggedSection = (text: string, tag: string): string => {
  const pattern = new RegExp(`#\s*${tag}\b([^#]*)`, "i");
  const match = pattern.exec(text);
  if (!match) {
    return "";
  }
  const rawValue = match[1] ?? "";
  const withoutPrefix = rawValue.replace(/^:\s*/, "");
  return withoutPrefix.trim();
};

const sanitizeNameForSearch = (value: string): string => {
  const normalizedWhitespace = value.replace(/\r?\n/g, " ");
  const withoutPunctuation = normalizedWhitespace.replace(PUNCTUATION_FOR_NAME, " ");
  return withoutPunctuation.replace(/\s+/g, " ").trim();
};

const sanitizeFilterToken = (value: string): string => {
  const normalizedWhitespace = value.replace(/\r?\n/g, " ");
  const withoutControl = normalizedWhitespace.replace(/[\[\]\|?]/g, " ");
  return withoutControl.replace(/\s+/g, " ").trim();
};

const extractSemicolonValues = (text: string, tag: string): string[] => {
  const section = extractTaggedSection(text, tag);
  if (!section) {
    return [];
  }
  const rawParts = section.split(";");
  const cleaned: string[] = [];
  rawParts.forEach((part) => {
    const sanitized = sanitizeFilterToken(part);
    if (sanitized) {
      cleaned.push(sanitized);
    }
  });
  return cleaned;
};

const buildNameSearchQuery = (text: string): string | null => {
  const section = extractTaggedSection(text, "name");
  if (!section) {
    return null;
  }
  const sanitized = sanitizeNameForSearch(section);
  return sanitized || null;
};

const buildCodeOrUrlSearchQuery = (text: string): string | null => {
  const productCodes = extractSemicolonValues(text, "product_code");
  const urls = extractSemicolonValues(text, "url");
  const filters: string[] = [];
  productCodes.forEach((code) => {
    filters.push(`?product_code[${code}]`);
  });
  urls.forEach((url) => {
    filters.push(`?url[${url}]`);
  });
  if (filters.length === 0) {
    return null;
  }
  return `* ${filters.join(" | ")}`;
};

const openSearchWindow = (query: string): void => {
  if (typeof window === "undefined") {
    return;
  }
  const encodedQuery = encodeURIComponent(query);
  window.open(`/search/${encodedQuery}`, "_blank", "noopener,noreferrer");
};

const ensureFreeformRows = (list: AutoSummaryEntry[]): AutoSummaryEntry[] => {
  const next = [...list];
  let blanks = next.filter(isEntryBlank).length;
  let seed = next.length;
  while (blanks < MIN_FREEFORM_ROWS) {
    next.push(createBlankEntry(seed));
    blanks += 1;
    seed += 1;
  }
  return next;
};

const AutoInvoiceSummaryPanel: React.FC<AutoInvoiceSummaryPanelProps> = ({ invoiceUuid, autoSummaryRaw }) => {
  const [entries, setEntries] = useState<AutoSummaryEntry[]>([]);
  const [missingMessage, setMissingMessage] = useState<string | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [parseErrorValue, setParseErrorValue] = useState<string>("");
  const [isBusy, setIsBusy] = useState<boolean>(false);
  const [modalError, setModalError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    setStatusMessage(null);
    setModalError(null);
    setMissingMessage(null);
    setParseError(null);
    setParseErrorValue("");

    const raw = autoSummaryRaw ?? "";
    if (raw.trim() === "") {
      setEntries(ensureFreeformRows([]));
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
        const rawId = base.client_id;
        const clientId = typeof rawId === "string" && rawId.trim() !== "" ? rawId : generateClientId(index);
        const imageValue = typeof base.image === "string" ? base.image : "";
        return {
          id: clientId,
          text: textValue,
          image: imageValue,
        };
      });

      setEntries(ensureFreeformRows(normalized));
      setMissingMessage(null);
    } catch (error) {
      setEntries(ensureFreeformRows([]));
      setParseError("Failed to parse auto summary.");
      setParseErrorValue(raw);
    }
  }, [autoSummaryRaw]);

  useEffect(() => {
    if (!modalError) {
      return;
    }
    // Respect the expectation that any modal overlay should dismiss when Escape is pressed so the
    // user never feels trapped after encountering an error dialog.
    const handleEscapeKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setModalError(null);
      }
    };
    window.addEventListener("keydown", handleEscapeKey);
    return () => {
      window.removeEventListener("keydown", handleEscapeKey);
    };
  }, [modalError]);

  const hasInvoiceUuid = Boolean(invoiceUuid);
  const canMutate = hasInvoiceUuid && !isBusy;

  const handleEntryChange = (id: string, key: "text" | "image", value: string) => {
    if (key === "image" && value.startsWith("data:") && value.length > MAX_DATA_URL_LENGTH) {
      setModalError(
        "The pasted image is too large to store as a data URL. Please choose a smaller image or host it externally."
      );
      return;
    }
    setEntries((prev) =>
      ensureFreeformRows(
        prev.map((entry) =>
          entry.id === id
            ? { ...entry, [key]: value }
            : entry
        )
      )
    );
  };

  const handleTextAreaKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Provide keyboard shortcuts so users can research potential duplicates without losing focus.
    if (!event.ctrlKey || !event.shiftKey || !event.altKey) {
      return;
    }
    if (event.repeat) {
      return;
    }

    const pressedKey = event.key.toLowerCase();
    const currentValue = event.currentTarget.value;

    if (pressedKey === "f") {
      const query = buildNameSearchQuery(currentValue);
      if (query) {
        event.preventDefault();
        openSearchWindow(query);
      }
      return;
    }

    if (pressedKey === "g") {
      const query = buildCodeOrUrlSearchQuery(currentValue);
      if (query) {
        event.preventDefault();
        openSearchWindow(query);
      }
    }
  };

  const handleImagePaste = (id: string, event: React.ClipboardEvent<HTMLInputElement>) => {
    const clipboardData = event.clipboardData;
    if (!clipboardData) {
      return;
    }
    const items = clipboardData.items;
    if (!items) {
      return;
    }
    const imageItem = Array.from(items).find((item) => item.type.startsWith("image/"));
    if (!imageItem) {
      return;
    }
    event.preventDefault();
    const file = imageItem.getAsFile();
    if (!file) {
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        setModalError("Unable to read the pasted image data.");
        return;
      }
      if (result.length > MAX_DATA_URL_LENGTH) {
        setModalError(
          "The pasted image is too large to convert to a data URL. Please choose a smaller image or upload it separately."
        );
        return;
      }
      setEntries((prev) =>
        ensureFreeformRows(
          prev.map((entry) =>
            entry.id === id
              ? { ...entry, image: result }
              : entry
          )
        )
      );
    };
    reader.onerror = () => {
      setModalError("Failed to process the pasted image.");
    };
    reader.readAsDataURL(file);
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
      setModalError("Provide at least one row before inserting.");
      return;
    }

    setIsBusy(true);
    setModalError(null);
    setStatusMessage(null);

    try {
      const payloadItems = entriesToInsert.map((entry) => {
        const normalizedText = entry.text.replace(/\r\n?/g, "\n").trim();
        const imageCandidate = entry.image.trim();
        return {
          client_id: entry.id,
          text: normalizedText,
          image: imageCandidate,
        };
      });

      const missingTextEntry = payloadItems.find((item) => item.text === "");
      if (missingTextEntry) {
        setModalError("Each entry must include tagged text before inserting.");
        return;
      }

      // Submit the request and collect the immediate response payload because the backend now runs the work inline.
      const response = await fetch("/api/autogenitems", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          invoice_uuid: invoiceUuid,
          items: payloadItems,
        }),
      });

      let rawData: any = null;
      try {
        rawData = await response.json();
      } catch (error) {
        rawData = null;
      }

      if (!response.ok) {
        const responseErrorText =
          typeof rawData?.error === "string" && rawData.error.trim() !== ""
            ? rawData.error
            : typeof rawData?.message === "string" && rawData.message.trim() !== ""
            ? rawData.message
            : `Request failed: ${response.status}`;
        throw new Error(responseErrorText);
      }

      // Abort any follow-up state updates if the component unmounted while waiting for the server reply.
      if (!isMountedRef.current) {
        return;
      }

      const payloadRecord: Record<string, unknown> =
        rawData && typeof rawData === "object" ? (rawData as Record<string, unknown>) : {};

      const rawSucceededValues = Array.isArray(payloadRecord["succeeded_ids"] as unknown[])
        ? (payloadRecord["succeeded_ids"] as unknown[])
        : [];
      const succeededIds = rawSucceededValues.map((value) => String(value));

      if (succeededIds.length > 0) {
        setEntries((prev) =>
          ensureFreeformRows(prev.filter((entry) => !succeededIds.includes(entry.id))),
        );
      }

      const successFlag = Boolean(payloadRecord["success"]);

      const rawMessage = payloadRecord["message"];
      const messageText =
        typeof rawMessage === "string" && rawMessage.trim() !== ""
          ? rawMessage.trim()
          : succeededIds.length > 0
          ? `Inserted ${succeededIds.length} item${succeededIds.length === 1 ? "" : "s"}.`
          : "";

      const shouldShowStatus = successFlag || succeededIds.length > 0;
      setStatusMessage(shouldShowStatus && messageText ? messageText : null);

      const failureSummary = buildErrorSummary(payloadRecord["failures"]);
      const rawError = payloadRecord["error"];
      const fallbackError =
        typeof rawError === "string" && rawError.trim() !== "" ? rawError.trim() : "";

      if (failureSummary) {
        setModalError(failureSummary);
      } else if (!successFlag) {
        const combinedError =
          fallbackError || messageText || "Auto-generated item insertion did not succeed.";
        if (combinedError) {
          setModalError(combinedError);
        }
      }
    } catch (error: any) {
      if (!isMountedRef.current) {
        return;
      }
      const message = error?.message || "Failed to insert items.";
      setModalError(message);
    } finally {
      if (isMountedRef.current) {
        setIsBusy(false);
      }
    }
  };

  const handleRowInsert = (entry: AutoSummaryEntry) => {
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
      {isBusy && (
        <div className="alert alert-info" role="status">Processing auto-generated items…</div>
      )}

      {statusMessage && (
        <div className="alert alert-success" role="status">{statusMessage}</div>
      )}

      {!parseError && entries.length === 0 && (
        <div className="alert alert-secondary" role="status">
          No auto-generated summary entries are available.
        </div>
      )}

      {entries.length > 0 && (
        <div className="table-responsive">
          <table className="table table-sm align-middle">
            <tbody>
              {entries.map((entry, index) => {
                const insertLabel = isBusy ? "⏳" : "🪄➕";
                const displayNumber = index + 1;
                return (
                  <tr key={entry.id}>
                    <td className="text-center align-top" style={{ width: "1%", whiteSpace: "nowrap" }}>
                      <button
                        type="button"
                        className="btn btn-outline-primary btn-sm"
                        onClick={() => handleRowInsert(entry)}
                        disabled={!canMutate}
                        title="Insert this entry into the invoice"
                      >
                        {insertLabel}
                      </button>
                    </td>
                    <td>
                      <div className="text-muted small mb-2">Suggested entry #{displayNumber}</div>
                      {/* Multiline area keeps tagged text readable while giving the user room to expand. */}
                      <div className="input-group input-group-sm mb-2">
                        <span className="input-group-text" aria-hidden="true">🪪</span>
                        <textarea
                          className="form-control"
                          value={entry.text}
                          placeholder="use # Name, # URL, # Notes, …"
                          rows={3}
                          style={{ resize: "vertical" }}
                          onKeyDown={handleTextAreaKeyDown}
                          onChange={(event) => handleEntryChange(entry.id, "text", event.target.value)}
                          disabled={isBusy}
                        />
                      </div>
                      <div className="input-group input-group-sm">
                        <span className="input-group-text" aria-hidden="true">📸</span>
                        <input
                          type="text"
                          className="form-control"
                          value={entry.image}
                          placeholder="no image URL"
                          onChange={(event) => handleEntryChange(entry.id, "image", event.target.value)}
                          onPaste={(event) => handleImagePaste(entry.id, event)}
                          disabled={isBusy}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
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
