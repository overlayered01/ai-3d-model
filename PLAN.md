# Pixal3D 로컬 구축 계획서 (rev.4 — Phase 6 실측 반영)

- 작성일: 2026-08-22 (rev.3: 2026-08-23 · rev.4: 2026-08-25)
- 상태: **게이트 G0~G5 통과. G6는 S6 판정 확정 후 부분 통과.**
  Track A(Windows 네이티브)로 GLB 생성·웹 종단 흐름 성공.
  남은 것은 브라우저 육안 확인 하나뿐이다 (S2 — 사용자 몫).
- 대상: `https://github.com/TencentARC/Pixal3D` (SIGGRAPH 2026, MIT License)
- 프로젝트 위치: `D:\Work\Private\AI_3D_Model`
- 문서: https://claude.ai/code/artifact/ad436919-514b-4dbb-bd51-33c050873a3d

---

## 1. 목표와 성공 기준

### 목표
단일 이미지 → 3D 에셋(GLB, PBR 텍스처) 생성을 **브라우저에서 반복 실행·비교할 수 있는 로컬 웹 도구**로 구축한다.
업로드 → 파라미터 조정 → 생성 → 인터랙티브 3D 뷰어 확인 → 이력 비교가 한 페이지에서 끝나야 한다.

### 성공 기준 (Definition of Done)

| # | 기준 | 검증 방법 |
|---|---|---|
| S1 | `inference.py`가 오류 없이 GLB 생성 | 파일 존재 + 크기 > 0 |
| S2 | 생성 GLB가 뷰어에서 정상 렌더 (지오메트리 + 텍스처) | 브라우저 뷰어 육안 확인 |
| S3 | **웹페이지에서 업로드 → 생성 → 뷰어 확인**이 한 흐름으로 완결 | 브라우저에서 처음부터 끝까지 1회 통과 |
| S4 | 생성 중 진행 상태가 실시간 표시 | 단계명 + 스텝 카운터 갱신 |
| S5 | 이전 결과 이력 조회 및 나란히 비교 | 갤러리에서 2개 이상 재열람 |
| S6 | 동일 seed 재실행 시 결과 재현 | 2회 실행 정점수·파일해시 비교 |
| S7 | 설치·실행 절차가 스크립트로 재현 가능 | 클린 환경 재설치 1회 성공 |

**최소 합격선(MVP)**: S1 + S2 + S3.

### 판정 현황 (rev.4)

| # | 판정 | 근거 |
|---|---|---|
| S1 | ✅ | CLI·웹 양쪽에서 GLB 생성. `docs/BENCHMARK.md` |
| S2 | ⏳ | **브라우저 육안 확인만 남음.** API 로는 `web.glb` 정상 서빙 확인 |
| S3 | ✅ | `tools/e2e_test.py` 로 업로드→생성→결과 서빙 전 구간 통과 |
| S4 | ✅ | tqdm 가로채기로 단계명·스텝 카운터 갱신 |
| S5 | ✅ | `runs/` 디렉터리 인덱스 + `/api/runs` 갤러리 |
| S6 | ⚠️ | **바이트 재현 실패.** 정점 편차 0.7%. 원인 규명 완료 — 아래 참고 |
| S7 | ✅ | `setup/verify_clean_install.ps1` 로 반복 검증 가능. 실제로 결함 2건 검출 |

**S6 는 달성하지 못했다.** 같은 이미지·seed·해상도로 3회 돌린 결과가 매번 달랐다
(정점 792,017 / 797,445 / 795,792). 다만 원인은 좁혔다 — `processed.png` 가 네 번
모두 비트 단위로 동일했으므로 배경 제거는 결정적이고, 비결정성은 CUDA 생성 경로에
있다.

`DETERMINISTIC=1`(cuDNN 자동선택·TF32 끄기)을 만들어 실측했으나 **듣지 않았다.**
재현은 그대로 안 되고 소요만 70% 늘었다(346.5s / 370.5s). 원인이 그 둘이
아니라는 뜻이며, 희소 복셀 커널의 atomic 누적 순서가 유력하다. 기본값은 꺼짐이고,
스위치는 "이미 해봤고 안 된다"를 남기기 위해 유지한다.

편차가 형상을 바꾸는 수준은 아니므로 결과 비교는 해시 대신 정점 수와 육안으로 한다.
정확히 같은 것이 다시 필요하면 다시 만들지 말고 GLB 를 보관한다.

**S7 검증이 실제로 결함 6건을 찾아냈다.** 클린 venv 로 01→04 를 순서대로
태워 보고서야 드러난 것들이다. 기존 환경에는 이미 다 깔려 있어 보이지 않았다.

| # | 결함 | 증상 |
|---|---|---|
| 1 | `03` 이 `nvdiffrast` 를 설치하지 않음 | 생성 4분을 태운 뒤 GLB 내보내기에서만 죽음 |
| 2 | `o_voxel_vb` 대신 `_ap` 판만 설치 | 같은 자리 — `_ap` 엔 `postprocess` 가 없음 |
| 3 | `triton-windows` 미설치 | torch 의 triton 핀에 `platform_system == "Linux"` 조건이 붙어 pip 이 조용히 건너뜀 |
| 4 | 커널 휠의 파이썬 의존성 미공급 | `--no-deps` 로 넣었으니 `trimesh`·`plyfile`·`zstandard` 등을 03 이 직접 넣어야 함 |
| 5 | 셰임 검증이 `tqdm` 을 요구 | 04 의 requirements 에 있어 03 시점엔 없음 |
| 6 | `03` 의 게이트가 04 의 산출물까지 검사 | **통과할 수 없는 게이트였다** |

1~3 은 "손으로 고치고 스크립트에 반영 안 함", 4~6 은 "기존 환경이라 안 드러남"
부류다. 전부 스크립트와 `doctor.py` 게이트에 반영했다.

교훈 두 가지를 남긴다.

- **게이트는 그 단계가 책임지는 것만 검사한다.** 아니면 통과할 수 없는 게이트가
  생기고, 그 사실은 클린 설치를 해봐야만 드러난다. `g2` 는 03 의 범위(커널)로
  좁혔고 파이썬 의존성은 04 가 부르는 `all` 로 옮겼다.
- **`--no-deps` 로 설치했으면 그 대가를 같은 스크립트에서 치른다.** torch 를
  지키려고 의존성 해석을 껐다면, 휠이 import 되는 데 필요한 것은 직접 넣고
  torch 가 밀려나지 않았는지 확인까지 해야 한다.

**클린 설치 소요는 7.5분이다** (가중치·upstream 클론 재사용, pip 캐시 있음).

---

## 2. 현재 환경 진단 (실측값)

| 항목 | 실측 결과 | 판정 |
|---|---|---|
| OS | Windows 11 Pro 26100 | ⚠️ upstream은 **Linux만 테스트됨** |
| GPU | NVIDIA RTX 3090 24GB (Ampere, **sm_86**) | ✅ 최소 요구(24GB) 충족, 여유 없음 |
| GPU 현재 점유 | 3.4GB (Edge WebView, Slack 등) | ⚠️ 실사용 가능 VRAM ≈ 20GB |
| 드라이버 | 610.47 / CUDA UMD 13.3 | ✅ cu130 빌드 사용 가능 |
| Python | 3.14.3 (시스템 전역) | ❌ **사용 불가** — torch 미지원 |
| Conda | 미설치 | ⚠️ TRELLIS.2 공식 절차는 conda 전제 |
| WSL | **미설치** | ⚠️ Track B 선택 시 신규 설치 필요 |
| 디스크 C: | 55GB 여유 (95% 사용) | ❌ 모델/캐시 저장 불가 |
| 디스크 D: | 812GB 여유 | ✅ 전 자산 D:에 배치 |
| Git | 2.49.0 | ✅ |

> **선조치 3가지**
> 1. Python 3.12.10 별도 설치 — 전역 3.14는 건드리지 않고 venv 격리
> 2. `HF_HOME` / pip 캐시 / temp를 **전부 D:로 리다이렉트**
> 3. 웹 서버는 GPU를 **상시 점유**하므로 서버 가동 중 다른 GPU 작업 병행 불가

---

## 3. 기술 스택 의존성 지도

```
Pixal3D (inference.py · app.py)
 ├── TRELLIS.2 base env          공식 설치 절차가 Linux/conda 전제
 │    ├── flex_gemm              [필수] Triton 기반 sparse GEMM
 │    ├── cumesh                 [필수] mesh 후처리 · UV unwrap
 │    ├── o_voxel                [필수] GLB export
 │    ├── natten 0.21.x          [필수] NAF 업샘플러, CUDA libnatten
 │    ├── flash_attn             [선택] ATTN_BACKEND=sdpa 로 대체
 │    └── nvdiffrast/nvdiffrec   [조건부] 서버사이드 턴테이블 렌더
 ├── utils3d 0.0.2 (순수 python 휠)
 ├── MoGe (git)                  카메라 FOV 추정
 ├── spaces                      [조건부] app.py 최상위 import, HF Spaces 전용
 └── 가중치: TencentARC/Pixal3D, camenduru/dinov3-vitl16, Ruicheng/moge-2-vitl
            (+ briaai/RMBG-2.0 — 게이트된 저장소, HF 계정 동의 필요)
```

### nvdiffrast — rev.3 최종 판정 (rev.1·rev.2 정정)

rev.1은 "생략 가능", rev.2는 "서버 프리뷰용이라 조건부"라고 적었다. **둘 다 틀렸다.**

실제로 확인한 사실: `o_voxel/postprocess.py`가 `import nvdiffrast.torch as dr`를 하고,
GLB의 **UV 텍스처 베이킹**에 쓴다 — 프리뷰가 아니라 내보내기 자체다.

```python
ctx = dr.RasterizeCudaContext()
rast_chunk, _ = dr.rasterize(...)
pos = dr.interpolate(out_vertices.unsqueeze(0), rast, out_faces)[0][0]
```

`inference.py`가 최상위에서 nvdiffrast를 import하지 않는다는 관찰은 맞았지만,
`o_voxel.postprocess`를 통해 **전이적으로** 요구된다는 점을 놓쳤다.

→ **텍스처 있는 GLB를 만들려면 nvdiffrast가 필수다.** 다만 컴파일할 필요는 없다 —
`nvdiffrast-0.4.0+cu130torch2.10-cp312-win_amd64` 사전빌드 휠이 존재한다.

결정 D6("서버 렌더 프리뷰 미사용")은 여전히 유효하다. 턴테이블 6모드 JPEG을 굽지
않을 뿐, 렌더러 자체는 내보내기 경로에 남는다.

### o_voxel 휠 변형 — 반드시 `_vb`

| 휠 | 포함 | GLB 내보내기 |
|---|---|---|
| `o_voxel_vb_ap` | convert · io · serialize | ❌ `postprocess` 없음 |
| `o_voxel_vb` | + **postprocess** · rasterize | ✅ |

`_ap`를 쓰면 생성이 4분 넘게 전부 성공한 뒤 **마지막 내보내기에서만** 죽는다.
ComfyUI 포크 문서가 `_ap`를 지목하지만, 그쪽은 자체 노드 코드가 보완하는 전제다.

---

## 4. 설치 트랙 선택

| | Track A ⭐ 권장 | Track B 폴백 | Track C 우회 |
|---|---|---|---|
| | Windows 네이티브 + 사전빌드 휠 | WSL2 Ubuntu | ComfyUI 노드 |
| 장점 | 컴파일 0회, 하루 안에 첫 결과 | upstream 공식 지원 플랫폼 | Windows 지원 문서화, VRAM 프리셋 |
| 단점 | 비공식 휠, 버전 미스매치 | WSL+CUDA+컴파일 수 시간, 포트 포워딩 | 웹 UI가 ComfyUI 그래프 — 원하는 형태 아님 |

### Track A 버전 고정표

| 컴포넌트 | 버전 | 출처 |
|---|---|---|
| Python | **3.12.10** (고정) | python.org |
| PyTorch | 2.10.0+cu130 | pytorch.org |
| natten | 0.21.6+torch2100cu130 · Ampere sm86 · win_amd64 | `NeilsMabet/Natten-0.21.6-Amphere-wheel-windows` |
| flex_gemm_ap / cumesh_vb / o_voxel_vb_ap | cu130torch2.10-cp312 · win_amd64 | `PozzettiAndrea/cuda-wheels` |
| flash_attn | 설치 안 함 → `ATTN_BACKEND=sdpa` | upstream README 공식 허용 |
| 웹 스택 | FastAPI + uvicorn (단일 워커) | 표준 PyPI |

> **결정**: Track A로 시작 → Phase 2 게이트 실패 시 Track B 전환. Phase 0 및 4–6 산출물은 100% 재사용.

---

## 5. 웹 인터페이스 설계

### 5.1 upstream app.py에서 확인한 것

| upstream 구현 | 내용 | 우리 방침 |
|---|---|---|
| 3단계 API 분리 | `preprocess` → `generate_3d` → `extract_glb_api` | ✅ 그대로 채택 |
| 잠재 캐시 | `pack_state`/`unpack_state` — 생성 없이 GLB만 재추출 | ✅ 그대로 채택 |
| 진행률 추적 | tqdm 몽키패치 → 세션별 JSON 파일 → `/progress` 폴링 | ✅ 그대로 채택 |
| 커스텀 프론트엔드 | `index.html`을 `/`에서 서빙 (Gradio Blocks 아님) | ⚠️ 교체 |
| 서버사이드 렌더 프리뷰 | 턴테이블 6모드 JPEG — nvdiffrast 필요 | ❌ 제거 |
| `import spaces` | HF Spaces ZeroGPU 전용, `@spaces.GPU(duration=)` | ⚠️ 제거 또는 셰임 |
| `app.launch(share=True)` | **기본값이 공개 gradio.live 터널** | ❌ 반드시 끔 |

> ⚠️ **보안 — 먼저 조치할 것**
> upstream `app.py` 마지막 줄은 `app.launch(show_error=True, share=True)`.
> `share=True`는 **인증 없는 공개 URL을 인터넷에 띄운다.** URL을 아는 누구나 이 워크스테이션의 GPU로 작업을 돌릴 수 있다.
> 로컬 테스트용 기본값은 `127.0.0.1` 바인딩이어야 한다.

### 5.2 웹 구성 선택지

- **W1 최소** — upstream `app.py`를 `share=False`로만 바꿔 실행. 추가 개발 0, 그러나 nvdiffrast·spaces 의존, 이력/비교/큐 없음. **값싼 검증용.**
- **W3 하이브리드 ⭐ 권장** — upstream의 3단계 API·진행률 훅·잠재 캐시를 재사용하고 프론트엔드를 브라우저 3D 뷰어로 새로 제작. nvdiffrast 불필요.
- **W2 최대** — 서버까지 전면 자체 구현. 통제력 최고, 그러나 upstream이 이미 푼 문제를 다시 푼다.

### 5.3 W3 아키텍처

GPU가 하나이므로 **동시 실행은 반드시 1건**이고, 모델은 **프로세스에 상주**해야 한다.
요청마다 `inference.py`를 새로 띄우면 매번 모델 로딩에 수 분을 낭비한다. 이 두 제약이 아키텍처를 결정한다.

```
브라우저 (단일 index.html, 오프라인 자산)
  │
  ├─ 드롭존 업로드 ─────────────────► POST /api/preprocess
  ├─ 파라미터 폼 (seed·resolution·steps·fov)
  ├─ [생성] ────────────────────────► POST /api/generate      → job_id
  ├─ 진행률 (500ms 폴링) ───────────► GET  /api/progress?job_id=
  ├─ [GLB 추출] ────────────────────► POST /api/extract       → web.glb + full.glb
  ├─ <model-viewer> 인터랙티브 뷰어  ◄─ GET  /runs/{id}/web.glb
  └─ 이력 갤러리 · 나란히 비교 ─────► GET  /api/runs

FastAPI · uvicorn --workers 1 · 127.0.0.1:7860
  ├─ 작업 큐: asyncio.Queue, 동시성 1 (초과 요청은 대기열 표시)
  ├─ /runs 정적 서빙
  └─ 파이프라인 상주 (CUDA 컨텍스트 1개)
       └─ pack_state 잠재 캐시 → 재추출 시 생성 단계 생략
```

### 5.4 요청 흐름

| # | 레인 | 동작 |
|---|---|---|
| 01 | 브라우저 | 이미지 드롭 → 업로드 (PNG/JPG, RGBA면 알파 유지 옵션) |
| 02 | **GPU** | `preprocess_image()` — 배경 제거·정사각 패딩. 수 초, 미리보기 즉시 반환 |
| 03 | 브라우저 | 전처리 결과 확인 후 파라미터 조정 → 생성 요청<br>*여기서 멈출 수 있는 게 중요 — 배경 제거가 틀리면 생성은 무조건 실패* |
| 04 | 서버 | 작업 큐 등록, `job_id` 즉시 반환. GPU가 바쁘면 대기 순번 표시 |
| 05 | **GPU** | MoGe 카메라 추정 → 3단 캐스케이드 (Sparse Structure → Shape → Texture)<br>tqdm 훅이 단계·스텝을 파일로 기록 |
| 06 | 브라우저 | 진행률 폴링 — 현재 단계명과 스텝 카운터 표시 |
| 07 | **GPU** | 잠재 저장 후 GLB 2종 추출 — `full.glb`(4096 tex / 1M tri) + `web.glb`(2048 tex / 200k tri) |
| 08 | 브라우저 | 경량 `web.glb`를 뷰어에 로드, `full.glb`는 다운로드 링크. 이력 갤러리에 추가 |

> **GLB 2종 추출 근거**
> upstream 기본값 `texture_size=4096`, `decimation_target=1,000,000` → GLB가 수십~수백 MB.
> 브라우저에서 열면 로딩이 느리고 메모리를 크게 먹는다. 뷰어용 경량본과 저장용 원본을 분리 추출하면
> 웹 UI 반응성과 결과물 품질을 둘 다 지킬 수 있다. 잠재 캐시가 있으므로 추출 2회 비용은 생성 대비 미미하다.

### 5.5 브라우저 뷰어

`<model-viewer>`(three.js 기반) 또는 three.js `GLTFLoader`. 어느 쪽이든 **JS를 로컬에 받아두고 CDN을 참조하지 않는다.**

> ⚠️ **함정 — WebP 텍스처**
> `inference.py`는 `glb.export(output_path, extension_webp=True)`로 내보낸다 → glTF `EXT_texture_webp` 확장.
> **이 확장을 지원하지 않는 뷰어에서는 텍스처가 통째로 빠져 보인다** (Windows 기본 3D 뷰어가 대표적).
> three.js 계열은 지원하나 로더 설정 확인 필요. Phase 3의 검증 항목.
> 실패 시 `extension_webp=False`로 PNG 텍스처 내보내기(용량 증가 감수).

### 5.6 접근 범위

| 범위 | 설정 | 비고 |
|---|---|---|
| **로컬 전용** (기본값) | `--host 127.0.0.1` | 이 PC의 브라우저에서만 접근 |
| LAN 공유 | `--host 0.0.0.0` + Windows 방화벽 인바운드 규칙 | 인증 없음에 유의 |
| 외부 공유 | Cloudflare Tunnel 등 + 기본 인증 | **인증 없이 열지 않는다** |

---

## 6. 프로젝트 구조

```
D:\Work\Private\AI_3D_Model\
├── PLAN.md                     본 계획서
├── README.md                   설치·실행 요약
├── .env.example                HF_HOME, ATTN_BACKEND, HOST, PORT
├── setup/
│   ├── 01_python_venv.ps1      Python 3.12.10 venv, pip 캐시 D: 지정
│   ├── 02_torch.ps1            torch 2.10.0+cu130
│   ├── 03_cuda_wheels.ps1      flex_gemm / cumesh / o_voxel / natten
│   ├── 04_pixal3d.ps1          저장소 클론 + requirements + utils3d + MoGe
│   ├── 05_weights.ps1          HF 가중치 사전 다운로드 → D:\models
│   └── doctor.py               환경 자가진단 — 게이트 G2의 실행체
├── upstream/Pixal3D/           git clone (수정 금지)
├── server/
│   ├── main.py                 FastAPI 앱 · 라우트 · 정적 서빙
│   ├── queue.py                단일 슬롯 작업 큐 · 상태 머신
│   ├── pipeline.py             파이프라인 상주 래퍼 (preprocess/generate/extract)
│   ├── progress.py             upstream tqdm 훅 재사용
│   └── store.py                runs/ 인덱스 · 메타 기록
├── web/
│   ├── index.html              단일 페이지 UI
│   ├── app.js                  업로드 · 폴링 · 뷰어 · 갤러리
│   ├── style.css
│   └── vendor/                 model-viewer / three.js (로컬 동봉)
├── tools/
│   ├── run_batch.py            CLI 배치 — 서버 API 호출 방식
│   ├── config.py               프리셋 quality / balanced / lowvram
│   └── shims/                  모듈명 별칭 — 리스크 R1 대응
├── inputs/                     개인 테스트 이미지 (추적 안 함)
│   └── samples/                upstream 공식 샘플 (가벼운 5장만 커밋)
├── runs/<YYYYMMDD_HHMMSS>/     input.png · full.glb · web.glb · meta.json · log.txt
└── docs/
    ├── TROUBLESHOOTING.md
    └── BENCHMARK.md
```

**원칙**: `upstream/Pixal3D`는 직접 수정하지 않는다. `server/pipeline.py`가 upstream 모듈을 import해 감싸고,
필요한 우회는 `tools/shims/`에서 `sys.path` 주입으로 해결한다.

CLI 배치(`run_batch.py`)는 파이프라인을 따로 로드하지 않고 **웹 서버 API를 호출**한다.
모델이 두 번 뜨는 일이 없고, 웹과 CLI가 같은 큐·같은 이력을 공유한다.

---

## 7. 단계별 실행 계획

### Phase 0 — 스캐폴딩 (0.5h)
1. 디렉터리 구조 생성 (`server/`, `web/` 포함), `git init`
2. `.gitignore` — `runs/`, `models/`, `.venv/`, `upstream/` 제외
3. `.env.example` — `HF_HOME=D:\models\hf`, `ATTN_BACKEND=sdpa`, `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`, `HOST=127.0.0.1`
4. `upstream/Pixal3D` 클론 — **`main` 브랜치**

**G0**: 구조 생성 완료

### Phase 1 — 파이썬 환경 (1h)
1. Python 3.12.10 설치 — 전역 3.14 유지, PATH 미등록
2. `.venv` 생성
3. pip 캐시 → `D:\cache\pip`, `HF_HOME` → `D:\models\hf` 고정
4. `torch==2.10.0+cu130` 설치

**G1**: `torch.cuda.get_device_capability() == (8, 6)`. 실패 시 Track B 조기 전환.

### Phase 2 — CUDA 확장 설치 (2~4h, **최대 난관**)

설치 **순서 엄수** — 틀리면 pip이 작동하던 torch를 갈아엎는다.
1. torch (Phase 1 완료)
2. `flex_gemm_ap`, `cumesh_vb`, `o_voxel_vb_ap` — 전부 `--no-deps`.
   **`drtk`·nvdiffrast는 설치하지 않는다** (브라우저 뷰어 채택)
3. attention: **생략** (sdpa 사용)
4. `natten` Ampere sm86 휠
5. `pip install -r upstream/Pixal3D/requirements.txt`
6. `utils3d-0.0.2-py3-none-any.whl`

```
# setup/doctor.py 가 자동 검사하는 항목
torch.cuda.is_available()            == True
import flex_gemm  (또는 flex_gemm_ap)
import cumesh     (또는 cumesh_vb)
import o_voxel    (또는 o_voxel_vb_ap)
import natten; natten.HAS_LIBNATTEN  == True
import utils3d, moge
```

**반드시 확인**: 휠 모듈명이 `o_voxel_vb_ap`라면 upstream의 `import o_voxel`이 실패한다.
`tools/shims/o_voxel.py` 별칭 모듈로 재수출. **Track A의 최대 미검증 지점.**

**G2 (차단 게이트)**: import 스모크 전항목 통과. 실패 시 Track B 전환.

### Phase 3 — CLI 첫 추론 (1~2h)

웹을 붙이기 전에 파이프라인 자체를 CLI로 먼저 통과시킨다 — 웹 레이어 버그와 파이프라인 버그를 섞지 않기 위해.

1. 가중치 사전 다운로드 (다운로드 실패를 추론 실패와 분리)
2. 가장 보수적인 설정으로 첫 실행
3. 단계적 상향: `1024 --low_vram` → `1536 --low_vram` → `1536 표준`

```
ATTN_BACKEND=sdpa python inference.py \
  --image assets/images/0_img.png --output ./out.glb \
  --low_vram --resolution 1024
```

**G3**: GLB 생성 + 뷰어 정상 렌더 → **S1, S2 달성.** WebP 텍스처 표시 여부도 함께 확인.

### Phase 4 — 웹 백엔드 (3~4h)
- `server/pipeline.py` — 파이프라인 상주 래퍼. upstream `app.py`에서 `preprocess`/`generate`/`extract` 로직을 가져오되 **렌더 프리뷰 호출과 `spaces` 데코레이터는 제거**
- `server/queue.py` — 단일 슬롯 큐. 상태 `queued → running → done / failed`, 대기 순번 노출
- `server/progress.py` — upstream tqdm 몽키패치 재사용, `job_id` 기준으로 전환
- `server/store.py` — `runs/<ts>/`에 입력·GLB 2종·`meta.json`·로그 기록
- 라우트: `POST /api/preprocess`, `POST /api/generate`, `GET /api/progress`, `POST /api/extract`, `GET /api/runs`
- `uvicorn --workers 1 --host 127.0.0.1` — 워커가 여럿이면 모델 중복 로드 → OOM

**G4**: `curl`로 전 라우트 왕복 성공, GLB 2종 생성 확인

### Phase 5 — 웹 프론트엔드 (3~4h, **MVP**)
- 드롭존 업로드 + 전처리 결과 미리보기 + 재시도
- 파라미터 폼 — seed, resolution, sampling steps, FOV, 프리셋 3종
- 진행률 패널 — 단계명 + 스텝 카운터 + 대기 순번
- `<model-viewer>` 인터랙티브 뷰어 (`vendor/`에 로컬 동봉)
- 이력 갤러리 — 썸네일·메타·재열람, 2개 나란히 비교
- 원본 `full.glb` 다운로드 링크

**G5**: 브라우저에서 업로드 → 생성 → 3D 확인 한 흐름 완결 → **S3, S4, S5 달성.**

### Phase 6 — 운영·문서화 (2~3h) — **완료**
- 실패 복구 — OOM 시 작업만 실패 처리하고 서버는 유지, `torch.cuda.empty_cache()` 후 다음 작업 수용
- 배치 CLI(`tools/run_batch.py`)를 서버 API 호출로 구현 → `reports/batch_*.md`
- 접근 범위 확정 — 기본 `127.0.0.1`, 필요 시 LAN 규칙 문서화
- `docs/TROUBLESHOOTING.md` · `docs/BENCHMARK.md`
- 클린 재설치 검증 (`setup/verify_clean_install.ps1`)

Phase 6 에서 추가로 드러난 것 — 계획에 없던 항목이다.

- **1536 해상도가 통과한다.** 최대 18.7GB, 498초. R4 의 전제를 완화한다.
- **소요 시간 편차가 크다.** 1024 에서 178초와 742초가 나왔다. 전부 GLB 추출의
  `Parameterizing new mesh`(cumesh UV 아틀라스) 구간에서 갈렸다 — 17초에서 229초.
- **그 구간이 GIL 을 오래 쥔다.** 진행률 폴링이 30초를 넘겨 실패하고, 배치가
  다음 건을 밀어 넣어 큐가 꼬였다. 클라이언트·서버 양쪽을 고쳤다 (R13).

**G6**: S7 달성. S6 는 미달성이며 원인 규명과 완화책으로 갈음한다.

---

## 8. 리스크 등록부

| ID | 리스크 | 영향 | 확률 | 대응 |
|---|---|:---:|:---:|---|
| R1 | 휠 모듈명 불일치 (`o_voxel_vb_ap` vs `o_voxel`) | 높음 | 중 | `tools/shims/` 별칭 모듈. 실패 시 Track B |
| R2 | natten 버전 차이 (요구 0.21.0 vs 가용 0.21.6) | 중 | 중 | API 호환 확인. 불가 시 NAF 폴백, 품질 하락 감수 |
| R3 | `setup.sh` 없이 Windows에서 base env 구성 → 의존성 누락 | 높음 | 중 | `pip check` + import 스모크로 조기 발견 |
| R4 | ~~**1536 표준 모드 OOM**~~ → **완화됨.** 실측 18.7GB 로 통과 (여유 2.6GB) | 중 | 낮음 | 1024 기본값 유지. 1536 은 데스크톱 GPU 점유 3GB 이하일 때만 |
| R5 | C: 용량 부족(55GB)으로 다운로드 실패 | 높음 | **높음** | `HF_HOME`·pip cache·temp를 D:로 강제 |
| R6 | 가중치 다운로드 지연·중단. RMBG-2.0은 게이트된 저장소 | 중 | 중 | 재시도 + 별도 Phase. RMBG는 HF 계정 동의 선행 |
| R7 | 상용 이용 시 3rd-party 라이선스 (DINOv3·RMBG 등) | 중 | 낮음 | `NOTICE` 확인. 로컬 테스트·연구 목적 한정 |
| R8 | **웹 서버 무인증 노출** — upstream 기본값이 `share=True` | 높음 | **높음** | `127.0.0.1` 바인딩 기본 고정. 외부 공유는 인증 필수 |
| R9 | GLB 용량으로 브라우저 뷰어 지연·메모리 과다 | 중 | **높음** | 뷰어용 `web.glb` 경량본 별도 추출 |
| R10 | 생성 취소 불가 — 파이프라인이 중단점 미제공 | 낮음 | 중 | 취소는 큐 대기 작업에만 적용. 실행 중은 완료까지 대기 명시 |
| R11 | OOM·크래시가 웹 서버 프로세스를 함께 종료 | 중 | 중 | 작업 단위 `try/except` + `empty_cache()`. 반복 시 서브프로세스 분리 |
| R12 | `EXT_texture_webp` 미지원 뷰어에서 텍스처 누락 | 중 | 중 | Phase 3에서 조기 확인. 실패 시 `EXPORT_WEBP=0` |
| R13 | **GIL 을 오래 쥐는 C 확장이 웹 서버를 먹통으로 만든다** — cumesh UV 아틀라스 구간이 최대 4분 | 중 | **높음** | 클라이언트 폴링이 연속 실패 20회까지 견딤. 전처리 대기 한도 1200초 + 503 응답 (rev.4에서 대응) |
| R14 | **동일 seed 재실행이 재현되지 않는다** — 정점 편차 0.7% | 낮음 | **확정** | 원인은 CUDA 생성 경로. `DETERMINISTIC=1` 로 일부 완화. 비교는 해시 대신 정점 수·육안 (rev.4) |

---

## 9. 일정

| Phase | 내용 | 예상 | 누적 |
|---|---|---:|---:|
| 0 | 스캐폴딩 | 0.5h | 0.5h |
| 1 | Python · torch | 1h | 1.5h |
| 2 | CUDA 확장 ⚠️ | 2~4h | 5.5h |
| 3 | CLI 첫 추론 | 1~2h | 7.5h |
| 4 | 웹 백엔드 | 3~4h | 11.5h |
| 5 | 웹 프론트엔드 (MVP) | 3~4h | 15.5h |
| 6 | 운영 · 문서 | 2~3h | 19h |

- **Track A 성공 시**: 총 2~3일
- **Track B 전환 시**: Phase 1~2를 WSL에서 재수행, +1일

**값싼 중간 검증**: Phase 3 직후 W1(upstream `app.py`를 `share=False`로 실행)을 한 번 띄워본다.
nvdiffrast가 없으면 렌더 프리뷰 단계에서 실패하겠지만, 그 전까지의 웹 경로는 30분이면 확인된다.

---

## 10. 열린 결정 사항

| # | 결정 필요 | 기본안 |
|---|---|---|
| D1 | 설치 트랙 — A(Windows 네이티브) vs B(WSL2) | **A로 시작, G2에서 재평가** |
| D2 | 웹 구성 — W1 / W2 / W3 | **W3 하이브리드** |
| D3 | `paper` 브랜치 결과 재현 필요 여부 | **불필요** — `main`만 |
| D4 | 최종 용도 — 연구 테스트 / 파이프라인 통합 / 상용 | **연구·테스트 전제.** 상용이면 R7 선행 검토 |
| D5 | **접근 범위** — 이 PC만 / LAN 공유 / 외부 공유 | **이 PC만(127.0.0.1).** LAN 이상이면 인증 설계 추가 |
| D6 | **서버사이드 렌더 프리뷰** 필요 여부 (HDRI 조명 비교 등) | **불필요** — 브라우저 뷰어로 대체, nvdiffrast 미설치 |

D6을 "필요"로 바꾸면 nvdiffrast·nvdiffrec 빌드가 Phase 2에 추가되고,
Windows에서는 이 빌드가 Track A의 난이도를 크게 올린다.

---

## 11. 다음 액션

승인 시 **Phase 0 + Phase 1**부터 착수:
1. 프로젝트 스캐폴딩 생성 — `server/`, `web/` 포함
2. `upstream/Pixal3D` 클론 (`main`)
3. `setup/doctor.py` 작성 — 환경 진단이 이후 모든 단계의 게이트 역할
4. Python 3.12.10 설치 안내 → venv 구성

> Python 3.12.10 설치는 시스템 변경이므로 해당 시점에 별도 안내·승인.
> 웹 서버를 LAN에 여는 경우(D5)에도 Windows 방화벽 규칙 추가 전에 확인.
