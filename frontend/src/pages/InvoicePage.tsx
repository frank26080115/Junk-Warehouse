import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import "../styles/forms.css";

import AutoInvoiceSummaryPanel from "../app/components/AutoInvoiceSummaryPanel";
import { PIN_OPEN_EXPIRY_MS } from "../app/config";

type JobStatus = "queued" | "busy" | "done" | "error";

interface InvoiceDto {
  id?: string;
  date?: string | null;
  order_number?: string;
  shop_name?: string;
  urls?: string;
  subject?: string;
  html?: string;
  has_been_processed?: boolean;
  snooze?: string | null;
  is_deleted?: boolean;
  auto_summary?: string | null;
  pin_as_opened?: string | null; // ISO timestamp when the invoice was pinned as opened
}

const EMPTY_INVOICE: InvoiceDto = {
  id: "",
  date: null,
  order_number: "",
  shop_name: "",
  urls: "",
  subject: "",
  html: "",
  has_been_processed: false,
  snooze: null,
  is_deleted: false,
  auto_summary: "",
  pin_as_opened: null,
};

function describeRelativeDays(date: Date): string | null {
  // Communicate how recent the invoice is without forcing the user to do mental math.
  const now = new Date();
  const msPerDay = 24 * 60 * 60 * 1000;
  const diffMs = now.getTime() - date.getTime();
  const rawDays = diffMs / msPerDay;
  if (Math.abs(rawDays) < 1) {
    return "today";
  }
  if (rawDays > 0) {
    const fullDays = Math.floor(rawDays);
    return fullDays === 1 ? "1 day ago" : `${fullDays} days ago`;
  }
  const fullDaysAhead = Math.ceil(Math.abs(rawDays));
  return fullDaysAhead === 1 ? "in 1 day" : `in ${fullDaysAhead} days`;
}

function fmtDateTime(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(+date)) return "";
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  const hours = `${date.getHours()}`.padStart(2, "0");
  const minutes = `${date.getMinutes()}`.padStart(2, "0");
  const base = `${year}/${month}/${day} ${hours}:${minutes}`;
  const relative = describeRelativeDays(date);
  return relative ? `${base} (${relative})` : base;
}

function toDateInputValue(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(+date)) return "";
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function fromDateInputValue(value: string): string | null {
  if (!value) return null;
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return null;
  const date = new Date(year, month - 1, day, 0, 0, 0);
  return date.toISOString();
}

function splitUrls(value?: string): string[] {
  if (!value) return [];
  return value
    .split(";")
    .map((url) => url.trim())
    .filter((url) => url.length > 0);
}


const GMAIL_URL_PATTERN = /mail\.google\.com|gmail\.com/i;

function isGmailUrl(url: string): boolean {
  return GMAIL_URL_PATTERN.test(url);
}

function truncateUrlForDisplay(url: string, maxLength = 30): string {
  // Keep the display readable while still hinting at the destination.
  if (url.length <= maxLength) {
    return url;
  }
  const visiblePortion = Math.max(maxLength - 3, 0);
  return `${url.slice(0, visiblePortion)}...`;
}


function describePinTimestamp(value?: string | null): { readable: string; instant: Date | null } {
  if (!value) {
    return { readable: "not yet pinned", instant: null };
  }
  const instant = new Date(value);
  if (Number.isNaN(+instant)) {
    return { readable: "not yet pinned", instant: null };
  }
  const month = `${instant.getMonth() + 1}`.padStart(2, "0");
  const day = `${instant.getDate()}`.padStart(2, "0");
  const hours = `${instant.getHours()}`.padStart(2, "0");
  const minutes = `${instant.getMinutes()}`.padStart(2, "0");
  return { readable: `${month}/${day}-${hours}:${minutes}`, instant };
}

const InvoicePage: React.FC = () => {
  const { uuid } = useParams();
  const navigate = useNavigate();
  const isNewInvoice = !uuid || uuid === "new";
  const [invoice, setInvoice] = useState<InvoiceDto>({ ...EMPTY_INVOICE });
  const [snapshot, setSnapshot] = useState<InvoiceDto>({ ...EMPTY_INVOICE });
  const [isReadOnly, setIsReadOnly] = useState<boolean>(() => !isNewInvoice);
  const [loading, setLoading] = useState<boolean>(false);
  const [saving, setSaving] = useState<boolean>(false);
  const [error, setError] = useState<string>("");
  const [success, setSuccess] = useState<string>("");
  const [htmlExpanded, setHtmlExpanded] = useState<boolean>(false);
  const [analyzeHtml, setAnalyzeHtml] = useState<string>("");
  const [analyzingHtml, setAnalyzingHtml] = useState<boolean>(false);

  const [analyzeJobId, setAnalyzeJobId] = useState<string | null>(null);
  const [analyzeJobStatus, setAnalyzeJobStatus] = useState<JobStatus | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const pollJobUntilComplete = async (
    jobId: string,
    onStatusUpdate: (status: JobStatus) => void,
  ): Promise<any> => {
    let delay = 1000;
    while (isMountedRef.current) {
      const response = await fetch(`/api/jobstatus?id=${encodeURIComponent(jobId)}`);
      let payload: any = null;
      try {
        payload = await response.json();
      } catch (error) {
        payload = null;
      }
      if (!response.ok) {
        const message =
          (payload && (payload.error || payload.message)) ||
          response.statusText ||
          "Failed to query job status.";
        throw new Error(message);
      }
      const rawStatus = typeof payload?.status === "string" ? payload.status : "";
      let normalised: JobStatus = "queued";
      if (rawStatus === "busy" || rawStatus === "done" || rawStatus === "error" || rawStatus === "queued") {
        normalised = rawStatus as JobStatus;
      }
      onStatusUpdate(normalised);
      if (normalised === "done") {
        return payload?.result;
      }
      if (normalised === "error") {
        const message =
          typeof payload?.error === "string" && payload.error.trim()
            ? payload.error
            : "Job failed.";
        throw new Error(message);
      }
      await new Promise((resolve) => setTimeout(resolve, delay));
      delay = Math.min(5000, delay + 500);
    }
    throw new Error("Job monitoring cancelled.");
  };

  useEffect(() => {
    let ignore = false;
    async function load() {
      if (isNewInvoice) {
        const blank = { ...EMPTY_INVOICE };
        if (!ignore) {
          setInvoice(blank);
          setSnapshot(blank);
          setIsReadOnly(false);
        }
        return;
      }
      try {
        setLoading(true);
        setError("");
        const res = await fetch("/api/getinvoice", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ uuid }),
        });
        if (!res.ok) throw new Error(`GET failed: ${res.status}`);
        const data: InvoiceDto = await res.json();
        if (!ignore) {
          const merged = { ...EMPTY_INVOICE, ...data };
          setInvoice(merged);
          setSnapshot(merged);
          setIsReadOnly(true);
        }
      } catch (e: any) {
        console.error(e);
        if (!ignore) {
          setError(e?.message || "Failed to load invoice");
        }
      } finally {
        if (!ignore) setLoading(false);
      }
    }
    load();
    return () => {
      ignore = true;
    };
  }, [uuid, isNewInvoice]);

  const urlEntries = useMemo(() => splitUrls(invoice.urls), [invoice.urls]);
  const singleMailUrl = useMemo(() => {
    if (urlEntries.length === 1 && isGmailUrl(urlEntries[0])) {
      return urlEntries[0];
    }
    return null;
  }, [urlEntries]);
  const effectiveUuid = invoice.id || uuid || "";
  const pinDetails = describePinTimestamp(invoice.pin_as_opened);
  const isPinCurrentlyActive = pinDetails.instant
    ? (() => {
        const ageMs = Date.now() - pinDetails.instant.getTime();
        return ageMs >= 0 && ageMs <= PIN_OPEN_EXPIRY_MS;
      })()
    : false;

  const snoozeNeedsAttention = useMemo(() => {
    if (invoice.has_been_processed) return false;
    if (!invoice.snooze) return false;
    const date = new Date(invoice.snooze);
    if (Number.isNaN(+date)) return false;
    const snoozeDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const today = new Date();
    const todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    return snoozeDay < todayStart;
  }, [invoice.has_been_processed, invoice.snooze]);

  const handleFieldChange = (key: keyof InvoiceDto, value: InvoiceDto[keyof InvoiceDto]) => {
    if (isReadOnly) return;
    setInvoice((prev) => ({ ...prev, [key]: value }));
  };

  // Persisting early ensures callbacks can reference this function without temporal dead zone issues.
  const persistInvoice = useCallback(
    async (
      proposedInvoice: InvoiceDto,
      options?: { successMessage?: string; lockAfterSave?: boolean; navigateOnIdChange?: boolean },
    ) => {
      const {
        successMessage = "Invoice saved.",
        lockAfterSave = true,
        navigateOnIdChange = true,
      } = options || {};
      try {
        setSaving(true);
        setError("");
        setSuccess("");
        const payload: InvoiceDto = {
          ...proposedInvoice,
          id: proposedInvoice.id || (!isNewInvoice && uuid ? uuid : proposedInvoice.id),
        };
        const res = await fetch("/api/setinvoice", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(`Save failed: ${res.status}`);
        const data: InvoiceDto = await res.json();
        const merged = { ...EMPTY_INVOICE, ...data };
        setInvoice(merged);
        setSnapshot(merged);
        if (lockAfterSave) {
          setIsReadOnly(true);
        }
        if (successMessage) {
          setSuccess(successMessage);
        }
        if (navigateOnIdChange && merged.id && uuid !== merged.id) {
          navigate(`/invoice/${merged.id}`, { replace: true });
        }
      } catch (e: any) {
        console.error(e);
        setError(e?.message || "Failed to save invoice");
      } finally {
        setSaving(false);
      }
    },
    [isNewInvoice, navigate, uuid],
  );
  const handleDeletedChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const nextValue = event.target.checked;
      const proposedInvoice: InvoiceDto = { ...invoice, is_deleted: nextValue };
      setInvoice(proposedInvoice);
      setSuccess("");
      setError("");
      if (!effectiveUuid) {
        return;
      }
      void persistInvoice(proposedInvoice, {
        successMessage: nextValue ? "Invoice moved to trash." : "Invoice restored.",
        lockAfterSave: isReadOnly,
      });
    },
    [effectiveUuid, invoice, isReadOnly, persistInvoice],
  );

  const handleProcessedChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const nextValue = event.target.checked;
      const proposedInvoice: InvoiceDto = { ...invoice, has_been_processed: nextValue };
      setInvoice(proposedInvoice);
      setSuccess("");
      setError("");
      if (!effectiveUuid) {
        return;
      }
      void persistInvoice(proposedInvoice, {
        successMessage: nextValue ? "Invoice marked as processed." : "Invoice marked as not processed.",
        lockAfterSave: isReadOnly,
      });
    },
    [effectiveUuid, invoice, isReadOnly, persistInvoice],
  );

  const handleUrlsChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    handleFieldChange("urls", event.target.value);
  };

  const handleSnoozeChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    handleFieldChange("snooze", fromDateInputValue(event.target.value));
  };

  const handleEdit = () => {
    setIsReadOnly(false);
    setSuccess("");
    setError("");
  };

  const handleCancel = () => {
    setInvoice(snapshot);
    setIsReadOnly(true);
    setSuccess("");
    setError("");
  };


  const handleSave = async () => {
    await persistInvoice(invoice);
  };

  const handlePinUpdate = useCallback(
    async (nextValue: string | null) => {
      const proposedInvoice: InvoiceDto = { ...invoice, pin_as_opened: nextValue };
      setInvoice(proposedInvoice);
      if (!effectiveUuid) {
        return;
      }
      await persistInvoice(proposedInvoice, { successMessage: "Pin state saved." });
    },
    [effectiveUuid, invoice, persistInvoice],
  );

  const handleAnalyzeHtmlJob = async () => {
    const targetUuid = invoice.id || uuid || "";
    if (!targetUuid) {
      setError("Cannot analyze HTML without an invoice ID.");
      return;
    }
    if (!analyzeHtml.trim()) {
      setError("Please provide HTML to analyze.");
      return;
    }
    try {
      setAnalyzingHtml(true);
      setAnalyzeJobId(null);
      setAnalyzeJobStatus(null);
      setError("");
      setSuccess("");
      const response = await fetch("/api/analyzeinvoicehtml", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uuid: targetUuid, html: analyzeHtml }),
      });
      let payload: any = null;
      try {
        payload = await response.json();
      } catch (error) {
        payload = null;
      }
      if (!response.ok) {
        const message =
          payload && typeof payload === "object" && "error" in payload && typeof (payload as any).error === "string"
            ? (payload as any).error
            : `Analyze failed: ${response.status}`;
        throw new Error(message);
      }
      const jobId: string | null =
        (payload && ((payload as any).job_id || (payload as any).jobId)) || null;
      if (!jobId || typeof jobId !== "string") {
        throw new Error("Job identifier was not provided.");
      }
      setAnalyzeJobId(jobId);
      setAnalyzeJobStatus("queued");
      const result = await pollJobUntilComplete(jobId, (status) => {
        if (isMountedRef.current) {
          setAnalyzeJobStatus(status);
        }
      });
      if (!isMountedRef.current) {
        return;
      }
      const invoiceData =
        result && typeof result === "object" && "invoice" in result ? (result as any).invoice : undefined;
      if (invoiceData && typeof invoiceData === "object") {
        setInvoice((prev) => ({ ...prev, ...(invoiceData as InvoiceDto) }));
        setSnapshot((prev) => ({ ...prev, ...(invoiceData as InvoiceDto) }));
      }
      setAnalyzeHtml("");
      setSuccess("Additional HTML analyzed.");
      setHtmlExpanded(true);
    } catch (err: any) {
      if (!isMountedRef.current) {
        return;
      }
      console.error(err);
      setError(err?.message || "Failed to analyze HTML");
    } finally {
      if (isMountedRef.current) {
        setAnalyzingHtml(false);
        setAnalyzeJobId(null);
        setAnalyzeJobStatus(null);
      }
    }
  };

  const snoozeInputValue = useMemo(() => toDateInputValue(invoice.snooze), [invoice.snooze]);

  const snoozeClassName = useMemo(() => {
    const classes = ["form-control"];
    if (isReadOnly) classes.push("bg-light");
    if (snoozeNeedsAttention) classes.push("border-warning", "bg-warning-subtle");
    return classes.join(" ");
  }, [isReadOnly, snoozeNeedsAttention]);

  return (
    <div className="container py-4">
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h1 className="h3 mb-0">Invoice</h1>
        <div className="d-flex gap-2">
          {isReadOnly ? (
            <button className="btn btn-primary" type="button" onClick={handleEdit} disabled={loading}>
              Edit
            </button>
          ) : (
            <>
              <button className="btn btn-secondary" type="button" onClick={handleCancel} disabled={saving}>
                Cancel
              </button>
              <button className="btn btn-success" type="button" onClick={handleSave} disabled={saving}>
                {saving ? "Saving..." : "Save"}
              </button>
            </>
          )}
        </div>
      </div>
      {loading && (
        <div className="alert alert-info" role="status">Loading invoice...</div>
      )}
      {error && (
        <div className="alert alert-danger" role="alert">{error}</div>
      )}
      {success && (
        <div className="alert alert-success" role="status">{success}</div>
      )}
      <div className="mb-3">
        <label className="form-label">📅 Date</label>
        <div className="form-control-plaintext">{fmtDateTime(invoice.date)}</div>
      </div>
      <div className="mb-3">
        <label className="form-label">✉️ Subject</label>
        <div className="form-control-plaintext">{invoice.subject || ""}</div>
      </div>
      <div className="mb-3">
        <label className="form-label" htmlFor="invoice-order-number">🛒🔢 Order Number</label>
        <input
          id="invoice-order-number"
          type="text"
          className="form-control"
          value={invoice.order_number || ""}
          onChange={(event) => handleFieldChange("order_number", event.target.value)}
          disabled={isReadOnly}
        />
      </div>
      <div className="mb-3">
        <label className="form-label" htmlFor="invoice-shop-name">🛒🏠 Store Name</label>
        <input
          id="invoice-shop-name"
          type="text"
          className="form-control"
          value={invoice.shop_name || ""}
          onChange={(event) => handleFieldChange("shop_name", event.target.value)}
          disabled={isReadOnly}
        />
      </div>
      <div className="mb-3">
        <label className="form-label" htmlFor="invoice-urls">🔗🔗🔗 URLs</label>
        {isReadOnly ? (
          singleMailUrl ? (
            <a
              href={singleMailUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="d-inline-flex align-items-center gap-2 text-decoration-none"
              title={singleMailUrl}
            >
              <span role="img" aria-label="Email link">
                ✉️
              </span>
              <span className="text-break">{truncateUrlForDisplay(singleMailUrl)}</span>
            </a>
          ) : (
            <div className="d-flex flex-wrap gap-2">
              {urlEntries.length === 0 && <span className="text-muted">No URLs</span>}
              {urlEntries.map((url, index) => {
                const linkKey = `${index}-${url}`;
                const label = isGmailUrl(url) ? "✉️" : "🔗";
                return (
                  <a
                    key={linkKey}
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn btn-sm btn-outline-secondary"
                    title={url}
                  >
                    {label}
                  </a>
                );
              })}
            </div>
          )
        ) : (
          <textarea
            id="invoice-urls"
            className="form-control"
            rows={3}
            value={invoice.urls || ""}
            onChange={handleUrlsChange}
          />
        )}
      </div>
      <div className="mb-3">
        <label className="form-label" htmlFor="invoice-snooze">⏰ Snooze</label>
        <input
          id="invoice-snooze"
          type="date"
          className={snoozeClassName}
          value={snoozeInputValue}
          onChange={handleSnoozeChange}
          disabled={isReadOnly}
        />
      </div>
      <div className="mb-3">
        <div className="d-flex flex-column flex-md-row align-items-start align-items-md-center gap-3">
          <div className="form-check mb-0">
            <input
              id="invoice-deleted-toggle"
              type="checkbox"
              className="form-check-input"
              checked={Boolean(invoice.is_deleted)}
              onChange={handleDeletedChange}
            />
            <label className="form-check-label" htmlFor="invoice-deleted-toggle">
              🗑️ Mark invoice as deleted
            </label>
          </div>
          <div className="form-check mb-0">
            <input
              id="invoice-processed"
              type="checkbox"
              className="form-check-input"
              checked={Boolean(invoice.has_been_processed)}
              onChange={handleProcessedChange}
            />
            <label className="form-check-label" htmlFor="invoice-processed">Has been processed</label>
          </div>
        </div>
      </div>
      <div className="mb-3">
        <button
          type="button"
          className="btn btn-outline-primary w-100 d-flex justify-content-between align-items-center"
          onClick={() => setHtmlExpanded((prev) => !prev)}
          aria-expanded={htmlExpanded}
        >
          <span className="fw-semibold">Show/Hide HTML</span>
          <span aria-hidden="true">{htmlExpanded ? "▲" : "▼"}</span>
        </button>
        {htmlExpanded && (
          <div className="mt-3">
            <div className="ratio ratio-16x9">
              <iframe
                title="Invoice HTML"
                srcDoc={invoice.html || ""}
                className="w-100 h-100 border"
                sandbox="allow-same-origin"
              />
            </div>
          </div>
        )}
      </div>
      <div className="mb-3">
        <label className="form-label" htmlFor="invoice-analyze-html">Analyze More HTML</label>
        <textarea
          id="invoice-analyze-html"
          className="form-control"
          rows={3}
          value={analyzeHtml}
          onChange={(event) => setAnalyzeHtml(event.target.value)}
          onPaste={(event) => {
            const htmlData = event.clipboardData?.getData("text/html");
            if (htmlData) {
              event.preventDefault();
              const textarea = event.target as HTMLTextAreaElement;
              const selectionStart = textarea.selectionStart ?? analyzeHtml.length;
              const selectionEnd = textarea.selectionEnd ?? analyzeHtml.length;
              const updatedValue =
                analyzeHtml.substring(0, selectionStart) + htmlData + analyzeHtml.substring(selectionEnd);
              setAnalyzeHtml(updatedValue);
              const caretPosition = selectionStart + htmlData.length;
              window.setTimeout(() => {
                try {
                  textarea.setSelectionRange(caretPosition, caretPosition);
                } catch (err) {
                  console.error("Failed to restore selection after HTML paste", err);
                }
              }, 0);
            }
          }}
        />
        <button
          type="button"
          className="btn btn-primary mt-2"
          onClick={handleAnalyzeHtmlJob}
          disabled={analyzingHtml || !effectiveUuid || !analyzeHtml.trim()}
        >
          🪄
        </button>
        {analyzingHtml && analyzeJobStatus === "queued" && (
          <div className="text-muted small">Job queued…</div>
        )}
        {analyzingHtml && analyzeJobId && (
          <div className="text-muted small">Job ID: {analyzeJobId}</div>
        )}
      </div>
      <AutoInvoiceSummaryPanel invoiceUuid={effectiveUuid} autoSummaryRaw={invoice.auto_summary} />

      {/* Pin controls live at the bottom so they are easy to access after reviewing invoice details */}
      <div className="mt-4">
        <div className="d-flex flex-column flex-md-row align-items-start align-items-md-center gap-3">
          {pinDetails.instant && (
            <div className="fw-semibold">
              {/* Only render the opened timestamp when a valid pin exists to avoid placeholder text */}
              📌🕒 Opened at: {pinDetails.readable}
              {!isPinCurrentlyActive && (
                <span className="text-muted ms-2">(pin expired)</span>
              )}
            </div>
          )}
          <div className="d-flex flex-wrap gap-2">
            {!isPinCurrentlyActive && (
              <button
                type="button"
                className="btn btn-outline-primary"
                onClick={() => handlePinUpdate(new Date().toISOString())}
                disabled={saving || loading || !effectiveUuid}
                title={effectiveUuid ? "Mark this invoice as opened" : "Save the invoice before pinning"}
              >
                📌 Pin Invoice to be Auto-Attached
              </button>
            )}
            {isPinCurrentlyActive && (
              <>
                <button
                  type="button"
                  className="btn btn-outline-danger"
                  onClick={() => handlePinUpdate(null)}
                  disabled={saving || loading || !effectiveUuid}
                  title={effectiveUuid ? "Clear the opened marker" : "Save the invoice before clearing"}
                >
                  ❌📌 Close Pinned
                </button>
                <button
                  type="button"
                  className="btn btn-outline-secondary"
                  onClick={() => handlePinUpdate(new Date().toISOString())}
                  disabled={saving || loading || !effectiveUuid}
                  title={effectiveUuid ? "Refresh the opened timestamp" : "Save the invoice before pinning"}
                >
                  📌➕🕒 Re-pin as Opened
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      <footer className="mt-4 text-muted small">
        Invoice UUID: {effectiveUuid ? (
          <a href={`/invoice/${effectiveUuid}`}>{effectiveUuid}</a>
        ) : (
          <span>Unavailable</span>
        )}
      </footer>
    </div>
  );
};

export default InvoicePage;

