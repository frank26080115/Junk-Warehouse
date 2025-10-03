import React, { useEffect, useMemo, useState } from "react";

interface ContainmentPathEntry {
  path: string[];
  names: string[];
  terminal_is_fixed_location: boolean;
  terminal_is_dead_end: boolean;
}

interface ContainmentPathPanelProps {
  targetUuid?: string | null;
  refreshSignal?: number;
  /**
   * Indicates whether the inspected item itself is fixed in place. When true we limit the
   * rendered storage chains to those that also terminate in a fixed location, because a
   * fixed item should only be stored inside other fixed destinations.
   */
  targetIsFixedLocation?: boolean;
}

interface DisplayRow {
  ids: string[];
  names: string[];
  terminalIsFixed: boolean;
  terminalIsDeadEnd: boolean;
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
  targetIsFixedLocation = false,
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
        const endpoint = `/api/containmentpaths?target_uuid=${encodeURIComponent(targetUuid)}`;
        // Using a relative URL ensures we share the same origin as the login session,
        // so authentication cookies are included consistently by the browser.
        const response = await fetch(endpoint, {
          signal: controller.signal,
          credentials: "include",
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

  // Pre-compute display-friendly rows so the render logic remains easy to read. We also track
  // how many candidate paths we filtered out so the UI can explain the absence of results.
  const { rowsToDisplay, filteredOutCount } = useMemo(() => {
    if (!paths.length) {
      return { rowsToDisplay: [], filteredOutCount: 0 };
    }
    const normalizedTarget = targetUuid ? targetUuid.toLowerCase() : null;
    const rows: DisplayRow[] = [];
    paths.forEach((entry) => {
      const ids = Array.isArray(entry.path) ? entry.path : [];
      const names = Array.isArray(entry.names) ? entry.names : [];
      const resolvedNames = ids.map((id, index) => coerceName(names[index]));
      let idsForDisplay = ids;
      let namesForDisplay = resolvedNames;
      if (
        normalizedTarget &&
        idsForDisplay.length > 0 &&
        typeof idsForDisplay[0] === "string" &&
        idsForDisplay[0].toLowerCase() === normalizedTarget
      ) {
        // The backend includes the target item as the first hop in the raw path.
        // Remove it locally as a defensive measure so the breadcrumbs only show
        // neighboring containers.
        idsForDisplay = idsForDisplay.slice(1);
        namesForDisplay = namesForDisplay.slice(1);
      }
      if (idsForDisplay.length === 0) {
        // Without any other items there is nothing meaningful to display.
        return;
      }
      const isFixed = Boolean(entry.terminal_is_fixed_location);
      const isDeadEnd = Boolean(entry.terminal_is_dead_end);
      rows.push({
        ids: idsForDisplay,
        names: namesForDisplay,
        terminalIsFixed: isFixed,
        terminalIsDeadEnd: isDeadEnd,
      });
    });
    let rowsToDisplay = rows;
    if (targetIsFixedLocation) {
      // When the inspected item is fixed in place, only show storage chains that also end at a
      // fixed location. This keeps the suggestions consistent with the item's immovability.
      rowsToDisplay = rows.filter((row) => row.terminalIsFixed);
    }
    const filteredOutCount = rows.length - rowsToDisplay.length;
    return { rowsToDisplay, filteredOutCount };
  }, [paths, targetUuid, targetIsFixedLocation]);

  const shouldExplainFiltering = targetIsFixedLocation && filteredOutCount > 0;

  return (
    <div>
      {/* The surrounding page is responsible for rendering the section heading so this
          panel focuses solely on the informative content. */}
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
      {targetUuid &&
        !loading &&
        !errorMessage &&
        rowsToDisplay.length === 0 && (
          <div className="text-muted">
            {targetIsFixedLocation
              ? "No fixed-location containment paths are currently recorded."
              : "No containment paths are currently recorded."}
          </div>
      )}
      {shouldExplainFiltering && (
        <div className="text-muted small mb-2">
          Showing only storage chains that terminate in a fixed location; {filteredOutCount}
          {filteredOutCount === 1 ? " path is" : " paths are"} hidden because they end in
          movable storage.
        </div>
      )}
      {rowsToDisplay.map((row) => {
        const key = row.ids.join("→") || (row.terminalIsFixed ? "fixed" : "dead-end");
        const indicatorEmoji = row.terminalIsFixed ? "🏠" : "🔚";
        const indicatorDescription = row.terminalIsFixed
          ? "This path ends at a fixed location."
          : row.terminalIsDeadEnd
            ? "This path currently has no further containment options."
            : "This path continues beyond the listed containers.";
        return (
          <div key={key} className="mb-2">
            <div className="d-flex align-items-center gap-2 flex-wrap">
              <span aria-hidden className="fs-5">
                {indicatorEmoji}
              </span>
              <span className="visually-hidden">
                {indicatorDescription}
              </span>
              {row.ids.map((id, index) => {
                // Each breadcrumb links directly to the relevant item page for quick navigation.
                const fullName = row.names[index];
                const shortLabel = formatDisplayName(fullName);
                const isTruncated = shortLabel !== fullName;
                // Provide accessible context for truncated labels so that assistive technology
                // and hover tooltips expose the complete item name within the storage chain.
                const accessibleLabel = fullName;
                const tooltipText = isTruncated ? fullName : undefined;
                return (
                  <React.Fragment key={`${id}-${index}`}>
                    <a
                      href={`/item/${id}`}
                      className="text-decoration-none"
                      title={tooltipText}
                      aria-label={accessibleLabel}
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
          </div>
        );
      })}
    </div>
  );
};

export default ContainmentPathPanel;
