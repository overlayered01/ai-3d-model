# 벤치마크

RTX 3090 24GB · Windows 11 · Python 3.12.10 · torch 2.10.0+cu130 · upstream `cdbb2bb`

측정값은 이 워크스테이션 기준이다. 데스크톱이 GPU 를 2~3GB 점유한 상태에서 잰 것이라
GPU 를 비우면 조금 더 나온다.

---

## 실측 (해상도 1024 · low_vram · 12 steps · seed 42)

| 실행 | 경로 | 소요 | 최대 VRAM | 원본 GLB | 경량 GLB | 정점 | 삼각형 |
|---|---|---:|---:|---:|---:|---:|---:|
| `0_img.png` | CLI | 253.2s | — | 36.5MB | — | 685,074 | 972,626 |
| `0_img.png` | 웹 | 179.5s | 14.23GB | 36.4MB | 8.0MB | — | — |
| `1_img.png` | 웹 | 178.5s | 14.22GB | 41.0MB | 10.0MB | 792,017 | 927,087 |

### CLI 가 웹보다 74초 느린 이유

모델 로딩 때문이다. CLI 는 매 실행마다 22GB 체크포인트를 다시 읽지만,
웹 서버는 프로세스에 상주시킨다. 순수 생성 시간은 양쪽이 같다.

이것이 계획서가 "요청마다 `inference.py` 를 새로 띄우지 않는다"를
아키텍처 제약으로 못 박은 이유다.

| 구간 | 시간 |
|---|---:|
| 모델 로딩 (서버 기동 시 1회) | 90.5s |
| 전처리 (배경 제거) | 0.0~0.6s |
| 카메라 추정 (MoGe) | ~7s |
| 3단 캐스케이드 생성 | ~150s |
| GLB 추출 2벌 | ~20s |

---

## VRAM

| 항목 | 값 |
|---|---:|
| 총 VRAM | 24.0GB |
| 데스크톱 점유 (Edge, Slack 등) | 2.7~3.4GB |
| 생성 중 최대 사용 | **14.2GB** |
| 여유 | 약 7GB |

1024 + `low_vram` 은 여유가 넉넉하다. 1536 은 아직 측정하지 않았다.

`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` 는 **Windows 에서 무시된다.**
파편화 완화 수단이 하나 없는 상태이므로 1536 은 더 보수적으로 봐야 한다. (리스크 R4)

---

## GLB 2벌 추출 (리스크 R9)

| | 텍스처 | 폴리곤 목표 | 실제 크기 |
|---|---:|---:|---:|
| `full.glb` 저장용 | 4096px | 1,000,000 | 36~41MB |
| `web.glb` 뷰어용 | 2048px | 200,000 | 8~10MB |

**4~4.5배 축소.** 브라우저 뷰어에는 경량본을 올리고 원본은 내려받기로만 제공한다.
잠재 캐시가 있어 추출을 두 번 하는 비용은 생성 대비 미미하다(약 20초).

---

## 측정 방법

```powershell
# CLI 단독
powershell -File setup\06_smoke_inference.ps1 -Resolution 1024

# 웹 서버 종단 (모델 상주 상태)
powershell -File setup\run_server.ps1        # 별도 창
python tools\e2e_test.py --image inputs\samples\1_img.png
```

`peak_vram_gb` 는 `torch.cuda.max_memory_allocated()` 로 생성 구간만 잰다.
`vertices` / `triangles` 는 최종 GLB 의 accessor count 를 직접 읽는다 —
파이프라인이 보고한 값이 아니라 파일에서 센다.

각 실행의 `runs/<id>/meta.json` 에 같은 값이 남는다.

---

## 아직 측정하지 않은 것

- 해상도 1536 (표준 · low_vram 양쪽)
- 샘플링 스텝 변화에 따른 품질/시간 곡선
- 동일 seed 재실행의 바이트 단위 재현성 (S6)
- 배경 제거 모델 차이 (`ZhengPeng7/BiRefNet` vs `briaai/RMBG-2.0`)
