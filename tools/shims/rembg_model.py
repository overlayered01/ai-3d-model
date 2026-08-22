"""배경 제거 모델 교체 셰임.

문제:
    모델 저장소의 `pipeline.json` 이 배경 제거 모델을 이렇게 지정한다.

        {"name": "BiRefNet", "args": {"model_name": "briaai/RMBG-2.0"}}

    `briaai/RMBG-2.0` 은 HuggingFace 의 게이트된 저장소다. 계정을 만들고
    라이선스에 동의한 뒤 토큰으로 인증해야 내려받을 수 있다.

    그런데 `Pixal3DImageTo3DPipeline.from_pretrained()` 는 파이프라인을
    만드는 시점에 이 모델을 무조건 로드한다. 배경이 이미 지워진 알파 PNG 를
    넣어도 우회되지 않는다 — 파이프라인 자체가 생성되지 않는다.

해결:
    `BiRefNet` 클래스의 기본 모델은 게이트가 없는 `ZhengPeng7/BiRefNet` 이다.
    RMBG-2.0 은 애초에 이 BiRefNet 아키텍처를 BRIA 가 파인튜닝한 것이라
    배경 제거 품질은 대체로 동등하다.

    upstream 코드는 건드리지 않는다. `from_pretrained()` 가
    `getattr(rembg, "BiRefNet")` 으로 호출 시점에 이름을 해석하므로,
    모듈 속성만 갈아끼우면 된다.

사용:
    PIXAL3D_REMBG_MODEL 환경변수로 모델을 지정한다.
    설정하지 않으면 아무것도 하지 않는다 — upstream 그대로 RMBG-2.0 을 쓴다.

        $env:PIXAL3D_REMBG_MODEL = 'ZhengPeng7/BiRefNet'

    이 모듈을 import 하는 것만으로 패치가 적용된다.
    반드시 파이프라인을 만들기 전에 import 해야 한다.
"""

from __future__ import annotations

import os

ENV_VAR = "PIXAL3D_REMBG_MODEL"

_applied = False


def apply() -> str | None:
    """패치를 적용한다. 교체한 모델 이름을 돌려주고, 안 했으면 None."""
    global _applied

    override = os.environ.get(ENV_VAR, "").strip()
    if not override or _applied:
        return None

    from pixal3d.pipelines import rembg as rembg_module

    original = rembg_module.BiRefNet

    class _OverriddenBiRefNet(original):  # type: ignore[misc, valid-type]
        """pipeline.json 이 지정한 model_name 을 무시하고 지정된 것을 쓴다."""

        def __init__(self, model_name: str | None = None, **kwargs):
            print(f"[rembg] 배경 제거 모델 교체: {model_name} -> {override}")
            super().__init__(model_name=override, **kwargs)

    _OverriddenBiRefNet.__name__ = original.__name__
    _OverriddenBiRefNet.__qualname__ = original.__qualname__

    # from_pretrained 가 getattr(rembg, "BiRefNet") 으로 찾는 그 이름을 바꾼다.
    rembg_module.BiRefNet = _OverriddenBiRefNet

    _applied = True
    return override


# import 만으로 적용된다.
apply()
