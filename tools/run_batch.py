"""배치 실행 — 여러 이미지를 서버 API 로 연달아 돌리고 표로 정리한다.

브라우저를 열지 않고 성능·재현성을 재기 위한 도구다. 서버를 거치는 이유는
두 가지다. 모델이 프로세스에 상주하므로 매 건 22GB 를 다시 읽지 않고,
웹 UI 와 완전히 같은 경로를 지나므로 여기서 잰 값이 곧 UI 의 값이다.

    python tools/run_batch.py                         # inputs/samples 전부
    python tools/run_batch.py inputs/samples/1_img.png
    python tools/run_batch.py --resolution 1536 --limit 1
    python tools/run_batch.py --repeat 3              # 재현성 확인 (S6)

GPU 가 하나라 서버가 동시 실행을 1건으로 묶는다. 여기서도 순차로 보낸다.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _api  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "inputs" / "samples"
REPORTS = ROOT / "reports"

IMAGE_SUFFIX = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass
class Result:
    image: str
    attempt: int
    run_id: str = ""
    ok: bool = False
    error: str = ""
    seconds: float = 0.0
    peak_vram_gb: float = 0.0
    full_glb_mb: float = 0.0
    web_glb_mb: float = 0.0
    vertices: int = 0
    triangles: int = 0
    digests: dict[str, str] = field(default_factory=dict)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_images(args: argparse.Namespace) -> list[Path]:
    """인자로 받은 경로들을 이미지 목록으로 편다. 없으면 샘플 폴더 전체."""
    given = [Path(p) for p in args.images]
    if args.dir:
        given.append(Path(args.dir))
    if not given:
        given = [SAMPLES]

    out: list[Path] = []
    for p in given:
        if p.is_dir():
            out += sorted(q for q in p.iterdir()
                          if q.suffix.lower() in IMAGE_SUFFIX)
        elif p.is_file():
            out.append(p)
        else:
            print(f"  [WARN] 없는 경로: {p}")

    # 중복 제거 (순서 유지)
    seen, uniq = set(), []
    for p in out:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)

    if args.limit > 0:
        uniq = uniq[: args.limit]
    return uniq


def run_one(base: str, image: Path, attempt: int, args: argparse.Namespace) -> Result:
    r = Result(image=image.name, attempt=attempt)
    t0 = time.time()

    # 서버가 바쁘면 503 을 준다 (큐가 슬롯 하나). 실패로 접지 않고 기다린다.
    for attempt_n in range(5):
        try:
            pre = _api.preprocess(base, image)
            r.run_id = pre["run_id"]
            break
        except _api.ApiError as e:
            if "HTTP 503" in str(e) and attempt_n < 4:
                print(f"      서버가 바쁩니다. 60초 뒤 재시도 ({attempt_n + 1}/4)")
                time.sleep(60)
                continue
            r.error = f"전처리: {e}"
            return r
        except Exception as e:
            r.error = f"전처리: {e}"
            return r

    try:
        gen = _api.generate(base, r.run_id, seed=args.seed,
                            resolution=args.resolution, steps=args.steps,
                            fov=args.fov)
    except Exception as e:
        r.error = f"생성 요청: {e}"
        return r

    show = _api.StagePrinter(indent="      ")
    try:
        info = _api.poll(base, gen["job_id"], on_progress=show)
    except Exception as e:
        r.error = f"진행률: {e}"
        return r
    show.clear()

    if info["status"] != "done":
        r.error = info.get("error") or info["status"]
        return r

    meta = _api.find_run(base, r.run_id)
    if meta is None:
        r.error = "이력에서 결과를 찾지 못했습니다."
        return r

    r.ok = True
    r.seconds = meta.get("seconds", 0.0)
    r.peak_vram_gb = meta.get("peak_vram_gb", 0.0)
    r.full_glb_mb = meta.get("full_glb_mb", 0.0)
    r.web_glb_mb = meta.get("web_glb_mb", 0.0)
    r.vertices = meta.get("vertices", 0)
    r.triangles = meta.get("triangles", 0)

    # 재현성 비교용 해시. 로컬 서버라면 파일을 그대로 읽는다 —
    # 40MB 를 HTTP 로 다시 받을 이유가 없다.
    run_dir = ROOT / "runs" / r.run_id
    for name in ("full.glb", "web.glb"):
        path = run_dir / name
        if path.is_file():
            r.digests[name] = sha256(path)

    return r


# ── 보고 ────────────────────────────────────────────────────────────

def table(results: list[Result]) -> list[str]:
    head = "| 입력 | 회차 | 소요 | 최대 VRAM | 원본 GLB | 경량 GLB | 정점 | 삼각형 |"
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|"
    rows = [head, sep]
    for r in results:
        if r.ok:
            rows.append(
                f"| `{r.image}` | {r.attempt} | {r.seconds:.1f}s | "
                f"{r.peak_vram_gb:.2f}GB | {r.full_glb_mb:.1f}MB | "
                f"{r.web_glb_mb:.1f}MB | {r.vertices:,} | {r.triangles:,} |"
            )
        else:
            rows.append(f"| `{r.image}` | {r.attempt} | 실패 | | | | | |")
    return rows


def reproducibility(results: list[Result]) -> list[str]:
    """같은 이미지를 같은 seed 로 여러 번 돌린 결과를 비교한다 (S6).

    바이트 단위로 같으면 확정, 다르면 지오메트리 수치라도 같은지 본다.
    GPU 커널의 비결정성(atomic 누적 순서 등)으로 파일이 달라질 수 있는데,
    그렇더라도 정점·삼각형 수까지 흔들리면 성격이 다른 문제다.
    """
    groups: dict[str, list[Result]] = {}
    for r in results:
        if r.ok:
            groups.setdefault(r.image, []).append(r)

    lines: list[str] = []
    for image, rs in groups.items():
        if len(rs) < 2:
            continue
        digests = {r.digests.get("full.glb", "") for r in rs}
        counts = {(r.vertices, r.triangles) for r in rs}
        spread = max(r.seconds for r in rs) - min(r.seconds for r in rs)

        if len(digests) == 1 and "" not in digests:
            verdict = "**바이트 단위 동일** — 완전 재현"
        elif len(counts) == 1:
            verdict = "지오메트리 동일, 파일 바이트는 다름 (커널 비결정성)"
        else:
            verdict = "**결과가 다름** — 재현되지 않음"

        lines.append(f"- `{image}` × {len(rs)}회 · seed 고정 → {verdict}")
        lines.append(f"  - 소요 편차 {spread:.1f}초")
        for r in rs:
            d = r.digests.get("full.glb", "")
            lines.append(f"  - {r.attempt}회차 `{r.run_id}` full.glb `{d[:16] or '?'}` "
                         f"정점 {r.vertices:,} / 삼각형 {r.triangles:,}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Pixal3D 배치 실행")
    ap.add_argument("images", nargs="*", help="이미지 파일 또는 폴더 (생략 시 inputs/samples)")
    ap.add_argument("--dir", default="", help="이미지 폴더를 추가로 지정")
    ap.add_argument("--base", default=_api.DEFAULT_BASE)
    ap.add_argument("--resolution", type=int, default=0, help="0 이면 서버 기본값")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--fov", type=float, default=-1.0)
    ap.add_argument("--repeat", type=int, default=1, help="같은 이미지를 몇 번 돌릴지 (S6)")
    ap.add_argument("--limit", type=int, default=0, help="이미지 개수 상한")
    ap.add_argument("--report", default="", help="보고서 경로 (생략 시 reports/ 아래 자동)")
    ap.add_argument("--wait-load", type=int, default=900)
    args = ap.parse_args()

    images = collect_images(args)
    if not images:
        print("  [FAIL] 돌릴 이미지가 없습니다.")
        return 1

    total = len(images) * args.repeat
    print()
    print(f"  배치 실행  {args.base}")
    print(f"  대상       {len(images)}장 × {args.repeat}회 = {total}건")
    print(f"  설정       해상도 {args.resolution or '서버 기본값'} · seed {args.seed} · {args.steps} steps")
    print("  " + "─" * 66)

    try:
        status = _api.wait_ready(
            args.base, limit=args.wait_load,
            on_wait=lambda s: print(f"  ...... 모델 로딩 대기 {s:.0f}초", end="\r"),
        )
    except _api.ApiError as e:
        print(f"  [FAIL] {e}")
        return 1
    print(f"  [ OK ] 모델 준비 완료 ({status['load_seconds']}초)".ljust(78))

    results: list[Result] = []
    t_all = time.time()

    for attempt in range(1, args.repeat + 1):
        for i, image in enumerate(images, 1):
            n = len(results) + 1
            label = f"[{n}/{total}] {image.name}"
            if args.repeat > 1:
                label += f"  ({attempt}회차)"
            print(f"  {label}")

            r = run_one(args.base, image, attempt, args)
            results.append(r)

            if r.ok:
                print(f"      [ OK ] {r.seconds:.1f}초 · VRAM {r.peak_vram_gb:.2f}GB · "
                      f"{r.full_glb_mb:.1f}MB / {r.web_glb_mb:.1f}MB")
            else:
                print(f"      [FAIL] {r.error[:200]}")

    ok = sum(1 for r in results if r.ok)
    elapsed = time.time() - t_all

    print()
    print("  " + "─" * 66)
    for line in table(results):
        print("  " + line)

    repro = reproducibility(results)
    if repro:
        print()
        print("  재현성 (S6)")
        for line in repro:
            print("  " + line)

    print()
    print(f"  {ok}/{len(results)}건 성공 · 총 {elapsed / 60:.1f}분")

    # ── 보고서 ──
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(args.report) if args.report else REPORTS / f"batch_{stamp}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = [
        f"# 배치 실행 {stamp}",
        "",
        f"- 서버: `{args.base}`",
        f"- 해상도: {args.resolution or '서버 기본값'} · seed {args.seed} · {args.steps} steps",
        f"- 결과: {ok}/{len(results)}건 성공 · 총 {elapsed / 60:.1f}분",
        "",
        *table(results),
    ]
    if repro:
        doc += ["", "## 재현성 (S6)", "", *repro]

    failed = [r for r in results if not r.ok]
    if failed:
        doc += ["", "## 실패", ""]
        doc += [f"- `{r.image}` {r.attempt}회차 — {r.error}" for r in failed]

    path.write_text("\n".join(doc) + "\n", encoding="utf-8")
    print(f"  보고서: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    print()

    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
