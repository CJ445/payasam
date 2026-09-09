// Thin fetch wrapper against payasam-backend. Always calls relative
// '/api/...' paths -- nginx.conf (deployed) and vite.config.js's dev
// proxy (local `npm run dev`) both forward these to the real
// payasam-backend Service, so this file never needs to know which
// environment it's running in.
const BASE = '/api'

async function getJson(path) {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body?.detail?.message || body?.error || `${path} -> HTTP ${res.status}`)
  }
  return res.json()
}

async function postJson(path) {
  const res = await fetch(`${BASE}${path}`, { method: 'POST' })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(body?.detail?.message || body?.detail?.error || `${path} -> HTTP ${res.status}`)
  }
  return body
}

export const fetchStatus = () => getJson('/status')
export const fetchImpact = (windowMinutes = 5) => getJson(`/impact?window_minutes=${windowMinutes}`)
export const fetchRemediation = () => getJson('/remediation')
export const injectFault = (scenario) => postJson(`/faults/inject?scenario=${encodeURIComponent(scenario)}`)
export const recoverSystem = () => postJson('/faults/recover')
export const generateTraffic = (count = 5) => postJson(`/traffic/generate?count=${count}`)
