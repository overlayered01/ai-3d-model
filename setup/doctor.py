"""환경 자가진단 — 게이트 G1/G2의 실행체.

계획서의 Phase 1(G1)과 Phase 2(G2) 통과 여부를 기계적으로 판정한다.
설치 도중 아무 때나 돌려도 안전하며, 실패 항목마다 다음에 무엇을 할지 알려준다.

사용법:
    python setup/doctor.py            # 전체 진단
    python setup/doctor.py --gate g1  # G1 항목만
    python setup/doctor.py --gate g2  # G2 항목만
    python setup/doctor.py --json     # 기계 판독용 출력

종료 코드: 0 = 요청한 게이트 통과, 1 = 실패 항목 있음
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import shutil
import sys
from dataclasses import dataclass, field, asdict
from typing import Callable

# ── 계획서에 고정한 목표 스택 (PLAN.md §4 Track A 버전 고정표) ──────────
TARGET_PY = (3, 12)
TARGET_TORCH_MAJOR_MINOR = "2.10"
TARGET_CUDA = "13.0"
TARGET_CAPABILITY = (8, 6)  # RTX 3090 = Ampere sm_86

# upstream이 import하는 이름 → 휠이 설치할 수 있는 대체 이름 (리스크 R1)
KERNEL_ALIASES = {
    "flex_gemm": ["flex_gemm", "flex_gemm_ap"],
    "cumesh": ["cumesh", "cumesh_vb"],
    # o_voxel_vb 를 _ap 보다 앞에 둔다. _ap 판에는 postprocess 가 없다.
    "o_voxel": ["o_voxel", "o_voxel_vb", "o_voxel_vb_ap"],
}

# import 가 되는 것만으로는 부족한 것들. 실제로 쓰는 서브모듈까지 확인한다.
# o_voxel_vb_ap 는 import 는 멀쩡히 되지만 postprocess 가 없어서,
# 생성이 다 끝난 뒤 GLB 추출 직전에야 AttributeError 로 죽는다.
KERNEL_SUBMODULES = {"o_voxel": ["postprocess"]}

OK, WARN, FAIL = "OK", "WARN", "FAIL"

# Windows 콘솔 기본 코드페이지(cp949)는 em dash/체크마크를 인코딩하지 못한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""
    fix: str = ""
    gate: str = "g2"


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, *args, **kwargs) -> Result:
        r = Result(*args, **kwargs)
        self.results.append(r)
        return r


# ══════════════════════════════════════════════════════════════════════
# G1 — 파이썬 · torch · GPU
# ══════════════════════════════════════════════════════════════════════

def check_python(rep: Report) -> None:
    v = sys.version_info
    got = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) == TARGET_PY:
        rep.add("Python", OK, f"{got} ({sys.executable})", gate="g1")
    else:
        rep.add(
            "Python", FAIL, f"{got} — 목표는 3.12.x",
            "setup/01_python_venv.ps1 로 3.12.10 venv를 만들고 그 python으로 실행하라. "
            "전역 3.14는 torch/커뮤니티 휠이 지원하지 않는다.",
            gate="g1",
        )


def check_venv(rep: Report) -> None:
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv:
        rep.add("가상환경", OK, sys.prefix, gate="g1")
    else:
        rep.add(
            "가상환경", WARN, "venv 밖에서 실행 중",
            "전역 환경을 오염시키지 않도록 .venv 를 활성화한 뒤 실행하라.",
            gate="g1",
        )


def check_cache_redirect(rep: Report) -> None:
    """가중치·캐시·임시파일이 프로젝트 안에 있는지 본다 (리스크 R5).

    프로젝트 밖(AppData, 사용자 홈)으로 새면 두 가지가 문제다.
    C: 여유가 55GB뿐이라 25GB 가중치를 받다가 실패하고,
    프로젝트를 옮기거나 지울 때 그 파일들이 따라오지 않는다.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_root = os.path.abspath(os.environ.get("PIXAL3D_DATA_ROOT") or root)

    problems = []
    for var in ("HF_HOME", "TORCH_HOME", "TMP", "TRITON_CACHE_DIR"):
        val = os.environ.get(var, "")
        if not val:
            problems.append(f"{var} 미설정")
        elif not os.path.abspath(val).lower().startswith(data_root.lower()):
            problems.append(f"{var}={val}")

    if problems:
        rep.add(
            "저장 위치", WARN, "; ".join(problems),
            "setup/_env.ps1 을 dot-source 한 셸에서 실행하라:  . .\\setup\\_env.ps1\n"
            "    파이썬을 직접 부르는 경우 server.config 를 import 하면 같은 값이 강제된다.",
            gate="g1",
        )
    else:
        rep.add("저장 위치", OK, f"프로젝트 안 ({data_root})", gate="g1")


def check_torch(rep: Report) -> "object | None":
    try:
        import torch
    except ImportError as e:
        rep.add(
            "torch", FAIL, f"import 실패: {e}",
            "setup/02_torch.ps1 을 실행하라 (torch 2.10.0+cu130).",
            gate="g1",
        )
        return None

    ver = torch.__version__
    cuda_ver = getattr(torch.version, "cuda", None)
    detail = f"{ver} (CUDA {cuda_ver})"
    if ver.startswith(TARGET_TORCH_MAJOR_MINOR) and cuda_ver == TARGET_CUDA:
        rep.add("torch", OK, detail, gate="g1")
    else:
        rep.add(
            "torch", WARN,
            f"{detail} — 목표는 {TARGET_TORCH_MAJOR_MINOR}.x + CUDA {TARGET_CUDA}",
            "커뮤니티 CUDA 휠은 torch/CUDA 버전 태그가 정확히 맞아야 import된다. "
            "버전이 다르면 그 조합에 맞는 휠을 다시 찾아야 한다.",
            gate="g1",
        )
    return torch


def check_triton(rep: Report) -> None:
    """flex_gemm 휠이 Triton 커널을 쓴다 — 없으면 G2 에서 import 가 깨진다.

    G1 에 두는 이유가 있다. torch 2.10.0 의 triton 핀에는
    `; platform_system == "Linux"` 조건이 붙어 있어 Windows 에서는 pip 이
    조용히 건너뛴다. 그래서 torch 설치는 성공했는데 triton 만 없는 상태가
    자연스럽게 만들어지고, 그 사실은 한참 뒤 G2 에서야 드러난다.
    """
    try:
        import triton
    except Exception as e:
        rep.add(
            "triton", FAIL, f"import 실패: {type(e).__name__}: {e}",
            "공식 triton 에는 Windows 휠이 없다. triton-windows 를 설치하라: "
            "pip install triton-windows==3.6.0.post26 (setup/02_torch.ps1). "
            "torch 가 요구하는 앞 세 자리에 맞춘다.",
            gate="g1",
        )
        return
    rep.add("triton", OK, getattr(triton, "__version__", "설치됨"), gate="g1")


def check_gpu(rep: Report, torch) -> None:
    if torch is None:
        rep.add("CUDA 가용성", FAIL, "torch 없음", "torch 설치 후 재실행", gate="g1")
        return

    if not torch.cuda.is_available():
        rep.add(
            "CUDA 가용성", FAIL, "torch.cuda.is_available() == False",
            "CPU 전용 torch가 설치됐거나 드라이버 문제다. "
            "nvidia-smi 가 동작하는지 확인하고 cu130 인덱스로 재설치하라.",
            gate="g1",
        )
        return

    name = torch.cuda.get_device_name(0)
    cap = torch.cuda.get_device_capability(0)
    total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    free, _ = torch.cuda.mem_get_info()
    free_gb = free / (1024 ** 3)

    detail = f"{name} sm_{cap[0]}{cap[1]} · {total:.1f}GB (여유 {free_gb:.1f}GB)"
    if cap == TARGET_CAPABILITY:
        rep.add("GPU", OK, detail, gate="g1")
    else:
        rep.add(
            "GPU", WARN, f"{detail} — 계획서는 sm_86(RTX 3090) 기준",
            f"sm_{cap[0]}{cap[1]} 용 natten/CUDA 휠을 따로 찾아야 한다.",
            gate="g1",
        )

    # VRAM 여유 — 계획서 리스크 R4
    if free_gb < 18:
        rep.add(
            "VRAM 여유", WARN, f"{free_gb:.1f}GB 사용 가능",
            "브라우저·Slack 등 GPU를 쓰는 앱을 종료하면 2~3GB를 회수할 수 있다. "
            "1024 + low_vram 로도 부족하면 이 회수가 필요하다.",
            gate="g1",
        )
    else:
        rep.add("VRAM 여유", OK, f"{free_gb:.1f}GB 사용 가능", gate="g1")


# ══════════════════════════════════════════════════════════════════════
# G2 — CUDA 확장 · 파이프라인 의존성
# ══════════════════════════════════════════════════════════════════════

def _try_import(names: list[str]) -> tuple[str, object] | None:
    for n in names:
        try:
            return n, importlib.import_module(n)
        except Exception:
            continue
    return None


def check_kernels(rep: Report) -> None:
    """flex_gemm / cumesh / o_voxel — 리스크 R1의 판정 지점."""
    for canonical, candidates in KERNEL_ALIASES.items():
        hit = _try_import(candidates)
        if hit is None:
            rep.add(
                canonical, FAIL, f"import 실패 ({' 또는 '.join(candidates)})",
                "setup/03_cuda_wheels.ps1 을 실행하라. "
                "torch/CUDA/Python 태그가 정확히 맞는 win_amd64 휠이어야 한다.",
            )
            continue

        found, mod = hit
        loc = getattr(mod, "__file__", "?")

        # 서브모듈까지 확인 — import 성공만으로는 판정할 수 없는 것이 있다.
        missing = []
        for sub in KERNEL_SUBMODULES.get(canonical, []):
            try:
                importlib.import_module(f"{found}.{sub}")
            except Exception:
                if not hasattr(mod, sub):
                    missing.append(sub)
        if missing:
            rep.add(
                canonical, FAIL,
                f"'{found}' 에 {', '.join(missing)} 서브모듈이 없다",
                "설치된 휠 판이 잘못됐다. o_voxel 은 _ap 가 아니라 o_voxel_vb 를 써야 한다. "
                "setup/03_cuda_wheels.ps1 을 다시 실행하고 tools/shims/install_shims.py 로 "
                "셰임 우선순위를 다시 심어라.",
            )
            continue

        if found == canonical:
            rep.add(canonical, OK, f"{found} · {loc}")
        else:
            # 휠이 대체 이름으로 설치됨 → upstream의 import가 깨진다
            rep.add(
                canonical, WARN,
                f"'{found}' 로만 import됨 — upstream은 '{canonical}' 를 import한다",
                f"tools/shims/{canonical}.py 에 재수출 셰임을 만들어라:\n"
                f"        import sys, {found} as _m\n"
                f"        sys.modules['{canonical}'] = _m\n"
                f"        from {found} import *  # noqa",
            )


def check_natten(rep: Report) -> None:
    try:
        import natten
    except ImportError as e:
        rep.add(
            "natten", FAIL, f"import 실패: {e}",
            "Ampere sm86 win_amd64 휠을 설치하라 (setup/03_cuda_wheels.ps1).",
        )
        return

    ver = getattr(natten, "__version__", "?")
    has_lib = bool(getattr(natten, "HAS_LIBNATTEN", False))
    if has_lib:
        rep.add("natten", OK, f"{ver} · HAS_LIBNATTEN=True")
    else:
        rep.add(
            "natten", FAIL, f"{ver} · HAS_LIBNATTEN=False",
            "CUDA libnatten이 없는 순수 파이썬 natten이다. NAF 업샘플러가 동작하지 않는다. "
            "Python/torch/CUDA/GPU 아키텍처가 모두 맞는 휠을 설치하거나, "
            "NAF 폴백 경로를 쓰도록 파이프라인을 조정해야 한다 (품질 하락 감수).",
        )

    # 계획서 리스크 R2: upstream requirements는 0.21.0을 요구, 가용 휠은 0.21.6
    if ver != "?" and not str(ver).startswith("0.21"):
        rep.add(
            "natten 버전", WARN, f"{ver} — upstream 요구는 0.21.x",
            "API 호환 여부를 확인하라.",
        )


def check_nvdiffrast(rep: Report) -> None:
    """UV 텍스처 베이킹에 쓴다 — 없으면 생성이 다 끝난 뒤 GLB 추출에서 죽는다.

    계획서 rev.1/rev.2 는 이것을 '렌더 프리뷰용이라 생략 가능'으로 봤는데
    틀린 판단이었다. o_voxel/postprocess.py 가 직접 import 한다.
    """
    try:
        import nvdiffrast.torch as dr  # noqa: F401
    except Exception as e:
        rep.add(
            "nvdiffrast", FAIL, f"import 실패: {type(e).__name__}: {e}",
            "o_voxel.postprocess.to_glb 가 UV 베이킹에 쓴다. "
            "setup/03_cuda_wheels.ps1 을 실행하라 (사전빌드 휠 있음).",
        )
        return
    import nvdiffrast
    rep.add("nvdiffrast", OK, getattr(nvdiffrast, "__version__", "설치됨"))


def check_python_deps(rep: Report) -> None:
    for mod, fix in (
        ("utils3d", "utils3d-0.0.2-py3-none-any.whl 을 설치하라 (setup/04_pixal3d.ps1)."),
        ("moge", "pip install git+https://github.com/microsoft/MoGe.git"),
        ("trimesh", "pip install -r upstream/Pixal3D/requirements.txt"),
        ("transformers", "pip install -r upstream/Pixal3D/requirements.txt"),
        ("diffusers", "pip install -r upstream/Pixal3D/requirements.txt"),
        ("fastapi", "pip install fastapi uvicorn python-multipart"),
        ("uvicorn", "pip install fastapi uvicorn python-multipart"),
    ):
        try:
            m = importlib.import_module(mod)
        except Exception as e:
            # 실제 메시지를 보여준다. "ModuleNotFoundError" 만으로는
            # 이 패키지가 없는 건지, 이 패키지가 부르는 다른 것이 없는 건지
            # 구분되지 않아 엉뚱한 곳을 고치게 된다.
            rep.add(mod, FAIL, f"import 실패: {type(e).__name__}: {e}", fix)
            continue

        # 버전 조회는 import 성공 여부와 분리한다.
        # utils3d 처럼 PEP 562 지연 로딩을 쓰는 패키지는 없는 속성에 접근하면
        # 서브모듈 import 를 시도해 ModuleNotFoundError 를 던진다.
        # getattr 의 기본값은 AttributeError 만 잡으므로 여기서 함께 삼켜야
        # 멀쩡한 패키지가 실패로 잡히지 않는다.
        try:
            ver = str(m.__version__)
        except Exception:
            ver = "설치됨"
        rep.add(mod, OK, ver)


def check_upstream(rep: Report) -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    up = os.path.join(root, "upstream", "Pixal3D")
    if not os.path.isdir(up):
        rep.add(
            "upstream/Pixal3D", FAIL, "디렉터리 없음",
            "git clone --branch master https://github.com/TencentARC/Pixal3D.git upstream/Pixal3D",
        )
        return

    missing = [f for f in ("inference.py", "app.py", "pixal3d", "requirements.txt")
               if not os.path.exists(os.path.join(up, f))]
    if missing:
        rep.add("upstream/Pixal3D", FAIL, f"누락: {', '.join(missing)}", "클론을 다시 하라.")
        return

    rep.add("upstream/Pixal3D", OK, up)

    # pixal3d 패키지가 import 가능한지 (sys.path에 upstream이 있어야 함)
    sys.path.insert(0, up)
    try:
        importlib.import_module("pixal3d")
        rep.add("pixal3d 패키지", OK, "import 가능")
    except Exception as e:
        rep.add(
            "pixal3d 패키지", FAIL, f"{type(e).__name__}: {e}",
            "위의 CUDA 확장 항목이 먼저 통과해야 한다. "
            "그 항목들이 모두 OK인데도 실패하면 누락된 의존성을 개별 설치하라.",
        )
    finally:
        sys.path.pop(0)


def check_disk(rep: Report) -> None:
    for drive in ("D:\\", "C:\\"):
        if not os.path.exists(drive):
            continue
        free = shutil.disk_usage(drive).free / (1024 ** 3)
        if drive == "D:\\":
            status = OK if free > 100 else WARN
            fix = "" if status == OK else "가중치와 실행 산출물을 담을 공간이 부족하다."
        else:
            status = OK if free > 30 else WARN
            fix = "" if status == OK else "캐시가 C: 로 새지 않는지 확인하라 (HF_HOME/PIP_CACHE_DIR)."
        rep.add(f"디스크 {drive}", status, f"{free:.0f}GB 여유", fix, gate="g1")


# ══════════════════════════════════════════════════════════════════════

def run(gate: str) -> Report:
    rep = Report()

    check_python(rep)
    check_venv(rep)
    check_cache_redirect(rep)
    check_disk(rep)
    torch = check_torch(rep)
    check_triton(rep)
    check_gpu(rep, torch)

    # G2 는 03_cuda_wheels.ps1 의 책임 범위다 — CUDA 확장까지.
    if gate in ("all", "g2"):
        check_upstream(rep)
        check_kernels(rep)
        check_natten(rep)
        check_nvdiffrast(rep)

    # 파이썬 의존성은 04_pixal3d.ps1 이 설치한다. G2 에 넣으면 03 이
    # 자기가 설치하지도 않은 것을 검사하다 떨어져, 클린 설치가 03 에서
    # 영원히 막힌다. 04 는 --gate 없이(=all) 부르므로 여기서 검사된다.
    if gate == "all":
        check_python_deps(rep)

    if gate == "g1":
        rep.results = [r for r in rep.results if r.gate == "g1"]

    return rep


def render(rep: Report, gate: str) -> int:
    icon = {OK: "  OK  ", WARN: " WARN ", FAIL: " FAIL "}
    width = max(len(r.name) for r in rep.results) + 2

    print()
    print(f"  Pixal3D 환경 진단 — {platform.system()} {platform.release()}")
    print(f"  게이트: {gate.upper()}")
    print("  " + "─" * 74)

    for r in rep.results:
        print(f"  [{icon[r.status]}] {r.name.ljust(width)} {r.detail}")

    fails = [r for r in rep.results if r.status == FAIL]
    warns = [r for r in rep.results if r.status == WARN]

    if fails or warns:
        print()
        print("  " + "─" * 74)
        print("  조치 사항")
        print()
        for r in fails + warns:
            if not r.fix:
                continue
            print(f"  {r.status} · {r.name}")
            for line in r.fix.split("\n"):
                print(f"      {line}")
            print()

    print("  " + "─" * 74)
    if fails:
        print(f"  ✗ {gate.upper()} 미통과 — FAIL {len(fails)}건, WARN {len(warns)}건")
    elif warns:
        print(f"  ✓ {gate.upper()} 통과 (WARN {len(warns)}건 — 확인 권장)")
    else:
        print(f"  ✓ {gate.upper()} 통과")
    print()

    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Pixal3D 환경 자가진단")
    ap.add_argument("--gate", choices=["all", "g1", "g2"], default="all")
    ap.add_argument("--json", action="store_true", help="기계 판독용 JSON 출력")
    args = ap.parse_args()

    rep = run(args.gate)

    if args.json:
        payload = {
            "gate": args.gate,
            "pass": not any(r.status == FAIL for r in rep.results),
            "results": [asdict(r) for r in rep.results],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["pass"] else 1

    return render(rep, args.gate)


if __name__ == "__main__":
    raise SystemExit(main())
