from cost import compute_service_cost, cluster_total, optimization_opportunity, PRICING_SNAPSHOT


def test_compute_service_cost_matches_formula():
    result = compute_service_cost(replicas=2, cpu_request_cores=0.5, memory_request_gib=1.0)
    expected_hourly = 2 * (0.5 * PRICING_SNAPSHOT["vcpu_hour"] + 1.0 * PRICING_SNAPSHOT["memory_gib_hour"])
    assert result["hourly_usd"] == round(expected_hourly, 6)
    assert result["replicas"] == 2


def test_compute_service_cost_zero_replicas_is_zero():
    result = compute_service_cost(replicas=0, cpu_request_cores=0.5, memory_request_gib=1.0)
    assert result["hourly_usd"] == 0
    assert result["monthly_usd"] == 0


def test_monthly_is_hourly_times_730():
    result = compute_service_cost(replicas=1, cpu_request_cores=1.0, memory_request_gib=2.0)
    assert result["monthly_usd"] == round(result["hourly_usd"] * 730, 2)


def test_optimization_opportunity_positive_when_scaled_above_baseline():
    opp = optimization_opportunity(current_replicas=4, baseline_replicas=1, cpu_request_cores=0.5, memory_request_gib=1.0)
    assert opp["delta_hourly_usd"] > 0
    assert opp["current_replicas"] == 4
    assert opp["baseline_replicas"] == 1


def test_optimization_opportunity_negative_when_scaled_below_baseline():
    opp = optimization_opportunity(current_replicas=0, baseline_replicas=1, cpu_request_cores=0.5, memory_request_gib=1.0)
    assert opp["delta_hourly_usd"] < 0


def test_optimization_opportunity_zero_at_baseline():
    opp = optimization_opportunity(current_replicas=1, baseline_replicas=1, cpu_request_cores=0.5, memory_request_gib=1.0)
    assert opp["delta_hourly_usd"] == 0


def test_cluster_total_sums_services():
    a = compute_service_cost(replicas=1, cpu_request_cores=0.5, memory_request_gib=1.0)
    b = compute_service_cost(replicas=2, cpu_request_cores=0.25, memory_request_gib=0.5)
    total = cluster_total([a, b])
    assert total["hourly_usd"] == round(a["hourly_usd"] + b["hourly_usd"], 6)


def test_cluster_total_empty_is_zero():
    total = cluster_total([])
    assert total["hourly_usd"] == 0
