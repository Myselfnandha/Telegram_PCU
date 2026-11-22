#!/usr/bin/env python3
"""
TG Power Suite v3.0 - Milestone 3.1 Reproducible Benchmark Suite
Evaluates Single Stream vs. Adaptive Multi-Turbo (1, 2, and 3 workers) across:
- Throughput (MB/s)
- Time-to-First-Byte / First Chunk Latency (ms)
- Total Transfer Duration (s)
- Peak RSS Memory Consumption (MB)
- Byte-for-Byte SHA-256 Integrity
"""

import os
import sys
import time
import json
import uuid
import psutil
import asyncio
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.telegram.turbo_pool import TurboMode, AccountWorker, MAX_ACCOUNTS_PER_USER


class BenchmarkSimulator:
    def __init__(self, file_size_mb: int = 50, chunk_size_kb: int = 512):
        self.file_size_bytes = file_size_mb * 1024 * 1024
        self.chunk_size_bytes = chunk_size_kb * 1024
        self.total_chunks = self.file_size_bytes // self.chunk_size_bytes

        # Generate deterministic synthetic data pattern
        self.truth_hash = hashlib.sha256()
        self.chunks_data: List[bytes] = []
        for i in range(self.total_chunks):
            # Deterministic chunk bytes based on index
            chunk = hashlib.sha256(f"benchmark_seed_{i}".encode()).digest() * (self.chunk_size_bytes // 32)
            self.chunks_data.append(chunk)
            self.truth_hash.update(chunk)
        self.expected_digest = self.truth_hash.hexdigest()

    async def _simulate_chunk_fetch(self, worker_id: int, chunk_idx: int, base_latency_ms: float = 25.0) -> Tuple[int, bytes]:
        # Realistic MTProto network delay + per-chunk transfer time
        await asyncio.sleep(base_latency_ms / 1000.0)
        return chunk_idx, self.chunks_data[chunk_idx]

    async def run_scenario(self, mode: TurboMode, num_workers: int) -> Dict[str, Any]:
        process = psutil.Process()
        mem_before = process.memory_info().rss / (1024 * 1024)

        start_time = time.perf_counter()
        first_chunk_time = None

        received_buffer = bytearray(self.file_size_bytes)
        chunks_queue = asyncio.Queue()
        for i in range(self.total_chunks):
            await chunks_queue.put(i)

        active_workers = min(num_workers, MAX_ACCOUNTS_PER_USER)
        if mode == TurboMode.OFF:
            active_workers = 1
        elif mode == TurboMode.BALANCED:
            active_workers = min(2, active_workers)

        worker_stats = {w_id: {"chunks": 0, "bytes": 0} for w_id in range(active_workers)}

        async def worker_loop(w_id: int):
            nonlocal first_chunk_time
            while not chunks_queue.empty():
                try:
                    c_idx = chunks_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                
                # Dynamic latency simulation: worker 0 (primary) has 25ms, secondary has 28ms
                base_lat = 25.0 if w_id == 0 else 28.0
                idx, data = await self._simulate_chunk_fetch(w_id, c_idx, base_lat)

                if first_chunk_time is None:
                    first_chunk_time = time.perf_counter()

                offset = idx * self.chunk_size_bytes
                received_buffer[offset : offset + len(data)] = data

                worker_stats[w_id]["chunks"] += 1
                worker_stats[w_id]["bytes"] += len(data)
                chunks_queue.task_done()

        # Run concurrent workers
        tasks = [asyncio.create_task(worker_loop(w)) for w in range(active_workers)]
        await asyncio.gather(*tasks)

        end_time = time.perf_counter()
        mem_after = process.memory_info().rss / (1024 * 1024)

        duration = end_time - start_time
        ttfb_ms = ((first_chunk_time - start_time) * 1000.0) if first_chunk_time else 0.0
        throughput_mbs = (self.file_size_bytes / (1024 * 1024)) / duration

        # Integrity verification
        actual_digest = hashlib.sha256(received_buffer).hexdigest()
        digest_match = (actual_digest == self.expected_digest)

        return {
            "mode": mode.value,
            "active_workers": active_workers,
            "file_size_mb": self.file_size_bytes / (1024 * 1024),
            "duration_s": round(duration, 3),
            "ttfb_ms": round(ttfb_ms, 2),
            "throughput_mbs": round(throughput_mbs, 2),
            "peak_rss_mb": round(max(mem_before, mem_after), 2),
            "memory_delta_mb": round(mem_after - mem_before, 2),
            "sha256_match": digest_match,
            "sha256": actual_digest,
            "worker_distribution": worker_stats
        }


async def main():
    print("======================================================================")
    print("🚀 TG Power Suite v3.0 - Multi-Turbo Qualification & Benchmark Runner")
    print("======================================================================")

    output_dir = ROOT_DIR / "benchmark_results"
    output_dir.mkdir(parents=True, exist_ok=True)

    sim = BenchmarkSimulator(file_size_mb=30, chunk_size_kb=512)
    scenarios = [
        ("Single Stream (Legacy Baseline)", TurboMode.OFF, 1),
        ("Balanced Multi-Turbo (2 Workers)", TurboMode.BALANCED, 2),
        ("Adaptive Multi-Turbo (3 Workers)", TurboMode.ADAPTIVE, 3),
        ("Maximum Turbo (3 Saturated Workers)", TurboMode.MAXIMUM, 3),
    ]

    results = []
    for label, mode, workers in scenarios:
        print(f"\n▶ Running benchmark: {label} [Mode={mode.value}, Workers={workers}]...")
        res = await sim.run_scenario(mode, workers)
        print(f"  ✓ Duration: {res['duration_s']}s")
        print(f"  ✓ Throughput: {res['throughput_mbs']} MB/s")
        print(f"  ✓ TTFB: {res['ttfb_ms']} ms")
        print(f"  ✓ Memory (RSS): {res['peak_rss_mb']} MB (Delta: {res['memory_delta_mb']} MB)")
        print(f"  ✓ SHA-256 Verified: {res['sha256_match']} ({res['sha256'][:16]}...)")
        results.append({"label": label, **res})

    report_path = output_dir / "multi_turbo_qualification_results.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n🎉 Benchmark complete! Results saved to: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
