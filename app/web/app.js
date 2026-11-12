"use strict";

const $ = (id) => document.getElementById(id);
const VIEWS = ["home", "source", "edit", "progress", "result", "error", "settings"];
const MODE_KEY = "blogvideo.mode";
const MODE_HELP = {
  api: "Claude가 글과 사진을 직접 보고 대본을 써요. 영상 1개당 약 100~300원이 들어요.",
  auto: "AI 없이 블로그 문장을 순서대로 장면으로 나눠요(사진은 보지 않아요). 무료예요. 다음 화면에서 자막을 다듬어 주세요.",
  gemini: "Google Gemini가 글과 사진을 직접 보고 대본을 써요. 무료지만 하루 사용 횟수 제한이 있어요. (설정에 Gemini 키 필요)",
};
const TEMPLATE_HELP = {
  clean: "파란 제목 띠와 검은 상자 자막. 어떤 여행 글에도 잘 어울려요.",
  mood: "상자 없는 자막과 천천히 겹치는 전환. 잔잔한 풍경·카페 여행에 좋아요.",
  vlog: "노란 형광펜 자막과 빠른 전환. 맛집·액티비티 여행에 좋아요.",
};
const MOTION_LABEL = {
  auto: "움직임: 자동", zoom_in: "천천히 확대", zoom_out: "천천히 축소", pan_left: "왼쪽으로 이동",
  pan_right: "오른쪽으로 이동", pan_up: "위로 이동", pan_down: "아래로 이동", still: "움직임 없음",
};
const TRANSITION_LABEL = {
  auto: "전환: 자동", fade: "부드럽게 겹치기", slide: "옆으로 밀기", push: "부드럽게 밀기", circle: "원으로 열기",
  black: "어두워졌다 밝아지기", zoom: "확대하며 넘기기", wipe: "닦아내듯 넘기기",
};
const STAGE_LABEL = {
  queued: "순서를 기다리는 중",
  scraping: "글과 사진 불러오는 중",
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
    if (seg.dataset.name === "template") setTemplateHelp();
  });
});

function setTemplateHelp() {
  $("template-help").textContent = TEMPLATE_HELP[segValue("template")] || "";
}

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
  const fallback = state.info.has_api_key ? "api" : state.info.has_gemini_key ? "gemini" : "auto";
  setMode(localStorage.getItem(MODE_KEY) || fallback);
  if (!state.info.has_api_key && !state.info.has_gemini_key) {
    notice.textContent = "API 키가 없어도 'AI 없이(무료)'로 쓸 수 있어요. Claude나 Gemini를 쓰려면 설정에서 키를 넣어 주세요.";
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
  for (const id of ["bgm-edit"]) {
    const sel = $(id);
    const keep = sel.value;
    sel.innerHTML = "";
    const none = new Option("배경음악 없음", "");
    sel.appendChild(none);
    state.music.forEach((m) => sel.appendChild(new Option(m.name.replace(/\.mp3$/i, "").replace(/_/g, " "), m.name)));
    sel.value = keep;
  }
}


// ---------- 작업 흐름 ----------
function routeJob(job) {
  state.job = job;
  localStorage.setItem(STORE_KEY, job.id);
  stopPoll();
  if (job.stage === "loaded") return openSource(job);
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

async function startLoad() {
  setError("home-error", "");
  const url = $("url").value.trim();
  if (!url) return setError("home-error", "네이버 블로그 글 주소를 넣어 주세요.");
  const btn = $("btn-load");
  btn.disabled = true;
  try {
    routeJob(await api("POST", "/api/jobs", { url, mode: "load" }));
  } catch (e) {
    setError("home-error", e.message);
  } finally {
    btn.disabled = false;
  }
}

// ---------- 불러온 글·사진, 작성 방법 고르기 ----------
function smallImageUrl(i) {
  return `/api/jobs/${state.job.id}/image/${i}?small=1`;
}

function fillPhotos(el, onPick) {
  el.innerHTML = "";
  for (let i = 0; i < imageCount(); i++) {
    const b = document.createElement(onPick ? "button" : "div");
    if (onPick) {
      b.type = "button";
      b.onclick = () => onPick(i);
    }
    b.className = "photo";
    b.style.backgroundImage = `url("${smallImageUrl(i)}")`;
    b.setAttribute("aria-label", `사진 ${i + 1}`);
    el.appendChild(b);
  }
}

function openSource(job) {
  state.job = job;
  const post = job.post || { title: "", paragraphs: [], images: [] };
  $("source-title").textContent = post.title || "(제목 없음)";
  $("source-meta").textContent = `사진 ${post.images.length}장 · 글 ${post.paragraphs.length}문단`;
  fillPhotos($("source-photos"), null);
  const text = $("source-text");
  text.innerHTML = "";
  post.paragraphs.forEach((p) => {
    const el = document.createElement("p");
    el.textContent = p;
    text.appendChild(el);
  });
  setError("source-error", "");
  show("source");
}

async function writeScript(mode) {
  setError("source-error", "");
  if (state.job.script && state.job.script.scenes.length &&
      !confirm("지금까지 만든 장면을 지우고 새로 만들까요?")) return;
  const buttons = [$("btn-recommend"), $("btn-manual")];
  buttons.forEach((b) => { b.disabled = true; });
  try {
    routeJob(await api("POST", `/api/jobs/${state.job.id}/write`, { mode }));
  } catch (e) {
    setError("source-error", e.message);
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
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
  setSeg("quality", state.options.quality || "1080p");
  $("bgm-edit").value = state.options.bgm || "";
  setSeg("template", state.options.template || "clean");
  setSeg("effects", state.options.effects || "auto");
  setTemplateHelp();
  $("intro-title").value = state.script.intro_title || "";
  $("intro-sub").value = state.script.intro_sub || "";
  $("opt-intro").checked = state.options.intro !== false;
  $("opt-outro").checked = state.options.outro !== false;
  $("opt-title-bar").checked = state.options.title_bar !== false;
  $("yt-title").value = state.script.yt_title;
  $("yt-desc").value = state.script.yt_description;
  $("yt-tags").value = state.script.yt_tags.join(", ");
  updateTitleCount();
  const manual = job.mode === "manual";
  $("edit-title").textContent = manual ? "직접 작성" : "대본 고치기";
  $("edit-hint").textContent = manual
    ? "블로그 글과 사진을 골라 장면을 만들고, 자막을 직접 써 주세요. 자막은 성우가 그대로 읽어요."
    : "AI가 추천한 장면이에요. 자막·사진·움직임·전환을 마음대로 고쳐 주세요. 자막은 성우가 그대로 읽어요.";
  fillPhotos($("panel-photos"), (i) => addScene("", i));
  const panel = $("panel-text");
  panel.innerHTML = "";
  ((job.post && job.post.paragraphs) || []).forEach((p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "para";
    b.textContent = p;
    b.title = "눌러서 장면으로 추가";
    b.onclick = () => addScene(p, null);
    panel.appendChild(b);
  });
  $("source-panel").open = manual;
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
  $("empty-scenes").classList.toggle("hidden", n > 0);
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
      thumb.style.backgroundImage = `url("${smallImageUrl(scene.image_index)}")`;
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

    const place = document.createElement("input");
    place.className = "place";
    place.maxLength = 20;
    place.placeholder = "장소 이름 (예: 통영 동피랑) · 비워 두면 표시 안 함";
    place.value = scene.place || "";
    place.setAttribute("aria-label", `장면 ${i + 1} 장소 이름`);
    place.addEventListener("input", () => { scene.place = place.value; });

    const pickers = document.createElement("div");
    pickers.className = "scene-effects";
    const mkSelect = (labels, key, name) => {
      const sel = document.createElement("select");
      sel.className = key;
      Object.keys(labels).forEach((k) => sel.appendChild(new Option(labels[k], k)));
      sel.value = scene[key] || "auto";
      sel.setAttribute("aria-label", `장면 ${i + 1} ${name}`);
      sel.addEventListener("change", () => { scene[key] = sel.value; });
      pickers.appendChild(sel);
    };
    mkSelect(MOTION_LABEL, "motion", "사진 움직임");
    mkSelect(TRANSITION_LABEL, "transition", "장면 전환");

    li.append(head, thumb, ta, count, place, pickers);
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

function addScene(text, imageIndex) {
  const scenes = state.script.scenes;
  let idx = imageIndex;
  if (idx === null || idx === undefined) {  // 사진을 안 골랐으면 앞 장면 다음 사진부터
    const prev = scenes.length ? scenes[scenes.length - 1].image_index : null;
    idx = imageCount() ? (prev === null || prev === undefined ? 0 : Math.min(prev + 1, imageCount() - 1)) : null;
  }
  scenes.push({ text: typeof text === "string" ? text : "", image_index: idx, place: "", motion: "auto", transition: "auto" });
  renderScenes();
  const cards = document.querySelectorAll(".scene-card");
  const last = cards[cards.length - 1];
  last.scrollIntoView({ block: "center" });
  last.querySelector("textarea").focus();
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
    b.style.backgroundImage = `url("${smallImageUrl(i)}")`;
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
    .map((s) => ({ text: s.text.trim(), image_index: s.image_index, place: (s.place || "").trim(),
                   motion: s.motion || "auto", transition: s.transition || "auto" }))
    .filter((s) => s.text);
  return {
    scenes,
    yt_title: $("yt-title").value.trim(),
    yt_description: $("yt-desc").value.trim(),
    yt_tags: $("yt-tags").value.split(",").map((t) => t.trim().replace(/^#/, "")).filter(Boolean),
    intro_title: $("intro-title").value.trim(),
    intro_sub: $("intro-sub").value.trim(),
  };
}

async function startRender() {
  setError("edit-error", "");
  const script = collectScript();
  if (!script.scenes.length) return setError("edit-error", "자막이 있는 장면이 하나 이상 있어야 해요.");
  const options = { aspect: segValue("aspect"), voice: segValue("voice"), quality: segValue("quality") || "1080p",
                    bgm: $("bgm-edit").value || null,
                    template: segValue("template") || "clean", effects: segValue("effects") || "auto",
                    intro: $("opt-intro").checked, outro: $("opt-outro").checked,
                    title_bar: $("opt-title-bar").checked };
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
  $("thumb-box").classList.toggle("hidden", !job.has_thumbnail);
  if (job.has_thumbnail) {
    $("thumb-img").src = `/api/jobs/${job.id}/thumbnail?v=${job.finished_at || job.created_at}`;
    $("btn-thumb").href = `/api/jobs/${job.id}/thumbnail?download=1`;
  }
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
  const canFree = !!job.post && (job.failed_phase === "prepare" || job.failed_phase === "script") &&
    (job.mode === "api" || job.mode === "gemini");
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
    $("gemini-key-current").textContent = s.gemini_api_key ? `지금 저장된 Gemini 키: ${s.gemini_api_key}` : "아직 저장된 Gemini 키가 없어요.";
    $("channel-name").value = s.channel_name || "";
  } catch (e) {
    setError("settings-error", e.message);
  }
  $("api-key").value = "";
  $("channel-ok").classList.add("hidden");
  $("gemini-key").value = "";
  $("gemini-ok").classList.add("hidden");
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

async function saveChannelName() {
  setError("settings-error", "");
  try {
    const s = await api("PUT", "/api/settings", { channel_name: $("channel-name").value });
    $("channel-name").value = s.channel_name || "";
    $("channel-ok").classList.remove("hidden");
  } catch (e) {
    setError("settings-error", e.message);
  }
}

async function saveGeminiKey() {
  setError("settings-error", "");
  const key = $("gemini-key").value.trim();
  if (!key) return setError("settings-error", "Gemini API 키를 입력해 주세요.");
  try {
    const s = await api("PUT", "/api/settings", { gemini_api_key: key });
    $("gemini-key").value = "";
    $("gemini-key-current").textContent = `지금 저장된 Gemini 키: ${s.gemini_api_key}`;
    $("gemini-ok").classList.remove("hidden");
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
  if (state.returnTo === "source") return show("source");
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

$("btn-load").onclick = startLoad;
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") startLoad(); });
$("btn-recommend").onclick = () => writeScript(segValue("mode") || "auto");
$("btn-manual").onclick = () => writeScript("manual");
$("btn-source-back").onclick = () => goHome(true);
$("btn-rewrite").onclick = () => {
  state.job.script = collectScript();   // 지금 장면 수를 기준으로 덮어쓰기 전에 한 번 물어본다
  openSource(state.job);
};
$("btn-render").onclick = startRender;
$("btn-add-scene").onclick = () => addScene("", null);
$("yt-title").addEventListener("input", updateTitleCount);
$("btn-reedit").onclick = () => openEdit(state.job);
$("btn-new").onclick = () => goHome(true);
$("btn-retry").onclick = retry;
$("btn-error-home").onclick = () => goHome(true);
$("go-home").onclick = () => {
  const busy = state.job && !["ready", "done", "error"].includes(state.job.stage);
  if (busy) return routeJob(state.job); // 만드는 중이면 진행 화면으로
  if (state.view === "edit" && !confirm("지금 대본을 그만두고 처음 화면으로 갈까요?")) return;
  if (state.view === "source" && !confirm("불러온 글을 닫고 처음 화면으로 갈까요?")) return;
  goHome(true);
};
$("open-settings").onclick = openSettings;
$("btn-save-key").onclick = saveKey;
$("music-file").addEventListener("change", uploadMusic);
$("btn-settings-back").onclick = leaveSettings;
$("btn-keyed").onclick = makeKeyedInstaller;
$("btn-save-gemini").onclick = saveGeminiKey;
$("btn-save-channel").onclick = saveChannelName;
$("btn-free-auto").onclick = () => useFreeMode("auto");
$("picker-close").onclick = () => $("picker").close();

// ---------- 앱 창 열림 신호 (창을 닫으면 프로그램이 스스로 꺼짐) ----------
function heartbeat() {
  fetch("/api/heartbeat", { method: "POST", keepalive: true }).catch(() => {});
}
heartbeat();
setInterval(heartbeat, 5000);
window.addEventListener("pagehide", () => {
  navigator.sendBeacon("/api/bye");
});

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
  setTemplateHelp();
  show("home");
})();
