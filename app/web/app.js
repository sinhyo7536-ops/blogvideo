"use strict";

const $ = (id) => document.getElementById(id);
const VIEWS = ["home", "source", "edit", "progress", "result", "error", "settings"];
const MODE_KEY = "blogvideo.mode";
const MODE_HELP = {
  api: "Claude가 글과 사진을 직접 보고 대본을 써요. 영상 1개당 약 100~300원이 들어요.",
};
const LOOK_LABEL = { warm: "따뜻한 색감", cool: "시원한 색감", bw: "흑백", vivid: "선명한 색감", soft: "부드러운 색감" };
const CAPTION_SIZE_LABEL = { large: "자막 크게", small: "자막 작게" };
const CAPTION_POS_LABEL = { top: "자막 위쪽", center: "자막 가운데", bottom: "자막 아래쪽" };
const STRENGTH_LABEL = { subtle: "은은한 움직임", strong: "큰 움직임" };
const TEMPLATE_HELP = {
  clean: "파란 제목 띠와 검은 상자 자막. 어떤 여행 글에도 잘 어울려요.",
  mood: "상자 없는 자막과 천천히 겹치는 전환. 잔잔한 풍경·카페 여행에 좋아요.",
  vlog: "노란 형광펜 자막과 빠른 전환. 맛집·액티비티 여행에 좋아요.",
  cinema: "영화처럼 위아래 검은 띠, 어두워졌다 밝아지는 느린 전환. 풍경 여행에 좋아요.",
  essay: "작고 조용한 자막, 사진이 오래 머물고 천천히 넘어가요. 사진이 예쁜 글에 좋아요.",
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
  analyzing: "AI가 여행을 분석하는 중",
  scripting: "AI가 대본 쓰는 중",
  directing: "AI가 연출을 정하는 중",
  tts: "목소리 만드는 중",
  rendering: "영상 만드는 중",
};
const CANCEL_LABEL = {
  queued: "멈추기", scraping: "불러오기 멈추기", analyzing: "분석 멈추기", scripting: "대본 쓰기 멈추기",
  directing: "영상 만들기 멈추기", tts: "영상 만들기 멈추기", rendering: "영상 만들기 멈추기",
};
const STORE_KEY = "blogvideo.jobId";
const AI_PRESETS = [["natural", "자연스럽게"], ["youtuber", "유튜버 말투"], ["emotion", "감성 더하기"],
                    ["plain", "과장 줄이기"], ["shorter", "30% 줄이기"], ["longer", "조금 길게"],
                    ["experience", "경험이 드러나게"], ["friendly", "친근하게"], ["polite", "정중하게"]];
const AUTOSAVE_MS = 1200;
const LOW_SCORE = 50;
const PHOTO_SECS = [[0, "자동"], [1, "1초"], [2, "2초"], [3, "3초"], [4, "4초"], [5, "5초"], [7, "7초"], [10, "10초"]];

const state = { info: null, job: null, script: null, options: null, music: [], pollTimer: null, pickerScene: -1,
                selected: null, cancelling: false, opening: false, saveTimer: null, saving: null, dirty: false,
                ai: null };

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
  if (state.view === "edit" && view !== "edit") stopAudio();
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
    if (seg.dataset.name === "bgm-source") showBgmSource(b.dataset.value);
  });
});

function setTemplateHelp() {
  $("template-help").textContent = TEMPLATE_HELP[segValue("template")] || "";
}

// ---------- 영상 프리셋 ----------
async function loadPresets() {
  const r = await api("GET", "/api/presets");
  state.presets = r.presets;
  const box = $("preset-list");
  box.innerHTML = "";
  r.presets.forEach((p) => {
    const b = el("button", "", p.label);
    b.type = "button";
    b.dataset.value = p.key;
    b.onclick = () => applyPreset(p.key);
    box.appendChild(b);
  });
}

function applyPreset(key) {
  const p = (state.presets || []).find((x) => x.key === key);
  if (!p) return;
  const o = p.options;
  setSeg("preset", key);
  if (o.template) setSeg("template", o.template);
  if (o.effects) setSeg("effects", o.effects);
  if (o.title_bar !== undefined) $("opt-title-bar").checked = o.title_bar;
  if (o.intro !== undefined) $("opt-intro").checked = o.intro;
  if (o.outro !== undefined) $("opt-outro").checked = o.outro;
  $("preset-help").textContent = p.help;
  setTemplateHelp();
  updateSummary();
  markDirty();
}

function showPreset(key) {
  const p = (state.presets || []).find((x) => x.key === key);
  setSeg("preset", p ? key : "");
  $("preset-help").textContent = p ? p.help : "프리셋을 고르거나, 아래에서 하나씩 정해도 돼요.";
}

// 스타일을 하나씩 바꾸면 프리셋과 달라졌는지 확인한다
function presetStillMatches() {
  const key = segValue("preset");
  const p = (state.presets || []).find((x) => x.key === key);
  if (!p) return;
  const o = p.options;
  const same = (!o.template || segValue("template") === o.template) && (!o.effects || segValue("effects") === o.effects)
    && (o.title_bar === undefined || $("opt-title-bar").checked === o.title_bar);
  if (!same) showPreset("custom");
}

function setMode(mode) {
  setSeg("mode", mode);
  localStorage.setItem(MODE_KEY, mode);
  document.querySelectorAll(".mode-help").forEach((el) => { el.textContent = MODE_HELP[mode] || ""; });
}

// ---------- 초기화 ----------
async function loadInfo() {
  state.info = await api("GET", "/api/info");
  $("open-settings").classList.toggle("hidden", !state.info.is_local);
  const notice = $("key-notice");
  setMode("api");
  if (!state.info.has_api_key) {
    notice.textContent = "Claude API 키가 아직 없어요. 설정에서 키를 넣어 주세요.";
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


// ---------- 목소리·배경음악 ----------
async function loadVoices() {
  const voices = await api("GET", "/api/voices");
  const sel = $("voice");
  sel.innerHTML = "";
  const groups = {};
  voices.forEach((v) => {
    if (!groups[v.group]) {
      groups[v.group] = document.createElement("optgroup");
      groups[v.group].label = v.group === "다국어" ? "다국어 (억양이 조금 달라요)" : v.group;
      sel.appendChild(groups[v.group]);
    }
    groups[v.group].appendChild(new Option(v.label, v.key));
  });
}

function playAudio(el, src, btn, idle) {
  if (!el.paused && el.dataset.src === src) {   // 같은 것을 다시 누르면 멈춤
    el.pause();
    btn.textContent = idle;
    return;
  }
  el.dataset.src = src;
  el.src = src;
  el.onended = el.onpause = () => { btn.textContent = idle; };
  el.play().then(() => { btn.textContent = "멈추기"; }).catch(() => { btn.textContent = idle; });
}

function stopAudio() {
  ["voice-audio", "bgm-audio"].forEach((id) => { $(id).pause(); });
}

async function previewVoice() {
  const btn = $("btn-voice-play");
  const audio = $("voice-audio");
  if (!audio.paused) { audio.pause(); return; }
  btn.disabled = true;
  btn.textContent = "만드는 중…";
  try {
    const res = await fetch("/api/voice-preview", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice: $("voice").value, speed: segValue("speed") || "normal" }) });
    if (!res.ok) {
      let msg = "목소리를 들려 드리지 못했어요.";
      try { msg = (await res.json()).message || msg; } catch (e) { /* 무시 */ }
      throw new Error(msg);
    }
    const url = URL.createObjectURL(await res.blob());
    btn.disabled = false;
    playAudio(audio, url, btn, "미리 듣기");
  } catch (e) {
    toast(e.message);
    btn.textContent = "미리 듣기";
  } finally {
    btn.disabled = false;
  }
}

function previewBgm() {
  const name = $("bgm-edit").value;
  if (!name) return toast("먼저 배경음악을 골라 주세요.");
  playAudio($("bgm-audio"), `/api/music/file/${encodeURIComponent(name)}`, $("btn-bgm-play"), "미리 듣기");
}

function showBgmSource(which) {
  setSeg("bgm-source", which);
  $("bgm-pick").classList.toggle("hidden", which !== "pick");
  $("bgm-youtube").classList.toggle("hidden", which !== "youtube");
  $("bgm-file").classList.toggle("hidden", which !== "file");
}

async function uploadBgmFile() {
  const f = $("bgm-upload").files[0];
  if (!f) return;
  setError("bgm-error", "");
  $("bgm-ok").classList.add("hidden");
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await api("POST", "/api/music", fd);
    await loadMusic();
    $("bgm-edit").value = r.name;
    $("bgm-upload").value = "";
    showBgmSource("pick");
    const ok = $("bgm-ok");
    ok.textContent = `배경음악으로 골랐어요: ${r.name.replace(/\.mp3$/i, "")}`;
    ok.classList.remove("hidden");
  } catch (e) {
    setError("bgm-error", e.message);
  }
}

async function fetchYoutubeMusic() {
  setError("bgm-error", "");
  $("bgm-ok").classList.add("hidden");
  const url = $("yt-url").value.trim();
  if (!url) return setError("bgm-error", "유튜브 영상 주소를 넣어 주세요.");
  const btn = $("btn-yt-music");
  btn.disabled = true;
  btn.textContent = "가져오는 중…";
  try {
    const r = await api("POST", "/api/music/youtube", { url });
    await loadMusic();
    $("bgm-edit").value = r.name;
    $("yt-url").value = "";
    showBgmSource("pick");
    const ok = $("bgm-ok");
    ok.textContent = `배경음악으로 골랐어요: ${r.name.replace(/\.mp3$/i, "")}`;
    ok.classList.remove("hidden");
  } catch (e) {
    setError("bgm-error", e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "음악 가져오기";
  }
}

// ---------- 작업 흐름 ----------
function routeJob(job) {
  state.job = job;
  localStorage.setItem(STORE_KEY, job.id);
  stopPoll();
  const active = !["loaded", "ready", "done", "error"].includes(job.stage);
  if (!active && state.cancelling) {
    state.cancelling = false;
    if (job.stage === "error" && job.failed_phase === "prepare") return goHome(true);   // 불러오기를 멈춤
    toast("멈췄어요.");
  }
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

const PIPE_STEP = { queued: "load", scraping: "load", analyzing: "analyze", scripting: "write", directing: "voice",
                    tts: "voice", rendering: "render" };
const PIPE_ORDER = ["load", "analyze", "write", "voice", "render"];

function showPipeline(job) {
  const now = PIPE_STEP[job.stage] || "load";
  // 대본이 이미 있는 작업(영상 만들기·쇼츠)은 앞 단계를 끝난 것으로
  const start = job.script && ["queued", "directing", "tts", "rendering"].includes(job.stage) ? "voice" : now;
  const at = PIPE_ORDER.indexOf(job.stage === "queued" && job.script ? start : now);
  document.querySelectorAll("#pipeline li").forEach((li) => {
    const k = PIPE_ORDER.indexOf(li.dataset.step);
    li.classList.toggle("done", k < at);
    li.classList.toggle("now", k === at);
  });
}

function openProgress(job) {
  showPipeline(job);
  $("progress-title").textContent = STAGE_LABEL[job.stage] || "만드는 중";
  const pct = Math.round((job.progress || 0) * 100);
  $("bar-fill").style.width = pct + "%";
  $("percent").textContent = pct + "%";
  document.querySelector(".bar").setAttribute("aria-valuenow", String(pct));
  const btn = $("btn-cancel");
  btn.textContent = state.cancelling ? "멈추는 중…" : (CANCEL_LABEL[job.stage] || "멈추기");
  btn.disabled = !!state.cancelling;
  show("progress");
}

async function cancelJob() {
  const making = state.job.stage === "tts" || state.job.stage === "rendering" ||
    (state.job.stage === "queued" && state.job.script);
  if (making && !confirm("영상 만들기를 멈출까요? 지금까지 만든 부분은 버려지고, 대본 고치기 화면으로 돌아가요.")) return;
  state.cancelling = true;
  $("btn-cancel").disabled = true;
  $("btn-cancel").textContent = "멈추는 중…";
  try {
    const job = await api("POST", `/api/jobs/${state.job.id}/cancel`);
    if (["ready", "loaded", "done", "error"].includes(job.stage)) routeJob(job);
  } catch (e) {
    state.cancelling = false;
    toast(e.message);
  }
}

// ---------- 대본 설정 ----------
const SETTINGS_KEY = "blogvideo.scriptSettings";
const SECTION_LABEL = { opening: "오프닝", intro: "여행 소개", body: "여행 이야기", place: "장소 소개",
                        review: "후기", ending: "엔딩" };
const EVIDENCE_CLASS = { "글에 나옴": "fact", "사진에서 봄": "photo", "추론": "guess" };

function scriptSettings() {
  return { style: segValue("sstyle") || "vlog", length: $("s-length").value, tone: $("s-tone").value,
           info_ratio: Number($("s-ratio").value), detail: $("s-detail").value };
}

function showRatio() {
  const v = Number($("s-ratio").value);
  $("s-ratio-label").textContent = v >= 70 ? "정보 위주" : v <= 30 ? "감성 위주" : "반반";
}

function loadScriptSettings() {
  let s = {};
  try { s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}"); } catch (e) { s = {}; }
  if (s.style) setSeg("sstyle", s.style);
  if (s.length) $("s-length").value = s.length;
  if (s.tone) $("s-tone").value = s.tone;
  if (s.detail) $("s-detail").value = s.detail;
  if (s.info_ratio !== undefined) $("s-ratio").value = s.info_ratio;
  showRatio();
}

function saveScriptSettings() {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(scriptSettings()));
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function renderAnalysis(job) {
  const panel = $("analysis-panel");
  const body = $("analysis-body");
  const a = job.analysis;
  panel.classList.toggle("hidden", !a);
  body.innerHTML = "";
  if (a) {
    const facts = [["주제", a.theme], ["지역", a.region], ["기간", a.period], ["동행", a.companions],
                   ["가장 흥미로운 장면", a.best_moment]];
    const dl = el("dl", "facts");
    facts.forEach(([k, v]) => { if (v) { dl.append(el("dt", "", k), el("dd", "", v)); } });
    body.appendChild(dl);
    if (a.days && a.days.length) {
      body.appendChild(el("h3", "", "일정"));
      const ul = el("ul");
      a.days.forEach((d) => ul.appendChild(el("li", "", `${d.day}일차${d.date ? " (" + d.date + ")" : ""}: ${d.summary || ""} ${d.places.length ? "— " + d.places.join(", ") : ""}`)));
      body.appendChild(ul);
    }
    if (a.places && a.places.length) {
      body.appendChild(el("h3", "", "방문한 곳"));
      const ul = el("ul", "places");
      a.places.forEach((p) => {
        const li = el("li");
        li.append(el("b", "", p.name), el("span", "kind", p.kind),
                  el("span", "evidence " + (EVIDENCE_CLASS[p.evidence] || "guess"), p.evidence));
        if (p.summary) li.appendChild(el("div", "muted", p.summary));
        if (p.experience) li.appendChild(el("div", "", "경험: " + p.experience));
        ul.appendChild(li);
      });
      body.appendChild(ul);
    }
    const notes = [].concat((a.highlights || []).map((n) => ["인상적인 경험", n]), (a.episodes || []).map((n) => ["에피소드", n]));
    if (notes.length) {
      body.appendChild(el("h3", "", "경험과 에피소드"));
      const ul = el("ul");
      notes.forEach(([k, n]) => {
        const li = el("li", "", `${k}: ${n.text} `);
        li.appendChild(el("span", "evidence " + (EVIDENCE_CLASS[n.evidence] || "guess"), n.evidence));
        ul.appendChild(li);
      });
      body.appendChild(ul);
    }
    if (a.missing_info && a.missing_info.length) {
      body.appendChild(el("h3", "", "글에 없어서 모르는 것"));
      const ul = el("ul");
      a.missing_info.forEach((m) => ul.appendChild(el("li", "muted", m)));
      body.appendChild(ul);
    }
    body.appendChild(el("p", "muted", "표시: 글에 나옴 = 본문에 적혀 있음 · 사진에서 봄 = 사진으로만 확인 · 추론 = AI가 짐작한 것(대본에서는 단정하지 않아요)"));
  }
  const post = job.post || {};
  const notice = [];
  if (post.missing && post.missing.length) notice.push(`사진 ${post.missing.length}장을 받지 못했어요: ${post.missing.join(", ")}. 영상에는 들어가지 않아요.`);
  if (post.duplicates) notice.push(`같은 사진 ${post.duplicates}장은 한 장만 남겼어요.`);
  $("photo-notice").textContent = notice.join(" ");
  $("photo-notice").classList.toggle("hidden", !notice.length);
}

// mode: api = Claude가 바로 대본을 씀, manual = 글·사진만 불러와 직접 작성
async function startLoad(mode) {
  setError("home-error", "");
  const url = $("url").value.trim();
  if (!url) return setError("home-error", "네이버 블로그 글 주소를 넣어 주세요.");
  const urls = $("more-urls").value.split(/\s+/).map((u) => u.trim()).filter(Boolean);
  if (urls.length > 9) return setError("home-error", "함께 넣을 글은 9개까지예요.");
  const buttons = [$("btn-text"), $("btn-load")];
  buttons.forEach((b) => { b.disabled = true; });
  try {
    saveScriptSettings();
    routeJob(await api("POST", "/api/jobs", { url, urls, mode, settings: scriptSettings() }));
  } catch (e) {
    setError("home-error", e.message);
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

// ---------- 불러온 글·사진, 작성 방법 고르기 ----------
function smallImageUrl(i) {
  return `/api/jobs/${state.job.id}/image/${i}?small=1`;
}

function photoList(s) {
  const first = s.image_index === null || s.image_index === undefined ? [] : [s.image_index];
  return first.concat((s.more_images || []).filter((x) => !first.includes(x)));
}

function usedCounts() {
  const used = {};
  ((state.script && state.script.scenes) || []).forEach((s) => {
    photoList(s).forEach((k) => { used[k] = (used[k] || 0) + 1; });
  });
  return used;
}

function excluded() {
  return (state.script && state.script.excluded_images) || [];
}

function photoInfo(i) {
  const a = state.job && state.job.analysis;
  return (a && a.photos && a.photos.find((p) => p.index === i)) || null;
}

// 사진 한 칸: 진짜 <img>를 넣어 칸 크기가 사진에 맞게 잡히게 한다(배경 그림으로 넣으면 칸이 찌그러져 겹쳐 보였음)
function photoTile(i, opts) {
  const o = opts || {};
  const el = document.createElement(o.onPick ? "button" : "div");
  if (o.onPick) {
    el.type = "button";
    el.onclick = () => o.onPick(i);
  }
  el.className = "photo" + (o.selected ? " selected" : "");
  el.setAttribute("aria-label", `사진 ${i + 1}`);
  const img = document.createElement("img");
  img.src = smallImageUrl(i);
  img.alt = "";
  img.loading = "lazy";
  const num = document.createElement("span");
  num.className = "num";
  num.textContent = String(i + 1);
  el.append(img, num);
  if (o.used) {
    const u = document.createElement("span");
    u.className = "used";
    u.textContent = o.used > 1 ? `사용 ${o.used}번` : "사용 중";
    el.appendChild(u);
  }
  if (o.category) {
    const c = document.createElement("span");
    c.className = "cat";
    c.textContent = o.category;
    el.appendChild(c);
  }
  if (o.excluded) {
    el.classList.add("excluded");
    const x = document.createElement("span");
    x.className = "out";
    x.textContent = "뺀 사진";
    el.appendChild(x);
  }
  return el;
}

function fillPhotos(el, onPick, markUsed) {
  el.innerHTML = "";
  const used = markUsed ? usedCounts() : {};
  for (let i = 0; i < imageCount(); i++) el.appendChild(photoTile(i, { onPick, used: used[i] }));
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
  const buttons = [$("btn-text-source"), $("btn-recommend")];
  buttons.forEach((b) => { b.disabled = true; });
  try {
    saveScriptSettings();
    routeJob(await api("POST", `/api/jobs/${state.job.id}/write`, { mode, settings: scriptSettings() }));
  } catch (e) {
    setError("source-error", e.message);
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

// ---------- 장면 편집 ----------
function openEdit(job) {
  state.script = JSON.parse(JSON.stringify(job.script));
  state.options = Object.assign({}, job.options);
  setSeg("aspect", state.options.aspect);
  $("voice").value = state.options.voice || "female";
  if (!$("voice").value) $("voice").value = "female";
  setSeg("speed", state.options.speed || "normal");
  showBgmSource("pick");
  setError("bgm-error", "");
  $("bgm-ok").classList.add("hidden");
  setSeg("quality", state.options.quality || "1080p");
  $("bgm-edit").value = state.options.bgm || "";
  setSeg("template", state.options.template || "clean");
  setSeg("effects", state.options.effects || "auto");
  setTemplateHelp();
  showPreset(state.options.preset || "custom");
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
  const asIs = job.mode === "text";
  $("edit-title").textContent = manual ? "직접 작성" : asIs ? "블로그 글 그대로" : "대본 고치기";
  $("edit-hint").textContent = manual
    ? "블로그 글과 사진을 골라 장면을 만들고, 자막을 직접 써 주세요. 자막은 성우가 그대로 읽어요."
    : asIs
      ? "블로그 글을 그대로 장면으로 나눴어요. 장면마다 블로그에서 그 글 바로 뒤에 있던 사진이 붙어 있어요. 자막은 성우가 그대로 읽어요. 필요하면 'AI로 고치기'로 다듬을 수 있어요."
      : "AI가 글과 사진을 보고 쓴 대본이에요. 자막·사진·움직임·전환을 마음대로 고치고, 원하는 연출은 말로 적어 주세요. 자막은 성우가 그대로 읽어요.";
  $("direction").value = state.script.direction || "";
  state.selected = null;
  const panel = $("panel-text");
  panel.innerHTML = "";
  ((job.post && job.post.paragraphs) || []).forEach((p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "para";
    b.textContent = p;
    b.title = "눌러서 장면으로 추가";
    b.onclick = () => addScene(p, null);   // 선택한 장면 바로 아래(없으면 맨 아래)에
    panel.appendChild(b);
  });
  $("source-panel").open = manual;
  renderAnalysis(job);
  renderSources(job);
  state.opening = true;
  renderScenes();
  state.opening = false;
  state.dirty = false;
  showSaved("");
  showConfirm(job.is_confirmed);
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
  updateSummary();
  if (state.selected !== null && state.selected >= n) state.selected = null;
  fillPhotos($("panel-photos"), (k) => addScene("", k), true);
  $("panel-hint").textContent = state.selected === null
    ? "글 한 토막이나 사진을 누르면 그 내용으로 새 장면이 맨 아래에 생겨요. 장면을 먼저 눌러 고르면 그 장면 바로 아래에 생겨요."
    : `글 한 토막이나 사진을 누르면 고른 장면(장면 ${state.selected + 1}) 바로 아래에 새 장면이 생겨요.`;
  state.script.scenes.forEach((scene, i) => {
    const li = document.createElement("li");
    li.className = "scene-card" + (state.selected === i ? " selected" : "");
    li.addEventListener("focusin", () => selectScene(i));
    li.addEventListener("click", () => selectScene(i));

    const head = document.createElement("div");
    head.className = "scene-head";
    const num = document.createElement("span");
    num.className = "scene-num";
    num.textContent = `장면 ${i + 1}`;
    if (SECTION_LABEL[scene.section]) num.appendChild(el("span", "section-tag", SECTION_LABEL[scene.section]));
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
    thumb.onclick = () => openPicker(i, "set");
    if (excluded().includes(scene.image_index)) thumb.classList.add("excluded");
    const photoBox = scenePhotos(scene, i);

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
    ta.addEventListener("input", () => { upd(); updateSummary(); if (previewIndex() === i) renderPreview(); });
    upd();

    const place = document.createElement("input");
    place.className = "place";
    place.maxLength = 20;
    place.placeholder = "장소 이름 (예: 통영 동피랑) · 비워 두면 표시 안 함";
    place.value = scene.place || "";
    place.setAttribute("aria-label", `장면 ${i + 1} 장소 이름`);
    place.addEventListener("input", () => { scene.place = place.value; if (previewIndex() === i) renderPreview(); });

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

    const more = document.createElement("details");
    more.className = "direction-more";
    more.open = !!(scene.direction || "").trim();
    more.appendChild(el("summary", "", "연출 요청 더 보기"));
    const want = document.createElement("textarea");
    more.appendChild(want);
    want.className = "direction";
    want.rows = 2;
    want.maxLength = 400;
    want.placeholder = "연출 요청 (선택) 예: 천천히 확대하면서 따뜻한 색감으로, 자막 크게";
    want.value = scene.direction || "";
    want.setAttribute("aria-label", `장면 ${i + 1} 연출 요청`);
    want.addEventListener("input", () => { scene.direction = want.value; });

    const applied = document.createElement("div");
    applied.className = "applied";
    const chips = appliedLabels(scene);
    if (chips.length) {
      const label = document.createElement("span");
      label.textContent = "AI 연출: " + chips.join(" · ");
      const reset = document.createElement("button");
      reset.type = "button";
      reset.textContent = "되돌리기";
      reset.title = "색감·자막 크기와 위치·머무는 시간·움직임 세기를 기본으로 되돌리기";
      reset.onclick = (e) => {
        e.stopPropagation();
        Object.assign(scene, { look: "auto", caption_size: "auto", caption_pos: "auto", strength: "auto", hold: 0,
                               direction_done: "" });
        renderScenes();
      };
      applied.append(label, reset);
    }

    const actions = document.createElement("div");
    actions.className = "scene-actions";
    const act = (label, title, fn) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.title = title;
      b.onclick = (e) => { e.stopPropagation(); fn(); };
      actions.appendChild(b);
    };
    act("+ 앞에 추가", "이 장면 앞에 빈 장면 넣기", () => addScene("", null, i));
    act("+ 뒤에 추가", "이 장면 뒤에 빈 장면 넣기", () => addScene("", null, i + 1));
    act("복제", "이 장면을 똑같이 하나 더 만들기", () => duplicateScene(i));
    act("나누기", "자막의 커서 위치에서 장면을 둘로 나누기", () => splitScene(i, ta));
    const aiBtn = document.createElement("button");
    aiBtn.type = "button";
    aiBtn.className = "ai-fix";
    aiBtn.textContent = "AI로 고치기";
    aiBtn.title = "이 장면의 글만 AI가 고쳐요";
    aiBtn.onclick = (e) => { e.stopPropagation(); openAiDialog([i]); };
    actions.prepend(aiBtn);

    li.append(head, thumb, photoBox, ta, count, place, pickers, more, applied, actions);
    list.appendChild(li);
  });
  renderTimeline();
  markDirty();
}

// 장면의 사진 줄: 이어서 보여 줄 사진, 사진별 시간, AI 관련도 점수와 추천 사진
function scenePhotos(scene, i) {
  const box = el("div", "scene-photos");
  const strip = el("div", "photo-strip");
  (scene.more_images || []).forEach((k, n) => {
    const t = el("span", "mini");
    const img = document.createElement("img");
    img.src = smallImageUrl(k);
    img.alt = `이어서 보여 줄 사진 ${k + 1}`;
    const x = el("button", "mini-x", "✕");
    x.type = "button";
    x.title = "이 사진 빼기";
    x.setAttribute("aria-label", `장면 ${i + 1}에서 사진 ${k + 1} 빼기`);
    x.onclick = (e) => {
      e.stopPropagation();
      scene.more_images.splice(n, 1);
      scene.photo_secs = [];
      renderScenes();
    };
    t.append(img, x);
    strip.appendChild(t);
  });
  if (imageCount() > 1) {
    const add = el("button", "add-photo", "+ 사진 더하기");
    add.type = "button";
    add.title = "이 장면에서 사진을 여러 장 이어서 보여 줘요";
    add.onclick = (e) => { e.stopPropagation(); openPicker(i, "add"); };
    strip.appendChild(add);
  }
  box.appendChild(strip);
  const list = photoList(scene);
  if (list.length > 1) {
    const times = el("div", "photo-times");
    times.appendChild(el("span", "muted", "사진별 시간"));
    list.forEach((k, n) => {
      const sel = document.createElement("select");
      PHOTO_SECS.forEach(([v, label]) => sel.appendChild(new Option(`${n + 1}번째 ${label}`, String(v))));
      sel.value = String((scene.photo_secs || [])[n] || 0);
      sel.setAttribute("aria-label", `장면 ${i + 1} ${n + 1}번째 사진 시간`);
      sel.addEventListener("change", () => {
        const secs = list.map((_, m) => Number((scene.photo_secs || [])[m] || 0));
        secs[n] = Number(sel.value);
        scene.photo_secs = secs;
      });
      times.appendChild(sel);
    });
    box.appendChild(times);
  }
  if (scene.photo_score >= 0) {
    const low = scene.photo_score < LOW_SCORE;
    const badge = el("div", "score " + (low ? "low" : "good"),
                     low ? `⚠ 글과 사진이 잘 안 맞을 수 있어요 (${scene.photo_score}점)`
                         : `글과 사진이 잘 맞아요 (${scene.photo_score}점)`);
    box.appendChild(badge);
    const alts = (scene.photo_alts || []).filter((k) => k !== scene.image_index && !excluded().includes(k));
    if (low && alts.length) {
      const row = el("div", "alts");
      row.appendChild(el("span", "muted", "추천 사진:"));
      alts.forEach((k) => {
        const b = el("button", "mini alt");
        b.type = "button";
        b.title = `사진 ${k + 1}로 바꾸기`;
        b.setAttribute("aria-label", `장면 ${i + 1} 사진을 추천 사진 ${k + 1}로 바꾸기`);
        const img = document.createElement("img");
        img.src = smallImageUrl(k);
        img.alt = "";
        b.appendChild(img);
        b.onclick = (e) => {
          e.stopPropagation();
          scene.image_index = k;
          scene.photo_score = -1;
          renderScenes();
        };
        row.appendChild(b);
      });
      box.appendChild(row);
    }
  }
  return box;
}

function updateSummary() {
  // 예상 길이: 성우가 1초에 5~6자쯤 읽고, 장면마다 0.4초 쉰다. 시작·끝 화면은 6.5초.
  const scenes = state.script.scenes.filter((s) => s.text.trim());
  const pace = { slow: 4.7, normal: 5.5, fast: 6.5 }[segValue("speed") || "normal"];
  let sec = scenes.reduce((a, s) => a + s.text.trim().length / pace + 0.4, 0);
  if (scenes.length) sec += ($("opt-intro").checked ? 3 : 0) + ($("opt-outro").checked ? 3.5 : 0);
  const m = Math.floor(sec / 60);
  const rest = Math.round(sec % 60);
  $("scene-summary").textContent = scenes.length
    ? `장면 ${scenes.length}개 · 예상 길이 약 ${m ? m + "분 " : ""}${rest}초 (장면을 지우거나 더하면 길이가 바뀌어요)`
    : "";
}

function moveScene(i, d) {
  const s = state.script.scenes;
  [s[i], s[i + d]] = [s[i + d], s[i]];
  state.selected = i + d;
  renderScenes();
}

function deleteScene(i) {
  if (!confirm(`장면 ${i + 1}을 지울까요?`)) return;
  state.script.scenes.splice(i, 1);
  state.selected = null;
  renderScenes();
}

function appliedLabels(scene) {
  const out = [];
  if (LOOK_LABEL[scene.look]) out.push(LOOK_LABEL[scene.look]);
  if (STRENGTH_LABEL[scene.strength]) out.push(STRENGTH_LABEL[scene.strength]);
  if (CAPTION_SIZE_LABEL[scene.caption_size]) out.push(CAPTION_SIZE_LABEL[scene.caption_size]);
  if (CAPTION_POS_LABEL[scene.caption_pos]) out.push(CAPTION_POS_LABEL[scene.caption_pos]);
  if (scene.hold > 0) out.push(`${scene.hold}초 더 머무름`);
  return out;
}

// ---------- 스튜디오 미리보기 · 타임라인 ----------
function previewIndex() {
  const n = state.script ? state.script.scenes.length : 0;
  if (!n) return -1;
  return state.selected === null || state.selected === undefined ? 0 : Math.min(state.selected, n - 1);
}

function renderPreview() {
  if (!state.script || !$("pv-stage")) return;
  const scenes = state.script.scenes;
  const i = previewIndex();
  const s = i >= 0 ? scenes[i] : null;
  const stage = $("pv-stage");
  stage.dataset.aspect = segValue("aspect") || "16:9";
  stage.dataset.tpl = segValue("template") || "clean";
  const img = $("pv-img");
  const has = s && s.image_index !== null && s.image_index !== undefined && s.image_index < imageCount();
  if (has) {
    const src = imageUrl(s.image_index);
    if (img.getAttribute("src") !== src) img.src = src;
  }
  img.classList.toggle("hidden", !has);
  $("pv-caption").textContent = s ? (s.text || "(자막이 비어 있어요)") : "장면을 만들면 여기서 미리 보여요";
  const place = $("pv-place");
  place.textContent = s && s.place ? s.place : "";
  place.classList.toggle("hidden", !(s && s.place && $("opt-title-bar").checked));
  const photos = s ? photoList(s).length : 0;
  $("pv-info").textContent = s ? `장면 ${i + 1} / ${scenes.length}` + (photos > 1 ? ` · 사진 ${photos}장이 차례로` : "") : "";
  document.querySelectorAll("#pv-timeline .tl-item").forEach((b, k) => b.classList.toggle("on", k === i));
}

function renderTimeline() {
  const box = $("pv-timeline");
  if (!box || !state.script) return;
  box.innerHTML = "";
  state.script.scenes.forEach((s, i) => {
    const b = el("button", "tl-item");
    b.type = "button";
    b.title = s.text;
    b.setAttribute("aria-label", `타임라인 장면 ${i + 1}`);
    if (s.image_index !== null && s.image_index !== undefined && s.image_index < imageCount()) {
      const img = document.createElement("img");
      img.src = smallImageUrl(s.image_index);
      img.alt = "";
      img.loading = "lazy";
      b.appendChild(img);
    }
    b.appendChild(el("span", "tl-num", String(i + 1)));
    b.onclick = () => { selectScene(i); focusScene(i); };
    box.appendChild(b);
  });
  const sum = $("scene-summary").textContent.replace(/ \(.*\)$/, "");
  $("pv-total").textContent = sum;
  renderPreview();
}

function selectScene(i) {
  if (state.selected === i) return;
  state.selected = i;
  renderPreview();
  document.querySelectorAll(".scene-card").forEach((c, k) => c.classList.toggle("selected", k === i));
  $("panel-hint").textContent = `글 한 토막이나 사진을 누르면 고른 장면(장면 ${i + 1}) 바로 아래에 새 장면이 생겨요.`;
}

function focusScene(i) {
  const card = document.querySelectorAll(".scene-card")[i];
  if (!card) return;
  card.scrollIntoView({ block: "center" });
  card.querySelector("textarea").focus({ preventScroll: true });
}

// at: 넣을 자리(0이면 맨 앞). 안 주면 고른 장면 바로 아래, 고른 장면이 없으면 맨 아래.
function addScene(text, imageIndex, at) {
  const scenes = state.script.scenes;
  let pos = at;
  if (pos === undefined || pos === null) pos = state.selected === null ? scenes.length : state.selected + 1;
  pos = Math.max(0, Math.min(pos, scenes.length));
  let idx = imageIndex;
  if (idx === null || idx === undefined) {  // 사진을 안 골랐으면 바로 앞 장면의 다음 사진
    const prev = pos > 0 ? scenes[pos - 1].image_index : null;
    idx = imageCount() ? (prev === null || prev === undefined ? 0 : Math.min(prev + 1, imageCount() - 1)) : null;
  }
  scenes.splice(pos, 0, { text: typeof text === "string" ? text : "", image_index: idx, place: "",
                          motion: "auto", transition: "auto", section: "" });
  state.selected = pos;
  renderScenes();
  focusScene(pos);
}

function duplicateScene(i) {
  const s = state.script.scenes[i];
  state.script.scenes.splice(i + 1, 0, Object.assign(JSON.parse(JSON.stringify(s)), { transition: "auto" }));
  state.selected = i + 1;
  renderScenes();
  focusScene(i + 1);
}

function splitScene(i, ta) {
  const s = state.script.scenes[i];
  const text = s.text;
  let cut = ta.selectionStart;
  if (!cut || cut >= text.length) {   // 커서가 처음·끝이면 가운데에서 가장 가까운 문장 끝
    const ends = [];
    const re = /[.!?。…]\s+/g;
    let m;
    while ((m = re.exec(text)) !== null) ends.push(m.index + m[0].length);
    if (!ends.length) return toast("자막에서 나눌 자리에 커서를 두고 다시 눌러 주세요.");
    cut = ends.reduce((a, b) => (Math.abs(b - text.length / 2) < Math.abs(a - text.length / 2) ? b : a));
  }
  const first = text.slice(0, cut).trim();
  const second = text.slice(cut).trim();
  if (!first || !second) return toast("자막에서 나눌 자리에 커서를 두고 다시 눌러 주세요.");
  s.text = first;
  state.script.scenes.splice(i + 1, 0, { text: second, image_index: s.image_index, place: s.place || "",
                                         motion: "auto", transition: "auto" });
  state.selected = i + 1;
  renderScenes();
  focusScene(i + 1);
}

// mode: set = 첫 사진 바꾸기, add = 이어서 보여 줄 사진 더하기
function openPicker(sceneIdx, mode) {
  state.pickerScene = sceneIdx;
  state.pickerMode = mode || "set";
  state.pickerFilter = "전체";
  const add = state.pickerMode === "add";
  $("picker-title").textContent = add ? `장면 ${sceneIdx + 1}에 사진 더하기` : `장면 ${sceneIdx + 1} 사진 고르기`;
  const box = $("picker-filter");
  box.innerHTML = "";
  const cats = [...new Set(Array.from({ length: imageCount() }, (_, i) => (photoInfo(i) || {}).category).filter(Boolean))];
  if (cats.length > 1) {
    ["전체"].concat(cats).forEach((c) => {
      const b = el("button", "chip" + ((state.pickerFilter || "전체") === c ? " on" : ""), c);
      b.type = "button";
      b.onclick = () => { state.pickerFilter = c; openPickerGrid(); box.querySelectorAll(".chip").forEach((x) => x.classList.toggle("on", x === b)); };
      box.appendChild(b);
    });
  } else {
    state.pickerFilter = "전체";
  }
  openPickerGrid();
  if (!$("picker").open) $("picker").showModal();
  const sel = $("picker-grid").querySelector(".selected");
  if (sel) sel.scrollIntoView({ block: "center" });
}

function openPickerGrid() {
  const scene = state.script.scenes[state.pickerScene];
  const add = state.pickerMode === "add";
  const current = scene.image_index;
  const used = usedCounts();
  const grid = $("picker-grid");
  grid.innerHTML = "";
  if (!add) {
    const none = document.createElement("button");
    none.type = "button";
    none.className = "photo none" + (current === null || current === undefined ? " selected" : "");
    none.textContent = "사진 없음";
    none.onclick = () => pick(null);
    grid.appendChild(none);
  }
  const filter = state.pickerFilter || "전체";
  for (let i = 0; i < imageCount(); i++) {
    const info = photoInfo(i);
    if (filter !== "전체" && (!info || info.category !== filter)) continue;
    const out = excluded().includes(i);
    const cell = el("div", "photo-cell");
    const tile = photoTile(i, { onPick: pick, selected: add ? photoList(scene).includes(i) : current === i,
                                used: used[i], category: info && info.category, excluded: out });
    if (info && info.description) tile.title = info.description;
    const x = el("button", "exclude", out ? "되살리기" : "빼기");
    x.type = "button";
    x.setAttribute("aria-label", `사진 ${i + 1} ${out ? "되살리기" : "영상에서 빼기"}`);
    x.onclick = () => toggleExclude(i);
    cell.append(tile, x);
    grid.appendChild(cell);
  }
}

function pick(idx) {
  const scene = state.script.scenes[state.pickerScene];
  if (idx !== null && excluded().includes(idx)) return toast("뺀 사진이에요. 쓰려면 아래의 '되살리기'를 먼저 눌러 주세요.");
  if (state.pickerMode === "add") {
    if (idx === null || photoList(scene).includes(idx)) return toast("이미 이 장면에 있는 사진이에요.");
    if (scene.image_index === null || scene.image_index === undefined) scene.image_index = idx;
    else {
      scene.more_images = (scene.more_images || []).concat([idx]);
      if ((scene.more_images || []).length >= 14) $("picker").close();
    }
    scene.photo_secs = [];
    openPickerGrid();
    renderScenes();
    return;
  }
  scene.image_index = idx;
  scene.photo_score = -1;   // 직접 고른 사진은 AI 점수를 지운다
  $("picker").close();
  renderScenes();
}

function toggleExclude(i) {
  const list = excluded();
  if (list.includes(i)) {
    state.script.excluded_images = list.filter((k) => k !== i);
  } else {
    const users = state.script.scenes.filter((s) => photoList(s).includes(i)).length;
    if (users && !confirm(`이 사진을 쓰는 장면 ${users}개에서 사진이 빠져요. 뺄까요?`)) return;
    state.script.scenes.forEach((s) => {
      if (!photoList(s).includes(i)) return;   // 이 사진을 안 쓰는 장면은 그대로
      s.more_images = (s.more_images || []).filter((k) => k !== i);
      if (s.image_index === i) {   // 빠진 자리는 이어지는 사진 → AI 추천 사진 → 없음 순서로 채운다
        const next = (s.more_images || []).shift();
        const alt = (s.photo_alts || []).find((k) => k !== i && !list.includes(k));
        s.image_index = next !== undefined ? next : alt !== undefined ? alt : null;
        s.photo_score = -1;
      }
      s.photo_secs = [];
    });
    state.script.excluded_images = list.concat([i]);
  }
  openPickerGrid();
  renderScenes();
}

// ---------- 사진 자동 맞추기 ----------
async function runMatch() {
  state.ai = { indexes: [], preset: null, items: [], mode: "photos" };
  $("ai-title").textContent = "사진 자동 맞추기";
  $("ai-ask").classList.add("hidden");
  $("ai-results").innerHTML = "";
  $("btn-ai-apply").classList.add("hidden");
  setError("ai-error", "");
  $("ai-wait").textContent = "AI가 장면마다 어울리는 사진을 찾고 있어요… (처음이면 사진 분석까지 1분쯤 걸려요)";
  $("ai-wait").classList.remove("hidden");
  $("ai-dialog").showModal();
  try {
    const map = serverIndexMap();
    await saveNow();
    const r = await api("POST", `/api/jobs/${state.job.id}/match-photos`);
    state.job = await api("GET", `/api/jobs/${state.job.id}`);   // 처음 분석했으면 사진 분류가 생김
    const all = r.matches.map((m) => Object.assign({}, m, { index: map[m.index] }));
    all.forEach((m) => {   // 점수와 추천은 모든 장면에 적어 둔다(바꾸지 않은 장면은 지금 사진의 점수)
      const s = state.script.scenes[m.index];
      if (s) { s.photo_score = m.current_score; s.photo_alts = m.alts; }
    });
    const items = all.filter((m) => m.best !== null && m.best !== m.current && m.score > m.current_score);
    state.ai.items = items;
    const list = $("ai-results");
    if (!items.length) {
      list.appendChild(el("li", "muted", "지금 사진이 모두 잘 맞아요. 바꿀 사진이 없어요."));
    }
    items.forEach((m, k) => {
      const li = el("li", "suggestion");
      const head = el("label", "suggestion-head");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = true;
      cb.dataset.k = k;
      head.append(cb, el("span", "", ` 장면 ${m.index + 1} · ${m.current_score}점 → ${m.score}점 · ${m.reason}`));
      const row = el("div", "swap");
      [m.current, m.best].forEach((p, n) => {
        if (n === 1) row.appendChild(el("span", "arrow", "→"));
        if (p === null || p === undefined) row.appendChild(el("span", "mini empty", "사진 없음"));
        else {
          const img = document.createElement("img");
          img.src = smallImageUrl(p);
          img.alt = `사진 ${p + 1}`;
          img.className = "mini";
          row.appendChild(img);
        }
      });
      const text = (state.script.scenes[m.index] || {}).text || "";
      li.append(head, el("div", "muted", text.slice(0, 60)), row);
      list.appendChild(li);
    });
    if (items.length) {
      $("btn-ai-apply").classList.remove("hidden");
      $("btn-ai-apply").textContent = "고른 사진으로 바꾸기";
    }
    state.opening = true;
    renderScenes();
    state.opening = false;
    state.dirty = true;
    saveNow().catch(() => {});
  } catch (e) {
    setError("ai-error", e.message);
  } finally {
    $("ai-wait").classList.add("hidden");
    $("ai-wait").textContent = "AI가 고치는 중이에요… (보통 5~20초)";
  }
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
                   motion: s.motion || "auto", transition: s.transition || "auto",
                   direction: (s.direction || "").trim(), direction_done: s.direction_done || "",
                   section: s.section || "",
                   look: s.look || "auto", caption_size: s.caption_size || "auto",
                   caption_pos: s.caption_pos || "auto", strength: s.strength || "auto", hold: s.hold || 0,
                   more_images: s.more_images || [], photo_secs: s.photo_secs || [],
                   photo_score: s.photo_score === undefined ? -1 : s.photo_score, photo_alts: s.photo_alts || [] }))
    .filter((s) => s.text);
  return {
    scenes,
    yt_title: $("yt-title").value.trim(),
    yt_description: $("yt-desc").value.trim(),
    yt_tags: $("yt-tags").value.split(",").map((t) => t.trim().replace(/^#/, "")).filter(Boolean),
    intro_title: $("intro-title").value.trim(),
    intro_sub: $("intro-sub").value.trim(),
    direction: $("direction").value.trim(),
    direction_done: state.script.direction_done || "",
    excluded_images: excluded(),
  };
}

function collectOptions() {
  return { aspect: segValue("aspect"), voice: $("voice").value || "female",
           speed: segValue("speed") || "normal", quality: segValue("quality") || "1080p",
           bgm: $("bgm-edit").value || null,
           template: segValue("template") || "clean", effects: segValue("effects") || "auto",
           intro: $("opt-intro").checked, outro: $("opt-outro").checked,
           title_bar: $("opt-title-bar").checked, preset: segValue("preset") || "custom" };
}

// ---------- 자동 저장 · 대본 확정 ----------
function markDirty() {
  if (state.opening || state.view !== "edit" || !state.job) return;
  state.dirty = true;
  showConfirm(false);
  showSaved("고치는 중…");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => saveNow().catch(() => {}), AUTOSAVE_MS);
}

function showSaved(text) {
  $("save-status").textContent = text;
}

function showConfirm(ok) {
  $("confirm-status").textContent = ok
    ? "✓ 대본을 확정했어요. 이 대본으로 영상을 만들어요."
    : "대본을 끝까지 확인한 뒤 '대본 확정'을 눌러 주세요. 확정한 뒤에 고치면 다시 확정해야 해요.";
  $("confirm-box").classList.toggle("done", !!ok);
  $("btn-confirm").disabled = !!ok;
  $("btn-render").textContent = ok ? "영상 만들기" : "대본 확정하고 영상 만들기";
}

// label: 있으면 바꾸기 전 대본을 버전으로 남긴다
async function saveNow(label) {
  clearTimeout(state.saveTimer);
  if (state.saving) await state.saving.catch(() => {});
  if (!state.dirty && !label) return state.job;
  state.dirty = false;
  const body = { script: collectScript(), options: collectOptions(), version_label: label || "" };
  showSaved("저장하는 중…");
  state.saving = api("PUT", `/api/jobs/${state.job.id}/script`, body);
  try {
    const job = await state.saving;
    state.job = job;
    showSaved("저장됨 ✓");
    showConfirm(job.is_confirmed);
    return job;
  } catch (e) {
    state.dirty = true;
    showSaved("저장하지 못했어요");
    throw e;
  } finally {
    state.saving = null;
  }
}

async function confirmScript() {
  setError("edit-error", "");
  const script = collectScript();
  if (!script.scenes.length) {
    setError("edit-error", "자막이 있는 장면이 하나 이상 있어야 해요.");
    return false;
  }
  try {
    state.dirty = true;
    await saveNow();
    state.job = await api("POST", `/api/jobs/${state.job.id}/confirm`);
    showConfirm(state.job.is_confirmed);
    return state.job.is_confirmed;
  } catch (e) {
    setError("edit-error", e.message);
    return false;
  }
}

async function startRender() {
  setError("edit-error", "");
  const btn = $("btn-render");
  btn.disabled = true;
  try {
    if (!(await confirmScript())) return;
    routeJob(await api("POST", `/api/jobs/${state.job.id}/render`));
  } catch (e) {
    setError("edit-error", e.message);
  } finally {
    btn.disabled = false;
  }
}

// ---------- 바뀐 부분 보여 주기 (낱말 단위) ----------
function diffWords(a, b) {
  const x = a.split(/(\s+)/).filter((w) => w !== "");
  const y = b.split(/(\s+)/).filter((w) => w !== "");
  const n = x.length, m = y.length;
  const L = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && x[i] === y[j]) { out.push(["same", x[i]]); i++; j++; }
    else if (j < m && (i >= n || L[i][j + 1] >= L[i + 1][j])) { out.push(["add", y[j]]); j++; }
    else { out.push(["del", x[i]]); i++; }
  }
  // 같은 종류가 이어지면 한 덩어리로 (낱말마다 따로 칠하지 않게)
  return out.reduce((acc, [k, w]) => {
    const last = acc[acc.length - 1];
    if (last && last[0] === k) last[1] += w; else acc.push([k, w]);
    return acc;
  }, []);
}

function diffView(before, after) {
  const box = el("div", "diff");
  const old = el("div", "diff-before");
  const neu = el("div", "diff-after");
  old.appendChild(el("b", "", "지금: "));
  neu.appendChild(el("b", "", "고친 글: "));
  diffWords(before, after).forEach(([k, w]) => {
    if (k !== "add") old.appendChild(k === "del" ? el("del", "", w) : document.createTextNode(w));
    if (k !== "del") neu.appendChild(k === "add" ? el("ins", "", w) : document.createTextNode(w));
  });
  box.append(old, neu);
  return box;
}

// 서버에는 글이 있는 장면만 저장된다. 화면의 장면 번호 → 서버 장면 번호
function serverIndexMap() {
  const m = [];
  state.script.scenes.forEach((s, i) => { if (s.text.trim()) m.push(i); });
  return m;
}

// ---------- AI로 고치기 (장면 하나 또는 전체) ----------
function openAiDialog(indexes) {
  state.ai = { indexes, preset: null, items: [] };
  const one = indexes.length === 1;
  $("ai-title").textContent = one ? `장면 ${indexes[0] + 1} AI로 고치기` : "AI로 전체 고치기";
  $("ai-target").textContent = one
    ? `"${state.script.scenes[indexes[0]].text.slice(0, 60)}"`
    : `장면 ${indexes.length}개를 모두 고쳐요. 말투나 길이를 한꺼번에 바꿀 때 좋아요.`;
  const box = $("ai-presets");
  box.innerHTML = "";
  AI_PRESETS.forEach(([key, label]) => {
    const b = el("button", "chip", label);
    b.type = "button";
    b.onclick = () => {
      state.ai.preset = state.ai.preset === key ? null : key;
      box.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b && state.ai.preset === key));
    };
    box.appendChild(b);
  });
  $("ai-command").value = "";
  $("ai-ask").classList.remove("hidden");
  $("ai-results").innerHTML = "";
  $("btn-ai-apply").classList.add("hidden");
  $("ai-wait").classList.add("hidden");
  setError("ai-error", "");
  $("ai-dialog").showModal();
}

async function runAi() {
  const a = state.ai;
  const command = $("ai-command").value.trim();
  if (!a.preset && !command) return setError("ai-error", "버튼을 하나 고르거나, 어떻게 고칠지 직접 적어 주세요.");
  setError("ai-error", "");
  $("btn-ai-run").disabled = true;
  $("ai-wait").classList.remove("hidden");
  try {
    const map = serverIndexMap();
    const indexes = a.indexes.map((i) => map.indexOf(i)).filter((k) => k >= 0);
    if (!indexes.length) throw new Error("글이 있는 장면만 고칠 수 있어요.");
    await saveNow();
    const r = await api("POST", `/api/jobs/${state.job.id}/ai-rewrite`, { indexes, preset: a.preset, command });
    showSuggestions(r.suggestions.map((s) => ({ index: map[s.index], before: s.before, after: s.after, text: s.after })),
                    "AI가 고칠 곳을 찾지 못했어요. 다른 명령으로 다시 해 보세요.");
  } catch (e) {
    setError("ai-error", e.message);
  } finally {
    $("btn-ai-run").disabled = false;
    $("ai-wait").classList.add("hidden");
  }
}

function showSuggestions(items, emptyText) {
  state.ai.items = items;
  const list = $("ai-results");
  list.innerHTML = "";
  if (!items.length) {
    list.appendChild(el("li", "muted", emptyText));
    $("btn-ai-apply").classList.add("hidden");
    return;
  }
  items.forEach((s, k) => {
    const li = el("li", "suggestion");
    const head = el("label", "suggestion-head");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.dataset.k = k;
    const sizes = s.reason ? "" : ` · ${s.before.length}자 → ${s.after.length}자`;
    head.append(cb, el("span", "", ` 장면 ${s.index + 1}` + (s.reason ? ` · ${s.reason}` : "") + sizes));
    li.append(head, diffView(s.before, s.after));
    list.appendChild(li);
  });
  $("btn-ai-apply").classList.remove("hidden");
  $("btn-ai-apply").textContent = items.length === 1 ? "바꾸기" : "고른 것 바꾸기";
}

async function applySuggestions() {
  const chosen = [...$("ai-results").querySelectorAll("input[type=checkbox]")]
    .filter((c) => c.checked).map((c) => state.ai.items[Number(c.dataset.k)]);
  if (!chosen.length) return setError("ai-error", "바꿀 것을 하나 이상 골라 주세요.");
  const review = state.ai.mode === "review";
  const scenes = state.script.scenes;
  if (state.ai.mode === "photos") {
    chosen.forEach((m) => {
      const sc = scenes[m.index];
      if (sc) { sc.image_index = m.best; sc.photo_score = m.score; }
    });
    $("ai-dialog").close();
    state.opening = true;
    renderScenes();
    state.opening = false;
    state.dirty = true;
    try {
      await saveNow("사진 바꾸기 전");
      toast(`장면 ${chosen.length}개의 사진을 바꿨어요.`);
    } catch (e) {
      setError("edit-error", e.message);
    }
    return;
  }
  chosen.forEach((s) => {
    const sc = scenes[s.index];
    if (!sc) return;
    // 맞춤법 제안은 같은 장면에 여러 개일 수 있어 글 전체가 아니라 그 부분만 바꾼다
    sc.text = review ? sc.text.replace(s.before, s.after) : s.text;
  });
  $("ai-dialog").close();
  state.opening = true;
  renderScenes();
  state.opening = false;
  state.dirty = true;
  try {
    await saveNow(review ? "맞춤법 고치기 전" : "AI로 고치기 전");
    toast(`${chosen.length}곳을 바꿨어요. 예전 글은 '버전 기록'에 남아 있어요.`);
  } catch (e) {
    setError("edit-error", e.message);
  }
}

async function runReview() {
  state.ai = { indexes: [], preset: null, items: [], mode: "review" };
  $("ai-title").textContent = "맞춤법·문맥 검사";
  $("ai-ask").classList.add("hidden");
  $("ai-results").innerHTML = "";
  $("btn-ai-apply").classList.add("hidden");
  setError("ai-error", "");
  $("ai-wait").textContent = "AI가 대본을 읽고 있어요… (보통 5~20초)";
  $("ai-wait").classList.remove("hidden");
  $("ai-dialog").showModal();
  try {
    const map = serverIndexMap();
    await saveNow();
    const r = await api("POST", `/api/jobs/${state.job.id}/ai-review`);
    showSuggestions(r.fixes.map((f) => Object.assign({}, f, { index: map[f.index] })), "고칠 곳을 찾지 못했어요. 잘 쓰인 대본이에요!");
  } catch (e) {
    setError("ai-error", e.message);
  } finally {
    $("ai-wait").classList.add("hidden");
    $("ai-wait").textContent = "AI가 고치는 중이에요… (보통 5~20초)";
  }
}

// ---------- 전체 대본 보기 ----------
function openOverview() {
  const list = $("overview-list");
  list.innerHTML = "";
  let chars = 0;
  state.script.scenes.forEach((s, i) => {
    chars += s.text.trim().length;
    const li = el("li");
    const b = el("button", "overview-item");
    b.type = "button";
    if (SECTION_LABEL[s.section]) b.appendChild(el("span", "section-tag", SECTION_LABEL[s.section]));
    b.appendChild(document.createTextNode(" " + (s.text || "(빈 장면)")));
    b.onclick = () => { $("overview-dialog").close(); selectScene(i); focusScene(i); };
    li.appendChild(b);
    list.appendChild(li);
  });
  $("overview-summary").textContent = `장면 ${state.script.scenes.length}개 · ${chars}자 · ` +
    $("scene-summary").textContent.replace(/^장면 \d+개 · /, "").replace(/ \(.*\)$/, "") +
    " · 문장을 누르면 그 장면으로 가요.";
  $("overview-dialog").showModal();
}

// ---------- 버전 기록 ----------
async function openVersions() {
  setError("versions-error", "");
  $("versions-dialog").showModal();
  await loadVersions();
}

async function loadVersions() {
  const list = $("versions-list");
  list.innerHTML = "";
  try {
    await saveNow();
    const r = await api("GET", `/api/jobs/${state.job.id}/versions`);
    if (!r.versions.length) list.appendChild(el("li", "muted", "아직 남은 버전이 없어요."));
    r.versions.forEach((v) => {
      const li = el("li", "version");
      const when = new Date(v.time * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric",
                                                                       hour: "2-digit", minute: "2-digit" });
      li.appendChild(el("div", "", `${v.label} · ${when}`));
      li.appendChild(el("div", "muted", `장면 ${v.scenes}개 · ${v.chars}자 · "${v.first}"`));
      const b = el("button", "ghost", "이 버전으로 되돌리기");
      b.type = "button";
      b.onclick = () => restoreVersion(v.n);
      li.appendChild(b);
      list.appendChild(li);
    });
  } catch (e) {
    setError("versions-error", e.message);
  }
}

async function saveVersion() {
  try {
    await saveNow();
    await api("POST", `/api/jobs/${state.job.id}/versions`, { label: "직접 남긴 버전" });
    toast("지금 대본을 버전으로 남겼어요.");
    await loadVersions();
  } catch (e) {
    setError("versions-error", e.message);
  }
}

async function restoreVersion(n) {
  if (!confirm("이 버전으로 되돌릴까요? 지금 대본은 '되돌리기 전' 버전으로 남아요.")) return;
  try {
    await saveNow();
    const job = await api("POST", `/api/jobs/${state.job.id}/versions/${n}/restore`);
    $("versions-dialog").close();
    state.job = job;
    openEdit(job);
    toast("예전 버전으로 되돌렸어요.");
  } catch (e) {
    setError("versions-error", e.message);
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
  const info = job.render_info;
  const reused = info && info.reused;
  $("render-info").textContent = info
    ? (reused ? `지난번과 같은 장면 ${info.reused}개는 다시 쓰고, 바뀐 장면 ${info.clips - info.reused}개만 새로 만들었어요. ` : "")
      + `걸린 시간 ${Math.max(1, Math.round(info.seconds))}초`
    : "";
  $("render-info").classList.toggle("hidden", !info);
  renderYoutube(job.youtube);
  renderShortsBox(job);
  $("check-list").innerHTML = "";
  $("check-summary").classList.add("hidden");
  setError("check-error", "");
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
    renderModels(s.models);
    $("channel-name").value = s.channel_name || "";
  } catch (e) {
    setError("settings-error", e.message);
  }
  $("api-key").value = "";
  $("channel-ok").classList.add("hidden");
  $("models-ok").classList.add("hidden");
  $("model-check").classList.add("hidden");
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

// ---------- AI 모델 ----------
function renderModels(m) {
  state.models = m;
  const list = $("model-list");
  list.innerHTML = "";
  m.tasks.forEach((t) => {
    const row = document.createElement("label");
    row.className = "model-row";
    const name = document.createElement("span");
    name.innerHTML = "";
    const b = document.createElement("b");
    b.textContent = t.label;
    const help = document.createElement("small");
    help.className = "muted";
    help.textContent = t.help;
    name.append(b, help);
    const sel = document.createElement("select");
    sel.dataset.task = t.key;
    m.choices.forEach((c) => {
      const label = `${c.label} (${c.price})` + (c.id === t.default ? " · 추천" : "");
      sel.appendChild(new Option(label, c.id));
    });
    sel.value = m.current[t.key];
    sel.setAttribute("aria-label", `${t.label} 모델`);
    row.append(name, sel);
    list.appendChild(row);
  });
}

function chosenModels() {
  const out = {};
  document.querySelectorAll("#model-list select").forEach((s) => { out[s.dataset.task] = s.value; });
  return out;
}

async function saveModels() {
  setError("settings-error", "");
  try {
    const s = await api("PUT", "/api/settings", { models: chosenModels() });
    renderModels(s.models);
    $("models-ok").classList.remove("hidden");
  } catch (e) {
    setError("settings-error", e.message);
  }
}

async function checkModels() {
  setError("settings-error", "");
  const btn = $("btn-check-models");
  const out = $("model-check");
  btn.disabled = true;
  btn.textContent = "확인하는 중…";
  out.innerHTML = "";
  try {
    await api("PUT", "/api/settings", { models: chosenModels() });   // 고른 그대로 확인
    const results = await api("POST", "/api/settings/check-models");
    results.forEach((r) => {
      const li = document.createElement("li");
      li.className = r.ok ? "ok" : "bad";
      li.textContent = `${r.ok ? "✓" : "✕"} ${r.label} — ${r.message} (쓰는 곳: ${r.tasks.join(", ")})`;
      out.appendChild(li);
    });
    out.classList.remove("hidden");
  } catch (e) {
    setError("settings-error", e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "연결 확인";
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
    $("more-urls").value = "";
  }
  setError("home-error", "");
  show("home");
  loadProjects();
}

// ---------- 유튜브 글 ----------
function copyRow(text, extraLabel, extraFn) {
  const li = el("li", "copy-row");
  li.appendChild(el("span", "", text));
  const b = el("button", "ghost small", "복사");
  b.type = "button";
  b.onclick = async () => toast((await copyText(text)) ? "복사했어요" : "복사하지 못했어요.");
  li.appendChild(b);
  if (extraLabel) {
    const x = el("button", "ghost small", extraLabel);
    x.type = "button";
    x.onclick = () => extraFn(text);
    li.appendChild(x);
  }
  return li;
}

function renderYoutube(y) {
  $("yt-result").classList.toggle("hidden", !y);
  setError("yt-error", "");
  if (!y) return;
  $("yt-titles").innerHTML = "";
  y.titles.forEach((t) => $("yt-titles").appendChild(copyRow(t)));
  $("yt-desc-out").textContent = y.description;
  $("yt-hashtags").textContent = y.hashtag_text || y.hashtags.map((t) => "#" + t).join(" ");
  $("yt-keywords").textContent = y.keywords.join(", ");
  $("yt-thumbs").innerHTML = "";
  y.thumbnail_texts.forEach((t) => $("yt-thumbs").appendChild(copyRow(t, "이 문구로 썸네일 만들기", remakeThumb)));
}

async function makeYoutube() {
  setError("yt-error", "");
  $("btn-yt").disabled = true;
  $("yt-wait").classList.remove("hidden");
  try {
    const y = await api("POST", `/api/jobs/${state.job.id}/youtube`);
    state.job.youtube = y;
    renderYoutube(y);
  } catch (e) {
    setError("yt-error", e.message);
  } finally {
    $("btn-yt").disabled = false;
    $("yt-wait").classList.add("hidden");
  }
}

async function remakeThumb(text) {
  try {
    const job = await api("POST", `/api/jobs/${state.job.id}/thumbnail`, { text });
    state.job = job;
    $("thumb-img").src = `/api/jobs/${job.id}/thumbnail?v=${job.finished_at}`;
    $("btn-thumb").href = `/api/jobs/${job.id}/thumbnail?download=1`;
    $("thumb-box").classList.remove("hidden");
    $("thumb-box").scrollIntoView({ block: "center" });
    toast("썸네일을 다시 만들었어요.");
  } catch (e) {
    setError("yt-error", e.message);
  }
}

// ---------- 쇼츠 ----------
function renderShortsBox(job) {
  setError("shorts-error", job.shorts_error || "");
  const has = !!job.has_shorts;
  $("shorts-result").classList.toggle("hidden", !has);
  if (has) {
    const v = `/api/jobs/${job.id}/shorts/video?v=${job.finished_at || job.created_at}#t=0.5`;
    if ($("shorts-video").dataset.src !== v) {
      $("shorts-video").src = v;
      $("shorts-video").dataset.src = v;
    }
    $("btn-shorts-download").href = `/api/jobs/${job.id}/shorts/video?download=1`;
  }
  state.shorts = job.shorts_script ? JSON.parse(JSON.stringify(job.shorts_script)) : null;
  $("btn-shorts-plan").textContent = state.shorts ? "쇼츠 대본 새로 만들기" : "쇼츠 대본 만들기";
  renderShortsEditor(!has && !!state.shorts);
}

function renderShortsEditor(open) {
  $("shorts-edit").classList.toggle("hidden", !open || !state.shorts);
  if (!state.shorts) return;
  const list = $("shorts-scenes");
  list.innerHTML = "";
  state.shorts.scenes.forEach((s, i) => {
    const li = el("li", "short-scene");
    if (s.image_index !== null && s.image_index !== undefined) {
      const img = document.createElement("img");
      img.className = "mini";
      img.src = smallImageUrl(s.image_index);
      img.alt = "";
      li.appendChild(img);
    }
    const ta = document.createElement("textarea");
    ta.rows = 2;
    ta.value = s.text;
    ta.setAttribute("aria-label", `쇼츠 장면 ${i + 1} 자막`);
    ta.addEventListener("input", () => { s.text = ta.value; shortsSummary(); });
    const x = el("button", "ghost small", "빼기");
    x.type = "button";
    x.disabled = state.shorts.scenes.length <= 1;
    x.onclick = () => { state.shorts.scenes.splice(i, 1); renderShortsEditor(true); };
    li.append(ta, x);
    list.appendChild(li);
  });
  shortsSummary();
}

function shortsSummary() {
  const scenes = state.shorts.scenes.filter((s) => s.text.trim());
  const chars = scenes.reduce((a, s) => a + s.text.trim().length, 0);
  const sec = Math.round(chars / 5.5 + scenes.length * 0.4);
  $("shorts-summary").textContent = `장면 ${scenes.length}개 · ${chars}자 · 약 ${sec}초` + (sec > 60 ? " (60초가 넘어요. 글을 줄여 주세요)" : "");
}

async function planShorts() {
  setError("shorts-error", "");
  $("btn-shorts-plan").disabled = true;
  $("shorts-wait").classList.remove("hidden");
  try {
    const r = await api("POST", `/api/jobs/${state.job.id}/shorts/plan`);
    state.shorts = r.script;
    state.job.shorts_script = r.script;
    $("shorts-result").classList.add("hidden");
    renderShortsEditor(true);
  } catch (e) {
    setError("shorts-error", e.message);
  } finally {
    $("btn-shorts-plan").disabled = false;
    $("shorts-wait").classList.add("hidden");
  }
}

async function renderShortsVideo() {
  setError("shorts-error", "");
  try {
    routeJob(await api("POST", `/api/jobs/${state.job.id}/shorts`, { script: state.shorts }));
  } catch (e) {
    setError("shorts-error", e.message);
  }
}

// ---------- 영상 검수 ----------
const CHECK_ICON = { warn: "⚠", typo: "✎", info: "ⓘ" };

async function runCheckup() {
  setError("check-error", "");
  $("btn-checkup").disabled = true;
  $("check-wait").classList.remove("hidden");
  const list = $("check-list");
  list.innerHTML = "";
  try {
    const r = await api("POST", `/api/jobs/${state.job.id}/checkup`);
    const c = r.counts;
    $("check-summary").textContent = r.items.length
      ? `꼭 볼 것 ${c.warn}개 · 오탈자 ${c.typo}개 · 참고 ${c.info}개`
      : "문제를 찾지 못했어요. 그대로 올려도 좋아요!";
    $("check-summary").classList.remove("hidden");
    r.items.forEach((x) => {
      const li = el("li", "check " + x.level);
      li.appendChild(el("span", "icon", CHECK_ICON[x.level] || "•"));
      li.appendChild(el("span", "", (x.scene >= 0 ? `장면 ${x.scene + 1}: ` : "") + x.text));
      if (x.scene >= 0) {
        const b = el("button", "ghost small", "고치러 가기");
        b.type = "button";
        b.onclick = () => {
          openEdit(state.job);
          selectScene(x.scene);
          focusScene(x.scene);
        };
        li.appendChild(b);
      }
      list.appendChild(li);
    });
  } catch (e) {
    setError("check-error", e.message);
  } finally {
    $("btn-checkup").disabled = false;
    $("check-wait").classList.add("hidden");
  }
}

// ---------- 내 프로젝트 ----------
const PROJECT_STAGE = { loaded: "글 불러옴", ready: "대본 고치는 중", done: "영상 완성", error: "멈춤·오류" };

async function loadProjects() {
  let jobs = [];
  try {
    jobs = (await api("GET", "/api/jobs")).jobs;
  } catch (e) {
    return;
  }
  const list = $("project-list");
  list.innerHTML = "";
  $("projects").classList.toggle("hidden", !jobs.length);
  jobs.forEach((j) => {
    const li = el("li", "project");
    if (j.photos) {
      const img = document.createElement("img");
      img.className = "project-thumb";
      img.src = `/api/jobs/${j.id}/image/0?small=1`;
      img.alt = "";
      img.loading = "lazy";
      li.appendChild(img);
    }
    const open = el("button", "project-open");
    open.type = "button";
    open.appendChild(el("b", "", j.name || "(이름 없음)"));
    const when = new Date((j.finished_at || j.created_at) * 1000).toLocaleDateString("ko-KR",
      { month: "numeric", day: "numeric" });
    const bits = [PROJECT_STAGE[j.stage] || STAGE_LABEL[j.stage] || j.stage, when];
    if (j.posts > 1) bits.push(`글 ${j.posts}편`);
    if (j.scenes) bits.push(`장면 ${j.scenes}개`);
    open.appendChild(el("span", "muted", bits.join(" · ")));
    open.onclick = () => openProject(j.id);
    const tools = el("div", "project-tools");
    const rename = el("button", "ghost small", "이름 바꾸기");
    rename.type = "button";
    rename.onclick = () => renameProject(j);
    const del = el("button", "ghost small danger", "삭제");
    del.type = "button";
    del.onclick = () => deleteProject(j);
    tools.append(rename, del);
    li.append(open, tools);
    list.appendChild(li);
  });
}

async function openProject(id) {
  try {
    state.job = { id };
    routeJob(await api("GET", `/api/jobs/${id}`));
  } catch (e) {
    setError("home-error", e.message);
  }
}

async function renameProject(j) {
  const name = prompt("새 이름을 적어 주세요.", j.name || "");
  if (name === null || !name.trim()) return;
  try {
    await api("PATCH", `/api/jobs/${j.id}`, { name: name.trim() });
    loadProjects();
  } catch (e) {
    setError("home-error", e.message);
  }
}

async function deleteProject(j) {
  if (!confirm(`'${j.name}' 프로젝트를 지울까요? 대본과 영상이 모두 지워지고 되돌릴 수 없어요.`)) return;
  try {
    await api("DELETE", `/api/jobs/${j.id}`);
    loadProjects();
  } catch (e) {
    setError("home-error", e.message);
  }
}

// ---------- 불러온 글 목록, 다시 불러오기, 사진 올리기 ----------
const SOURCE_STATUS = { ok: "불러옴", error: "못 불러옴", duplicate: "같은 글이라 뺌" };

function renderSources(job) {
  const list = $("source-list");
  list.innerHTML = "";
  const sources = job.sources || [];
  sources.forEach((x, k) => {
    const li = el("li", "source " + x.status);
    li.appendChild(el("span", "source-status " + x.status, SOURCE_STATUS[x.status] || x.status));
    li.appendChild(el("b", "", ` ${k + 1}. ${x.title || x.url}`));
    if (x.status === "ok") {
      const extra = [`문단 ${x.paragraphs}`, `사진 ${x.photos}장`];
      if (x.missing) extra.push(`못 받은 사진 ${x.missing}장`);
      if (x.duplicates) extra.push(`겹친 사진 ${x.duplicates}장 뺌`);
      li.appendChild(el("div", "muted", extra.join(" · ")));
    } else {
      li.appendChild(el("div", "muted", x.message || ""));
    }
    list.appendChild(li);
  });
  const failed = sources.filter((x) => x.status === "error").length;
  $("btn-retry-sources").classList.toggle("hidden", !failed);
  const photos = (job.post && job.post.images.length) || 0;
  $("sources-summary").textContent = `불러온 글 ${sources.filter((x) => x.status === "ok").length || 1}편 · 사진 ${photos}장`
    + (failed ? ` · 못 불러온 글 ${failed}편` : "");
  $("sources-panel").open = failed > 0;
  setError("sources-error", "");
}

async function retrySources() {
  try {
    await saveNow();
    routeJob(await api("POST", `/api/jobs/${state.job.id}/sources/retry`));
  } catch (e) {
    setError("sources-error", e.message);
  }
}

async function uploadPhotos() {
  const files = [...$("photo-upload").files];
  $("photo-upload").value = "";
  if (!files.length) return;
  setError("sources-error", "");
  let added = 0;
  const errors = [];
  for (const f of files) {
    const form = new FormData();
    form.append("file", f);
    try {
      const r = await fetch(`/api/jobs/${state.job.id}/photos`, { method: "POST", body: form });
      const data = await r.json();
      if (!r.ok) throw new Error(data.message || "사진을 올리지 못했어요.");
      state.job.post = data.post;
      added += 1;
    } catch (e) {
      errors.push(`${f.name}: ${e.message}`);
    }
  }
  if (errors.length) setError("sources-error", errors.join(" / "));
  if (added) {
    renderSources(state.job);
    state.opening = true;
    renderScenes();
    state.opening = false;
    toast(`사진 ${added}장을 올렸어요. 장면의 사진을 눌러 고를 수 있어요.`);
  }
}

$("s-ratio").addEventListener("input", showRatio);
$("btn-voice-play").onclick = previewVoice;
$("btn-bgm-play").onclick = previewBgm;
$("btn-yt-music").onclick = fetchYoutubeMusic;
$("bgm-upload").addEventListener("change", uploadBgmFile);
$("yt-url").addEventListener("keydown", (e) => { if (e.key === "Enter") fetchYoutubeMusic(); });
$("bgm-edit").addEventListener("change", () => { $("bgm-audio").pause(); });
$("voice").addEventListener("change", () => { $("voice-audio").pause(); });
document.querySelector('#view-edit .seg[data-name="speed"]').addEventListener("click", () => updateSummary());
["opt-intro", "opt-outro"].forEach((id) => $(id).addEventListener("change", () => updateSummary()));
$("btn-cancel").onclick = cancelJob;
$("btn-load").onclick = () => startLoad("api");
$("btn-text").onclick = () => startLoad("text");
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") startLoad("text"); });   // 엔터는 무료인 쪽
$("url").addEventListener("input", () => setError("home-error", ""));   // 주소를 넣으면 '주소를 넣어 주세요'를 지운다
$("btn-recommend").onclick = () => writeScript("api");
$("btn-text-source").onclick = () => writeScript("text");
$("btn-source-back").onclick = () => goHome(true);
$("btn-rewrite").onclick = () => {
  state.job.script = collectScript();   // 지금 장면 수를 기준으로 덮어쓰기 전에 한 번 물어본다
  openSource(state.job);
};
$("btn-render").onclick = startRender;
$("btn-yt").onclick = makeYoutube;
$("btn-shorts-plan").onclick = planShorts;
$("btn-shorts-render").onclick = renderShortsVideo;
$("btn-checkup").onclick = runCheckup;
$("btn-more-urls").onclick = () => {
  $("more-urls-box").classList.remove("hidden");
  $("btn-more-urls").classList.add("hidden");
  $("more-urls").focus();
};
$("btn-retry-sources").onclick = retrySources;
$("photo-upload").addEventListener("change", uploadPhotos);
$("btn-confirm").onclick = confirmScript;
$("btn-overview").onclick = openOverview;
$("btn-overview-close").onclick = () => $("overview-dialog").close();
$("btn-ai-all").onclick = () => openAiDialog(state.script.scenes.map((_, i) => i));
$("btn-ai-run").onclick = runAi;
$("btn-ai-apply").onclick = applySuggestions;
$("btn-ai-close").onclick = () => $("ai-dialog").close();
$("ai-command").addEventListener("keydown", (e) => { if (e.key === "Enter") runAi(); });
$("btn-review").onclick = runReview;
$("btn-match").onclick = runMatch;
$("btn-versions").onclick = openVersions;
$("btn-versions-close").onclick = () => $("versions-dialog").close();
$("btn-version-save").onclick = saveVersion;
// 편집 화면에서 무엇이든 바꾸면 잠시 뒤 저절로 저장
["input", "change"].forEach((ev) => $("view-edit").addEventListener(ev, (e) => {
  if (e.target.closest && e.target.closest("#yt-url, #bgm-upload, #photo-upload")) return;
  markDirty();
}));
$("view-edit").addEventListener("click", (e) => {
  if (e.target.closest && e.target.closest(".seg button")) {
    renderPreview();
    if (!e.target.closest("#preset-list")) presetStillMatches();
    markDirty();
  }
});
$("opt-title-bar").addEventListener("change", presetStillMatches);
$("opt-title-bar").addEventListener("change", renderPreview);
window.addEventListener("beforeunload", () => { if (state.dirty) saveNow().catch(() => {}); });
$("btn-add-scene").onclick = () => addScene("", null, state.script.scenes.length);   // 맨 아래에
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
$("btn-save-models").onclick = saveModels;
$("btn-check-models").onclick = checkModels;
$("btn-save-channel").onclick = saveChannelName;
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
    loadScriptSettings();
    await Promise.all([loadInfo(), loadMusic(), loadVoices(), loadPresets()]);
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
  loadProjects();
})();
