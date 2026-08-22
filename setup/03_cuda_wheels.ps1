<#
  Phase 2 — CUDA 확장 설치 (게이트 G2, 최대 난관)

  설치 순서를 엄수한다. --no-deps 를 붙이는 이유는 이 휠들의 메타데이터가
  torch 를 요구사항으로 걸고 있어, 없으면 pip 이 방금 맞춰놓은 torch 를
  PyPI 의 CPU 판으로 갈아엎기 때문이다.

  설치하지 않는 것:
    - flash_attn : ATTN_BACKEND=sdpa 로 대체 (upstream README 공식 허용)
    - drtk / nvdiffrast : 서버사이드 렌더 프리뷰를 쓰지 않는다 (결정 D6)
                          브라우저 3D 뷰어가 그 역할을 대신한다.

  대상 스택 (전부 정확히 일치해야 한다):
    Python 3.12.10 · PyTorch 2.10.0+cu130 · CUDA 13.0 · Ampere sm_86

  사용법:  powershell -ExecutionPolicy Bypass -File setup\03_cuda_wheels.ps1
#>

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"
$Root   = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
$Cache  = Join-Path $Root "cache\wheels"

if (-not (Test-Path $VenvPy)) {
    Write-Host "  [FAIL] .venv 가 없습니다. setup\01_python_venv.ps1 부터 실행하세요." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $Cache)) { New-Item -ItemType Directory -Force -Path $Cache | Out-Null }

Write-Host ""
Write-Host "  Phase 2 · CUDA 확장 설치" -ForegroundColor Cyan
Write-Host "  ----------------------------------------------------------------"

# ── 0. 전제 확인 — torch 가 목표 버전인지 ──────────────────────────────
$torchInfo = & $VenvPy -c "import torch,sys; print(torch.__version__, torch.version.cuda)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] torch 가 없습니다. setup\02_torch.ps1 을 먼저 실행하세요." -ForegroundColor Red
    exit 1
}
Write-Host "  [ OK ] torch $torchInfo" -ForegroundColor Green
if ($torchInfo -notmatch '^2\.10\.' -or $torchInfo -notmatch '13\.0') {
    Write-Host "  [WARN] 휠은 torch2.10 + cu130 으로 빌드됐습니다. 조합이 다르면 import 가 깨집니다." -ForegroundColor Yellow
}

# ── 1. Pixal3D CUDA 커널 3종 ───────────────────────────────────────────
# 출처: PozzettiAndrea/cuda-wheels (cu130torch2.10-cp312-win_amd64)
$kernels = @(
    @{ Name = 'flex_gemm_ap'
       Url  = 'https://github.com/PozzettiAndrea/cuda-wheels/releases/download/flex_gemm_ap-latest/flex_gemm_ap-1.0.0%2Bcu130torch2.10-cp312-cp312-win_amd64.whl' },
    @{ Name = 'cumesh_vb'
       Url  = 'https://github.com/PozzettiAndrea/cuda-wheels/releases/download/cumesh_vb-latest/cumesh_vb-1.0%2Bcu130torch2.10-cp312-cp312-win_amd64.whl' },
    @{ Name = 'o_voxel_vb_ap'
       Url  = 'https://github.com/PozzettiAndrea/cuda-wheels/releases/download/o_voxel_vb_ap-latest/o_voxel_vb_ap-0.0.1%2Bcu130torch2.10-cp312-cp312-win_amd64.whl' }
)

foreach ($k in $kernels) {
    Write-Host "  ...... $($k.Name)"
    & $VenvPy -m pip install --no-deps --upgrade $k.Url
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] $($k.Name) 설치 실패." -ForegroundColor Red
        Write-Host "         torch/CUDA/Python 태그가 정확히 맞는 휠인지 확인하세요:"
        Write-Host "         https://pozzettiandrea.github.io/cuda-wheels/"
        exit 1
    }
    Write-Host "  [ OK ] $($k.Name)" -ForegroundColor Green
}

# ── 2. natten (Ampere sm_86 전용 빌드) ─────────────────────────────────
# 저장소가 릴리스가 아닌 raw 파일로 제공하고, 파일명이 '.whl.whl' 로 잘못돼 있다.
# pip 은 wheel 파일명 규격을 검사하므로 규격에 맞게 바꿔 설치한다.
$nattenSrc = 'https://raw.githubusercontent.com/NeilsMabet/Natten-0.21.6-Amphere-wheel-windows/main/natten-0.21.6-torch210-cu130-cp312-cp312-win_amd64.whl.whl'
$nattenDst = Join-Path $Cache 'natten-0.21.6-cp312-cp312-win_amd64.whl'

if (-not (Test-Path $nattenDst)) {
    Write-Host "  ...... natten 휠 내려받는 중 (약 13MB)"
    try {
        Invoke-WebRequest -Uri $nattenSrc -OutFile $nattenDst -UseBasicParsing
    } catch {
        Write-Host "  [FAIL] natten 휠 다운로드 실패: $_" -ForegroundColor Red
        Write-Host "         수동으로 받아 '$nattenDst' 로 저장한 뒤 다시 실행하세요."
        exit 1
    }
}
Write-Host "  [ OK ] $nattenDst" -ForegroundColor Green

Write-Host "  ...... natten 설치"
& $VenvPy -m pip install --no-deps --force-reinstall $nattenDst
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] natten 설치 실패." -ForegroundColor Red
    exit 1
}

# libnatten(CUDA 커널)이 실제로 들어갔는지 — 이게 False 면 NAF 가 동작하지 않는다
$hasLib = & $VenvPy -c "import natten; print(natten.HAS_LIBNATTEN)" 2>&1
if ($hasLib -match 'True') {
    Write-Host "  [ OK ] natten HAS_LIBNATTEN=True" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] natten HAS_LIBNATTEN=$hasLib" -ForegroundColor Red
    Write-Host "         CUDA 커널이 없는 순수 파이썬 natten 입니다. NAF 업샘플러가 동작하지 않습니다."
    Write-Host "         GPU 아키텍처(sm_86)/Python(3.12)/torch(2.10)/CUDA(13.0) 조합을 다시 확인하세요."
}

# ── 3. 모듈명 셰임 (리스크 R1) ─────────────────────────────────────────
# 휠은 flex_gemm_ap / cumesh_vb / o_voxel_vb_ap 로 설치되는데
# upstream 코드는 flex_gemm / cumesh / o_voxel 을 import 한다.
Write-Host "  ...... 모듈명 셰임 확인"
& $VenvPy (Join-Path $Root "tools\shims\install_shims.py")

# ── 4. 게이트 판정 ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ...... 검증"
& $VenvPy "$Root\setup\doctor.py" --gate g2
$gate = $LASTEXITCODE

Write-Host "  ----------------------------------------------------------------"
if ($gate -eq 0) {
    Write-Host "  게이트 G2 통과. 다음: powershell -File setup\04_pixal3d.ps1" -ForegroundColor Green
} else {
    Write-Host "  게이트 G2 미통과." -ForegroundColor Red
    Write-Host "  이 게이트에서 막히면 계획서의 Track B(WSL2) 전환을 검토합니다."
    Write-Host "  Phase 0 과 웹 산출물(Phase 4~6)은 트랙 전환 후에도 그대로 재사용됩니다."
}
Write-Host ""
exit $gate
