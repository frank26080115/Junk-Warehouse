import React, { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import SearchPanel from "../app/components/SearchPanel";

const LedgerSearchPage: React.FC = () => {
  const { xyz } = useParams<{ xyz?: string }>();
  const prefilled = useMemo(() => (xyz ? decodeURIComponent(xyz) : ""), [xyz]);
  const [searchPrefill, setSearchPrefill] = useState(prefilled);
  const [checkEmailBusy, setCheckEmailBusy] = useState(false);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [modalMessage, setModalMessage] = useState<
    | {
        title: string;
        body: string;
      }
    | null
  >(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setSearchPrefill(prefilled);
  }, [prefilled]);

  const showModal = (title: string, body: string) => {
    setModalMessage({ title, body });
  };

  const handleCheckEmail = async () => {
    if (checkEmailBusy) {
      return;
    }
    setCheckEmailBusy(true);
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
      setSearchPrefill("* ?!has_been_processed \\bydate \\orderrev");
      const message =
        (payload && (payload.message || payload.detail)) ||
        "Email check completed successfully.";
      showModal("Email check complete", message);
    } catch (error: any) {
      const message = error?.message || "Email check failed.";
      showModal("Email check failed", message);
    } finally {
      setCheckEmailBusy(false);
    }
  };

  const handleFileSelection = (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const files = event.target.files;
    if (!files) {
      setSelectedFiles([]);
      return;
    }
    setSelectedFiles(Array.from(files));
  };

  const handleUpload = async () => {
    if (!selectedFiles.length || uploadBusy) {
      return;
    }
    setUploadBusy(true);
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
      setSearchPrefill("* ?!has_been_processed \\bydate \\orderrev");
      const message =
        (payload && (payload.message || payload.detail)) ||
        "Invoices uploaded successfully.";
      showModal("Invoice upload complete", message);
      setSelectedFiles([]);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (error: any) {
      const message = error?.message || "Invoice upload failed.";
      showModal("Invoice upload failed", message);
    } finally {
      setUploadBusy(false);
    }
  };

  const renderBusyIndicator = (message: string) => (
    <div className="d-flex align-items-center gap-2 mt-2" role="status">
      <div className="spinner-border spinner-border-sm" aria-hidden="true" />
      <span>{message}</span>
    </div>
  );

  return (
    <div className="container-lg py-4" style={{ maxWidth: "960px" }}>
      <h1 className="h3 mb-4">Search Invoices</h1>
      <SearchPanel
        displayedTitle="Invoices"
        prefilledQuery={searchPrefill}
        tableName="invoices"
        allowDelete
      />

      <div className="mt-5">
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
        {checkEmailBusy && renderBusyIndicator("Checking mailbox for invoices…")}
      </div>

      <div className="mt-4">
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
        {uploadBusy && renderBusyIndicator("Uploading invoices…")}
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

export default LedgerSearchPage;
