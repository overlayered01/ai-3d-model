# Pixal3D 로컬 테스트 환경

단일 이미지 → 3D 에셋(GLB, PBR 텍스처)을 **브라우저에서 테스트**하기 위한 로컬 도구.
모델은 [TencentARC/Pixal3D](https://github.com/TencentARC/Pixal3D) (SIGGRAPH 2026, MIT).

- 상세 계획: [PLAN.md](PLAN.md) · [문서 뷰](https://claude.ai/code/artifact/ad436919-514b-4dbb-bd51-33c050873a3d)
- 대상 하드웨어: RTX 3090 24GB (Ampere sm_86) · Windows 11

---

## 설치

각 스크립트는 끝에서 `doctor.py`로 게이트를 판정한다. 통과하지 못하면 다음으로 넘어가지 않는다.

```powershell
# Phase 1 — Python 3.12 venv + 캐시를 D: 로 리다이렉트
powershell -ExecutionPolicy Bypass -File setup\01_python_venv.ps1
.\.venv\Scripts\Activate.ps1

# Phase 1 — PyTorch 2.10.0+cu130                        [게이트 G1]
powershell -ExecutionPolicy Bypass -File setup\02_torch.ps1

# Phase 2 — CUDA 확장 (최대 난관)                        [게이트 G2]
powershell -ExecutionPolicy Bypass -File setup\03_cuda_wheels.ps1
powershell -ExecutionPolicy Bypass -File setup\04_pixal3d.ps1

# Phase 3 — 가중치 다운로드
powershell -ExecutionPolicy Bypass -File setup\05_weights.ps1
```

언제든 현재 상태를 확인할 수 있다:

```powershell
python setup\doctor.py            # 전체
python setup\doctor.py --gate g1  # Python/torch/GPU 만
python setup\doctor.py --json     # 기계 판독용
```

설치 절차 자체가 아직 재현되는지 확인하려면 (지금 쓰는 `.venv`는 건드리지 않는다):

```powershell
powershell -ExecutionPolicy Bypass -File setup\verify_clean_install.ps1
```

별도 venv를 새로 만들어 01→04를 그대로 태우고 G2 게이트로 판정한 뒤 지운다.
가중치와 upstream 클론은 재사용하므로 25GB를 다시 받지 않는다.

### 선행 조건

**Python 3.12.10 이 필요하다.** 전역에 설치된 3.14는 torch와 커뮤니티 CUDA 휠이 지원하지 않는다.
`01_python_venv.ps1`이 3.12를 찾지 못하면 설치 방법을 안내하고 멈춘다. 전역 PATH에 등록할 필요는 없다.

---

## 왜 이 버전들로 고정했나

RTX 3090은 Ampere `sm_86`이고, 이 아키텍처용 Windows 사전빌드 휠이 **하나의 조합으로만** 존재한다.
넷 중 하나라도 어긋나면 커널이 import 단계에서 깨진다.

| | 버전 |
|---|---|
| Python | 3.12.10 |
| PyTorch | 2.10.0+cu130 |
| CUDA | 13.0 |
| GPU arch | sm_86 |

설치하지 않는 것:

- **flash_attn** — `ATTN_BACKEND=sdpa`로 대체 (upstream README가 공식 허용)
- **drtk** — 서버사이드 턴테이블 렌더를 쓰지 않는다. 브라우저 3D 뷰어가 그 역할을 한다

`nvdiffrast`는 **설치한다.** 렌더 프리뷰용이라 생략할 수 있을 것처럼 보이지만,
`o_voxel.postprocess.to_glb`가 UV 텍스처 베이킹에 직접 쓴다. 없으면 생성이 다
끝난 뒤 마지막 내보내기에서만 죽는다.

---

## 구조

```
setup/         설치 스크립트 + doctor.py (환경 자가진단)
upstream/      Pixal3D 클론 — 수정하지 않는다
server/        FastAPI 웹 서버
web/           브라우저 UI (model-viewer 로컬 동봉)
tools/         래퍼·검증 도구 (e2e_test · run_batch · run_inference)
tools/shims/   모듈명 셰임 — 휠이 o_voxel_vb 로 설치하는데 upstream은 o_voxel 로 import
inputs/samples/ upstream 공식 샘플 이미지
runs/          실행 산출물 (input.png · full.glb · web.glb · meta.json · log.txt)
reports/       배치 실행 보고서
docs/          BENCHMARK.md · TROUBLESHOOTING.md
```

`upstream/`을 직접 고치지 않는다. `server/pipeline.py`가 감싸고, 모듈명 차이는
`tools/shims/install_shims.py`가 site-packages에 재수출 모듈을 심어 해결한다.

---

## 실행

### 웹 서버 — 평소에는 이것만 쓰면 된다

```powershell
powershell -File setup\run_server.ps1
```

띄운 뒤 브라우저에서 **http://127.0.0.1:7860**. 업로드 → 배경 제거 확인 →
생성 → 3D 뷰어 → 이력 비교가 한 페이지에서 끝난다.

모델 로딩에 90~170초가 걸린다. 그동안 페이지는 열리지만 생성 요청은
'준비 중'으로 거절된다. `--workers 1`은 필수다 — 워커가 여럿이면 각각
22GB 모델을 로드해 곧바로 OOM이 난다.

### 검증·배치 (브라우저 없이)

```powershell
python tools\e2e_test.py                                       # 종단 1회
python tools\run_batch.py                                      # inputs\samples 전부
python tools\run_batch.py inputs\samples\1_img.png --repeat 3  # 재현성
python tools\run_batch.py --resolution 1536                    # 고해상도
```

`run_batch.py`는 `reports/batch_<타임스탬프>.md`에 표를 남긴다.

### CLI 단독 (파이프라인만 따로 볼 때)

```powershell
powershell -File setup\06_smoke_inference.ps1 -Resolution 1024
```

매 실행마다 22GB를 다시 읽으므로 웹 경로보다 70초 이상 느리다.

---

## 알아둘 것

**VRAM.** 24GB 중 데스크톱이 3GB 안팎을 점유한다. `low_vram` + 해상도 1024가 기본값이다.
1536도 실측으로 통과했다 — 최대 18.7GB, 498초. 다만 여유가 2.6GB뿐이라 브라우저에서
영상을 틀거나 다른 GPU 앱을 열면 그대로 잠식된다. 여러 건을 연달아 돌릴 때는 1024가 안전하다.

**같은 seed라도 결과가 매번 조금 다르다.** 정점 수 기준 약 0.7% 편차다. 배경 제거는
결정적이므로 원인은 CUDA 생성 경로에 있다. 결과 비교는 파일 해시 대신 정점 수와 육안으로
한다. `DETERMINISTIC=1`로 일부 완화할 수 있으나 보장은 아니고 느려진다.

**서버는 GPU를 상시 점유한다.** 웹 서버를 띄운 채로 다른 GPU 작업을 병행할 수 없다.

**네트워크.** 기본값은 `127.0.0.1`(이 PC만). upstream `app.py`는 마지막 줄이 `share=True`라
**인증 없는 공개 URL을 인터넷에 띄운다** — 그대로 실행하지 않는다. LAN 공유가 필요하면
`.env`의 `HOST`를 바꾸고 방화벽 규칙을 추가하되, 무인증 상태로 외부에 노출하지 않는다.

**GLB 텍스처가 안 보이면.** upstream은 `EXT_texture_webp` 확장으로 내보낸다. 이를 지원하지 않는
뷰어(Windows 기본 3D 뷰어 등)에서는 텍스처가 통째로 빠져 보인다. `.env`에서 `EXPORT_WEBP=0`으로
바꾸면 PNG 텍스처로 내보낸다(용량 증가).

---

## 라이선스

이 저장소의 코드는 로컬 테스트·연구 목적이다. Pixal3D 코드는 MIT지만
DINOv3 · RMBG-2.0 등 서드파티 가중치는 각각 별도 조건을 가진다.
상용 이용 전에 `upstream/Pixal3D/NOTICE`를 확인할 것.
