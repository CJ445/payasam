import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchStatus, fetchImpact, fetchRemediation, injectFault, recoverSystem, generateTraffic } from './api.js'
import { appendEvent, buildEvent } from './activityLog.js'
import { appendHistoryPoint } from './impactHistory.js'

// The standard local OpenObserve address this whole project assumes
// (see SETUP.md/DEMO.md) -- these link to real, verified page paths
// (checked directly against a running OpenObserve instance: /web/logs,
// /web/traces, /web/alerts all return 200), not a claimed pre-filtered
// deep link into a specific query, which OpenObserve's URL scheme
// doesn't document clearly enough to promise.
const OPENOBSERVE_URL = 'http://localhost:5080'

const POLL_INTERVAL_MS = 3000
const IMPACT_WINDOW_MINUTES = 5
const PULSE_DURATION_MS = 900

const FAULT_BUTTONS = [
  { scenario: 'payment_latency', label: 'Inject Payment Latency' },
  { scenario: 'payment_errors', label: 'Inject Payment Errors' },
  { scenario: 'database_latency', label: 'Inject Database Latency' },
  { scenario: 'payment_unavailable', label: 'Take Payment Service Down' },
]

// Maps this app's semantic "kind" values to the project's four reserved
// status colors (good/warning/serious/critical -- see the dataviz design
// system). 'info' intentionally maps to no dot: it's a narrative event,
// not a state.
const KIND_TO_STATUS = { success: 'good', warning: 'warning', error: 'critical', info: null }

// Tracks whether `value` just changed and returns true for
// PULSE_DURATION_MS afterward -- used to flash a value on screen so a
// live-polling dashboard reads as live, not static. Deliberately a
// binary flash, not a fake animated counter: the underlying data really
// did change at a discrete moment (the last poll), so a discrete flash
// is the honest representation of that, not a fabricated smooth
// transition implying continuous measurement that doesn't exist.
function usePulse(value) {
  const [pulsing, setPulsing] = useState(false)
  const prevRef = useRef(value)

  useEffect(() => {
    if (prevRef.current !== value) {
      prevRef.current = value
      setPulsing(true)
      const timer = setTimeout(() => setPulsing(false), PULSE_DURATION_MS)
      return () => clearTimeout(timer)
    }
  }, [value])

  return pulsing
}

// A small colored dot carrying status, always beside a text label --
// never the sole carrier of meaning, and text itself stays in normal ink
// rather than being recolored (dataviz design system: "text wears text
// tokens, never the status color").
function StatusDot({ status }) {
  if (!status) return null
  return <span className={`status-dot status-dot-${status}`} aria-hidden="true" />
}

function StatusPill({ status }) {
  const pulsing = usePulse(status)
  const dotStatus = { healthy: 'good', degraded: 'critical' }[status] || null
  return (
    <span className={pulsing ? 'status-pill pulse' : 'status-pill'}>
      <StatusDot status={dotStatus} />
      {status}
    </span>
  )
}

function ProgressBar({ label }) {
  // Indeterminate by design: fault injection and traffic generation
  // don't expose a real, granular progress fraction over this API (that
  // would need requests streamed one at a time -- a deliberately
  // deferred follow-up, see DECISIONS.md D017). An indeterminate bar
  // honestly represents "something real is in progress, duration
  // unknown," rather than fabricating a percentage that doesn't
  // correspond to anything actually measured.
  return (
    <div className="progress-wrap" role="status" aria-live="polite">
      <div className="progress-bar"><div className="progress-bar-fill" /></div>
      <span className="muted small">{label}</span>
    </div>
  )
}

// A 12-point-scale sparkline: the trend line itself in a de-emphasis
// hue, with the current (most recent) point marked in the accent color --
// the stat-tile trend contract from the dataviz design system. Thin
// (2px) line, rounded ends, one axis, nothing more.
function Sparkline({ points }) {
  if (points.length < 2) return null
  const width = 120
  const height = 28
  const max = Math.max(...points, 1)
  const stepX = width / (points.length - 1)
  const coords = points.map((p, i) => {
    const x = i * stepX
    const y = height - (p / max) * (height - 4) - 2
    return [x, y]
  })
  const polyline = coords.map(([x, y]) => `${x},${y}`).join(' ')
  const [lastX, lastY] = coords[coords.length - 1]
  return (
    <svg className="sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
      <polyline points={polyline} fill="none" stroke="#9ec5f4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lastX} cy={lastY} r="3" fill="#2a78d6" />
    </svg>
  )
}

function ActivityFeed({ events }) {
  return (
    <section className="panel">
      <h2>Activity</h2>
      {events.length === 0 ? (
        <p className="muted">No actions yet — click a button below to start.</p>
      ) : (
        <ul className="activity-feed">
          {events.map((e) => (
            <li key={e.id} className="activity-item">
              <span className="activity-time">{e.time.toLocaleTimeString()}</span>
              <StatusDot status={KIND_TO_STATUS[e.kind]} />
              <span className="activity-message">{e.message}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function FailureSimulator({ onInject, onRecover, onGenerateTraffic, busy, busyLabel }) {
  return (
    <section className="panel">
      <h2>Failure simulator</h2>
      <p className="muted">
        Injecting a fault alone changes no data by itself — it only takes
        effect on real requests. Generate traffic before or after
        injecting a fault to actually see it happen.
      </p>
      <div className="button-row">
        <button disabled={busy} onClick={onGenerateTraffic} className="button-primary">
          Generate test traffic (5 orders)
        </button>
        <button disabled={busy} onClick={onRecover} className="button-secondary">
          ↺ Recover system
        </button>
      </div>
      <div className="button-row" style={{ marginTop: 12 }}>
        {FAULT_BUTTONS.map(({ scenario, label }) => (
          <button key={scenario} disabled={busy} onClick={() => onInject(scenario)}>
            {label}
          </button>
        ))}
      </div>
      {busy && <ProgressBar label={busyLabel} />}
    </section>
  )
}

function TechnicalPanel({ status }) {
  if (!status) return <section className="panel"><h2>Technical</h2><p>Loading…</p></section>
  const faults = status.active_faults || {}
  return (
    <section className="panel">
      <h2>Technical</h2>
      <p className="muted small">What's the problem with the deployment right now.</p>

      <h3>Service health</h3>
      <table className="data-table">
        <tbody>
          {Object.entries(status.services).map(([service, state]) => (
            <tr key={service}>
              <td>{service}</td>
              <td><StatusDot status={state === 'healthy' ? 'good' : 'critical'} />{state}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Active faults</h3>
      {Object.keys(faults).length === 0 ? (
        <p><StatusDot status="good" />None — system is behaving normally.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr><th>Fault</th><th>Deployment</th><th>Value</th></tr>
          </thead>
          <tbody>
            {Object.entries(faults).map(([var_, info]) => (
              <tr key={var_}>
                <td><StatusDot status="serious" />{var_}</td>
                <td>{info.deployment}</td>
                <td>{info.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Investigate further</h3>
      <p className="muted small">
        These open OpenObserve's own pages for deeper evidence (raw
        logs/traces/alert history) -- they land on the general page, not
        a pre-filtered query, since that deep-link scheme isn't
        something this project has verified.
      </p>
      <div className="button-row">
        <a className="button-link" href={`${OPENOBSERVE_URL}/web/logs`} target="_blank" rel="noreferrer">Logs ↗</a>
        <a className="button-link" href={`${OPENOBSERVE_URL}/web/traces`} target="_blank" rel="noreferrer">Traces ↗</a>
        <a className="button-link" href={`${OPENOBSERVE_URL}/web/alerts`} target="_blank" rel="noreferrer">Alerts ↗</a>
      </div>
    </section>
  )
}

function StatCard({ value, label, history, tooltip, tag }) {
  const pulsing = usePulse(value)
  return (
    <div className={pulsing ? 'stat-card pulse' : 'stat-card'} title={tooltip}>
      <div className="stat-value">{value}</div>
      <div className="stat-name">{label}</div>
      {tag && <div className="stat-tag">{tag}</div>}
      {history && <Sparkline points={history} />}
    </div>
  )
}

function BusinessPanel({ impact, failedTxHistory, revenueHistory }) {
  if (!impact) return <section className="panel"><h2>Business</h2><p>Loading…</p></section>
  const { avg_transaction_value_inr: avgValue, downtime_cost_per_minute_inr: costPerMinute } = impact.assumptions
  return (
    <section className="panel">
      <h2>Business</h2>
      <p className="muted small">How this affects the business, right now.</p>
      <p className="impact-label">{impact.label}</p>
      <div className="stat-grid">
        {impact.affected_users !== null && (
          <StatCard
            value={impact.affected_users}
            label="Affected users"
            tooltip="Real count of distinct user_id values among the failed transactions below, queried live from OpenObserve -- the same user failing twice counts once"
          />
        )}
        <StatCard
          value={impact.failed_transactions}
          label={`Failed transactions (last ${impact.window_minutes} minutes)`}
          history={failedTxHistory}
          tooltip="Real count of order-service failure events in this window, queried live from OpenObserve"
        />
        <StatCard
          value={`₹${impact.estimated_revenue_at_risk_inr}`}
          label="Estimated revenue at risk"
          history={revenueHistory}
          tooltip={`${impact.failed_transactions} failed × ₹${avgValue} avg transaction value = ₹${impact.estimated_revenue_at_risk_inr}`}
        />
        <StatCard
          value={`₹${impact.estimated_downtime_cost_inr}`}
          label="Estimated downtime cost"
          tooltip={`${impact.incident_duration_minutes} min × ₹${costPerMinute}/min = ₹${impact.estimated_downtime_cost_inr}`}
        />
        <StatCard
          value={`${impact.business_criticality}/5`}
          label={`Business criticality (${impact.business_function})`}
          tag="assumption, not measured"
          tooltip="A fixed configuration value from business_metadata.yaml -- not derived from live telemetry"
        />
      </div>

      {impact.affected_users === null && (
        <p className="muted">Affected users: {impact.affected_users_note}</p>
      )}

      <div className="assumptions-box">
        <span className="assumptions-box-label">Assumptions used</span>
        <p className="muted small" style={{ margin: '4px 0 0' }}>
          Avg transaction value ₹{avgValue}, downtime cost ₹{costPerMinute}/minute.
          Incident duration measured from real telemetry: {impact.incident_duration_minutes} minutes.
        </p>
      </div>
    </section>
  )
}

function RemediationPanel({ remediation }) {
  if (!remediation) return <section className="panel"><h2>Remediation</h2><p>Loading…</p></section>
  return (
    <section className="panel">
      <h2>Remediation</h2>
      <p className="muted small">Steps for how to fix it.</p>
      {remediation.status === 'healthy' ? (
        <p><StatusDot status="good" />No action needed — all services healthy.</p>
      ) : (
        <div>
          <h3>What's wrong</h3>
          <ul className="issue-list">
            {remediation.issues.map((issue, i) => (
              <li key={i}><StatusDot status="serious" />{issue}</li>
            ))}
          </ul>
          <h3>Recommended actions</h3>
          <ul>{remediation.recommended_actions.map((action, i) => <li key={i}>{action}</li>)}</ul>
          <p className="muted small">
            Rule-based, derived directly from which fault is currently active — not a generic AI claim.
          </p>
        </div>
      )}
    </section>
  )
}

export default function App() {
  const [status, setStatus] = useState(null)
  const [impact, setImpact] = useState(null)
  const [remediation, setRemediation] = useState(null)
  const [busy, setBusy] = useState(false)
  const [busyLabel, setBusyLabel] = useState('')
  const [error, setError] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [events, setEvents] = useState([])
  const [failedTxHistory, setFailedTxHistory] = useState([])
  const [revenueHistory, setRevenueHistory] = useState([])

  const prevOverallStatus = useRef(null)
  const prevFailedTransactions = useRef(null)

  const log = useCallback((message, kind = 'info') => {
    setEvents((current) => appendEvent(current, buildEvent(message, kind)))
  }, [])

  const refresh = useCallback(async () => {
    try {
      const [s, i, r] = await Promise.all([
        fetchStatus(),
        fetchImpact(IMPACT_WINDOW_MINUTES),
        fetchRemediation(),
      ])

      // Log state transitions the *system* reports on its own, not just
      // ones the user directly caused by clicking a button -- this is
      // what makes the feed read as "the system is being observed," not
      // just "here's a log of my own clicks."
      if (prevOverallStatus.current !== null && prevOverallStatus.current !== s.overall_status) {
        log(`System status: ${prevOverallStatus.current} → ${s.overall_status}`, s.overall_status === 'healthy' ? 'success' : 'warning')
      }
      prevOverallStatus.current = s.overall_status

      if (prevFailedTransactions.current !== null && prevFailedTransactions.current !== i.failed_transactions) {
        log(`Business impact updated: ${i.failed_transactions} failed transactions, ₹${i.estimated_revenue_at_risk_inr} at risk`, i.failed_transactions > 0 ? 'warning' : 'success')
      }
      prevFailedTransactions.current = i.failed_transactions

      setFailedTxHistory((h) => appendHistoryPoint(h, i.failed_transactions))
      setRevenueHistory((h) => appendHistoryPoint(h, i.estimated_revenue_at_risk_inr))

      setStatus(s)
      setImpact(i)
      setRemediation(r)
      setLastUpdated(new Date())
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }, [log])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [refresh])

  const handleInject = async (scenario) => {
    const label = FAULT_BUTTONS.find((b) => b.scenario === scenario)?.label || scenario
    setBusy(true)
    setBusyLabel(`Injecting fault: ${label}…`)
    setError(null)
    log(`Injecting fault: ${label}`)
    try {
      await injectFault(scenario)
      log(`Fault injected: ${label}`, 'warning')
      await refresh()
    } catch (err) {
      setError(err.message)
      log(`Failed to inject fault: ${err.message}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  const handleGenerateTraffic = async () => {
    setBusy(true)
    setBusyLabel('Sending 5 real orders through the system…')
    setError(null)
    log('Generating 5 test orders…')
    try {
      const result = await generateTraffic(5)
      log(
        `Sent ${result.sent} orders: ${result.success} succeeded, ${result.failed} failed`,
        result.failed > 0 ? 'warning' : 'success',
      )
      await refresh()
    } catch (err) {
      setError(err.message)
      log(`Failed to generate traffic: ${err.message}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  const handleRecover = async () => {
    setBusy(true)
    setBusyLabel('Recovering system…')
    setError(null)
    log('Recovering system…')
    try {
      await recoverSystem()
      log('Recovery triggered', 'success')
      await refresh()
    } catch (err) {
      setError(err.message)
      log(`Failed to recover: ${err.message}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Payasam</h1>
        <div className="header-right">
          {status && <StatusPill status={status.overall_status} />}
          {lastUpdated && <span className="muted small">updated {lastUpdated.toLocaleTimeString()}</span>}
        </div>
      </header>

      {error && <div className="error-banner">Error: {error}</div>}

      <FailureSimulator
        onInject={handleInject}
        onRecover={handleRecover}
        onGenerateTraffic={handleGenerateTraffic}
        busy={busy}
        busyLabel={busyLabel}
      />

      <ActivityFeed events={events} />

      <div className="dashboard-grid">
        <TechnicalPanel status={status} />
        <BusinessPanel impact={impact} failedTxHistory={failedTxHistory} revenueHistory={revenueHistory} />
        <RemediationPanel remediation={remediation} />
      </div>
    </div>
  )
}
