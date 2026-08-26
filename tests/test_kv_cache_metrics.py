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


def test_vllm_prefix_cache_hit_rate_is_retained_while_idle() -> None:
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

    assert derived["observability:prefix_cache_hit_rate"] == pytest.approx(0.5)


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
    assert groups[0]["cache_hit_rate"] == pytest.approx(0.6)
    assert groups[0]["kv_evictions_per_sample"] is None
    assert groups[0]["kv_eviction_unit"] == "tokens"


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


def test_grouped_vllm_hit_rate_uses_counter_deltas_without_eviction_value() -> None:
    store = MetricStore()
    now = datetime.now()

    store._update_cumulative_groups([
        {"key": "vllm:prefix_cache_hits", "value": 100, "labels": {"model_name": "model-a"}},
        {"key": "vllm:prefix_cache_queries", "value": 200, "labels": {"model_name": "model-a"}},
    ], now)
    store._update_cumulative_groups([
        {"key": "vllm:prefix_cache_hits", "value": 130, "labels": {"model_name": "model-a"}},
        {"key": "vllm:prefix_cache_queries", "value": 240, "labels": {"model_name": "model-a"}},
    ], now + timedelta(seconds=2))

    group = store.get_cumulative_groups(now + timedelta(seconds=2))[0]
    assert group["cache_hit_rate"] == pytest.approx(0.75)
    assert group["kv_evictions_per_sample"] is None
    assert group["kv_eviction_unit"] is None


def test_grouped_sglang_evictions_use_counter_delta_per_sample() -> None:
    store = MetricStore()
    now = datetime.now()

    store._update_cumulative_groups([
        {"key": "sglang:evicted_tokens", "value": 10, "labels": {"model_name": "model-a"}},
    ], now)
    store._update_cumulative_groups([
        {"key": "sglang:evicted_tokens", "value": 14, "labels": {"model_name": "model-a"}},
    ], now + timedelta(seconds=2))

    group = store.get_cumulative_groups(now + timedelta(seconds=2))[0]
    assert group["kv_evictions_per_sample"] == 4
    assert group["kv_eviction_unit"] == "tokens"


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
