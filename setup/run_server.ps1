<#
  웹 서버 실행 (Phase 4~5)

  --workers 1 은 선택이 아니라 필수다. 워커가 여럿이면 각각 파이프라인을
  로드해 22GB 를 중복으로 올리고 곧바로 OOM 이 난다.

  기본 바인딩은 127.0.0.1 — 이 PC 에서만 접근된다. 이 서버에는 인증이 없으므로
  접근 가능한 사람은 누구나 이 GPU 로 작업을 돌릴 수 있다. (리스크 R8)

  사용법:
    powershell -File setup\run_server.ps1
    powershell -File setup\run_server.ps1 -Port 8000
    powershell -File setup\run_server.ps1 -Lan        # LAN 공유 (확인 후 진행)
#>

param(
    [int]$Port = 0,
    [switch]$Lan,
    [switch]$Reload      # 개발용. 파이프라인이 매번 다시 로드되므로 평소엔 쓰지 않는다.
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"

$Root   = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPy)) {
    Write-Host "  [FAIL] .venv 없음. setup\01_python_venv.ps1 부터 실행하세요." -ForegroundColor Red
    exit 1
}

# .env 를 우선하되, 인자로 준 값이 이긴다.
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Z_]+)\s*=\s*(.+?)\s*$') {
            $k, $v = $Matches[1], $Matches[2]
            if (-not (Get-Item "env:$k" -ErrorAction SilentlyContinue).Value) {
                Set-Item -Path "env:$k" -Value $v
            }
        }
    }
}

$bindHost = if ($Lan) { '0.0.0.0' } elseif ($env:HOST) { $env:HOST } else { '127.0.0.1' }
$bindPort = if ($Port -gt 0) { $Port } elseif ($env:PORT) { [int]$env:PORT } else { 7860 }

if ($Lan) {
    Write-Host ""
    Write-Host "  [경고] LAN 공유 모드입니다." -ForegroundColor Yellow
    Write-Host "         이 서버에는 인증이 없습니다. 같은 네트워크에서 주소를 아는"
    Write-Host "         누구나 이 PC 의 GPU 로 작업을 돌릴 수 있습니다."
    Write-Host "         Windows 방화벽 인바운드 규칙도 따로 열어야 합니다."
    Write-Host ""
    $answer = Read-Host "  계속하려면 'yes' 를 입력하세요"
    if ($answer -ne 'yes') { Write-Host "  취소했습니다."; exit 0 }
}

Write-Host ""
Write-Host "  Pixal3D 웹 서버" -ForegroundColor Cyan
Write-Host "  ----------------------------------------------------------------"
Write-Host "  주소     http://$(if ($bindHost -eq '0.0.0.0') { '<이 PC의 IP>' } else { $bindHost }):$bindPort"
Write-Host "  가중치   $env:HF_HOME"
Write-Host "  배경제거 $(if ($env:PIXAL3D_REMBG_MODEL) { $env:PIXAL3D_REMBG_MODEL } else { 'briaai/RMBG-2.0 (upstream 기본값)' })"
Write-Host ""
Write-Host "  모델 로딩에 수 분이 걸립니다. 그동안 페이지는 열리지만" -ForegroundColor Yellow
Write-Host "  생성 요청은 '준비 중'으로 거절됩니다."
Write-Host ""
Write-Host "  종료: Ctrl+C"
Write-Host "  ----------------------------------------------------------------"

$uvicornArgs = @(
    "-u", "-m", "uvicorn", "server.main:app",
    "--host", $bindHost,
    "--port", $bindPort,
    "--workers", "1"
)
if ($Reload) { $uvicornArgs += "--reload" }

Push-Location $Root

# PowerShell 5.1 함정: stderr 를 파이프로 받으면 각 줄이 NativeCommandError 가 된다.
# 파이썬은 로그와 tqdm 을 stderr 로 내보내므로 여기서만 Continue 로 낮춘다.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $VenvPy @uvicornArgs
$rc = $LASTEXITCODE
$ErrorActionPreference = $prevEAP

Pop-Location
exit $rc
