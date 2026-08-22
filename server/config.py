"""서버 설정 — .env 를 읽고 기본값을 확정한다.

기본값의 근거는 계획서(PLAN.md)에 있다. 특히:
  - HOST 는 127.0.0.1 이다. upstream app.py 의 share=True 와 정반대다 (R8).
  - 해상도 기본값은 1024 + low_vram 이다. 24GB 에서 1536 은 OOM 을 전제한다 (R4).
  - GLB 는 두 벌 뽑는다. 뷰어용 경량본과 저장용 원본 (R9).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "upstream" / "Pixal3D"
RUNS = ROOT / "runs"
WEB = ROOT / "web"

# 이 프로젝트가 만드는 파일은 전부 프로젝트 폴더 안에 둔다.
# setup/_env.ps1 과 같은 규칙이며, 파이썬을 직접 실행할 때도 동일해야 한다.
DATA_ROOT = Path(os.environ.get("PIXAL3D_DATA_ROOT") or ROOT)
MODELS = DATA_ROOT / "models"
CACHE = DATA_ROOT / "cache"


def pin_paths() -> None:
    """가중치·캐시·임시파일이 AppData 나 사용자 홈으로 새지 않게 고정한다.

    비어 있을 때만 채우는 방식은 쓰지 않는다. TMP/TEMP 는 Windows 가 항상
    설정하므로 그 방식으로는 영원히 적용되지 않고, 낡은 전역 설정이 남아
    프로젝트 밖을 가리키는 경우도 실제로 있었다.
    """
    paths = {
        "HF_HOME": MODELS / "hf",
        "TORCH_HOME": MODELS / "torch",
        "TRITON_CACHE_DIR": CACHE / "triton",
        "TMP": CACHE / "tmp",
        "TEMP": CACHE / "tmp",
    }
    for key, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)

    # upstream 은 자기 폴더에 오토튜닝 캐시를 쓰려 하지만
    # 그곳은 '수정하지 않는다' 원칙의 대상이다.
    os.environ["FLEX_GEMM_AUTOTUNE_CACHE_PATH"] = str(CACHE / "flex_gemm_autotune.json")
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

    # Windows 콘솔 기본 코드페이지(cp949)로는 한글 로그가 깨진다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass


def _load_dotenv() -> None:
    """.env 를 읽어 os.environ 에 채운다. 이미 설정된 값은 덮지 않는다."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        # 셸에서 이미 준 값이 우선이다 — .env 는 기본값 모음일 뿐이다.
        if key and key not in os.environ:
            os.environ[key] = val


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "")
    if raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int

    low_vram: bool
    resolution: int

    full_texture_size: int
    full_decimation_target: int
    web_texture_size: int
    web_decimation_target: int
    export_webp: bool

    attn_backend: str
    hf_home: str

    @property
    def is_public(self) -> bool:
        """루프백이 아닌 주소에 바인딩하는가 — 인증이 없으므로 경고 대상이다."""
        return self.host not in ("127.0.0.1", "localhost", "::1")


def load() -> Settings:
    _load_dotenv()
    pin_paths()

    # upstream 코드가 import 시점에 읽는 값들이므로 여기서 먼저 확정한다.
    os.environ.setdefault("ATTN_BACKEND", "sdpa")
    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

    # 배경 제거 모델. upstream 이 지정한 briaai/RMBG-2.0 은 게이트된 저장소라
    # HF 계정·라이선스 동의·토큰이 있어야 받을 수 있고, 없으면 파이프라인이
    # 아예 만들어지지 않는다. ZhengPeng7/BiRefNet 은 게이트가 없고,
    # RMBG-2.0 이 애초에 이 아키텍처를 파인튜닝한 것이라 품질도 대체로 동등하다.
    # RMBG-2.0 을 쓰려면 이 값을 빈 문자열로 두고 setup/hf_login.ps1 로 인증한다.
    os.environ.setdefault("PIXAL3D_REMBG_MODEL", "ZhengPeng7/BiRefNet")

    low_vram = _bool("LOW_VRAM", True)

    return Settings(
        host=os.environ.get("HOST", "127.0.0.1").strip(),
        port=_int("PORT", 7860),
        low_vram=low_vram,
        # upstream 규칙과 동일: low_vram 이면 1024, 아니면 1536
        resolution=_int("DEFAULT_RESOLUTION", 1024 if low_vram else 1536),
        full_texture_size=_int("FULL_TEXTURE_SIZE", 4096),
        full_decimation_target=_int("FULL_DECIMATION_TARGET", 1_000_000),
        web_texture_size=_int("WEB_TEXTURE_SIZE", 2048),
        web_decimation_target=_int("WEB_DECIMATION_TARGET", 200_000),
        export_webp=_bool("EXPORT_WEBP", True),
        attn_backend=os.environ["ATTN_BACKEND"],
        hf_home=os.environ.get("HF_HOME", ""),
    )


SETTINGS = load()
