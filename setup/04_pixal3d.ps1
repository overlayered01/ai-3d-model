<#
  Phase 2 (계속) — Pixal3D 파이썬 의존성 설치

  CUDA 확장(03) 이 끝난 뒤에 실행한다.

  upstream requirements.txt 를 그대로 설치하면 실패한다. 두 가지 이유가 있다:

  1) 의존성 충돌
     requirements.txt 는 trimesh==4.10.1 로 고정하면서 동시에 MoGe 를
     버전 없이 git 에서 끌어온다. 현재 MoGe(3.0.0)는 trimesh>=4.11 을
     요구하므로 pip 이 해결하지 못한다.

  2) 더 위험한 문제 — MoGe 3.0.0 의 의존성 목록에
     `flex-gemm @ git+https://github.com/JeffreyXiang/FlexGEMM.git` 가 있다.
     의존성을 그대로 따라가면 방금 설치한 사전빌드 flex_gemm_ap 휠을
     소스 빌드로 덮어쓰려 시도한다. Windows 에서 이 빌드는 실패하거나,
     성공해도 우리가 맞춰놓은 스택을 깨뜨린다.

  그래서 MoGe 를 분리해 --no-deps 로 설치하고, 실제로 필요한 것만 따로 넣는다.
  Pixal3D 는 MoGe 를 카메라 FOV 추정에만 쓰며 `moge.model.v2.MoGeModel` 하나만
  import 한다. 이 경로는 trimesh/gradio/flex-gemm 을 건드리지 않는다.

  사용법:  powershell -ExecutionPolicy Bypass -File setup\04_pixal3d.ps1
#>

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"

$Root     = Split-Path -Parent $PSScriptRoot
$Upstream = Join-Path $Root "upstream\Pixal3D"

if (-not (Test-Path $VenvPy))   { Write-Host "  [FAIL] .venv 없음. 01 부터 실행하세요." -ForegroundColor Red; exit 1 }
if (-not (Test-Path $Upstream)) { Write-Host "  [FAIL] upstream\Pixal3D 없음." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  Phase 2 · Pixal3D 의존성" -ForegroundColor Cyan
Write-Host "  ----------------------------------------------------------------"

# 나중에 갈아엎혔는지 비교하기 위해 기록해 둔다
$torchBefore    = & $VenvPy -c "import torch; print(torch.__version__)" 2>&1
$flexGemmBefore = & $VenvPy -c "import importlib.metadata as m; print(m.version('flex-gemm-ap'))" 2>&1

# ── 1. requirements.txt 에서 MoGe 줄만 제외하고 설치 ───────────────────
$reqSrc = Join-Path $Upstream "requirements.txt"
# $env:TEMP 는 _env.ps1 이 프로젝트 안(cache	mp)으로 돌려놓았다.
$reqTmp = Join-Path $env:TEMP "pixal3d-requirements-nomoge.txt"
Get-Content $reqSrc | Where-Object { $_ -notmatch 'MoGe' } | Set-Content $reqTmp -Encoding utf8

Write-Host "  ...... requirements.txt (MoGe 제외)"
& $VenvPy -m pip install -r $reqTmp
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] requirements 설치 실패." -ForegroundColor Red
    exit 1
}
Write-Host "  [ OK ] requirements.txt — trimesh 는 upstream 이 고정한 4.10.1 유지" -ForegroundColor Green

# ── 2. MoGe — 의존성 없이 ──────────────────────────────────────────────
Write-Host "  ...... MoGe (--no-deps)"
& $VenvPy -m pip install --no-deps "git+https://github.com/microsoft/MoGe.git"
if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] MoGe 설치 실패." -ForegroundColor Red; exit 1 }

# utils3d — 두 가지가 필요하다. 모듈명이 달라 공존한다.
#   utils3d       : Pixal3D 가 쓰는 0.0.2 휠
#   utils3d_moge  : MoGe 의 포크. v2.py 가 utils3d_moge 를 먼저 찾고
#                   없으면 utils3d 로 폴백하는데, API 가 달라 폴백은 깨진다.
Write-Host "  ...... utils3d (Pixal3D 0.0.2)"
& $VenvPy -m pip install "https://github.com/LDYang694/Storages/releases/download/20260430/utils3d-0.0.2-py3-none-any.whl"
if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] utils3d 설치 실패." -ForegroundColor Red; exit 1 }

Write-Host "  ...... utils3d_moge (MoGe 포크, 커밋 고정)"
& $VenvPy -m pip install --no-deps "git+https://github.com/EasternJournalist/utils3d-moge.git@62f09d58509485564e24d5d9f6aac9ee9ebc0c37"
if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] utils3d_moge 설치 실패." -ForegroundColor Red; exit 1 }
Write-Host "  [ OK ] utils3d + utils3d_moge" -ForegroundColor Green

# MoGe 가 import 시 실제로 필요로 하는 것만 보충한다.
# 제외: trimesh(4.10.1 유지), flex-gemm(사전빌드 휠 보호), gradio>=6(upstream 이 별도 지정)
# einops 는 배경 제거 모델(BiRefNet/RMBG-2.0)이 trust_remote_code 로 받아오는
# 모델링 파일이 요구한다. requirements.txt 에는 없어서 따로 넣어야 한다.
Write-Host "  ...... MoGe 보조 의존성 + einops"
& $VenvPy -m pip install click scipy matplotlib huggingface-hub requests einops
if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] MoGe 보조 의존성 실패." -ForegroundColor Red; exit 1 }
Write-Host "  [ OK ] MoGe" -ForegroundColor Green

# ── 3. 웹 서버 의존성 (Phase 4에서 사용) ───────────────────────────────
Write-Host "  ...... 웹 서버 의존성"
& $VenvPy -m pip install fastapi "uvicorn[standard]" python-multipart python-dotenv
if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] 웹 의존성 설치 실패." -ForegroundColor Red; exit 1 }
Write-Host "  [ OK ] fastapi / uvicorn / python-multipart / python-dotenv" -ForegroundColor Green

# ── 4. 스택이 갈아엎히지 않았는지 확인 ─────────────────────────────────
$torchAfter    = & $VenvPy -c "import torch; print(torch.__version__)" 2>&1
$flexGemmAfter = & $VenvPy -c "import importlib.metadata as m; print(m.version('flex-gemm-ap'))" 2>&1

if ($torchBefore -ne $torchAfter) {
    Write-Host ""
    Write-Host "  [FAIL] torch 가 교체되었습니다: $torchBefore -> $torchAfter" -ForegroundColor Red
    Write-Host "         setup\02_torch.ps1 과 setup\03_cuda_wheels.ps1 을 다시 실행하세요."
    exit 1
}
if ($flexGemmBefore -ne $flexGemmAfter) {
    Write-Host ""
    Write-Host "  [FAIL] flex_gemm 이 교체되었습니다: $flexGemmBefore -> $flexGemmAfter" -ForegroundColor Red
    Write-Host "         사전빌드 휠이 소스 빌드로 덮어써졌습니다. 03 을 다시 실행하세요."
    exit 1
}
Write-Host "  [ OK ] torch $torchAfter · flex-gemm-ap $flexGemmAfter 유지됨" -ForegroundColor Green

# ── 5. 모듈명 셰임 (리스크 R1) ─────────────────────────────────────────
# 03 에서 tqdm 이 없어 실패했을 수 있으므로 여기서 다시 시도한다.
Write-Host "  ...... 모듈명 셰임"
& $VenvPy (Join-Path $Root "tools\shims\install_shims.py")
if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] 셰임 설치 실패." -ForegroundColor Red; exit 1 }

# ── 6. MoGe API 호환 확인 ──────────────────────────────────────────────
# upstream 은 `from moge.model.v2 import MoGeModel` 을 쓴다. MoGe 는 버전 고정 없이
# 끌려오므로, 최신 MoGe 에서 이 경로가 살아있는지 확인해야 한다.
Write-Host "  ...... MoGe API 확인 (moge.model.v2.MoGeModel)"
& $VenvPy -c "from moge.model.v2 import MoGeModel; print('  [ OK ] MoGeModel import 가능')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] upstream 이 쓰는 MoGe API 가 없습니다." -ForegroundColor Red
    Write-Host "         MoGe 최신판이 API 를 옮겼을 수 있습니다. 구버전 커밋으로 고정해 보세요."
    exit 1
}

# ── 7. spaces (upstream app.py 비교 검증용, 선택) ──────────────────────
Write-Host "  ...... spaces (선택)"
& $VenvPy -m pip install --quiet spaces 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [ OK ] spaces" -ForegroundColor Green
} else {
    Write-Host "  [WARN] spaces 없음 — upstream app.py 직접 실행만 막힙니다." -ForegroundColor Yellow
    Write-Host "         우리 웹 서버(server/)는 이 패키지 없이 동작합니다."
}

Write-Host ""
Write-Host "  ...... 검증"
& $VenvPy "$Root\setup\doctor.py"
$gate = $LASTEXITCODE

Write-Host "  ----------------------------------------------------------------"
if ($gate -eq 0) {
    Write-Host "  게이트 G2 통과. 다음: powershell -File setup\05_weights.ps1" -ForegroundColor Green
} else {
    Write-Host "  미통과 — 위 조치 사항을 확인하세요." -ForegroundColor Red
}
Write-Host ""
exit $gate
