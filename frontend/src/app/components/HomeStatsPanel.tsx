import React, { useCallback, useEffect, useState } from "react";

type NumberDisplayStyle = "plain" | "odometer" | "flip";
type EndpointType = "items" | "invoices";

interface StatDefinition {
  id: string;
  label: string;
  emoji: string;
  query: string;
  endpoint: EndpointType;
  /**
   * When true, the entry is updated through a dedicated polling routine instead of the shared search endpoint.
   */
  usesQueueSizeEndpoint?: boolean;
  /**
   * When true, the entry renders without a trailing search button because navigation is not meaningful.
   */
  hideSearchButton?: boolean;
}

interface StatState {
  definition: StatDefinition;
  count: number | null;
  isLoading: boolean;
  errorMessage: string | null;
}

interface HomeStatsPanelProps {
  onItemQuerySelected: (query: string, endpoint: EndpointType) => void;
}

/**
 * Adjust this constant to switch between the available number presentations.
 * The component has dedicated styling for each choice so future adjustments are straightforward.
 */
const numberDisplayStyle: NumberDisplayStyle = "flip";

/**
 * The configuration block below keeps the layout knobs in one visible location so future
 * adjustments remain easy to reason about. All measurements use small, comfortable spacing so the
 * rendered cards feel less bulky.
 */
const STAT_LAYOUT_TOKENS = {
  minimumColumnWidth: 260,
  gridGap: 12,
  cardBorderRadius: 8,
  cardBorderColor: "#d8dee9",
  labelPadding: "8px 12px",
  labelFontSize: "0.95rem",
  labelEmojiSize: "1.15rem",
  labelEmojiSpacing: "0.3rem",
  numberPadding: "8px 12px",
  numberMinimumWidth: 150,
  numberFontSize: "1.6rem",
  buttonPadding: "8px 12px",
  buttonFontSize: "1.4rem",
};

const QUEUE_SIZE_STAT_ID = "database-queue-size";
/**
 * The queue size poller expands its wait time whenever the queue remains empty so the backend can
 * rest while idle. All values are expressed in milliseconds for clarity.
 */
const QUEUE_SIZE_MIN_REFRESH_INTERVAL_MS = 5_000;
const QUEUE_SIZE_REFRESH_INCREMENT_MS = 5_000;
const QUEUE_SIZE_MAX_REFRESH_INTERVAL_MS = 5 * 60 * 1000;

// NOTE: label is not displayed
const STAT_DEFINITIONS: StatDefinition[] = [
  {
    id: "staging",
    label: "Staging Items",
    emoji: "⏳",
    query: "* ?is_staging",
    endpoint: "items",
  },
  {
    id: "lost",
    label: "Lost Items",
    emoji: "👻",
    query: "* ?is_lost",
    endpoint: "items",
  },
  {
    id: "alarmed",
    label: "Alarmed Items",
    emoji: "⏰",
    query: "* ?alarm",
    endpoint: "items",
  },
  {
    id: QUEUE_SIZE_STAT_ID,
    label: "Database Queue Size",
    emoji: "🌀",
    query: "",
    endpoint: "items",
    usesQueueSizeEndpoint: true,
    hideSearchButton: true,
  },
  {
    id: "merges",
    label: "Merges Planned",
    emoji: "⏳🤝",
    query: "* \\mergewaiting",
    endpoint: "items",
  },
  {
    id: "invoices-pending",
    label: "Unprocessed Invoices",
    emoji: "⏳✉️",
    query: "* ?!has_been_processed",
    endpoint: "invoices",
  },
  {
    id: "pinned-items",
    label: "Pinned Containers",
    emoji: "📌📦",
    query: "\\pinned",
    endpoint: "items",
  },
  {
    id: "pinned-invoices",
    label: "Pinned Invoices",
    emoji: "📌✉️",
    query: "\\pinned",
    endpoint: "invoices",
  },
];

const BASE_PANEL_STYLE: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: `repeat(auto-fit, minmax(${STAT_LAYOUT_TOKENS.minimumColumnWidth}px, 1fr))`,
  gap: `${STAT_LAYOUT_TOKENS.gridGap}px`,
  width: "100%",
};

const BASE_ITEM_STYLE: React.CSSProperties = {
  display: "flex",
  alignItems: "stretch",
  borderRadius: `${STAT_LAYOUT_TOKENS.cardBorderRadius}px`,
  border: `1px solid ${STAT_LAYOUT_TOKENS.cardBorderColor}`,
  backgroundColor: "#ffffff",
  boxShadow: "0 1px 3px rgba(0, 0, 0, 0.08)",
  overflow: "hidden",
};

const LABEL_SECTION_STYLE: React.CSSProperties = {
  flex: "1 1 auto",
  display: "flex",
  alignItems: "center",
  padding: STAT_LAYOUT_TOKENS.labelPadding,
  fontSize: STAT_LAYOUT_TOKENS.labelFontSize,
  fontWeight: 600,
  whiteSpace: "nowrap",
};

const BASE_NUMBER_WRAPPER_STYLE: React.CSSProperties = {
  flex: "0 0 auto",
  minWidth: `${STAT_LAYOUT_TOKENS.numberMinimumWidth}px`,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: STAT_LAYOUT_TOKENS.numberPadding,
  borderLeft: `1px solid ${STAT_LAYOUT_TOKENS.cardBorderColor}`,
  borderRight: `1px solid ${STAT_LAYOUT_TOKENS.cardBorderColor}`,
};

const BASE_NUMBER_TEXT_STYLE: React.CSSProperties = {
  fontSize: STAT_LAYOUT_TOKENS.numberFontSize,
  fontWeight: 700,
  letterSpacing: "0.1em",
  textAlign: "center",
};

const SEARCH_BUTTON_STYLE: React.CSSProperties = {
  flex: "0 0 auto",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: STAT_LAYOUT_TOKENS.buttonPadding,
  fontSize: STAT_LAYOUT_TOKENS.buttonFontSize,
  border: "none",
  backgroundColor: "#f1f5f9",
  cursor: "pointer",
  transition: "background-color 0.2s ease",
};

const SEARCH_BUTTON_HOVER_STYLE: React.CSSProperties = {
  backgroundColor: "#e2e8f0",
};

// This subtle placeholder keeps the queue size entry aligned with the other statistics while
// ensuring no visible icon is rendered for the passive button shell.
const PASSIVE_BUTTON_PLACEHOLDER_ICON_STYLE: React.CSSProperties = {
  display: "inline-block",
  visibility: "hidden",
};

const numberStyleConfig: Record<
  NumberDisplayStyle,
  { wrapper: React.CSSProperties; text: React.CSSProperties }
> = {
  plain: {
    wrapper: {
      backgroundColor: "#f8fafc",
    },
    text: {
      fontFamily: '"Segoe UI", "Helvetica Neue", Arial, sans-serif',
      letterSpacing: "0.08em",
      color: "#111827",
    },
  },
  odometer: {
    wrapper: {
      background: "linear-gradient(180deg, #2d2d2d 0%, #111111 100%)",
      color: "#f8f9fa",
      borderLeft: "1px solid #0b0b0b",
      borderRight: "1px solid #0b0b0b",
      boxShadow: "inset 0 0 6px rgba(0, 0, 0, 0.6)",
    },
    text: {
      fontFamily: '"Courier New", Courier, monospace',
      letterSpacing: "0.18em",
      textShadow: "0 0 6px rgba(0, 0, 0, 0.6)",
    },
  },
  flip: {
    wrapper: {
      background: "linear-gradient(180deg, #3f4c6b 0%, #606c88 100%)",
      color: "#ffffff",
      boxShadow:
        "inset 0 4px 0 rgba(255, 255, 255, 0.25), inset 0 -4px 0 rgba(0, 0, 0, 0.25)",
    },
    text: {
      fontFamily: '"Roboto Mono", "Courier New", monospace',
      letterSpacing: "0.14em",
    },
  },
};

type StatUpdate = Partial<Omit<StatState, "definition">>;

const HomeStatsPanel: React.FC<HomeStatsPanelProps> = ({ onItemQuerySelected }) => {
  const [buttonHoverId, setButtonHoverId] = useState<string | null>(null);
  const [statStates, setStatStates] = useState<StatState[]>(() =>
    STAT_DEFINITIONS.map((definition) => ({
      definition,
      count: null,
      isLoading: true,
      errorMessage: null,
    })),
  );

  // Maintain a dedicated helper so each statistic entry can be updated in isolation without manual array cloning.
  const updateStatState = useCallback((id: string, update: StatUpdate) => {
    setStatStates((previous) =>
      previous.map((entry) =>
        entry.definition.id === id ? { ...entry, ...update } : entry,
      ),
    );
  }, []);

  // Load every statistic count once when the panel mounts so the home page immediately shows relevant data.
  useEffect(() => {
    let isUnmounted = false;
    const abortControllers = new Map<string, AbortController>();

    const fetchCountForDefinition = async (definition: StatDefinition) => {
      if (definition.usesQueueSizeEndpoint) {
        // The queue size statistic is loaded by its own polling routine.
        return;
      }
      const controller = new AbortController();
      abortControllers.set(definition.id, controller);
      updateStatState(definition.id, { isLoading: true, errorMessage: null });

      try {
        const endpoint =
          definition.endpoint === "invoices" ? "/api/searchinvoices" : "/api/search";
        const response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ q: definition.query, include_thumbnails: false }),
          signal: controller.signal,
        });

        let payload: any = null;
        try {
          payload = await response.json();
        } catch {
          payload = null;
        }

        if (!response.ok) {
          const message: string =
            (payload && typeof payload.error === "string" && payload.error.trim()) ||
            `Unable to load statistics for ${definition.label}.`;
          throw new Error(message);
        }

        const rows: unknown = payload && (payload.data ?? payload.results ?? []);
        const normalisedCount = Array.isArray(rows) ? rows.length : 0;

        if (!isUnmounted && !controller.signal.aborted) {
          updateStatState(definition.id, {
            count: normalisedCount,
            isLoading: false,
            errorMessage: null,
          });
        }
      } catch (error: any) {
        if (controller.signal.aborted || isUnmounted) {
          return;
        }
        const message =
          (error && typeof error.message === "string" && error.message) ||
          `Unable to load statistics for ${definition.label}.`;
        updateStatState(definition.id, {
          count: null,
          isLoading: false,
          errorMessage: message,
        });
      }
    };

    const loadSequentially = async () => {
      for (const definition of STAT_DEFINITIONS) {
        if (definition.usesQueueSizeEndpoint) {
          continue;
        }
        if (isUnmounted) {
          break;
        }

        // Await each request before starting the next one so the backend never receives a surge of
        // concurrent search queries. This conservative approach helps the database remain stable
        // even when additional statistics are introduced in the future.
        await fetchCountForDefinition(definition);
      }
    };

    void loadSequentially();

    return () => {
      isUnmounted = true;
      abortControllers.forEach((controller) => {
        controller.abort();
      });
    };
  }, [updateStatState]);

  useEffect(() => {
    let isUnmounted = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let currentDelayMs = QUEUE_SIZE_MIN_REFRESH_INTERVAL_MS;
    let isFirstLoad = true;

    const updateQueueStat = (update: StatUpdate) => {
      if (!isUnmounted) {
        updateStatState(QUEUE_SIZE_STAT_ID, update);
      }
    };

    const fetchQueueSize = async (showLoading: boolean) => {
      if (showLoading) {
        updateQueueStat({ isLoading: true, errorMessage: null });
      }

      // Remember the desired delay for the next poll so the scheduler can back off after the current request finishes.
      let plannedDelayMs = currentDelayMs;

      try {
        const response = await fetch("/api/getdbqueuesize");

        let payload: any = null;
        try {
          payload = await response.json();
        } catch {
          payload = null;
        }

        if (!response.ok) {
          const message: string =
            (payload && typeof payload.error === "string" && payload.error.trim()) ||
            "Unable to load the database queue size.";
          throw new Error(message);
        }

        const rawValue = payload && payload.queue_size;
        const numericValue = typeof rawValue === "number" ? rawValue : Number(rawValue);
        const safeValue = Number.isFinite(numericValue) ? Math.max(0, Math.trunc(numericValue)) : 0;

        updateQueueStat({ count: safeValue, isLoading: false, errorMessage: null });

        // When there is work waiting in the queue, poll aggressively; otherwise gradually extend the delay up to five minutes.
        if (safeValue > 0) {
          plannedDelayMs = QUEUE_SIZE_MIN_REFRESH_INTERVAL_MS;
        } else {
          plannedDelayMs = Math.min(
            QUEUE_SIZE_MAX_REFRESH_INTERVAL_MS,
            currentDelayMs + QUEUE_SIZE_REFRESH_INCREMENT_MS,
          );
        }
      } catch (error: any) {
        const message =
          (error && typeof error.message === "string" && error.message) ||
          "Unable to load the database queue size.";
        updateQueueStat({ count: null, isLoading: false, errorMessage: message });
        plannedDelayMs = QUEUE_SIZE_MIN_REFRESH_INTERVAL_MS;
      } finally {
        isFirstLoad = false;

        currentDelayMs = plannedDelayMs;

        if (!isUnmounted) {
          if (timeoutId) {
            clearTimeout(timeoutId);
          }
          timeoutId = setTimeout(() => {
            void fetchQueueSize(false);
          }, currentDelayMs);
        }
      }
    };

    // Start the polling loop immediately so the user receives feedback as soon as the page loads.
    void fetchQueueSize(true);

    return () => {
      isUnmounted = true;
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    };
  }, [updateStatState]);

  // When the magnifying glass is clicked, ask the parent component to take care of navigation or inline searching.
  const handleSearchClick = useCallback(
    (definition: StatDefinition) => {
      const trimmedQuery = definition.query.trim();
      onItemQuerySelected(trimmedQuery, definition.endpoint);
    },
    [onItemQuerySelected],
  );

  // Prepare the final number styling ahead of rendering so the JSX stays tidy and easy to adjust later.
  const mergedNumberWrapperStyle: React.CSSProperties = {
    ...BASE_NUMBER_WRAPPER_STYLE,
    ...numberStyleConfig[numberDisplayStyle].wrapper,
  };

  const mergedNumberTextStyle: React.CSSProperties = {
    ...BASE_NUMBER_TEXT_STYLE,
    ...numberStyleConfig[numberDisplayStyle].text,
  };

  return (
    <div style={BASE_PANEL_STYLE}>
      {statStates.map((stat) => {
        const { definition, count, isLoading, errorMessage } = stat;
        const displayValue = isLoading
          ? "???"
          : count != null
          ? count.toLocaleString()
          : "--";
        // Determine whether this entry should render an active search control or a passive placeholder.
        const shouldRenderInteractiveButton = !definition.hideSearchButton;
        const shouldRenderPassiveButton =
          definition.usesQueueSizeEndpoint && definition.hideSearchButton;
        const shouldRenderButtonShell =
          shouldRenderInteractiveButton || shouldRenderPassiveButton;

        let buttonStyle: React.CSSProperties | undefined;
        if (shouldRenderButtonShell) {
          if (shouldRenderInteractiveButton && buttonHoverId === definition.id) {
            buttonStyle = { ...SEARCH_BUTTON_STYLE, ...SEARCH_BUTTON_HOVER_STYLE };
          } else if (shouldRenderInteractiveButton) {
            buttonStyle = { ...SEARCH_BUTTON_STYLE };
          } else {
            buttonStyle = {
              ...SEARCH_BUTTON_STYLE,
              cursor: "default",
              pointerEvents: "none",
            };
          }
        }

        if (shouldRenderButtonShell && !buttonStyle) {
          // Fall back to the baseline styling if no branch configured the button so React receives a consistent object.
          buttonStyle = { ...SEARCH_BUTTON_STYLE };
        }

        // Prepare the shared button label once so both accessibility paths remain consistent.
        const buttonLabel = `Search for ${definition.label.toLowerCase()}`;
        const buttonProps: React.ButtonHTMLAttributes<HTMLButtonElement> = shouldRenderInteractiveButton
          ? {
              onMouseEnter: () => setButtonHoverId(definition.id),
              onMouseLeave: () =>
                setButtonHoverId((current) =>
                  current === definition.id ? null : current,
                ),
              onFocus: () => setButtonHoverId(definition.id),
              onBlur: () =>
                setButtonHoverId((current) =>
                  current === definition.id ? null : current,
                ),
              onClick: () => handleSearchClick(definition),
              "aria-label": buttonLabel,
              title: buttonLabel,
            }
          : {
              disabled: true,
              tabIndex: -1,
              "aria-hidden": true,
            };

        // Provide a visually hidden magnifying glass when the button is passive so spacing stays uniform.
        const buttonContent = shouldRenderInteractiveButton ? (
          "🔍"
        ) : (
          <span aria-hidden="true" style={PASSIVE_BUTTON_PLACEHOLDER_ICON_STYLE}>
            🔍
          </span>
        );
        return (
          <div key={definition.id} style={BASE_ITEM_STYLE}>
            <div style={LABEL_SECTION_STYLE} title={errorMessage || definition.label}>
              <span
                style={{
                  fontSize: STAT_LAYOUT_TOKENS.labelEmojiSize,
                  marginRight: STAT_LAYOUT_TOKENS.labelEmojiSpacing,
                }}
              >
                {definition.emoji}
              </span>
              {/* the label is not displayed */}
            </div>
            <div style={mergedNumberWrapperStyle} aria-live="polite">
              <span style={mergedNumberTextStyle}>{displayValue}</span>
            </div>
            {shouldRenderButtonShell ? (
              <button type="button" style={buttonStyle} {...buttonProps}>
                {buttonContent}
              </button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
};

export default HomeStatsPanel;
