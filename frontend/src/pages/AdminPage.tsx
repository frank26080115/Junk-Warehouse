import React, { useCallback, useState } from "react";

type TaskName =
  | "prune_deleted"
  | "prune_stale_staging_items"
  | "prune_stale_staging_invoices"
  | "prune_images";

const sectionStyle: React.CSSProperties = {
  border: "1px solid #d0d0d0",
  borderRadius: "8px",
  padding: "16px",
  marginBottom: "16px",
  backgroundColor: "#fafafa",
};

const statusTextStyle: React.CSSProperties = {
  marginTop: "8px",
  fontStyle: "italic",
  color: "#555555",
};

const inputGroupStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "8px",
  marginTop: "8px",
  marginBottom: "8px",
};

const resultBoxStyle: React.CSSProperties = {
  marginTop: "12px",
  padding: "12px",
  backgroundColor: "#f5f5f5",
  borderRadius: "4px",
  whiteSpace: "pre-wrap",
  fontFamily: "Menlo, Monaco, Consolas, 'Courier New', monospace",
  fontSize: "0.9rem",
};

const taskTitles: Record<TaskName, string> = {
  prune_deleted: "Prune Soft-Deleted Records",
  prune_stale_staging_items: "Prune Stale Staging Items",
  prune_stale_staging_invoices: "Prune Stale Staging Invoices",
  prune_images: "Prune Unused Images",
};

const AdminPage: React.FC = () => {
  const [activeTask, setActiveTask] = useState<TaskName | null>(null);
  const [lastTask, setLastTask] = useState<TaskName | null>(null);
  const [resultText, setResultText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [itemsCutoff, setItemsCutoff] = useState<string>("");
  const [invoicesCutoff, setInvoicesCutoff] = useState<string>("");

  const busy = activeTask !== null;

  const runTask = useCallback(
    async (task: TaskName, parameters: Record<string, unknown>) => {
      if (activeTask) {
        return;
      }

      setActiveTask(task);
      setLastTask(task);
      setError(null);
      setResultText(null);

      try {
        const response = await fetch("/api/task", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ task, parameters }),
        });

        const responseText = await response.text();
        let payload: unknown = null;

        if (responseText) {
          try {
            payload = JSON.parse(responseText);
          } catch {
            payload = responseText;
          }
        }

        if (!response.ok) {
          if (
            payload &&
            typeof payload === "object" &&
            "error" in payload &&
            typeof (payload as { error: unknown }).error === "string"
          ) {
            setError((payload as { error: string }).error);
          } else {
            setError(`Task failed with status ${response.status}.`);
          }
          return;
        }

        if (
          payload &&
          typeof payload === "object" &&
          "result" in payload
        ) {
          const resultValue = (payload as { result: unknown }).result;
          setResultText(JSON.stringify(resultValue, null, 2));
        } else if (typeof payload === "string") {
          setResultText(payload);
        } else if (payload != null) {
          setResultText(JSON.stringify(payload, null, 2));
        } else {
          setResultText("Task completed.");
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setActiveTask(null);
      }
    },
    [activeTask],
  );

  const handlePruneDeleted = () => {
    runTask("prune_deleted", {});
  };

  const handlePruneStagingItems = () => {
    if (!itemsCutoff) {
      setError("Please choose a cutoff date for staging items.");
      return;
    }
    runTask("prune_stale_staging_items", { cutoff_date: itemsCutoff });
  };

  const handlePruneStagingInvoices = () => {
    if (!invoicesCutoff) {
      setError("Please choose a cutoff date for staging invoices.");
      return;
    }
    runTask("prune_stale_staging_invoices", { cutoff_date: invoicesCutoff });
  };

  const handlePruneImages = () => {
    runTask("prune_images", {});
  };

  return (
    <div style={{ maxWidth: "800px", margin: "0 auto", padding: "1rem" }}>
      <h1>Admin</h1>
      <p>Run maintenance tasks here.</p>

      <section style={sectionStyle}>
        <h2>{taskTitles.prune_deleted}</h2>
        <p>Permanently removes rows that were previously marked as deleted.</p>
        <button type="button" onClick={handlePruneDeleted} disabled={busy}>
          Run task
        </button>
        {activeTask === "prune_deleted" && (
          <p style={statusTextStyle}>Busy, please wait...</p>
        )}
      </section>

      <section style={sectionStyle}>
        <h2>{taskTitles.prune_stale_staging_items}</h2>
        <p>
          Marks staging items as deleted when they were last modified before your
          selected cutoff date.
        </p>
        <div style={inputGroupStyle}>
          <label htmlFor="staging-items-cutoff">Cutoff date</label>
          <input
            id="staging-items-cutoff"
            type="date"
            value={itemsCutoff}
            onChange={(event) => setItemsCutoff(event.target.value)}
            disabled={busy}
          />
        </div>
        <button
          type="button"
          onClick={handlePruneStagingItems}
          disabled={busy || !itemsCutoff}
        >
          Run task
        </button>
        {activeTask === "prune_stale_staging_items" && (
          <p style={statusTextStyle}>Busy, please wait...</p>
        )}
      </section>

      <section style={sectionStyle}>
        <h2>{taskTitles.prune_stale_staging_invoices}</h2>
        <p>
          Marks invoices that have snoozed beyond the cutoff as deleted so they stop
          appearing in staging queues.
        </p>
        <div style={inputGroupStyle}>
          <label htmlFor="staging-invoices-cutoff">Cutoff date</label>
          <input
            id="staging-invoices-cutoff"
            type="date"
            value={invoicesCutoff}
            onChange={(event) => setInvoicesCutoff(event.target.value)}
            disabled={busy}
          />
        </div>
        <button
          type="button"
          onClick={handlePruneStagingInvoices}
          disabled={busy || !invoicesCutoff}
        >
          Run task
        </button>
        {activeTask === "prune_stale_staging_invoices" && (
          <p style={statusTextStyle}>Busy, please wait...</p>
        )}
      </section>

      <section style={sectionStyle}>
        <h2>{taskTitles.prune_images}</h2>
        <p>
          Removes database entries and files for images that are deleted or no longer
          associated with any items using the server's default image directory.
        </p>
        <button
          type="button"
          onClick={handlePruneImages}
          disabled={busy}
        >
          Run task
        </button>
        {activeTask === "prune_images" && (
          <p style={statusTextStyle}>Busy, please wait...</p>
        )}
      </section>

      <section style={sectionStyle}>
        <h2>Task status</h2>
        {busy && <p style={statusTextStyle}>Busy, please wait...</p>}
        {lastTask ? (
          <p>Last task: {taskTitles[lastTask]}</p>
        ) : (
          <p>No tasks have been run yet.</p>
        )}
        {error && (
          <p style={{ color: "#b30000", marginTop: "8px" }} role="alert">
            {error}
          </p>
        )}
        {resultText && (
          <pre style={resultBoxStyle}>{resultText}</pre>
        )}
      </section>
    </div>
  );
};

export default AdminPage;
