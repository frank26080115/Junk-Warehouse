import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ALL_ASSOCIATION_BITS,
  ALL_ASSOCIATION_MASK,
  CONTAINMENT_BIT,
  MERGE_BIT,
  RELATED_BIT,
  SIMILAR_BIT,
  bit_to_emoji_character,
  bit_to_word,
  collect_emoji_characters_from_int,
  collect_words_from_int,
  int_has_containment,
  int_has_merge,
  int_has_related,
  int_has_similar,
} from "../helpers/assocHelper";
import { PIN_OPEN_EXPIRY_MS } from "../config";

type TableName = "items" | "invoices";
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
  targetSlug?: string | null;
  refreshToken?: number;
  tableName?: TableName;
  smallMode?: boolean;
  allowDelete?: boolean;
  onRelationshipsChanged?: () => void;
}

const API_ENDPOINTS: Record<
  TableName,
  { search: string; delete: string; relate: string }
> = {
  items: {
    search: "/api/search",
    delete: "/api/bulkdelete",
    relate: "/api/bulkassoc",
  },
  invoices: {
    search: "/api/searchinvoices",
    delete: "/api/invoicesbulkdelete",
    relate: "/api/invoicesassociations",
  },
};

const MOVE_ITEM_ENDPOINT = "/api/moveitem";

const ITEM_NAME_MAX_LENGTH = 30;
const INVOICE_LINE_MAX_LENGTH = 40;
/**
 * Convert an unknown value into a usable Date instance so that we can evaluate pin freshness.
 */
function coerceToDate(value: unknown): Date | null {
  if (value == null) {
    return null;
  }
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    const fromNumber = new Date(value);
    return Number.isNaN(fromNumber.getTime()) ? null : fromNumber;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) {
      return null;
    }
    const fromString = new Date(trimmed);
    return Number.isNaN(fromString.getTime()) ? null : fromString;
  }
  return null;
}

/**
 * Determine whether the provided pin_as_opened timestamp is still within the active pin window defined in configuration.
 */
function isPinOpenedRecently(value: unknown): boolean {
  const pinDate = coerceToDate(value);
  if (!pinDate) {
    return false;
  }
  const nowMs = Date.now();
  const pinMs = pinDate.getTime();
  if (!Number.isFinite(pinMs)) {
    return false;
  }
  const difference = nowMs - pinMs;
  if (difference < 0) {
    // Treat future timestamps as opened because they are certainly recent and should be highlighted.
    return true;
  }
  return difference <= PIN_OPEN_EXPIRY_MS;
}

function isBlank(value?: string | null): boolean {
  return !value || value.trim().length === 0;
}

function truncateText(value: string, max = 72): string {
  if (value.length <= max) return value;
  if (max <= 1) return value.slice(0, max);
  return `${value.slice(0, max - 1)}…`;
}

function formatDateToYMD(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }

  const isoMatch = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (isoMatch) {
    return `${isoMatch[1]}-${isoMatch[2]}-${isoMatch[3]}`;
  }

  const parsed = new Date(trimmed);
  if (!Number.isNaN(parsed.getTime())) {
    const year = parsed.getFullYear();
    const month = String(parsed.getMonth() + 1).padStart(2, "0");
    const day = String(parsed.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  return trimmed;
}

function extractInvoiceLines(row: SearchRow): string[] {
  const normalize = (value: unknown): string => {
    if (value == null) {
      return "";
    }
    if (typeof value === "string") {
      return value.trim();
    }
    return String(value).trim();
  };

  const lines: string[] = [];

  const dateRaw = normalize((row as any).date);
  let formattedDate = formatDateToYMD(dateRaw);
  const hasBeenProcessed = (row as any).has_been_processed;
  if (isBlank(formattedDate)) {
    formattedDate = "(no date)";
  }
  let line = `📅 ${formattedDate}`;
  if (hasBeenProcessed === false) {
    line += `⏳`;
  }
  // TODO: const hasAutoSummary = ???????
  //if (hasAutoSummary) {
  //  line += `🪄`;
  //}
  lines.push(line);

  const shopRaw = normalize((row as any).shop_name);
  const orderRaw = normalize((row as any).order_number);
  const shopLine = !isBlank(shopRaw) ? `🛒 ${shopRaw}` : "";
  const orderLine = !isBlank(orderRaw) ? `🔢 ${orderRaw}` : "";

  if (!isBlank(shopRaw) && !isBlank(orderRaw)) {
    if (shopRaw.length + orderRaw.length <= INVOICE_LINE_MAX_LENGTH) {
      lines.push(`${shopLine} ${orderLine}`.trim());
    } else {
      lines.push(shopLine);
      lines.push(orderLine);
    }
  } else {
    if (shopLine) {
      lines.push(shopLine);
    }
    if (orderLine) {
      lines.push(orderLine);
    }
  }

  const subjectRaw = normalize((row as any).subject);
  if (!isBlank(subjectRaw)) {
    lines.push(`✉ ${truncateText(subjectRaw, INVOICE_LINE_MAX_LENGTH)}`);
  }

  if (lines.length === 0) {
    lines.push(String(row.pk));
  }

  return lines;
}
const SearchPanel: React.FC<SearchPanelProps> = ({
  displayedTitle,
  prefilledQuery,
  hideTextBox = false,
  targetUuid,
  targetSlug,
  refreshToken,
  tableName = "items",
  smallMode = false,
  allowDelete = false,
  onRelationshipsChanged,
}) => {
  const normalizedTable: TableName = tableName ?? "items";
  const isInvoiceMode = normalizedTable === "invoices";
  const [query, setQuery] = useState<string>(prefilledQuery ?? "");
  const [rawResults, setRawResults] = useState<SearchRow[]>([]);
  const [errorMessage, setErrorMessage] = useState<string>("");
  const [isSearching, setIsSearching] = useState<boolean>(false);
  const [isActionBusy, setIsActionBusy] = useState<boolean>(false);
  const [hasQueried, setHasQueried] = useState<boolean>(false);
  const [selectedPks, setSelectedPks] = useState<Set<string>>(new Set());
  const [associationBits, setAssociationBits] = useState<number>(
    CONTAINMENT_BIT,
  );
  const associationSummary = useMemo(() => {
    if (isInvoiceMode) {
      return "link";
    }
    const words = collect_words_from_int(associationBits);
    return words.length ? words.join(", ") : "unlink";
  }, [associationBits, isInvoiceMode]);
  const [modalMessage, setModalMessage] = useState<
    | {
        title: string;
        body: string;
      }
    | null
  >(null);

  // When the table switches between items and invoices, adjust the association bits accordingly so the footer buttons behave predictably.
  useEffect(() => {
    setAssociationBits((previous) => {
      if (isInvoiceMode) {
        return RELATED_BIT;
      }
      const sanitized = Number.isFinite(previous)
        ? previous & ALL_ASSOCIATION_MASK
        : 0;
      return sanitized === 0 ? CONTAINMENT_BIT : sanitized;
    });
  }, [isInvoiceMode]);

  const abortRef = useRef<AbortController | null>(null);
  const pinnedRef = useRef<Map<string, SearchRow>>(new Map());
  const lastQueryRef = useRef<string>(prefilledQuery ?? "");
  const refreshTokenRef = useRef<number | undefined>(undefined);
  const lastPrefilledRef = useRef<string | null>(null);
  const lastTargetRef = useRef<string | null | undefined>(undefined);

  const isBusy = isSearching || isActionBusy;
  const selectedCount = selectedPks.size;
  // Convert the set of selected identifiers into a simple array so downstream helpers can reference a stable ordering.
  const selectedPkArray = useMemo(() => Array.from(selectedPks), [selectedPks]);
  const normalizedTargetSlug = useMemo(() => {
    if (!targetSlug) {
      return "";
    }
    // Normalizing trims whitespace so the footer text renders cleanly and avoids awkward spacing.
    return targetSlug.trim();
  }, [targetSlug]);

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

  const singleSelectedRow = useMemo(() => {
    if (selectedPkArray.length !== 1) {
      return null;
    }
    const pk = selectedPkArray[0];
    if (!pk) {
      return null;
    }
    let row = displayRows.find((entry) => entry.pk === pk) || null;
    if (!row) {
      row = pinnedRef.current.get(pk) || null;
    }
    if (!row) {
      return null;
    }
    const slugCandidate = typeof row.slug === "string" ? row.slug.trim() : "";
    const fallbackSlug = slugCandidate || (typeof row.pk === "string" ? row.pk.trim() : "");
    return { pk, row, slug: fallbackSlug };
  }, [displayRows, selectedPkArray]);

  const targetDisplaySlug = useMemo(() => {
    if (!targetUuid) {
      return "";
    }
    if (normalizedTargetSlug) {
      return normalizedTargetSlug;
    }
    return targetUuid;
  }, [normalizedTargetSlug, targetUuid]);

  const selectedDisplaySlug = singleSelectedRow?.slug || singleSelectedRow?.pk || "";

  /**
   * Render slug-like identifiers with an inline "code" presentation so that users can immediately recognize
   * the exact identifier the move helpers will operate on.
   */
  const renderSlugBadge = useCallback(
    (value: string | number | null | undefined) => {
      const resolved = value == null ? "" : String(value);
      return (
        <code
          className="text-danger border border-danger-subtle bg-transparent rounded px-1 fw-semibold"
          style={{
            backgroundColor: "rgba(220, 53, 69, 0.08)",
            display: "inline-block",
            letterSpacing: "0.01em",
          }}
        >
          {resolved}
        </code>
      );
    },
    []
  );

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

  const handleAssociationBitToggle = useCallback(
    (bit: number, nextChecked: boolean) => {
      setAssociationBits((previous) => {
        const baseline = Number.isFinite(previous) ? previous : 0;
        if (nextChecked) {
          return (baseline | bit) & ALL_ASSOCIATION_MASK;
        }
        return baseline & ~bit;
      });
    },
    [],
  );

  const formatAssociationEmojis = useCallback((value: number): string => {
    if (!Number.isFinite(value)) {
      return "";
    }
    if (value < 0) {
      // Negative association values represent unknown states, so do not render an icon.
      return "";
    }
    const normalized = value & ALL_ASSOCIATION_MASK;
    const icons = collect_emoji_characters_from_int(normalized);
    if (icons.length > 0) {
      return icons.join("");
    }
    // Show a question mark when the association value is explicitly zero or unrecognized.
    return "❓";
  }, []);

  const containmentChecked = int_has_containment(associationBits);
  const relatedChecked = int_has_related(associationBits);
  const similarChecked = int_has_similar(associationBits);
  const mergeChecked = int_has_merge(associationBits);

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
      if (normalizedTable !== "items") {
        // When the search panel is not focused on items, avoid generating misleading links.
        return "#";
      }

      // Always direct users to the canonical internal item route rather than trusting arbitrary URLs from the data row.
      const slugCandidate = typeof row.slug === "string" ? row.slug.trim() : "";
      if (!isBlank(slugCandidate)) {
        return `/item/${slugCandidate}`;
      }

      const pkCandidate = typeof row.pk === "string" ? row.pk.trim() : "";
      if (!isBlank(pkCandidate)) {
        return `/item/${pkCandidate}`;
      }

      // If neither slug nor primary key information is present, fall back to a safe placeholder anchor.
      return "#";
    },
    [normalizedTable],
  );

  const buildInvoiceHref = useCallback((row: SearchRow): string => {
    const directHref = (row as any).href;
    if (typeof directHref === "string" && !isBlank(directHref)) {
      return directHref;
    }
    const url = (row as any).url;
    if (typeof url === "string" && !isBlank(url)) {
      return url;
    }
    if (typeof row.pk === "string" && !isBlank(row.pk)) {
      return `/invoice/${row.pk}`;
    }
    return "#";
  }, []);

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
      const truncated = truncateText(raw, ITEM_NAME_MAX_LENGTH);
      const emojiParts: string[] = [];
      const pinOpened = isPinOpenedRecently((row as any).pin_as_opened);
      if (pinOpened) {
        // Place the pin icon at the front so it appears to the left of the other emojis.
        emojiParts.push("📌");
      }
      const isCollection = Boolean((row as any).is_collection);
      const isContainer = Boolean((row as any).is_container);
      const isFixedLocation = Boolean((row as any).is_fixed_location);
      const isLost = Boolean((row as any).is_lost);
      const isStaging = Boolean((row as any).is_staging);
      const isConsumable = Boolean((row as any).is_consumable);
      const reminderSource = (row as any).date_reminder;
      const reminderDate = coerceToDate(reminderSource);
      let isReminderOverdue = false;
      if (reminderDate) {
        // Treat the reminder as overdue whenever the current time is equal to or later than the stored reminder moment.
        const reminderTime = reminderDate.getTime();
        if (Number.isFinite(reminderTime)) {
          const nowTime = Date.now();
          if (nowTime >= reminderTime) {
            isReminderOverdue = true;
          }
        }
      }

      if (isCollection) {
        emojiParts.push("🗃️");
      } else if (isContainer) {
        emojiParts.push("📦");
      } else if (isFixedLocation) {
        emojiParts.push("🛏️");
      }

      if (isLost) {
        emojiParts.push("👻");
      }
      if (isStaging) {
        emojiParts.push("⏳");
      }
      if (isReminderOverdue) {
        // The reminder clock helps draw attention to entries whose reminder date has already passed.
        emojiParts.push("⏰");
      }
      // === Consumable emoji logic START ===
      if (isConsumable) {
        emojiParts.push("🍽️");
      }
      // === Consumable emoji logic END ===

      const decorated = emojiParts.length
        ? `${truncated} ${emojiParts.join("")}`
        : truncated;

      const href = buildItemHref(row);
      return (
        <a
          href={href}
          className="text-decoration-none"
          title={raw}
          style={{ display: "inline-block", whiteSpace: "nowrap" }}
        >
          {decorated}
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

      const isAssociated = (row as any).is_associated;
      if (typeof isAssociated === "boolean") {
        return isAssociated ? "🔗" : "⚫";
      }

      const assocRaw =
        (row as any).assoc_type ??
        (row as any).association_type ??
        (row as any).relation_bits ??
        (row as any).relation_type;
      let assocValue = Number.NaN;
      if (typeof assocRaw === "number") {
        assocValue = assocRaw;
      } else if (typeof assocRaw === "string" && !isBlank(assocRaw)) {
        const parsed = Number.parseInt(assocRaw, 10);
        if (Number.isFinite(parsed)) {
          assocValue = parsed;
        }
      }
      if (Number.isFinite(assocValue)) {
        const icons = formatAssociationEmojis(assocValue);
        if (icons) {
          return icons;
        }
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
    [formatAssociationEmojis, normalizedTable],
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

  // Allow the user to move items between containers without leaving the search panel when exactly one match is selected.
  const handleMoveBetweenItems = useCallback(
    async (direction: "target-into-selected" | "selected-into-target") => {
      if (
        !targetUuid ||
        normalizedTable !== "items" ||
        selectedPkArray.length !== 1 ||
        !singleSelectedRow
      ) {
        return;
      }

      const selectedPk = selectedPkArray[0];
      const movingItemUuid =
        direction === "target-into-selected" ? targetUuid : selectedPk;
      const destinationUuid =
        direction === "target-into-selected" ? selectedPk : targetUuid;

      setIsActionBusy(true);
      try {
        const response = await fetch(MOVE_ITEM_ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            item_uuid: movingItemUuid,
            target_uuid: destinationUuid,
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
              : `Move failed (${response.status})`;
          throw new Error(message);
        }
        if (payload && typeof payload === "object" && "ok" in payload && !payload.ok) {
          throw new Error(payload.error ? String(payload.error) : "Move failed");
        }

        pinnedRef.current.clear();
        setSelectedPks(() => new Set());

        await runSearch(lastQueryRef.current || query);

        if (onRelationshipsChanged) {
          onRelationshipsChanged();
        }

        const sourceLabel =
          direction === "target-into-selected"
            ? targetDisplaySlug || targetUuid
            : selectedDisplaySlug || selectedPk;
        const destinationLabel =
          direction === "target-into-selected"
            ? selectedDisplaySlug || selectedPk
            : targetDisplaySlug || targetUuid;

        setModalMessage({
          title: "Move complete",
          body: `Moved ${sourceLabel} into ${destinationLabel}.`,
        });
      } catch (error: any) {
        setModalMessage({
          title: "Move failed",
          body: error?.message || "Unable to move the selected items.",
        });
      } finally {
        setIsActionBusy(false);
      }
    },
    [
      normalizedTable,
      onRelationshipsChanged,
      query,
      runSearch,
      selectedDisplaySlug,
      selectedPkArray,
      singleSelectedRow,
      targetDisplaySlug,
      targetUuid,
    ],
  );

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
            association_type: associationBits,
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

      if (associationBits === 0) {
        pinnedRef.current.clear();
        setSelectedPks(() => new Set());
      }

      await runSearch(lastQueryRef.current || query);

      if (onRelationshipsChanged) {
        onRelationshipsChanged();
      }

      setModalMessage({
        title: isInvoiceMode
          ? "Invoices linked"
          : associationBits === 0
          ? "Links removed"
          : "Relations updated",
        body: isInvoiceMode
          ? "Selected invoices have been linked to the target."
          : associationBits === 0
          ? "Selected relations have been removed."
          : `Association set to ${associationSummary} for the selected entries.`,
      });
    } catch (error: any) {
      setModalMessage({
        title: "Action failed",
        body: error?.message || "Unable to update the selected entries.",
      });
    } finally {
      setIsActionBusy(false);
    }
  }, [associationBits, associationSummary, isInvoiceMode, normalizedTable, query, runSearch, selectedCount, selectedPks, targetUuid, onRelationshipsChanged]);

  const handleUnlinkAssociation = useCallback(async () => {
    if (!targetUuid || selectedCount === 0) {
      return;
    }
    const ids = Array.from(selectedPks);
    setIsActionBusy(true);
    try {
      const endpoint = API_ENDPOINTS[normalizedTable].relate;
      const response = await fetch(endpoint, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            table: normalizedTable,
            target_uuid: targetUuid,
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
            : `Request failed (${response.status})`;
        throw new Error(message);
      }
      if (payload && typeof payload === "object" && "ok" in payload && !payload.ok) {
        throw new Error(payload.error ? String(payload.error) : "Request failed");
      }

      pinnedRef.current.clear();
      setSelectedPks(() => new Set());

      await runSearch(lastQueryRef.current || query);

      if (onRelationshipsChanged) {
        onRelationshipsChanged();
      }

      setModalMessage({
        title: "Relationships removed",
        body:
          ids.length === 1
            ? "Selected relationship was completely removed."
            : `${ids.length} relationships were completely removed.`,
      });
    } catch (error: any) {
      setModalMessage({
        title: "Action failed",
        body: error?.message || "Unable to remove the selected relationships.",
      });
    } finally {
      setIsActionBusy(false);
    }
  }, [normalizedTable, query, runSearch, selectedCount, selectedPks, targetUuid, onRelationshipsChanged]);

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
          <h1 className="h5 mb-0">{displayedTitle}</h1>
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
                  const invoiceHref =
                    normalizedTable === "invoices" ? buildInvoiceHref(row) : "";
                  const pinOpened = isPinOpenedRecently((row as any).pin_as_opened);
                  const invoiceTextClass = pinOpened
                    ? "small text-decoration-none text-danger"
                    : "small text-body text-decoration-none";

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
                        <td style={{ width: "7rem" }}>
                          <div
                            className="d-flex justify-content-center align-items-center"
                            style={{ width: "100px", height: "100px" }}
                          >
                            {thumbnail ? (
                              <img
                                src={thumbnail}
                                alt=""
                                className="rounded"
                                style={{
                                  width: "100%",
                                  height: "100%",
                                  maxWidth: "100px",
                                  maxHeight: "100px",
                                  objectFit: "cover",
                                }}
                              />
                            ) : (
                              <div
                                className="border rounded bg-light w-100 h-100"
                                style={{ width: "100%", height: "100%" }}
                              />
                            )}
                          </div>
                        </td>
                      )}

                      {targetUuid && (
                        <td
                          className="text-center"
                          style={{ width: "3rem", fontSize: "1.5rem" }}
                        >
                          <span
                            aria-hidden
                            style={{ display: "inline-block", whiteSpace: "nowrap" }}
                          >
                            {relationIcon}
                          </span>
                        </td>
                      )}

                      <td>
                        {normalizedTable === "items" ? (
                          renderItemName(row)
                        ) : (
                          <a
                            href={invoiceHref || undefined}
                            className={invoiceTextClass}
                            style={{
                              display: "inline-block",
                              whiteSpace: "nowrap",
                              color: pinOpened ? "var(--bs-danger)" : undefined,
                            }}
                          >
                            {invoiceLines.map((line, idx) => (
                              <div
                                key={`${row.pk}-line-${idx}`}
                                style={{ whiteSpace: "nowrap" }}
                              >
                                {line}
                              </div>
                            ))}
                          </a>
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

      {targetUuid && normalizedTable === "items" && singleSelectedRow && (
        <div
          className="d-flex flex-wrap justify-content-end align-items-center gap-3 px-3 py-2 mb-2 rounded-3"
          style={{ backgroundColor: "rgba(0, 0, 0, 0.06)" }}
        >
          {/* Provide quick containment move shortcuts when exactly one item is selected. */}
          <span className="small text-muted">Move 👋 items between containers:</span>
          <button
            type="button"
            className="btn btn-link btn-sm text-decoration-none"
            disabled={isBusy}
            onClick={() => handleMoveBetweenItems("target-into-selected")}
          >
            {/*
             * Show the direction of movement with descriptive emojis and highlight the involved slugs so that the
             * operator can double-check the action before committing to it.
             */}
            Move 👋 {renderSlugBadge(targetDisplaySlug || targetUuid)} into ➡️{' '}
            {renderSlugBadge(selectedDisplaySlug || singleSelectedRow.pk)}
          </button>
          <button
            type="button"
            className="btn btn-link btn-sm text-decoration-none"
            disabled={isBusy}
            onClick={() => handleMoveBetweenItems("selected-into-target")}
          >
            Move 👋 {renderSlugBadge(selectedDisplaySlug || singleSelectedRow.pk)} into ➡️{' '}
            {renderSlugBadge(targetDisplaySlug || targetUuid)}
          </button>
        </div>
      )}

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
                ☑️☑️
              </button>
              <button
                type="button"
                className="btn btn-outline-secondary btn-sm"
                disabled={!selectedCount}
                onClick={handleSelectNone}
              >
                ☐☐
              </button>
              {allowDelete && (
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  disabled={!selectedCount}
                  onClick={handleDelete}
                >
                  🗑️
                </button>
              )}
            </div>

            {targetUuid && (
              <div className="d-flex flex-wrap align-items-center gap-2">
                {isInvoiceMode ? (
                  <>
                    {/* Invoice associations are binary, so only show link and unlink buttons. */}
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={handleSetAssociation}
                      disabled={isBusy || !selectedCount}
                      title="Link selected invoices to the target"
                      aria-label="Link selected invoices to the target"
                    >
                      <span aria-hidden>🔗</span>
                    </button>
                    <button
                      type="button"
                      className="btn btn-outline-danger btn-sm"
                      onClick={handleUnlinkAssociation}
                      disabled={isBusy || !selectedCount}
                      title="Unlink selected invoices from the target"
                      aria-label="Unlink selected invoices from the target"
                    >
                      <span aria-hidden>💥</span>
                    </button>
                  </>
                ) : (
                  <>
                    <div className="d-flex align-items-center gap-2">
                      {ALL_ASSOCIATION_BITS.map((bit) => {
                        const checked =
                          bit === CONTAINMENT_BIT
                            ? containmentChecked
                            : bit === RELATED_BIT
                            ? relatedChecked
                            : bit === SIMILAR_BIT
                            ? similarChecked
                            : bit === MERGE_BIT
                            ? mergeChecked
                            : (associationBits & bit) === bit;
                        const emoji = bit_to_emoji_character(bit);
                        const label = bit_to_word(bit) || "association";
                        return (
                          <label
                            key={bit}
                            className="mb-0"
                            style={{
                              cursor: isBusy ? "not-allowed" : "pointer",
                              userSelect: "none",
                            }}
                            title={label}
                          >
                            <input
                              type="checkbox"
                              className="form-check-input d-none"
                              checked={checked}
                              onChange={(event) =>
                                handleAssociationBitToggle(bit, event.target.checked)
                              }
                              disabled={isBusy}
                            />
                            <span
                              aria-hidden
                              style={{
                                display: "inline-block",
                                fontSize: "1.5rem",
                                opacity: checked ? 1 : 0.25,
                                transition: "opacity 0.2s ease",
                              }}
                            >
                              {emoji}
                            </span>
                            <span className="visually-hidden">{label}</span>
                          </label>
                        );
                      })}
                    </div>

                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={handleSetAssociation}
                      disabled={isBusy || !selectedCount}
                      aria-label="Save association"
                    >
                      <span aria-hidden>💾</span>
                    </button>
                    <button
                      type="button"
                      className="btn btn-outline-danger btn-sm"
                      onClick={handleUnlinkAssociation}
                      disabled={isBusy || !selectedCount}
                      title="Completely unlink selected relationships"
                      aria-label="Completely unlink selected relationships"
                    >
                      <span aria-hidden>💥</span>
                    </button>
                  </>
                )}
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

