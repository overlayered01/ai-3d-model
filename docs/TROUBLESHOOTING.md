# 트러블슈팅

실제로 겪은 문제와 해결책. 계획서의 리스크 등록부(R1~R12)와 대응된다.

---

## 설치 단계

### PowerShell 스크립트가 `Missing closing '}'` 파서 오류로 죽는다

**원인.** Windows PowerShell 5.1은 BOM 없는 `.ps1` 파일을 ANSI(한국어 환경에서는 cp949)로
읽는다. UTF-8로 저장된 한글 주석의 바이트열이 cp949로 잘못 해석되면서 따옴표나 중괄호로
보이는 문자가 생겨 파서가 깨진다.

**해결.** 모든 `.ps1`을 **UTF-8 with BOM**으로 저장한다.

```python
raw = open(path, 'rb').read()
if not raw.startswith(b'\xef\xbb\xbf'):
    open(path, 'wb').write(b'\xef\xbb\xbf' + raw)
```

PowerShell 7+에서는 발생하지 않지만, 이 프로젝트는 시스템 기본 5.1을 전제로 한다.

---

### `HF_HOME 미설정` 경고가 계속 뜬다

**원인.** `01_python_venv.ps1`이 `[Environment]::SetEnvironmentVariable(..., 'User')`로
영구 등록하지만, **그 변경 이전에 시작된 셸과 그 자식 프로세스는 상속받지 못한다.**

**해결.** `setup/_env.ps1`이 User 수준 값을 현재 세션으로 끌어온다. 나머지 스크립트가
이를 dot-source 한다. 새 터미널을 열면 자연히 해결된다.

```powershell
. "$PSScriptRoot\_env.ps1"
```

`HF_HOME`이 C:를 가리키면 안 된다 — C: 여유가 55GB뿐이라 가중치를 받다가 실패한다. (R5)

---

### `ModuleNotFoundError: No module named 'triton'`

**증상.** `flex_gemm_ap` import 시 발생.

**원인.** CUDA 커널 휠을 `--no-deps`로 설치했기 때문이다. `--no-deps`는 pip이 우리가
맞춰놓은 `torch 2.10.0+cu130`을 PyPI의 CPU 판으로 갈아엎는 것을 막기 위해 반드시
필요하지만, 그 대가로 정상 의존성인 Triton도 함께 빠진다.

**해결.** Triton은 공식 Windows 휠이 없다. `triton-windows`를 쓴다.
휠 제작자도 이를 전제하고 있다 — `flex_gemm_ap`의 메타데이터가
`triton-windows >=3.2.0 ; platform_system == "Windows"`를 선언한다.

**버전은 torch가 정한다.** 추측하지 말고 확인할 것:

```bash
curl -sL https://pypi.org/pypi/torch/2.10.0/json | grep triton
# -> triton==3.6.0; platform_system == "Linux" ...
```

```powershell
.\.venv\Scripts\python.exe -m pip install "triton-windows==3.6.0.post26"
```

설치 후 반드시 torch가 그대로인지 확인한다.

---

### `ModuleNotFoundError: No module named 'tqdm'`

`--no-deps`의 또 다른 여파. `cumesh_vb`와 `o_voxel_vb_ap`도 `tqdm`, `plyfile`,
`trimesh`, `zstandard`, `easydict`를 요구한다. `04_pixal3d.ps1`이 `requirements.txt`를
설치하면 함께 해결되므로, 03 단계의 셰임 실패는 04 이후 재시도하면 된다.

---

### `ResolutionImpossible: trimesh==4.10.1` vs `moge 3.0.0 depends on trimesh>=4.11`

**원인.** upstream `requirements.txt`의 결함이다. `trimesh==4.10.1`로 고정해 놓고
동시에 MoGe를 **버전 없이** git에서 끌어온다. MoGe가 그 사이 3.0.0으로 올라가면서
`trimesh>=4.11`을 요구하게 되어 pip이 해를 찾지 못한다.

**더 위험한 문제가 숨어 있다.** MoGe 3.0.0의 의존성 목록에 이것이 있다:

```
flex-gemm @ git+https://github.com/JeffreyXiang/FlexGEMM.git@b2fadb2
```

의존성을 그대로 따라가면 **방금 설치한 사전빌드 `flex_gemm_ap` 휠을 소스 빌드로
덮어쓰려 시도한다.** Windows에서 이 빌드는 실패하거나, 성공하더라도 맞춰놓은
CUDA 스택을 깨뜨린다.

**해결.** MoGe를 분리한다.

```powershell
# 1. requirements.txt 에서 MoGe 줄만 빼고 설치 (trimesh 4.10.1 유지)
Get-Content requirements.txt | Where-Object { $_ -notmatch 'MoGe' } | Set-Content req-nomoge.txt
pip install -r req-nomoge.txt

# 2. MoGe 는 의존성 없이
pip install --no-deps "git+https://github.com/microsoft/MoGe.git"

# 3. 실제로 필요한 것만 보충 (trimesh/flex-gemm/gradio 제외)
pip install click scipy matplotlib huggingface-hub requests
```

Pixal3D는 MoGe를 카메라 FOV 추정에만 쓰며 `moge.model.v2.MoGeModel` 하나만
import 한다. 이 경로는 trimesh/gradio/flex-gemm을 건드리지 않는다.

`04_pixal3d.ps1`이 설치 전후로 `torch`와 `flex-gemm-ap` 버전을 비교해
갈아엎힘을 감지한다.

---

### 추론이 `NativeCommandError` 로 즉시 죽는다 (실제 오류는 경고 한 줄)

**증상.** PowerShell 스크립트에서 `inference.py` 를 돌리면 다음처럼 죽는다:

```
python.exe : ...sparse_structure_vae.py:103: SyntaxWarning: invalid escape sequence '\m'
    + FullyQualifiedErrorId : NativeCommandError
```

**원인.** PowerShell 5.1 의 함정이다. `$ErrorActionPreference = 'Stop'` 상태에서
네이티브 exe 의 stderr 를 `2>&1` 로 파이프에 받으면, PowerShell 이 stderr 각 줄을
`ErrorRecord` 로 감싼다. `Stop` 이므로 첫 줄에서 스크립트가 중단된다.

**파이썬은 경고와 tqdm 진행률을 모두 stderr 로 내보낸다.** 그래서 upstream 코드의
무해한 `SyntaxWarning` 한 줄만으로도 추론이 시작조차 못 한다.

**해결.** 호출 구간에서만 `Continue` 로 낮추고, 성패는 종료 코드로만 판단한다.

```powershell
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $VenvPy @argsList 2>&1 | Tee-Object -FilePath $logTxt
$rc = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
```

---

### `@jit functions should be defined in a Python file`

Triton 테스트를 `python -c "..."` 인라인으로 돌렸을 때 나온다. `@triton.jit`은
`inspect.getsourcelines()`로 함수 소스를 읽으므로 실제 `.py` 파일에 있어야 한다.
**Triton 문제가 아니라 테스트 방식 문제다.** `setup/smoke_triton.py`를 쓸 것.

---

### `ModuleNotFoundError: No module named 'src.model'` (NAF 로딩 중)

**증상.** 파이프라인 로딩 중 NAF 업샘플러 단계에서 죽는다.

```
File ".../models/torch/hub/valeoai_NAF_main/hubconf.py", line 5, in <module>
    from src.model.naf import NAF
ModuleNotFoundError: No module named 'src.model'
```

**원인.** NAF 는 `torch.hub` 로 `valeoai/NAF` 저장소를 받아와 실행하는데,
그 `hubconf.py` 가 `from src.model.naf import NAF` 를 한다. 프로젝트 루트가
`sys.path` 에 있고 그 아래에 `src/` 폴더가 있으면, 우리 폴더가 NAF 의 `src` 를
가려버린다. 특히 `src/__init__.py` 가 있으면 정규 패키지가 되어 탐색이 거기서
멈춘다.

`src` 는 hubconf 방식 저장소에서 흔한 이름이라 언제든 재발할 수 있다.

**해결.** 두 겹으로 막는다.

1. 우리 폴더 이름을 `src` 가 아니라 `tools` 로 둔다.
2. `tools/run_inference.py` 가 upstream 을 실행하기 직전에 프로젝트 루트를
   `sys.path` 에서 걷어낸다. 우리 모듈은 파일 경로로 직접 불러
   `sys.modules` 에 이름을 남기지 않는다.

이미 잘못된 상태로 받아진 hub 캐시가 있으면 지우고 다시 받는다:

```powershell
Remove-Item -Recurse -Force models	orch\hubaleoai_NAF_main
```

---

### `ImportError: ... requires the following packages that were not found: einops`

배경 제거 모델(BiRefNet / RMBG-2.0)이 `trust_remote_code` 로 받아오는 모델링
파일이 `einops` 를 요구한다. upstream `requirements.txt` 에는 없다.

```powershell
pip install einops
```

---

### 배경 제거 모델이 게이트되어 파이프라인이 만들어지지 않는다

```
OSError: You are trying to access a gated repo ... briaai/RMBG-2.0
```

모델 저장소의 `pipeline.json` 이 `BiRefNet(model_name="briaai/RMBG-2.0")` 을
지정하는데, 이 저장소는 HF 계정으로 라이선스에 동의해야 받을 수 있다.

**알파 PNG 를 넣어도 우회되지 않는다.** `from_pretrained()` 가 파이프라인을
만드는 시점에 무조건 로드하므로, 파이프라인 자체가 생성되지 않는다.

두 가지 길이 있다.

**A. 게이트 없는 모델로 교체 (기본값).** `BiRefNet` 클래스의 기본 모델인
`ZhengPeng7/BiRefNet` 은 게이트가 없다. RMBG-2.0 은 애초에 이 아키텍처를
BRIA 가 파인튜닝한 것이라 품질은 대체로 동등하다. `setup/_env.ps1` 이
`PIXAL3D_REMBG_MODEL` 을 이 값으로 설정하고, `tools/shims/rembg_model.py` 가
upstream 코드를 건드리지 않고 모듈 속성만 갈아끼운다.

**B. RMBG-2.0 을 그대로 쓴다.** 라이선스에 동의하고 인증한다.

```powershell
powershell -File setup\hf_login.ps1
```

`_env.ps1` 의 `PIXAL3D_REMBG_MODEL` 줄을 지우면 upstream 기본값으로 돌아간다.

---

## 실행 단계

### GLB에 텍스처가 안 보인다

upstream은 `glb.export(..., extension_webp=True)`로 내보낸다. 이는 glTF
`EXT_texture_webp` 확장을 쓴다는 뜻이고, 이를 지원하지 않는 뷰어
(**Windows 기본 3D 뷰어** 등)에서는 텍스처가 통째로 빠져 보인다. (R12)

`.env`에서 `EXPORT_WEBP=0`으로 바꾸면 PNG 텍스처로 내보낸다. 용량은 커진다.

three.js / `<model-viewer>`는 이 확장을 지원한다.

---

### `expandable_segments not supported on this platform`

**Windows 에서는 이 설정이 무시된다.** upstream `inference.py` 와 `app.py` 는
`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` 를 설정하지만 Linux 전제이고,
Windows 에서는 PyTorch 가 경고만 내고 무시한다.

VRAM 파편화 완화 수단 하나가 통째로 없다는 뜻이므로, **1536 표준 모드의 OOM 확률이
계획서 초안 예상보다 높다.** 1024 + `--low_vram` 기본값 방침은 그대로 유효하다. (R4)

경고 자체는 무해하므로 무시해도 된다.

---

### huggingface 다운로드가 느리다 / `hf_xet` 경고

```
Xet Storage is enabled for this repo, but the 'hf_xet' package is not installed.
Falling back to regular HTTP download.
```

가중치가 약 25GB 라 체감 차이가 크다. 설치하면 다음 다운로드부터 빨라진다:

```powershell
pip install hf_xet
```

일반 HTTP 로도 40MB/s 이상 나온다면 굳이 재시작할 필요는 없다 — 이어받기되므로
다음 실행에서 자동으로 적용된다.

---

### `cache-system uses symlinks by default ... your machine does not support them`

Windows 에서 개발자 모드가 꺼져 있거나 관리자 권한이 아니면 HF 캐시가 심볼릭 링크
대신 실제 복사본을 쓴다. **동작에는 문제가 없고 디스크만 더 쓴다.** D: 여유가
충분하므로 무시한다. 끄려면 `HF_HUB_DISABLE_SYMLINKS_WARNING=1`.

---

### `AttributeError: module 'o_voxel_vb_ap' has no attribute 'postprocess'`

생성이 4분 넘게 전부 성공한 뒤 **마지막 GLB 내보내기에서만** 죽는다.

**원인.** Windows 사전빌드 휠에 o_voxel 변형이 두 가지 있는데 내용이 다르다.

| 휠 | 포함 | GLB 내보내기 |
|---|---|---|
| `o_voxel_vb_ap` | convert · io · serialize | ❌ postprocess 없음 |
| `o_voxel_vb` | convert · io · serialize · **postprocess** · rasterize | ✅ |

upstream `inference.py` 는 `o_voxel.postprocess.to_glb(...)` 로 GLB 를 만든다.
`_ap` 변형을 쓰면 이 호출에서 죽는다.

**해결.** `o_voxel_vb` 를 쓴다.

```powershell
pip install --no-deps "https://github.com/PozzettiAndrea/cuda-wheels/releases/download/o_voxel_vb-latest/o_voxel_vb-0.0.1%2Bcu130torch2.10-cp312-cp312-win_amd64.whl"
python tools\shims\install_shims.py
```

셰임 설치기는 이제 `o_voxel.postprocess` 접근까지 검사한다. `import o_voxel` 만
확인하면 이 문제를 설치 시점에 놓치고 4분 뒤에야 알게 된다.

---

### `nvdiffrast` 가 필요하다 — 계획서 rev.1/rev.2 판정 정정

계획서는 "`inference.py` 가 최상위에서 `o_voxel` 만 import 하므로 nvdiffrast 는
서버사이드 프리뷰 전용이고 생략 가능"이라고 적었다. **틀렸다.**

`o_voxel/postprocess.py` 가 `import nvdiffrast.torch as dr` 를 하고,
GLB 의 **UV 텍스처 베이킹**에 쓴다:

```python
ctx = dr.RasterizeCudaContext()
rast_chunk, _ = dr.rasterize(...)
pos = dr.interpolate(out_vertices.unsqueeze(0), rast, out_faces)[0][0]
```

텍스처 있는 GLB 를 만들려면 nvdiffrast 가 **필수**다. 다만 컴파일할 필요는 없다 —
사전빌드 휠이 있다.

```powershell
pip install --no-deps "https://github.com/PozzettiAndrea/cuda-wheels/releases/download/nvdiffrast-latest/nvdiffrast-0.4.0%2Bcu130torch2.10-cp312-cp312-win_amd64.whl"
```

"서버 렌더 프리뷰를 쓰지 않는다"(결정 D6)는 여전히 유효하다. 턴테이블 6모드
JPEG 을 굽지 않을 뿐, 렌더러 자체는 내보내기에 쓰인다.

---

### 웹 서버에서만 배경 제거 모델 교체가 안 먹는다

셰임은 **두 경로 모두에** 걸어야 한다.

| 경로 | 적용 위치 |
|---|---|
| CLI | `tools/run_inference.py` |
| 웹 서버 | `server/pipeline.py` 의 `_apply_rembg_shim()` |

한쪽만 걸면 CLI 는 되는데 서버는 게이트 오류로 죽는다. 환경변수가 설정돼 있어도
패치를 적용하는 코드가 없으면 소용없다.

`tools/e2e_test.py` 가 이 종류의 누락을 잡는다.

---

### CUDA OOM

24GB 중 데스크톱이 3.4GB 정도를 점유한다. upstream 기준선은 A100/H100이다. (R4)

1. `--low_vram --resolution 1024`로 시작한다 (기본값)
2. 브라우저·Slack 등 GPU를 쓰는 앱을 닫으면 2~3GB를 회수할 수 있다
3. 1536 표준 모드는 best-effort로 취급한다

`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`는 **Windows에서 동작하지 않는다**
(위 항목 참고). 파편화 완화 수단이 하나 없는 상태이므로 1·2번이 그만큼 더 중요하다.

**웹 서버는 GPU를 상시 점유한다.** 서버를 띄운 채 다른 GPU 작업을 병행할 수 없다.

---

### 웹 서버가 여러 번 모델을 로드하며 OOM

`uvicorn --workers 1` 이 필수다. 워커가 여럿이면 각각 파이프라인을 로드한다.

---

### upstream `app.py`를 그대로 실행하지 말 것

마지막 줄이 `app.launch(show_error=True, share=True)`다. **`share=True`는 인증 없는
공개 URL을 인터넷에 띄운다** — URL을 아는 누구나 이 워크스테이션의 GPU로 작업을
돌릴 수 있다. (R8)

또한 `app.py`의 `generate_3d()`는 서버사이드 턴테이블 렌더를 호출하므로
**nvdiffrast를 요구한다.** 이 프로젝트는 nvdiffrast를 설치하지 않으므로
그 지점에서 실패한다. 브라우저 3D 뷰어가 그 역할을 대신한다. (결정 D6)

---

## 진단

무엇이 깨졌는지 모를 때는 항상 여기부터:

```powershell
.\.venv\Scripts\python.exe setup\doctor.py          # 전체
.\.venv\Scripts\python.exe setup\doctor.py --gate g1 # Python/torch/GPU
.\.venv\Scripts\python.exe setup\smoke_triton.py     # Triton JIT
.\.venv\Scripts\python.exe tools\shims\install_shims.py --check
```
