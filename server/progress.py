"""진행률 추적 — upstream app.py 의 tqdm 가로채기를 재사용한다.

Pixal3D 파이프라인은 진행 상황을 알려주는 콜백을 노출하지 않는다. upstream 이
찾은 방법은 tqdm 을 몽키패치해 스텝 수를 가로채는 것이고, 우리도 같은 수법을 쓴다.
다만 upstream 은 세션별 JSON 파일에 쓰고 폴링하게 했는데, 우리는 단일 프로세스
안에서 돌므로 메모리에 두고 그대로 읽는다.

`patch()` 는 반드시 pixal3d 를 import 한 뒤에 불러야 한다. 대상 모듈들이
`from tqdm import tqdm` 으로 이름을 자기 네임스페이스에 복사해 가기 때문에,
tqdm 모듈만 갈아끼워서는 이미 import 된 쪽에 반영되지 않는다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, asdict


@dataclass
class Progress:
    stage: str = "대기 중"
    step: int = 0
    total: int = 0
    done: bool = False

    @property
    def fraction(self) -> float:
        return (self.step / self.total) if self.total else 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["fraction"] = round(self.fraction, 4)
        return d


_lock = threading.Lock()
_current: dict[str, Progress] = {}
_local = threading.local()

# tqdm 의 desc 를 그대로 보여주면 영문 내부 용어가 노출된다.
# 사용자가 이해할 수 있는 단계명으로 바꾼다.
_STAGE_NAMES = {
    "sampling": "생성 중",
    "sparse structure": "1/3 구조 생성",
    "shape": "2/3 형상 생성",
    "texture": "3/3 텍스처 생성",
    "remesh": "메시 정리",
    "uv": "UV 펼치기",
    "decimat": "폴리곤 감축",
    "bake": "텍스처 굽기",
    "export": "GLB 내보내기",
}


def _humanize(desc: str) -> str:
    low = (desc or "").strip().lower()
    for key, name in _STAGE_NAMES.items():
        if key in low:
            return name
    return desc or "처리 중"


def begin(job_id: str) -> None:
    """이 스레드에서 시작하는 작업을 job_id 에 묶는다."""
    _local.job_id = job_id
    with _lock:
        _current[job_id] = Progress(stage="준비 중")


def set_stage(stage: str) -> None:
    """tqdm 이 없는 구간(모델 로딩, 카메라 추정 등)을 직접 알린다."""
    job_id = getattr(_local, "job_id", None)
    if not job_id:
        return
    with _lock:
        p = _current.get(job_id)
        if p:
            p.stage, p.step, p.total = stage, 0, 0


def update(stage: str, step: int, total: int) -> None:
    job_id = getattr(_local, "job_id", None)
    if not job_id:
        return
    with _lock:
        p = _current.get(job_id)
        if p:
            p.stage, p.step, p.total = _humanize(stage), step, total


def finish(job_id: str | None = None) -> None:
    job_id = job_id or getattr(_local, "job_id", None)
    if not job_id:
        return
    with _lock:
        p = _current.get(job_id)
        if p:
            p.done = True
            p.stage = "완료"
    _local.job_id = None


def get(job_id: str) -> Progress | None:
    with _lock:
        p = _current.get(job_id)
        return Progress(**asdict(p)) if p else None


def drop(job_id: str) -> None:
    with _lock:
        _current.pop(job_id, None)


# ── tqdm 몽키패치 ───────────────────────────────────────────────────

_patched = False


def patch() -> list[str]:
    """tqdm 을 가로채도록 갈아끼운다. 패치한 모듈 이름들을 돌려준다.

    반드시 pixal3d / o_voxel 을 import 한 뒤에 호출할 것.
    """
    global _patched
    if _patched:
        return []

    import tqdm as tqdm_module

    original = tqdm_module.tqdm

    class _Intercepting(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            self._stage_desc = kwargs.get("desc", "")
            super().__init__(*args, **kwargs)

        def set_description(self, desc=None, refresh=True):
            self._stage_desc = desc or ""
            super().set_description(desc, refresh)

        def update(self, n=1):
            super().update(n)
            update(self._stage_desc, self.n, self.total or 0)

    tqdm_module.tqdm = _Intercepting

    # `from tqdm import tqdm` 으로 이름을 복사해 간 모듈들도 개별로 바꿔야 한다.
    # upstream app.py 가 패치하는 목록과 같되, 우리가 쓰지 않는 렌더러는 뺀다.
    targets = [
        "pixal3d.pipelines.samplers.flow_euler",
        "o_voxel.postprocess",
    ]
    patched: list[str] = []
    for name in targets:
        try:
            import importlib

            mod = importlib.import_module(name)
        except Exception:
            continue
        if hasattr(mod, "tqdm"):
            mod.tqdm = _Intercepting  # type: ignore[attr-defined]
            patched.append(name)

    _patched = True
    return patched
