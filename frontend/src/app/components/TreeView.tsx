import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { collect_emoji_characters_from_int } from "../helpers/assocHelper";

interface RawTreeItem {
  id: string;
  name?: string | null;
  slug?: string | null;
  child_nodes?: RawTreeItem[];
  containments?: string[];
  association_type?: number | string | null;
  assoc_type?: number | string | null;
  association_bits?: number | string | null;
  is_container?: boolean | null;
  is_tree_root?: boolean | null;
  is_collection?: boolean | null;
  is_deleted?: boolean | null;
  [key: string]: unknown;
}

interface TreeNodeState {
  data: RawTreeItem;
  children: TreeNodeState[];
  isOpen: boolean;
  hasLoadedChildren: boolean;
  isLoadingChildren: boolean;
}

interface PinnedSuggestion {
  id: string;
  name: string;
  slug?: string;
}

const TREE_ENDPOINT = "/api/getinittree";
const ITEM_ENDPOINT = "/api/getitem";
const SAVE_ENDPOINT = "/api/saveitem";
const MOVE_ENDPOINT = "/api/moveitem";
const SEARCH_ENDPOINT = "/api/search";
const PINNED_QUERY = "\\pinned";

const TREE_ROOT_STYLE: React.CSSProperties = {
  listStyleType: "none",
  margin: 0,
  padding: 0,
};

const ROW_WRAPPER_STYLE: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
  margin: 0,
  padding: 0,
};

const INDICATOR_STYLE: React.CSSProperties = {
  listStyleType: "none",
  cursor: "pointer",
  userSelect: "none",
  padding: "2px 6px",
  borderRadius: "4px",
};

const LABEL_STYLE: React.CSSProperties = {
  listStyleType: "none",
  cursor: "pointer",
  userSelect: "none",
  whiteSpace: "nowrap",
  flex: 1,
  padding: "2px 6px",
  borderRadius: "4px",
};

const LOADING_STYLE: React.CSSProperties = {
  listStyleType: "none",
  color: "#555",
  padding: "2px 6px",
  fontStyle: "italic",
};

const STATUS_STYLE: React.CSSProperties = {
  marginTop: "8px",
  color: "#a33",
};

const INFO_STYLE: React.CSSProperties = {
  marginTop: "8px",
  color: "#2a6",
};

const MODAL_OVERLAY_STYLE: React.CSSProperties = {
  position: "fixed",
  top: 0,
  left: 0,
  width: "100%",
  height: "100%",
  backgroundColor: "rgba(0, 0, 0, 0.35)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 2000,
  padding: "16px",
};

const MODAL_CARD_STYLE: React.CSSProperties = {
  backgroundColor: "#fff",
  borderRadius: "8px",
  minWidth: "320px",
  maxWidth: "420px",
  padding: "16px",
  boxShadow: "0 12px 28px rgba(0, 0, 0, 0.25)",
};

const MODAL_LINE_STYLE: React.CSSProperties = {
  marginBottom: "12px",
  display: "flex",
  alignItems: "center",
  gap: "8px",
};

const MODAL_HEADER_STYLE: React.CSSProperties = {
  ...MODAL_LINE_STYLE,
  justifyContent: "space-between",
  alignItems: "flex-start",
};

const MODAL_ACTION_GROUP_STYLE: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "12px",
};

const MODAL_ICON_BUTTON_STYLE: React.CSSProperties = {
  cursor: "pointer",
  fontSize: "18px",
  userSelect: "none",
};

const MODAL_LINK_STYLE: React.CSSProperties = {
  cursor: "pointer",
  color: "#0645ad",
  textDecoration: "underline",
  userSelect: "none",
};

function sanitizeIncomingData(data: RawTreeItem): RawTreeItem {
  const sanitized: RawTreeItem = { ...data };
  delete sanitized.child_nodes;
  return sanitized;
}

function createTreeNode(raw: RawTreeItem): TreeNodeState {
  const childArray = Array.isArray(raw.child_nodes) ? raw.child_nodes : [];
  const sanitized = sanitizeIncomingData(raw);
  const children = childArray.map((child) => createTreeNode(child));
  return {
    data: sanitized,
    children,
    isOpen: false,
    hasLoadedChildren: children.length > 0,
    isLoadingChildren: false,
  };
}

function mergeTreeData(current: RawTreeItem, incoming: RawTreeItem): RawTreeItem {
  const sanitized = sanitizeIncomingData(incoming);
  return { ...current, ...sanitized };
}

function updateNodeList(
  nodes: TreeNodeState[],
  id: string,
  updater: (node: TreeNodeState) => TreeNodeState,
): TreeNodeState[] {
  // Walk the entire tree to find the matching node, cloning branches on the way down
  // so React notices the structural change and re-renders the modified portion.
  let changed = false;
  const updated = nodes.map((node) => {
    if (node.data.id === id) {
      changed = true;
      return updater(node);
    }
    if (node.children.length > 0) {
      const nextChildren = updateNodeList(node.children, id, updater);
      if (nextChildren !== node.children) {
        changed = true;
        return { ...node, children: nextChildren };
      }
    }
    return node;
  });
  return changed ? updated : nodes;
}

function findNode(nodes: TreeNodeState[], id: string): TreeNodeState | null {
  for (const node of nodes) {
    if (node.data.id === id) {
      return node;
    }
    const nested = findNode(node.children, id);
    if (nested) {
      return nested;
    }
  }
  return null;
}

function collectAncestorIds(nodes: TreeNodeState[], targetId: string): string[] {
  // This helper performs a depth-first search so we can exclude ancestors from child lists.
  const ancestors: string[] = [];
  const stack: string[] = [];

  function walk(branches: TreeNodeState[]): boolean {
    for (const branch of branches) {
      stack.push(branch.data.id);
      if (branch.data.id === targetId) {
        ancestors.push(...stack.slice(0, -1));
        stack.pop();
        return true;
      }
      if (walk(branch.children)) {
        stack.pop();
        return true;
      }
      stack.pop();
    }
    return false;
  }

  walk(nodes);
  return ancestors;
}

function deriveAssociationBits(data: RawTreeItem): number {
  const candidates = [data.association_type, data.assoc_type, data.association_bits];
  for (const candidate of candidates) {
    const numeric = Number(candidate);
    if (Number.isFinite(numeric)) {
      return numeric;
    }
  }
  return 0;
}

function coerceName(value: unknown, fallback: string): string {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed.length > 0) {
      return trimmed;
    }
  }
  return fallback;
}

function formatNodeLabel(data: RawTreeItem): string {
  const baseName = coerceName(
    data.name,
    coerceName(data.slug, data.id),
  );
  const icons = collect_emoji_characters_from_int(deriveAssociationBits(data));
  const prefix = icons.length > 0 ? `${icons.join("")} ` : "";
  const deletedSuffix = data.is_deleted ? " (deleted)" : "";
  return `${prefix}${baseName}${deletedSuffix}`.trim();
}

function buildItemUrl(data: RawTreeItem): string {
  const slugValue = typeof data.slug === "string" ? data.slug.trim() : "";
  if (slugValue) {
    return `/item/${slugValue}`;
  }
  return `/item/${data.id}`;
}

function computeHasChildren(node: TreeNodeState): boolean {
  if (node.children.length > 0) {
    return true;
  }
  const candidate = node.data;
  if (Array.isArray(candidate.containments) && candidate.containments.length > 0) {
    return true;
  }
  return Boolean(candidate.is_container || candidate.is_tree_root || candidate.is_collection);
}

function createNestedListStyle(depth: number): React.CSSProperties {
  if (depth <= 0) {
    return TREE_ROOT_STYLE;
  }
  return {
    listStyleType: "none",
    margin: 0,
    paddingLeft: "16px",
    borderLeft: "2px solid rgba(0, 0, 0, 0.1)",
  };
}

const TreeView: React.FC = () => {
  const [treeNodes, setTreeNodes] = useState<TreeNodeState[]>([]);
  const [initialLoading, setInitialLoading] = useState<boolean>(false);
  const [initialError, setInitialError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusIsError, setStatusIsError] = useState<boolean>(false);
  const [modalNodeId, setModalNodeId] = useState<string | null>(null);
  const [modalName, setModalName] = useState<string>("");
  const [modalError, setModalError] = useState<string | null>(null);
  const [modalBusy, setModalBusy] = useState<boolean>(false);
  const [pinnedSuggestion, setPinnedSuggestion] = useState<PinnedSuggestion | null>(null);

  const clickTimerRef = useRef<number | null>(null);
  const mountedRef = useRef<boolean>(true);

  const treeNodesRef = useRef<TreeNodeState[]>([]);

  useEffect(() => {
    // React's Strict Mode intentionally mounts components twice in development, so we reset
    // the mounted flag on every entry to guarantee asynchronous responses can update state.
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (clickTimerRef.current !== null) {
        window.clearTimeout(clickTimerRef.current);
        clickTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    // Persist the latest tree structure so background requests can reference fresh ancestry data.
    treeNodesRef.current = treeNodes;
  }, [treeNodes]);

  const parseFetchJson = useCallback(async (response: Response): Promise<any> => {
    let payload: any = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const errorMessage =
        payload && typeof payload === "object" && payload !== null && "error" in payload
          ? String(payload.error)
          : `Request failed (${response.status})`;
      throw new Error(errorMessage);
    }
    return payload;
  }, []);

  const loadInitialTree = useCallback(async () => {
    setInitialLoading(true);
    setInitialError(null);
    try {
      const response = await fetch(TREE_ENDPOINT, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
      });
      const payload = await parseFetchJson(response);
      const nodes: RawTreeItem[] = Array.isArray(payload?.root_nodes)
        ? (payload.root_nodes as RawTreeItem[])
        : [];
      const prepared = nodes
        .filter((node) => node && typeof node.id === "string")
        .map((node) => createTreeNode(node));
      if (mountedRef.current) {
        setTreeNodes(prepared);
      }
    } catch (error: any) {
      if (mountedRef.current) {
        setInitialError(error?.message ?? "Unable to load tree data.");
      }
    } finally {
      if (mountedRef.current) {
        setInitialLoading(false);
      }
    }
  }, [parseFetchJson]);

  useEffect(() => {
    loadInitialTree();
  }, [loadInitialTree]);

  const fetchItem = useCallback(
    async (identifier: string, includeContainments = false): Promise<RawTreeItem | null> => {
      const response = await fetch(ITEM_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          xyz: identifier,
          inc_containments: includeContainments,
        }),
      });
      const payload = await parseFetchJson(response);
      if (!payload || typeof payload !== "object") {
        return null;
      }
      const identifierValue = (payload as any).id;
      if (typeof identifierValue !== "string") {
        return null;
      }
      return payload as RawTreeItem;
    },
    [parseFetchJson],
  );

  const loadChildrenForNode = useCallback(
    async (nodeId: string) => {
      // Optimistically mark the node as loading so the UI reflects the expansion instantly.
      setTreeNodes((previous) =>
        updateNodeList(previous, nodeId, (node) => ({
          ...node,
          isLoadingChildren: true,
        })),
      );
      try {
        // Always request containment identifiers first so we know which children to hydrate lazily.
        const parent = await fetchItem(nodeId, true);
        if (!parent) {
          throw new Error("Item details were not returned by the server.");
        }
        // Build a list of ancestor identifiers so parent nodes never masquerade as their own children.
        const ancestorIds = collectAncestorIds(treeNodesRef.current, nodeId);
        const invalidIds = new Set<string>([nodeId, ...ancestorIds]);
        const containmentIds = Array.isArray(parent.containments)
          ? parent.containments
              .filter((value): value is string => typeof value === "string" && value.length > 0)
              .filter((value) => !invalidIds.has(value))
          : [];
        const uniqueContainmentIds: string[] = [];
        // This local set prevents redundant fetches when the API returns duplicate identifiers.
        const seenContainmentIds = new Set<string>();
        for (const candidateId of containmentIds) {
          if (!seenContainmentIds.has(candidateId)) {
            seenContainmentIds.add(candidateId);
            uniqueContainmentIds.push(candidateId);
          }
        }
        const childPromises = uniqueContainmentIds.map((childId) => fetchItem(childId, false));
        const childRows = await Promise.all(childPromises);
        const preparedChildren = childRows
          .filter((row): row is RawTreeItem => Boolean(row && typeof row.id === "string"))
          .map((row) => createTreeNode(row));
        if (!mountedRef.current) {
          return;
        }
        setTreeNodes((previous) =>
          updateNodeList(previous, nodeId, (node) => ({
            ...node,
            data: mergeTreeData(node.data, parent),
            children: preparedChildren,
            hasLoadedChildren: true,
            isLoadingChildren: false,
          })),
        );
      } catch (error: any) {
        if (mountedRef.current) {
          setStatusIsError(true);
          setStatusMessage(error?.message ?? "Unable to load child items.");
          setTreeNodes((previous) =>
            updateNodeList(previous, nodeId, (node) => ({
              ...node,
              isLoadingChildren: false,
            })),
          );
        }
      }
    },
    [fetchItem],
  );

  const applyNodeUpdate = useCallback(
    (nodeId: string, incoming: RawTreeItem) => {
      // Merge new item data into the existing node so name changes or flag updates appear immediately.
      setTreeNodes((previous) =>
        updateNodeList(previous, nodeId, (node) => ({
          ...node,
          data: mergeTreeData(node.data, incoming),
        })),
      );
    },
    [],
  );

  const closeModal = useCallback(() => {
    setModalNodeId(null);
    setModalName("");
    setModalError(null);
    setModalBusy(false);
    setPinnedSuggestion(null);
  }, []);

  const openModalForNode = useCallback(
    (node: TreeNodeState) => {
      setModalNodeId(node.data.id);
      setModalName(coerceName(node.data.name, coerceName(node.data.slug, node.data.id)));
      setModalError(null);
      setModalBusy(false);
      setPinnedSuggestion(null);
    },
    [],
  );

  useEffect(() => {
    if (!modalNodeId) {
      return;
    }
    let ignore = false;
    async function loadPinnedSuggestion() {
      try {
        const response = await fetch(SEARCH_ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            q: PINNED_QUERY,
            table: "items",
          }),
        });
        const payload = await parseFetchJson(response);
        const rawResults: unknown = Array.isArray(payload)
          ? payload
          : payload && typeof payload === "object" && "data" in payload
          ? (payload as any).data
          : [];
        const rows: any[] = Array.isArray(rawResults) ? (rawResults as any[]) : [];
        const firstRow = rows.find((row) => row && typeof row.pk === "string");
        if (firstRow && !ignore) {
          const suggestion: PinnedSuggestion = {
            id: String(firstRow.pk),
            name: coerceName(firstRow.name, coerceName(firstRow.slug, String(firstRow.pk))),
            slug: typeof firstRow.slug === "string" ? firstRow.slug : undefined,
          };
          setPinnedSuggestion(suggestion);
        } else if (!ignore) {
          setPinnedSuggestion(null);
        }
      } catch {
        if (!ignore) {
          setPinnedSuggestion(null);
        }
      }
    }
    loadPinnedSuggestion();
    return () => {
      ignore = true;
    };
  }, [modalNodeId, parseFetchJson]);

  const handleSave = useCallback(async () => {
    if (!modalNodeId || modalBusy) {
      return;
    }
    setModalBusy(true);
    setModalError(null);
    try {
      const response = await fetch(SAVE_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: modalNodeId,
          name: modalName,
        }),
      });
      const payload = await parseFetchJson(response);
      applyNodeUpdate(modalNodeId, payload as RawTreeItem);
      setStatusIsError(false);
      setStatusMessage("Item saved successfully.");
      closeModal();
    } catch (error: any) {
      setModalError(error?.message ?? "Unable to save the item.");
    } finally {
      setModalBusy(false);
    }
  }, [applyNodeUpdate, closeModal, modalBusy, modalName, modalNodeId, parseFetchJson]);

  const handleDelete = useCallback(async () => {
    if (!modalNodeId || modalBusy) {
      return;
    }
    setModalBusy(true);
    setModalError(null);
    try {
      const response = await fetch(SAVE_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: modalNodeId,
          is_deleted: true,
        }),
      });
      const payload = await parseFetchJson(response);
      applyNodeUpdate(modalNodeId, payload as RawTreeItem);
      setStatusIsError(false);
      setStatusMessage("Item marked as deleted.");
      closeModal();
    } catch (error: any) {
      setModalError(error?.message ?? "Unable to delete the item.");
    } finally {
      setModalBusy(false);
    }
  }, [applyNodeUpdate, closeModal, modalBusy, modalNodeId, parseFetchJson]);

  const handleMoveIntoPinned = useCallback(async () => {
    if (!modalNodeId || modalBusy) {
      return;
    }
    setModalBusy(true);
    setModalError(null);
    try {
      const response = await fetch(MOVE_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          item_uuid: modalNodeId,
          target_uuid: "pinned",
        }),
      });
      const payload = await parseFetchJson(response);
      if (!payload || typeof payload !== "object" || payload.ok !== true) {
        throw new Error(
          payload && typeof payload === "object" && "error" in payload
            ? String(payload.error)
            : "Move operation did not succeed.",
        );
      }
      setStatusIsError(false);
      setStatusMessage("Item moved to the pinned container.");
      closeModal();
      loadInitialTree();
    } catch (error: any) {
      setModalError(error?.message ?? "Unable to move the item.");
    } finally {
      setModalBusy(false);
    }
  }, [closeModal, loadInitialTree, modalBusy, modalNodeId, parseFetchJson]);

  const handleIndicatorClick = useCallback(
    (event: React.MouseEvent<HTMLLIElement>, node: TreeNodeState) => {
      event.preventDefault();
      event.stopPropagation();
      if (!computeHasChildren(node)) {
        return;
      }
      const willOpen = !node.isOpen;
      setTreeNodes((previous) =>
        updateNodeList(previous, node.data.id, (current) => ({
          ...current,
          isOpen: willOpen,
          isLoadingChildren: willOpen && !current.hasLoadedChildren ? true : current.isLoadingChildren,
        })),
      );
      if (willOpen && !node.hasLoadedChildren) {
        loadChildrenForNode(node.data.id);
      }
    },
    [loadChildrenForNode],
  );

  const openItemInNewTab = useCallback((data: RawTreeItem) => {
    const url = buildItemUrl(data);
    // Using noopener and noreferrer protects the original window from potential tab hijacking.
    window.open(url, "_blank", "noopener,noreferrer");
  }, []);

  const handleLabelClick = useCallback(
    (event: React.MouseEvent<HTMLLIElement>, node: TreeNodeState) => {
      event.preventDefault();
      event.stopPropagation();
      if (clickTimerRef.current !== null) {
        window.clearTimeout(clickTimerRef.current);
        clickTimerRef.current = null;
      }
      // Defer opening the modal slightly so a rapid double-click can be interpreted as navigation instead.
      clickTimerRef.current = window.setTimeout(() => {
        openModalForNode(node);
        if (clickTimerRef.current !== null) {
          window.clearTimeout(clickTimerRef.current);
          clickTimerRef.current = null;
        }
      }, 200);
    },
    [openModalForNode],
  );

  const handleLabelDoubleClick = useCallback(
    (event: React.MouseEvent<HTMLLIElement>, node: TreeNodeState) => {
      event.preventDefault();
      event.stopPropagation();
      if (clickTimerRef.current !== null) {
        window.clearTimeout(clickTimerRef.current);
        clickTimerRef.current = null;
      }
      // Double-clicks open a separate tab so the original view remains visible for quick comparisons.
      openItemInNewTab(node.data);
    },
    [openItemInNewTab],
  );

  const handleLabelAuxClick = useCallback(
    (event: React.MouseEvent<HTMLLIElement>, node: TreeNodeState) => {
      if (event.button !== 1) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      if (clickTimerRef.current !== null) {
        window.clearTimeout(clickTimerRef.current);
        clickTimerRef.current = null;
      }
      openItemInNewTab(node.data);
    },
    [openItemInNewTab],
  );

  const renderNode = useCallback(
    (node: TreeNodeState, depth: number): React.ReactNode => {
      const hasChildren = computeHasChildren(node);
      const indicator = hasChildren ? (node.isOpen ? "🔽" : "▶️") : "🔹";
      const labelText = formatNodeLabel(node.data);
      const nestedStyle = createNestedListStyle(depth + 1);
      return (
        <li key={node.data.id} style={{ listStyleType: "none", margin: "4px 0" }}>
          {/* Each row is rendered as a flex-based list to respect the requirement of using only <ul>/<li> elements. */}
          <ul style={ROW_WRAPPER_STYLE}>
            <li
              style={{ ...INDICATOR_STYLE, opacity: hasChildren ? 1 : 0.5 }}
              onClick={(event) => handleIndicatorClick(event, node)}
            >
              {indicator}
            </li>
            <li
              style={LABEL_STYLE}
              onClick={(event) => handleLabelClick(event, node)}
              onDoubleClick={(event) => handleLabelDoubleClick(event, node)}
              onAuxClick={(event) => handleLabelAuxClick(event, node)}
              title={labelText}
            >
              {labelText}
            </li>
          </ul>
          {node.isOpen ? (
            <ul style={nestedStyle}>
              {node.isLoadingChildren ? (
                <li style={LOADING_STYLE}>Loading…</li>
              ) : node.children.length > 0 ? (
                node.children.map((child) => renderNode(child, depth + 1))
              ) : node.hasLoadedChildren ? (
                <li style={LOADING_STYLE}>No child items found.</li>
              ) : null}
            </ul>
          ) : null}
        </li>
      );
    },
    [handleIndicatorClick, handleLabelAuxClick, handleLabelClick, handleLabelDoubleClick],
  );

  const modalNode = useMemo(() => {
    if (!modalNodeId) {
      return null;
    }
    const node = findNode(treeNodes, modalNodeId);
    return node;
  }, [modalNodeId, treeNodes]);

  return (
    <div style={{ marginTop: "24px" }}>
      <h2 className="h5" style={{ marginBottom: "12px" }}>
        Storage Tree
      </h2>
      {initialError ? <div style={STATUS_STYLE}>{initialError}</div> : null}
      {statusMessage ? (
        <div style={statusIsError ? STATUS_STYLE : INFO_STYLE}>{statusMessage}</div>
      ) : null}
      <ul style={createNestedListStyle(0)}>
        {initialLoading ? (
          <li style={LOADING_STYLE}>Loading tree…</li>
        ) : treeNodes.length === 0 ? (
          <li style={LOADING_STYLE}>No containment data available.</li>
        ) : (
          treeNodes.map((node) => renderNode(node, 0))
        )}
      </ul>
      {modalNodeId && modalNode ? (
        <div style={MODAL_OVERLAY_STYLE}>
          <div style={MODAL_CARD_STYLE}>
            <div style={MODAL_HEADER_STYLE}>
              <label htmlFor="treeview-name" style={{ flex: 1, marginBottom: 0 }}>
                Rename item:
              </label>
              <div style={MODAL_ACTION_GROUP_STYLE}>
                <span
                  role="button"
                  aria-label="Delete item"
                  style={{
                    ...MODAL_ICON_BUTTON_STYLE,
                    opacity: modalBusy ? 0.4 : 1,
                    pointerEvents: modalBusy ? "none" : "auto",
                  }}
                  onClick={handleDelete}
                  title="Mark item as deleted"
                >
                  🗑️
                </span>
                <span
                  role="button"
                  aria-label="Save changes"
                  style={{
                    ...MODAL_ICON_BUTTON_STYLE,
                    opacity: modalBusy ? 0.4 : 1,
                    pointerEvents: modalBusy ? "none" : "auto",
                  }}
                  onClick={handleSave}
                  title="Save changes"
                >
                  💾
                </span>
                <span
                  role="button"
                  aria-label="Cancel"
                  style={MODAL_ICON_BUTTON_STYLE}
                  onClick={closeModal}
                  title="Cancel and close"
                >
                  ❌
                </span>
              </div>
            </div>
            <div style={{ ...MODAL_LINE_STYLE, marginBottom: "16px" }}>
              <input
                id="treeview-name"
                type="text"
                className="form-control"
                value={modalName}
                onChange={(event) => setModalName(event.target.value)}
                disabled={modalBusy}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    handleSave();
                  }
                }}
              />
            </div>
            {pinnedSuggestion ? (
              <div style={MODAL_LINE_STYLE}>
                <span
                  role="button"
                  aria-label="Move into pinned item"
                  style={{
                    ...MODAL_ICON_BUTTON_STYLE,
                    opacity: modalBusy ? 0.4 : 1,
                    pointerEvents: modalBusy ? "none" : "auto",
                  }}
                  onClick={handleMoveIntoPinned}
                  title="Move into the most recently pinned item"
                >
                  ↪️📌
                </span>
                <span
                  role="link"
                  style={MODAL_LINK_STYLE}
                  onClick={() => openItemInNewTab({
                    id: pinnedSuggestion.id,
                    name: pinnedSuggestion.name,
                    slug: pinnedSuggestion.slug,
                  })}
                  title="Open pinned item in a new tab"
                >
                  {pinnedSuggestion.name}
                </span>
              </div>
            ) : null}
            {modalError ? <div style={STATUS_STYLE}>{modalError}</div> : null}
            {modalNode ? (
              <div style={{ marginTop: "8px", color: "#555" }}>
                <div>ID: {modalNode.data.id}</div>
                <div>Slug: {modalNode.data.slug || "(none)"}</div>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default TreeView;
