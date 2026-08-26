const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || body.error?.message || detail;
    } catch {
      // ignore
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  health: () => request("/health"),
  generateSimulation: (records = 300, seed) =>
    request(`/simulation/generate?records=${records}${seed ? `&seed=${seed}` : ""}`, { method: "POST" }),
  getMetrics: () => request("/metrics"),
  listPayments: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/payments${qs ? `?${qs}` : ""}`);
  },
  getPayment: (id) => request(`/payments/${id}`),
  getQueue: () => request("/recovery/queue"),
  approve: (id) => request(`/recovery/${id}/approve`, { method: "POST" }),
  reject: (id) => request(`/recovery/${id}/reject`, { method: "POST" }),
  escalate: (id) => request(`/recovery/${id}/escalate`, { method: "POST" }),
  stop: (id) => request(`/recovery/${id}/stop`, { method: "POST" }),
  listAudit: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/audit${qs ? `?${qs}` : ""}`);
  },
  getEvaluation: () => request("/evaluation"),
};
