// Pure, framework-free logic for the live activity feed -- separated
// from App.jsx specifically so it's unit-testable without a rendering
// environment (no jsdom/React Testing Library needed, matching the
// project's general preference for testing pure logic directly). App.jsx
// owns all the React state/timing around this; this file only owns "what
// an event looks like" and "how the list grows."

export const MAX_EVENTS = 20

/**
 * @param {string} message
 * @param {'info'|'success'|'warning'|'error'} kind
 * @param {() => Date} now injectable clock, defaults to real time --
 *   passed explicitly in tests so assertions don't depend on wall-clock
 *   timing.
 */
export function buildEvent(message, kind = 'info', now = () => new Date()) {
  const time = now()
  return {
    id: `${time.getTime()}-${Math.random().toString(36).slice(2, 8)}`,
    time,
    message,
    kind,
  }
}

/**
 * Prepends a new event (newest first) and caps the list length so the
 * feed can't grow unbounded over a long-running demo session.
 */
export function appendEvent(events, newEvent, maxLen = MAX_EVENTS) {
  return [newEvent, ...events].slice(0, maxLen)
}
