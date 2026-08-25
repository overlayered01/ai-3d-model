<#
  S7 검증 — 설치 절차가 클린 환경에서 재현되는가

  지금 쓰고 있는 .venv 를 건드리지 않는다. 별도 venv 를 새로 만들어
  01 -> 02 -> 03 -> 04 를 그대로 태우고 G2 게이트로 판정한다.
  끝나면 그 venv 를 지운다 (-Keep 을 주면 남긴다).

  이 검증이 덮는 것과 덮지 않는 것을 분명히 해 둔다.

    덮는다   Python 탐색 · venv 생성 · torch 2.10.0+cu130 설치
             CUDA 확장 5종 · natten · 셰임 · MoGe 분리 설치 · 의존성 충돌 회피
             즉, 실패가 몰려 있던 구간 전부

    안 덮는다  가중치 24.7GB 다운로드 (models\hf 를 재사용한다)
               upstream 클론 (upstream\Pixal3D 를 재사용한다)
               Python 3.12 자체의 설치

  둘 다 '이미 있으면 건너뛴다'로 짜여 있어 여기서 다시 받지 않는다.
  진짜 맨바닥 검증을 하려면 models\ 와 upstream\ 까지 지워야 하는데,
  그건 25GB 재다운로드다. 그 경우 05_weights.ps1 -Check 로 대신 확인한다.

  사용법:
    powershell -ExecutionPolicy Bypass -File setup\verify_clean_install.ps1
    powershell -ExecutionPolicy Bypass -File setup\verify_clean_install.ps1 -Keep
#>

param(
    [string]$VenvPath = "",
    [switch]$Keep       # 검증용 venv 를 지우지 않고 남긴다
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

$target = if ($VenvPath) { $VenvPath } else { Join-Path $Root ".venv-clean" }

Write-Host ""
Write-Host "  S7 · 클린 설치 검증" -ForegroundColor Cyan
Write-Host "  ----------------------------------------------------------------"
Write-Host "  검증용 venv   $target"
Write-Host "  현재 venv     $(Join-Path $Root '.venv')  (건드리지 않음)"
Write-Host "  재사용        models\  upstream\  cache\pip"
Write-Host ""

if (Test-Path $target) {
    Write-Host "  ...... 이전 검증 venv 제거"
    Remove-Item -Recurse -Force $target
}

# 이 프로세스 안에서만 유효하다. 자식 스크립트가 _env.ps1 을 통해 읽는다.
$env:PIXAL3D_VENV = $target

$steps = @(
    @{ N = '01'; File = '01_python_venv.ps1' },
    @{ N = '02'; File = '02_torch.ps1' },
    @{ N = '03'; File = '03_cuda_wheels.ps1' },
    @{ N = '04'; File = '04_pixal3d.ps1' }
)

$failed = $null
$t0 = Get-Date

foreach ($s in $steps) {
    Write-Host ""
    Write-Host "  ══ $($s.N) $($s.File) ══════════════════════════════════" -ForegroundColor Cyan

    # PowerShell 5.1 함정: 네이티브 exe 의 stderr 를 파이프로 받으면 각 줄이
    # NativeCommandError 가 된다. pip 은 진행 표시를 stderr 로 낸다.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot $s.File)
    $rc = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP

    if ($rc -ne 0) {
        $failed = $s
        Write-Host ""
        Write-Host "  [FAIL] $($s.File) 가 종료코드 $rc 로 끝났습니다." -ForegroundColor Red
        break
    }
}

$elapsed = ((Get-Date) - $t0).TotalMinutes

Write-Host ""
Write-Host "  ----------------------------------------------------------------"

$verdict = 1
if ($failed) {
    Write-Host "  S7 미통과 — $($failed.File) 에서 막혔습니다. ($([math]::Round($elapsed,1))분)" -ForegroundColor Red
    Write-Host "  검증용 venv 를 남겨 둡니다: $target"
    Write-Host "  현재 쓰는 .venv 는 그대로입니다."
    $Keep = $true
} else {
    # 최종 판정은 doctor 가 한다. 스크립트가 0 으로 끝났다는 것과
    # 환경이 실제로 쓸 수 있다는 것은 다른 얘기다.
    Write-Host "  ...... G2 게이트 판정"
    $cleanPy = Join-Path $target "Scripts\python.exe"
    & $cleanPy (Join-Path $PSScriptRoot "doctor.py") --gate g2
    $verdict = $LASTEXITCODE

    if ($verdict -eq 0) {
        Write-Host ""
        Write-Host "  S7 통과 — 설치 절차가 클린 환경에서 재현됩니다. ($([math]::Round($elapsed,1))분)" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "  S7 미통과 — 스크립트는 끝났지만 G2 게이트가 떨어집니다." -ForegroundColor Red
        $Keep = $true
    }
}

if (-not $Keep -and (Test-Path $target)) {
    Write-Host "  ...... 검증용 venv 제거 (남기려면 -Keep)"
    Remove-Item -Recurse -Force $target
}

Write-Host ""
exit $verdict
