const base = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
export class ApiFailure extends Error {
  constructor(kind, message, status = null, code = null) {
    super(message); Object.assign(this, { kind, status, code });
  }
}
async function request(path, method = 'GET', body) {
  let response;
  try {
    response = await fetch(`${base}${path}`, {
      method, ...(body === undefined ? {} : {
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      }),
    });
  } catch {
    throw new ApiFailure('network', 'Connection lost. Request completion is uncertain. Refresh committed state before another change.');
  }
  let data;
  try { data = await response.json(); } catch {
    throw new ApiFailure('protocol', 'The server response could not be read. Refresh committed state before another change.', response.status);
  }
  if (!response.ok) {
    throw new ApiFailure('http', response.status >= 500 ? 'The server could not complete the operation.' :
      data.error?.message || 'Request failed.', response.status, data.error?.code);
  }
  if (!data.session_id || !data.state?.warehouse) {
    throw new ApiFailure('protocol', 'The server returned an incomplete state. Refresh committed state before another change.');
  }
  return data;
}
const sessionPath = id => `/sessions/${encodeURIComponent(id)}`;
export const api = {
  createSession: () => request('/sessions', 'POST'),
  refresh: id => request(`${sessionPath(id)}/state`),
  createOrder: (id, body) => request(`${sessionPath(id)}/orders`, 'POST', body),
  plan: id => request(`${sessionPath(id)}/plan`, 'POST'),
  execute: id => request(`${sessionPath(id)}/execute`, 'POST'),
  block: (id, cell) => request(`${sessionPath(id)}/blocked-cells`, 'POST', cell),
  unblock: (id, cell) => request(`${sessionPath(id)}/blocked-cells/${cell.x}/${cell.y}`, 'DELETE'),
  reset: id => request(`${sessionPath(id)}/reset`, 'POST'),
};
