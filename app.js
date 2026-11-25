/* 壹线样卡 · 手机独立版（全部在 Safari 里算，不连电脑） */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const MAX_SIDE = 3000;          // 送进引擎前把照片缩到这个长边，省内存
  const S = {
    photos: [],
    table: null,
    templateBytes: null,
    templateProfile: null,
    worker: null,
    ready: false,
    booting: false,
    engineInited: false,
    busy: false,
    cancelled: false,
    results: [],
    errors: [],
    stage: "准备",
    seq: 0,
  };

  /* ---------------- 小工具 ---------------- */

  let toastTimer = null;
  function toast(text, ok) {
    const el = $("toast");
    el.textContent = text;
    el.classList.toggle("ok", !!ok);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
  }

  function engine(text, cls) {
    const pill = $("enginePill");
    pill.textContent = text;
    pill.className = "engine" + (cls ? " " + cls : "");
  }

  function cleanModel(name) {
    return String(name || "")
      .replace(/\.[^.]+$/, "")
      .replace(/\s*[-_ ]?(副本|copy)\s*\d*$/i, "")
      .replace(/\(\d+\)$/, "")
      .trim();
  }

  const bytesText = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");

  /* ---------------- 照片读取 ---------------- */

  function loadImg(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("这张图片读不出来")); };
      img.src = url;
    });
  }

  async function normalize(file) {
    const img = await loadImg(file);
    const w = img.naturalWidth || img.width;
    const h = img.naturalHeight || img.height;
    const scale = Math.min(1, MAX_SIDE / Math.max(w, h));
    const cw = Math.max(1, Math.round(w * scale));
    const ch = Math.max(1, Math.round(h * scale));
    const canvas = document.createElement("canvas");
    canvas.width = cw;
    canvas.height = ch;
    const ctx = canvas.getContext("2d", { alpha: false });
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, cw, ch);
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, 0, 0, cw, ch);
    const blob = await new Promise((res, rej) => {
      canvas.toBlob((b) => (b ? res(b) : rej(new Error("转码失败"))), "image/jpeg", 0.93);
    });
    return { bytes: await blob.arrayBuffer(), thumb: URL.createObjectURL(blob), size: blob.size };
  }

  /* ---------------- 照片列表 ---------------- */

  function refreshPhotos() {
    const total = S.photos.length;
    const ready = S.photos.filter((p) => p.bytes).length;
    $("photoCount").textContent = !total ? "未选择"
      : (ready < total ? `${ready}/${total} 处理中` : `${total} 张`);
    $("goBtn").disabled = S.busy || ready === 0;
  }

  function photoRow(item) {
    const row = document.createElement("div");
    row.className = "pitem";
    row.innerHTML =
      `<img alt="" src="${item.thumb}">
       <div class="pinfo">
         <input type="text" spellcheck="false" value="${escapeAttr(item.model)}" placeholder="型号">
         <span class="fname">${escapeHtml(item.file.name)} · ${bytesText(item.size)}</span>
       </div>
       <button class="del" type="button" aria-label="删除">×</button>`;
    const input = row.querySelector("input");
    input.addEventListener("input", () => { item.model = input.value; });
    input.addEventListener("blur", () => {
      item.model = input.value.replace(/[\\/:*?"<>|]/g, "-").trim();
      input.value = item.model;
    });
    row.querySelector(".del").addEventListener("click", () => {
      const i = S.photos.indexOf(item);
      if (i >= 0) S.photos.splice(i, 1);
      URL.revokeObjectURL(item.thumb);
      row.remove();
      refreshPhotos();
    });
    item.el = row;
    return row;
  }

  async function addPhotos(files) {
    const list = Array.from(files || []);
    if (!list.length) return;
    if (!S.worker) bootEngine();          // 选完照片就开始预加载引擎
    for (const file of list) {
      if (!/^image\//.test(file.type) && !/\.(jpe?g|png|heic|heif|webp)$/i.test(file.name)) {
        toast("跳过不是图片的文件：" + file.name);
        continue;
      }
      const item = {
        id: "p" + (++S.seq), file, model: cleanModel(file.name),
        thumb: "", bytes: null, size: 0,
      };
      S.photos.push(item);
      const row = photoRow(item);
      row.classList.add("wait");
      $("photoList").appendChild(row);
      refreshPhotos();
      try {
        const norm = await normalize(file);
        item.bytes = norm.bytes;
        item.thumb = norm.thumb;
        item.size = norm.size;
        row.querySelector("img").src = norm.thumb;
        row.querySelector(".fname").textContent = `${file.name} · ${bytesText(norm.size)}`;
        row.classList.remove("wait");
        refreshPhotos();
      } catch (err) {
        toast(file.name + "：" + err.message);
        const i = S.photos.indexOf(item);
        if (i >= 0) S.photos.splice(i, 1);
        row.remove();
        refreshPhotos();
      }
    }
  }

  /* ---------------- 引擎 ---------------- */

  function bootEngine() {
    if (S.worker || S.booting) return;
    S.booting = true;
    engine("引擎加载中…", "busy");
    const worker = new Worker("worker.js", { type: "module" });
    S.worker = worker;
    worker.onmessage = onEngineMessage;
    worker.onerror = (e) => {
      S.booting = false;
      S.worker = null;
      engine("引擎出错", "");
      showDiag("Worker 启动失败", (e && e.message) || "worker.js 没能加载（多半是服务器 MIME 或文件缺失）");
    };
    worker.postMessage({ type: "boot" });
  }

  async function ensureEngine() {
    if (S.ready) return;
    if (!S.worker) bootEngine();
    await new Promise((resolve, reject) => {
      const started = Date.now();
      const timer = setInterval(() => {
        if (S.ready) { clearInterval(timer); resolve(); }
        else if (!S.worker) { clearInterval(timer); reject(new Error("引擎没起来")); }
        else if (Date.now() - started > 90000) {
          clearInterval(timer);
          reject(new Error("引擎加载超时（卡在「" + (S.stage || "准备") + "」）"));
        }
      }, 300);
    });
  }

  function showDiag(title, detail) {
    const info = [
      "时间：" + new Date().toLocaleString(),
      "页面：" + location.href,
      "协议：" + location.protocol,
      "浏览器：" + navigator.userAgent,
      "引擎阶段：" + (S.stage || "未开始"),
      "问题：" + title,
      detail ? "详情：" + detail : "",
      "设备内存提示：" + (navigator.deviceMemory || "未知"),
    ].filter(Boolean).join("\n");
    $("diagText").textContent = info;
    $("diag").hidden = false;
    $("diag").scrollIntoView({ behavior: "smooth", block: "center" });
    engine("引擎出错", "");
  }

  function onEngineMessage(ev) {
    const msg = ev.data || {};
    switch (msg.type) {
      case "boot":
        S.stage = msg.stage || S.stage;
        engine(msg.text ? msg.text.slice(0, 10) : "加载中", "busy");
        if (msg.text && !$("prog").hidden) $("progText").textContent = msg.text;
        break;
      case "ready":
        S.ready = true;
        S.booting = false;
        S.stage = "就绪";
        $("diag").hidden = true;
        engine("引擎就绪", "ok");
        break;
      case "init-ok":
      case "template-ok":
        if (msg.profile) S.templateProfile = msg.profile;
        break;
      case "card":
        addResult(msg);
        break;
      case "card-error":
        S.errors.push({ name: msg.id, message: msg.message });
        renderErrors();
        break;
      case "render-done":
        finishRender();
        break;
      case "fatal":
        S.booting = false;
        endBusy();
        showDiag("引擎在「" + (msg.stage || S.stage || "未知") + "」阶段失败", msg.message);
        break;
      default:
        break;
    }
  }

  /* ---------------- 生成 ---------------- */

  async function start() {
    if (S.busy) return;
    const ready = S.photos.filter((p) => p.bytes);
    if (!ready.length) { toast("请先选择照片"); return; }

    S.busy = true;
    S.cancelled = false;
    S.errors = [];
    S.results = [];
    S.pending = ready.length;
    $("goBtn").disabled = true;
    $("goBtn").textContent = "正在生成…";
    $("errBox").hidden = true;
    $("result").hidden = true;
    $("grid").innerHTML = "";
    $("prog").hidden = false;
    $("barFill").style.width = "0%";
    $("progText").textContent = S.ready ? "正在准备…" : "正在加载出卡引擎（首次约 10–20 秒）…";

    try {
      await loadTemplateAssets();
      await ensureEngine();
      if (!S.engineInited) {
        await initTemplate();
        S.engineInited = true;
      }

      const worker = S.worker;
      if (S.table) {
        worker.postMessage({ type: "table", bytes: S.table.bytes, name: S.table.name });
        S.table.dirty = false;
      } else {
        worker.postMessage({ type: "table", bytes: null });
      }

      const jobs = ready.map((p) => ({
        id: p.id,
        model: (p.model || "").replace(/[\\/:*?"<>|]/g, "-").trim(),
        bytes: p.bytes,
      }));
      worker.postMessage({
        type: "render",
        jobs,
        params: {
          mode: currentMode(),
          coverage: Number($("cov").value),
          maxZoom: 3,
          maxCount: 4,
          swapNote: $("swapNote").checked ? "换色" : "",
          kind: "auto",
          quality: 95,
        },
      });
      $("progText").textContent = `共 ${jobs.length} 张，正在出第一张…`;
    } catch (err) {
      toast(err.message);
      endBusy();
      $("prog").hidden = true;
      showDiag("生成前准备失败", err.message);
    }
  }

  async function loadTemplateAssets() {
    if (S.templateBytes && S.templateProfile) return;
    if (!S.templateBytes) {
      const res = await fetch("assets/template.jpg", { cache: "force-cache" });
      S.templateBytes = await res.arrayBuffer();
    }
    if (!S.templateProfile) {
      const res = await fetch("assets/template.json", { cache: "force-cache" });
      S.templateProfile = await res.arrayBuffer();
    }
  }

  async function initTemplate() {
    const worker = S.worker;
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        worker.removeEventListener("message", handler);
        resolve();
      }, 12000);
      const handler = (ev) => {
        if (ev.data && (ev.data.type === "init-ok" || ev.data.type === "fatal")) {
          clearTimeout(timer);
          worker.removeEventListener("message", handler);
          resolve();
        }
      };
      worker.addEventListener("message", handler);
      worker.postMessage({
        type: "init",
        template: S.templateBytes,
        profile: S.templateProfile,
      });
    });
  }

  function currentMode() {
    const btn = document.querySelector("#segMode button.on");
    return btn ? btn.dataset.mode : "auto";
  }

  function addResult(msg) {
    const blob = new Blob([msg.bytes], { type: "image/jpeg" });
    const url = URL.createObjectURL(blob);
    const info = msg.info || {};
    S.results.push({ name: info.name || msg.id, url, blob });

    const card = document.createElement("div");
    card.className = "res";
    const modeText = { fill: "满幅", single: "单条", double: "并列", auto: "自动" }[info.mode] || info.mode || "";
    card.innerHTML =
      `<img alt="${escapeAttr(info.name || "")}" src="${url}">
       <div class="meta">
         <span class="nm">${escapeHtml(info.name || msg.id)}</span>
         <button class="sv" type="button">保存</button>
       </div>`;
    card.querySelector("img").addEventListener("click", () => openViewer(S.results[S.results.length - 1]));
    card.querySelector(".sv").addEventListener("click", () => saveOne(S.results[S.results.length - 1]));
    $("grid").appendChild(card);

    $("result").hidden = false;
    const total = msg.total || 1;
    $("barFill").style.width = Math.round((msg.index / total) * 100) + "%";
    $("progText").textContent = `已完成 ${msg.index}/${total}：${info.name || ""}（${modeText}${info.coverage ? ` · 占比 ${Math.round(info.coverage * 100)}%` : ""}）`;
  }

  function finishRender() {
    const ok = S.results.length;
    $("summary").textContent = ok ? `成功 ${ok} 张` : "没有成功";
    $("prog").hidden = true;
    $("result").hidden = ok === 0;
    endBusy();
    if (ok) {
      $("result").scrollIntoView({ behavior: "smooth", block: "start" });
      toast(`完成 ${ok} 张，点图保存`, true);
    }
  }

  function endBusy() {
    S.busy = false;
    $("goBtn").textContent = S.results.length ? "再生成一批" : "生成样卡";
    $("goBtn").disabled = S.photos.length === 0;
    refreshPhotos();
  }

  function renderErrors() {
    if (!S.errors || !S.errors.length) return;
    $("errBox").hidden = false;
    $("errBox").innerHTML = "有照片没出成功：<ul>" +
      S.errors.map((e) => `<li>${escapeHtml(e.name)}：${escapeHtml(e.message)}</li>`).join("") + "</ul>";
  }

  /* ---------------- 保存 ---------------- */

  function fileOf(item) {
    return new File([item.blob], item.name + ".jpg", { type: "image/jpeg" });
  }

  function canShareFiles(files) {
    try {
      return !!(navigator.canShare && navigator.share && navigator.canShare({ files }));
    } catch (e) { return false; }
  }

  async function saveOne(item) {
    const file = fileOf(item);
    if (canShareFiles([file])) {
      try { await navigator.share({ files: [file] }); return; } catch (e) { /* 用户取消 */ }
    }
    openViewer(item);
    toast("长按图片 →「存储图像」即可存进相册");
  }

  async function saveAll() {
    if (!S.results.length) return;
    const files = S.results.map(fileOf);
    if (canShareFiles(files)) {
      try { await navigator.share({ files }); return; } catch (e) { /* 取消 */ }
    }
    if (S.results.length === 1) { saveOne(S.results[0]); return; }
    toast("这台手机上只能一张张存：点开图 → 长按 → 存储图像");
  }

  function openViewer(item) {
    if (!item) return;
    $("viewerName").textContent = item.name;
    $("viewerImg").src = item.url;
    $("viewer").hidden = false;
    document.body.style.overflow = "hidden";
    $("viewerSave").onclick = () => saveOne(item);
  }

  function closeViewer() {
    $("viewer").hidden = true;
    $("viewerImg").removeAttribute("src");
    document.body.style.overflow = "";
  }

  /* ---------------- 表格 / 模板 ---------------- */

  async function pickTable(file) {
    if (!file) return;
    if (!S.worker) bootEngine();
    S.table = { name: file.name, bytes: await file.arrayBuffer(), dirty: true };
    $("tableName").textContent = file.name;
    $("clearTable").hidden = false;
    toast("规格表已选：" + file.name, true);
  }

  async function pickTemplate(file) {
    if (!file) return;
    try {
      engine("识别模板中…", "busy");
      await ensureEngine();
      const bytes = await file.arrayBuffer();
      S.worker.postMessage({ type: "template", bytes }, [bytes]);
      // 等识别的结果
      const info = await new Promise((resolve, reject) => {
        const t = setTimeout(() => reject(new Error("识别超时")), 30000);
        const handler = (ev) => {
          if (ev.data && ev.data.type === "template-ok") {
            clearTimeout(t);
            S.worker.removeEventListener("message", handler);
            resolve(ev.data);
          }
        };
        S.worker.addEventListener("message", handler);
      });
      // 引擎已经用新模板 init 过了，这里只记下来，避免下次又切回默认模板
      S.templateBytes = bytes;
      S.templateProfile = info.profile;
      S.engineInited = true;
      $("templateName").textContent = file.name;
      $("templateState").textContent = "自定义";
      engine("引擎就绪", "ok");
      toast(`模板已换：${info.frame_size[0]}×${info.frame_size[1]} 框，字号 ${info.font}`, true);
    } catch (err) {
      engine("引擎就绪", "ok");
      toast("模板识别失败：" + err.message);
    }
  }

  /* ---------------- 工具 ---------------- */

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }
  function escapeAttr(text) { return escapeHtml(text); }

  /* ---------------- 绑定 ---------------- */

  document.addEventListener("DOMContentLoaded", () => {
    $("filePhotos").addEventListener("change", (e) => {
      addPhotos(e.target.files);
      e.target.value = "";
    });
    $("fileTable").addEventListener("change", (e) => {
      pickTable(e.target.files[0]);
      e.target.value = "";
    });
    $("fileTemplate").addEventListener("change", (e) => {
      pickTemplate(e.target.files[0]);
      e.target.value = "";
    });
    $("clearTable").addEventListener("click", () => {
      S.table = null;
      $("tableName").textContent = "选择产品信息表";
      $("clearTable").hidden = true;
      if (S.worker) S.worker.postMessage({ type: "table", bytes: null });
    });
    $("segMode").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-mode]");
      if (!btn) return;
      document.querySelectorAll("#segMode button").forEach((b) => b.classList.remove("on"));
      btn.classList.add("on");
    });
    $("cov").addEventListener("input", () => { $("covVal").textContent = $("cov").value + "%"; });
    $("goBtn").addEventListener("click", () => {
      if (S.busy) return;
      if (S.results.length) {           // 再生成一批：先清掉上一批
        S.results = [];
        $("grid").innerHTML = "";
        $("result").hidden = true;
      }
      start();
    });
    $("cancelBtn").addEventListener("click", () => {
      S.cancelled = true;
      if (S.worker) { S.worker.terminate(); S.worker = null; S.ready = false; S.booting = false; S.engineInited = false; engine("引擎待启动", ""); }
      $("prog").hidden = true;
      endBusy();
      toast("已停止");
    });
    $("saveAll").addEventListener("click", saveAll);
    $("viewerClose").addEventListener("click", closeViewer);
    $("viewer").addEventListener("click", (e) => { if (e.target.id === "viewer") closeViewer(); });

    $("diagRetry").addEventListener("click", () => {
      if (S.worker) S.worker.terminate();
      S.worker = null;
      S.ready = false;
      S.booting = false;
      S.engineInited = false;
      S.stage = "准备";
      $("diag").hidden = true;
      engine("引擎待启动", "");
      bootEngine();
    });
    $("diagCopy").addEventListener("click", async () => {
      const text = $("diagText").textContent;
      try {
        await navigator.clipboard.writeText(text);
        toast("已复制，发给做页面的人", true);
      } catch (e) {
        const range = document.createRange();
        range.selectNodeContents($("diagText"));
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        toast("已选中，长按复制");
      }
    });

    if (location.protocol === "file:") {
      showDiag("是直接用文件方式打开的（file://）",
        "浏览器不允许本地文件加载 Python 引擎。请按「部署到免费网站.md」放到网站，或在文件夹里运行 python -m http.server 8000 后用 http://电脑IP:8000 打开。");
    }
    refreshPhotos();
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("sw.js").then(() => {
        $("verInfo").textContent = "离线可用";
      }).catch(() => { $("verInfo").textContent = ""; });
    }
  });
})();
