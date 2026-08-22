"""upstream inference.py 를 셰임을 적용한 상태로 실행하는 래퍼.

upstream 을 수정하지 않는다는 원칙(PLAN.md §6)을 지키면서 우리 쪽 조정만
얹어서 돌리기 위한 얇은 껍데기다. 하는 일은 네 가지뿐이다.

  1. 저장 위치를 프로젝트 안으로 고정한다
  2. 배경 제거 모델 셰임을 적용한다
  3. sys.path 를 정리한다
  4. upstream inference.py 를 스크립트 그대로 실행한다

인자는 inference.py 와 완전히 같다. 그대로 넘어간다.

    python tools/run_inference.py --image ... --output ... --low_vram --resolution 1024


sys.path 를 정리하는 이유 (실제로 겪은 문제):
    NAF 업샘플러는 torch.hub 로 valeoai/NAF 를 받아오는데, 그 hubconf.py 가
    `from src.model.naf import NAF` 를 한다. 프로젝트 루트가 sys.path 에 남아
    있으면 우리 폴더가 그 `src` 를 가려버려 ModuleNotFoundError 가 난다.
    (그래서 우리 폴더 이름도 src 가 아니라 tools 다.)
    upstream 을 실행하기 직전에 우리 경로를 걷어내 충돌 여지를 없앤다.
"""

from __future__ import annotations

import importlib.util
import os
import runpy
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "upstream" / "Pixal3D"


def _load_by_path(name: str, path: Path):
    """파일 경로로 직접 불러온다.

    패키지 import(`from server.config import ...`)를 쓰지 않는 이유는
    프로젝트 루트를 sys.path 에 올려두지 않기 위해서다. 올려두면 위 주석의
    hubconf 충돌이 되살아난다. 여기서 쓰는 이름은 `_pixal3d_` 로 시작해
    남의 모듈명과 겹치지 않는다.

    exec_module 전에 sys.modules 에 등록해야 한다. @dataclass 가 필드 타입을
    해석할 때 sys.modules[cls.__module__] 를 찾기 때문에, 등록하지 않으면
    AttributeError: 'NoneType' object has no attribute '__dict__' 로 죽는다.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"불러올 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ── 1. 저장 위치 고정 ───────────────────────────────────────────────
# 가중치·캐시·임시파일이 AppData 나 사용자 홈으로 새지 않게 한다.
_config = _load_by_path("_pixal3d_config", ROOT / "server" / "config.py")
_config.pin_paths()

# ── 2. upstream 을 import 가능하게 만든다 ───────────────────────────
# inference.py 가 상대 경로로 assets 를 찾으므로 작업 디렉터리도 옮긴다.
sys.path.insert(0, str(UPSTREAM))
os.chdir(UPSTREAM)

# ── 3. 배경 제거 모델 셰임 ──────────────────────────────────────────
# pixal3d 를 import 한 뒤여야 패치할 대상이 존재한다.
import pixal3d.pipelines  # noqa: E402, F401

_shim = _load_by_path("_pixal3d_rembg_shim", ROOT / "tools" / "shims" / "rembg_model.py")
if not os.environ.get(_shim.ENV_VAR):
    print("[rembg] PIXAL3D_REMBG_MODEL 미설정 — upstream 기본값(briaai/RMBG-2.0)을 씁니다.")

# ── 4. sys.path 정리 ────────────────────────────────────────────────
# 프로젝트 루트가 남아 있으면 torch.hub 로 받는 코드의 import 를 가릴 수 있다.
_root_str = str(ROOT)
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _root_str]

# ── 5. 실행 ─────────────────────────────────────────────────────────
# runpy 로 __main__ 처럼 돌린다. inference.py 의 argparse 가 그대로 동작한다.
sys.argv[0] = str(UPSTREAM / "inference.py")
runpy.run_path(str(UPSTREAM / "inference.py"), run_name="__main__")
