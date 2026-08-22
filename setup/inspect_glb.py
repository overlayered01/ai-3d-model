"""GLB 검사 — 생성물이 실제로 읽히는지, 뷰어에서 텍스처가 보일지 미리 판정한다.

glTF 바이너리 컨테이너를 직접 파싱한다. trimesh 로 로드하지 않는 이유는
trimesh 가 읽을 수 있다는 사실이 '브라우저 뷰어에서 보인다'를 보장하지 않기
때문이다. 우리가 알고 싶은 건 후자다.

특히 EXT_texture_webp 를 확인한다 (계획서 리스크 R12).
upstream inference.py 는 `extension_webp=True` 로 내보내는데, 이 확장을
지원하지 않는 뷰어(Windows 기본 3D 뷰어 등)에서는 텍스처가 통째로 빠져 보인다.

사용법:
    python setup/inspect_glb.py runs/20260822_120000_lowvram_1024/output.glb
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

GLTF_MAGIC = 0x46546C67  # 'glTF'
CHUNK_JSON = 0x4E4F534A  # 'JSON'
CHUNK_BIN = 0x004E4942   # 'BIN\0'

# 널리 지원되는 확장 — 있어도 걱정하지 않는다
SAFE_EXTENSIONS = {
    "KHR_materials_unlit",
    "KHR_texture_transform",
    "KHR_materials_emissive_strength",
    "KHR_materials_ior",
    "KHR_materials_specular",
}

# 뷰어 지원이 갈리는 확장 — 있으면 경고한다
RISKY_EXTENSIONS = {
    "EXT_texture_webp": (
        "WebP 텍스처. three.js / <model-viewer> 는 지원하지만 "
        "Windows 기본 3D 뷰어 등에서는 텍스처가 통째로 빠져 보입니다."
    ),
    "KHR_texture_basisu": (
        "KTX2/Basis 압축 텍스처. 전용 트랜스코더가 없는 뷰어에서는 표시되지 않습니다."
    ),
    "EXT_meshopt_compression": (
        "meshopt 압축. 디코더를 등록하지 않은 뷰어에서는 로드에 실패합니다."
    ),
}


def read_gltf_json(path: Path) -> tuple[dict, int]:
    """GLB 의 JSON 청크만 파싱한다. (gltf, BIN 청크 크기)"""
    with path.open("rb") as f:
        header = f.read(12)
        if len(header) < 12:
            raise ValueError("파일이 너무 짧습니다 — GLB 가 아닙니다")

        magic, version, total = struct.unpack("<III", header)
        if magic != GLTF_MAGIC:
            raise ValueError("glTF 매직이 없습니다 — GLB 가 아닙니다")
        if version != 2:
            raise ValueError(f"glTF 버전 {version} — 2 만 지원합니다")

        gltf: dict | None = None
        bin_size = 0

        while f.tell() < total:
            head = f.read(8)
            if len(head) < 8:
                break
            length, ctype = struct.unpack("<II", head)
            data = f.read(length)
            if ctype == CHUNK_JSON:
                gltf = json.loads(data.decode("utf-8"))
            elif ctype == CHUNK_BIN:
                bin_size = length

        if gltf is None:
            raise ValueError("JSON 청크가 없습니다")
        return gltf, bin_size


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python setup/inspect_glb.py <파일.glb>")
        return 2

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"  [FAIL] 파일 없음: {path}")
        return 1

    size_mb = path.stat().st_size / 2**20

    try:
        gltf, bin_size = read_gltf_json(path)
    except Exception as e:
        print(f"  [FAIL] GLB 파싱 실패: {e}")
        return 1

    # ── 지오메트리 ──────────────────────────────────────────────────
    meshes = gltf.get("meshes", [])
    accessors = gltf.get("accessors", [])
    verts = tris = 0
    for mesh in meshes:
        for prim in mesh.get("primitives", []):
            pos = prim.get("attributes", {}).get("POSITION")
            if pos is not None and pos < len(accessors):
                verts += accessors[pos].get("count", 0)
            idx = prim.get("indices")
            if idx is not None and idx < len(accessors):
                tris += accessors[idx].get("count", 0) // 3

    # ── 텍스처 ──────────────────────────────────────────────────────
    images = gltf.get("images", [])
    mimes: dict[str, int] = {}
    for img in images:
        mt = img.get("mimeType", "(uri)")
        mimes[mt] = mimes.get(mt, 0) + 1

    materials = gltf.get("materials", [])
    used = set(gltf.get("extensionsUsed", []))
    required = set(gltf.get("extensionsRequired", []))

    print(f"  파일     {path.name} · {size_mb:.1f}MB (BIN {bin_size / 2**20:.1f}MB)")
    print(f"  지오메트리 정점 {verts:,} · 삼각형 {tris:,} · 메시 {len(meshes)}")
    print(f"  머티리얼 {len(materials)}개 · 이미지 {len(images)}개"
          + (f" ({', '.join(f'{k} x{v}' for k, v in mimes.items())})" if mimes else ""))

    problems = 0

    if verts == 0 or tris == 0:
        print("  [FAIL] 지오메트리가 비어 있습니다.")
        problems += 1

    if not images:
        print("  [WARN] 텍스처 이미지가 없습니다 — 지오메트리만 있는 결과일 수 있습니다.")

    # ── 확장 판정 ───────────────────────────────────────────────────
    if used:
        print(f"  확장     {', '.join(sorted(used))}")

    for ext in sorted(used):
        if ext in RISKY_EXTENSIONS:
            marker = "필수" if ext in required else "선택"
            print()
            print(f"  [WARN] {ext} ({marker})")
            for line in RISKY_EXTENSIONS[ext].split(". "):
                if line:
                    print(f"         {line.rstrip('.')}.")
            if ext == "EXT_texture_webp":
                print("         텍스처가 안 보이면 .env 의 EXPORT_WEBP=0 으로 PNG 내보내기를 쓰세요.")
            problems += 1
        elif ext not in SAFE_EXTENSIONS:
            print(f"  [INFO] {ext} — 뷰어 지원 여부를 확인하세요.")

    print()
    if problems == 0:
        print("  [ OK ] GLB 정상 — 일반 뷰어에서 그대로 열립니다.")
    elif verts and tris:
        print("  [ OK ] GLB 는 유효합니다. 위 경고는 뷰어 선택에만 영향을 줍니다.")
    else:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
