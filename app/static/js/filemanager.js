/* VPS 文件管理器 - 前端逻辑 */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  const state = {
    path: "/",
    entries: [],
    sortKey: "name",
    sortDir: 1, // 1 升序, -1 降序
    filter: "",
    selected: null, // 当前选中的行 path
  };

  const QUICK_PATHS = [
    "/", "/opt", "/opt/customer-develop", "/etc", "/var", "/var/log",
    "/var/www", "/home", "/root", "/tmp", "/usr", "/srv", "/data",
  ];

  // ── 图标映射 ──────────────────────────────────────────────
  function iconFor(entry) {
    if (entry.is_link) return "bi-link-45deg";
    if (entry.is_dir) return "bi-folder2-open";
    const ext = (entry.name.split(".").pop() || "").toLowerCase();
    const map = {
      txt: "bi-file-text", log: "bi-file-text", md: "bi-file-text", csv: "bi-file-text",
      py: "bi-filetype-py", js: "bi-filetype-js", ts: "bi-filetype-ts", json: "bi-filetype-json",
      html: "bi-filetype-html", htm: "bi-filetype-html", css: "bi-filetype-css",
      sh: "bi-terminal", conf: "bi-gear", cfg: "bi-gear", ini: "bi-gear", env: "bi-gear",
      zip: "bi-file-earmark-zip", tar: "bi-file-earmark-zip", gz: "bi-file-earmark-zip", "7z": "bi-file-earmark-zip",
      pdf: "bi-file-earmark-pdf", doc: "bi-file-earmark-word", docx: "bi-file-earmark-word",
      xls: "bi-file-earmark-excel", xlsx: "bi-file-earmark-excel",
      jpg: "bi-file-earmark-image", jpeg: "bi-file-earmark-image", png: "bi-file-earmark-image",
      gif: "bi-file-earmark-image", webp: "bi-file-earmark-image", svg: "bi-file-earmark-image",
      mp3: "bi-file-earmark-music", wav: "bi-file-earmark-music",
      mp4: "bi-file-earmark-play", mov: "bi-file-earmark-play", avi: "bi-file-earmark-play",
      db: "bi-database", sqlite: "bi-database", sql: "bi-database",
      pem: "bi-shield-lock", key: "bi-shield-lock", crt: "bi-shield-lock",
    };
    return map[ext] || "bi-file-earmark";
  }

  // ── 请求封装 ──────────────────────────────────────────────
  async function api(url, opts) {
    const res = await fetch(url, opts);
    if (res.status === 401) { location.href = "/login?next=/file/"; throw new Error("未登录"); }
    if (res.status === 403) { location.href = "/"; throw new Error("无权限"); }
    if (!res.ok) {
      let detail = res.statusText;
      try { const j = await res.json(); detail = j.detail || detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    return res.json();
  }

  // ── Toast ─────────────────────────────────────────────────
  function toast(msg, kind) {
    const wrap = $("#toastWrap");
    const el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 2600);
    setTimeout(() => el.remove(), 3000);
  }

  // ── 加载目录 ──────────────────────────────────────────────
  async function load(path) {
    $("#loading").classList.add("show");
    try {
      const data = await api("/file/api/list?path=" + encodeURIComponent(path));
      state.path = data.path;
      state.entries = data.entries;
      state.selected = null;
      renderBreadcrumb(data.path);
      renderTable();
    } catch (e) {
      toast(e.message, "err");
    } finally {
      $("#loading").classList.remove("show");
    }
  }

  function renderBreadcrumb(path) {
    const bar = $("#crumbs");
    bar.innerHTML = "";
    const parts = path.split("/").filter(Boolean);
    let acc = "";
    const addCrumb = (label, full) => {
      const c = document.createElement("span");
      c.className = "crumb";
      const icon = document.createElement("i");
      icon.className = "bi bi-folder2";
      const txt = document.createElement("span");
      txt.textContent = label;
      txt.title = full;
      c.appendChild(icon);
      c.appendChild(txt);
      c.addEventListener("click", () => { if (full !== state.path) load(full); });
      bar.appendChild(c);
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "/";
      bar.appendChild(sep);
    };
    addCrumb("/", "/");
    parts.forEach((p, i) => {
      acc += "/" + p;
      if (i === parts.length - 1) {
        const c = document.createElement("span");
        c.className = "crumb current";
        const txt = document.createElement("span");
        txt.textContent = p;
        txt.title = acc;
        c.appendChild(txt);
        bar.appendChild(c);
      } else {
        addCrumb(p, acc);
      }
    });
    $("#pathInput").value = path;
  }

  function filteredEntries() {
    const f = state.filter.toLowerCase();
    const list = f ? state.entries.filter((e) => e.name.toLowerCase().includes(f)) : state.entries.slice();
    const key = state.sortKey;
    const dir = state.sortDir;
    list.sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      let r = 0;
      if (key === "name") r = a.name.localeCompare(b.name, undefined, { numeric: true });
      else if (key === "size") r = (a.size || 0) - (b.size || 0);
      else if (key === "mtime") r = (a.mtime || 0) - (b.mtime || 0);
      return r * dir;
    });
    return list;
  }

  function setSort(key) {
    if (state.sortKey === key) state.sortDir *= -1;
    else { state.sortKey = key; state.sortDir = 1; }
    renderTable();
  }

  function renderTable() {
    const tbody = $("#tbody");
    tbody.innerHTML = "";
    const list = filteredEntries();
    $("#count").textContent = list.length + " 项" + (state.filter ? "（已过滤）" : "");
    if (!list.length) {
      const tr = document.createElement("tr");
      tr.className = "empty-row";
      const td = document.createElement("td");
      td.colSpan = 5;
      const icon = document.createElement("i");
      icon.className = "bi bi-inbox";
      td.appendChild(icon);
      td.appendChild(document.createTextNode(state.filter ? "没有匹配的文件" : "空目录"));
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    list.forEach((e) => tbody.appendChild(rowFor(e)));
    updateSortMarks();
  }

  function rowFor(e) {
    const tr = document.createElement("tr");
    if (state.selected === e.path) tr.classList.add("selected");

    // 名称
    const tdName = document.createElement("td");
    tdName.className = "name-cell";
    const fname = document.createElement("div");
    fname.className = "fname";
    const icon = document.createElement("i");
    icon.className = "bi " + iconFor(e);
    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = e.name;
    nm.title = e.path;
    fname.appendChild(icon);
    fname.appendChild(nm);
    if (e.is_link) {
      const badge = document.createElement("span");
      badge.className = "link-badge";
      badge.textContent = "LINK";
      fname.appendChild(badge);
    }
    tdName.appendChild(fname);
    tdName.addEventListener("click", () => {
      if (e.is_dir) load(e.path);
      else downloadFile(e.path);
    });
    tr.appendChild(tdName);

    // 大小
    const tdSize = document.createElement("td");
    tdSize.className = "size";
    tdSize.textContent = e.size_text;
    tr.appendChild(tdSize);

    // 时间
    const tdTime = document.createElement("td");
    tdTime.className = "mtime";
    tdTime.textContent = e.mtime_text;
    tr.appendChild(tdTime);

    // 权限
    const tdMode = document.createElement("td");
    tdMode.className = "mode";
    tdMode.textContent = e.mode;
    tr.appendChild(tdMode);

    // 操作
    const tdAct = document.createElement("td");
    tdAct.className = "actions";
    if (e.is_dir) {
      tdAct.appendChild(actionBtn("bi-download", "打包下载", () => downloadFile(e.path), "下载为 ZIP"));
    } else {
      tdAct.appendChild(actionBtn("bi-download", "下载", () => downloadFile(e.path)));
    }
    tdAct.appendChild(actionBtn("bi-pencil-square", "重命名", () => openRename(e)));
    tdAct.appendChild(actionBtn("bi-arrows-move", "移动", () => openMove(e)));
    tdAct.appendChild(actionBtn("bi-trash3", "删除", () => openDelete(e), "danger"));
    tr.appendChild(tdAct);

    tr.addEventListener("click", (ev) => {
      if (ev.target.closest(".actions")) return;
      state.selected = e.path;
      tr.classList.add("selected");
      document.querySelectorAll("tbody tr.selected").forEach((r) => { if (r !== tr) r.classList.remove("selected"); });
    });
    tr.addEventListener("dblclick", () => { if (e.is_dir) load(e.path); });
    return tr;
  }

  function actionBtn(icon, label, fn, kind) {
    const b = document.createElement("button");
    b.className = "btn btn-sm icon-only" + (kind === "danger" ? " danger" : "");
    b.title = label;
    const i = document.createElement("i");
    i.className = "bi " + icon;
    b.appendChild(i);
    b.addEventListener("click", (ev) => { ev.stopPropagation(); fn(); });
    return b;
  }

  function updateSortMarks() {
    ["name", "size", "mtime"].forEach((k) => {
      const th = $("#th-" + k);
      const mark = th.querySelector(".sort");
      if (state.sortKey === k) mark.textContent = state.sortDir === 1 ? "▲" : "▼";
      else mark.textContent = "";
    });
  }

  // ── 下载 ──────────────────────────────────────────────────
  function downloadFile(path) {
    const a = document.createElement("a");
    a.href = "/file/api/download?path=" + encodeURIComponent(path);
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  // ── 上传 ──────────────────────────────────────────────────
  function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    files.forEach((f) => uploadOne(f));
  }

  async function uploadOne(file) {
    const item = document.createElement("div");
    item.className = "upload-item";
    item.innerHTML =
      '<div class="u-name"><span></span><span class="u-pct">0%</span></div>' +
      '<div class="u-bar"><div class="fill" style="width:0%"></div></div>';
    item.querySelector(".u-name span").textContent = file.name;
    $("#uploadList").appendChild(item);
    const fill = item.querySelector(".fill");
    const pct = item.querySelector(".u-pct");

    try {
      const form = new FormData();
      form.append("files", file, file.name);
      const xhr = new XMLHttpRequest();
      const res = await new Promise((resolve, reject) => {
        xhr.open("POST", "/file/api/upload?path=" + encodeURIComponent(state.path));
        xhr.upload.onprogress = (ev) => {
          if (ev.lengthComputable) {
            const p = Math.round((ev.loaded / ev.total) * 100);
            fill.style.width = p + "%";
            pct.textContent = p + "%";
          }
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
          else {
            let msg = xhr.statusText;
            try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (e) { /* ignore */ }
            reject(new Error(msg));
          }
        };
        xhr.onerror = () => reject(new Error("网络错误"));
        xhr.send(form);
      });
      item.classList.add("done");
      pct.textContent = "完成";
      toast("已上传: " + res.saved.map((s) => s.name).join(", "), "ok");
      load(state.path);
    } catch (e) {
      item.classList.add("err");
      pct.textContent = "失败";
      toast("上传失败 " + file.name + ": " + e.message, "err");
    }
    setTimeout(() => item.remove(), 5000);
  }

  // ── 弹窗基础 ──────────────────────────────────────────────
  function openModal(id) { $("#" + id).classList.add("show"); }
  function closeModal(id) { $("#" + id).classList.remove("show"); }
  document.querySelectorAll(".modal-mask").forEach((m) => {
    m.addEventListener("click", (ev) => { if (ev.target === m) m.classList.remove("show"); });
  });
  document.querySelectorAll(".modal .btn-cancel").forEach((b) => {
    b.addEventListener("click", () => b.closest(".modal-mask").classList.remove("show"));
  });

  // ── 新建文件夹 ────────────────────────────────────────────
  function openMkdir() {
    $("#mkdirName").value = "";
    $("#mkdirPath").textContent = state.path;
    openModal("mkdirModal");
  }
  $("#mkdirOk").addEventListener("click", async () => {
    const name = $("#mkdirName").value.trim();
    if (!name) { toast("请输入文件夹名称", "err"); return; }
    try {
      await api("/file/api/mkdir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: state.path, name }),
      });
      toast("已创建: " + name, "ok");
      closeModal("mkdirModal");
      load(state.path);
    } catch (e) { toast(e.message, "err"); }
  });
  $("#mkdirName").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#mkdirOk").click(); });

  // ── 重命名 ────────────────────────────────────────────────
  let renameTarget = null;
  function openRename(e) {
    renameTarget = e;
    $("#renameName").value = e.name;
    openModal("renameModal");
  }
  $("#renameOk").addEventListener("click", async () => {
    if (!renameTarget) return;
    const name = $("#renameName").value.trim();
    if (!name) { toast("名称不能为空", "err"); return; }
    try {
      await api("/file/api/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: renameTarget.path, new_name: name }),
      });
      toast("已重命名", "ok");
      closeModal("renameModal");
      load(state.path);
    } catch (e) { toast(e.message, "err"); }
  });
  $("#renameName").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#renameOk").click(); });

  // ── 移动 ──────────────────────────────────────────────────
  let moveTarget = null;
  function openMove(e) {
    moveTarget = e;
    $("#moveDest").value = "";
    $("#moveSub").textContent = "移动: " + e.path;
    openModal("moveModal");
  }
  $("#moveOk").addEventListener("click", async () => {
    if (!moveTarget) return;
    const dest = $("#moveDest").value.trim();
    if (!dest) { toast("请输入目标目录", "err"); return; }
    try {
      await api("/file/api/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: moveTarget.path, dest_dir: dest }),
      });
      toast("已移动到 " + dest, "ok");
      closeModal("moveModal");
      load(state.path);
    } catch (e) { toast(e.message, "err"); }
  });
  $("#moveDest").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#moveOk").click(); });

  // ── 删除 ──────────────────────────────────────────────────
  let deleteTarget = null;
  function openDelete(e) {
    deleteTarget = e;
    $("#deleteSub").textContent = e.path;
    $("#deleteRecursive").checked = false;
    $("#deleteRecursiveWrap").classList.toggle("hidden", !e.is_dir);
    openModal("deleteModal");
  }
  $("#deleteOk").addEventListener("click", async () => {
    if (!deleteTarget) return;
    try {
      await api("/file/api/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: deleteTarget.path, recursive: $("#deleteRecursive").checked }),
      });
      toast("已删除", "ok");
      closeModal("deleteModal");
      load(state.path);
    } catch (e) { toast(e.message, "err"); }
  });

  // ── 磁盘信息 ──────────────────────────────────────────────
  async function openDisk() {
    try {
      const d = await api("/file/api/disk");
      $("#diskTotal").textContent = d.total_text;
      $("#diskUsed").textContent = d.used_text + " (" + d.percent + "%)";
      $("#diskFree").textContent = d.free_text;
      const fill = $("#diskBar");
      fill.style.width = Math.min(d.percent, 100) + "%";
      fill.className = "fill" + (d.percent > 90 ? " danger" : d.percent > 70 ? " warn" : "");
      openModal("diskModal");
    } catch (e) { toast(e.message, "err"); }
  }

  // ── 事件绑定 ──────────────────────────────────────────────
  function init() {
    // 面包屑 / 路径输入
    $("#pathInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const p = $("#pathInput").value.trim();
        if (p) load(p);
      }
    });

    // 快捷路径
    const qs = $("#quickJump");
    QUICK_PATHS.forEach((p) => {
      const o = document.createElement("option");
      o.value = p;
      o.textContent = p;
      qs.appendChild(o);
    });
    qs.addEventListener("change", () => { if (qs.value) load(qs.value); });

    // 筛选
    $("#filterInput").addEventListener("input", (e) => {
      state.filter = e.target.value.trim();
      renderTable();
    });

    // 排序
    $("#th-name").addEventListener("click", () => setSort("name"));
    $("#th-size").addEventListener("click", () => setSort("size"));
    $("#th-mtime").addEventListener("click", () => setSort("mtime"));

    // 上传按钮
    $("#uploadBtn").addEventListener("click", () => $("#fileInput").click());
    $("#fileInput").addEventListener("change", (e) => { uploadFiles(e.target.files); e.target.value = ""; });

    // 拖拽上传
    ["dragenter", "dragover"].forEach((evt) =>
      document.addEventListener(evt, (e) => { e.preventDefault(); document.body.classList.add("dragging"); })
    );
    ["dragleave", "drop"].forEach((evt) =>
      document.addEventListener(evt, (e) => { e.preventDefault(); document.body.classList.remove("dragging"); })
    );
    document.addEventListener("drop", (e) => {
      if (e.dataTransfer && e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
    });

    // 工具栏按钮
    $("#refreshBtn").addEventListener("click", () => load(state.path));
    $("#mkdirBtn").addEventListener("click", openMkdir);
    $("#diskBtn").addEventListener("click", openDisk);
    $("#upBtn").addEventListener("click", () => load(state.parent || "/"));
    $("#homeBtn").addEventListener("click", () => load("/"));

    // 初始加载
    load("/");
  }

  document.addEventListener("DOMContentLoaded", init);
})();
