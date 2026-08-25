<#
  공용 환경 설정 — 다른 setup 스크립트와 서버 실행 스크립트가 dot-source 한다.

      . "$PSScriptRoot\_env.ps1"

  이 프로젝트가 만들어내는 모든 파일은 프로젝트 폴더 안에 둔다.
  AppData 나 사용자 홈에 흩어지지 않게 하는 것이 목적이며, 그래야
  폴더 하나만 복사하면 다른 PC 로 옮길 수 있고 지울 때도 한 번에 지워진다.

  경로는 전부 이 스크립트 위치에서 계산한다. 시스템 전역(User 수준)
  환경변수는 쓰지 않는다 — 프로젝트를 옮기면 곧바로 틀린 값이 되고,
  다른 파이썬 작업까지 이 캐시로 끌어들이기 때문이다.

  값은 이미 설정돼 있어도 덮어쓴다. 일부러 그렇게 한다:
    - TMP/TEMP 는 Windows 가 항상 설정하므로, 비어 있을 때만 넣는 방식으로는
      영원히 적용되지 않는다.
    - 예전 세션이나 낡은 전역 설정이 남아 프로젝트 밖(C: 등)을 가리키는 경우가
      실제로 있었다. 조용히 그쪽으로 25GB 를 쏟는 것보다 강제하는 편이 낫다.

  다른 위치를 쓰고 싶으면 PIXAL3D_DATA_ROOT 를 지정한다. 그 아래에 같은
  구조로 만들어진다.
#>

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DataRoot = if ($env:PIXAL3D_DATA_ROOT) { $env:PIXAL3D_DATA_ROOT } else { $ProjectRoot }

# ── venv 위치 ──────────────────────────────────────────────────────────
# 기본은 프로젝트 안의 .venv. PIXAL3D_VENV 로 다른 곳을 지정할 수 있다.
# 이 갈래가 있어야 S7(설치 절차가 클린 환경에서 재현되는가) 검증을,
# 지금 돌아가고 있는 환경을 부수지 않고 별도 venv 에 대고 할 수 있다.
#     $env:PIXAL3D_VENV = 'D:\...\.venv-clean'
#     powershell -File setup\01_python_venv.ps1   # 이하 04 까지
$VenvDir = if ($env:PIXAL3D_VENV) { $env:PIXAL3D_VENV } else { Join-Path $ProjectRoot '.venv' }
$VenvPy  = Join-Path $VenvDir 'Scripts\python.exe'

# ── 프로젝트 내부 저장 위치 ────────────────────────────────────────────
#   models\hf      HuggingFace 가중치 + 인증 토큰 (약 25GB)
#   models\torch   torch.hub 캐시 (NAF 업샘플러 등)
#   cache\pip      pip 다운로드 캐시
#   cache\wheels   직접 내려받은 휠 (natten 등)
#   cache\triton   Triton JIT 컴파일 캐시
#   cache\tmp      임시 파일 — pip 의 git 빌드(pip-req-build-*)가 여기로 간다
$env:HF_HOME          = Join-Path $DataRoot 'models\hf'
$env:TORCH_HOME       = Join-Path $DataRoot 'models\torch'
$env:PIP_CACHE_DIR    = Join-Path $DataRoot 'cache\pip'
$env:TRITON_CACHE_DIR = Join-Path $DataRoot 'cache\triton'

$env:TMP  = Join-Path $DataRoot 'cache\tmp'
$env:TEMP = $env:TMP

# flex_gemm 오토튜닝 결과. upstream 은 자기 폴더에 쓰려 하지만
# 그곳은 '수정하지 않는다' 원칙의 대상이므로 우리 캐시로 돌린다.
$env:FLEX_GEMM_AUTOTUNE_CACHE_PATH = Join-Path $DataRoot 'cache\flex_gemm_autotune.json'

foreach ($d in @(
    $env:HF_HOME, $env:TORCH_HOME, $env:PIP_CACHE_DIR,
    $env:TRITON_CACHE_DIR, $env:TMP, (Join-Path $DataRoot 'cache\wheels')
)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

# ── 런타임 ─────────────────────────────────────────────────────────────
# flash_attn 을 설치하지 않으므로 PyTorch SDPA 백엔드를 쓴다.
if (-not $env:ATTN_BACKEND) { $env:ATTN_BACKEND = 'sdpa' }
$env:OPENCV_IO_ENABLE_OPENEXR = '1'
# Windows 에서는 무시되지만(경고만 뜬다) Linux 로 옮겼을 때를 위해 남겨둔다.
if (-not $env:PYTORCH_CUDA_ALLOC_CONF) { $env:PYTORCH_CUDA_ALLOC_CONF = 'expandable_segments:True' }
# 배경 제거 모델. upstream 기본값 briaai/RMBG-2.0 은 게이트된 저장소라
# HF 계정·라이선스 동의·토큰이 필요하다. ZhengPeng7/BiRefNet 은 게이트가 없고,
# RMBG-2.0 이 애초에 이 아키텍처를 파인튜닝한 것이라 품질도 대체로 동등하다.
# RMBG-2.0 을 쓰려면 이 줄을 지우고 setup\hf_login.ps1 로 인증한다.
if (-not $env:PIXAL3D_REMBG_MODEL) { $env:PIXAL3D_REMBG_MODEL = 'ZhengPeng7/BiRefNet' }

# 파이썬 stdout/stderr 를 UTF-8 로 고정한다. 없으면 Windows 콘솔 기본
# 코드페이지(cp949)로 인코딩돼 한글 로그가 깨진다.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

# 심볼릭 링크 미지원 경고 — 동작에는 문제가 없고 디스크만 더 쓴다.
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
