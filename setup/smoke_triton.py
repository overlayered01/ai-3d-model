"""Triton JIT 스모크 테스트.

flex_gemm 은 Triton 기반 sparse GEMM 이므로, triton 이 import 되는 것만으로는
부족하고 실제로 커널을 컴파일해 GPU 에서 돌릴 수 있어야 한다.
Windows 의 triton-windows 는 JIT 시 컴파일러 툴체인을 요구할 수 있어
여기서 미리 확인한다.

`@triton.jit` 은 함수가 실제 .py 파일에 있어야 소스를 읽을 수 있다.
(`python -c` 로는 'could not get source code' 로 실패한다.)

사용법:  python setup/smoke_triton.py
"""

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

import torch
import triton
import triton.language as tl


@triton.jit
def _add_kernel(x_ptr, y_ptr, out_ptr, n, BLOCK: tl.constexpr):
    pid = tl.program_id(0)
    offs = pid * BLOCK + tl.arange(0, BLOCK)
    mask = offs < n
    x = tl.load(x_ptr + offs, mask=mask)
    y = tl.load(y_ptr + offs, mask=mask)
    tl.store(out_ptr + offs, x + y, mask=mask)


def main() -> int:
    print(f"  torch  {torch.__version__} (CUDA {torch.version.cuda})")
    print(f"  triton {triton.__version__}")

    if not torch.cuda.is_available():
        print("  [FAIL] CUDA 사용 불가")
        return 1
    print(f"  GPU    {torch.cuda.get_device_name(0)}")

    n = 4096
    x = torch.rand(n, device="cuda")
    y = torch.rand(n, device="cuda")
    out = torch.empty_like(x)

    try:
        _add_kernel[(triton.cdiv(n, 1024),)](x, y, out, n, BLOCK=1024)
        torch.cuda.synchronize()
    except Exception as e:
        print(f"  [FAIL] 커널 컴파일/실행 실패: {type(e).__name__}: {e}")
        print()
        print("  Windows 에서 triton JIT 은 MSVC 툴체인을 요구할 수 있습니다.")
        print("  Visual Studio Build Tools (C++ 데스크톱 개발) 설치를 검토하세요:")
        print("    winget install Microsoft.VisualStudio.2022.BuildTools")
        return 1

    if not torch.allclose(out, x + y):
        print("  [FAIL] 결과 불일치 — 커널은 돌았으나 값이 틀립니다")
        return 1

    print("  [ OK ] Triton JIT 컴파일 + GPU 실행 + 결과 검증 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
