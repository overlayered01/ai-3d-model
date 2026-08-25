"""서버 API 클라이언트 — 표준 라이브러리만 쓴다.

tools/ 아래 스크립트(e2e_test.py, run_batch.py)가 공유한다.
requests 를 쓰지 않는 이유는, 이 스크립트들이 .venv 밖에서도
그냥 돌아야 하기 때문이다. 설치가 깨졌을 때 진단에 쓰는 도구인데
그 도구가 설치를 요구하면 곤란하다.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

DEFAULT_BASE = "http://127.0.0.1:7860"


class ApiError(RuntimeError):
    pass


def request(url: str, data: bytes | None = None, headers: dict | None = None,
            method: str | None = None, timeout: int = 300) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise ApiError(f"HTTP {e.code}: {body[:400]}") from None


def multipart(fields: dict[str, str], file: tuple[str, Path] | None = None) -> tuple[bytes, str]:
    """의존성 없이 multipart/form-data 를 만든다."""
    boundary = f"----pixal3d{uuid.uuid4().hex}"
    parts: list[bytes] = []

    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            .encode("utf-8")
        )

    if file is not None:
        name, path = file
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
            .encode("utf-8")
        )
        parts.append(path.read_bytes())
        parts.append(b"\r\n")

    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def post(base: str, path: str, fields: dict[str, str],
         file: tuple[str, Path] | None = None, timeout: int = 300) -> dict:
    body, ctype = multipart(fields, file)
    return request(f"{base}{path}", data=body,
                   headers={"Content-Type": ctype}, timeout=timeout)


# ── 흐름 ────────────────────────────────────────────────────────────

def wait_ready(base: str, limit: int = 900,
               on_wait: Callable[[float], None] | None = None) -> dict:
    """모델 로딩이 끝날 때까지 기다린다. 로딩 실패는 즉시 예외."""
    t0 = time.time()
    while True:
        try:
            status = request(f"{base}/api/status", timeout=15)
        except Exception as e:
            raise ApiError(f"서버에 연결할 수 없습니다: {e}") from None

        if status.get("pipeline_error"):
            raise ApiError(f"모델 로딩 실패: {status['pipeline_error']}")
        if status.get("pipeline_loaded"):
            return status

        waited = time.time() - t0
        if waited > limit:
            raise ApiError(f"모델 로딩이 {limit}초를 넘겼습니다.")
        if on_wait:
            on_wait(waited)
        time.sleep(5)


def preprocess(base: str, image: Path, timeout: int = 600) -> dict:
    return post(base, "/api/preprocess", {}, file=("file", image), timeout=timeout)


def generate(base: str, run_id: str, *, seed: int = 42, resolution: int = 0,
             steps: int = 12, fov: float = -1.0) -> dict:
    return post(base, "/api/generate", {
        "run_id": run_id,
        "seed": str(seed),
        "resolution": str(resolution),
        "steps": str(steps),
        "fov": str(fov),
    }, timeout=60)


def poll(base: str, job_id: str,
         on_progress: Callable[[dict], None] | None = None,
         interval: float = 1.0, tolerate: int = 20) -> dict:
    """끝날 때까지 진행률을 폴링한다. 마지막 응답을 돌려준다.

    폴링 실패를 곧바로 예외로 올리지 않는다. 파이프라인 후반의
    'Parameterizing new mesh'(cumesh 의 UV 아틀라스 생성)는 C 확장 안에서
    수 분을 보내며 GIL 을 오래 쥔다. 그동안 uvicorn 이벤트 루프가 밀려
    /api/progress 응답이 30초를 넘길 수 있다. 여기서 포기해 버리면
    멀쩡히 돌고 있는 작업을 실패로 보고하고, 배치라면 다음 건을 밀어 넣어
    큐를 꼬이게 만든다 — 실제로 그렇게 한 번 어그러졌다.

    tolerate 회 '연속' 실패해야 포기한다. 한 번이라도 성공하면 초기화된다.
    """
    misses = 0
    while True:
        try:
            info = request(f"{base}/api/progress?job_id={job_id}", timeout=30)
            misses = 0
        except Exception as e:
            misses += 1
            if misses > tolerate:
                raise ApiError(
                    f"진행률 조회가 {misses}회 연속 실패했습니다: {e}"
                ) from None
            time.sleep(interval * 2)
            continue

        if info["status"] in ("done", "failed", "cancelled"):
            return info
        if on_progress:
            on_progress(info)
        time.sleep(interval)


def find_run(base: str, run_id: str, limit: int = 60) -> dict | None:
    runs = request(f"{base}/api/runs?limit={limit}", timeout=30)
    return next((r for r in runs if r.get("run_id") == run_id), None)


# ── 진행률 표시 ─────────────────────────────────────────────────────

class StagePrinter:
    """진행 상황을 한 줄로 보여준다.

    터미널에서는 캐리지 리턴으로 같은 줄을 덮어쓰고, 파일이나 파이프로
    리다이렉트되면 단계가 바뀔 때만 새 줄을 찍는다. 후자를 구분하지 않으면
    초당 한 줄씩 수백 줄이 로그에 쌓인다.
    """

    def __init__(self, indent: str = "  ", stream=None) -> None:
        import sys

        self.stream = stream or sys.stdout
        self.indent = indent
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.last_key = ""
        self.t0 = time.time()

    def __call__(self, info: dict) -> None:
        p = info.get("progress") or {}
        stage = p.get("stage", info.get("status", ""))
        key = f"{stage}|{p.get('step', '')}/{p.get('total', '')}"

        if self.tty:
            line = f"{self.indent}{stage}"
            if p.get("total"):
                line += f"  {p['step']}/{p['total']}"
            line += f"  ({time.time() - self.t0:.0f}초)"
            print(line.ljust(78), end="\r", file=self.stream)
        elif key != self.last_key and stage:
            # 스텝 단위로는 너무 잦다 — 단계 이름이 바뀔 때만 남긴다.
            if stage != self.last_key.split("|")[0]:
                print(f"{self.indent}{stage} ... ({time.time() - self.t0:.0f}초)",
                      file=self.stream, flush=True)
        self.last_key = key

    def clear(self) -> None:
        if self.tty:
            print(" " * 78, end="\r", file=self.stream)
