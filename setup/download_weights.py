"""가중치 사전 다운로드 — Phase 3.

추론과 분리해 별도로 돌린다. 그래야 "다운로드 실패"와 "추론 실패"를
혼동하지 않는다 (계획서 리스크 R6).

CLI(`hf download`) 대신 Python API 를 쓴다. 재시도·부분 실패 처리·용량 보고를
직접 제어할 수 있고, huggingface_hub 의 CLI 진입점 변경에 영향받지 않는다.

이미 받은 파일은 건너뛴다. 중단 후 다시 돌려도 안전하다.

사용법:
    python setup/download_weights.py
    python setup/download_weights.py --check   # 다운로드 없이 현황만
"""

from __future__ import annotations

import argparse
import os
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

# 필수 3종 — 이 중 하나라도 없으면 추론이 불가능하다
REQUIRED = [
    ("TencentARC/Pixal3D", "메인 모델 (3단 캐스케이드 체크포인트)"),
    ("camenduru/dinov3-vitl16-pretrain-lvd1689m", "이미지 조건 인코더"),
    ("Ruicheng/moge-2-vitl", "카메라 FOV 추정"),
]

# 선택 — 배경 제거. 게이트된 저장소라 HF 계정 동의가 선행되어야 한다.
OPTIONAL = [
    ("briaai/RMBG-2.0", "배경 제거 (알파 PNG 를 쓰면 불필요)"),
]

RETRIES = 3


def _gb(n: int) -> str:
    return f"{n / 2**30:.2f} GB"


def remote_size(repo_id: str) -> int:
    """저장소 총 용량. 실패하면 0."""
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo_id, files_metadata=True)
        return sum((s.size or 0) for s in (info.siblings or []))
    except Exception:
        return 0


def local_size(repo_id: str) -> int:
    """캐시에 이미 있는 용량. 없으면 0."""
    try:
        from huggingface_hub import scan_cache_dir

        for repo in scan_cache_dir().repos:
            if repo.repo_id == repo_id:
                return repo.size_on_disk
    except Exception:
        pass
    return 0


def download(repo_id: str, note: str, required: bool) -> bool:
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import GatedRepoError

    remote = remote_size(repo_id)
    have = local_size(repo_id)

    print()
    print(f"  {repo_id}")
    print(f"    {note}")
    if remote:
        print(f"    원격 {_gb(remote)} · 캐시 {_gb(have)}")

    if remote and have >= remote * 0.99:
        print("    [ OK ] 이미 완료 — 건너뜀")
        return True

    for attempt in range(1, RETRIES + 1):
        try:
            t0 = time.time()
            snapshot_download(repo_id=repo_id, resume_download=True)
            dt = time.time() - t0
            got = local_size(repo_id)
            print(f"    [ OK ] {_gb(got)} · {dt / 60:.1f}분")
            return True

        except GatedRepoError:
            print("    [게이트] 이 저장소는 접근 승인이 필요합니다.")
            print(f"           1. https://huggingface.co/{repo_id} 에서 라이선스 동의")
            print("           2. hf auth login  (또는 huggingface-cli login)")
            return False

        except KeyboardInterrupt:
            raise

        except Exception as e:
            kind = type(e).__name__
            if attempt < RETRIES:
                wait = attempt * 10
                print(f"    [재시도 {attempt}/{RETRIES}] {kind}: {e}")
                print(f"           {wait}초 후 다시 시도합니다 (받은 부분은 유지됩니다)")
                time.sleep(wait)
            else:
                print(f"    [FAIL] {kind}: {e}")
                return False

    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Pixal3D 가중치 다운로드")
    ap.add_argument("--check", action="store_true", help="다운로드 없이 현황만 본다")
    args = ap.parse_args()

    hf_home = os.environ.get("HF_HOME", "")
    print()
    print("  Pixal3D 가중치")
    print(f"  HF_HOME = {hf_home or '(미설정 — 기본 캐시 위치)'}")
    print("  " + "─" * 70)

    if hf_home.upper().startswith("C:"):
        print("  [FAIL] HF_HOME 이 C: 를 가리킵니다. C: 여유가 부족합니다.")
        print("         setup/01_python_venv.ps1 을 실행하거나 직접 D: 로 지정하세요.")
        return 1

    if args.check:
        total_remote = total_have = 0
        for repo_id, note in REQUIRED + OPTIONAL:
            r, h = remote_size(repo_id), local_size(repo_id)
            total_remote += r
            total_have += h
            mark = "OK  " if r and h >= r * 0.99 else "없음"
            print(f"  [{mark}] {repo_id:46s} {_gb(h)} / {_gb(r)}")
        print("  " + "─" * 70)
        print(f"  합계 {_gb(total_have)} / {_gb(total_remote)}")
        return 0

    failed = []
    for repo_id, note in REQUIRED:
        if not download(repo_id, note, required=True):
            failed.append(repo_id)

    for repo_id, note in OPTIONAL:
        if not download(repo_id, note, required=False):
            print("    선택 항목이므로 계속 진행합니다.")

    print()
    print("  " + "─" * 70)
    if failed:
        print(f"  ✗ 필수 가중치 {len(failed)}건 실패: {', '.join(failed)}")
        print("    네트워크 문제라면 같은 명령을 다시 실행하세요 — 이어받기됩니다.")
        return 1

    total = sum(local_size(r) for r, _ in REQUIRED + OPTIONAL)
    print(f"  ✓ 필수 가중치 준비 완료 — 캐시 {_gb(total)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
