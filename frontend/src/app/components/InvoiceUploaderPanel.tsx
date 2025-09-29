import React, { useEffect, useRef, useState } from "react";

// JobStatus represents the limited set of progress values that the background job endpoint can emit.
type JobStatus = "queued" | "busy" | "done" | "error";

interface InvoiceUploaderPanelProps {
  // Allow a parent page to react when the component suggests a helpful follow-up search query.
  onSearchPrefillSuggested?: (query: string) => void;
  // Let the parent hide the email checker when an automated background job takes over.
  showCheckEmailPanel?: boolean;
}

const InvoiceUploaderPanel: React.FC<InvoiceUploaderPanelProps> = ({
  onSearchPrefillSuggested,
  showCheckEmailPanel = true,
}) => {
  // Track busy states, job identifiers, and status details for both the email check and direct upload flows.
  const [checkEmailBusy, setCheckEmailBusy] = useState(false);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [checkEmailJobId, setCheckEmailJobId] = useState<string | null>(null);
  const [checkEmailStatus, setCheckEmailStatus] = useState<JobStatus | null>(null);
  const [uploadJobId, setUploadJobId] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<JobStatus | null>(null);
  const [modalMessage, setModalMessage] = useState<
    | {
        title: string;
        body: string;
      }
    | null
  >(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    // Maintain a defensive flag so asynchronous work does not update state after unmount.
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const showModal = (title: string, body: string) => {
    setModalMessage({ title, body });
  };

  const pollJobUntilComplete = async (
    jobId: string,
    onStatusUpdate: (status: JobStatus) => void,
  ): Promise<any> => {
    // Poll the backend job endpoint with a modest backoff so the user sees frequent updates without flooding the server.
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

  const handleCheckEmail = async () => {
    // Kick off the backend email ingestion job so newly arrived invoices land in the ledger.
    if (checkEmailBusy) {
      return;
    }
    setCheckEmailBusy(true);
    setCheckEmailJobId(null);
    setCheckEmailStatus(null);
    try {
      const response = await fetch("/api/checkemail", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
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
          "Failed to contact email checker.";
        throw new Error(message);
      }
      const jobId: string | null =
        (payload && (payload.job_id || payload.jobId)) || null;
      if (!jobId || typeof jobId !== "string") {
        throw new Error("Job identifier was not provided.");
      }
      setCheckEmailJobId(jobId);
      setCheckEmailStatus("queued");
      const result = await pollJobUntilComplete(jobId, (status) => {
        if (isMountedRef.current) {
          setCheckEmailStatus(status);
        }
      });
      if (!isMountedRef.current) {
        return;
      }
      onSearchPrefillSuggested?.("* ?!has_been_processed \bydate \orderrev");
      const message =
        (result && (result.message || result.detail)) ||
        "Email check completed successfully.";
      showModal("Email check complete", message);
    } catch (error: any) {
      if (!isMountedRef.current) {
        return;
      }
      const message = error?.message || "Email check failed.";
      showModal("Email check failed", message);
    } finally {
      if (isMountedRef.current) {
        setCheckEmailBusy(false);
        setCheckEmailJobId(null);
        setCheckEmailStatus(null);
      }
    }
  };

  const handleFileSelection = (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    // Mirror the file picker into state so we can show button enablement and hand the files to FormData.
    const files = event.target.files;
    if (!files) {
      setSelectedFiles([]);
      return;
    }
    setSelectedFiles(Array.from(files));
  };

  const handleUpload = async () => {
    // Submit the selected email archives to the upload endpoint and surface progress information.
    if (!selectedFiles.length || uploadBusy) {
      return;
    }
    setUploadBusy(true);
    setUploadJobId(null);
    setUploadStatus(null);
    try {
      const formData = new FormData();
      selectedFiles.forEach((file) => {
        formData.append("files", file);
      });
      const response = await fetch("/api/invoiceupload", {
        method: "POST",
        body: formData,
      });
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
          "Invoice upload failed.";
        throw new Error(message);
      }
      const jobId: string | null =
        (payload && (payload.job_id || payload.jobId)) || null;
      if (!jobId || typeof jobId !== "string") {
        throw new Error("Job identifier was not provided.");
      }
      setUploadJobId(jobId);
      setUploadStatus("queued");
      const result = await pollJobUntilComplete(jobId, (status) => {
        if (isMountedRef.current) {
          setUploadStatus(status);
        }
      });
      if (!isMountedRef.current) {
        return;
      }
      onSearchPrefillSuggested?.("* ?!has_been_processed \bydate \orderrev");
      const message =
        (result && (result.message || result.detail)) ||
        "Invoices uploaded successfully.";
      showModal("Invoice upload complete", message);
      setSelectedFiles([]);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (error: any) {
      if (!isMountedRef.current) {
        return;
      }
      const message = error?.message || "Invoice upload failed.";
      showModal("Invoice upload failed", message);
    } finally {
      if (isMountedRef.current) {
        setUploadBusy(false);
        setUploadJobId(null);
        setUploadStatus(null);
      }
    }
  };

  const renderBusyIndicator = (message: string) => (
    // Provide a consistent inline spinner message pairing for the ongoing background jobs.
    <div className="d-flex align-items-center gap-2 mt-2" role="status">
      <div className="spinner-border spinner-border-sm" aria-hidden="true" />
      <span>{message}</span>
    </div>
  );

  return (
    <div className="mt-5">
      {showCheckEmailPanel && (
        <>
          <h2 className="h5">Check email for invoices</h2>
          <p className="text-muted">
            Trigger the email processor to pull the latest invoices from the mailbox.
          </p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleCheckEmail}
            disabled={checkEmailBusy}
          >
            {checkEmailBusy ? "Checking…" : "Check email"}
          </button>
          {checkEmailBusy &&
            renderBusyIndicator(
              checkEmailStatus === "queued"
                ? `Job queued${checkEmailJobId ? ` (${checkEmailJobId})` : ""}…`
                : `Checking mailbox for invoices${checkEmailJobId ? ` (${checkEmailJobId})` : ""}…`
            )}
        </>
      )}

      <div className={showCheckEmailPanel ? "mt-4" : ""}>
        <h2 className="h5">Upload invoice files</h2>
        <p className="text-muted">
          Upload archived email files (.mht, .mhtml, .htm, .html) to import invoices directly.
        </p>
        <div className="d-flex flex-column flex-sm-row align-items-start gap-2">
          <input
            ref={fileInputRef}
            type="file"
            className="form-control"
            accept=".mht,.mhtm,.mhtml,.htm,.html"
            multiple
            onChange={handleFileSelection}
            disabled={uploadBusy}
          />
          <button
            type="button"
            className="btn btn-outline-secondary"
            onClick={handleUpload}
            disabled={uploadBusy || !selectedFiles.length}
          >
            {uploadBusy ? "Uploading…" : "Upload"}
          </button>
        </div>
        {uploadBusy &&
          renderBusyIndicator(
            uploadStatus === "queued"
              ? `Job queued${uploadJobId ? ` (${uploadJobId})` : ""}…`
              : `Uploading invoices${uploadJobId ? ` (${uploadJobId})` : ""}…`
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

export default InvoiceUploaderPanel;
