/* Pixal3D 로컬 테스트 UI
 *
 * 흐름은 서버의 3단계와 같다.
 *   업로드 → /api/preprocess → 결과 확인 → /api/generate → 폴링 → 뷰어
 *
 * 전처리와 생성을 나눈 이유는, 배경 제거가 잘못됐을 때 사용자가 거기서
 * 멈출 수 있어야 하기 때문이다. 전처리가 틀리면 생성은 반드시 실패한다.
 */

const $ = (id) => document.getElementById(id);

const state = {
  runId: null,
  jobId: null,
  polling: null,
  pipelineReady: false,
};

// ── 유틸 ────────────────────────────────────────────────────────────

let toastTimer = null;
function toast(message, ms = 6000) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch { /* JSON 이 아니면 상태 코드를 그대로 쓴다 */ }
    throw new Error(detail);
  }
  return res.json();
}

const fmtSeconds = (s) =>
  s >= 60 ? `${Math.floor(s / 60)}분 ${Math.round(s % 60)}초` : `${Math.round(s)}초`;

// ── 서버 상태 ───────────────────────────────────────────────────────

async function pollStatus() {
  const box = $("status");
  const text = $("status-text");
  try {
    const s = await api("/api/status");
    state.pipelineReady = s.pipeline_loaded;

    if (s.pipeline_error) {
      box.className = "status error";
      text.textContent = "모델 로딩 실패";
      toast(`모델을 불러오지 못했습니다: ${s.pipeline_error}`, 20000);
    } else if (!s.pipeline_loaded) {
      box.className = "status loading";
      text.textContent = "모델 불러오는 중 (수 분 소요)";
    } else if (s.queue.running) {
      box.className = "status busy";
      text.textContent = `생성 중 · 대기 ${s.queue.queued}`;
    } else {
      box.className = "status ready";
      text.textContent = `준비 완료 · 로딩 ${s.load_seconds}초`;
    }

    // 기본값은 서버가 정한다. 사용자가 이미 만졌으면 건드리지 않는다.
    const res = $("resolution");
    if (!res.dataset.touched) res.value = String(s.defaults.resolution);

    updateGenerateButton();
  } catch {
    box.className = "status error";
    text.textContent = "서버에 연결할 수 없음";
  }
}

function updateGenerateButton() {
  $("generate").disabled = !(state.pipelineReady && state.runId);
}

// ── ① 업로드 · 전처리 ───────────────────────────────────────────────

const drop = $("drop");
const fileInput = $("file");

drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
});
["dragenter", "dragover"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));

drop.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
});

async function handleFile(file) {
  if (!state.pipelineReady) {
    toast("모델을 아직 불러오는 중입니다. 잠시 후 다시 시도하세요.");
    return;
  }

  // 미리보기를 먼저 보여준다 — 업로드가 몇 초 걸려도 반응이 있다.
  const src = $("preview-src");
  src.src = URL.createObjectURL(file);
  src.hidden = false;
  $("drop-inner").hidden = true;

  $("preprocess-result").hidden = true;
  state.runId = null;
  updateGenerateButton();

  const form = new FormData();
  form.append("file", file);

  try {
    toast("배경 제거 중…", 30000);
    const result = await api("/api/preprocess", { method: "POST", body: form });
    state.runId = result.run_id;

    $("preview-processed").src = result.processed_url + "?t=" + Date.now();
    $("preprocess-result").hidden = false;
    $("toast").hidden = true;
    updateGenerateButton();
  } catch (e) {
    toast(`전처리 실패: ${e.message}`, 12000);
  }
}

// ── ② 생성 ──────────────────────────────────────────────────────────

$("resolution").addEventListener("change", (e) => { e.target.dataset.touched = "1"; });

$("generate").addEventListener("click", async () => {
  if (!state.runId) return;

  const form = new FormData();
  form.append("run_id", state.runId);
  form.append("seed", $("seed").value);
  form.append("resolution", $("resolution").value);
  form.append("steps", $("steps").value);
  form.append("fov", $("fov").value);

  $("generate").disabled = true;
  $("panel-result").hidden = true;
  $("panel-progress").hidden = false;
  $("stage").textContent = "대기열에 넣는 중";
  $("bar-fill").style.width = "0%";

  try {
    const { job_id } = await api("/api/generate", { method: "POST", body: form });
    state.jobId = job_id;
    startPolling();
  } catch (e) {
    $("panel-progress").hidden = true;
    toast(`생성 요청 실패: ${e.message}`, 12000);
    updateGenerateButton();
  }
});

function startPolling() {
  clearInterval(state.polling);
  state.polling = setInterval(tickProgress, 500);
}

async function tickProgress() {
  if (!state.jobId) return;

  let info;
  try {
    info = await api(`/api/progress?job_id=${encodeURIComponent(state.jobId)}`);
  } catch {
    return;  // 일시적인 실패는 다음 틱에서 회복된다
  }

  const bar = document.querySelector(".bar");
  const p = info.progress;

  if (info.status === "queued") {
    $("stage").textContent = `대기 중 — 앞에 ${info.position}건`;
    bar.classList.add("indeterminate");
  } else if (info.status === "running") {
    $("stage").textContent = p ? p.stage : "생성 중";
    if (p && p.total > 0) {
      bar.classList.remove("indeterminate");
      $("bar-fill").style.width = `${(p.fraction * 100).toFixed(1)}%`;
      $("progress-meta").textContent = `${p.step} / ${p.total}`;
    } else {
      // 스텝을 알 수 없는 구간(모델 로딩, 카메라 추정 등)
      bar.classList.add("indeterminate");
      $("progress-meta").textContent = "";
    }
  } else {
    clearInterval(state.polling);
    bar.classList.remove("indeterminate");
    $("panel-progress").hidden = true;
    updateGenerateButton();

    if (info.status === "done") {
      await showResult(state.runId);
      loadHistory();
    } else {
      toast(`생성 실패: ${info.error || info.status}`, 20000);
      loadHistory();
    }
  }
}

// ── ③ 결과 ──────────────────────────────────────────────────────────

async function showResult(runId) {
  const runs = await api("/api/runs?limit=60");
  const meta = runs.find((r) => r.run_id === runId);
  if (!meta) return;

  // 뷰어에는 경량본을 올린다. 원본은 수백 MB 라 브라우저가 버겁다.
  $("viewer").src = `/runs/${runId}/web.glb?t=${Date.now()}`;
  $("download-full").href = `/runs/${runId}/full.glb`;
  $("download-full").download = `${runId}-full.glb`;
  $("download-web").href = `/runs/${runId}/web.glb`;
  $("download-web").download = `${runId}-web.glb`;

  $("result-meta").innerHTML = [
    `<span><b>소요</b> ${fmtSeconds(meta.seconds)}</span>`,
    `<span><b>최대 VRAM</b> ${meta.peak_vram_gb}GB</span>`,
    `<span><b>해상도</b> ${meta.resolution}</span>`,
    `<span><b>시드</b> ${meta.seed}</span>`,
    `<span><b>원본</b> ${meta.full_glb_mb}MB</span>`,
    `<span><b>경량</b> ${meta.web_glb_mb}MB</span>`,
  ].join("");

  $("panel-result").hidden = false;
  $("panel-result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ── ④ 이력 ──────────────────────────────────────────────────────────

async function loadHistory() {
  let runs;
  try {
    runs = await api("/api/runs?limit=60");
  } catch {
    return;
  }

  const gallery = $("gallery");
  if (!runs.length) {
    gallery.innerHTML = '<p class="empty">아직 생성한 결과가 없습니다.</p>';
    return;
  }

  gallery.innerHTML = "";
  for (const r of runs) {
    const card = document.createElement("button");
    card.className = "card";
    card.type = "button";

    const label = { done: "완료", failed: "실패", running: "생성 중", queued: "대기" }[r.status] || r.status;

    card.innerHTML = `
      <img src="/runs/${r.run_id}/processed.png" alt="" loading="lazy"
           onerror="this.style.visibility='hidden'">
      <div class="body">
        <div class="name">${r.source_name || r.run_id}</div>
        <div class="facts">${r.resolution} · seed ${r.seed}${
          r.seconds ? ` · ${fmtSeconds(r.seconds)}` : ""
        }</div>
        <span class="badge ${r.status}">${label}</span>
      </div>`;

    if (r.status === "done") {
      card.addEventListener("click", () => showResult(r.run_id));
    } else if (r.status === "failed") {
      card.addEventListener("click", () => toast(r.error || "실패 사유가 기록되지 않았습니다.", 15000));
    }
    gallery.appendChild(card);
  }
}

// ── 시작 ────────────────────────────────────────────────────────────

pollStatus();
setInterval(pollStatus, 3000);
loadHistory();
