"""실행 이력 저장소 — runs/ 디렉터리를 읽고 쓴다.

한 번의 생성은 runs/<타임스탬프>_<슬러그>/ 하나에 대응한다:

    input.png       업로드 원본
    processed.png   배경 제거·정사각 패딩 결과
    full.glb        저장용 원본 (4096 텍스처 / 100만 삼각형)
    web.glb         뷰어용 경량본 (2048 텍스처 / 20만 삼각형)
    latent.pt       잠재 캐시 — 생성 없이 GLB 만 재추출할 때 쓴다
    meta.json       입력·파라미터·소요시간·VRAM·커밋해시
    log.txt         해당 실행의 로그

DB 를 두지 않는다. 디렉터리 자체가 인덱스다. 실행 결과를 손으로 지우거나
다른 PC 로 복사해도 그대로 동작하고, 서버가 죽어도 이력이 남는다.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RUNS

SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str, limit: int = 24) -> str:
    """파일명에 쓸 수 있는 짧은 이름. 한글 등은 전부 '-' 로 접힌다."""
    s = SAFE.sub("-", text).strip("-")
    return (s[:limit].rstrip("-") or "run")


@dataclass
class RunMeta:
    """meta.json 의 내용. 웹 UI 의 이력 카드가 이 값을 그대로 쓴다."""

    run_id: str
    created_at: str
    status: str = "queued"          # queued | running | done | failed
    source_name: str = ""

    seed: int = 42
    resolution: int = 1024
    low_vram: bool = True
    fov: float = -1.0

    seconds: float = 0.0
    peak_vram_gb: float = 0.0

    full_glb_mb: float = 0.0
    web_glb_mb: float = 0.0
    vertices: int = 0
    triangles: int = 0

    upstream_commit: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)


class Store:
    def __init__(self, root: Path = RUNS) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # ── 경로 ────────────────────────────────────────────────────────

    def dir_for(self, run_id: str) -> Path:
        # run_id 는 우리가 만든 값이지만, 외부에서 들어온 값으로 경로를 만들 때
        # 상위 디렉터리로 빠져나가지 못하도록 항상 검사한다.
        d = (self.root / run_id).resolve()
        if not str(d).startswith(str(self.root.resolve())):
            raise ValueError(f"잘못된 run_id: {run_id!r}")
        return d

    def new_run(self, source_name: str, **params: Any) -> RunMeta:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{stamp}_{_slug(Path(source_name).stem)}"

        # 같은 초에 두 건이 들어오면 뒤에 번호를 붙인다
        run_id, n = base, 2
        while (self.root / run_id).exists():
            run_id = f"{base}-{n}"
            n += 1

        (self.root / run_id).mkdir(parents=True)

        meta = RunMeta(
            run_id=run_id,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source_name=source_name,
            **params,
        )
        self.save(meta)
        return meta

    # ── 읽기/쓰기 ───────────────────────────────────────────────────

    def save(self, meta: RunMeta) -> None:
        path = self.dir_for(meta.run_id) / "meta.json"
        tmp = path.with_suffix(".json.tmp")
        # 원자적 교체 — 웹 UI 가 폴링 중에 반쯤 쓰인 JSON 을 읽지 않도록.
        tmp.write_text(
            json.dumps(asdict(meta), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def load(self, run_id: str) -> RunMeta | None:
        """meta.json 을 읽는다. 읽을 수 없으면 None.

        관대하게 읽는 이유가 있다. 이 디렉터리에는 웹 서버가 쓴 것 말고
        CLI 스모크 스크립트(setup/06_smoke_inference.ps1)가 쓴 것도 섞인다.
        PowerShell 의 `Set-Content -Encoding utf8` 은 BOM 을 붙이고, 필드
        구성도 다르다. 한 건이라도 읽다 실패하면 이력 API 전체가 죽으므로
        건별로 삼키고, 없는 필드는 기본값으로 채운다.
        """
        path = self.dir_for(run_id) / "meta.json"
        if not path.exists():
            return None
        try:
            # utf-8-sig: PowerShell 이 붙이는 BOM 을 벗겨낸다.
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None

        # 필드가 늘거나 준 옛 실행도 읽을 수 있도록 알려진 키만 취한다
        known = set(RunMeta.__dataclass_fields__)
        fields = {k: v for k, v in data.items() if k in known}

        # 필수 필드가 없으면 디렉터리에서 되찾는다 (CLI 가 쓴 meta.json 등)
        fields.setdefault("run_id", run_id)
        if "created_at" not in fields:
            try:
                stamp = path.stat().st_mtime
                fields["created_at"] = datetime.fromtimestamp(
                    stamp, timezone.utc
                ).isoformat(timespec="seconds")
            except OSError:
                fields["created_at"] = ""
        if "status" not in fields:
            # CLI 는 성공했을 때만 meta.json 을 쓴다.
            fields["status"] = "done"
        if "source_name" not in fields and data.get("image"):
            fields["source_name"] = Path(str(data["image"])).name
        if "full_glb_mb" not in fields and data.get("glb_mb"):
            fields["full_glb_mb"] = data["glb_mb"]

        try:
            return RunMeta(**fields)
        except TypeError:
            return None

    def list(self, limit: int = 100) -> list[RunMeta]:
        """최신순. meta.json 이 없는 디렉터리는 조용히 건너뛴다."""
        dirs = sorted(
            (d for d in self.root.iterdir() if d.is_dir()),
            key=lambda d: d.name,
            reverse=True,
        )
        out: list[RunMeta] = []
        for d in dirs:
            if len(out) >= limit:
                break
            meta = self.load(d.name)
            if meta is not None:
                out.append(meta)
        return out

    def delete(self, run_id: str) -> bool:
        d = self.dir_for(run_id)
        if not d.is_dir():
            return False
        shutil.rmtree(d)
        return True

    # ── 정리 ────────────────────────────────────────────────────────

    def sweep_stale(self) -> int:
        """서버가 비정상 종료되면 running 인 채로 남는 실행이 생긴다.
        기동 시 한 번 불러 실패로 정리한다."""
        n = 0
        for meta in self.list(limit=1000):
            if meta.status in ("queued", "running"):
                meta.status = "failed"
                meta.error = "서버가 재시작되어 중단되었습니다."
                self.save(meta)
                n += 1
        return n

    def prune_latents(self, keep: int = 20) -> int:
        """잠재 캐시는 한 건당 수백 MB 다. 최근 것만 남기고 지운다.
        지워도 GLB 는 남으므로 이력 조회에는 영향이 없다 — 재추출만 못 한다."""
        removed = 0
        for meta in self.list(limit=1000)[keep:]:
            latent = self.dir_for(meta.run_id) / "latent.pt"
            if latent.exists():
                latent.unlink()
                removed += 1
        return removed
