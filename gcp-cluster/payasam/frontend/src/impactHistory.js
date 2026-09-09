// Pure rolling-history logic for the Business panel's sparklines --
// separated from App.jsx for the same reason as activityLog.js: no
// rendering environment needed to test it.

export const MAX_HISTORY_POINTS = 30

/**
 * Appends a new numeric data point (oldest first, so it can be drawn
 * left-to-right directly) and caps the length so a long-running demo
 * session's sparkline stays a short recent window, not its entire
 * history.
 */
export function appendHistoryPoint(history, value, maxLen = MAX_HISTORY_POINTS) {
  return [...history, value].slice(-maxLen)
}
