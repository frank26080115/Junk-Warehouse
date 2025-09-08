export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5000";

export async function ping(): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/health`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
