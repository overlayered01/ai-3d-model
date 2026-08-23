# 테스트 입력 이미지

```
inputs/           개인 이미지를 두는 곳. git 이 추적하지 않는다.
inputs/samples/   upstream 공식 샘플. 가벼운 5장만 커밋된다.
```

## 샘플

`upstream/Pixal3D/assets/images/` 에서 복사한 공식 샘플 13장이 `samples/` 에 있다.
그중 1MB 미만 5장(`1_img` `4_img` `9_img` `21_img` `s_18_img`)만 저장소에 포함한다.

나머지 8장은 4096px 원본이라 합쳐서 59MB 다. upstream 클론에 이미 들어 있으므로
중복 저장하지 않는다 — 저장소를 새로 받은 경우 `setup/04_pixal3d.ps1` 을 돌리면
`upstream/Pixal3D/assets/images/` 에 그대로 생긴다.

## 좋은 입력 조건

- 배경이 단순하고 피사체가 화면 중앙에 있을수록 결과가 좋다
- 알파 채널이 있는 PNG 를 넣어도 배경 제거는 그대로 수행된다
- 파이프라인이 내부적으로 512px 로 줄여 조건 인코딩에 쓰므로,
  입력이 4096px 든 1024px 든 결과 차이는 크지 않다
