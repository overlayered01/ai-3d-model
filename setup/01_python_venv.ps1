<#
  Phase 1 — Python 3.12 venv 구성 (게이트 G1의 앞부분)

  전역 Python 3.14 는 건드리지 않는다. torch 와 커뮤니티 CUDA 휠이
  3.14 를 지원하지 않으므로 3.12.10 을 별도로 두고 venv 로 격리한다.

  이 스크립트는 Python 을 자동 설치하지 않는다. 설치는 시스템 변경이므로
  사용자가 직접 하도록 안내만 하고, 이미 있으면 그것을 쓴다.

  사용법:  powershell -ExecutionPolicy Bypass -File setup\01_python_venv.ps1
#>

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "  Phase 1 · Python 3.12 venv 구성" -ForegroundColor Cyan
Write-Host "  ----------------------------------------------------------------"

# ── 1. Python 3.12 탐색 ────────────────────────────────────────────────
# py launcher 를 먼저 보고, 없으면 알려진 설치 경로를 훑는다.
$py312 = $null

try {
    $probe = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $probe) { $py312 = $probe.Trim() }
} catch { }

if (-not $py312) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "C:\Python312\python.exe",
        "D:\Python312\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $py312 = $c; break }
    }
}

if (-not $py312) {
    Write-Host ""
    Write-Host "  [FAIL] Python 3.12 을 찾지 못했습니다." -ForegroundColor Red
    Write-Host ""
    Write-Host "  계획서가 3.12.10 을 고정한 이유:" -ForegroundColor Yellow
    Write-Host "    natten Ampere sm86 휠이 cp312 로 빌드되어 있고,"
    Write-Host "    flex_gemm / cumesh / o_voxel 휠도 전부 cp312-win_amd64 입니다."
    Write-Host "    Python 버전이 다르면 이 휠들이 설치되지 않습니다."
    Write-Host ""
    Write-Host "  설치 방법 (둘 중 하나):" -ForegroundColor Yellow
    Write-Host "    A. https://www.python.org/downloads/release/python-31210/"
    Write-Host "       'Windows installer (64-bit)' 를 받아 설치."
    Write-Host "       설치 시 'Add to PATH' 는 체크하지 마세요 (전역 3.14 유지)."
    Write-Host ""
    Write-Host "    B. winget install Python.Python.3.12"
    Write-Host ""
    Write-Host "  설치 후 이 스크립트를 다시 실행하세요."
    Write-Host ""
    exit 1
}

$verOut = & $py312 -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
Write-Host "  [ OK ] Python $verOut" -ForegroundColor Green
Write-Host "         $py312"

if (-not $verOut.StartsWith("3.12")) {
    Write-Host "  [FAIL] 3.12.x 가 아닙니다." -ForegroundColor Red
    exit 1
}

# ── 2. 저장 위치를 프로젝트 안으로 (리스크 R5) ─────────────────────────
# 가중치·캐시·임시파일을 전부 프로젝트 폴더 안에 둔다. C: 여유가 55GB 뿐이라
# 기본 위치(AppData, 사용자 홈)에 쌓이면 가중치를 받다가 터지고,
# 흩어져 있으면 프로젝트를 옮기거나 지울 때 따라오지 않는다.
#
# 전역(User 수준) 환경변수는 쓰지 않는다. setup\_env.ps1 이 스크립트 위치에서
# 매번 계산하므로 프로젝트를 통째로 옮겨도 그대로 동작한다.
. "$PSScriptRoot\_env.ps1"

Write-Host "  [ OK ] HF_HOME          = $env:HF_HOME" -ForegroundColor Green
Write-Host "  [ OK ] TORCH_HOME       = $env:TORCH_HOME" -ForegroundColor Green
Write-Host "  [ OK ] PIP_CACHE_DIR    = $env:PIP_CACHE_DIR" -ForegroundColor Green
Write-Host "  [ OK ] TRITON_CACHE_DIR = $env:TRITON_CACHE_DIR" -ForegroundColor Green
Write-Host "  [ OK ] TMP              = $env:TMP" -ForegroundColor Green

# 예전 버전이 남긴 전역 환경변수가 있으면 걷어낸다 — 프로젝트 밖을 가리킨다.
foreach ($k in @('HF_HOME','PIP_CACHE_DIR','TORCH_HOME')) {
    if ([Environment]::GetEnvironmentVariable($k, 'User')) {
        [Environment]::SetEnvironmentVariable($k, $null, 'User')
        Write-Host "  [정리] User 환경변수 $k 제거 (프로젝트 밖을 가리킴)" -ForegroundColor Yellow
    }
}

# ── 3. venv 생성 ───────────────────────────────────────────────────────
# $VenvDir / $VenvPy 는 _env.ps1 이 정한다 (PIXAL3D_VENV 로 바꿀 수 있다).
if (Test-Path $VenvDir) {
    Write-Host "  [WARN] venv 가 이미 있습니다 — 재사용합니다." -ForegroundColor Yellow
    Write-Host "         새로 만들려면 먼저 삭제: Remove-Item -Recurse -Force '$VenvDir'"
} else {
    Write-Host "  ...... venv 생성 중"
    & $py312 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] venv 생성 실패" -ForegroundColor Red; exit 1 }
}
Write-Host "  [ OK ] $VenvDir" -ForegroundColor Green

# ── 4. pip 최신화 ──────────────────────────────────────────────────────
Write-Host "  ...... pip 업그레이드"
& $VenvPy -m pip install --quiet --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] pip 업그레이드 실패" -ForegroundColor Red; exit 1 }
Write-Host "  [ OK ] pip $(& $VenvPy -m pip --version)" -ForegroundColor Green

# ── 5. .env 준비 ───────────────────────────────────────────────────────
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvFile
    Write-Host "  [ OK ] .env 생성 (.env.example 복사)" -ForegroundColor Green
}

Write-Host ""
Write-Host "  ----------------------------------------------------------------"
Write-Host "  다음 단계" -ForegroundColor Cyan
Write-Host "    1. venv 활성화:  .\.venv\Scripts\Activate.ps1"
Write-Host "    2. torch 설치:   powershell -File setup\02_torch.ps1"
Write-Host ""
