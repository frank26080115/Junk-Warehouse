import React from "react";

type PinSummary = {
  /** Number of items that still have their pin_as_opened timestamp within the active window. */
  items_opened: number;
  /** Number of invoices that still have their pin_as_opened timestamp within the active window. */
  invoices_opened: number;
};

/**
 * Display a compact, single-line summary of how many pinned entities remain "opened".
 * The component intentionally behaves like a regular <span> so callers can style it inline.
 */
const PinnedItemsIndicator: React.FC<React.HTMLAttributes<HTMLSpanElement>> = ({
  style,
  ...rest
}) => {
  const [summary, setSummary] = React.useState<PinSummary>({
    items_opened: 0,
    invoices_opened: 0,
  });

  // Fetch the summary a single time when the footer mounts. The endpoint already enforces auth.
  React.useEffect(() => {
    const controller = new AbortController();

    async function loadSummary() {
      try {
        const response = await fetch("/api/pinsummary", {
          method: "GET",
          credentials: "include",
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`Unexpected response status ${response.status}`);
        }

        const payload = await response.json();
        const data = payload?.data ?? {};
        const items = Number.isFinite(data.items_opened) ? Number(data.items_opened) : 0;
        const invoices = Number.isFinite(data.invoices_opened) ? Number(data.invoices_opened) : 0;
        setSummary({ items_opened: items, invoices_opened: invoices });
      } catch (error) {
        if ((error as any)?.name === "AbortError") {
          return;
        }
        console.warn("PinnedItemsIndicator: unable to load pin summary", error);
        setSummary({ items_opened: 0, invoices_opened: 0 });
      }
    }

    loadSummary();
    return () => controller.abort();
  }, []);

  const displayText = React.useMemo(() => {
    const { items_opened: items, invoices_opened: invoices } = summary;
    if (items <= 0 && invoices <= 0) {
      return "0🪄📌";
    }

    let text = "🪄📌";
    if (items > 0) {
      text += `${items}📦`;
    }
    if (invoices > 0) {
      text += `${invoices}🛒`;
    }
    return text;
  }, [summary]);

  const mergedStyle: React.CSSProperties = {
    fontSize: "0.625rem", // Use a tiny font so the indicator remains subtle.
    whiteSpace: "nowrap", // Keep everything on a single visual line.
    display: "inline", // Behave exactly like a span.
    ...style,
  };

  return (
    <span {...rest} style={mergedStyle}>
      {displayText}
    </span>
  );
};

export default PinnedItemsIndicator;
