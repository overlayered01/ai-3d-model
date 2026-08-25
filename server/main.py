"""FastAPI 웹 서버.

실행:
    powershell -File setup\\run_server.ps1
    또는
    python -m uvicorn server.main:app --host 127.0.0.1 --port 7860 --workers 1

--workers 1 은 선택이 아니라 필수다. 워커가 여럿이면 각각 파이프라인을
로드해 22GB 를 중복으로 올리고 곧바로 OOM 이 난다.

기본 바인딩은 127.0.0.1 이다. upstream app.py 의 share=True 와 정반대이며,
의도한 것이다 — 인증이 없으므로 URL 을 아는 누구나 이 GPU 를 쓰게 된다.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import progress
from .config import SETTINGS, UPSTREAM, WEB
from .jobs import QUEUE
from .store import Store

store = Store()

# 업로드 검증 — 브라우저에서 아무거나 올릴 수 있으므로 서버에서 막는다.
ALLOWED_SUFFIX = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MAX_UPLOAD_MB = 40

# 전처리 요청이 큐에서 순서를 기다리는 한도(초). 앞선 생성 작업 하나가
# 끝나기를 기다릴 수 있어야 한다 — 1536 해상도는 8분 넘게 걸린다.
PREPROCESS_WAIT = 1200


def glb_counts(path: Path) -> dict[str, int]:
    """GLB 의 JSON 청크만 읽어 정점·삼각형 수를 센다.

    trimesh 로 다시 로드하지 않는 이유는, 수백 MB 파일을 메모리에 올리지 않고
    accessor 의 count 만 보면 되기 때문이다. setup/inspect_glb.py 와 같은 방식.
    """
    import struct

    try:
        with path.open("rb") as f:
            magic, _, total = struct.unpack("<III", f.read(12))
            if magic != 0x46546C67:
                return {"vertices": 0, "triangles": 0}
            gltf = None
            while f.tell() < total:
                head = f.read(8)
                if len(head) < 8:
                    break
                length, ctype = struct.unpack("<II", head)
                chunk = f.read(length)
                if ctype == 0x4E4F534A:      # 'JSON'
                    gltf = json.loads(chunk.decode("utf-8"))
                    break
                # BIN 청크는 건너뛴다 — 이미 read 로 소비했다.
        if not gltf:
            return {"vertices": 0, "triangles": 0}

        accessors = gltf.get("accessors", [])
        verts = tris = 0
        for mesh in gltf.get("meshes", []):
            for prim in mesh.get("primitives", []):
                pos = prim.get("attributes", {}).get("POSITION")
                if pos is not None and pos < len(accessors):
                    verts += accessors[pos].get("count", 0)
                idx = prim.get("indices")
                if idx is not None and idx < len(accessors):
                    tris += accessors[idx].get("count", 0) // 3
        return {"vertices": verts, "triangles": tris}
    except Exception:
        return {"vertices": 0, "triangles": 0}


def upstream_commit() -> str:
    """어떤 upstream 리비전으로 만든 결과인지 남긴다."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(UPSTREAM), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    banner()
    # 서버가 비정상 종료되면 running 인 채로 남은 실행이 있다. 정리하고 시작한다.
    stale = store.sweep_stale()
    if stale:
        print(f"[store] 중단된 실행 {stale}건을 실패로 정리했습니다.")

    QUEUE.start()

    # 모델 로딩은 수 분이 걸린다. 기동을 막지 않고 백그라운드에서 올린다.
    # 그동안 UI 는 열리고, 생성 요청만 '준비 중'으로 거절된다.
    def _load():
        from .pipeline import RUNNER

        try:
            RUNNER.load()
        except Exception as e:
            print(f"[pipeline] 로딩 실패: {type(e).__name__}: {e}")

    asyncio.get_running_loop().run_in_executor(None, _load)

    yield
    QUEUE.stop()


def banner() -> None:
    print()
    print(f"  Pixal3D 웹 서버  http://{SETTINGS.host}:{SETTINGS.port}")
    print(f"  해상도 {SETTINGS.resolution} · low_vram={SETTINGS.low_vram}")
    if SETTINGS.is_public:
        print()
        print("  [경고] 루프백이 아닌 주소에 바인딩했습니다.")
        print("         이 서버에는 인증이 없습니다. 접근 가능한 사람은 누구나")
        print("         이 PC 의 GPU 로 작업을 돌릴 수 있습니다.")
    print()


app = FastAPI(title="Pixal3D 로컬 테스트", lifespan=lifespan)


# ── 상태 ────────────────────────────────────────────────────────────

@app.get("/api/status")
async def status():
    from .pipeline import RUNNER

    return {
        "pipeline_loaded": RUNNER.loaded,
        "pipeline_error": RUNNER.load_error,
        "load_seconds": round(RUNNER.load_seconds, 1),
        "queue": QUEUE.snapshot(),
        "defaults": {
            "resolution": SETTINGS.resolution,
            "low_vram": SETTINGS.low_vram,
        },
    }


# ── 1단계: 전처리 ───────────────────────────────────────────────────

@app.post("/api/preprocess")
async def preprocess(file: UploadFile = File(...)):
    """배경 제거 + 정사각 패딩. 생성 전에 결과를 확인할 수 있게 따로 뗀다."""
    from .pipeline import RUNNER

    if not RUNNER.loaded:
        raise HTTPException(503, RUNNER.load_error or "모델을 아직 불러오는 중입니다.")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIX:
        raise HTTPException(400, f"지원하지 않는 형식입니다: {suffix or '(확장자 없음)'}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 2**20:
        raise HTTPException(400, f"파일이 너무 큽니다 (최대 {MAX_UPLOAD_MB}MB).")

    meta = store.new_run(file.filename or "upload.png")
    run_dir = store.dir_for(meta.run_id)
    src = run_dir / f"input{suffix}"
    src.write_bytes(data)

    job_id = meta.run_id
    out = run_dir / "processed.png"

    def _work():
        RUNNER.preprocess(src, out)

    job = QUEUE.submit(job_id, _work, label=f"preprocess:{meta.run_id}")

    # 전처리 자체는 수 초다. 그래도 넉넉히 기다리는 이유는 큐가 슬롯 하나여서,
    # 앞에 생성 작업이 돌고 있으면 그것이 끝날 때까지 순서가 오지 않기
    # 때문이다. 생성은 해상도에 따라 10분을 넘길 수 있다. 예전의 180초는
    # 이 대기를 '전처리 실패'로 오인해 배치 실행을 어그러뜨렸다.
    await asyncio.get_running_loop().run_in_executor(None, job.wait, PREPROCESS_WAIT)

    if job.status != "done":
        # 아직 큐에 있거나 도는 중이면 실패가 아니다. 실행을 실패로 못박지 않고
        # 클라이언트에게 '지금은 바쁘다'고 알린다 — 나중에 다시 부르면 된다.
        if job.status in ("queued", "running"):
            raise HTTPException(
                503,
                f"앞선 작업이 아직 끝나지 않아 {PREPROCESS_WAIT}초 안에 순서가 오지 않았습니다. "
                "잠시 뒤 다시 시도하세요.",
            )
        meta.status = "failed"
        meta.error = job.error or "전처리에 실패했습니다."
        store.save(meta)
        raise HTTPException(500, meta.error)

    return {
        "run_id": meta.run_id,
        "processed_url": f"/runs/{meta.run_id}/processed.png",
    }


# ── 2단계: 생성 ─────────────────────────────────────────────────────

@app.post("/api/generate")
async def generate(
    run_id: str = Form(...),
    seed: int = Form(42),
    resolution: int = Form(0),
    fov: float = Form(-1.0),
    steps: int = Form(12),
):
    from .pipeline import RUNNER

    if not RUNNER.loaded:
        raise HTTPException(503, RUNNER.load_error or "모델을 아직 불러오는 중입니다.")

    meta = store.load(run_id)
    if meta is None:
        raise HTTPException(404, "그런 실행이 없습니다.")

    run_dir = store.dir_for(run_id)
    processed = run_dir / "processed.png"
    if not processed.exists():
        raise HTTPException(400, "전처리를 먼저 해야 합니다.")

    meta.seed = seed
    meta.resolution = resolution or SETTINGS.resolution
    meta.low_vram = SETTINGS.low_vram
    meta.fov = fov
    meta.status = "queued"
    store.save(meta)

    job_id = f"{run_id}:gen"

    def _work():
        import time

        t0 = time.time()
        meta.status = "running"
        store.save(meta)

        result = RUNNER.generate(
            processed,
            seed=seed,
            resolution=meta.resolution,
            fov=fov,
            steps=steps,
        )

        # GLB 두 벌 — 저장용 원본과 뷰어용 경량본 (리스크 R9)
        full = RUNNER.extract_glb(
            result["mesh"], run_dir / "full.glb",
            grid_size=result["grid_size"],
            texture_size=SETTINGS.full_texture_size,
            decimation_target=SETTINGS.full_decimation_target,
        )
        web = RUNNER.extract_glb(
            result["mesh"], run_dir / "web.glb",
            grid_size=result["grid_size"],
            texture_size=SETTINGS.web_texture_size,
            decimation_target=SETTINGS.web_decimation_target,
        )

        meta.seconds = round(time.time() - t0, 1)
        meta.peak_vram_gb = result["peak_vram_gb"]
        meta.full_glb_mb = full["mb"]
        meta.web_glb_mb = web["mb"]

        # 원본 GLB 에서 실제 지오메트리 수치를 읽는다. 결과 비교에 쓰이므로
        # 파이프라인이 보고한 값이 아니라 최종 파일에서 세는 편이 정확하다.
        counts = glb_counts(run_dir / "full.glb")
        meta.vertices = counts["vertices"]
        meta.triangles = counts["triangles"]
        meta.upstream_commit = upstream_commit()

        meta.status = "done"
        store.save(meta)

    QUEUE.submit(job_id, _work, label=f"generate:{run_id}")
    return {"job_id": job_id, "run_id": run_id, "position": QUEUE.position(job_id)}


# ── 진행률 ──────────────────────────────────────────────────────────

@app.get("/api/progress")
async def get_progress(job_id: str):
    job = QUEUE.get(job_id)
    if job is None:
        raise HTTPException(404, "그런 작업이 없습니다.")

    p = progress.get(job_id)
    return {
        "status": job.status,
        "error": job.error,
        "position": QUEUE.position(job_id),
        "progress": p.as_dict() if p else None,
    }


@app.post("/api/cancel")
async def cancel(job_id: str = Form(...)):
    ok, message = QUEUE.cancel(job_id)
    return JSONResponse({"cancelled": ok, "message": message}, status_code=200 if ok else 409)


# ── 이력 ────────────────────────────────────────────────────────────

@app.get("/api/runs")
async def list_runs(limit: int = 60):
    from dataclasses import asdict

    return [asdict(m) for m in store.list(limit=limit)]


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: str):
    if not store.delete(run_id):
        raise HTTPException(404, "그런 실행이 없습니다.")
    return {"deleted": run_id}


# ── 정적 파일 ───────────────────────────────────────────────────────

@app.get("/runs/{run_id}/{name}")
async def run_file(run_id: str, name: str):
    # 경로 조작 방지 — run_id 와 파일명 모두 검사한다.
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "잘못된 파일명입니다.")
    path = store.dir_for(run_id) / name
    if not path.is_file():
        raise HTTPException(404, "파일이 없습니다.")
    return FileResponse(path)


if WEB.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
