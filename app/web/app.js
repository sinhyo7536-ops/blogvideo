"use strict";

const $ = (id) => document.getElementById(id);
const VIEWS = ["home", "edit", "progress", "result", "error", "settings"];
const MODE_KEY = "blogvideo.mode";
const MODE_HELP = {
  api: "Claude API로 자연스러운 대본을 자동으로 써요. 영상 1개당 약 100~200원이 들어요.",
  auto: "블로그 문장을 프로그램이 장면으로 나눠요. 무료예요. 다음 화면에서 자막을 다듬어 주세요.",
};
const STAGE_LABEL = {
  queued: "순서를 기다리는 중",
  scraping: "글 읽는 중",
  scripting: "대본 쓰는 중",
  tts: "목소리 만드는 중",
  rendering: "영상 만드는 중",
};
const STORE_KEY = "blogvideo.jobId";

const state = { info: null, job: null, script: null, options: null, music: [], pollTimer: null, pickerScene: -1 };

// ---------- 공통 ----------
async function api(method, path, body) {
  const init = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new Error("PC의 프로그램과 연결이 끊겼어요. PC에서 프로그램이 켜져 있는지 확인해 주세요.");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* 빈 응답 */ }
  if (!res.ok) {
    const err = new Error((data && data.message) || "알 수 없는 오류가 났어요. 다시 시도해 주세요.");
    err.status = res.status;
    throw err;
  }
  return data;
}

function show(view) {
  state.view = view;
  VIEWS.forEach((v) => $("view-" + v).classList.toggle("hidden", v !== view));
  window.scrollTo(0, 0);
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2200);
}

function setError(id, msg) {
  const el = $(id);
  el.textContent = msg || "";
  el.classList.toggle("hidden", !msg);
}

function segValue(name, scope) {
  const seg = (scope || document).querySelector(`.seg[data-name="${name}"] .on`);
  return seg ? seg.dataset.value : null;
}

function setSeg(name, value) {
  document.querySelectorAll(`.seg[data-name="${name}"]`).forEach((seg) => {
    seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.value === value));
  });
}

document.querySelectorAll(".seg").forEach((seg) => {
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    setSeg(seg.dataset.name, b.dataset.value);
    if (seg.dataset.name === "mode") setMode(b.dataset.value);
  });
});

function setMode(mode) {
  setSeg("mode", mode);
  localStorage.setItem(MODE_KEY, mode);
  $("mode-help").textContent = MODE_HELP[mode] || "";
}

// ---------- 초기화 ----------
async function loadInfo() {
  state.info = await api("GET", "/api/info");
  $("open-settings").classList.toggle("hidden", !state.info.is_local);
  const notice = $("key-notice");
  setMode(localStorage.getItem(MODE_KEY) || (state.info.has_api_key ? "api" : "auto"));
  if (!state.info.has_api_key) {
    notice.textContent = "API 키가 없어도 무료 방법(AI 없이)으로 쓸 수 있어요. 유료 'AI 자동'은 설정에서 키를 넣어야 해요.";
    if (state.info.is_local) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = "설정 열기";
      b.onclick = openSettings;
      notice.appendChild(b);
    }
    notice.classList.remove("hidden");
  } else {
    notice.classList.add("hidden");
  }
}

async function loadMusic() {
  state.music = await api("GET", "/api/music");
  for (const id of ["bgm", "bgm-edit"]) {
    const sel = $(id);
    const keep = sel.value;
    sel.innerHTML = "";
    const none = new Option("배경음악 없음", "");
    sel.appendChild(none);
    state.music.forEach((m) => sel.appendChild(new Option(m.name.replace(/\.mp3$/i, "").replace(/_/g, " "), m.name)));
    sel.value = keep;
  }
}

function currentOptions() {
  return { aspect: segValue("aspect") || "16:9", voice: segValue("voice") || "female", bgm: $("bgm").value || null };
}

// ---------- 작업 흐름 ----------
function routeJob(job) {
  state.job = job;
  localStorage.setItem(STORE_KEY, job.id);
  stopPoll();
  if (job.stage === "ready") return openEdit(job);
  if (job.stage === "done") return openResult(job);
  if (job.stage === "error") return openError(job);
  openProgress(job);
  state.pollTimer = setTimeout(poll, 1000);
}

async function poll() {
  try {
    routeJob(await api("GET", `/api/jobs/${state.job.id}`));
  } catch (e) {
    if (e.status === 404) return goHome(true);
    state.pollTimer = setTimeout(poll, 2000);
  }
}

function stopPoll() {
  clearTimeout(state.pollTimer);
  state.pollTimer = null;
}

function openProgress(job) {
  $("progress-title").textContent = STAGE_LABEL[job.stage] || "만드는 중";
  const pct = Math.round((job.progress || 0) * 100);
  $("bar-fill").style.width = pct + "%";
  $("percent").textContent = pct + "%";
  document.querySelector(".bar").setAttribute("aria-valuenow", String(pct));
  show("progress");
}

async function startScript() {
  setError("home-error", "");
  const url = $("url").value.trim();
  if (!url) return setError("home-error", "네이버 블로그 글 주소를 넣어 주세요.");
  const btn = $("btn-script");
  btn.disabled = true;
  try {
    routeJob(await api("POST", "/api/jobs", { url, options: currentOptions(), mode: segValue("mode") || "auto" }));
  } catch (e) {
    setError("home-error", e.message);
  } finally {
    btn.disabled = false;
  }
}

async function useFreeMode(mode) {
  try {
    routeJob(await api("POST", `/api/jobs/${state.job.id}/free-mode`, { mode }));
  } catch (e) {
    $("error-message").textContent = e.message;
  }
}

// ---------- 장면 편집 ----------
function openEdit(job) {
  state.script = JSON.parse(JSON.stringify(job.script));
  state.options = Object.assign({}, job.options);
  setSeg("aspect", state.options.aspect);
  setSeg("voice", state.options.voice);
  $("bgm-edit").value = state.options.bgm || "";
  $("yt-title").value = state.script.yt_title;
  $("yt-desc").value = state.script.yt_description;
  $("yt-tags").value = state.script.yt_tags.join(", ");
  updateTitleCount();
  renderScenes();
  setError("edit-error", "");
  show("edit");
}

function imageUrl(i) {
  return `/api/jobs/${state.job.id}/image/${i}`;
}

function imageCount() {
  return (state.job && state.job.post && state.job.post.images.length) || 0;
}

function renderScenes() {
  const list = $("scenes");
  list.innerHTML = "";
  const n = state.script.scenes.length;
  state.script.scenes.forEach((scene, i) => {
    const li = document.createElement("li");
    li.className = "scene-card";

    const head = document.createElement("div");
    head.className = "scene-head";
    const num = document.createElement("span");
    num.className = "scene-num";
    num.textContent = `장면 ${i + 1}`;
    const tools = document.createElement("div");
    tools.className = "scene-tools";
    const mk = (label, title, fn, disabled) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.title = title;
      b.setAttribute("aria-label", title);
      b.disabled = !!disabled;
      b.onclick = fn;
      tools.appendChild(b);
    };
    mk("↑", "위로", () => moveScene(i, -1), i === 0);
    mk("↓", "아래로", () => moveScene(i, 1), i === n - 1);
    mk("✕", "삭제", () => deleteScene(i), n <= 1);
    head.append(num, tools);

    const thumb = document.createElement("button");
    thumb.type = "button";
    thumb.className = "thumb";
    if (scene.image_index !== null && scene.image_index !== undefined && scene.image_index < imageCount()) {
      thumb.classList.add("has-img");
      thumb.style.backgroundImage = `url("${imageUrl(scene.image_index)}")`;
      thumb.textContent = "사진 바꾸기";
    } else {
      thumb.textContent = "사진 없음 · 눌러서 고르기";
    }
    thumb.onclick = () => openPicker(i);

    const ta = document.createElement("textarea");
    ta.value = scene.text;
    ta.setAttribute("aria-label", `장면 ${i + 1} 자막`);
    const count = document.createElement("small");
    count.className = "count";
    const upd = () => {
      scene.text = ta.value;
      count.textContent = `${ta.value.length}자` + (ta.value.length > 40 ? " · 조금 길어요" : "");
      count.classList.toggle("over", ta.value.length > 40);
    };
    ta.addEventListener("input", upd);
    upd();

    li.append(head, thumb, ta, count);
    list.appendChild(li);
  });
}

function moveScene(i, d) {
  const s = state.script.scenes;
  [s[i], s[i + d]] = [s[i + d], s[i]];
  renderScenes();
}

function deleteScene(i) {
  if (!confirm(`장면 ${i + 1}을 지울까요?`)) return;
  state.script.scenes.splice(i, 1);
  renderScenes();
}

function addScene() {
  state.script.scenes.push({ text: "", image_index: imageCount() ? 0 : null });
  renderScenes();
  const tas = document.querySelectorAll(".scene-card textarea");
  tas[tas.length - 1].focus();
}

function openPicker(sceneIdx) {
  state.pickerScene = sceneIdx;
  const grid = $("picker-grid");
  grid.innerHTML = "";
  const none = document.createElement("button");
  none.type = "button";
  none.className = "none";
  none.textContent = "사진 없음";
  none.onclick = () => pick(null);
  grid.appendChild(none);
  for (let i = 0; i < imageCount(); i++) {
    const b = document.createElement("button");
    b.type = "button";
    b.style.backgroundImage = `url("${imageUrl(i)}")`;
    b.setAttribute("aria-label", `사진 ${i + 1}`);
    b.onclick = () => pick(i);
    grid.appendChild(b);
  }
  $("picker").showModal();
}

function pick(idx) {
  state.script.scenes[state.pickerScene].image_index = idx;
  $("picker").close();
  renderScenes();
}

function updateTitleCount() {
  const n = $("yt-title").value.length;
  const c = $("title-count");
  c.textContent = `${n}/60자`;
  c.classList.toggle("over", n > 60);
}

function collectScript() {
  const scenes = state.script.scenes
    .map((s) => ({ text: s.text.trim(), image_index: s.image_index }))
    .filter((s) => s.text);
  return {
    scenes,
    yt_title: $("yt-title").value.trim(),
    yt_description: $("yt-desc").value.trim(),
    yt_tags: $("yt-tags").value.split(",").map((t) => t.trim().replace(/^#/, "")).filter(Boolean),
  };
}

async function startRender() {
  setError("edit-error", "");
  const script = collectScript();
  if (!script.scenes.length) return setError("edit-error", "자막이 있는 장면이 하나 이상 있어야 해요.");
  const options = { aspect: segValue("aspect"), voice: segValue("voice"), bgm: $("bgm-edit").value || null };
  const btn = $("btn-render");
  btn.disabled = true;
  try {
    await api("PUT", `/api/jobs/${state.job.id}/script`, { script, options });
    routeJob(await api("POST", `/api/jobs/${state.job.id}/render`));
  } catch (e) {
    setError("edit-error", e.message);
  } finally {
    btn.disabled = false;
  }
}

// ---------- 결과 ----------
function openResult(job) {
  const v = `/api/jobs/${job.id}/video?v=${job.finished_at || job.created_at}#t=0.6`;
  const video = $("video");
  if (video.dataset.src !== v) {
    video.src = v;
    video.dataset.src = v;
  }
  $("btn-download").href = `/api/jobs/${job.id}/video?download=1`;
  $("out-title").textContent = job.script.yt_title;
  $("out-desc").textContent = job.script.yt_description;
  $("out-tags").textContent = job.script.yt_tags.join(", ");
  show("result");
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
    ta.remove();
    return ok;
  }
}

document.querySelectorAll("[data-copy]").forEach((b) => {
  b.addEventListener("click", async () => {
    const ok = await copyText($(b.dataset.copy).textContent);
    toast(ok ? "복사했어요" : "복사하지 못했어요. 글자를 길게 눌러 직접 복사해 주세요.");
  });
});

// ---------- 오류 ----------
function openError(job) {
  $("error-message").textContent = job.message || "알 수 없는 오류가 났어요. 다시 시도해 주세요.";
  const canFree = !!job.post && job.failed_phase === "prepare" && job.mode === "api";
  $("free-options").classList.toggle("hidden", !canFree);
  show("error");
}

async function retry() {
  try {
    routeJob(await api("POST", `/api/jobs/${state.job.id}/retry`));
  } catch (e) {
    $("error-message").textContent = e.message;
  }
}

// ---------- 설정 ----------
async function openSettings() {
  stopPoll();
  state.returnTo = state.view;
  setError("settings-error", "");
  $("settings-ok").classList.add("hidden");
  $("music-ok").classList.add("hidden");
  try {
    const s = await api("GET", "/api/settings");
    $("api-key-current").textContent = s.anthropic_api_key ? `지금 저장된 키: ${s.anthropic_api_key}` : "아직 저장된 키가 없어요.";
  } catch (e) {
    setError("settings-error", e.message);
  }
  $("api-key").value = "";
  show("settings");
}

async function saveKey() {
  setError("settings-error", "");
  const key = $("api-key").value.trim();
  if (!key) return setError("settings-error", "API 키를 입력해 주세요.");
  try {
    const s = await api("PUT", "/api/settings", { anthropic_api_key: key });
    $("api-key").value = "";
    $("api-key-current").textContent = `지금 저장된 키: ${s.anthropic_api_key}`;
    $("settings-ok").classList.remove("hidden");
    await loadInfo();
  } catch (e) {
    setError("settings-error", e.message);
  }
}

async function uploadMusic() {
  const f = $("music-file").files[0];
  if (!f) return;
  setError("settings-error", "");
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await api("POST", "/api/music", fd);
    const ok = $("music-ok");
    ok.textContent = `추가했어요: ${r.name}`;
    ok.classList.remove("hidden");
    $("music-file").value = "";
    await loadMusic();
  } catch (e) {
    setError("settings-error", e.message);
  }
}

async function makeKeyedInstaller() {
  setError("keyed-error", "");
  $("keyed-ok").classList.add("hidden");
  const f = $("setup-file").files[0];
  if (!f) return setError("keyed-error", "먼저 BlogVideo-Setup.exe 파일을 골라 주세요.");
  const btn = $("btn-keyed");
  btn.disabled = true;
  btn.textContent = "만드는 중…";
  try {
    const fd = new FormData();
    fd.append("file", f);
    const res = await fetch("/api/keyed-installer", { method: "POST", body: fd });
    if (!res.ok) {
      let msg = "설치 파일을 만들지 못했어요.";
      try { msg = (await res.json()).message || msg; } catch (e) { /* 무시 */ }
      throw new Error(msg);
    }
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename\*=UTF-8''([^;]+)/);
    const name = m ? decodeURIComponent(m[1]) : "블로그영상변환기_설치_키포함.exe";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    const ok = $("keyed-ok");
    ok.textContent = `만들었어요: ${name} (다운로드 폴더를 확인하세요)`;
    ok.classList.remove("hidden");
  } catch (e) {
    setError("keyed-error", e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "키 넣은 설치 파일 만들기";
  }
}

function leaveSettings() {
  // 편집 중이던 대본은 서버 값으로 덮어쓰지 않고 그대로 돌아간다
  if (state.returnTo === "edit" && state.script) return show("edit");
  if (state.returnTo === "home" || !state.job) return show("home");
  routeJob(state.job);
}

// ---------- 이동 ----------
function goHome(clear) {
  stopPoll();
  if (clear) {
    localStorage.removeItem(STORE_KEY);
    state.job = null;
    $("url").value = "";
  }
  setError("home-error", "");
  show("home");
}

$("btn-script").onclick = startScript;
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") startScript(); });
$("btn-render").onclick = startRender;
$("btn-add-scene").onclick = addScene;
$("yt-title").addEventListener("input", updateTitleCount);
$("btn-reedit").onclick = () => openEdit(state.job);
$("btn-new").onclick = () => goHome(true);
$("btn-retry").onclick = retry;
$("btn-error-home").onclick = () => goHome(true);
$("go-home").onclick = () => {
  const busy = state.job && !["ready", "done", "error"].includes(state.job.stage);
  if (busy) return routeJob(state.job); // 만드는 중이면 진행 화면으로
  if (state.view === "edit" && !confirm("지금 대본을 그만두고 처음 화면으로 갈까요?")) return;
  goHome(true);
};
$("open-settings").onclick = openSettings;
$("btn-save-key").onclick = saveKey;
$("music-file").addEventListener("change", uploadMusic);
$("btn-settings-back").onclick = leaveSettings;
$("btn-keyed").onclick = makeKeyedInstaller;
$("btn-free-auto").onclick = () => useFreeMode("auto");
$("picker-close").onclick = () => $("picker").close();

(async function init() {
  try {
    await Promise.all([loadInfo(), loadMusic()]);
  } catch (e) {
    setError("home-error", e.message);
  }
  const saved = localStorage.getItem(STORE_KEY);
  if (saved) {
    try {
      state.job = { id: saved };
      return routeJob(await api("GET", `/api/jobs/${saved}`));
    } catch (e) {
      localStorage.removeItem(STORE_KEY);
      state.job = null;
    }
  }
  show("home");
})();
