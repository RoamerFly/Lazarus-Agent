/* Explicit model configuration. Keys never return to the browser once saved. */
(() => {
  const byId = (id) => document.getElementById(id);
  const dialog = byId("modelSettingsDialog");
  const open = byId("modelSettingsButton");
  if (!dialog || !open) return;
  const fields = { url: byId("modelUrl"), model: byId("modelName"), key: byId("modelKey"),
    enabled: byId("modelEnabled"), clear: byId("modelClearKey") };
  const provider=byId("modelProvider"), suggestions=byId("modelSuggestions");
  const presets=window.LazarusModelProviders;
  const status = byId("modelSettingsStatus"), keyState = byId("modelKeyState");
  let token = null, requestGeneration = 0, busy = false, effectiveSource = "none", pendingKey = "", pendingBinding = "";
  const setStatus = (text) => { status.textContent = text; };
  const controls = () => [provider, byId("modelTest"), byId("modelSave"), fields.url, fields.model, fields.key, fields.enabled, fields.clear];
  function setBusy(value) { busy = value; controls().forEach(control => { control.disabled = value; }); }
  async function request(path, method = "GET", body = null) {
    if (!token) {
      const response = await fetch("/api/config");
      const config = await response.json();
      if (!response.ok || !config.token) throw Error("无法取得本地工作台授权令牌。");
      token = config.token;
    }
    const headers = method === "POST" ? { "x-reviver-token": token, "Content-Type": "application/json" } : {};
    const response = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : null });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(result.detail || "模型配置操作失败。");
    return result;
  }
  const binding=()=>JSON.stringify([fields.url.value.trim().replace(/\/$/,""),fields.model.value.trim()]);
  function resetDraftKey(){fields.key.value="";pendingKey="";pendingBinding="";}
  function suggestModels(){
    suggestions.replaceChildren();
    for(const model of presets[provider.value]?.models||[]){const option=document.createElement("option");option.value=model;suggestions.append(option);}
  }
  function detectProvider(){
    const url=fields.url.value.trim().replace(/\/$/,"");
    provider.value=Object.keys(presets).find(name=>presets[name].url===url)||"custom";
    suggestModels();
  }
  provider.addEventListener("change",()=>{
    resetDraftKey();fields.clear.checked=false;
    const preset=presets[provider.value];
    if(preset){fields.url.value=preset.url;fields.model.value=preset.model;}
    suggestModels();keyState.textContent="请填写当前服务对应的 API Key。";
    setStatus("预设已填入，可修改地址和模型；保存后生效。");
  });
  function apply(config) {
    effectiveSource = config.source || "none";
    fields.url.value = config.url || "";
    fields.model.value = config.model || "";
    fields.key.value = "";
    fields.enabled.checked = Boolean(config.enabled);
    fields.clear.checked = false;
    detectProvider();
    if (effectiveSource === "environment") {
      keyState.textContent = "当前进程由 REVIVER_MODEL_URL 和 REVIVER_MODEL 环境变量接管。此处保存的配置会保留，但本次启动不会生效。";
    } else if (config.has_key) {
      keyState.textContent = "已保存 API Key。留空保存将继续使用它。";
    } else {
      keyState.textContent = "尚未保存 API Key。可配置本机不需要密钥的模型服务。";
    }
  }
  function body() {
    const typedKey = fields.key.value;
    return { url: fields.url.value.trim(), model: fields.model.value.trim(), key: typedKey || (pendingBinding===binding()?pendingKey:""),
      enabled: fields.enabled.checked, clear_key: fields.clear.checked };
  }
  async function load() {
    const generation = ++requestGeneration;
    setBusy(true);setStatus("正在读取本机模型配置…");
    try {
      const config = await request("/api/model/settings");
      if (generation !== requestGeneration || !dialog.open) return;
      apply(config); setStatus(config.source === "environment" ? "当前会话优先使用环境变量配置。" : "");
    } catch (error) { if (generation === requestGeneration && dialog.open) setStatus(error.message); }
    finally {if(generation===requestGeneration)setBusy(false);}
  }
  function clearSensitiveInput() { resetDraftKey(); fields.clear.checked = false; }
  function close() { requestGeneration++; setBusy(false); clearSensitiveInput(); dialog.close(); }
  open.addEventListener("click", () => { setBusy(false); if (!dialog.open) dialog.showModal(); load(); });
  byId("closeModelSettings").addEventListener("click", close);
  dialog.addEventListener("close", () => { requestGeneration++; clearSensitiveInput(); });
  byId("modelSettingsForm").addEventListener("submit", async (event) => {
    event.preventDefault(); if (busy) return;
    const generation = ++requestGeneration, payload = body();
    // The request body is already captured. Remove the typed secret from the DOM
    // before network I/O so close/reopen and later screenshots cannot expose it.
    fields.key.value = ""; setBusy(true); setStatus("正在保存到本机…");
    try {
      const config = await request("/api/model/settings", "POST", payload);
      if (generation !== requestGeneration || !dialog.open) return;
      apply(config); resetDraftKey();
      setStatus(config.source === "environment" ? "配置已保存。当前会话仍优先使用环境变量。" : (config.enabled ? "配置已保存，后续修复会调用模型。" : "配置已保存，后续修复使用本地规则。"));
    } catch (error) { if (generation === requestGeneration && dialog.open) setStatus(error.message); }
    finally { if (generation === requestGeneration) setBusy(false); }
  });
  byId("modelTest").addEventListener("click", async () => {
    if (busy) return;
    const generation = ++requestGeneration, payload = body();
    // Keep a successfully tested typed key only in this page's JS state. The
    // password field is cleared now, while a subsequent Save can still store
    // the exact tested endpoint credential without asking the user to retype.
    pendingKey = payload.key;pendingBinding=binding();
    fields.key.value = ""; setBusy(true); setStatus("正在验证服务和受限工具调用…");
    try {
      const result = await request("/api/model/test", "POST", payload);
      if (generation === requestGeneration && dialog.open) setStatus(result.message || "连接和工具调用测试完成。");
    } catch (error) { if (generation === requestGeneration && dialog.open) setStatus(error.message); }
    finally { if (generation === requestGeneration) setBusy(false); }
  });
  fields.url.addEventListener("input",()=>{resetDraftKey();detectProvider();keyState.textContent="地址已修改，请核对当前服务的密钥。";});
  fields.model.addEventListener("input",()=>{pendingKey="";pendingBinding="";});
  fields.clear.addEventListener("change", () => { if (fields.clear.checked) pendingKey = ""; });
  window.LazarusModelSettings = { refresh: load };
})();
