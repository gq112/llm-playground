from datetime import datetime, timedelta
from pathlib import Path

import pytest

from playground.dcgm import DcgmStore


DCGM_SAMPLE = """
# HELP DCGM_FI_DEV_GPU_UTIL GPU utilization.
# TYPE DCGM_FI_DEV_GPU_UTIL gauge
DCGM_FI_DEV_GPU_UTIL{gpu="0",UUID="GPU-a",device="nvidia0",modelName="NVIDIA RTX 5090",Hostname="master-1"} 92
DCGM_FI_PROF_SM_ACTIVE{gpu="0",UUID="GPU-a"} 0.91
DCGM_FI_PROF_SM_OCCUPANCY{gpu="0",UUID="GPU-a"} 0.58
DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{gpu="0",UUID="GPU-a"} 0.78
DCGM_FI_PROF_DRAM_ACTIVE{gpu="0",UUID="GPU-a"} 0.42
DCGM_FI_DEV_FB_USED{gpu="0",UUID="GPU-a"} 24576
DCGM_FI_DEV_FB_FREE{gpu="0",UUID="GPU-a"} 8192
DCGM_FI_DEV_POWER_USAGE{gpu="0",UUID="GPU-a"} 410
DCGM_FI_DEV_DEC_UTIL{gpu="0",UUID="GPU-a"} 12
DCGM_FI_PROF_PCIE_TX_BYTES{gpu="0",UUID="GPU-a"} 1000000000
DCGM_FI_PROF_PCIE_RX_BYTES{gpu="0",UUID="GPU-a"} 2000000000
DCGM_FI_PROF_NVLINK_TX_BYTES{gpu="0",UUID="GPU-a"} 8000000000
DCGM_FI_PROF_NVLINK_RX_BYTES{gpu="0",UUID="GPU-a"} 12000000000
DCGM_FI_DEV_GPU_UTIL{gpu="1",UUID="GPU-b",device="nvidia1",modelName="NVIDIA RTX 5090",Hostname="master-1"} 45
DCGM_FI_PROF_SM_ACTIVE{gpu="1",UUID="GPU-b"} 0.40
DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{gpu="1",UUID="GPU-b"} 0.20
DCGM_FI_PROF_DRAM_ACTIVE{gpu="1",UUID="GPU-b"} 0.30
"""


def _store(tmp_path: Path) -> DcgmStore:
    return DcgmStore(target_path=tmp_path / "dcgm-target.json")


def test_dcgm_metrics_are_grouped_and_normalized_per_gpu(tmp_path: Path) -> None:
    parsed = _store(tmp_path).parse_prometheus(DCGM_SAMPLE)

    assert sorted(parsed) == ["0", "1"]
    assert parsed["0"]["model"] == "NVIDIA RTX 5090"
    assert parsed["0"]["metrics"]["gpu_util"] == pytest.approx(0.92)
    assert parsed["0"]["metrics"]["fb_usage"] == pytest.approx(0.75)
    assert parsed["0"]["metrics"]["decoder_util"] == pytest.approx(0.12)
    assert parsed["0"]["metrics"]["pcie_total_bytes_per_second"] == 3_000_000_000
    assert parsed["0"]["metrics"]["nvlink_total_bytes_per_second"] == 20_000_000_000


def test_dcgm_nvlink_bandwidth_counter_count_is_not_treated_as_throughput(tmp_path: Path) -> None:
    parsed = _store(tmp_path).parse_prometheus(
        'DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL{gpu="0"} 87900\n'
    )

    assert parsed == {}


def test_dcgm_new_ratio_field_names_are_supported(tmp_path: Path) -> None:
    parsed = _store(tmp_path).parse_prometheus(
        'DCGM_FI_PROF_SM_UTIL_RATIO{gpu="0"} 0.8\n'
        'DCGM_FI_PROF_TENSOR_UTIL_RATIO{gpu="0"} 0.7\n'
        'DCGM_FI_PROF_DRAM_UTIL_RATIO{gpu="0"} 0.4\n'
        'DCGM_FI_DEV_GPU_UTIL_RATIO{gpu="0"} 0.9\n'
    )

    assert parsed["0"]["metrics"] == {
        "sm_active": 0.8,
        "tensor_active": 0.7,
        "dram_active": 0.4,
        "gpu_util": 0.9,
    }


@pytest.mark.parametrize(("metrics", "kind"), [
    ({"sm_active": 0.91, "tensor_active": 0.78, "dram_active": 0.42}, "compute_bound"),
    ({"sm_active": 0.91, "tensor_active": 0.20, "dram_active": 0.90}, "memory_bound"),
    ({"sm_active": 0.40, "tensor_active": 0.20, "dram_active": 0.30}, "underutilized"),
    ({"tensor_active": 0.78, "dram_active": 0.42}, "insufficient_data"),
])
def test_dcgm_bottleneck_diagnosis_is_conservative(metrics: dict, kind: str) -> None:
    assert DcgmStore.diagnose(metrics)["kind"] == kind


def test_dcgm_status_and_history_keep_gpu_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now = datetime.now()
    store.gpus = store.parse_prometheus(DCGM_SAMPLE)
    store.last_scrape = now
    store._append_snapshot(now)

    status = store.status(now + timedelta(seconds=1))
    history = store.get_history(5, now=now + timedelta(seconds=1))

    assert status["gpu_count"] == 2
    assert status["gpus"][0]["diagnosis"]["kind"] == "compute_bound"
    assert status["gpus"][1]["diagnosis"]["kind"] == "underutilized"
    assert history[0]["gpus"]["0"]["sm_active"] == pytest.approx(0.91)


def test_dcgm_target_accepts_metrics_suffix_and_can_be_removed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.configure("http://master-1:9400/metrics") == "http://master-1:9400"
    store.save_target()
    assert store.target_path.exists()

    store.clear_target()

    assert store.target_url is None
    assert not store.target_path.exists()
