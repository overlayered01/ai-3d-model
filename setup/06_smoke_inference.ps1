<#
  Phase 3 — CLI 첫 추론 (게이트 G3)

  웹을 붙이기 전에 파이프라인 자체를 CLI 로 먼저 통과시킨다.
  그래야 웹 레이어의 버그와 파이프라인 버그를 섞지 않는다.

  가장 보수적인 설정(1024 + low_vram)부터 시작한다. 24GB 중 데스크톱이
  일부를 점유하고, expandable_segments 가 Windows 에서 동작하지 않으므로
  1536 표준 모드는 OOM 을 전제로 둔다 (리스크 R4).

  사용법:
    powershell -File setup\06_smoke_inference.ps1
    powershell -File setup\06_smoke_inference.ps1 -Resolution 1536
    powershell -File setup\06_smoke_inference.ps1 -Standard      # low_vram 끔
    powershell -File setup\06_smoke_inference.ps1 -Image inputs\samples\1_img.png
#>

param(
    [string]$Image      = "",
    [int]$Resolution    = 1024,
    [switch]$Standard,          # 지정하면 --low_vram 을 빼고 표준 모드로
    [int]$Seed          = 42
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"

$Root     = Split-Path -Parent $PSScriptRoot
$VenvPy   = Join-Path $Root ".venv\Scripts\python.exe"
$Upstream = Join-Path $Root "upstream\Pixal3D"

if (-not (Test-Path $VenvPy)) { Write-Host "  [FAIL] .venv 없음." -ForegroundColor Red; exit 1 }

if (-not $Image) { $Image = Join-Path $Upstream "assets\images\0_img.png" }
if (-not (Test-Path $Image)) { Write-Host "  [FAIL] 입력 이미지 없음: $Image" -ForegroundColor Red; exit 1 }

$stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
$mode    = if ($Standard) { "std" } else { "lowvram" }
$runDir  = Join-Path $Root "runs\$stamp`_$mode`_$Resolution"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$outGlb = Join-Path $runDir "output.glb"
$logTxt = Join-Path $runDir "log.txt"

Write-Host ""
Write-Host "  Phase 3 · CLI 첫 추론 (게이트 G3)" -ForegroundColor Cyan
Write-Host "  ----------------------------------------------------------------"
Write-Host "  입력    $Image"
Write-Host "  해상도  $Resolution"
Write-Host "  모드    $(if ($Standard) { '표준' } else { 'low_vram' })"
Write-Host "  출력    $runDir"
Write-Host ""

# GPU 를 쓰는 다른 앱이 있으면 알려준다 — 2~3GB 회수가 OOM 을 가른다.
$gpuUsed = (nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits) -as [int]
if ($gpuUsed -gt 1500) {
    Write-Host "  [WARN] 다른 프로세스가 VRAM ${gpuUsed}MB 를 쓰고 있습니다." -ForegroundColor Yellow
    Write-Host "         OOM 이 나면 브라우저/Slack 을 닫고 다시 시도하세요."
    Write-Host ""
}

$argsList = @(
    # -u: 파이프로 연결되면 파이썬이 stdout 을 블록 버퍼링해 진행 로그가
    #     끝날 때까지 안 보인다. 실시간으로 보려면 버퍼링을 꺼야 한다.
    "-u"
    # upstream inference.py 를 직접 부르지 않고 래퍼를 거친다.
    # 래퍼가 저장 위치 고정과 배경 제거 모델 셰임을 적용한 뒤 실행한다.
    (Join-Path $Root "tools/run_inference.py")
    "--image",  $Image
    "--output", $outGlb
    "--seed",   $Seed
    "--resolution", $Resolution
)
if (-not $Standard) { $argsList += "--low_vram" }

# 래퍼가 작업 디렉터리를 upstream 으로 옮기므로 여기서는 옮기지 않는다.
$sw = [Diagnostics.Stopwatch]::StartNew()

# PowerShell 5.1 함정: $ErrorActionPreference='Stop' 상태에서 네이티브 exe 의
# stderr 를 2>&1 로 받으면 각 줄이 NativeCommandError 로 감싸져 스크립트가 중단된다.
# 파이썬은 경고와 tqdm 진행률을 모두 stderr 로 내보내므로, 무해한 SyntaxWarning
# 한 줄만으로도 추론이 죽는다. 실제 성패는 종료 코드로만 판단한다.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $VenvPy @argsList 2>&1 | Tee-Object -FilePath $logTxt
$rc = $LASTEXITCODE
$ErrorActionPreference = $prevEAP

$sw.Stop()

Write-Host ""
Write-Host "  ----------------------------------------------------------------"

if ($rc -ne 0 -or -not (Test-Path $outGlb)) {
    Write-Host "  [FAIL] 추론 실패 ($([math]::Round($sw.Elapsed.TotalSeconds))초)" -ForegroundColor Red
    Write-Host "         로그: $logTxt"
    if (Select-String -Path $logTxt -Pattern "out of memory" -Quiet -ErrorAction SilentlyContinue) {
        Write-Host ""
        Write-Host "  CUDA OOM 입니다. 순서대로 시도하세요:" -ForegroundColor Yellow
        Write-Host "    1. GPU 를 쓰는 앱(브라우저/Slack) 종료 후 재시도"
        if ($Resolution -gt 1024) { Write-Host "    2. -Resolution 1024 로 낮춰 재시도" }
        if ($Standard)            { Write-Host "    2. -Standard 를 빼고 low_vram 으로 재시도" }
    }
    exit 1
}

$sizeMb = [math]::Round((Get-Item $outGlb).Length / 1MB, 1)
Write-Host "  [ OK ] GLB 생성 — ${sizeMb}MB · $([math]::Round($sw.Elapsed.TotalSeconds))초" -ForegroundColor Green
Write-Host "         $outGlb"

# 메타 기록 — 나중에 벤치마크 표를 만들 때 쓴다
$meta = [ordered]@{
    timestamp   = $stamp
    image       = $Image
    resolution  = $Resolution
    low_vram    = (-not $Standard)
    seed        = $Seed
    seconds     = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    glb_mb      = $sizeMb
    upstream_commit = (git -C $Upstream rev-parse --short HEAD)
}
$meta | ConvertTo-Json | Set-Content (Join-Path $runDir "meta.json") -Encoding utf8

# GLB 를 열어 실제로 읽히는지, 텍스처가 들어있는지 확인한다 (리스크 R12)
Write-Host ""
Write-Host "  ...... GLB 검증"
& $VenvPy (Join-Path $Root "setup\inspect_glb.py") $outGlb

Write-Host ""
Write-Host "  게이트 G3 통과 — 브라우저 뷰어에서 육안 확인이 남았습니다." -ForegroundColor Green
Write-Host ""
exit 0
