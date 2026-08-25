"""웹 서버 종단 검증 — 브라우저가 하는 일을 그대로 API 로 재현한다.

브라우저에서 사람이 클릭해야 확인되는 흐름(S3)을 자동으로 한 번 돌린다.
UI 를 손으로 눌러보기 전에 서버 쪽이 맞는지 먼저 갈라내기 위한 것이다.

    업로드 → /api/preprocess → /api/generate → 진행률 폴링 → 결과 확인

여러 장을 돌려 성능·재현성을 재려면 tools/run_batch.py 를 쓴다.

사용법:
    python tools/e2e_test.py
    python tools/e2e_test.py --image inputs/samples/1_img.png --resolution 1024
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _api  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description="Pixal3D 웹 서버 종단 검증")
    ap.add_argument("--base", default=_api.DEFAULT_BASE)
    ap.add_argument("--image", default="")
    ap.add_argument("--resolution", type=int, default=1024)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--wait-load", type=int, default=900, help="모델 로딩 대기 한도(초)")
    args = ap.parse_args()

    # 기본 샘플은 저장소에 함께 들어있는 가벼운 것으로 둔다.
    image = Path(args.image) if args.image else ROOT / "inputs" / "samples" / "1_img.png"
    if not image.exists():
        print(f"  [FAIL] 입력 이미지 없음: {image}")
        return 1

    print()
    print(f"  종단 검증  {args.base}")
    print(f"  입력       {image.name}")
    print("  " + "─" * 66)

    # ── 1. 모델 로딩 대기 ───────────────────────────────────────────
    try:
        status = _api.wait_ready(
            args.base, limit=args.wait_load,
            on_wait=lambda s: print(f"  ...... 모델 로딩 대기 {s:.0f}초", end="\r"),
        )
    except _api.ApiError as e:
        print(f"  [FAIL] {e}".ljust(78))
        return 1
    print(f"  [ OK ] 모델 준비 완료 ({status['load_seconds']}초)".ljust(78))

    # ── 2. 전처리 ───────────────────────────────────────────────────
    print("  ...... 전처리 (배경 제거)")
    t = time.time()
    pre = _api.preprocess(args.base, image)
    run_id = pre["run_id"]
    print(f"  [ OK ] 전처리 {time.time() - t:.1f}초 · run_id={run_id}")

    # ── 3. 생성 ─────────────────────────────────────────────────────
    print("  ...... 생성 요청")
    gen = _api.generate(args.base, run_id, seed=args.seed,
                        resolution=args.resolution, steps=12)

    # ── 4. 진행률 폴링 ──────────────────────────────────────────────
    t = time.time()
    show = _api.StagePrinter(indent="  ...... ")
    info = _api.poll(args.base, gen["job_id"], on_progress=show)
    show.clear()

    elapsed = time.time() - t
    if info["status"] != "done":
        print(f"  [FAIL] 생성 {info['status']}: {info.get('error', '')}")
        return 1
    print(f"  [ OK ] 생성 완료 {elapsed:.0f}초")

    # ── 5. 결과 검증 ────────────────────────────────────────────────
    meta = _api.find_run(args.base, run_id)
    if meta is None:
        print("  [FAIL] 이력에서 결과를 찾지 못했습니다.")
        return 1

    print()
    print(f"    소요        {meta['seconds']}초")
    print(f"    최대 VRAM   {meta['peak_vram_gb']}GB")
    print(f"    원본 GLB    {meta['full_glb_mb']}MB")
    print(f"    경량 GLB    {meta['web_glb_mb']}MB")

    # 실제로 내려받아지는지 — 뷰어가 쓸 경로다
    problems = []
    for name in ("processed.png", "full.glb", "web.glb"):
        url = f"{args.base}/runs/{run_id}/{name}"
        try:
            with urllib.request.urlopen(url, timeout=120) as res:
                size = len(res.read())
            if size == 0:
                problems.append(f"{name} 크기 0")
            else:
                print(f"    {name:<14} {size / 2**20:.1f}MB  내려받기 OK")
        except Exception as e:
            problems.append(f"{name}: {e}")

    print()
    print("  " + "─" * 66)
    if problems:
        for p in problems:
            print(f"  [FAIL] {p}")
        return 1

    if meta["web_glb_mb"] >= meta["full_glb_mb"]:
        print("  [WARN] 경량본이 원본보다 작지 않습니다. 추출 설정을 확인하세요.")

    print("  종단 검증 통과 — 브라우저에서 열어 육안 확인만 남았습니다.")
    print(f"  {args.base}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
