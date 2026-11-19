(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const speed = reducedMotion ? 0.15 : 1;

  const state = {
    account: "",
    region: "com",
    media: new Set(["photo", "video", "live"]),
    scope: "recent",
    recent: 500,
    dateFrom: "2026-01-01",
    dateTo: "2026-06-30",
    album: "旅行",
    directory: "D:\\iCloudPD-Demo\\Photos",
    previewOnly: false,
  };

  const demoFiles = [
    "IMG_0004.JPG",
    "IMG_0002.JPG",
    "IMG_0001.JPG",
    "IMG_0003.JPG",
    "IMG_0005.JPG",
    "IMG_0007.MOV",
  ];

  const el = {
    form: $("#sync-form"),
    account: $("#account"),
    directory: $("#directory"),
    recentCount: $("#recent-count"),
    dateFrom: $("#date-from"),
    dateTo: $("#date-to"),
    albumName: $("#album-name"),
    previewOnly: $("#preview-only"),
    sumAccount: $("#sum-account"),
    sumRegion: $("#sum-region"),
    sumScope: $("#sum-scope"),
    sumMedia: $("#sum-media"),
    sumDir: $("#sum-dir"),
    sumMode: $("#sum-mode"),
    command: $("#command-preview code"),
    copy: $("#copy-command"),
    start: $("#start-sync"),
    reset: $("#reset-demo"),
    terminal: $("#terminal"),
    log: $("#log"),
    status: $("#terminal-status"),
    mfaPanel: $("#mfa-panel"),
    mfaCode: $("#mfa-code"),
    mfaSubmit: $("#mfa-submit"),
    mfaFill: $("#mfa-fill"),
    progress: $("#progress"),
    progressBar: $("#progress-bar"),
    progressLabel: $("#progress-label"),
    countDone: $("#count-done"),
    countSkip: $("#count-skip"),
    countError: $("#count-error"),
    countTime: $("#count-time"),
    gallery: $("#result-gallery"),
    galleryGrid: $("#gallery-grid"),
    gallerySearch: $("#gallery-search"),
    galleryEmpty: $("#gallery-empty"),
    resultTotal: $("#result-total"),
  };

  /* ---------- command + summary ---------- */

  const quote = (value) => `"${String(value).replace(/"/g, '\\"')}"`;

  function buildCommand() {
    const account = state.account.trim() || "name@example.com";
    const directory = state.directory.trim() || "D:\\iCloudPD-Demo\\Photos";
    const parts = [
      "icloudpd",
      "--username", quote(account),
      "--directory", quote(directory),
    ];
    if (state.region === "cn") parts.push("--domain", "cn");
    parts.push("--size", "original", "--folder-structure", quote("{:%Y/%m/%d}"));

    if (state.scope === "recent") {
      parts.push("--recent", String(Math.max(1, Number(state.recent) || 1)));
    } else if (state.scope === "range") {
      if (state.dateFrom) parts.push("--skip-created-before", state.dateFrom);
      if (state.dateTo) parts.push("--skip-created-after", state.dateTo);
    } else if (state.scope === "album") {
      parts.push("--album", quote(state.album.trim() || "旅行"));
    }

    if (!state.media.has("photo")) parts.push("--skip-photos");
    if (!state.media.has("video")) parts.push("--skip-videos");
    if (!state.media.has("live")) parts.push("--skip-live-photos");

    parts.push("--skip-existing", "--no-progress-bar", "--log-level", "info");
    if (state.previewOnly) parts.push("--dry-run");
    return parts.join(" ");
  }

  function scopeLabel() {
    if (state.scope === "all") return "全部内容";
    if (state.scope === "recent") return `最近 ${Math.max(1, Number(state.recent) || 1)} 张`;
    if (state.scope === "range") return `${state.dateFrom || "最早"} → ${state.dateTo || "最新"}`;
    return `相册「${state.album.trim() || "旅行"}」`;
  }

  function mediaLabel() {
    const map = { photo: "照片", video: "视频", live: "Live Photo" };
    const picked = ["photo", "video", "live"].filter((k) => state.media.has(k)).map((k) => map[k]);
    return picked.length ? picked.join(" · ") : "未选择";
  }

  function render() {
    const command = buildCommand();
    el.sumAccount.textContent = state.account.trim() || "name@example.com";
    el.sumRegion.textContent = state.region === "cn" ? "中国大陆（.cn）" : "全球（.com）";
    el.sumScope.textContent = scopeLabel();
    el.sumMedia.textContent = mediaLabel();
    el.sumDir.textContent = state.directory.trim() || "D:\\iCloudPD-Demo\\Photos";
    el.sumDir.title = el.sumDir.textContent;
    el.sumMode.textContent = state.previewOnly ? "仅预览（--dry-run）" : "正式同步";
    el.command.textContent = command;
  }

  /* ---------- form events ---------- */

  el.account.addEventListener("input", () => {
    state.account = el.account.value;
    render();
  });

  el.directory.addEventListener("input", () => {
    state.directory = el.directory.value;
    render();
  });

  el.recentCount.addEventListener("input", () => {
    state.recent = el.recentCount.value;
    render();
  });

  el.dateFrom.addEventListener("change", () => {
    state.dateFrom = el.dateFrom.value;
    render();
  });
  el.dateTo.addEventListener("change", () => {
    state.dateTo = el.dateTo.value;
    render();
  });

  el.albumName.addEventListener("input", () => {
    state.album = el.albumName.value;
    render();
  });

  el.previewOnly.addEventListener("change", () => {
    state.previewOnly = el.previewOnly.checked;
    render();
  });

  $$(".seg").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.region = btn.dataset.region;
      $$(".seg").forEach((b) => {
        const on = b === btn;
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-pressed", String(on));
      });
      render();
    });
  });

  $$(".toggle-card input").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.id.replace("media-", "");
      if (input.checked) state.media.add(key);
      else state.media.delete(key);
      input.closest(".toggle-card").classList.toggle("is-on", input.checked);
      render();
    });
  });

  $$('input[name="scope"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      state.scope = radio.value;
      $$(".scope-card").forEach((card) => {
        card.classList.toggle("is-active", card.contains(radio));
      });
      $$(".scope-extra").forEach((extra) => {
        extra.hidden = extra.dataset.extra !== state.scope;
      });
      render();
    });
  });

  el.copy.addEventListener("click", async () => {
    const text = buildCommand();
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    const old = el.copy.textContent;
    el.copy.textContent = "已复制";
    setTimeout(() => { el.copy.textContent = old; }, 1600);
  });

  /* ---------- demo run ---------- */

  const run = {
    active: false,
    waitingMfa: false,
    timers: new Set(),
    startedAt: 0,
    clock: null,
    resolveMfa: null,
    mfaAuto: null,
    token: 0,
  };
  const alive = (token) => token === run.token && run.active;

  const wait = (ms) => new Promise((resolve) => {
    const id = setTimeout(() => {
      run.timers.delete(id);
      resolve();
    }, Math.max(10, ms * speed));
    run.timers.add(id);
  });

  function clearRunTimers() {
    run.timers.forEach((id) => clearTimeout(id));
    run.timers.clear();
    if (run.clock) {
      clearInterval(run.clock);
      run.clock = null;
    }
  }

  function log(text, cls = "") {
    const span = document.createElement("span");
    span.className = "ln" + (cls ? " " + cls : "");
    span.textContent = text;
    el.log.appendChild(span);
    el.log.scrollTop = el.log.scrollHeight;
  }

  function setStatus(text, cls = "") {
    el.status.textContent = text;
    el.status.className = "terminal-status" + (cls ? " " + cls : "");
  }

  function setProgress(value) {
    const pct = Math.max(0, Math.min(100, Math.round(value)));
    el.progressBar.style.width = pct + "%";
    el.progressLabel.textContent = pct + "%";
    el.progress.setAttribute("aria-valuenow", String(pct));
  }

  function setCounts(done, skip, error) {
    el.countDone.textContent = done;
    el.countSkip.textContent = skip;
    el.countError.textContent = error;
  }

  function tickClock() {
    const seconds = Math.floor((Date.now() - run.startedAt) / 1000);
    const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
    const ss = String(seconds % 60).padStart(2, "0");
    el.countTime.textContent = `${mm}:${ss}`;
  }

  function resetLog() {
    el.log.innerHTML = "";
    log("$ icloudpd --version", "muted");
    log("version: 1.32.3", "ok");
    log("引擎就绪。配置左侧选项，然后开始模拟同步。", "muted");
  }

  async function askMfa() {
    run.waitingMfa = true;
    setStatus("等待双重认证", "running");
    log("等待双重认证验证码…", "warn");
    el.mfaPanel.hidden = false;
    el.mfaCode.value = "";
    el.mfaCode.focus();

    const code = await new Promise((resolve) => {
      run.resolveMfa = resolve;
      run.mfaAuto = setTimeout(() => {
        if (!run.waitingMfa) return;
        el.mfaCode.value = "123456";
        log("演示：15 秒未输入，自动填入 123456。", "muted");
        submitMfa();
      }, 15000);
    });

    run.waitingMfa = false;
    el.mfaPanel.hidden = true;
    return code;
  }

  function submitMfa() {
    if (!run.waitingMfa) return;
    const value = el.mfaCode.value.replace(/\D/g, "");
    if (value.length !== 6) {
      el.mfaCode.focus();
      el.mfaCode.setAttribute("aria-invalid", "true");
      log("验证码需要 6 位数字（演示：任意 6 位）。", "err");
      return;
    }
    el.mfaCode.removeAttribute("aria-invalid");
    run.waitingMfa = false;
    if (run.mfaAuto) {
      clearTimeout(run.mfaAuto);
      run.mfaAuto = null;
    }
    const resolve = run.resolveMfa;
    run.resolveMfa = null;
    if (resolve) resolve(value);
  }

  el.mfaSubmit.addEventListener("click", submitMfa);
  el.mfaFill.addEventListener("click", () => {
    el.mfaCode.value = "123456";
    el.mfaCode.focus();
  });
  el.mfaCode.addEventListener("input", () => {
    el.mfaCode.value = el.mfaCode.value.replace(/\D/g, "").slice(0, 6);
  });
  el.mfaCode.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submitMfa();
    }
  });

  async function startSync() {
    if (run.active) return;
    if (state.media.size === 0) {
      resetLog();
      log("错误：至少选择一种媒体类型（照片 / 视频 / Live Photo）。", "err");
      setStatus("未开始", "");
      return;
    }

    const token = ++run.token;
    run.active = true;
    run.waitingMfa = false;
    run.startedAt = Date.now();
    clearRunTimers();
    setProgress(0);
    setCounts(0, 0, 0);
    el.countTime.textContent = "00:00";
    resetLog();
    el.gallery.hidden = true;
    el.mfaPanel.hidden = true;
    el.start.disabled = true;
    el.start.textContent = "正在模拟同步…";
    el.reset.hidden = true;
    run.clock = setInterval(tickClock, 1000);
    setStatus("运行中", "running");

    await wait(220);
    if (!alive(token)) return;
    log("$ " + buildCommand(), "accent");
    await wait(520);
    if (!alive(token)) return;
    log("正在读取 ~/.pyicloud 中的会话…", "muted");

    const hasSession = false;
    if (hasSession) {
      log("找到有效会话，跳过密码验证。", "ok");
    } else {
      await wait(480);
      if (!alive(token)) return;
      log(`未找到可用会话，开始登录 ${state.account.trim() || "name@example.com"}`, "warn");
      await wait(640);
      if (!alive(token)) return;
      log("[icloudpd] 密码验证通过", "ok");
      await wait(480);
      if (!alive(token)) return;
      const code = await askMfa();
      if (!code || !alive(token)) return;
      log("验证码通过，正在获取照片清单…", "ok");
    }

    await wait(700);
    if (!alive(token)) return;
    log("发现 6 个待下载文件（演示清单）", "muted");
    setProgress(6);

    if (state.previewOnly) {
      for (let i = 0; i < demoFiles.length; i += 1) {
        await wait(260);
        if (!alive(token)) return;
        log(`[dry-run] 将下载 ${demoFiles[i]}`, "muted");
        setProgress(10 + ((i + 1) / demoFiles.length) * 84);
      }
      await wait(420);
      if (!alive(token)) return;
      setProgress(100);
      setCounts(0, 0, 0);
      setStatus("预览完成", "done");
      log("预演完成：6 个文件待下载，未写入磁盘。", "ok");
      finishRun(true);
      return;
    }

    for (let i = 0; i < demoFiles.length; i += 1) {
      await wait(420);
      if (!alive(token)) return;
      log(`[${i + 1}/6] 下载 ${demoFiles[i]} … 完成`, "ok");
      setProgress(10 + ((i + 1) / demoFiles.length) * 84);
      setCounts(i + 1, 0, 0);
    }

    await wait(520);
    if (!alive(token)) return;
    setProgress(100);
    setStatus("同步完成", "done");
    log("完成：6 个已下载，0 个跳过，0 个错误。", "ok");
    log("文件已按目录结构写入 " + (state.directory.trim() || "D:\\iCloudPD-Demo\\Photos"), "muted");
    finishRun(false);
  }

  function finishRun(preview) {
    run.active = false;
    clearRunTimers();
    el.start.disabled = false;
    el.start.innerHTML = "再跑一次";
    el.reset.hidden = false;
    el.resultTotal.textContent = preview ? "6 个待下载" : "6 个文件";
    el.gallery.hidden = false;
    applyGalleryFilter();

    if (preview) {
      const sub = $(".gallery-sub");
      sub.textContent = "仅预览：这些文件尚未写入磁盘。演示素材用于展示真实应用里的本地图库。";
    }

    if (el.gallery.getBoundingClientRect().top > window.innerHeight * 0.8) {
      el.gallery.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
    }
  }

  function resetDemo() {
    run.token += 1;
    run.active = false;
    run.waitingMfa = false;
    if (run.mfaAuto) {
      clearTimeout(run.mfaAuto);
      run.mfaAuto = null;
    }
    const pending = run.resolveMfa;
    run.resolveMfa = null;
    clearRunTimers();
    if (pending) pending(null);
    el.mfaPanel.hidden = true;
    el.start.disabled = false;
    el.start.innerHTML = '开始模拟同步 <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6"/></svg>';
    el.reset.hidden = true;
    el.gallery.hidden = true;
    $(".gallery-sub").textContent = "演示素材，用于展示真实应用里的本地图库；不是任何账户的真实照片。";
    setStatus("等待开始", "");
    setProgress(0);
    setCounts(0, 0, 0);
    el.countTime.textContent = "00:00";
    resetLog();
  }

  el.start.addEventListener("click", () => {
    if (run.active) return;
    if (el.start.textContent.includes("再跑一次")) resetDemo();
    startSync();
  });
  el.reset.addEventListener("click", resetDemo);

  /* ---------- gallery ---------- */

  let filter = "all";
  let lightboxIndex = -1;
  let lastFocus = null;

  function visibleCards() {
    return $$(".photo-card", el.galleryGrid).filter((card) => !card.hidden);
  }

  function applyGalleryFilter() {
    const query = (el.gallerySearch.value || "").trim().toUpperCase();
    $$(".photo-card", el.galleryGrid).forEach((card) => {
      const typeOk = filter === "all" || card.dataset.type === filter;
      const nameOk = !query || card.dataset.name.toUpperCase().includes(query);
      card.hidden = !(typeOk && nameOk);
    });
    el.galleryEmpty.hidden = visibleCards().length !== 0;
  }

  $$(".chip", el.galleryGrid.parentElement).forEach((chip) => {
    chip.addEventListener("click", () => {
      filter = chip.dataset.filter;
      $$(".chip").forEach((c) => c.classList.toggle("is-active", c === chip));
      applyGalleryFilter();
    });
  });

  el.gallerySearch.addEventListener("input", applyGalleryFilter);

  function photoCards() {
    return visibleCards().filter((card) => Boolean(card.dataset.src));
  }

  function openLightbox(card) {
    const cards = photoCards();
    const index = cards.indexOf(card);
    if (index < 0) return;
    lastFocus = document.activeElement;
    lightboxIndex = index;
    updateLightbox();
    $("#lightbox").hidden = false;
    document.body.style.overflow = "hidden";
    $("#lightbox-close").focus();
  }

  function updateLightbox() {
    const cards = photoCards();
    if (!cards.length) return;
    if (lightboxIndex >= cards.length) lightboxIndex = 0;
    if (lightboxIndex < 0) lightboxIndex = cards.length - 1;
    const card = cards[lightboxIndex];
    const img = $("#lightbox-img");
    img.src = card.dataset.src;
    img.alt = card.querySelector("img") ? card.querySelector("img").alt : card.dataset.name;
    $("#lightbox-caption").textContent = `${card.dataset.name} · ${card.dataset.size}`;
    const showNav = cards.length > 1;
    $("#lightbox-prev").hidden = !showNav;
    $("#lightbox-next").hidden = !showNav;
  }

  function closeLightbox() {
    $("#lightbox").hidden = true;
    document.body.style.overflow = "";
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
    lastFocus = null;
  }

  el.galleryGrid.addEventListener("click", (event) => {
    const card = event.target.closest(".photo-card");
    if (!card || !card.dataset.src) return;
    openLightbox(card);
  });

  $("#lightbox-close").addEventListener("click", closeLightbox);
  $("#lightbox").addEventListener("click", (event) => {
    if (event.target.id === "lightbox") closeLightbox();
  });
  $("#lightbox-prev").addEventListener("click", () => {
    lightboxIndex -= 1;
    updateLightbox();
  });
  $("#lightbox-next").addEventListener("click", () => {
    lightboxIndex += 1;
    updateLightbox();
  });
  document.addEventListener("keydown", (event) => {
    if ($("#lightbox").hidden) return;
    if (event.key === "Escape") closeLightbox();
    if (event.key === "ArrowLeft") {
      lightboxIndex -= 1;
      updateLightbox();
    }
    if (event.key === "ArrowRight") {
      lightboxIndex += 1;
      updateLightbox();
    }
  });

  /* ---------- misc ---------- */

  const header = $(".site-header");
  const onScroll = () => header.classList.toggle("scrolled", window.scrollY > 8);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  render();
})();
