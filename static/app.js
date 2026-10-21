/* ==========================================================================
   iCloudPD Studio — frontend
   ========================================================================== */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const state = {
    boot: null,
    config: {},
    engine: {},
    accounts: [],
    stats: {},
    jobs: [],
    activeJobId: null,
    selectedJobId: null,
    jobDetailLoaded: false,
    jobLogLast: 0,
    logCount: 0,
    promptSignature: "",
    manualPromptJobId: null,
    pollTimer: null,
    pollBoostUntil: 0,
    notifiedJobs: new Set(),
    formReady: false,
    pendingPreset: null,
    selectedAlbums: new Set(),
    albums: [],
    folderTarget: "download",
    folder: { path: "", parent: null, entries: [], shortcuts: [] },
    gallery: {
      items: [],
      total: 0,
      offset: 0,
      kind: "all",
      q: "",
      sort: "new",
      hasMore: false,
      loading: false,
      loaded: false,
      forceRefresh: false,
      requestId: 0,
    },
    lightboxIndex: 0,
    themeChoice: localStorage.getItem("icloudpd-studio-theme") || "system",
    activeView: "dashboard",
  };

  const THEME_KEY = "icloudpd-studio-theme";
  const ROUTES = {
    dashboard: { root: "工作台", page: "概览", title: "iCloudPD Studio · 本地影像归档" },
    download: { root: "工作台", page: "同步下载", title: "同步下载 · iCloudPD Studio" },
    jobs: { root: "工作台", page: "任务记录", title: "任务记录 · iCloudPD Studio" },
    gallery: { root: "工作台", page: "照片图库", title: "照片图库 · iCloudPD Studio" },
    settings: { root: "系统", page: "设置", title: "设置 · iCloudPD Studio" },
  };

  const ICON = {
    cloud: '<svg><use href="#i-cloud-dl"/></svg>',
    list: '<svg><use href="#i-list"/></svg>',
    image: '<svg><use href="#i-image"/></svg>',
    folder: '<svg><use href="#i-folder"/></svg>',
    check: '<svg><use href="#i-check"/></svg>',
    x: '<svg><use href="#i-x"/></svg>',
    alert: '<svg><use href="#i-alert"/></svg>',
    info: '<svg><use href="#i-info"/></svg>',
    play: '<svg><use href="#i-play"/></svg>',
    film: '<svg><use href="#i-film"/></svg>',
    photo: '<svg><use href="#i-photo"/></svg>',
    key: '<svg><use href="#i-key"/></svg>',
    login: '<svg><use href="#i-login"/></svg>',
    refresh: '<svg><use href="#i-refresh"/></svg>',
    arrow: '<svg><use href="#i-arrow-right"/></svg>',
    external: '<svg><use href="#i-external"/></svg>',
  };

  /* ------------------------------------------------------------------ */
  /* Utilities                                                          */
  /* ------------------------------------------------------------------ */

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  function number(value) {
    const n = Number(value || 0);
    return new Intl.NumberFormat("zh-CN").format(n);
  }

  function bytes(value) {
    let n = Number(value || 0);
    if (!Number.isFinite(n) || n <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) {
      n /= 1024;
      i += 1;
    }
    return `${i === 0 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
  }

  function basename(path) {
    const clean = String(path || "").replace(/[\\/]+$/, "");
    if (!clean) return "";
    const parts = clean.split(/[\\/]/);
    return parts[parts.length - 1] || clean;
  }

  function relativeTime(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    const diff = Date.now() - date.getTime();
    if (diff < 45_000) return "刚刚";
    if (diff < 3_600_000) return `${Math.max(1, Math.round(diff / 60_000))} 分钟前`;
    if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)} 小时前`;
    if (diff < 7 * 86_400_000) return `${Math.round(diff / 86_400_000)} 天前`;
    return date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
  }

  function formatDateTime(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatClock(iso) {
    if (!iso) return "--:--:--";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "--:--:--";
    return date.toLocaleTimeString("zh-CN", { hour12: false });
  }

  function formatDuration(startIso, endIso) {
    if (!startIso) return "—";
    const start = new Date(startIso).getTime();
    const end = endIso ? new Date(endIso).getTime() : Date.now();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
    const sec = Math.floor((end - start) / 1000);
    if (sec < 60) return `${sec}s`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ${sec % 60}s`;
    return `${Math.floor(min / 60)}h ${min % 60}m`;
  }

  function accountKey(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  }

  async function api(path, options = {}) {
    const opts = { ...options, headers: { ...(options.headers || {}) } };
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
      opts.headers["Content-Type"] = "application/json";
    }
    const res = await fetch(path, opts);
    let data = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    if (!res.ok || (data && data.ok === false)) {
      const message = (data && (data.error || data.message)) || `请求失败（HTTP ${res.status}）`;
      throw new Error(message);
    }
    return data || {};
  }

  function debounce(fn, wait = 250) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  }

  function toast(title, message = "", type = "success", timeout = 4200) {
    const root = $("#toastRoot");
    if (!root) return;
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    const icon = type === "error" ? ICON.x : type === "warn" ? ICON.alert : ICON.check;
    el.innerHTML = `
      <span class="toast-icon">${icon}</span>
      <span class="toast-copy"><strong>${esc(title)}</strong>${message ? `<span>${esc(message)}</span>` : ""}</span>
      <button class="toast-close" type="button" aria-label="关闭">×</button>`;
    root.appendChild(el);
    const remove = () => {
      el.classList.add("out");
      setTimeout(() => el.remove(), 220);
    };
    el.querySelector(".toast-close").addEventListener("click", remove);
    setTimeout(remove, timeout);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      try {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        const ok = document.execCommand("copy");
        area.remove();
        return ok;
      } catch {
        return false;
      }
    }
  }

  function confirmAction({ title, text, okLabel = "确认", danger = true }) {
    return new Promise((resolve) => {
      const dialog = $("#confirmDialog");
      if (!dialog) return resolve(false);
      $("#confirmTitle").textContent = title || "确认操作";
      $("#confirmText").textContent = text || "";
      const ok = $("#confirmOk");
      const cancel = $("#confirmCancel");
      ok.textContent = okLabel;
      ok.className = `button ${danger ? "danger" : "primary"}`;
      const finish = (value) => {
        ok.onclick = null;
        cancel.onclick = null;
        dialog.onclose = null;
        if (dialog.open) dialog.close();
        resolve(value);
      };
      ok.onclick = () => finish(true);
      cancel.onclick = () => finish(false);
      dialog.onclose = () => finish(false);
      dialog.showModal();
    });
  }

  /* ------------------------------------------------------------------ */
  /* Theme                                                              */
  /* ------------------------------------------------------------------ */

  function resolveTheme(choice) {
    if (choice === "dark") return "dark";
    if (choice === "light") return "light";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(choice, persist = true) {
    state.themeChoice = choice;
    if (persist) localStorage.setItem(THEME_KEY, choice);
    const resolved = resolveTheme(choice);
    document.documentElement.dataset.theme = resolved;
    $$("#themeOptions button").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.themeChoice === choice);
    });
  }

  function toggleTheme() {
    const current = resolveTheme(state.themeChoice);
    applyTheme(current === "dark" ? "light" : "dark");
  }

  /* ------------------------------------------------------------------ */
  /* Navigation                                                         */
  /* ------------------------------------------------------------------ */

  function viewFromHash() {
    const raw = (location.hash || "#/dashboard").replace(/^#\/?/, "").split("?")[0];
    return ROUTES[raw] ? raw : "dashboard";
  }

  function navigate(view) {
    if (!ROUTES[view]) view = "dashboard";
    if (location.hash !== `#/${view}`) {
      location.hash = `#/${view}`;
    } else {
      renderRoute(view);
    }
  }

  function renderRoute(view) {
    state.activeView = view;
    $$(".view").forEach((section) => {
      section.hidden = section.dataset.view !== view;
    });
    $$(".nav-link").forEach((link) => {
      link.classList.toggle("active", link.dataset.route === view);
    });
    const route = ROUTES[view] || ROUTES.dashboard;
    $("#crumbRoot").textContent = route.root;
    $("#crumbPage").textContent = route.page;
    document.title = route.title;
    closeSidebar();
    $("#pageScroll").scrollTop = 0;

    if (view === "download") {
      ensureDownloadForm();
      applyPreset();
      updateSummary();
      updateFreeSpace($("#downloadDir").value);
    } else if (view === "jobs") {
      renderJobList();
      if (!state.selectedJobId && state.jobs.length) {
        const preferred = state.jobs.find((job) => job.status === "running") || state.jobs[0];
        selectJob(preferred.id, { force: true });
      } else if (state.selectedJobId) {
        selectJob(state.selectedJobId, { quiet: true });
      }
    } else if (view === "gallery") {
      if (!state.gallery.loaded) {
        loadGallery(true);
      } else {
        renderGallery();
      }
    } else if (view === "settings") {
      fillSettings();
    } else if (view === "dashboard") {
      renderDashboard();
    }
  }

  function closeSidebar() {
    $("#sidebar")?.classList.remove("open");
    $("#sidebarScrim")?.classList.remove("open");
  }

  function toggleSidebar() {
    $("#sidebar")?.classList.toggle("open");
    $("#sidebarScrim")?.classList.toggle("open");
  }

  /* ------------------------------------------------------------------ */
  /* Bootstrap / account / engine                                       */
  /* ------------------------------------------------------------------ */

  async function refreshBootstrap({ preserveForm = true, silent = false } = {}) {
    try {
      const data = await api("/api/bootstrap");
      applyBootstrap(data, { preserveForm });
      if (!silent) updateAll();
      return data;
    } catch (error) {
      if (!silent) toast("无法刷新数据", error.message, "error");
      throw error;
    }
  }

  function applyBootstrap(data, { preserveForm = true } = {}) {
    state.boot = data;
    state.config = data.config || {};
    state.engine = data.engine || {};
    state.accounts = data.accounts || [];
    state.stats = data.stats || {};
    state.activeJobId = data.active_job_id || null;
    if (Array.isArray(data.jobs)) {
      state.jobs = data.jobs;
    }
    if (!preserveForm || !state.formReady) {
      state.formReady = false;
    }
    renderEngine();
    renderSession();
    renderStats();
    if (!preserveForm) {
      fillDownloadForm();
    }
    renderRecentDirs();
  }

  function updateAll() {
    renderEngine();
    renderSession();
    renderStats();
    renderDashboard();
    renderJobList();
    updateNavCount();
    if (state.activeView === "settings") fillSettings();
    if (state.activeView === "gallery") renderGalleryStats();
  }

  function renderEngine() {
    const engine = state.engine || {};
    const ok = Boolean(engine.available);
    const dot = $("#engineDot");
    if (dot) dot.className = `engine-dot ${ok ? "ok" : "bad"}`;
    const mini = $("#engineMiniText");
    if (mini) mini.textContent = ok ? "icloudpd 就绪" : "未检测到 icloudpd";
    const version = $("#engineMiniVersion");
    if (version) version.textContent = engine.version ? `v${engine.version}` : "";

    const chip = $("#sessionChip");
    const chipText = $("#sessionChipText");
    if (!ok) {
      chip?.classList.remove("ok", "warn");
      chip?.classList.add("bad");
      if (chipText) chipText.textContent = "引擎缺失";
    }

    const stat = $("#statEngine");
    const sub = $("#statEngineSub");
    if (stat) stat.textContent = ok ? (engine.version ? `v${engine.version}` : "已就绪") : "不可用";
    if (sub) {
      sub.textContent = ok ? "icloudpd 命令行引擎" : (engine.error ? String(engine.error).slice(0, 80) : "请先安装 icloudpd");
    }
    renderEngineDetails();
  }

  function renderSession() {
    const formValue = $("#username")?.value.trim() || "";
    const username = (formValue || state.config?.username || "").trim();
    const matched = state.accounts.find((item) => item.key === accountKey(username)) || null;
    const hasSession = Boolean(matched && (matched.has_session || matched.has_cookie));

    const chip = $("#sessionChip");
    const chipText = $("#sessionChipText");
    if (chip && state.engine?.available) {
      if (hasSession) {
        chip.classList.remove("ok", "bad");
        chip.classList.add("warn");
        if (chipText) chipText.textContent = "会话已保存";
      } else if (username) {
        chip.classList.remove("ok", "bad");
        chip.classList.add("warn");
        if (chipText) chipText.textContent = "会话待验证";
      } else {
        chip.classList.remove("ok", "warn", "bad");
        if (chipText) chipText.textContent = "尚未登录";
      }
    }

    const sideName = $("#sideAccountName");
    const sideMeta = $("#sideAccountMeta");
    if (sideName) sideName.textContent = username || "尚未登录";
    if (sideMeta) {
      sideMeta.textContent = hasSession
        ? `会话已保存${matched.updated_at ? ` · ${relativeTime(matched.updated_at)}` : ""}`
        : username
          ? "点击重新验证 iCloud"
          : "点击建立 iCloud 会话";
    }

    const pill = $("#formSessionPill");
    if (pill) {
      let label = "未填写账户";
      let cls = "";
      if (username) {
        if (hasSession) {
          label = "会话已保存";
          cls = "ok";
        } else if (state.accounts.length) {
          label = "会话待验证";
          cls = "warn";
        } else {
          label = "未找到会话";
          cls = "bad";
        }
      }
      pill.className = `session-pill ${cls}`;
      pill.innerHTML = `<i></i> ${esc(label)}`;
    }

    const hint = $("#accountHint");
    if (hint) {
      let text;
      if (!username) {
        text = "选择账户或填写 Apple ID 后，会读取本机已有的 iCloud 会话。";
      } else if (hasSession) {
        text = "已找到本机会话。若 iCloud 判定会话过期，任务开始后仍需输入密码或验证码。";
      } else {
        text = "本机没有该账户的会话。可以点击「登录 / 重验」，或在任务开始后按提示输入密码。";
      }
      hint.innerHTML = `${ICON.info}<span>${esc(text)}</span>`;
    }
  }

  function sessionExistsFor(username) {
    const matched = state.accounts.find((item) => item.key === accountKey(username));
    return Boolean(matched && (matched.has_session || matched.has_cookie));
  }

  /* ------------------------------------------------------------------ */
  /* Dashboard                                                          */
  /* ------------------------------------------------------------------ */

  function renderStats() {
    const library = state.stats?.library || {};
    const disk = state.stats?.disk || {};
    const latest = state.stats?.latest_items || [];

    const count = Number(library.count || 0);
    $("#statFiles").textContent = number(count);
    $("#statFilesSub").textContent = count
      ? `${number(library.photos || 0)} 张照片 · ${number(library.videos || 0)} 段视频`
      : "还没有本地归档";
    $("#statSize").textContent = library.bytes_label || bytes(library.bytes);
    $("#statSizeSub").textContent = disk.free_label ? `磁盘剩余 ${disk.free_label}` : "磁盘信息不可用";
    $("#statLast").textContent = library.latest_at ? relativeTime(library.latest_at) : "—";
    $("#statLastSub").textContent = state.config?.download_dir
      ? basename(state.config.download_dir) || "下载目录"
      : "还没有选择目录";

    $("#heroFileCount").textContent = number(count);
    $("#heroSize").textContent = library.bytes_label || bytes(library.bytes);
    $("#heroLatest").textContent = library.latest_at ? relativeTime(library.latest_at) : "—";

    const diskPath = $("#diskPath");
    const diskFree = $("#diskFree");
    const ring = $("#diskRing");
    if (diskPath) {
      diskPath.textContent = state.config?.download_dir || "尚未选择目录";
      diskPath.title = state.config?.download_dir || "";
    }
    if (diskFree) {
      diskFree.textContent = disk.total
        ? `${disk.used_label} / ${disk.total_label} 已用 · 剩余 ${disk.free_label}`
        : "磁盘信息不可用";
    }
    if (ring) {
      const pct = Number(disk.percent || 0);
      ring.style.setProperty("--pct", String(Math.max(0, Math.min(100, pct))));
      $("#diskPercent").textContent = disk.total ? `${pct}%` : "—";
    }

    const active = state.jobs.find((job) => job.status === "running");
    const float = $("#floatStatus");
    if (float) {
      float.textContent = active ? (active.stage === "mfa" ? "等待验证码" : "正在同步") : "等待开始";
    }

    if (latest.length && state.activeView === "dashboard") {
      // The archive card is decorative; real metadata is shown in the cards below.
    }
  }

  function statusMeta(status, stage) {
    if (status === "running") {
      if (stage === "mfa" || stage === "password") return { label: "等待验证", cls: "run" };
      if (stage === "authenticating" || stage === "authenticated") return { label: "验证中", cls: "run" };
      if (stage === "preview") return { label: "预览中", cls: "run" };
      return { label: "运行中", cls: "run" };
    }
    if (status === "completed") return { label: "已完成", cls: "ok" };
    if (status === "failed") return { label: "失败", cls: "bad" };
    if (status === "stopped") return { label: "已停止", cls: "warn" };
    if (status === "interrupted") return { label: "已中断", cls: "warn" };
    return { label: status || "未知", cls: "" };
  }

  function jobIcon(job, cls) {
    if (job.kind === "auth") return `<span class="recent-ico ${cls}">${ICON.login}</span>`;
    if (job.config?.preview) return `<span class="recent-ico ${cls}">${ICON.cloud}</span>`;
    return `<span class="recent-ico ${cls}">${job.config?.skip_videos ? ICON.photo : ICON.film}</span>`;
  }

  function jobSubtitle(job) {
    if (job.kind === "auth") return "验证并保存 iCloud 会话";
    const parts = [];
    if (job.config?.albums?.length) parts.push(`${job.config.albums.length} 个相册`);
    else if (job.config?.recent) parts.push(`最近 ${job.config.recent} 个`);
    else if (job.config?.skip_created_before || job.config?.skip_created_after) parts.push("日期范围");
    else parts.push("全部内容");
    if (job.config?.skip_videos) parts.push("仅照片");
    else if (job.config?.skip_photos) parts.push("仅视频");
    if (job.config?.preview) parts.push("仅预览");
    return parts.join(" · ");
  }

  function renderDashboard() {
    renderStats();
    const host = $("#dashJobs");
    if (!host) return;
    const jobs = state.jobs.slice(0, 4);
    if (!jobs.length) {
      host.innerHTML = `
        <div class="recent-empty">
          <span class="recent-empty-icon">${ICON.list}</span>
          <div><strong>还没有任务</strong><p>第一次同步会在这里留下运行记录与统计。</p></div>
          <a class="button ghost small" href="#/download">开始同步 ${ICON.arrow}</a>
        </div>`;
      return;
    }
    host.innerHTML = jobs.map((job) => {
      const meta = statusMeta(job.status, job.stage);
      const progress = job.percent != null && job.status === "running" ? ` · ${job.percent}%` : "";
      return `
        <button class="recent-item" type="button" data-job-id="${esc(job.id)}">
          ${jobIcon(job, meta.cls)}
          <span class="recent-main">
            <span class="recent-title">${esc(job.kind === "auth" ? "登录 iCloud" : job.config?.preview ? "预览待下载清单" : "同步 iCloud 照片")}</span>
            <span class="recent-meta">
              <span>${esc(jobSubtitle(job))}</span><i class="dot"></i>
              <span>${esc(relativeTime(job.created_at))}${esc(progress)}</span>
            </span>
          </span>
          <span class="recent-right">
            <span class="mini-status ${meta.cls}">${esc(meta.label)}</span>
            <span class="recent-time">${esc(job.exit_code != null ? `exit ${job.exit_code}` : "")}</span>
          </span>
        </button>`;
    }).join("");
  }

  function updateNavCount() {
    const running = state.jobs.filter((job) => job.status === "running").length;
    const badge = $("#navJobCount");
    if (!badge) return;
    badge.hidden = running === 0;
    badge.textContent = String(running);
  }

  /* ------------------------------------------------------------------ */
  /* Download form                                                      */
  /* ------------------------------------------------------------------ */

  function setDomainButtons(container, value) {
    if (!container) return;
    $$("button[data-domain]", container).forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.domain === value);
    });
  }

  function currentDomain() {
    const active = $("#domainSeg button.active");
    return active?.dataset.domain || state.config?.domain || "com";
  }

  function ensureDownloadForm() {
    if (state.formReady) return;
    fillDownloadForm();
    state.formReady = true;
  }

  function fillDownloadForm() {
    const config = state.config || {};
    const username = $("#username");
    if (username && !username.value) username.value = config.username || "";
    const dir = $("#downloadDir");
    if (dir && !dir.value) dir.value = config.download_dir || "";
    setDomainButtons($("#domainSeg"), config.domain || "com");
    renderRecentDirs();
    renderSession();
    updateSummary();
    if (dir?.value) updateFreeSpace(dir.value);
    if (config.username && !sessionExistsFor(config.username)) {
      const details = $("#passwordDetails");
      if (details) details.open = true;
    }
  }

  function renderRecentDirs() {
    const host = $("#recentDirs");
    if (!host) return;
    const dirs = (state.config?.recent_dirs || []).filter(Boolean);
    if (!dirs.length) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    host.innerHTML = dirs.map((dir) => `<button type="button" data-recent-dir="${esc(dir)}" title="${esc(dir)}">${esc(dir)}</button>`).join("");
  }

  function toggleRecentDirs(force) {
    const host = $("#recentDirs");
    if (!host) return;
    const visible = typeof force === "boolean" ? force : host.hidden;
    host.hidden = !visible;
  }

  function selectedScope() {
    return $('input[name="scope"]:checked')?.value || "all";
  }

  function setScope(value) {
    $$('input[name="scope"]').forEach((input) => {
      input.checked = input.value === value;
    });
    $("#recentPanel").hidden = value !== "recent";
    $("#datePanel").hidden = value !== "range";
    $("#albumPanel").hidden = value !== "albums";
    updateSummary();
  }

  function mediaSelection() {
    return {
      photos: Boolean($("#mediaPhotos")?.checked),
      videos: Boolean($("#mediaVideos")?.checked),
      live: Boolean($("#mediaLive")?.checked),
    };
  }

  function mediaSummary(media) {
    const items = [];
    if (media.photos) items.push("照片");
    if (media.videos) items.push("视频");
    if (media.live) items.push("Live");
    return items.length ? items.join(" · ") : "未选择";
  }

  function scopeSummary() {
    const scope = selectedScope();
    if (scope === "recent") return `最近 ${Number($("#recentCount")?.value || 0) || "?"} 个`;
    if (scope === "range") {
      const from = $("#dateFrom")?.value;
      const to = $("#dateTo")?.value;
      if (from && to) return `${from} → ${to}`;
      if (from) return `${from} 之后`;
      if (to) return `${to} 之前`;
      return "日期范围（未填写）";
    }
    if (scope === "albums") return state.selectedAlbums.size ? `${state.selectedAlbums.size} 个相册` : "未选择相册";
    return "全部内容";
  }

  function quoteArg(value) {
    const text = String(value ?? "");
    return /[\s"]/g.test(text) ? `"${text.replace(/"/g, '\\"')}"` : text;
  }

  function buildCommandPreview() {
    const username = $("#username")?.value.trim() || "<Apple ID>";
    const dir = $("#downloadDir")?.value.trim() || "<下载目录>";
    const media = mediaSelection();
    const scope = selectedScope();
    const preview = $("#previewMode")?.checked;
    const dryRun = $("#dryRun")?.checked;
    const parts = ["icloudpd"];
    if (currentDomain() === "cn") parts.push("--domain", "cn");
    parts.push("--username", username);
    if ($("#password")?.value) parts.push("--password", "••••••••");
    if (preview) parts.push("--only-print-filenames");
    if (!preview) parts.push("--directory", dir);
    if (!media.photos) parts.push("--skip-photos");
    if (!media.videos) parts.push("--skip-videos");
    if (!media.live) parts.push("--skip-live-photos");
    if (preview || !dryRun) parts.push("--no-progress-bar", "--log-level", "info");
    if (dryRun) parts.push("--dry-run");
    if (scope === "recent") parts.push("--recent", String(Number($("#recentCount")?.value || 0) || 0));
    if (scope === "range") {
      if ($("#dateFrom")?.value) parts.push("--skip-created-before", $("#dateFrom").value);
      if ($("#dateTo")?.value) parts.push("--skip-created-after", $("#dateTo").value);
    }
    if (scope === "albums") {
      [...state.selectedAlbums].forEach((album) => parts.push("--album", album));
    }
    if ($("#sizeSelect")?.value) parts.push("--size", $("#sizeSelect").value);
    if ($("#folderStructure")?.value) parts.push("--folder-structure", $("#folderStructure").value);
    if ($("#xmpSidecar")?.checked) parts.push("--xmp-sidecar");
    if ($("#setExif")?.checked) parts.push("--set-exif-datetime");
    if ($("#keepUnicode")?.checked) parts.push("--keep-unicode-in-filenames");
    if ($("#deleteAfter")?.checked) parts.push("--delete-after-download");
    if ($("#autoDelete")?.checked) parts.push("--auto-delete");
    if ($("#keepRecentEnable")?.checked) {
      parts.push("--keep-icloud-recent-days", String(Number($("#keepDays")?.value || 0) || 0));
    }
    if ($("#matchPolicy")?.value && $("#matchPolicy").value !== "name-size-dedup-with-suffix") {
      parts.push("--file-match-policy", $("#matchPolicy").value);
    }
    if ($("#untilFound")?.value) parts.push("--until-found", String(Number($("#untilFound").value) || 0));
    if ($("#livePhotoPolicy")?.value && $("#livePhotoPolicy").value !== "suffix") {
      parts.push("--live-photo-mov-filename-policy", $("#livePhotoPolicy").value);
    }
    if ($("#alignRaw")?.value && $("#alignRaw").value !== "as-is") {
      parts.push("--align-raw", $("#alignRaw").value);
    }
    const text = parts.map(quoteArg).join(" ");
    return text.length > 420 ? `${text.slice(0, 417)}…` : text;
  }

  function updateSummary() {
    const username = $("#username")?.value.trim() || "";
    const dir = $("#downloadDir")?.value.trim() || "";
    const media = mediaSelection();
    const scope = selectedScope();
    const preview = Boolean($("#previewMode")?.checked);
    const dryRun = Boolean($("#dryRun")?.checked);

    $("#sumAccount").textContent = username || "未填写";
    $("#sumAccount").title = username;
    $("#sumScope").textContent = scopeSummary();
    $("#sumMedia").textContent = mediaSummary(media);
    const target = $("#sumTarget");
    target.textContent = dir || "未选择目录";
    target.title = dir;
    $("#sumMode").textContent = preview ? "仅预览" : dryRun ? "试运行" : "正式同步";
    $("#commandPreview").textContent = buildCommandPreview();

    const warnings = [];
    if (preview) warnings.push("预览模式只会列出待下载文件，不写入磁盘。");
    if (dryRun) warnings.push("试运行不会修改本地文件或 iCloud。");
    if (!media.photos && !media.videos) warnings.push("照片和视频都被跳过，任务不会下载任何内容。");
    if (scope === "albums" && !state.selectedAlbums.size) warnings.push("还没有选择相册。");
    if ($("#deleteAfter")?.checked) warnings.push("下载后会从 iCloud 删除对应照片/视频，并放入「最近删除」。");
    if ($("#autoDelete")?.checked) warnings.push("会自动清空 iCloud「最近删除」，该操作不可撤销。");
    if ($("#keepRecentEnable")?.checked) {
      const days = Number($("#keepDays")?.value || 0);
      warnings.push(days === 0 ? "会删除 iCloud 中的全部照片与视频。" : `会删除 iCloud 中 ${days} 天之前的照片与视频。`);
    }
    if (username && !sessionExistsFor(username) && !$("#password")?.value) {
      warnings.push("当前账户没有本机会话。请先登录 / 重验，或填写 iCloud 密码。");
    }

    const warningBox = $("#runWarning");
    if (warnings.length) {
      warningBox.hidden = false;
      warningBox.querySelector("span").textContent = warnings[0];
      warningBox.title = warnings.join("\n");
    } else {
      warningBox.hidden = true;
    }

    const ready = $("#runReady");
    let readyText = "准备就绪";
    let readyBad = false;
    if (!state.engine?.available) {
      readyText = "引擎不可用";
      readyBad = true;
    } else if (!username) {
      readyText = "等待账户";
      readyBad = true;
    } else if (!dir) {
      readyText = "等待目录";
      readyBad = true;
    } else if (scope === "albums" && !state.selectedAlbums.size) {
      readyText = "等待相册";
      readyBad = true;
    } else if (!sessionExistsFor(username) && !$("#password")?.value) {
      readyText = "需要会话";
      readyBad = true;
    } else if (!media.photos && !media.videos) {
      readyText = "未选择媒体";
      readyBad = true;
    }
    ready.innerHTML = `<i></i> ${esc(readyText)}`;
    ready.classList.toggle("bad", readyBad);
  }

  async function updateFreeSpace(rawPath) {
    const host = $("#freeSpaceHint");
    if (!host) return;
    const path = String(rawPath || "").trim();
    if (!path) {
      host.innerHTML = `${ICON.folder} 尚未选择下载目录`;
      return;
    }
    try {
      const data = await api(`/api/disk?path=${encodeURIComponent(path)}`);
      if (data.ok) {
        host.innerHTML = `<svg><use href="#i-hdd"/></svg> 剩余 ${esc(data.free_label)} / 共 ${esc(data.total_label)}`;
        host.title = `${data.used_label} 已使用`;
      } else {
        host.innerHTML = `${ICON.alert} 无法读取磁盘信息`;
      }
    } catch {
      host.innerHTML = `${ICON.alert} 无法读取磁盘信息`;
    }
  }

  const debouncedFreeSpace = debounce((path) => updateFreeSpace(path), 450);

  function formPayload() {
    const media = mediaSelection();
    const scope = selectedScope();
    const options = {
      domain: currentDomain(),
      preview: Boolean($("#previewMode")?.checked),
      dry_run: Boolean($("#dryRun")?.checked),
      skip_photos: !media.photos,
      skip_videos: !media.videos,
      skip_live_photos: !media.live,
      folder_structure: $("#folderStructure")?.value || "{:%Y/%m/%d}",
      size: $("#sizeSelect")?.value || undefined,
      xmp_sidecar: Boolean($("#xmpSidecar")?.checked),
      set_exif_datetime: Boolean($("#setExif")?.checked),
      keep_unicode_in_filenames: Boolean($("#keepUnicode")?.checked),
      file_match_policy: $("#matchPolicy")?.value || "name-size-dedup-with-suffix",
      live_photo_mov_filename_policy: $("#livePhotoPolicy")?.value || "suffix",
      align_raw: $("#alignRaw")?.value || "as-is",
      delete_after_download: Boolean($("#deleteAfter")?.checked),
      auto_delete: Boolean($("#autoDelete")?.checked),
    };
    if (scope === "recent") options.recent = Number($("#recentCount")?.value || 0) || undefined;
    if (scope === "range") {
      options.skip_created_before = $("#dateFrom")?.value || undefined;
      options.skip_created_after = $("#dateTo")?.value || undefined;
    }
    if (scope === "albums") options.albums = [...state.selectedAlbums];
    if ($("#untilFound")?.value) options.until_found = Number($("#untilFound").value) || undefined;
    if ($("#keepRecentEnable")?.checked) {
      options.keep_icloud_recent_days = Number($("#keepDays")?.value || 0) || 0;
    }
    return {
      username: $("#username")?.value.trim() || "",
      password: $("#password")?.value || "",
      directory: $("#downloadDir")?.value.trim() || "",
      options,
    };
  }

  function validateForm(payload) {
    if (!state.engine?.available) {
      return "没有找到 icloudpd。请先安装并确保它在 PATH 中。";
    }
    if (!payload.username) return "请填写 Apple ID。";
    if (!payload.directory) return "请选择下载目录。";
    if (!sessionExistsFor(payload.username) && !payload.password) {
      return "本机没有该账户的 iCloud 会话。请先点击「登录 / 重验」，或填写 iCloud 密码。";
    }
    if (payload.options.skip_photos && payload.options.skip_videos) {
      return "至少保留一种媒体类型（照片或视频）。";
    }
    if (payload.options.delete_after_download && payload.options.auto_delete) {
      return "「下载后删除」与「清理最近删除」不能同时启用。";
    }
    if (selectedScope() === "albums" && !state.selectedAlbums.size) {
      return "请选择至少一个相册，或把范围切换为全部内容。";
    }
    if (selectedScope() === "recent" && (!payload.options.recent || payload.options.recent < 1)) {
      return "请输入要下载的数量。";
    }
    if (selectedScope() === "range" && payload.options.skip_created_before && payload.options.skip_created_after) {
      if (payload.options.skip_created_before > payload.options.skip_created_after) {
        return "开始日期不能晚于结束日期。";
      }
    }
    return null;
  }

  async function onSubmit(event) {
    event.preventDefault();
    const payload = formPayload();
    const error = validateForm(payload);
    if (error) {
      toast("无法开始同步", error, "warn");
      return;
    }

    const dangerous = [];
    if (payload.options.delete_after_download) dangerous.push("下载后从 iCloud 删除");
    if (payload.options.auto_delete) dangerous.push("清空 iCloud「最近删除」");
    if (payload.options.keep_icloud_recent_days !== undefined) dangerous.push("删除 iCloud 旧照片");
    if (dangerous.length && !payload.options.preview && !payload.options.dry_run) {
      const ok = await confirmAction({
        title: "确认修改 iCloud？",
        text: `当前任务启用了：${dangerous.join("、")}。这些操作可能从 iCloud 中删除照片或视频。请确认账户与筛选范围无误。`,
        okLabel: "确认并开始",
        danger: true,
      });
      if (!ok) return;
    }

    const start = $("#startBtn");
    const original = start.innerHTML;
    start.disabled = true;
    start.innerHTML = `<span>正在启动…</span><span class="spinner" style="border-top-color:#fff"></span>`;
    try {
      const data = await api("/api/jobs", { method: "POST", body: payload });
      $("#password").value = "";
      state.jobs.unshift(data.job);
      state.pollBoostUntil = Date.now() + 12_000;
      updateNavCount();
      renderDashboard();
      toast("任务已启动", payload.options.preview ? "正在读取待下载清单。" : "icloudpd 已开始运行，可在任务记录查看实时输出。", "success");
      state.selectedJobId = data.job.id;
      state.jobDetailLoaded = false;
      navigate("jobs");
      await selectJob(data.job.id, { force: true });
      pollJobs(true);
    } catch (err) {
      toast("启动失败", err.message, "error", 6000);
    } finally {
      start.disabled = false;
      start.innerHTML = original;
      updateSummary();
    }
  }

  async function loadAlbums() {
    const username = $("#username")?.value.trim() || "";
    if (!username) {
      toast("请先填写 Apple ID", "读取相册需要一个账户。", "warn");
      return;
    }
    const btn = $("#loadAlbumsBtn");
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `${ICON.refresh} 读取中…`;
    const hint = $("#albumHint");
    if (hint) hint.textContent = "正在连接 iCloud 读取专辑…";
    try {
      const data = await api("/api/albums", {
        method: "POST",
        body: { username, domain: currentDomain() },
      });
      state.albums = data.albums || [];
      if (state.albums.length) {
        renderAlbumChips();
        toast("已读取专辑", `共 ${state.albums.length} 个相册，可多选。`, "success");
        if (!data.ok) toast("读取不完整", data.error || "", "warn");
      } else {
        $("#albumChips").innerHTML = `<span class="muted-small">${esc(data.error || "没有读取到相册。请先登录 iCloud。")}</span>`;
        if (!data.ok) toast("无法读取专辑", data.error || "请先登录 iCloud。", "warn", 6000);
      }
    } catch (err) {
      $("#albumChips").innerHTML = `<span class="muted-small">${esc(err.message)}</span>`;
      toast("无法读取专辑", err.message, "error", 6000);
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  function renderAlbumChips() {
    const host = $("#albumChips");
    if (!host) return;
    if (!state.albums.length) {
      host.innerHTML = '<span class="muted-small" id="albumHint">没有相册。可以先读取专辑，或改用全部内容。</span>';
      return;
    }
    host.innerHTML = state.albums.map((album) => {
      const selected = state.selectedAlbums.has(album);
      return `<button type="button" class="album-chip ${selected ? "selected" : ""}" data-album="${esc(album)}">${selected ? ICON.check : ""}${esc(album)}</button>`;
    }).join("");
  }

  function toggleAlbum(album) {
    if (state.selectedAlbums.has(album)) state.selectedAlbums.delete(album);
    else state.selectedAlbums.add(album);
    renderAlbumChips();
    updateSummary();
  }

  function applyPreset() {
    if (!state.pendingPreset) return;
    const preset = state.pendingPreset;
    state.pendingPreset = null;
    if (preset.scope) setScope(preset.scope);
    if (preset.recent) {
      $("#recentCount").value = String(preset.recent);
      setScope("recent");
    }
    if (preset.preview) {
      $("#previewMode").checked = true;
      updateSummary();
    }
    updateSummary();
  }

  function goDownload(preset = null) {
    state.pendingPreset = preset;
    if (location.hash === "#/download") {
      ensureDownloadForm();
      applyPreset();
      updateSummary();
    } else {
      navigate("download");
    }
  }

  /* ------------------------------------------------------------------ */
  /* Auth modal                                                         */
  /* ------------------------------------------------------------------ */

  function openAuthDialog() {
    const dialog = $("#authDialog");
    if (!dialog || dialog.open) return;
    const username = $("#username")?.value.trim() || state.config?.username || "";
    $("#authUsername").value = username;
    $("#authPassword").value = "";
    const preferredDomain = state.activeView === "settings"
      ? ($("#setDomainSeg button.active")?.dataset.domain || state.config?.domain || "com")
      : currentDomain();
    setDomainButtons($("#authDomainSeg"), preferredDomain);
    dialog.showModal();
    setTimeout(() => {
      if (username) $("#authPassword").focus();
      else $("#authUsername").focus();
    }, 80);
  }

  async function onAuthSubmit(event) {
    event.preventDefault();
    const username = $("#authUsername").value.trim();
    const password = $("#authPassword").value;
    const domain = $("#authDomainSeg button.active")?.dataset.domain || "com";
    if (!username) {
      toast("请填写 Apple ID", "", "warn");
      $("#authUsername").focus();
      return;
    }
    if (!password) {
      toast("请填写 iCloud 密码", "", "warn");
      $("#authPassword").focus();
      return;
    }
    const submit = $("#authSubmit");
    const original = submit.innerHTML;
    submit.disabled = true;
    submit.innerHTML = `<span>正在启动验证…</span><span class="spinner" style="border-top-color:#fff"></span>`;
    try {
      const data = await api("/api/auth", { method: "POST", body: { username, password, domain } });
      $("#authPassword").value = "";
      $("#authDialog").close();
      state.config.username = username;
      state.config.domain = domain;
      if ($("#username")) $("#username").value = username;
      setDomainButtons($("#domainSeg"), domain);
      renderSession();
      state.jobs.unshift(data.job);
      state.selectedJobId = data.job.id;
      state.jobDetailLoaded = false;
      state.pollBoostUntil = Date.now() + 15_000;
      updateNavCount();
      navigate("jobs");
      await selectJob(data.job.id, { force: true });
      toast("验证任务已启动", "请在任务面板中输入 iCloud 发来的验证码。", "success", 6000);
      pollJobs(true);
    } catch (err) {
      toast("无法开始验证", err.message, "error", 6000);
    } finally {
      submit.disabled = false;
      submit.innerHTML = original;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Jobs                                                               */
  /* ------------------------------------------------------------------ */

  function renderJobList() {
    const host = $("#jobList");
    if (!host) return;
    $("#jobCount").textContent = String(state.jobs.length);
    updateNavCount();
    if (!state.jobs.length) {
      host.innerHTML = `
        <div class="job-list-empty">
          <span>${ICON.list}</span>
          <strong>还没有任务</strong>
          <p>新建一次同步后，任务会出现在这里。</p>
          <a class="button ghost small" href="#/download">新建任务</a>
        </div>`;
      return;
    }
    const scrollTop = host.scrollTop;
    host.innerHTML = state.jobs.map((job) => {
      const meta = statusMeta(job.status, job.stage);
      const active = job.id === state.selectedJobId ? "active" : "";
      return `
        <button class="job-list-item ${active}" type="button" data-job-id="${esc(job.id)}">
          <span class="job-list-dot ${meta.cls}"></span>
          <span class="job-list-copy">
            <span class="job-list-title">${esc(job.kind === "auth" ? "登录 iCloud" : job.config?.preview ? "预览清单" : "同步照片")}${job.kind === "sync" ? ` · ${esc(shortUser(job.username))}` : ""}</span>
            <span class="job-list-meta">
              <span>${esc(meta.label)}</span><i class="dot"></i>
              <span>${esc(jobSubtitle(job))}</span>
            </span>
          </span>
          <span class="job-list-time">${esc(relativeTime(job.created_at))}</span>
        </button>`;
    }).join("");
    host.scrollTop = scrollTop;
  }

  function shortUser(username) {
    const text = String(username || "");
    if (text.length <= 26) return text;
    return `${text.slice(0, 12)}…${text.slice(-8)}`;
  }

  async function selectJob(jobId, { quiet = false, force = false } = {}) {
    if (!jobId) return;
    if (!force && state.selectedJobId === jobId && state.jobDetailLoaded) {
      renderJobList();
      return;
    }
    state.selectedJobId = jobId;
    state.jobDetailLoaded = false;
    state.jobLogLast = 0;
    state.logCount = 0;
    state.promptSignature = "";
    state.manualPromptJobId = null;
    renderJobList();
    $("#jobDetailEmpty").hidden = true;
    $("#jobDetailContent").hidden = false;
    $("#jobLog").innerHTML = '<div class="terminal-placeholder">正在读取输出…</div>';
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(jobId)}?after=0`);
      applyJobDetail(data.job, true);
      state.jobDetailLoaded = true;
      state.pollBoostUntil = Date.now() + 6000;
    } catch (err) {
      if (!quiet) toast("无法读取任务", err.message, "error");
      $("#jobLog").innerHTML = `<div class="terminal-placeholder">${esc(err.message)}</div>`;
    }
  }

  function applyJobDetail(job, reset = false) {
    if (!job) return;
    const meta = statusMeta(job.status, job.stage);
    const title = job.kind === "auth" ? "登录 iCloud" : job.config?.preview ? "预览待下载清单" : "同步 iCloud 照片";
    $("#jobTitle").textContent = title;
    const subtitle = [
      job.username,
      job.kind === "auth" ? "仅验证会话" : (job.config?.directory || ""),
      formatDateTime(job.started_at || job.created_at),
    ].filter(Boolean).join(" · ");
    $("#jobSub").textContent = subtitle;
    const badge = $("#jobStatus");
    badge.className = `status-badge ${meta.cls}`;
    badge.innerHTML = `<i></i> ${esc(meta.label)}`;
    $("#jobKindIcon").innerHTML = job.kind === "auth" ? ICON.login : job.config?.preview ? ICON.cloud : ICON.cloud;

    $("#jobProgressText").textContent = progressLabel(job);
    $("#jobPercent").textContent = job.percent != null ? `${job.percent}%` : "—";
    const bar = $("#jobProgressBar");
    if (job.percent == null && job.status === "running") {
      bar.classList.add("indeterminate");
      bar.style.width = "";
    } else {
      bar.classList.remove("indeterminate");
      bar.style.width = `${Math.max(0, Math.min(100, Number(job.percent || 0)))}%`;
    }
    $("#jobDownloaded").textContent = number(job.progress?.downloaded || 0);
    $("#jobSkipped").textContent = number(job.progress?.skipped || 0);
    $("#jobErrors").textContent = number(job.progress?.errors || 0);
    $("#jobElapsed").textContent = formatDuration(job.started_at, job.ended_at);

    const stop = $("#jobStopBtn");
    stop.hidden = job.status !== "running";
    stop.disabled = false;
    const manual = $("#jobManualInput");
    if (manual) manual.hidden = job.status !== "running";
    if (job.status !== "running") state.manualPromptJobId = null;

    const live = $("#terminalLive");
    live.classList.toggle("off", job.status !== "running");
    live.innerHTML = job.status === "running" ? "<i></i> LIVE" : "<i></i> OFFLINE";

    $("#jobCommand").textContent = job.command_preview || "—";
    $("#jobCommand").title = job.command_preview || "";

    renderPrompt(job);
    if (Array.isArray(job.logs)) {
      appendLogs(job.logs, reset);
    }
    if (job.log_seq != null) state.jobLogLast = job.log_seq;
  }

  function progressLabel(job) {
    if (job.status === "completed") return job.kind === "auth" ? "验证完成，会话已保存" : "同步完成";
    if (job.status === "failed") return job.error ? String(job.error).slice(0, 150) : "任务失败";
    if (job.status === "stopped") return "任务已停止";
    if (job.status === "interrupted") return "服务重启导致任务中断";
    if (job.stage === "mfa") return "等待双重认证验证码";
    if (job.stage === "password") return "等待 iCloud 密码";
    if (job.stage === "authenticating") return "正在验证 iCloud 会话…";
    if (job.stage === "authenticated") return "验证成功，正在准备工作…";
    if (job.config?.preview) return `正在读取待下载清单 · 已发现 ${number(job.progress?.downloaded || 0)} 个`;
    if (job.progress?.total) return `正在下载 ${number(job.progress.current || 0)} / ${number(job.progress.total)}`;
    if (job.progress?.downloaded) return `已下载 ${number(job.progress.downloaded)} 个文件`;
    return "正在启动 icloudpd…";
  }

  function renderPrompt(job) {
    const box = $("#jobPrompt");
    if (!box) return;
    const manual = state.manualPromptJobId === job.id;
    const show = Boolean(job.status === "running" && (job.awaiting_input || manual));
    box.hidden = !show;
    if (!show) {
      state.promptSignature = "";
      return;
    }
    const isPassword = job.stage === "password";
    const signature = manual
      ? `${job.id}:manual`
      : `${job.id}:${job.stage}:${job.prompt || ""}`;
    if (signature === state.promptSignature) return;
    state.promptSignature = signature;
    const input = $("#jobPromptInput");
    const send = $("#jobPromptSend");
    if (manual) {
      box.querySelector(".prompt-copy strong").textContent = "手动输入";
      $("#jobPromptText").textContent = "如果 icloudpd 正在等待密码或验证码，可在这里提交。提交内容不会显示在日志中。";
      input.type = isPassword ? "password" : "text";
      input.placeholder = isPassword ? "iCloud 密码" : "密码或验证码";
      input.inputMode = "text";
      input.maxLength = 256;
      send.textContent = "提交";
    } else if (isPassword) {
      box.querySelector(".prompt-copy strong").textContent = "需要 iCloud 密码";
      $("#jobPromptText").textContent = "当前会话已过期，请输入该 Apple ID 的密码后继续。密码只会传给 icloudpd。";
      input.type = "password";
      input.placeholder = "iCloud 密码";
      input.inputMode = "text";
      input.maxLength = 256;
      send.textContent = "提交密码";
    } else {
      box.querySelector(".prompt-copy strong").textContent = "需要双重认证";
      $("#jobPromptText").textContent = "输入 Apple 设备上显示的验证码。提交后任务会自动继续。";
      input.type = "text";
      input.placeholder = "6 位验证码";
      input.inputMode = "numeric";
      input.maxLength = 12;
      send.textContent = "提交验证码";
    }
    input.value = "";
    setTimeout(() => input.focus(), 80);
  }

  function appendLogs(logs, reset = false) {
    const box = $("#jobLog");
    if (!box) return;
    const placeholder = box.querySelector(".terminal-placeholder");
    if (reset) {
      box.innerHTML = "";
      state.logCount = 0;
    } else if (placeholder) {
      placeholder.remove();
    }
    if (!logs || !logs.length) {
      if (reset && !box.children.length) {
        box.innerHTML = '<div class="terminal-placeholder">等待输出…</div>';
      }
      return;
    }
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
    const auto = $("#autoScroll")?.checked;
    const fragment = document.createDocumentFragment();
    for (const line of logs) {
      const level = line.level || "info";
      const text = String(line.text || "").replace(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,.]\d+)?\s+/, "");
      const row = document.createElement("div");
      row.className = `log-line ${level}`;
      const time = document.createElement("span");
      time.className = "log-time";
      time.textContent = formatClock(line.ts);
      const copy = document.createElement("span");
      copy.className = "log-text";
      copy.textContent = text;
      row.append(time, copy);
      fragment.appendChild(row);
      state.logCount += 1;
    }
    box.appendChild(fragment);
    $("#jobLogMeta").textContent = `${number(state.logCount)} 行`;
    if (reset || (auto && atBottom)) {
      box.scrollTop = box.scrollHeight;
    }
  }

  async function pollJobs(force = false) {
    if (!force && document.hidden) return;
    try {
      const data = await api("/api/jobs");
      const previous = new Map(state.jobs.map((job) => [job.id, job]));
      state.jobs = data.jobs || [];
      state.activeJobId = data.active_job_id || null;
      updateNavCount();
      renderJobList();
      if (state.activeView === "dashboard") renderDashboard();

      for (const job of state.jobs) {
        const before = previous.get(job.id);
        if (before?.status === "running" && job.status !== "running") {
          if (!state.notifiedJobs.has(job.id)) {
            state.notifiedJobs.add(job.id);
            const meta = statusMeta(job.status, job.stage);
            const type = job.status === "completed" ? "success" : job.status === "failed" ? "error" : "warn";
            toast(
              job.status === "completed" ? "任务完成" : job.status === "failed" ? "任务失败" : "任务已停止",
              `${job.kind === "auth" ? "iCloud 验证" : job.config?.preview ? "清单预览" : "同步任务"} · ${meta.label}`,
              type,
              6000
            );
            state.gallery.loaded = false;
            refreshBootstrap({ preserveForm: true, silent: true }).catch(() => {});
          }
        }
      }

      if (state.selectedJobId) {
        const selected = state.jobs.find((job) => job.id === state.selectedJobId);
        if (
          selected &&
          (selected.status === "running" ||
            force ||
            Date.now() < state.pollBoostUntil ||
            Number(selected.log_seq || 0) > state.jobLogLast)
        ) {
          await pollSelectedJob();
        }
      }
    } catch {
      /* transient network errors are ignored; the next tick retries */
    }
  }

  async function pollSelectedJob() {
    if (!state.selectedJobId) return;
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(state.selectedJobId)}?after=${state.jobLogLast}`);
      applyJobDetail(data.job, false);
    } catch {
      /* keep the last known state */
    }
  }

  async function sendPrompt() {
    const job = state.jobs.find((item) => item.id === state.selectedJobId);
    const input = $("#jobPromptInput");
    const value = input?.value.trim();
    if (!job) return;
    if (!value) {
      toast("请输入内容", "", "warn");
      input?.focus();
      return;
    }
    const button = $("#jobPromptSend");
    button.disabled = true;
    try {
      await api(`/api/jobs/${encodeURIComponent(job.id)}/input`, { method: "POST", body: { text: value } });
      input.value = "";
      $("#jobPrompt").hidden = true;
      state.promptSignature = "";
      state.manualPromptJobId = null;
      toast("已提交", "正在等待 icloudpd 继续。", "success");
      setTimeout(() => pollJobs(true), 600);
    } catch (err) {
      toast("提交失败", err.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function stopSelectedJob() {
    const job = state.jobs.find((item) => item.id === state.selectedJobId);
    if (!job || job.status !== "running") return;
    const ok = await confirmAction({
      title: "停止当前任务？",
      text: "已经下载完成的文件会保留。可以在之后重新运行同一配置继续增量同步。",
      okLabel: "停止任务",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/jobs/${encodeURIComponent(job.id)}/stop`, { method: "POST", body: {} });
      toast("正在停止", "任务进程会很快结束。", "warn");
      setTimeout(() => pollJobs(true), 500);
    } catch (err) {
      toast("停止失败", err.message, "error");
    }
  }

  async function clearJobs() {
    const finished = state.jobs.filter((job) => job.status !== "running").length;
    if (!finished) {
      toast("没有可清理的任务", "", "warn");
      return;
    }
    const ok = await confirmAction({
      title: "清理任务记录？",
      text: `将删除 ${finished} 条已结束任务的本地记录与日志。已下载的文件不会受影响。`,
      okLabel: "清理记录",
      danger: true,
    });
    if (!ok) return;
    try {
      await api("/api/jobs/clear", { method: "POST", body: {} });
      if (state.selectedJobId) {
        const selected = state.jobs.find((job) => job.id === state.selectedJobId);
        if (selected?.status !== "running") {
          state.selectedJobId = null;
          state.jobDetailLoaded = false;
          $("#jobDetailContent").hidden = true;
          $("#jobDetailEmpty").hidden = false;
        }
      }
      await pollJobs(true);
      toast("任务记录已清理", "", "success");
    } catch (err) {
      toast("清理失败", err.message, "error");
    }
  }

  /* ------------------------------------------------------------------ */
  /* Gallery                                                            */
  /* ------------------------------------------------------------------ */

  function renderGalleryStats() {
    const stats = state.stats?.library || {};
    const host = $("#galleryStats");
    if (host) {
      host.innerHTML = `<strong>${number(stats.count || 0)}</strong><span>个文件</span>`;
      host.title = stats.bytes_label || "";
    }
  }

  async function loadGallery(reset = false) {
    const gallery = state.gallery;
    if (gallery.loading && !reset) return;
    if (reset) {
      gallery.offset = 0;
      gallery.items = [];
      gallery.loaded = false;
    }
    gallery.loading = true;
    const requestId = ++gallery.requestId;
    const more = $("#galleryMore");
    if (more) {
      more.disabled = true;
      more.textContent = reset ? "正在扫描…" : "正在加载…";
    }
    if (reset) {
      $("#galleryGrid").innerHTML = "";
      $("#galleryEmpty").hidden = true;
    }
    try {
      const params = new URLSearchParams({
        offset: String(gallery.offset),
        limit: "60",
        kind: gallery.kind,
        sort: gallery.sort,
      });
      if (gallery.q) params.set("q", gallery.q);
      if (reset && state.gallery.forceRefresh) {
        params.set("refresh", "1");
        state.gallery.forceRefresh = false;
      }
      const data = await api(`/api/gallery?${params.toString()}`);
      if (requestId !== gallery.requestId) return;
      if (reset) gallery.items = [];
      gallery.items = gallery.items.concat(data.items || []);
      gallery.total = Number(data.total || 0);
      gallery.hasMore = Boolean(data.has_more);
      gallery.loaded = true;
      if (data.stats) {
        state.stats.library = data.stats;
      }
      renderGallery();
    } catch (err) {
      if (requestId !== gallery.requestId) return;
      $("#galleryGrid").innerHTML = "";
      $("#galleryEmpty").hidden = false;
      $("#galleryEmpty").querySelector("h2").textContent = "无法读取图库";
      $("#galleryEmpty").querySelector("p").textContent = err.message;
      $("#galleryMore").hidden = true;
      toast("无法读取图库", err.message, "error");
    } finally {
      if (requestId === gallery.requestId) {
        gallery.loading = false;
        if (more) {
          more.disabled = false;
          more.textContent = "加载更多";
        }
      }
    }
  }

  function renderGallery() {
    const gallery = state.gallery;
    const grid = $("#galleryGrid");
    const empty = $("#galleryEmpty");
    if (!grid) return;
    renderGalleryStats();

    if (!gallery.items.length) {
      grid.innerHTML = "";
      empty.hidden = false;
      empty.querySelector("h2").textContent = gallery.q ? "没有匹配的文件" : "这里还没有照片";
      empty.querySelector("p").textContent = gallery.q
        ? "换一个关键词，或清除搜索条件后再试。"
        : state.config?.download_dir
          ? "完成一次同步后，本地归档中的照片与视频会出现在这里。"
          : "先选择一个下载目录并完成同步。";
      $("#galleryCount").textContent = "0 个文件";
      $("#galleryMore").hidden = true;
      return;
    }
    empty.hidden = true;
    grid.innerHTML = gallery.items.map((item, index) => {
      const isVideo = item.kind === "video";
      const heic = !item.renderable && !isVideo;
      return `
        <article class="gallery-item" data-index="${index}" tabindex="0" role="button" aria-label="查看 ${esc(item.name)}">
          <div class="gallery-thumb">
            <img src="${esc(item.thumb_url)}" alt="${esc(item.name)}" loading="lazy" decoding="async" data-fallback="1">
            ${isVideo ? `<span class="gallery-badge">${ICON.play}<span>VIDEO</span></span>` : ""}
            ${heic ? `<span class="gallery-badge heic">HEIC</span>` : ""}
            <div class="gallery-overlay">
              <strong>${esc(item.name)}</strong>
              <span>${esc(item.kind === "video" ? "视频" : "照片")} · ${esc(item.size_label || bytes(item.size))}</span>
            </div>
          </div>
        </article>`;
    }).join("");
    $("#galleryCount").textContent = `${number(gallery.items.length)} / ${number(gallery.total)} 个文件`;
    $("#galleryMore").hidden = !gallery.hasMore;
  }

  function galleryFallback(img) {
    const wrap = img.closest(".gallery-thumb");
    const itemEl = img.closest(".gallery-item");
    if (!wrap || wrap.querySelector(".thumb-fallback")) return;
    const item = state.gallery.items[Number(itemEl?.dataset.index || 0)];
    img.remove();
    const fallback = document.createElement("div");
    fallback.className = `thumb-fallback ${item?.kind === "video" ? "video" : ""}`;
    fallback.innerHTML = `${item?.kind === "video" ? ICON.film : ICON.image}<span>${esc(item?.ext || "预览不可用")}</span>`;
    wrap.prepend(fallback);
  }

  function openLightbox(index) {
    const dialog = $("#lightboxDialog");
    const items = state.gallery.items;
    if (!dialog || !items.length) return;
    state.lightboxIndex = Math.max(0, Math.min(items.length - 1, index));
    renderLightbox();
    if (!dialog.open) dialog.showModal();
  }

  function renderLightbox() {
    const item = state.gallery.items[state.lightboxIndex];
    const stage = $("#lightboxStage");
    if (!item || !stage) return;
    if (item.kind === "video") {
      stage.innerHTML = `<video src="${esc(item.media_url)}" controls autoplay playsinline preload="metadata"></video>`;
    } else {
      stage.innerHTML = `<img src="${esc(item.preview_url)}" alt="${esc(item.name)}">`;
    }
    $("#lightboxName").textContent = item.name;
    $("#lightboxMeta").innerHTML = `
      <div><span>类型</span><span>${esc(item.kind === "video" ? "视频" : "照片")} · ${esc(String(item.ext || "").toUpperCase())}</span></div>
      <div><span>大小</span><span>${esc(item.size_label || bytes(item.size))}</span></div>
      <div><span>修改时间</span><span>${esc(formatDateTime(item.modified_at))}</span></div>
      <div><span>本地路径</span><span>${esc(item.rel)}</span></div>`;
    const prev = $("#lightboxPrev");
    const next = $("#lightboxNext");
    prev.disabled = state.gallery.items.length <= 1;
    next.disabled = state.gallery.items.length <= 1;
    stage.querySelector("img")?.addEventListener("error", () => {
      stage.innerHTML = `<div class="lightbox-error">${esc(item.name)}<span>无法在浏览器中预览此格式</span></div>`;
    });
  }

  function navigateLightbox(delta) {
    const total = state.gallery.items.length;
    if (!total) return;
    state.lightboxIndex = (state.lightboxIndex + delta + total) % total;
    renderLightbox();
  }

  async function revealItem(action) {
    const item = state.gallery.items[state.lightboxIndex];
    if (!item) return;
    try {
      await api(`/api/${action}`, { method: "POST", body: { path: item.rel } });
    } catch (err) {
      toast("操作失败", err.message, "error");
    }
  }

  /* ------------------------------------------------------------------ */
  /* Settings                                                           */
  /* ------------------------------------------------------------------ */

  function fillSettings() {
    const config = state.config || {};
    if ($("#setUsername")) $("#setUsername").value = config.username || "";
    if ($("#setDownloadDir")) $("#setDownloadDir").value = config.download_dir || "";
    if ($("#setCookieDir")) $("#setCookieDir").value = config.cookie_dir || "";
    setDomainButtons($("#setDomainSeg"), config.domain || "com");
    renderSessionList();
    renderEngineDetails();
    $("#dataPath").textContent = state.boot?.app?.data_dir || "data";
    $("#settingsJobCount").textContent = String(state.jobs.length);
    $("#settingsMediaCount").textContent = number(state.stats?.library?.count || 0);
    $("#aboutEngineVersion").textContent = state.engine?.version || "—";
    $("#aboutUiVersion").textContent = state.boot?.app?.version || "1.0.0";
    $("#aboutPython").textContent = state.boot?.app?.python || "—";
    $$("#themeOptions button").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.themeChoice === state.themeChoice);
    });
  }

  function renderSessionList() {
    const host = $("#sessionList");
    if (!host) return;
    if (!state.accounts.length) {
      host.innerHTML = '<span class="muted-small">本机没有找到 iCloud 会话。登录后会保存在 cookie 目录。</span>';
      return;
    }
    host.innerHTML = state.accounts.map((account) => {
      const bits = [];
      if (account.has_session) bits.push("session");
      if (account.has_cookie) bits.push("cookie");
      return `
        <div class="session-row ${account.matched ? "matched" : ""}">
          <i class="session-dot"></i>
          <strong title="${esc(account.key)}">${esc(account.key)}</strong>
          <span>${esc(bits.join(" + "))}${account.updated_at ? ` · ${esc(relativeTime(account.updated_at))}` : ""}</span>
        </div>`;
    }).join("");
  }

  function renderEngineDetails() {
    const host = $("#engineDetails");
    if (!host) return;
    const engine = state.engine || {};
    host.innerHTML = `
      <div><span>状态</span><strong class="${engine.available ? "ok" : "bad"}">${engine.available ? "可用" : "不可用"}</strong></div>
      <div><span>版本</span><strong>${esc(engine.version || "—")}</strong></div>
      <div><span>命令</span><strong>${esc(engine.command || "icloudpd")}</strong></div>
      <div><span>位置</span><strong>${esc(engine.folder || "PATH")}</strong></div>
      ${engine.error ? `<div><span>错误</span><strong class="bad">${esc(String(engine.error).slice(0, 180))}</strong></div>` : ""}`;
  }

  async function saveSettings() {
    const payload = {
      username: $("#setUsername")?.value.trim() || "",
      domain: $("#setDomainSeg button.active")?.dataset.domain || "com",
      download_dir: $("#setDownloadDir")?.value.trim() || "",
      cookie_dir: $("#setCookieDir")?.value.trim() || "",
    };
    if (!payload.download_dir) {
      toast("下载目录不能为空", "", "warn");
      return;
    }
    const btn = $("#saveSettingsBtn");
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `${ICON.refresh} 保存中…`;
    try {
      const data = await api("/api/settings", { method: "POST", body: payload });
      state.config = data.config;
      if ($("#username")) $("#username").value = state.config.username || "";
      if ($("#downloadDir")) $("#downloadDir").value = state.config.download_dir || "";
      setDomainButtons($("#domainSeg"), state.config.domain || "com");
      renderSession();
      updateSummary();
      updateFreeSpace(state.config.download_dir);
      fillSettings();
      toast("设置已保存", "新配置会用于下一次任务。", "success");
      refreshBootstrap({ preserveForm: true, silent: true }).catch(() => {});
    } catch (err) {
      toast("保存失败", err.message, "error");
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Folder picker                                                      */
  /* ------------------------------------------------------------------ */

  async function openFolderPicker(initialPath, target = "download") {
    const dialog = $("#folderDialog");
    if (!dialog) return;
    state.folderTarget = target;
    $("#folderNew").hidden = true;
    $("#folderNewName").value = "";
    $("#folderSelected").textContent = "正在读取…";
    if (!dialog.open) dialog.showModal();
    await loadFolder(initialPath || state.config?.download_dir || "");
  }

  async function loadFolder(path) {
    const list = $("#folderList");
    try {
      const data = await api(`/api/fs?path=${encodeURIComponent(path || "")}`);
      state.folder = data;
      $("#folderPath").textContent = data.path || "此电脑";
      $("#folderPath").title = data.path || "";
      $("#folderSelected").textContent = data.path || "此电脑";
      $("#folderSelected").title = data.path || "";
      $("#folderUp").disabled = !data.parent;
      const shortcuts = $("#folderShortcuts");
      shortcuts.innerHTML = (data.shortcuts || []).map((item) =>
        `<button type="button" data-folder-path="${esc(item.path)}">${esc(item.name)}</button>`
      ).join("");
      const drivesHost = $("#folderDrives");
      if (drivesHost) {
        const currentPath = String(data.path || "").toLowerCase();
        drivesHost.innerHTML = (data.drives || []).map((drive) => {
          const active = currentPath.startsWith(String(drive.path || "").toLowerCase());
          return `<button type="button" class="${active ? "active" : ""}" data-folder-path="${esc(drive.path)}" title="${esc(drive.path)}">
            <svg><use href="#i-drive"/></svg><span>${esc(drive.name)}</span>
          </button>`;
        }).join("");
      }
      if (!data.entries?.length) {
        list.innerHTML = data.path
          ? `<div class="empty-mini">${esc(data.error || "这个文件夹里没有子文件夹")}</div>`
          : '<div class="empty-mini">选择一个磁盘开始浏览</div>';
        return;
      }
      list.innerHTML = data.entries.map((entry) =>
        `<button type="button" class="folder-entry ${entry.hidden ? "hidden" : ""}" data-folder-path="${esc(entry.path)}" title="${esc(entry.path)}">
          <svg><use href="#i-folder"/></svg><span>${esc(entry.name)}</span>
        </button>`
      ).join("");
    } catch (err) {
      list.innerHTML = `<div class="empty-mini">${esc(err.message)}</div>`;
      toast("无法读取文件夹", err.message, "error");
    }
  }

  async function chooseFolder() {
    const path = state.folder.path || "";
    if (!path) {
      toast("请先进入一个文件夹", "", "warn");
      return;
    }
    if (state.folderTarget === "settings") {
      $("#setDownloadDir").value = path;
    } else if (state.folderTarget === "quick") {
      state.config.download_dir = path;
      if ($("#downloadDir")) $("#downloadDir").value = path;
      updateSummary();
      updateFreeSpace(path);
      try {
        await api("/api/settings", { method: "POST", body: { download_dir: path } });
        toast("默认目录已更新", path, "success");
        refreshBootstrap({ preserveForm: true, silent: true }).catch(() => {});
      } catch (err) {
        toast("保存失败", err.message, "error");
      }
    } else {
      if ($("#downloadDir")) $("#downloadDir").value = path;
      updateSummary();
      updateFreeSpace(path);
    }
    $("#folderDialog").close();
  }

  async function createFolder() {
    const name = $("#folderNewName").value.trim();
    if (!name) {
      toast("请输入文件夹名称", "", "warn");
      return;
    }
    try {
      const data = await api("/api/fs/mkdir", {
        method: "POST",
        body: { path: state.folder.path || "", name },
      });
      $("#folderNew").hidden = true;
      $("#folderNewName").value = "";
      toast("文件夹已创建", "", "success");
      await loadFolder(data.path);
    } catch (err) {
      toast("创建失败", err.message, "error");
    }
  }

  /* ------------------------------------------------------------------ */
  /* Event binding                                                      */
  /* ------------------------------------------------------------------ */

  function bindNavigation() {
    window.addEventListener("hashchange", () => renderRoute(viewFromHash()));
    document.addEventListener("click", (event) => {
      const link = event.target.closest("a[href^='#/']");
      if (link) closeSidebar();
    });
    $("#menuBtn")?.addEventListener("click", toggleSidebar);
    $("#sidebarScrim")?.addEventListener("click", closeSidebar);
    $("#themeBtn")?.addEventListener("click", toggleTheme);
    $("#refreshBtn")?.addEventListener("click", async () => {
      const button = $("#refreshBtn");
      button.classList.add("spinning");
      try {
        if (state.activeView === "gallery") {
          state.gallery.forceRefresh = true;
          await loadGallery(true);
          toast("图库已刷新", "", "success");
        } else {
          await refreshBootstrap({ preserveForm: true });
          await pollJobs(true);
          toast("数据已刷新", "", "success");
        }
      } catch (err) {
        toast("刷新失败", err.message, "error");
      } finally {
        setTimeout(() => button.classList.remove("spinning"), 500);
      }
    });
    $("#sideAccount")?.addEventListener("click", () => {
      if (state.config?.username && state.activeView !== "settings") navigate("settings");
      else openAuthDialog();
    });
    $("#sessionChip")?.addEventListener("click", () => {
      if (state.engine?.available && state.config?.username && state.activeView !== "settings") navigate("settings");
      else openAuthDialog();
    });
    window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
      if (state.themeChoice === "system") applyTheme("system", false);
    });
    document.addEventListener("keydown", (event) => {
      if (!event.altKey || event.ctrlKey || event.metaKey) return;
      const map = { "1": "dashboard", "2": "download", "3": "jobs", "4": "gallery", "5": "settings" };
      if (map[event.key]) {
        event.preventDefault();
        navigate(map[event.key]);
      }
    });
  }

  function bindDashboard() {
    $("#heroPreviewBtn")?.addEventListener("click", () => goDownload({ preview: true }));
    $("#changeDirBtn")?.addEventListener("click", () => openFolderPicker(state.config?.download_dir || "", "quick"));
    $("#openFolderBtn")?.addEventListener("click", async () => {
      try {
        await api("/api/reveal", { method: "POST", body: { path: "" } });
      } catch (err) {
        toast("无法打开文件夹", err.message, "error");
      }
    });
    $$("[data-quick]").forEach((button) => {
      button.addEventListener("click", () => {
        const mode = button.dataset.quick;
        if (mode === "all") goDownload({ scope: "all" });
        else if (mode === "recent") goDownload({ scope: "recent", recent: 100 });
        else goDownload({ preview: true });
      });
    });
    $("#toastRoot")?.addEventListener("click", (event) => {
      if (event.target.closest(".toast-close")) event.target.closest(".toast").remove();
    });
  }

  function bindForm() {
    $("#downloadForm")?.addEventListener("submit", onSubmit);
    $("#loginBtn")?.addEventListener("click", openAuthDialog);
    $("#username")?.addEventListener("input", () => {
      updateSummary();
      renderSession();
    });
    $("#domainSeg")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-domain]");
      if (!button) return;
      setDomainButtons($("#domainSeg"), button.dataset.domain);
      updateSummary();
    });
    $("#password")?.addEventListener("input", updateSummary);
    $("#revealPassword")?.addEventListener("click", () => {
      const input = $("#password");
      input.type = input.type === "password" ? "text" : "password";
    });
    $("#scopeGrid")?.addEventListener("change", (event) => {
      if (event.target.name === "scope") setScope(event.target.value);
    });
    ["#mediaPhotos", "#mediaVideos", "#mediaLive", "#previewMode", "#dryRun", "#xmpSidecar", "#setExif", "#keepUnicode", "#deleteAfter", "#autoDelete", "#keepRecentEnable", "#sizeSelect", "#folderStructure", "#matchPolicy", "#untilFound", "#livePhotoPolicy", "#alignRaw", "#keepDays"].forEach((selector) => {
      $(selector)?.addEventListener("change", () => {
        if (selector === "#keepRecentEnable") {
          $("#keepDaysField").hidden = !$("#keepRecentEnable").checked;
        }
        updateSummary();
      });
      $(selector)?.addEventListener("input", updateSummary);
    });
    $("#recentCount")?.addEventListener("input", updateSummary);
    $("#dateFrom")?.addEventListener("change", updateSummary);
    $("#dateTo")?.addEventListener("change", updateSummary);
    $("#downloadDir")?.addEventListener("input", () => {
      updateSummary();
      debouncedFreeSpace($("#downloadDir").value);
    });
    $("#browseDirBtn")?.addEventListener("click", () => openFolderPicker($("#downloadDir")?.value || "", "download"));
    $("#loadAlbumsBtn")?.addEventListener("click", loadAlbums);
    $("#albumChips")?.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-album]");
      if (chip) toggleAlbum(chip.dataset.album);
    });
    $("#recentDirsBtn")?.addEventListener("click", () => toggleRecentDirs());
    $("#recentDirs")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-recent-dir]");
      if (!button) return;
      $("#downloadDir").value = button.dataset.recentDir;
      toggleRecentDirs(false);
      updateSummary();
      updateFreeSpace(button.dataset.recentDir);
    });
    $("#copyCommandBtn")?.addEventListener("click", async () => {
      const ok = await copyText($("#commandPreview").textContent);
      toast(ok ? "命令已复制" : "复制失败", ok ? "密码不会包含在预览中。" : "请手动选择文本复制。", ok ? "success" : "warn");
    });
    $("#authForm")?.addEventListener("submit", onAuthSubmit);
    $("#authReveal")?.addEventListener("click", () => {
      const input = $("#authPassword");
      input.type = input.type === "password" ? "text" : "password";
    });
    $("#authDomainSeg")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-domain]");
      if (button) setDomainButtons($("#authDomainSeg"), button.dataset.domain);
    });
  }

  function bindJobs() {
    $("#jobList")?.addEventListener("click", (event) => {
      const item = event.target.closest("[data-job-id]");
      if (item) selectJob(item.dataset.jobId);
    });
    $("#jobStopBtn")?.addEventListener("click", stopSelectedJob);
    $("#jobRefreshBtn")?.addEventListener("click", () => {
      if (state.selectedJobId) selectJob(state.selectedJobId, { force: true });
      pollJobs(true);
    });
    $("#jobPromptSend")?.addEventListener("click", sendPrompt);
    $("#jobManualInput")?.addEventListener("click", () => {
      const job = state.jobs.find((item) => item.id === state.selectedJobId);
      if (!job || job.status !== "running") return;
      state.manualPromptJobId = job.id;
      state.promptSignature = "";
      renderPrompt(job);
    });
    $("#jobPromptInput")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        sendPrompt();
      }
    });
    $("#jobClearBtn")?.addEventListener("click", clearJobs);
    $("#autoScroll")?.addEventListener("change", () => {
      if ($("#autoScroll").checked) {
        const box = $("#jobLog");
        box.scrollTop = box.scrollHeight;
      }
    });
  }

  function bindGallery() {
    const grid = $("#galleryGrid");
    grid?.addEventListener("click", (event) => {
      const item = event.target.closest(".gallery-item");
      if (item) openLightbox(Number(item.dataset.index));
    });
    grid?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        const item = event.target.closest(".gallery-item");
        if (item) {
          event.preventDefault();
          openLightbox(Number(item.dataset.index));
        }
      }
    });
    grid?.addEventListener("error", (event) => {
      if (event.target.tagName === "IMG") galleryFallback(event.target);
    }, true);
    grid?.addEventListener("load", (event) => {
      if (event.target.tagName === "IMG") event.target.classList.add("loaded");
    }, true);
    $("#gallerySearch")?.addEventListener("input", debounce(() => {
      state.gallery.q = $("#gallerySearch").value.trim();
      loadGallery(true);
    }, 320));
    $("#galleryFilters")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-kind]");
      if (!button) return;
      $$("#galleryFilters button").forEach((btn) => btn.classList.toggle("active", btn === button));
      state.gallery.kind = button.dataset.kind;
      loadGallery(true);
    });
    $("#gallerySort")?.addEventListener("change", () => {
      state.gallery.sort = $("#gallerySort").value;
      loadGallery(true);
    });
    $("#galleryMore")?.addEventListener("click", () => {
      state.gallery.offset += 60;
      loadGallery(false);
    });
    $("#galleryRefresh")?.addEventListener("click", async () => {
      state.gallery.forceRefresh = true;
      await loadGallery(true);
      toast("图库已刷新", "已重新扫描本地目录。", "success");
    });
    $("#lightboxClose")?.addEventListener("click", () => $("#lightboxDialog").close());
    $("#lightboxPrev")?.addEventListener("click", () => navigateLightbox(-1));
    $("#lightboxNext")?.addEventListener("click", () => navigateLightbox(1));
    $("#lightboxReveal")?.addEventListener("click", () => revealItem("reveal"));
    $("#lightboxOpen")?.addEventListener("click", () => revealItem("open"));
    $("#lightboxDialog")?.addEventListener("close", () => {
      $("#lightboxStage").innerHTML = "";
    });
    $("#lightboxDialog")?.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        navigateLightbox(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        navigateLightbox(1);
      }
    });
    $("#lightboxDialog")?.addEventListener("click", (event) => {
      if (event.target === $("#lightboxDialog")) $("#lightboxDialog").close();
    });
  }

  function bindSettings() {
    $("#setDomainSeg")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-domain]");
      if (button) setDomainButtons($("#setDomainSeg"), button.dataset.domain);
    });
    $("#saveSettingsBtn")?.addEventListener("click", saveSettings);
    $("#setBrowseDownload")?.addEventListener("click", () => openFolderPicker($("#setDownloadDir")?.value || "", "settings"));
    $("#settingsLoginBtn")?.addEventListener("click", () => {
      if ($("#username")) $("#username").value = $("#setUsername")?.value || state.config?.username || "";
      openAuthDialog();
    });
    $("#clearHistoryBtn")?.addEventListener("click", clearJobs);
    $("#themeOptions")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-theme-choice]");
      if (button) {
        applyTheme(button.dataset.themeChoice);
        fillSettings();
      }
    });
  }

  function bindFolder() {
    $("#folderList")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-folder-path]");
      if (button) loadFolder(button.dataset.folderPath);
    });
    $("#folderShortcuts")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-folder-path]");
      if (button) loadFolder(button.dataset.folderPath);
    });
    $("#folderDrives")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-folder-path]");
      if (button) loadFolder(button.dataset.folderPath);
    });
    $("#folderThisPc")?.addEventListener("click", () => loadFolder(""));
    $("#folderUp")?.addEventListener("click", () => loadFolder(state.folder.parent || ""));
    $("#folderChoose")?.addEventListener("click", chooseFolder);
    $("#folderMkdir")?.addEventListener("click", () => {
      $("#folderNew").hidden = false;
      $("#folderNewName").focus();
    });
    $("#folderNewCancel")?.addEventListener("click", () => {
      $("#folderNew").hidden = true;
      $("#folderNewName").value = "";
    });
    $("#folderNewConfirm")?.addEventListener("click", createFolder);
    $("#folderNewName")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") createFolder();
    });
  }

  function bindDialogs() {
    $$("[data-close-dialog]").forEach((button) => {
      button.addEventListener("click", () => {
        const dialog = document.getElementById(button.dataset.closeDialog);
        if (dialog?.open) dialog.close();
      });
    });
    $$("dialog").forEach((dialog) => {
      dialog.addEventListener("click", (event) => {
        if (event.target !== dialog) return;
        if (dialog.id === "lightboxDialog") {
          // Full-screen lightbox: clicking the stage background closes it.
          dialog.close();
        } else {
          const rect = dialog.getBoundingClientRect();
          const inside = event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
          if (!inside) dialog.close();
        }
      });
    });
    $("#authDialog")?.addEventListener("close", () => {
      $("#authPassword").value = "";
    });
  }

  /* ------------------------------------------------------------------ */
  /* Boot                                                               */
  /* ------------------------------------------------------------------ */

  async function boot() {
    applyTheme(state.themeChoice, false);
    bindNavigation();
    bindDashboard();
    bindForm();
    bindJobs();
    bindGallery();
    bindSettings();
    bindFolder();
    bindDialogs();
    updateSummary();

    try {
      await refreshBootstrap({ preserveForm: false, silent: true });
      renderRoute(viewFromHash());
      state.pollTimer = setInterval(() => pollJobs(false), 1500);
      if (!state.engine?.available) {
        setTimeout(() => toast("未找到 icloudpd", "请安装 icloudpd 并确保它位于 PATH 中，然后重新启动 UI。", "error", 8000), 600);
      }
    } catch (error) {
      toast("无法连接本地服务", error.message, "error", 8000);
      $("#pageScroll").innerHTML = `<div class="fatal-error"><h1>无法连接后端</h1><p>${esc(error.message)}</p><p>请确认 <code>python app.py</code> 仍在运行，然后刷新页面。</p></div>`;
    }
  }

  boot();
})();
