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
