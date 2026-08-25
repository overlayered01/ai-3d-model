"""파이프라인 상주 래퍼.

원칙 두 가지가 이 파일의 모양을 결정한다.

1. upstream 을 수정하지 않는다.
   `upstream/Pixal3D/inference.py` 를 모듈로 import 해서 그 안의 검증된 헬퍼
   (init_pipeline / build_image_cond_model / load_moge_model /
    get_camera_params_wild_moge)를 그대로 쓴다. 우리가 새로 쓰는 것은
   3단계로 쪼갠 오케스트레이션뿐이다.

2. 모델은 프로세스에 상주한다.
   요청마다 inference.py 를 새로 띄우면 매번 22GB 를 다시 읽는다.
   GPU 가 하나이므로 동시 실행도 1건이다 (jobs.py 가 보장).

upstream app.py 와 다른 점:
  - 서버사이드 턴테이블 렌더(render_proj_aligned_video)를 호출하지 않는다.
    브라우저 뷰어가 그 역할을 한다 (결정 D6). 다만 nvdiffrast 자체는 여전히
    필요하다 — o_voxel.postprocess.to_glb 가 UV 텍스처 베이킹에 쓴다.
    (계획서 rev.1/rev.2 의 "nvdiffrast 생략 가능" 판단은 여기서 뒤집혔다.)
  - `spaces` (HF Spaces ZeroGPU) 데코레이터를 쓰지 않는다.
  - GLB 를 두 벌 뽑는다: 저장용 원본과 뷰어용 경량본 (리스크 R9).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image

from . import progress
from .config import ROOT, SETTINGS, UPSTREAM

# upstream 모듈을 import 하기 전에 환경을 확정해야 한다.
# inference.py 는 최상위에서 ATTN_BACKEND 를 setdefault 하므로 여기서 먼저 심는다.
os.environ.setdefault("ATTN_BACKEND", SETTINGS.attn_backend)
os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
# FLEX_GEMM_AUTOTUNE_CACHE_PATH 는 config.pin_paths() 가 cache/ 로 이미 지정했다.
# upstream 폴더에 쓰지 않는다 — '수정하지 않는다' 원칙의 대상이다.

if str(UPSTREAM) not in sys.path:
    sys.path.insert(0, str(UPSTREAM))


class PipelineNotReady(RuntimeError):
    pass


def _apply_rembg_shim() -> None:
    """배경 제거 모델 교체 셰임을 적용한다.

    셰임은 tools/shims/rembg_model.py 에 있다. 패키지로 import 하지 않고
    파일 경로로 부르는 이유는, 프로젝트 루트를 sys.path 에 올려두면
    torch.hub 로 받아오는 NAF 의 `from src.model.naf import ...` 를
    가릴 수 있기 때문이다.
    """
    import importlib.util

    path = ROOT / "tools" / "shims" / "rembg_model.py"
    if not path.exists():
        print(f"[rembg] 셰임 없음: {path}")
        return

    spec = importlib.util.spec_from_file_location("_pixal3d_rembg_shim", path)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules["_pixal3d_rembg_shim"] = module
    spec.loader.exec_module(module)

    if not os.environ.get(module.ENV_VAR):
        print("[rembg] PIXAL3D_REMBG_MODEL 미설정 — upstream 기본값(briaai/RMBG-2.0)을 씁니다.")


class Pixal3DRunner:
    """파이프라인 하나를 물고 있는 객체. 프로세스당 한 개만 만든다."""

    def __init__(self) -> None:
        self._pipeline: Any = None
        self._moge: Any = None
        self._upstream: Any = None       # upstream inference 모듈
        self._lock = threading.Lock()
        self.loaded = False
        self.load_error: str = ""
        self.load_seconds: float = 0.0

    # ── 로딩 ────────────────────────────────────────────────────────

    def load(self) -> None:
        """모델을 메모리에 올린다. 서버 기동 시 한 번만 부른다."""
        with self._lock:
            if self.loaded:
                return
            t0 = time.time()
            try:
                import inference as upstream_inference  # upstream/Pixal3D/inference.py

                self._upstream = upstream_inference

                # tqdm 가로채기는 대상 모듈이 import 된 뒤여야 의미가 있다.
                patched = progress.patch()
                print(f"[progress] tqdm 패치: {', '.join(patched) or '(없음)'}")

                # 배경 제거 모델 교체. init_pipeline 이 이 모델을 무조건 로드하므로
                # 반드시 그 전에 적용해야 한다. CLI 래퍼(tools/run_inference.py)와
                # 같은 셰임을 쓴다.
                _apply_rembg_shim()

                self._pipeline = upstream_inference.init_pipeline(
                    upstream_inference.MODEL_PATH,
                    device="cuda",
                    low_vram=SETTINGS.low_vram,
                )
                self.loaded = True
                self.load_seconds = time.time() - t0
                print(f"[pipeline] 준비 완료 — {self.load_seconds:.1f}초")

            except Exception as e:
                self.load_error = f"{type(e).__name__}: {e}"
                print(f"[pipeline] 로딩 실패 — {self.load_error}")
                raise

    def _require(self) -> Any:
        if not self.loaded or self._pipeline is None:
            raise PipelineNotReady(self.load_error or "파이프라인이 아직 준비되지 않았습니다.")
        return self._pipeline

    # ── 1단계: 전처리 ───────────────────────────────────────────────

    def preprocess(self, image_path: Path, out_path: Path) -> Path:
        """배경 제거 + 정사각 패딩. 생성 전에 사용자가 결과를 확인할 수 있게
        따로 뗀다 — 배경 제거가 틀리면 생성은 무조건 실패한다."""
        pipeline = self._require()
        progress.set_stage("배경 제거")

        img = Image.open(image_path)
        processed = pipeline.preprocess_image(img)
        processed.save(out_path)
        return out_path

    # ── 2단계: 생성 ─────────────────────────────────────────────────

    def generate(
        self,
        processed_path: Path,
        *,
        seed: int = 42,
        resolution: int | None = None,
        fov: float = -1.0,
        mesh_scale: float = 1.0,
        extend_pixel: int = 0,
        image_resolution: int = 512,
        max_num_tokens: int = 49152,
        steps: int = 12,
    ) -> dict:
        """3단 캐스케이드를 돌려 메시와 잠재를 만든다."""
        import torch

        pipeline = self._require()
        up = self._upstream
        resolution = resolution or SETTINGS.resolution

        # ── 카메라 파라미터 ──
        if fov > 0:
            progress.set_stage("카메라 설정")
            distance = up.distance_from_fov(
                fov,
                torch.tensor([-1.0, 0.0, 0.0]),
                torch.tensor([0 - extend_pixel, image_resolution - 1 + extend_pixel]),
                mesh_scale,
                image_resolution,
            )["distance_from_x"]
            camera_params = {
                "camera_angle_x": float(fov),
                "distance": distance,
                "mesh_scale": mesh_scale,
            }
        else:
            progress.set_stage("카메라 추정 (MoGe)")
            moge = up.load_moge_model(device="cuda")
            try:
                camera_params = up.get_camera_params_wild_moge(
                    str(processed_path), moge, device="cuda",
                    mesh_scale=mesh_scale, extend_pixel=extend_pixel,
                    image_resolution=image_resolution,
                )
            finally:
                # MoGe 는 카메라 추정에만 쓴다. 생성 단계에 VRAM 을 넘겨야 한다.
                moge.cpu()
                del moge
                torch.cuda.empty_cache()

        # ── 생성 ──
        progress.set_stage("생성 준비")

        # 같은 seed 로 돌려도 결과가 미세하게 달라진다 (S6 측정 결과, 정점 수
        # 편차 약 0.7%). 전처리 결과는 비트 단위로 같으므로 원인은 생성 쪽이다.
        #
        # DETERMINISTIC=1 은 cuDNN 자동선택과 TF32 를 끈다. 실측해 보니
        # **효과가 없었다** — 재현은 여전히 안 되고 소요만 70% 늘었다.
        # 원인이 그 둘이 아니라는 뜻이다(희소 복셀 커널의 atomic 누적 순서가
        # 유력하다). 기본값은 꺼짐이고, 이 분기는 "이미 해봤고 안 된다"를
        # 남겨두기 위해 유지한다. 측정값은 docs/BENCHMARK.md.
        if SETTINGS.deterministic:
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
            torch.backends.cudnn.allow_tf32 = False
            torch.backends.cuda.matmul.allow_tf32 = False

        torch.manual_seed(seed)          # CUDA 장치 seed 도 함께 설정된다
        torch.cuda.reset_peak_memory_stats()

        sampler = lambda gs, gr, rt: {  # noqa: E731
            "steps": steps, "guidance_strength": gs,
            "guidance_rescale": gr, "rescale_t": rt,
        }

        image = Image.open(processed_path)
        mesh_list, (shape_slat, tex_slat, res) = pipeline.run(
            image,
            camera_params=camera_params,
            seed=seed,
            sparse_structure_sampler_params=sampler(7.5, 0.7, 5.0),
            shape_slat_sampler_params=sampler(7.5, 0.5, 3.0),
            tex_slat_sampler_params=sampler(1.0, 0.0, 3.0),
            preprocess_image=False,   # 1단계에서 이미 했다
            return_latent=True,
            pipeline_type=f"{resolution}_cascade",
            max_num_tokens=max_num_tokens,
        )

        peak_gb = torch.cuda.max_memory_allocated() / 2**30

        return {
            "mesh": mesh_list[0],
            "latent": (shape_slat, tex_slat, res),
            # extract_glb 가 요구하는 값. run() 이 돌려준 res 를 그대로 전달한다.
            "grid_size": int(res),
            "camera_params": camera_params,
            "peak_vram_gb": round(peak_gb, 2),
            "resolution": resolution,
        }

    # ── 3단계: GLB 추출 ─────────────────────────────────────────────

    def extract_glb(
        self,
        mesh: Any,
        out_path: Path,
        *,
        grid_size: int,
        texture_size: int,
        decimation_target: int,
        webp: bool | None = None,
    ) -> dict:
        """메시를 GLB 로 내보낸다. 같은 메시로 여러 번 부를 수 있다 —
        저장용 원본과 뷰어용 경량본을 이렇게 뽑는다."""
        import numpy as np
        import o_voxel

        pipeline = self._require()
        webp = SETTINGS.export_webp if webp is None else webp

        progress.set_stage(f"GLB 추출 ({texture_size}px)")

        glb = o_voxel.postprocess.to_glb(
            vertices=mesh.vertices,
            faces=mesh.faces,
            attr_volume=mesh.attrs,
            coords=mesh.coords,
            attr_layout=pipeline.pbr_attr_layout,
            grid_size=grid_size,
            aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
            decimation_target=decimation_target,
            texture_size=texture_size,
            remesh=True,
            remesh_band=1,
            remesh_project=0,
            use_tqdm=True,
        )

        # upstream inference.py 와 동일한 회전. 이걸 빼면 뷰어에서 누워서 나온다.
        glb.apply_transform(np.array([
            [-1,  0,  0, 0],
            [ 0,  0, -1, 0],
            [ 0, -1,  0, 0],
            [ 0,  0,  0, 1],
        ], dtype=np.float64))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        glb.export(str(out_path), extension_webp=webp)

        return {
            "path": out_path,
            "mb": round(out_path.stat().st_size / 2**20, 1),
            "vertices": int(len(glb.geometry[next(iter(glb.geometry))].vertices))
            if hasattr(glb, "geometry") and glb.geometry else 0,
        }


RUNNER = Pixal3DRunner()
