// Thin client for the backend HTTP API.
// Configure base URL via NEXT_PUBLIC_API_URL or NEXT_PUBLIC_SIM_API.

const BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  process.env.NEXT_PUBLIC_SIM_API ||
  'http://127.0.0.1:8000';

async function jsonRequest(path, init = {}) {
  const headers = { ...(init.headers || {}) };
  if (init.body && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body?.error || body?.detail || '';
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status} ${res.statusText}${detail ? ` — ${detail}` : ''}`);
  }
  return res.json();
}

export const api = {
  algorithms: () => jsonRequest('/api/algorithms'),
  days: ({ minClients = 5, head = 50 } = {}) =>
    jsonRequest(`/api/days?min_clients=${minClients}&head=${head}`),
  run: ({ date, ruta, algo }) =>
    jsonRequest('/api/run', {
      method: 'POST',
      body: JSON.stringify({ date, ruta, algo }),
    }),
  bench: ({ algos, maxCases = 30, seed = 42, minClients = 5 }) =>
    jsonRequest('/api/bench', {
      method: 'POST',
      body: JSON.stringify({ algos, max_cases: maxCases, seed, min_clients: minClients }),
    }),
  routes: () => jsonRequest('/routes'),
  routeDetail: (date, ruta) => jsonRequest(`/routes/${date}/${ruta}`),
};

export const SIM_API_BASE = BASE;
