import React, { useEffect, useMemo, useState } from "react";

import { API_BASE } from "../api";

interface ContainmentPathEntry {
  path: string[];
  names: string[];
  terminal_is_fixed_location: boolean;
  terminal_is_dead_end: boolean;
}

interface ContainmentPathPanelProps {
  targetUuid?: string | null;
  refreshSignal?: number;
}

interface DisplayRow {
  ids: string[];
  names: string[];
  summary: string;
  terminalIsFixed: boolean;
}

const MAX_DISPLAY_NAME_LENGTH = 10;

function coerceName(value: string | null | undefined): string {
  const trimmed = (value ?? "").trim();
  return trimmed.length > 0 ? trimmed : "Unnamed item";
}

function formatDisplayName(name: string): string {
  if (name.length <= MAX_DISPLAY_NAME_LENGTH) {
    return name;
  }
  if (MAX_DISPLAY_NAME_LENGTH <= 1) {
    return name.slice(0, MAX_DISPLAY_NAME_LENGTH);
  }
  // Keep the visible text compact while signalling truncation clearly.
  return `${name.slice(0, MAX_DISPLAY_NAME_LENGTH - 1)}…`;
}

const ContainmentPathPanel: React.FC<ContainmentPathPanelProps> = ({
  targetUuid,
  refreshSignal = 0,
}) => {
  const [paths, setPaths] = useState<ContainmentPathEntry[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!targetUuid) {
      // When the item is not yet saved there is nothing to show.
      setPaths([]);
      setErrorMessage(null);
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    let isActive = true;

    const loadPaths = async () => {
      setLoading(true);
      setErrorMessage(null);
      try {
        const response = await fetch(
          `${API_BASE}/api/containmentpaths?target_uuid=${encodeURIComponent(targetUuid)}`,
          { signal: controller.signal, credentials: "include" }
        );
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
        if (payload && typeof payload === "object" && payload.ok === false) {
          throw new Error(payload.error ? String(payload.error) : "Request failed");
        }
        const rawPaths: ContainmentPathEntry[] = Array.isArray(payload?.paths)
          ? (payload.paths as ContainmentPathEntry[])
          : [];
        if (isActive) {
          setPaths(rawPaths);
        }
      } catch (error: any) {
        if (controller.signal.aborted) {
          return;
        }
        if (isActive) {
          setPaths([]);
          setErrorMessage(error?.message || "Unable to load containment paths.");
        }
      } finally {
        if (isActive) {
          setLoading(false);
        }
      }
    };

    void loadPaths();

    return () => {
      isActive = false;
      controller.abort();
    };
  }, [targetUuid, refreshSignal]);

  // Pre-compute display-friendly rows so the render logic remains easy to read.
  const displayRows = useMemo<DisplayRow[]>(() => {
    if (!paths.length) {
      return [];
    }
    return paths.map((entry) => {
      const ids = Array.isArray(entry.path) ? entry.path : [];
      const names = Array.isArray(entry.names) ? entry.names : [];
      const resolvedNames = ids.map((id, index) => coerceName(names[index]));
      const isFixed = Boolean(entry.terminal_is_fixed_location);
      const summary = isFixed
        ? "🏠 Fixed location reached at the end of this path."
        : "🔚 No further containment relationships beyond this point.";
      return {
        ids,
        names: resolvedNames,
        summary,
        terminalIsFixed: isFixed,
      };
    });
  }, [paths]);

  return (
    <div className="mb-4">
      <div className="d-flex align-items-center justify-content-between mb-2">
        <h2 className="h5 mb-0">Containment Paths</h2>
      </div>
      {!targetUuid && (
        <div className="text-muted">Save the item to explore containment paths.</div>
      )}
      {targetUuid && loading && (
        <div className="text-muted">Loading containment information…</div>
      )}
      {targetUuid && !loading && errorMessage && (
        <div className="alert alert-warning py-2 px-3" role="status">
          {errorMessage}
        </div>
      )}
      {targetUuid && !loading && !errorMessage && displayRows.length === 0 && (
        <div className="text-muted">No containment paths are currently recorded.</div>
      )}
      {displayRows.map((row) => {
        const key = row.ids.join("→") || row.summary;
        return (
          <div key={key} className="mb-3">
            <div className="d-flex flex-wrap align-items-center gap-2">
              {row.ids.map((id, index) => {
                // Each breadcrumb links directly to the relevant item page for quick navigation.
                const fullName = row.names[index];
                const shortLabel = formatDisplayName(fullName);
                return (
                  <React.Fragment key={`${id}-${index}`}>
                    <a
                      href={`/item/${id}`}
                      className="text-decoration-none"
                      title={fullName}
                      aria-label={fullName}
                    >
                      {shortLabel}
                    </a>
                    {index < row.ids.length - 1 && (
                      <span aria-hidden className="text-muted">
                        →
                      </span>
                    )}
                  </React.Fragment>
                );
              })}
            </div>
            <div className="text-muted small mt-1">{row.summary}</div>
          </div>
        );
      })}
    </div>
  );
};

export default ContainmentPathPanel;
