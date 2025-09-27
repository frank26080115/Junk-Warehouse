import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import ImageGallery from "../app/components/ImageGallery";
import SearchPanel from "../app/components/SearchPanel";
import { PIN_OPEN_EXPIRY_MS } from "../app/config";

import "../styles/forms.css";

// Types that mirror your table columns + a few virtuals the backend returns (e.g., slug)
export interface ItemDto {
  id?: string; // uuid
  short_id?: number; // integer
  name: string;
  description?: string;
  remarks?: string;
  quantity?: string;
  date_creation?: string | null; // ISO timestamp
  date_last_modified?: string | null; // ISO timestamp
  is_container?: boolean;
  is_collection?: boolean;
  is_large?: boolean;
  is_small?: boolean;
  is_fixed_location?: boolean;
  is_consumable?: boolean;
  metatext?: string;
  is_staging?: boolean;
  is_deleted?: boolean;
  is_lost?: boolean;
  date_reminder?: string | null; // ISO timestamp or null
  pin_as_opened?: string | null; // ISO timestamp when the item was pinned as opened
  product_code?: string;
  url?: string; // purchase url
  date_purchased?: string | null; // ISO timestamp or null
  source?: string;
  // Virtuals from backend (not table columns)
  slug?: string; // used to build the permalink
}

const EMPTY_ITEM: ItemDto = {
  name: "",
  description: "",
  remarks: "",
  quantity: "",
  metatext: "",
  url: "",
  product_code: "",
  source: "",
  is_container: false,
  is_collection: false,
  is_large: false,
  is_small: false,
  is_fixed_location: false,
  is_consumable: false,
  is_staging: true,
  is_deleted: false,
  is_lost: false,
  date_creation: null,
  date_last_modified: null,
  date_purchased: null,
  date_reminder: null,
  pin_as_opened: null,
  slug: "",
};

function isBlank(x?: string | null): boolean {
  return !x || x.trim().length === 0;
}

function fmtYMD(d?: string | null): string {
  if (!d) return "";
  const date = new Date(d);
  if (Number.isNaN(+date)) return "";
  const y = date.getFullYear();
  const m = `${date.getMonth() + 1}`.padStart(2, "0");
  const dd = `${date.getDate()}`.padStart(2, "0");
  return `${y}/${m}/${dd}`;
}

function fmtMDY(d?: string | null): string {
  if (!d) return "";
  const date = new Date(d);
  if (Number.isNaN(+date)) return "";
  const m = `${date.getMonth() + 1}`.padStart(2, "0");
  const dd = `${date.getDate()}`.padStart(2, "0");
  const y = date.getFullYear();
  return `${m}/${dd}/${y}`;
}

function toDateInputValue(d?: string | null): string {
  if (!d) return "";
  const date = new Date(d);
  if (Number.isNaN(+date)) return "";
  const y = date.getFullYear();
  const m = `${date.getMonth() + 1}`.padStart(2, "0");
  const dd = `${date.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${dd}`;
}

function fromDateInputValue(s: string): string | null {
  if (!s) return null;
  const [y, m, d] = s.split("-").map(Number);
  if (!y || !m || !d) return null;
  // Use local midnight; backend can normalize to UTC as needed
  const dt = new Date(y, m - 1, d, 0, 0, 0);
  return dt.toISOString();
}

function truncateWithEllipsis(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  if (maxLength <= 1) {
    return value.slice(0, maxLength);
  }
  return `${value.slice(0, maxLength - 1)}…`;
}

function formatPinTimestamp(value?: string | null): { readable: string; instant: Date | null } {
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


const booleanFlags = [
  { key: "is_container",      emoji: "📦", label: "Container" },
  { key: "is_collection",     emoji: "🗃️", label: "Collection" },
  { key: "is_large",          emoji: "🐘", label: "Large" },
  { key: "is_small",          emoji: "🐜", label: "Small" },
  { key: "is_fixed_location", emoji: "🛏️", label: "Fixed Location" },
  { key: "is_consumable",     emoji: "🍽️", label: "Consumable" },
  { key: "is_lost",           emoji: "👻", label: "Lost" },
  { key: "is_staging",        emoji: "⏳", label: "Staging" },
  { key: "is_deleted",        emoji: "🗑️", label: "Deleted" },
] as const;

type FlagKey = typeof booleanFlags[number]["key"];

const ItemPage: React.FC = () => {
  const { xyz } = useParams(); // id/slug/short-id or "new"
  const navigate = useNavigate();
  const isNewFromUrl = xyz === "new";

  const [item, setItem] = useState<ItemDto>({ ...EMPTY_ITEM, is_staging: isNewFromUrl ? true : EMPTY_ITEM.is_staging });
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>("");
  const [refreshToken, setRefreshToken] = useState<number>(0); // signal panels to refresh

  // Determine initial mode: edit if url is new OR item.is_staging
  const [isReadOnly, setIsReadOnly] = useState<boolean>(() => !isNewFromUrl); // temp until fetch completes

  // Load item when not creating new
  useEffect(() => {
    let ignore = false;
    async function load() {
      if (isNewFromUrl) {
        setItem((prev) => ({ ...EMPTY_ITEM, ...prev, is_staging: true }));
        setIsReadOnly(false);
        return;
      }
      try {
        setLoading(true);
        setError("");
        const res = await fetch("/api/getitem", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ xyz }), // backend can resolve id/slug/short-id
        });
        if (!res.ok) throw new Error(`GET failed: ${res.status}`);
        const data: ItemDto = await res.json();
        if (!ignore) {
          setItem({ ...EMPTY_ITEM, ...data });
          // Start mode according to is_staging
          setIsReadOnly(!(data?.is_staging ?? false));
        }
      } catch (e: any) {
        console.error(e);
        if (!ignore) setError(e?.message || "Failed to load item");
      } finally {
        if (!ignore) setLoading(false);
      }
    }
    load();
    return () => {
      ignore = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [xyz]);

  const permalink = useMemo(() => {
    if (isBlank(item.slug)) return "";
    return `/item/${item.slug}`;
  }, [item.slug]);

  const shortIdHex = useMemo(() => {
    const n = item.short_id ?? 0;
    // Ensure unsigned 32-bit behavior
    const u32 = (n >>> 0).toString(16).padStart(8, "0");
    return `0x${u32}`;
  }, [item.short_id]);

  const handleField = useCallback(<K extends keyof ItemDto>(key: K, value: ItemDto[K]) => {
    setItem((prev) => ({ ...prev, [key]: (value as any) }));
  }, []);

  const handleToggleFlag = useCallback((key: FlagKey) => {
    if (isReadOnly) return;
    setItem((prev) => ({ ...prev, [key]: !prev[key] } as ItemDto));
  }, [isReadOnly]);

  const commonSave = useCallback(async (endpoint: string, options?: { stripIdentifiers?: boolean; itemOverride?: ItemDto }) => {
    const sourceItem = options?.itemOverride ?? item;
    const payload: Partial<ItemDto> = options?.stripIdentifiers
      ? (() => {
          const { id: _omitId, short_id: _omitShortId, ...rest } = sourceItem;
          return rest;
        })()
      : sourceItem;
    // Save/Insert. Backend returns the authoritative updated object
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`${endpoint} failed: ${res.status}`);
    const data: ItemDto = await res.json();
    setItem((prev) => ({ ...prev, ...data }));
    const slug = data.slug;
    if (slug && slug.trim().length > 0 && slug !== xyz) {
      navigate(`/item/${slug}`, { replace: true });
    }
    // Nudge listeners to refresh
    setRefreshToken((x) => x + 1);
  }, [item, navigate, xyz]);

  const doInsert = useCallback(async () => {
    try {
      setLoading(true);
      setError("");
      await commonSave("/api/insertitem", { stripIdentifiers: true });
      setIsReadOnly(true); // after an explicit insert, land in read-only
    } catch (e: any) {
      console.error(e);
      setError(e?.message || "Insert failed");
    } finally {
      setLoading(false);
    }
  }, [commonSave]);

  const doSave = useCallback(async () => {
    try {
      setLoading(true);
      setError("");
      await commonSave("/api/saveitem");
    } catch (e: any) {
      console.error(e);
      setError(e?.message || "Save failed");
    } finally {
      setLoading(false);
    }
  }, [commonSave]);

  const toggleLock = useCallback(async () => {
    if (isReadOnly) {
      // Switch to edit mode
      setIsReadOnly(false);
    } else {
      // Leaving edit mode → save, then become read-only
      await doSave();
      setIsReadOnly(true);
    }
  }, [isReadOnly, doSave]);

  const disabled = isReadOnly;
  const targetUuid = item.id || "new";
  const urlEntries = useMemo(() => {
    const raw = item.url || "";
    return raw
      .split(";")
      .map((part) => part.trim())
      .filter((part) => part.length > 0);
  }, [item.url]);
  const hasExistingId = Boolean(item?.id);
  const insertEmoji = hasExistingId ? "👯" : "➕";
  const insertTitle = `Insert new item (${insertEmoji})`;
  const pinDetails = formatPinTimestamp(item.pin_as_opened);
  const isPinCurrentlyActive = (() => {
    if (!pinDetails.instant) {
      return false;
    }
    const nowMs = Date.now();
    const ageMs = nowMs - pinDetails.instant.getTime();
    return ageMs >= 0 && ageMs <= PIN_OPEN_EXPIRY_MS;
  })();

  const handlePinUpdate = useCallback(
    async (nextValue: string | null) => {
      const proposedItem: ItemDto = { ...item, pin_as_opened: nextValue };
      setItem(proposedItem);
      if (!hasExistingId) {
        return;
      }
      try {
        setLoading(true);
        setError("");
        await commonSave("/api/saveitem", { itemOverride: proposedItem });
      } catch (e: any) {
        console.error(e);
        setError(e?.message || "Failed to update pin state");
      } finally {
        setLoading(false);
      }
    },
    [commonSave, hasExistingId, item],
  );

  return (
    <div className="container-lg" style={{ maxWidth: "960px" }}>
      {/* Floating action icons top-right */}
      <div className="actions-sticky">
        <div className="actions-row">
          <button
            type="button"
            className="btn btn-light border rounded-circle shadow-sm"
            onClick={doInsert}
            aria-label="Insert new item"
            title={insertTitle}
          >
            <span aria-hidden>{insertEmoji}</span>
          </button>
          <button
            type="button"
            className="btn btn-light border rounded-circle shadow-sm"
            onClick={doSave}
            aria-label="Save item"
            title="Save item (💾)"
          >
            <span aria-hidden>💾</span>
          </button>
          <button
            type="button"
            className="btn btn-light border rounded-circle shadow-sm"
            onClick={toggleLock}
            aria-label={isReadOnly ? "Switch to edit mode (unlock)" : "Switch to read-only mode (lock)"}
            title={isReadOnly ? "Unlock for editing (🔓)" : "Lock and save (🔒)"}
          >
            <span aria-hidden>{isReadOnly ? "🔓" : "🔒"}</span>
          </button>
        </div>
      </div>

      <div className="my-4" />

      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}

      {/* NAME + 🔗 */}
      <div className="row align-items-center g-2 mb-3">
        <label className="col-12 col-sm-2 col-lg-2 col-form-label fw-semibold form-label-leftright nowrap">Item&nbsp;Name:</label>
        <div className="col-10 col-sm-8 col-lg-8">
          <input
            type="text"
            className="form-control"
            disabled={disabled}
            value={item.name}
            onChange={(e) => handleField("name", e.target.value)}
          />
        </div>
        <div className="col-2 col-sm-2 col-lg-2 text-end">
          {!isBlank(permalink) && (
            <a
              href={permalink}
              target="_blank"
              rel="noopener noreferrer"
              className="text-decoration-none fs-4"
              aria-label="Open item permalink in new tab"
              title="Open item permalink (🔗)"
            >
              🔗
            </a>
          )}
        </div>
      </div>

      {/* DESCRIPTION (fieldset look) */}
      {(!isReadOnly || !isBlank(item.description)) && (
        <fieldset className="border rounded p-3 mb-3">
          <legend className="float-none w-auto px-2 small text-muted mb-0">Description</legend>
          <textarea
            className="form-control mt-2"
            rows={5}
            disabled={disabled}
            value={item.description || ""}
            onChange={(e) => handleField("description", e.target.value)}
          />
        </fieldset>
      )}

      {/* REMARKS (fieldset look) */}
      {(!isReadOnly || !isBlank(item.remarks)) && (
        <fieldset className="border rounded p-3 mb-3">
          <legend className="float-none w-auto px-2 small text-muted mb-0">Remarks</legend>
          <textarea
            className="form-control mt-2"
            rows={4}
            disabled={disabled}
            value={item.remarks || ""}
            onChange={(e) => handleField("remarks", e.target.value)}
          />
        </fieldset>
      )}

      {/* TABLE-LIKE single-line inputs */}
      {/* quantity */}
      {(!isReadOnly || !isBlank(item.quantity)) && (
        <div className="row g-2 align-items-center mb-2">
          <label className="col-12 col-sm-2 col-lg-2 col-form-label fw-semibold form-label-leftright">Quantity:</label>
          <div className="col-12 col-sm-10 col-lg-10">
            <input
              type="text"
              className="form-control"
              disabled={disabled}
              value={item.quantity || ""}
              onChange={(e) => handleField("quantity", e.target.value)}
            />
          </div>
        </div>
      )}

      {/* metatext */}
      {(!isReadOnly || !isBlank(item.metatext)) && (
        <div className="row g-2 align-items-center mb-2">
          <label className="col-12 col-sm-2 col-lg-2 col-form-label fw-semibold form-label-leftright nowrap">Meta-text:</label>
          <div className="col-12 col-sm-10 col-lg-10">
            <input
              type="text"
              className="form-control"
              disabled={disabled}
              value={item.metatext || ""}
              onChange={(e) => handleField("metatext", e.target.value)}
            />
          </div>
        </div>
      )}

      {/* url (clickable in read-only) */}
      {(!isReadOnly || !isBlank(item.url)) && (
        <div className="row g-2 align-items-center mb-2">
          <label className="col-12 col-sm-2 col-lg-2 col-form-label fw-semibold form-label-leftright">🛒-URL:</label>
          <div className="col-12 col-sm-10 col-lg-10">
            {isReadOnly ? (
              urlEntries.length === 0 ? null : (
                <div className="form-control-plaintext p-0">
                  <div className="d-flex flex-wrap gap-2">
                    {urlEntries.map((urlValue, index) => {
                      const displayText =
                        urlEntries.length === 1
                          ? truncateWithEllipsis(urlValue, 30)
                          : "🔗";
                      const classNameBase = "text-decoration-none";
                      const className =
                        urlEntries.length === 1
                          ? `${classNameBase} text-truncate d-inline-block`
                          : classNameBase;
                      const style =
                        urlEntries.length === 1 ? { maxWidth: "100%" } : undefined;
                      const ariaLabel =
                        urlEntries.length === 1
                          ? undefined
                          : `Open URL ${index + 1}: ${urlValue}`;
                      return (
                        <a
                          key={`${urlValue}-${index}`}
                          href={urlValue}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={className}
                          style={style}
                          title={urlValue}
                          aria-label={ariaLabel}
                        >
                          {displayText}
                        </a>
                      );
                    })}
                  </div>
                </div>
              )
            ) : (
              <input
                type="text"
                className="form-control"
                disabled={disabled}
                value={item.url || ""}
                onChange={(e) => handleField("url", e.target.value)}
              />
            )}
          </div>
        </div>
      )}

      {/* product_code */}
      {(!isReadOnly || !isBlank(item.product_code)) && (
        <div className="row g-2 align-items-center mb-2">
          <label className="col-12 col-sm-2 col-lg-2 col-form-label fw-semibold form-label-leftright">🛒-PN/SKU:</label>
          <div className="col-12 col-sm-10 col-lg-10">
            <input
              type="text"
              className="form-control"
              disabled={disabled}
              value={item.product_code || ""}
              onChange={(e) => handleField("product_code", e.target.value)}
            />
          </div>
        </div>
      )}

      {/* source */}
      {(!isReadOnly || !isBlank(item.source)) && (
        <div className="row g-2 align-items-center mb-3">
          <label className="col-12 col-sm-2 col-lg-2 col-form-label fw-semibold form-label-leftright">🛒-source:</label>
          <div className="col-12 col-sm-10 col-lg-10">
            <input
              type="text"
              className="form-control"
              disabled={disabled}
              value={item.source || ""}
              onChange={(e) => handleField("source", e.target.value)}
            />
          </div>
        </div>
      )}

      <div className="row g-3 mb-3">
        {/* date_creation */}
        <div className="col-12 col-sm-6 d-flex align-items-center gap-2">
          <span className="text-muted nowrap">📅🌱:</span>
          <span className="fw-semibold">{fmtMDY(item.date_creation)}</span>
        </div>

        {/* date_last_modified */}
        <div className="col-12 col-sm-6 d-flex align-items-center gap-2">
          <span className="text-muted nowrap">📅🛠️:</span>
          <span className="fw-semibold">{fmtMDY(item.date_last_modified)}</span>
        </div>

        {/* date_purchased */}
        <div className="col-12 col-sm-6 d-flex align-items-center gap-2">
          <span className="text-muted nowrap">📅🛒️:</span>
          <input
            type="date"
            className="form-control"
            disabled={isReadOnly}
            style={{ maxWidth: 220 }}
            value={toDateInputValue(item.date_purchased)}
            onChange={(e) => handleField("date_purchased", fromDateInputValue(e.target.value))}
          />
        </div>

        {/* date_reminder */}
        <div className="col-12 col-sm-6 d-flex align-items-center gap-2">
          <span className="text-muted nowrap">📅⏰️:</span>
          <input
            type="date"
            className="form-control"
            disabled={isReadOnly}
            style={{ maxWidth: 220 }}
            value={toDateInputValue(item.date_reminder)}
            onChange={(e) => handleField("date_reminder", fromDateInputValue(e.target.value))}
          />
        </div>
      </div>

      {/* Boolean emoji toggles */}
      <div className="d-flex flex-wrap align-items-center gap-3 mb-4">
        {booleanFlags.map(({ key, emoji, label }) => {
          const on = Boolean(item[key]);
          return (
            <button
              key={key}
              type="button"
              className="btn btn-link p-0 border-0 text-decoration-none"
              onClick={() => handleToggleFlag(key)}
              aria-pressed={on}
              aria-label={label}
              title={label}
              style={{ opacity: on ? 1 : 0.25, fontSize: "1.5rem" }}
            >
              <span aria-hidden>{emoji}</span>
            </button>
          );
        })}
      </div>

      {/* Photos */}
      <div className="mb-4">
        <div className="d-flex align-items-center justify-content-between mb-2">
          <h2 className="h5 mb-0">Photos</h2>
        </div>
        <ImageGallery targetUuid={targetUuid} refreshToken={refreshToken} />
      </div>

      {/* Pin management controls appear before related search panels so they are easy to find */}
      <div className="mb-4">
        <div className="d-flex flex-column flex-md-row align-items-start align-items-md-center gap-3">
          {pinDetails.instant && (
            <div className="fw-semibold">
              {/* Only display the opened timestamp when a pin exists so operators do not see placeholder text */}
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
                disabled={loading || !hasExistingId}
                title={hasExistingId ? "Mark this item as opened" : "Save the item before pinning"}
              >
                📌 Pin as Opened
              </button>
            )}
            {isPinCurrentlyActive && (
              <>
                <button
                  type="button"
                  className="btn btn-outline-danger"
                  onClick={() => handlePinUpdate(null)}
                  disabled={loading || !hasExistingId}
                  title={hasExistingId ? "Clear the opened marker" : "Save the item before clearing"}
                >
                  ❌📌 Close Pinned
                </button>
                <button
                  type="button"
                  className="btn btn-outline-secondary"
                  onClick={() => handlePinUpdate(new Date().toISOString())}
                  disabled={loading || !hasExistingId}
                  title={hasExistingId ? "Refresh the opened timestamp" : "Save the item before pinning"}
                >
                  📌➕🕒 Re-pin as Opened
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Relationships/Links */}
      <div className="mb-4">
        <div className="d-flex align-items-center justify-content-between mb-2">
          <h2 className="h5 mb-0">Relationships/Links</h2>
        </div>
        <SearchPanel targetUuid={targetUuid} refreshToken={refreshToken} tableName="items" />
      </div>

      {/* Relevant Invoices */}
      <div className="mb-4">
        <div className="d-flex align-items-center justify-content-between mb-2">
          <h2 className="h5 mb-0">Relevant Invoices</h2>
        </div>
        <SearchPanel targetUuid={targetUuid} refreshToken={refreshToken} tableName="invoices" />
      </div>

      {/* Footer meta */}
      <div className="text-muted small">
        <div className="mb-1">UUID: <code>{item.id || "(not yet assigned)"}</code></div>
        <div>short-id: <code>{shortIdHex}</code></div>
      </div>

      {loading && (
        <div className="position-fixed bottom-0 end-0 m-3 alert alert-secondary py-2 px-3 shadow">
          Working…
        </div>
      )}
    </div>
  );
};

export default ItemPage;
