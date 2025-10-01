import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

interface GalleryImage {
  uuid: string;
  src: string;
  rank?: number;
}

interface ImageGalleryProps {
  targetUuid: string;
  refreshToken?: number;
}

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  backgroundColor: "rgba(0, 0, 0, 0.4)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 1000,
};

const modalStyle: React.CSSProperties = {
  backgroundColor: "#fff",
  borderRadius: "8px",
  padding: "1.5rem",
  width: "min(90vw, 420px)",
  boxShadow: "0 12px 32px rgba(0,0,0,0.25)",
};

const ImageGallery: React.FC<ImageGalleryProps> = ({ targetUuid, refreshToken }) => {
  const [images, setImages] = useState<GalleryImage[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showErrorModal, setShowErrorModal] = useState(false);
  const [urlInput, setUrlInput] = useState("");

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const uploadTileRef = useRef<HTMLDivElement | null>(null);

  const openErrorModal = useCallback((message: string) => {
    setErrorMessage(message);
    setShowErrorModal(true);
  }, []);

  const closeErrorModal = useCallback(() => {
    setShowErrorModal(false);
  }, []);

  const fetchImages = useCallback(async () => {
    if (!targetUuid) {
      setImages([]);
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(`/api/getimagesfor?item_id=${encodeURIComponent(targetUuid)}`);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload?.error ?? "Failed to load images");
      }

      const payload = await response.json();
      const list: unknown = payload?.images ?? payload;
      if (Array.isArray(list)) {
        setImages(list as GalleryImage[]);
      } else {
        setImages([]);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unexpected error while loading images";
      openErrorModal(message);
    } finally {
      setLoading(false);
    }
  }, [targetUuid, openErrorModal]);

  useEffect(() => {
    fetchImages();
  }, [fetchImages, refreshToken]);

  const submitFormData = useCallback(async (formData: FormData) => {
    if (!targetUuid) {
      return;
    }

    setUploading(true);
    try {
      const response = await fetch("/api/img_upload", {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload?.error ?? "Failed to upload image");
      }
      await fetchImages();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unexpected error while uploading";
      openErrorModal(message);
    } finally {
      setUploading(false);
    }
  }, [fetchImages, openErrorModal, targetUuid]);

  const handleDelete = useCallback(async (imageUuid: string) => {
    if (!targetUuid || uploading) {
      return;
    }

    setUploading(true);
    try {
      const response = await fetch("/api/deleteimagefor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_id: targetUuid, img_id: imageUuid }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload?.error ?? "Failed to delete image");
      }
      await fetchImages();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unexpected error while deleting";
      openErrorModal(message);
    } finally {
      setUploading(false);
    }
  }, [fetchImages, openErrorModal, targetUuid, uploading]);

  const handleSetMain = useCallback(async (imageUuid: string) => {
    if (!targetUuid || uploading) {
      return;
    }

    setUploading(true);
    try {
      const response = await fetch("/api/setmainimagesfor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_id: targetUuid, img_id: imageUuid }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload?.error ?? "Failed to update main image");
      }
      await fetchImages();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unexpected error while updating main image";
      openErrorModal(message);
    } finally {
      setUploading(false);
    }
  }, [fetchImages, openErrorModal, targetUuid, uploading]);

  const handleFileInput = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !targetUuid) {
      return;
    }

    const formData = new FormData();
    formData.append("item_id", targetUuid);
    formData.append("img_file", file);
    await submitFormData(formData);
  }, [submitFormData, targetUuid]);

  const handleUrlSubmit = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!urlInput.trim() || !targetUuid) {
      return;
    }

    const formData = new FormData();
    formData.append("item_id", targetUuid);
    formData.append("img_url", urlInput.trim());
    await submitFormData(formData);
    setUrlInput("");
  }, [submitFormData, targetUuid, urlInput]);

  const handlePaste = useCallback(async (event: React.ClipboardEvent<HTMLDivElement>) => {
    if (!targetUuid) {
      return;
    }

    const files = Array.from(event.clipboardData?.files ?? []);
    const imageFile = files.find((file) => file.type.startsWith("image/"));
    if (!imageFile) {
      return;
    }

    event.preventDefault();
    const clipboardFile = new File([imageFile], "", { type: imageFile.type || "image/png" });
    const formData = new FormData();
    formData.append("item_id", targetUuid);
    formData.append("img_clipboard", "1");
    formData.append("img_file", clipboardFile, "");
    await submitFormData(formData);
  }, [submitFormData, targetUuid]);

  const mainImage = useMemo(() => images[0] ?? null, [images]);
  const otherImages = useMemo(() => (images.length > 1 ? images.slice(1) : []), [images]);
  // Determine when the upload tile stands alone so we can center it on the grid for clarity.
  const isUploadTileSolo = useMemo(() => otherImages.length === 0, [otherImages]);

  const renderErrorModal = () => {
    if (!showErrorModal || !errorMessage) {
      return null;
    }

    return (
      <div style={overlayStyle} role="dialog" aria-modal="true" aria-labelledby="image-gallery-error-title">
        <div style={modalStyle}>
          <h2 id="image-gallery-error-title" className="mb-3">Something went wrong</h2>
          <p className="mb-4">{errorMessage}</p>
          <div className="text-end">
            <button type="button" className="btn btn-primary" onClick={closeErrorModal}>
              Close
            </button>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="image-gallery-wrapper position-relative">
      {renderErrorModal()}
      <div className="mb-4" style={{ maxWidth: "600px", margin: "0 auto", textAlign: "center" }}>
        {mainImage ? (
          <div>
            <a href={mainImage.src} target="_blank" rel="noopener noreferrer">
              <img
                src={mainImage.src}
                alt="Main item"
                style={{ maxWidth: "100%", height: "auto", borderRadius: "8px" }}
              />
            </a>
            <div className="mt-2">
              <button
                type="button"
                className="btn btn-outline-danger"
                onClick={() => handleDelete(mainImage.uuid)}
                disabled={uploading}
                title="Delete main image"
              >
                🗑️
              </button>
            </div>
          </div>
        ) : (
          <div className="border rounded py-5 bg-light">No main image available</div>
        )}
      </div>

      <div
        className="image-grid"
        style={{
          display: "grid",
          gap: "1rem",
          gridTemplateColumns: isUploadTileSolo
            ? "minmax(220px, 360px)"
            : "repeat(auto-fill, minmax(200px, 1fr))",
          justifyContent: isUploadTileSolo ? "center" : "stretch",
        }}
      >
        {otherImages.map((image) => (
          <div key={image.uuid} className="image-tile border rounded p-2 bg-white text-center">
            <a href={image.src} target="_blank" rel="noopener noreferrer">
              <img
                src={image.src}
                alt="Item"
                style={{ maxWidth: "100%", maxHeight: "200px", height: "auto", borderRadius: "4px" }}
              />
            </a>
            <div className="d-flex justify-content-center gap-2 mt-2">
              <button
                type="button"
                className="btn btn-outline-danger btn-sm"
                onClick={() => handleDelete(image.uuid)}
                disabled={uploading}
                title="Delete image"
              >
                🗑️
              </button>
              <button
                type="button"
                className="btn btn-outline-primary btn-sm"
                onClick={() => handleSetMain(image.uuid)}
                disabled={uploading}
                title="Make main image"
              >
                ✨
              </button>
            </div>
          </div>
        ))}

        <div
          ref={uploadTileRef}
          className="upload-tile border border-2 border-secondary rounded d-flex flex-column align-items-stretch justify-content-between p-3"
          tabIndex={0}
          onPaste={handlePaste}
          style={{
            backgroundColor: "#fafafa",
            justifySelf: isUploadTileSolo ? "center" : "stretch",
            minHeight: "220px",
            width: isUploadTileSolo ? "100%" : "auto",
          }}
        >
          <div className="mb-3 text-center fw-semibold">➕📸Upload Photo</div>
          <div className="d-grid gap-2 mb-3">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
            >
              🖇️From File
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              hidden
              onChange={handleFileInput}
            />
            <form onSubmit={handleUrlSubmit} className="d-flex gap-2">
              <input
                type="url"
                className="form-control"
                placeholder="https://example.com/image.jpg"
                value={urlInput}
                onChange={(event) => setUrlInput(event.target.value)}
                disabled={uploading}
              />
              <button type="submit" className="btn btn-primary" disabled={uploading || !urlInput.trim()}>🔗URL</button>
            </form>
          </div>
          <small className="text-muted text-center">📋 (Ctrl+V)</small>
        </div>
      </div>

      {loading && (
        <div className="text-center mt-3 text-muted">⏳📸…</div>
      )}
    </div>
  );
};

export default ImageGallery;
