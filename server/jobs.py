"""작업 큐 — 단일 슬롯.

GPU 가 하나이므로 동시에 도는 생성은 반드시 1건이다. 여러 요청이 겹치면
OOM 이 나거나 서로를 느리게 만든다. 그래서 큐를 두고 순서대로 흘린다.

설계상 정한 것:
  - 워커 스레드 1개. 파이프라인이 스레드 안전하지 않고, 어차피 GPU 가 하나다.
  - 실행 중인 작업은 취소할 수 없다. 파이프라인이 중단점을 제공하지 않기
    때문이다 (리스크 R10). 큐에서 대기 중인 작업만 취소된다.
  - 작업 하나가 죽어도 서버는 살아 있어야 한다. OOM 은 정상적으로 일어나는
    일이고, 그때마다 서버가 내려가면 쓸 수 없다 (리스크 R11).
"""

from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass, field
from typing import Callable

from . import progress


@dataclass
class Job:
    job_id: str
    run: Callable[[], None]
    label: str = ""

    status: str = "queued"      # queued | running | done | failed | cancelled
    error: str = ""
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)


class JobQueue:
    """단일 워커 스레드가 작업을 하나씩 처리한다."""

    def __init__(self) -> None:
        self._q: queue.Queue[Job] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []       # 대기 순번 계산용
        self._current: str | None = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

    # ── 수명 ────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._loop, name="pixal3d-worker", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()

    # ── 제출 / 조회 ─────────────────────────────────────────────────

    def submit(self, job_id: str, run: Callable[[], None], label: str = "") -> Job:
        job = Job(job_id=job_id, run=run, label=label)
        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)
        self._q.put(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def position(self, job_id: str) -> int:
        """앞에 몇 건이 남았는지. 실행 중이면 0, 없으면 -1."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ("queued", "running"):
                return -1
            if job.status == "running":
                return 0
            waiting = [
                jid for jid in self._order
                if self._jobs.get(jid) and self._jobs[jid].status == "queued"
            ]
            try:
                # 실행 중인 작업이 있으면 그것도 앞에 있는 셈이다.
                return waiting.index(job_id) + (1 if self._current else 0)
            except ValueError:
                return -1

    def cancel(self, job_id: str) -> tuple[bool, str]:
        """대기 중인 작업만 취소된다. 실행 중인 것은 중단할 수 없다."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False, "그런 작업이 없습니다."
            if job.status == "running":
                return False, "이미 실행 중이라 취소할 수 없습니다. 완료될 때까지 기다려 주세요."
            if job.status != "queued":
                return False, f"이미 {job.status} 상태입니다."
            job.status = "cancelled"
            job._done.set()
            return True, "대기열에서 취소했습니다."

    def snapshot(self) -> dict:
        with self._lock:
            queued = sum(1 for j in self._jobs.values() if j.status == "queued")
            return {
                "running": self._current,
                "queued": queued,
                "worker_alive": bool(self._worker and self._worker.is_alive()),
            }

    # ── 워커 ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._q.get(timeout=0.5)
            except queue.Empty:
                continue

            # 대기 중에 취소됐을 수 있다
            if job.status == "cancelled":
                self._q.task_done()
                continue

            with self._lock:
                job.status = "running"
                self._current = job.job_id

            progress.begin(job.job_id)
            try:
                job.run()
                job.status = "done"

            except Exception as e:
                # 작업 하나의 실패가 서버를 내리지 않게 한다 (리스크 R11).
                job.status = "failed"
                job.error = f"{type(e).__name__}: {e}"
                traceback.print_exc()
                self._free_vram()

            finally:
                progress.finish(job.job_id)
                with self._lock:
                    self._current = None
                job._done.set()
                self._q.task_done()

    @staticmethod
    def _free_vram() -> None:
        """실패 후 다음 작업이 같은 이유로 죽지 않도록 캐시를 비운다."""
        try:
            import torch

            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass


QUEUE = JobQueue()
