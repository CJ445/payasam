import { useCallback, useEffect, useState } from 'react'
import { fetchCost } from './api.js'

// Separate from App.jsx's POLL_INTERVAL_MS on purpose: cost is derived
// from replica counts and resource requests, which change far less
// often than health/impact -- no need to poll as tightly.
const COST_POLL_INTERVAL_MS = 5000

function usd(value) {
  return `$${value.toFixed(value < 1 ? 4 : 2)}`
}

function CostStatCard({ value, label, sub }) {
  return (
    <div className="stat-card">
      <div className="stat-value">{value}</div>
      <div className="stat-name">{label}</div>
      {sub && <div className="stat-tag">{sub}</div>}
    </div>
  )
}

function ServiceCostTable({ services }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Service</th>
          <th>Replicas</th>
          <th>CPU req (cores)</th>
          <th>Mem req (GiB)</th>
          <th>Hourly</th>
          <th>Monthly (projected)</th>
          <th>vs. baseline</th>
        </tr>
      </thead>
      <tbody>
        {services.map((s) => {
          const delta = s.optimization_opportunity.delta_hourly_usd
          return (
            <tr key={s.service}>
              <td>{s.service}</td>
              <td>{s.replicas}</td>
              <td>{s.cpu_request_cores}</td>
              <td>{s.memory_request_gib}</td>
              <td>{usd(s.hourly_usd)}</td>
              <td>{usd(s.monthly_usd)}</td>
              <td className={delta > 0 ? 'cost-delta-up' : delta < 0 ? 'cost-delta-down' : ''}>
                {delta === 0 ? '—' : `${delta > 0 ? '+' : ''}${usd(delta)}/hr`}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// Native <details>/<summary> rather than a hand-rolled toggle: free
// keyboard/screen-reader support, and no extra state to wire up for
// something this simple.
function CostExplainer({ data }) {
  if (!data) return null
  const { pricing_snapshot: pricing, services, cluster_total: total, optimization_opportunity: opportunity } = data

  return (
    <details className="cost-explainer">
      <summary>How these numbers are calculated</summary>

      <div className="cost-explainer-body">
        <h4>1. Where the price rates come from</h4>
        <p className="muted small">
          A static, dated snapshot of {pricing.product}'s public pricing in {pricing.region} (source: Google
          Cloud's published pricing, snapshot dated {pricing.snapshot_date}, not a live feed):
        </p>
        <ul className="cost-explainer-list">
          <li>CPU: {usd(pricing.vcpu_hour)} per vCPU per hour</li>
          <li>Memory: {usd(pricing.memory_gib_hour)} per GiB per hour</li>
        </ul>
        <p className="muted small">
          This billing model was picked deliberately: GKE Autopilot bills per-Pod resource <em>requests</em>
          directly, so <code>replicas × requests × rate</code> is a real GCP formula — not an approximation
          built on GKE Standard's node-packing, which this project has no data to model honestly.
        </p>

        <h4>2. Where the replica counts and requests come from</h4>
        <p className="muted small">
          Read live from the Kubernetes API for each Deployment — the same replica count{' '}
          <code>faults_control.get_replicas()</code> already used for fault status, plus each container's
          CPU/memory <code>resources.requests</code>. Not usage metrics: this project doesn't collect CPU/memory
          utilization, only requests and live replica counts, so that's all this models.
        </p>

        <h4>3. Per-service formula</h4>
        <p className="muted small">
          <code>hourly cost = replicas × (cpu_cores × {usd(pricing.vcpu_hour)} + memory_gib × {usd(pricing.memory_gib_hour)})</code>
        </p>
        <table className="data-table">
          <thead>
            <tr>
              <th>Service</th>
              <th>Formula</th>
              <th>= Hourly</th>
            </tr>
          </thead>
          <tbody>
            {services.map((s) => (
              <tr key={s.service}>
                <td>{s.service}</td>
                <td className="muted small">
                  {s.replicas} × ({s.cpu_request_cores} × {pricing.vcpu_hour} + {s.memory_request_gib} × {pricing.memory_gib_hour})
                </td>
                <td>{usd(s.hourly_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h4>4. Cluster total</h4>
        <p className="muted small">
          Sum of every priced service's hourly cost: {services.map((s) => usd(s.hourly_usd)).join(' + ')} ={' '}
          <strong>{usd(total.hourly_usd)}/hr</strong>. Daily and monthly figures are that hourly rate × 24 and
          × 730 (GCP's own standard monthly-hours convention) — a projection assuming today's replica counts
          hold steady, not a forecast.
        </p>

        <h4>5. Optimization opportunity</h4>
        <p className="muted small">
          Each service's current modeled cost minus its cost at a "healthy baseline" replica count (this
          project's normal, no-fault state — currently 1 replica per service). Summed across services:{' '}
          <strong>{opportunity.delta_hourly_usd > 0 ? '+' : ''}{usd(opportunity.delta_hourly_usd)}/hr</strong>.
          Positive means something is scaled above its normal replica count right now (e.g. a fault-induced
          autoscale) and that extra capacity is what's driving cost up; negative means a service is scaled
          below baseline (e.g. taken down to 0 replicas during an outage), which is a real cost saving but not
          one you'd want.
        </p>
      </div>
    </details>
  )
}

export default function CostDashboard({ onBack }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const result = await fetchCost()
      setData(result)
      setLastUpdated(new Date())
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, COST_POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [refresh])

  const opportunity = data?.optimization_opportunity

  return (
    <div className="app cost-dashboard">
      <header className="app-header">
        <h1>Payasam — Cost Intelligence</h1>
        <div className="header-right">
          {lastUpdated && <span className="muted small">updated {lastUpdated.toLocaleTimeString()}</span>}
          <button className="button-secondary" onClick={onBack}>← Back to dashboard</button>
        </div>
      </header>

      {error && <div className="error-banner">Error: {error}</div>}

      <section className="panel">
        <p className="muted small">
          <strong>Estimated GCP cost (modeled, not a real bill).</strong>{' '}
          {data && (
            <>
              Priced against {data.pricing_snapshot.product}, {data.pricing_snapshot.region}, snapshot dated{' '}
              {data.pricing_snapshot.snapshot_date}. Computed live from each service's actual replica count
              and container CPU/memory requests — not from usage metrics, which this project does not collect.
            </>
          )}
        </p>

        {!data ? (
          <p className="muted">Loading…</p>
        ) : (
          <>
            <div className="stat-grid">
              <CostStatCard value={usd(data.cluster_total.hourly_usd)} label="Cluster cost / hour" />
              <CostStatCard value={usd(data.cluster_total.daily_usd)} label="Cluster cost / day (projected)" />
              <CostStatCard value={usd(data.cluster_total.monthly_usd)} label="Cluster cost / month (projected)" />
              {opportunity && (
                <CostStatCard
                  value={`${opportunity.delta_hourly_usd > 0 ? '+' : ''}${usd(opportunity.delta_hourly_usd)}/hr`}
                  label="Optimization opportunity"
                  sub={
                    opportunity.delta_hourly_usd > 0
                      ? 'above healthy baseline replica counts'
                      : opportunity.delta_hourly_usd < 0
                        ? 'below baseline (e.g. a service scaled to 0)'
                        : 'at baseline'
                  }
                />
              )}
            </div>

            <h3>Per-service breakdown</h3>
            <ServiceCostTable services={data.services} />

            {data.errors.length > 0 && (
              <p className="muted small">
                Not priced: {data.errors.map((e) => `${e.service} (${e.note})`).join('; ')}
              </p>
            )}

            <CostExplainer data={data} />
          </>
        )}
      </section>
    </div>
  )
}
