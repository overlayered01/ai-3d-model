<#
  Phase 1 — PyTorch 2.10.0 + CUDA 13.0 설치 (게이트 G1)

  버전을 정확히 고정하는 이유:
    커뮤니티 CUDA 휠(natten / flex_gemm / cumesh / o_voxel)이
    'cu130torch2.10-cp312-win_amd64' 태그로 빌드되어 있다.
    torch 버전이 하나라도 어긋나면 그 휠들이 import 단계에서 깨진다.

  사용법:  powershell -ExecutionPolicy Bypass -File setup\02_torch.ps1
#>

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"
$Root   = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path $VenvPy)) {
    Write-Host "  [FAIL] .venv 가 없습니다. 먼저 setup\01_python_venv.ps1 을 실행하세요." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  Phase 1 · PyTorch 2.10.0+cu130" -ForegroundColor Cyan
Write-Host "  ----------------------------------------------------------------"

# 드라이버 확인 — CUDA UMD 13.3 이면 cu130 런타임을 받는다.
try {
    $smi = & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host "  [ OK ] $smi" -ForegroundColor Green }
} catch {
    Write-Host "  [WARN] nvidia-smi 를 찾지 못했습니다. 드라이버를 확인하세요." -ForegroundColor Yellow
}

Write-Host "  ...... torch 설치 중 (수 GB, 시간이 걸립니다)"

& $VenvPy -m pip install `
    torch==2.10.0 torchvision `
    --index-url https://download.pytorch.org/whl/cu130

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  [FAIL] torch 설치 실패." -ForegroundColor Red
    Write-Host "         cu130 인덱스에 2.10.0 이 없으면 사용 가능한 버전을 확인하세요:"
    Write-Host "         $VenvPy -m pip index versions torch --index-url https://download.pytorch.org/whl/cu130"
    Write-Host "         버전을 바꾸면 이후 CUDA 휠도 그 torch 태그에 맞춰 다시 골라야 합니다."
    exit 1
}

# ── Triton ─────────────────────────────────────────────────────────────
# torch 2.10.0 은 triton==3.6.0 을 요구하지만, 그 핀에 조건이 걸려 있다:
#     triton==3.6.0 ; platform_system == "Linux"
# 그래서 Windows 에서는 pip 이 조용히 건너뛰고, 없는 채로 넘어간다.
#
# 그런데 flex_gemm 휠은 Triton 커널을 쓴다 —
#     flex_gemm_ap/kernels/triton/grid_sample/config.py: import triton
# 없으면 03 의 셰임 검증이 ModuleNotFoundError 로 떨어진다. 여기서 미리 넣는다.
#
# 공식 triton 에는 Windows 휠이 없다. triton-windows 가 같은 모듈명을 제공한다.
Write-Host ""
Write-Host "  ...... triton-windows 설치 (flex_gemm 커널이 요구)"

& $VenvPy -m pip install "triton-windows==3.6.0.post26"

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] triton-windows 설치 실패." -ForegroundColor Red
    Write-Host "         torch 가 요구하는 triton 버전을 확인하세요:"
    Write-Host "         $VenvPy -m pip show torch  (Requires-Dist 의 triton 핀)"
    Write-Host "         triton-windows 는 앞 세 자리를 맞추면 된다 (3.6.0 -> 3.6.0.postN)."
    exit 1
}

$tritonVer = & $VenvPy -c "import triton; print(triton.__version__)" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [ OK ] triton $tritonVer" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] triton import 실패: $tritonVer" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  ...... 검증"
& $VenvPy "$Root\setup\doctor.py" --gate g1
$gate = $LASTEXITCODE

Write-Host "  ----------------------------------------------------------------"
if ($gate -eq 0) {
    Write-Host "  게이트 G1 통과. 다음: powershell -File setup\03_cuda_wheels.ps1" -ForegroundColor Green
} else {
    Write-Host "  게이트 G1 미통과 — 위 조치 사항을 먼저 해결하세요." -ForegroundColor Red
    Write-Host "  여기서 막히면 계획서의 Track B(WSL2) 전환을 검토합니다."
}
Write-Host ""
exit $gate
