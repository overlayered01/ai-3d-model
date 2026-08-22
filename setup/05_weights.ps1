<#
  Phase 3 — 모델 가중치 사전 다운로드 (약 24.7GB)

  추론과 분리해 별도 단계로 둔다. 그래야 "다운로드 실패"와 "추론 실패"를
  혼동하지 않는다 (계획서 리스크 R6).

  실제 다운로드는 setup\download_weights.py 가 한다. CLI 대신 Python API 를
  쓰는 이유는 재시도·부분 실패 처리·용량 보고를 직접 제어하기 위해서다.

  사용법:  powershell -ExecutionPolicy Bypass -File setup\05_weights.ps1
           powershell -ExecutionPolicy Bypass -File setup\05_weights.ps1 -Check
#>

param(
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"

$Root   = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPy)) { Write-Host "  [FAIL] .venv 없음. 01 부터 실행하세요." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  Phase 3 · 가중치 다운로드" -ForegroundColor Cyan
Write-Host "  ----------------------------------------------------------------"

# 용량 확인 — 필수 3종 합계가 약 24.7GB 다.
# 프로젝트가 놓인 드라이브의 여유를 본다 — D: 로 못 박지 않는다.
$drive = (Get-Item $Root).PSDrive
$free  = [math]::Round($drive.Free / 1GB)
Write-Host "  $($drive.Name): 여유 ${free}GB (필요 약 25GB)"
if ($free -lt 40) {
    Write-Host "  [FAIL] 여유가 부족합니다." -ForegroundColor Red
    exit 1
}

# 진행 막대는 로그를 뒤덮으므로 끈다. 대신 파일 단위 요약이 나온다.
$env:HF_HUB_DISABLE_PROGRESS_BARS = '1'

if ($Check) {
    & $VenvPy (Join-Path $Root "setup\download_weights.py") --check
    exit $LASTEXITCODE
}

& $VenvPy (Join-Path $Root "setup\download_weights.py")
$rc = $LASTEXITCODE

Write-Host "  ----------------------------------------------------------------"
if ($rc -ne 0) {
    Write-Host "  다운로드 미완료 — 같은 명령을 다시 실행하면 이어받습니다." -ForegroundColor Red
    exit $rc
}

Write-Host ""
Write-Host "  다음: CLI 첫 추론 (게이트 G3)" -ForegroundColor Cyan
Write-Host "    powershell -File setup\06_smoke_inference.ps1"
Write-Host ""
exit 0
