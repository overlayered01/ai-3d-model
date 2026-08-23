"""웹 서버 종단 검증 — 브라우저가 하는 일을 그대로 API 로 재현한다.

브라우저에서 사람이 클릭해야 확인되는 흐름(S3)을 자동으로 한 번 돌린다.
UI 를 손으로 눌러보기 전에 서버 쪽이 맞는지 먼저 갈라내기 위한 것이다.

    업로드 → /api/preprocess → /api/generate → 진행률 폴링 → 결과 확인

사용법:
    python tools/e2e_test.py
    python tools/e2e_test.py --image inputs/samples/1_img.png --resolution 1024
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parent.parent


def request(url: str, data: bytes | None = None, headers: dict | None = None,
            method: str | None = None, timeout: int = 300) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:400]}") from None


def multipart(fields: dict[str, str], file: tuple[str, Path] | None = None) -> tuple[bytes, str]:
    """의존성 없이 multipart/form-data 를 만든다."""
    boundary = f"----pixal3d{uuid.uuid4().hex}"
    parts: list[bytes] = []

    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            .encode("utf-8")
        )

    if file is not None:
        name, path = file
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
            .encode("utf-8")
        )
        parts.append(path.read_bytes())
        parts.append(b"\r\n")

    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Pixal3D 웹 서버 종단 검증")
    ap.add_argument("--base", default="http://127.0.0.1:7860")
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
    t0 = time.time()
    while True:
        try:
            status = request(f"{args.base}/api/status", timeout=15)
        except Exception as e:
            print(f"  [FAIL] 서버에 연결할 수 없습니다: {e}")
            return 1

        if status.get("pipeline_error"):
            print(f"  [FAIL] 모델 로딩 실패: {status['pipeline_error']}")
            return 1
        if status.get("pipeline_loaded"):
            print(f"  [ OK ] 모델 준비 완료 ({status['load_seconds']}초)")
            break

        waited = time.time() - t0
        if waited > args.wait_load:
            print(f"  [FAIL] 모델 로딩이 {args.wait_load}초를 넘겼습니다.")
            return 1
        print(f"  ...... 모델 로딩 대기 {waited:.0f}초", end="\r")
        time.sleep(5)

    # ── 2. 전처리 ───────────────────────────────────────────────────
    print("  ...... 전처리 (배경 제거)")
    body, ctype = multipart({}, file=("file", image))
    t = time.time()
    pre = request(f"{args.base}/api/preprocess", data=body,
                  headers={"Content-Type": ctype}, timeout=600)
    run_id = pre["run_id"]
    print(f"  [ OK ] 전처리 {time.time() - t:.1f}초 · run_id={run_id}")

    # ── 3. 생성 ─────────────────────────────────────────────────────
    print("  ...... 생성 요청")
    body, ctype = multipart({
        "run_id": run_id,
        "seed": str(args.seed),
        "resolution": str(args.resolution),
        "steps": "12",
        "fov": "-1",
    })
    gen = request(f"{args.base}/api/generate", data=body,
                  headers={"Content-Type": ctype}, timeout=60)
    job_id = gen["job_id"]

    # ── 4. 진행률 폴링 ──────────────────────────────────────────────
    t = time.time()
    last = ""
    while True:
        info = request(f"{args.base}/api/progress?job_id={job_id}", timeout=30)
        st = info["status"]

        if st in ("done", "failed", "cancelled"):
            print(" " * 78, end="\r")
            break

        p = info.get("progress") or {}
        stage = p.get("stage", st)
        line = f"  ...... {stage}"
        if p.get("total"):
            line += f"  {p['step']}/{p['total']}"
        line += f"  ({time.time() - t:.0f}초)"
        if line != last:
            print(line.ljust(78), end="\r")
            last = line
        time.sleep(1)

    elapsed = time.time() - t
    if st != "done":
        print(f"  [FAIL] 생성 {st}: {info.get('error', '')}")
        return 1
    print(f"  [ OK ] 생성 완료 {elapsed:.0f}초")

    # ── 5. 결과 검증 ────────────────────────────────────────────────
    runs = request(f"{args.base}/api/runs?limit=20", timeout=30)
    meta = next((r for r in runs if r["run_id"] == run_id), None)
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
