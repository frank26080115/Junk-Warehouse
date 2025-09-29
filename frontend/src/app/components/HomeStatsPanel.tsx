import React, { useCallback, useEffect, useState } from "react";

type NumberDisplayStyle = "plain" | "odometer" | "flip";
type EndpointType = "items" | "invoices";

interface StatDefinition {
  id: string;
  label: string;
  emoji: string;
  query: string;
  endpoint: EndpointType;
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
const numberDisplayStyle: NumberDisplayStyle = "odometer";

const STAT_DEFINITIONS: StatDefinition[] = [
  {
    id: "staging",
    label: "Staging Items",
    emoji: "⏳",
    query: "* ?is_staging=true",
    endpoint: "items",
  },
  {
    id: "lost",
    label: "Lost Items",
    emoji: "👻",
    query: "* ?is_lost=true",
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
    id: "merges",
    label: "Merges Planned",
    emoji: "🤝",
    query: "* \mergewaiting",
    endpoint: "items",
  },
  {
    id: "invoices-pending",
    label: "Unprocessed Invoices",
    emoji: "⏳🛒✉️",
    query: "* ?has_been_processed=false",
    endpoint: "invoices",
  },
  {
    id: "pinned-items",
    label: "Pinned Containers",
    emoji: "📌📦",
    query: "* \pinned",
    endpoint: "items",
  },
  {
    id: "pinned-invoices",
    label: "Pinned Invoices",
    emoji: "📌✉️",
    query: "* \pinned",
    endpoint: "invoices",
  },
];

const BASE_PANEL_STYLE: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
  gap: "16px",
  width: "100%",
};

const BASE_ITEM_STYLE: React.CSSProperties = {
  display: "flex",
  alignItems: "stretch",
  borderRadius: "10px",
  border: "1px solid #d8dee9",
  backgroundColor: "#ffffff",
  boxShadow: "0 1px 3px rgba(0, 0, 0, 0.08)",
  overflow: "hidden",
};

const LABEL_SECTION_STYLE: React.CSSProperties = {
  flex: "1 1 auto",
  display: "flex",
  alignItems: "center",
  padding: "12px 16px",
  fontSize: "1rem",
  fontWeight: 600,
  whiteSpace: "nowrap",
};

const BASE_NUMBER_WRAPPER_STYLE: React.CSSProperties = {
  flex: "0 0 auto",
  minWidth: "140px",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: "12px 16px",
  borderLeft: "1px solid #d8dee9",
  borderRight: "1px solid #d8dee9",
};

const BASE_NUMBER_TEXT_STYLE: React.CSSProperties = {
  fontSize: "1.9rem",
  fontWeight: 700,
  letterSpacing: "0.1em",
  textAlign: "center",
};

const SEARCH_BUTTON_STYLE: React.CSSProperties = {
  flex: "0 0 auto",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: "12px 16px",
  fontSize: "1.6rem",
  border: "none",
  backgroundColor: "#f1f5f9",
  cursor: "pointer",
  transition: "background-color 0.2s ease",
};

const SEARCH_BUTTON_HOVER_STYLE: React.CSSProperties = {
  backgroundColor: "#e2e8f0",
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

    STAT_DEFINITIONS.forEach((definition) => {
      void fetchCountForDefinition(definition);
    });

    return () => {
      isUnmounted = true;
      abortControllers.forEach((controller) => {
        controller.abort();
      });
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
        const buttonStyle =
          buttonHoverId === definition.id
            ? { ...SEARCH_BUTTON_STYLE, ...SEARCH_BUTTON_HOVER_STYLE }
            : SEARCH_BUTTON_STYLE;
        const buttonLabel = `Search for ${definition.label.toLowerCase()}`;
        return (
          <div key={definition.id} style={BASE_ITEM_STYLE}>
            <div style={LABEL_SECTION_STYLE} title={errorMessage || definition.label}>
              <span style={{ fontSize: "1.3rem", marginRight: "0.5rem" }}>{definition.emoji}</span>
              <span>{definition.label}</span>
            </div>
            <div style={mergedNumberWrapperStyle} aria-live="polite">
              <span style={mergedNumberTextStyle}>{displayValue}</span>
            </div>
            <button
              type="button"
              onMouseEnter={() => setButtonHoverId(definition.id)}
              onMouseLeave={() => setButtonHoverId((current) => (current === definition.id ? null : current))}
              onFocus={() => setButtonHoverId(definition.id)}
              onBlur={() => setButtonHoverId((current) => (current === definition.id ? null : current))}
              onClick={() => handleSearchClick(definition)}
              style={buttonStyle}
              aria-label={buttonLabel}
              title={buttonLabel}
            >
              🔍
            </button>
          </div>
        );
      })}
    </div>
  );
};

export default HomeStatsPanel;
