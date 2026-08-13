"""Frequency hop scheduler for the RX0 scanner.

Given a start/end frequency, a step (usually ~= sample rate to cover the span
with contiguous captures) and a dwell time, produces the sequence of center
frequencies the scanner should visit, and tracks timing so callers can decide
when to hop.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ScanPlan:
    """A computed frequency-hop plan."""

    freq_start_hz: float
    freq_end_hz: float
    step_hz: float
    dwell_ms: int
    centers: List[float] = field(default_factory=list)

    @property
    def num_steps(self) -> int:
        return len(self.centers)

    @property
    def sweep_time_s(self) -> float:
        return self.num_steps * self.dwell_ms / 1000.0


class ScanScheduler:
    """Round-robin frequency hop scheduler with dwell timing."""

    def __init__(self):
        self.plan: Optional[ScanPlan] = None
        self._index = 0
        self._last_hop_time = 0.0
        self._running = False

    def build_plan(self, freq_start_hz: float, freq_end_hz: float,
                   step_hz: float, dwell_ms: int) -> ScanPlan:
        """Compute the list of center frequencies for the sweep."""
        if step_hz <= 0:
            raise ValueError("step_hz must be positive")
        if freq_end_hz < freq_start_hz:
            freq_start_hz, freq_end_hz = freq_end_hz, freq_start_hz
        centers: List[float] = []
        # Center of the first tuning block is start + step/2 so the block spans
        # [start, start+step].
        f = freq_start_hz + step_hz / 2.0
        while f - step_hz / 2.0 < freq_end_hz:
            centers.append(f)
            f += step_hz
        if not centers:
            centers = [(freq_start_hz + freq_end_hz) / 2.0]
        self.plan = ScanPlan(freq_start_hz, freq_end_hz, step_hz, dwell_ms,
                             centers)
        self._index = 0
        return self.plan

    def start(self) -> None:
        self._running = True
        self._index = 0
        self._last_hop_time = time.time()

    def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_center(self) -> Optional[float]:
        if not self.plan or not self.plan.centers:
            return None
        return self.plan.centers[self._index % self.plan.num_steps]

    def should_hop(self) -> bool:
        """Return True if the dwell time for the current step has elapsed."""
        if not self._running or not self.plan:
            return False
        elapsed_ms = (time.time() - self._last_hop_time) * 1000.0
        return elapsed_ms >= self.plan.dwell_ms

    def next_center(self) -> Optional[float]:
        """Advance to the next center frequency and return it."""
        if not self.plan or not self.plan.centers:
            return None
        self._index = (self._index + 1) % self.plan.num_steps
        self._last_hop_time = time.time()
        return self.current_center

    def reset(self) -> None:
        self._index = 0
        self._last_hop_time = time.time()

    @property
    def progress(self) -> float:
        """Return sweep progress in the range 0..1."""
        if not self.plan or self.plan.num_steps == 0:
            return 0.0
        return (self._index + 1) / self.plan.num_steps
