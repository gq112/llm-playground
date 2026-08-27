from datetime import datetime, timedelta

import pytest

from playground.app import MetricStore


def _snapshot(timestamp: datetime, **values: float) -> dict:
    return {"timestamp": timestamp.isoformat(), **values}


def test_vllm_prefix_cache_hit_rate_uses_counter_deltas() -> None:
    store = MetricStore()
    now = datetime.now()
    store.history.append(
        _snapshot(
            now,
            **{
                "vllm:prefix_cache_hits": 100,
                "vllm:prefix_cache_queries": 200,
            },
        )
    )

    derived = store._derive_rates(
        _snapshot(
            now + timedelta(seconds=1),
            **{
                "vllm:prefix_cache_hits": 130,
                "vllm:prefix_cache_queries": 240,
            },
        )
    )

    assert derived["observability:prefix_cache_hit_rate"] == pytest.approx(0.75)


def test_vllm_prefix_cache_hit_rate_is_a_gap_while_idle() -> None:
    store = MetricStore()
    now = datetime.now()
    store.history.append(
        _snapshot(
            now,
            **{
                "vllm:prefix_cache_hits": 100,
                "vllm:prefix_cache_queries": 200,
                "observability:prefix_cache_hit_rate": 0.5,
            },
        )
    )

    derived = store._derive_rates(
        _snapshot(
            now + timedelta(seconds=1),
            **{
                "vllm:prefix_cache_hits": 100,
                "vllm:prefix_cache_queries": 200,
            },
        )
    )

    assert "observability:prefix_cache_hit_rate" not in derived


def test_flat_metrics_aggregate_labeled_counter_series_instead_of_last_value() -> None:
    parsed = MetricStore.parse_prometheus(
        """
# TYPE vllm:prompt_tokens_total counter
vllm:prompt_tokens_total{model_name="model-a",engine="0"} 100
vllm:prompt_tokens_total{model_name="model-b",engine="1"} 300
"""
    )

    entry = parsed["vllm:prompt_tokens"]
    assert entry["value"] == 400
    assert entry["aggregation"] == "sum"
    assert entry["series_count"] == 2
    assert len(entry["series"]) == 2


def test_kv_eviction_metrics_are_parsed_with_runtime_names() -> None:
    parsed = MetricStore.parse_prometheus(
        """
# TYPE vllm:kv_block_idle_before_evict_seconds histogram
vllm:kv_block_idle_before_evict_seconds_bucket{le="1"} 2
vllm:kv_block_idle_before_evict_seconds_bucket{le="+Inf"} 3
vllm:kv_block_idle_before_evict_seconds_sum 4
vllm:kv_block_idle_before_evict_seconds_count 3
# TYPE sglang:evicted_tokens_total counter
sglang:evicted_tokens_total 42
"""
    )

    assert parsed["vllm:kv_block_idle_before_evict_seconds_count"]["value"] == 3
    assert parsed["vllm:kv_block_idle_before_evict_seconds"]["type"] == "histogram"
    assert parsed["sglang:evicted_tokens"]["value"] == 42


def test_histogram_percentiles_match_prometheus_interpolation_rules() -> None:
    percentiles = MetricStore._percentiles([
        (1.0, 80),
        (2.0, 79),  # Non-monotonic scrape noise is repaired to the previous count.
        (3.0, 100),
        (float("inf"), 100),
    ])

    assert percentiles["p90"] == pytest.approx(2.5)
    assert percentiles["p99"] == pytest.approx(2.95)


@pytest.mark.parametrize("buckets", [
    [(1.0, 1)],
    [(1.0, 1), (2.0, 2)],
    [(1.0, 0), (float("inf"), 0)],
])
def test_histogram_percentiles_reject_incomplete_or_empty_buckets(buckets: list) -> None:
    assert MetricStore._percentiles(buckets) == {}


def test_cumulative_metrics_are_grouped_by_model_and_engine() -> None:
    store = MetricStore(cumulative_ttl_seconds=300)
    now = datetime.now()
    _, samples = store._parse_prometheus_payload(
        """
# TYPE sglang:num_requests_total counter
sglang:num_requests_total{model_name="model-a",engine_type="unified",tp_rank="0",finish_reason="stop"} 10
sglang:num_requests_total{model_name="model-a",engine_type="unified",tp_rank="1",finish_reason="length"} 2
sglang:num_requests_total{model_name="model-b",engine_type="unified",tp_rank="0",finish_reason="stop"} 7
# TYPE sglang:prompt_tokens_total counter
sglang:prompt_tokens_total{model_name="model-a",engine_type="unified",tp_rank="0"} 100
sglang:prompt_tokens_total{model_name="model-a",engine_type="unified",tp_rank="1"} 200
# TYPE sglang:generation_tokens_total counter
sglang:generation_tokens_total{model_name="model-a",engine_type="unified",tp_rank="0"} 30
sglang:generation_tokens_total{model_name="model-a",engine_type="unified",tp_rank="1"} 40
# TYPE sglang:cached_tokens_total counter
sglang:cached_tokens_total{model_name="model-a",engine_type="unified",cache_source="device"} 80
sglang:cached_tokens_total{model_name="model-a",engine_type="unified",cache_source="host"} 40
# TYPE sglang:cache_hit_rate gauge
sglang:cache_hit_rate{model_name="model-a",engine_type="unified",tp_rank="0"} 0.5
sglang:cache_hit_rate{model_name="model-a",engine_type="unified",tp_rank="1"} 0.7
# TYPE sglang:evicted_tokens_total counter
sglang:evicted_tokens_total{model_name="model-a",engine_type="unified",tp_rank="0"} 6
sglang:evicted_tokens_total{model_name="model-a",engine_type="unified",tp_rank="1"} 4
"""
    )

    store._update_cumulative_groups(samples, now)
    groups = store.get_cumulative_groups(now)

    assert [(group["model_name"], group["engine_type"]) for group in groups] == [
        ("model-a", "unified"),
        ("model-b", "unified"),
    ]
    assert groups[0]["values"] == {
        "requests": 12,
        "input_tokens": 300,
        "output_tokens": 70,
    }
    assert groups[1]["values"] == {"requests": 7}
    assert "cache_hit_rate" not in groups[0]
    assert "kv_evictions_per_sample" not in groups[0]


def test_cumulative_counter_reset_continues_and_stale_group_expires() -> None:
    store = MetricStore(cumulative_ttl_seconds=5)
    now = datetime.now()

    def update(value: int, timestamp: datetime) -> None:
        store._update_cumulative_groups([{
            "key": "vllm:request_success",
            "value": value,
            "labels": {"model_name": "model-a", "engine_type": "v1"},
        }], timestamp)

    update(10, now)
    update(15, now + timedelta(seconds=1))
    update(3, now + timedelta(seconds=2))

    groups = store.get_cumulative_groups(now + timedelta(seconds=2))
    assert groups[0]["values"]["requests"] == 18
    assert store.get_cumulative_groups(now + timedelta(seconds=8)) == []


def test_partial_rank_reset_is_handled_before_group_aggregation() -> None:
    store = MetricStore()
    now = datetime.now()

    def samples(rank_0: int, rank_1: int) -> list:
        return [
            {
                "key": "vllm:request_success",
                "value": rank_0,
                "labels": {"model_name": "model-a", "engine_type": "v1", "rank": "0"},
            },
            {
                "key": "vllm:request_success",
                "value": rank_1,
                "labels": {"model_name": "model-a", "engine_type": "v1", "rank": "1"},
            },
        ]

    store._update_cumulative_groups(samples(100, 100), now)
    store._update_cumulative_groups(samples(0, 110), now + timedelta(seconds=1))

    assert store.get_cumulative_groups(now + timedelta(seconds=1))[0]["values"]["requests"] == 210


def test_vllm_eviction_histogram_count_is_not_exposed_as_evictions() -> None:
    store = MetricStore()
    now = datetime.now()
    _, samples = store._parse_prometheus_payload(
        """
# TYPE vllm:kv_block_idle_before_evict_seconds histogram
vllm:kv_block_idle_before_evict_seconds_count{model_name="model-a",engine_type="v1",tp_rank="0"} 2
vllm:kv_block_idle_before_evict_seconds_count{model_name="model-a",engine_type="v1",tp_rank="1"} 3
"""
    )

    store._update_cumulative_groups(samples, now)
    groups = store.get_cumulative_groups(now)

    assert groups == []


def test_cache_only_group_is_not_exposed_as_cumulative_overview() -> None:
    store = MetricStore()
    now = datetime.now()

    store._update_cumulative_groups([
        {
            "key": "sglang:cache_hit_rate",
            "value": 0.5,
            "labels": {"engine_type": "sglang"},
        },
        {
            "key": "sglang:evicted_tokens",
            "value": 42,
            "labels": {"engine_type": "sglang"},
        },
    ], now)

    assert store.get_cumulative_groups(now) == []


def test_grouped_vllm_hit_rate_uses_per_series_counter_deltas() -> None:
    store = MetricStore()
    now = datetime.now()

    store._update_cumulative_groups([
        {"key": "vllm:prefix_cache_hits", "value": 100, "labels": {"model_name": "model-a"}},
        {"key": "vllm:prefix_cache_queries", "value": 200, "labels": {"model_name": "model-a"}},
    ], now)
    store.history.append(_snapshot(now))
    store._update_cumulative_groups([
        {"key": "vllm:prefix_cache_hits", "value": 130, "labels": {"model_name": "model-a"}},
        {"key": "vllm:prefix_cache_queries", "value": 240, "labels": {"model_name": "model-a"}},
    ], now + timedelta(seconds=2))

    derived = store._derive_rates(_snapshot(now + timedelta(seconds=2)))
    assert derived["observability:prefix_cache_hit_rate"] == pytest.approx(0.75)


def test_grouped_sglang_evictions_use_counter_delta_per_sample() -> None:
    store = MetricStore()
    now = datetime.now()

    store._update_cumulative_groups([
        {"key": "sglang:evicted_tokens", "value": 10, "labels": {"model_name": "model-a"}},
    ], now)
    store.history.append(_snapshot(now))
    store._update_cumulative_groups([
        {"key": "sglang:evicted_tokens", "value": 14, "labels": {"model_name": "model-a"}},
    ], now + timedelta(seconds=2))

    derived = store._derive_rates(_snapshot(now + timedelta(seconds=2)))
    assert derived["observability:kv_evictions_per_sample"] == 4


def test_sglang_hit_rate_uses_token_counter_deltas_not_batch_gauge_average() -> None:
    store = MetricStore()
    now = datetime.now()

    store._update_cumulative_groups([
        {"key": "sglang:prompt_tokens", "value": 100, "labels": {"model_name": "model-a", "rank": "0"}},
        {"key": "sglang:prompt_tokens", "value": 100, "labels": {"model_name": "model-a", "rank": "1"}},
        {"key": "sglang:cached_tokens", "value": 20, "labels": {"model_name": "model-a", "source": "device"}},
        {"key": "sglang:cached_tokens", "value": 30, "labels": {"model_name": "model-a", "source": "host"}},
        {"key": "sglang:cache_hit_rate", "value": 0.9, "labels": {"model_name": "model-a", "rank": "0"}},
        {"key": "sglang:cache_hit_rate", "value": 0.1, "labels": {"model_name": "model-a", "rank": "1"}},
    ], now)
    store.history.append(_snapshot(now))
    store._update_cumulative_groups([
        {"key": "sglang:prompt_tokens", "value": 150, "labels": {"model_name": "model-a", "rank": "0"}},
        {"key": "sglang:prompt_tokens", "value": 150, "labels": {"model_name": "model-a", "rank": "1"}},
        {"key": "sglang:cached_tokens", "value": 50, "labels": {"model_name": "model-a", "source": "device"}},
        {"key": "sglang:cached_tokens", "value": 40, "labels": {"model_name": "model-a", "source": "host"}},
        {"key": "sglang:cache_hit_rate", "value": 0.0, "labels": {"model_name": "model-a", "rank": "0"}},
        {"key": "sglang:cache_hit_rate", "value": 0.0, "labels": {"model_name": "model-a", "rank": "1"}},
    ], now + timedelta(seconds=2))

    derived = store._derive_rates(_snapshot(now + timedelta(seconds=2)))
    assert derived["observability:sglang_cache_hit_rate"] == pytest.approx(0.4)


def test_history_uses_eviction_delta_for_each_sample() -> None:
    store = MetricStore()
    now = datetime.now()
    store.metrics = {
        "sglang:evicted_tokens": {"value": 14, "type": "counter", "labels": ""},
    }
    store.history.append(_snapshot(now, **{"sglang:evicted_tokens": 10}))

    derived = store._derive_rates(
        _snapshot(now + timedelta(seconds=2), **{"sglang:evicted_tokens": 14})
    )

    assert derived["observability:kv_evictions_per_sample"] == 4


def test_history_merges_dense_recent_and_sparse_long_term_samples() -> None:
    store = MetricStore(
        interval=1,
        history_size=2,
        history_retention_seconds=3600,
        archive_interval_seconds=30,
    )
    now = datetime.now()
    for offset in (0, 10, 30, 60):
        store._append_snapshot(now + timedelta(seconds=offset), {"metric": float(offset)})

    history = store.get_history(now=now + timedelta(seconds=60))
    assert [point["metric"] for point in history] == [0, 30, 60]

    recent = store.get_history(45, now=now + timedelta(seconds=60))
    assert [point["metric"] for point in recent] == [30, 60]


def test_histogram_interval_survives_one_rank_counter_reset() -> None:
    store = MetricStore()
    now = datetime.now()
    first_metrics, first_samples = store._parse_prometheus_payload(
        """
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{model_name="model-a",rank="0",le="1"} 100
vllm:time_to_first_token_seconds_bucket{model_name="model-a",rank="0",le="+Inf"} 100
vllm:time_to_first_token_seconds_sum{model_name="model-a",rank="0"} 50
vllm:time_to_first_token_seconds_count{model_name="model-a",rank="0"} 100
vllm:time_to_first_token_seconds_bucket{model_name="model-a",rank="1",le="1"} 0
vllm:time_to_first_token_seconds_bucket{model_name="model-a",rank="1",le="+Inf"} 100
vllm:time_to_first_token_seconds_sum{model_name="model-a",rank="1"} 200
vllm:time_to_first_token_seconds_count{model_name="model-a",rank="1"} 100
"""
    )
    store.metrics = first_metrics
    store._update_histogram_intervals(first_samples)
    store._append_snapshot(now)

    second_metrics, second_samples = store._parse_prometheus_payload(
        """
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{model_name="model-a",rank="0",le="1"} 0
vllm:time_to_first_token_seconds_bucket{model_name="model-a",rank="0",le="+Inf"} 0
vllm:time_to_first_token_seconds_sum{model_name="model-a",rank="0"} 0
vllm:time_to_first_token_seconds_count{model_name="model-a",rank="0"} 0
vllm:time_to_first_token_seconds_bucket{model_name="model-a",rank="1",le="1"} 10
vllm:time_to_first_token_seconds_bucket{model_name="model-a",rank="1",le="+Inf"} 110
vllm:time_to_first_token_seconds_sum{model_name="model-a",rank="1"} 205
vllm:time_to_first_token_seconds_count{model_name="model-a",rank="1"} 110
"""
    )
    store.metrics = second_metrics
    store._update_histogram_intervals(second_samples)
    store._append_snapshot(now + timedelta(seconds=1))

    histogram = store.metrics["vllm:time_to_first_token_seconds"]
    assert histogram["avg"] == pytest.approx(0.5)
    assert histogram["p90"] == pytest.approx(0.9)
    assert histogram["p99"] == pytest.approx(0.99)
