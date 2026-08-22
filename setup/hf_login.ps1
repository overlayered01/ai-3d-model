<#
  HuggingFace 로그인 — 토큰을 프로젝트 안에 저장한다.

  그냥 `hf auth login` 을 실행하면 토큰이 C:\Users\<계정>\.cache\huggingface
  로 간다. 이 래퍼는 _env.ps1 을 먼저 불러 HF_HOME 을 프로젝트 안으로
  고정하므로, 토큰이 models\hf\token 에 저장된다.

  왜 필요한가:
    배경 제거 모델 briaai/RMBG-2.0 이 게이트된 저장소다. 파이프라인이
    생성 시점에 이 모델을 무조건 로드하므로, 인증 없이는 추론이 시작조차
    되지 않는다. (알파 PNG 를 써도 우회되지 않는다.)

  사전 준비:
    1. https://huggingface.co/briaai/RMBG-2.0 에서 라이선스 동의
    2. https://huggingface.co/settings/tokens 에서 Read 권한 토큰 발급

  사용법:  powershell -ExecutionPolicy Bypass -File setup\hf_login.ps1
#>

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"

$Root   = Split-Path -Parent $PSScriptRoot
$VenvHf = Join-Path $Root ".venv\Scripts\hf.exe"

if (-not (Test-Path $VenvHf)) {
    Write-Host "  [FAIL] .venv\Scripts\hf.exe 없음. setup\04_pixal3d.ps1 까지 실행하세요." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  HuggingFace 로그인" -ForegroundColor Cyan
Write-Host "  ----------------------------------------------------------------"
Write-Host "  토큰 저장 위치: $env:HF_HOME"
Write-Host ""
Write-Host "  아직 준비가 안 됐다면 먼저:" -ForegroundColor Yellow
Write-Host "    1. https://huggingface.co/briaai/RMBG-2.0  라이선스 동의"
Write-Host "    2. https://huggingface.co/settings/tokens  Read 토큰 발급"
Write-Host ""

& $VenvHf auth login

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] 로그인 실패." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  ...... 확인"
& $VenvHf auth whoami
& $VenvHf download briaai/RMBG-2.0 config.json 2>&1 | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-Host "  [ OK ] RMBG-2.0 접근 확인" -ForegroundColor Green
    Write-Host ""
    Write-Host "  다음: powershell -File setup\05_weights.ps1   (RMBG 5GB 내려받기)" -ForegroundColor Cyan
    Write-Host "        powershell -File setup\06_smoke_inference.ps1" -ForegroundColor Cyan
} else {
    Write-Host "  [FAIL] 로그인은 됐지만 RMBG-2.0 에 접근할 수 없습니다." -ForegroundColor Red
    Write-Host "         https://huggingface.co/briaai/RMBG-2.0 에서 라이선스에 동의했는지 확인하세요."
    exit 1
}
Write-Host ""
