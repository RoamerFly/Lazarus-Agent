/* Shared About/update dialog. The startup reminder and manual check intentionally
 * use this single dialog, so update state is never shown in a surprise popup. */
(() => {
  const button = document.getElementById("aboutButton");
  const dialog = document.getElementById("aboutDialog");
  if (!button || !dialog) return;
  const api = "/api/updates";
  let latest = null;
  let polling = null;
  let jobId = null;
  dialog.innerHTML = `
    <form method="dialog" class="about-card">
      <button class="about-close" aria-label="关闭" value="close">×</button>
      <p class="eyebrow">LAZARUS AGENT</p><h2>关于与更新</h2>
      <p id="aboutVersion">正在读取版本信息…</p>
      <p class="about-copy">遗留 Android 软件复活智能体。本地优先处理 APK，修复结果仍需在目标设备上复测。</p>
      <p id="developerLine" class="about-copy"></p>
      <div class="about-actions">
        <button id="checkUpdates" type="button">检查更新</button>
        <button id="downloadUpdate" type="button" hidden>下载已验证更新</button>
        <button id="cancelUpdate" type="button" hidden>取消下载</button>
        <button id="installUpdate" type="button" hidden>安装并重启</button>
        <button id="ignoreUpdate" type="button" hidden>忽略此版本</button>
      </div>
      <p id="updateStatus" class="about-status" role="status"></p>
      <p class="about-links"><a id="projectLink" href="#">项目主页</a> <a id="issuesLink" href="#">提交问题</a></p>
    </form>`;
  const status = dialog.querySelector("#updateStatus");
  const check = dialog.querySelector("#checkUpdates");
  const download = dialog.querySelector("#downloadUpdate");
  const cancel = dialog.querySelector("#cancelUpdate");
  const install = dialog.querySelector("#installUpdate");
  const ignore = dialog.querySelector("#ignoreUpdate");
  const setStatus = (text) => { status.textContent = text; };
  const external = (url) => window.open(url, "_blank", "noopener,noreferrer");
  const request = async (path, options = {}) => {
    const response = await fetch(`${api}${path}`, { method: "POST", ...options });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "操作失败，请稍后重试。");
    return body;
  };
  const show = () => { if (!dialog.open) dialog.showModal(); };
  const clearPoll = () => { if (polling) window.clearTimeout(polling); polling = null; };

  async function loadAbout() {
    const response = await fetch(`${api}/about`);
    const info = await response.json();
    if (!response.ok) throw new Error(info.detail || "读取版本失败。");
    dialog.querySelector("#aboutVersion").textContent = `当前版本 v${info.version} · ${info.platform}`;
    dialog.querySelector("#developerLine").textContent = `开发者：${info.developer}`;
    dialog.querySelector("#projectLink").onclick = (event) => { event.preventDefault(); external(info.repository_url); };
    dialog.querySelector("#issuesLink").onclick = (event) => { event.preventDefault(); external(info.issues_url); };
  }
  function showCandidate(result) {
    latest = result;
    download.hidden = !result.available;
    ignore.hidden = !result.available;
    if (result.available) setStatus(`发现 v${result.version}。${result.notes || ""}`);
    else if (result.ignored) setStatus(`已忽略 v${result.version} 的启动提醒；可手动检查。`);
    else setStatus(`当前已是最新版本 v${result.current_version}。`);
  }
  async function checkForUpdate(startup = false) {
    check.disabled = true;
    if (!startup) setStatus("正在校验官方更新清单…");
    try {
      const result = await request(`/check?startup=${startup}`);
      if (startup && result.available) { show(); await loadAbout(); }
      showCandidate(result);
    } catch (error) {
      if (!startup) setStatus(error.message);
    } finally { check.disabled = false; }
  }
  async function pollDownload() {
    if (!jobId) return;
    try {
      const state = await fetch(`${api}/download/${encodeURIComponent(jobId)}`).then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.detail || "无法读取下载进度。");
        return body;
      });
      if (state.status === "downloading" || state.status === "queued") {
        const amount = state.total ? ` ${state.percent}%` : ` ${Math.round(state.downloaded / 1024 / 1024)} MB`;
        setStatus(`正在下载并校验更新包…${amount}`);
        polling = window.setTimeout(pollDownload, 350);
        return;
      }
      cancel.hidden = true; download.disabled = false;
      if (state.ready) {
        await request(`/download/${encodeURIComponent(jobId)}/use`);
        setStatus("更新包已通过下载后与安装前的双重校验，可以安装。");
        install.hidden = false;
      } else setStatus(state.error || "下载未完成。");
    } catch (error) { setStatus(error.message); cancel.hidden = true; download.disabled = false; }
  }
  button.addEventListener("click", async () => {
    show();
    try { await loadAbout(); setStatus("更新包会先校验 Ed25519 签名与 SHA-256，再允许安装。"); }
    catch (error) { setStatus(error.message); }
  });
  check.addEventListener("click", () => checkForUpdate(false));
  download.addEventListener("click", async () => {
    if (!latest) return;
    download.disabled = true; cancel.hidden = false; install.hidden = true; setStatus("正在准备安全下载…");
    try { const state = await request(`/download?kind=${encodeURIComponent(latest.recommended_asset || "")}`); jobId = state.id; pollDownload(); }
    catch (error) { setStatus(error.message); download.disabled = false; cancel.hidden = true; }
  });
  cancel.addEventListener("click", async () => {
    if (!jobId) return;
    cancel.disabled = true;
    try { await request(`/download/${encodeURIComponent(jobId)}/cancel`); setStatus("正在取消下载…"); }
    catch (error) { setStatus(error.message); }
  });
  ignore.addEventListener("click", async () => {
    if (!latest?.version) return;
    try { await request(`/ignore?version=${encodeURIComponent(latest.version)}`); showCandidate({ ...latest, available: false, ignored: true }); }
    catch (error) { setStatus(error.message); }
  });
  install.addEventListener("click", async () => {
    install.disabled = true;
    try { const result = await request("/install"); setStatus(result.message || "正在安装更新。"); if (result.close_app) window.setTimeout(() => window.close(), 900); }
    catch (error) { setStatus(error.message); install.disabled = false; }
  });
  dialog.addEventListener("close", clearPoll);
  // Best-effort startup notice: no network failure is surfaced until the user asks.
  window.addEventListener("DOMContentLoaded", () => checkForUpdate(true), { once: true });
})();
