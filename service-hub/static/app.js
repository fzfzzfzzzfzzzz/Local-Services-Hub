const state = {
  snapshot: null,
  ports: [],
  editing: null,
  busy: new Map(),
  actionErrors: new Map(),
  refreshing: false,
  urlAutofill: true,
  takeover: null,
  stopExternal: null,
  logService: null,
  logData: null,
  logView: "current",
  detailsService: null,
  removeService: null,
  editingGroup: null,
  groupBusy: null,
  shuttingDown: false,
  restarting: false,
  query: "",
  filter: "all",
  view: readSavedView(),
  lastFetchedAt: null,
  autoRefreshDeferred: false,
};

const statePresentation = {
  Healthy: { symbol: "●", label: "运行中", tone: "running", origin: "本管理器启动" },
  "Managed Running": { symbol: "●", label: "运行中", tone: "running", origin: "本管理器启动" },
  "External Running": { symbol: "●", label: "外部运行", tone: "external", origin: "外部接入", originTone: "external" },
  "Mixed Running": { symbol: "!", label: "混合运行", tone: "mixed", origin: "来源不一致", originTone: "mixed" },
  Starting: { symbol: "◐", label: "启动中", tone: "starting", origin: "本管理器启动" },
  Unhealthy: { symbol: "!", label: "异常", tone: "error", origin: "本管理器启动" },
  Error: { symbol: "!", label: "异常", tone: "error", origin: "本管理器启动" },
  Stopped: { symbol: "○", label: "已停止", tone: "stopped", origin: "" },
  Disabled: { symbol: "○", label: "已停止", tone: "stopped", origin: "已禁用" },
  Unknown: { symbol: "!", label: "异常", tone: "error", origin: "状态未知" },
};

const operationPresentation = {
  start: { symbol: "◐", label: "启动中", tone: "starting", origin: "本管理器启动" },
  stop: { symbol: "◐", label: "停止中", tone: "starting", origin: "本管理器启动" },
  restart: { symbol: "◐", label: "重启中", tone: "starting", origin: "本管理器启动" },
  takeover: { symbol: "◐", label: "启动中", tone: "starting", origin: "正在纳入管理" },
  stopExternal: { symbol: "◐", label: "停止中", tone: "starting", origin: "正在停止外部进程" },
  remove: { symbol: "◐", label: "停止中", tone: "starting", origin: "正在移除登记" },
};

const operationLabels = { start: "启动", stop: "停止", restart: "重启", takeover: "纳入管理", stopExternal: "停止外部进程", remove: "移除登记" };
const typeLabels = { frontend: "Frontend", backend: "Backend", fullstack: "Fullstack", worker: "Worker", plugin: "Plugin", other: "Other" };
const healthTypeLabels = { process: "进程存在", tcp: "TCP 端口", http: "HTTP 请求", multi: "多端口" };
const healthStatusLabels = { passing: "正常", checking: "检查中", failing: "失败", inactive: "未运行", unknown: "未知" };
const exitTypeLabels = { normal_stop: "正常停止", abnormal_exit: "异常退出", start_failed: "启动失败" };
const planStatusLabels = { pending: "等待中", starting: "启动中", running: "已启动", already_running: "已在运行", external_running: "外部接入", error: "失败" };
const managedStates = new Set(["Healthy", "Managed Running", "Starting", "Unhealthy"]);
const openableStates = new Set(["Healthy", "Managed Running", "External Running", "Mixed Running", "Unhealthy"]);
const faultStates = new Set(["Unhealthy", "Error", "Unknown"]);
const issueStates = new Set([...faultStates, "Mixed Running"]);
const $ = (selector) => document.querySelector(selector);

function readSavedView() {
  try { return window.localStorage.getItem("service-hub-view") === "compact" ? "compact" : "cards"; }
  catch { return "cards"; }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.detail || body.error || `请求失败（HTTP ${response.status}）`);
    error.code = body.error;
    error.status = response.status;
    error.details = body;
    throw error;
  }
  return body;
}

function toast(message, isError = false) {
  const node = element("div", `toast${isError ? " is-error" : ""}`, message);
  $("#toast-region").append(node);
  window.setTimeout(() => node.remove(), 4400);
}

function presentationFor(service) {
  const operation = state.busy.get(service.id);
  if (operation && !actionHasSettled(operation, service)) {
    return operationPresentation[operation] || statePresentation.Unknown;
  }
  return statePresentation[service.state] || statePresentation.Unknown;
}

function statusBlock(service, { compact = false } = {}) {
  const presentation = presentationFor(service);
  const line = element("div", `status-line${compact ? " is-compact" : ""}`);
  const badge = element("span", "state-badge", `${presentation.symbol} ${presentation.label}`);
  badge.dataset.tone = presentation.tone;
  line.append(badge);
  if (presentation.origin) {
    const origin = element("small", "state-origin", presentation.origin);
    if (presentation.originTone) origin.dataset.tone = presentation.originTone;
    line.append(origin);
  }
  return line;
}

function setController(controller, configuration = {}) {
  const pill = $("#controller-pill");
  pill.className = `controller-pill ${controller.online ? "is-online" : "is-offline"}`;
  pill.querySelector("span:last-child").textContent = controller.online ? "Controller Online" : "Controller Offline";
  const alert = $("#controller-alert");
  const syncPending = Boolean(configuration.sync_pending);
  alert.hidden = controller.online && !syncPending;
  alert.className = `alert ${controller.online ? "alert-warning" : "alert-danger"}`;
  $("#controller-alert-title").textContent = controller.online ? "运行配置待同步" : "Controller Offline";
  const fallback = "无法连接 Process Compose :8751。登记与编辑仍然可用。";
  const pending = syncPending ? `配置文件已安全保存，等待控制器加载。${configuration.sync_error || ""}` : "";
  $("#controller-alert-message").textContent = [controller.error || (!controller.online ? fallback : ""), pending].filter(Boolean).join(" ");
}

function setStoreStatus(store) {
  const alert = $("#store-alert");
  alert.hidden = !store?.error;
  $("#store-alert-message").textContent = store?.error || "";
  $("#restore-backup").hidden = !store?.using_backup;
}

function createAction(label, style, action, service, disabled = false) {
  const button = element("button", `button ${style}`, label);
  button.type = "button";
  button.dataset.action = action;
  button.dataset.service = service.id;
  button.disabled = disabled || state.busy.has(service.id);
  return button;
}

function createMenuAction(label, action, service, { danger = false, disabled = false } = {}) {
  const button = element("button", `menu-item${danger ? " is-danger" : ""}`, label);
  button.type = "button";
  button.dataset.action = action;
  button.dataset.service = service.id;
  button.disabled = disabled || state.busy.has(service.id);
  return button;
}

function actionMenu(service, controllerOnline) {
  const menu = element("details", "action-menu");
  const summary = element("summary", "menu-trigger", "···");
  summary.setAttribute("aria-label", `${service.name} 更多操作`);
  const panel = element("div", "menu-panel");
  const canReadLogs = service.enabled;
  if (canReadLogs) panel.append(createMenuAction("查看日志", "logs", service, { disabled: !controllerOnline }));
  panel.append(
    createMenuAction("打开项目目录", "open-directory", service),
    createMenuAction("复制 URL", "copy-url", service, { disabled: !service.effective_url && !service.url }),
    createMenuAction("编辑配置", "edit", service),
    createMenuAction("查看详情", "details", service),
  );
  panel.append(
    element("div", "menu-separator"),
    createMenuAction("移除登记", "remove", service, {
      danger: true,
      disabled: service.state === "Mixed Running",
    }),
  );
  menu.append(summary, panel);
  return menu;
}

function configPendingSummary(service) {
  const block = element("div", "pending-config-summary");
  const activePorts = runtimeItemsFor(service.active_config).map((item) => `:${item.port}`).join(" · ");
  const desiredPorts = runtimeItemsFor(service).map((item) => `:${item.port}`).join(" · ");
  block.append(
    element("span", "pending-label", "待重启生效"),
    element("code", "", `当前 ${activePorts} → 下次 ${desiredPorts}`),
  );
  return block;
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total}秒`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}分`;
  const hours = Math.floor(minutes / 60);
  const remaining = minutes % 60;
  return remaining ? `${hours}小时 ${remaining}分` : `${hours}小时`;
}

function formatShortTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function dependencySummary(service) {
  if (!service.dependency_services?.length) return null;
  const block = element("div", "dependency-summary");
  block.append(
    element("strong", "", "依赖"),
    element("span", "", service.dependency_services.map((item) => item.name).join(" → ")),
  );
  return block;
}

function lastRunSummary(service) {
  const run = service.last_run;
  if (!run || !["Stopped", "Disabled", "Error"].includes(service.state)) return null;
  const block = element("div", "last-run-summary");
  block.dataset.exit = run.exit_type || "";
  const info = element("span", "last-run-info");
  info.append(
    element("strong", "", exitTypeLabels[run.exit_type] || "上次运行"),
    element("span", "", `${formatDuration(run.duration_seconds)} · ${formatShortTime(run.stopped_at)}`),
  );
  block.append(info);
  if (["abnormal_exit", "start_failed"].includes(run.exit_type)) {
    const dismiss = element("button", "last-run-dismiss", "清除");
    dismiss.type = "button";
    dismiss.title = "清除这条异常退出提示";
    dismiss.dataset.action = "dismiss-last-run";
    dismiss.dataset.service = service.id;
    block.append(dismiss);
  }
  return block;
}

function groupCard(group) {
  const card = element("article", "group-card");
  card.dataset.groupCard = group.id;
  const heading = element("div", "");
  heading.append(element("h3", "", group.name), element("p", "", group.description || "一键按依赖顺序启动所选服务"));
  const services = element("div", "group-services");
  (group.services || []).forEach((serviceId) => {
    const service = state.snapshot?.services.find((item) => item.id === serviceId);
    services.append(element("span", "group-service-chip", service?.name || serviceId));
  });
  const actions = element("div", "group-actions");
  const start = element("button", "button button-primary", state.groupBusy === group.id ? "启动中…" : "启动场景");
  start.type = "button"; start.dataset.groupAction = "start"; start.dataset.group = group.id; start.disabled = Boolean(state.groupBusy);
  const edit = element("button", "button button-secondary", "编辑");
  edit.type = "button"; edit.dataset.groupAction = "edit"; edit.dataset.group = group.id; edit.disabled = Boolean(state.groupBusy);
  const remove = element("button", "button button-danger-quiet", "移除");
  remove.type = "button"; remove.dataset.groupAction = "remove"; remove.dataset.group = group.id; remove.disabled = Boolean(state.groupBusy);
  actions.append(start, edit, remove);
  card.append(heading, services, actions);
  return card;
}

function renderGroups() {
  const grid = $("#groups-grid");
  grid.replaceChildren();
  const groups = state.snapshot?.groups || [];
  if (!groups.length) {
    const empty = element("div", "group-empty");
    empty.append(element("span", "", "把经常一起使用的服务保存成一个启动场景。"));
    const button = element("button", "button button-secondary", "新建第一个服务组");
    button.type = "button"; button.dataset.emptyGroup = "true";
    empty.append(button); grid.append(empty); return;
  }
  groups.forEach((group) => grid.append(groupCard(group)));
}

function operationError(service) {
  const localError = state.actionErrors.get(service.id);
  const currentFault = faultStates.has(service.state);
  const message = localError?.message || (currentFault ? service.last_error || service.error : null);
  if (!message) return null;
  const operation = localError?.operation;
  const box = element("div", localError ? "action-feedback" : "operation-error");
  const heading = element("div", "feedback-heading");
  heading.append(element("strong", "", `⚠ ${operation ? `${operationLabels[operation] || "操作"}失败` : "当前故障"}`));
  if (localError) {
    const dismiss = element("button", "feedback-dismiss", "关闭");
    dismiss.type = "button";
    dismiss.dataset.action = "dismiss-action-error";
    dismiss.dataset.service = service.id;
    heading.append(dismiss);
  }
  box.append(heading, element("p", "", message));
  const conflict = localError?.details?.port_conflict;
  if (conflict) {
    const process = conflict.process_name || "未知进程";
    const pid = conflict.pid ? `PID ${conflict.pid}` : "PID 不可用";
    box.append(element("p", "conflict-summary", `端口 ${conflict.port} · ${process} · ${pid}`));
    if (conflict.registered_service) {
      const relationship = conflict.registered_service.relationship === "other_registered_service" ? "占用服务" : "对应登记";
      box.append(element("p", "conflict-owner", `${relationship}：${conflict.registered_service.name}`));
    }
  }
  const links = element("div", "error-actions");
  links.append(createAction("查看详情", "button-quiet", "details", service));
  if (service.enabled) links.append(createAction("查看日志", "button-quiet", "logs", service));
  box.append(links);
  return box;
}

function serviceCard(service, controllerOnline) {
  const card = element("article", `service-card${state.view === "compact" ? " is-compact" : ""}`);
  card.dataset.state = service.state;
  card.dataset.serviceCard = service.id;

  const top = element("div", "card-top");
  const title = element("div", "card-identity");
  const nameRow = element("div", "name-row");
  nameRow.append(element("h3", "", service.name), element("span", "type-chip", typeLabels[service.type] || service.type));
  title.append(nameRow);
  if (service.note) title.append(element("p", "service-note", service.note));
  const badges = element("div", "badge-stack");
  badges.append(statusBlock(service));
  if (service.health_check && !["unknown", "inactive"].includes(service.health_check.status)) {
    const healthChip = element(
      "span",
      "health-chip",
      `${healthTypeLabels[service.health_check.type] || "健康检查"} · ${healthStatusLabels[service.health_check.status] || "未知"}`,
    );
    healthChip.dataset.health = service.health_check.status;
    healthChip.title = service.health_check.detail || "";
    badges.append(healthChip);
  }
  if (service.pending_restart) badges.append(element("span", "pending-badge", "待重启生效"));
  top.append(title, badges);

  const facts = element("div", "compact-facts");
  facts.append(element("strong", "mono runtime-ports", runtimeItemsFor(service).map((item) => `:${item.port}`).join(" · ")));
  const path = element("span", "truncate", service.working_dir);
  path.title = service.working_dir;
  facts.append(path);

  const actions = element("div", "card-actions");
  const operation = state.busy.get(service.id);
  const active = managedStates.has(service.state);
  if (service.effective_url && openableStates.has(service.state)) actions.append(createAction("打开网页", "button-primary", "open", service));
  if (["Stopped", "Error"].includes(service.state)) {
    actions.append(createAction(operation === "start" ? "启动中…" : "启动服务", "button-primary", "start", service, !controllerOnline));
  }
  if (active) {
    actions.append(createAction(operation === "restart" ? "重启中…" : service.pending_restart ? "应用配置并重启" : "重启", "button-secondary", "restart", service, !controllerOnline));
    actions.append(createAction(operation === "stop" ? "停止中…" : "停止", "button-quiet", "stop", service, !controllerOnline));
  }
  if (service.state === "External Running") {
    actions.append(createAction("停止外部进程", "button-quiet", "stop-external", service));
    actions.append(createAction(
      operation === "takeover" ? "正在重新纳入…" : "重新纳入管理",
      "button-secondary",
      "takeover",
      service,
      !controllerOnline,
    ));
  }
  if (service.state === "Mixed Running") {
    actions.append(createAction(
      operation === "takeover" ? "正在统一纳管…" : "统一纳入管理",
      "button-secondary",
      "takeover",
      service,
      !controllerOnline,
    ));
  }
  actions.append(actionMenu(service, controllerOnline));

  card.append(top, facts);
  if (service.pending_restart && service.active_config) card.append(configPendingSummary(service));
  const dependencies = dependencySummary(service);
  if (dependencies) card.append(dependencies);
  const previousRun = lastRunSummary(service);
  if (previousRun) card.append(previousRun);
  const error = operationError(service);
  if (error) card.append(error);
  card.append(actions);
  return card;
}

function matchesFilter(service) {
  if (state.filter === "running") return ["Healthy", "Managed Running", "Starting", "External Running", "Mixed Running"].includes(service.state);
  if (state.filter === "stopped") return ["Stopped", "Disabled"].includes(service.state);
  if (state.filter === "external") return ["External Running", "Mixed Running"].includes(service.state);
  if (state.filter === "error") return issueStates.has(service.state);
  return true;
}

function filteredServices() {
  if (!state.snapshot) return [];
  const query = state.query.trim().toLocaleLowerCase("zh-CN");
  return state.snapshot.services.filter((service) => {
    if (!matchesFilter(service)) return false;
    if (!query) return true;
    const runtimeSearch = runtimeItemsFor(service).flatMap((item) => [String(item.port), item.command]);
    return [service.name, service.working_dir, service.note, service.url, ...runtimeSearch]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase("zh-CN").includes(query));
  });
}

function renderHeroSummary(services) {
  const running = services.filter((item) => ["Healthy", "Managed Running"].includes(item.state)).length;
  const starting = services.filter((item) => item.state === "Starting").length;
  const external = services.filter((item) => item.state === "External Running").length;
  const mixed = services.filter((item) => item.state === "Mixed Running").length;
  const stopped = services.filter((item) => ["Stopped", "Disabled"].includes(item.state)).length;
  const issues = services.filter((item) => issueStates.has(item.state)).length;
  const parts = [`${services.length} 个服务`, `${running} 运行中`];
  if (starting) parts.push(`${starting} 启动中`);
  parts.push(`${external} 外部接入`);
  if (mixed) parts.push(`${mixed} 混合运行`);
  parts.push(`${stopped} 已停止`);
  if (issues) parts.push(`${issues} 异常`);
  $("#hero-summary").textContent = parts.join(" · ");
}

function renderServices(snapshot = null) {
  if (snapshot) {
    state.snapshot = snapshot;
    state.lastFetchedAt = new Date();
  }
  if (!state.snapshot) return;
  pruneActionErrors();
  setController(state.snapshot.controller, state.snapshot.configuration);
  setStoreStatus(state.snapshot.store);
  renderHeroSummary(state.snapshot.services);
  renderGroups();
  const services = filteredServices();
  $("#service-total").textContent = services.length === state.snapshot.services.length ? `${services.length} 个已登记服务` : `${services.length} / ${state.snapshot.services.length} 个服务`;
  const grid = $("#services-grid");
  grid.className = `services-grid${state.view === "compact" ? " is-compact" : ""}`;
  grid.replaceChildren();
  if (!state.snapshot.services.length) {
    const empty = element("div", "empty-state");
    empty.append(element("h3", "", "还没有登记服务"), element("p", "", "从推荐端口选择一个，或点击“登记新服务”。"));
    const button = element("button", "button button-primary", "＋ 登记新服务");
    button.type = "button"; button.dataset.emptyCreate = "true";
    empty.append(button); grid.append(empty);
  } else if (!services.length) {
    const empty = element("div", "empty-state compact-empty");
    empty.append(element("h3", "", "没有匹配的服务"), element("p", "", "可以清除搜索词或切换状态筛选。"));
    grid.append(empty);
  } else {
    services.forEach((service) => grid.append(serviceCard(service, state.snapshot.controller.online)));
  }
  $("#view-switch").querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("is-active", button.dataset.view === state.view));
  if (state.lastFetchedAt) {
    $("#last-updated").textContent = `更新于 ${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(state.lastFetchedAt)}`;
  }
}

function renderPorts(ports) {
  const grid = $("#ports-grid");
  grid.replaceChildren();
  if (!ports.length) {
    grid.append(element("div", "ports-error", "暂时没有找到可用端口，请稍后重新扫描。"));
    return;
  }
  ports.forEach((port) => {
    const button = element("button", "port-card");
    button.type = "button"; button.dataset.port = String(port);
    button.append(element("span", "availability", "● 可用"), element("strong", "", String(port)), element("small", "", "点击登记"));
    grid.append(button);
  });
}

async function refreshPorts({ announceError = false } = {}) {
  const button = $("#refresh-ports"); button.disabled = true;
  try {
    const data = await fetchJSON("/api/ports/recommended");
    state.ports = data.ports; renderPorts(data.ports);
  } catch (error) {
    $("#ports-grid").replaceChildren(element("div", "ports-error", `扫描失败：${error.message}`));
    if (announceError) toast(error.message, true);
  } finally { button.disabled = false; }
}

function interactionBlocksAutoRefresh() {
  return Boolean(
    document.querySelector("dialog[open]")
    || document.querySelector(".action-menu[open]")
    || state.busy.size
    || state.groupBusy,
  );
}

async function refreshServices({ announceError = false, automatic = false } = {}) {
  if (automatic && interactionBlocksAutoRefresh()) {
    state.autoRefreshDeferred = true;
    return;
  }
  if (state.refreshing || state.shuttingDown || state.restarting) return;
  state.refreshing = true; $("#refresh-services").disabled = true;
  try {
    const snapshot = await fetchJSON("/api/services");
    if (automatic && interactionBlocksAutoRefresh()) {
      state.autoRefreshDeferred = true;
      return;
    }
    state.autoRefreshDeferred = false;
    renderServices(snapshot);
  }
  catch (error) {
    if (announceError || !state.snapshot) toast(error.message, true);
    if (!state.snapshot) $("#services-grid").replaceChildren(element("div", "empty-state", "Service Hub API 暂时不可用。"));
  } finally { state.refreshing = false; $("#refresh-services").disabled = false; }
}

function updateServiceSnapshot(service) {
  if (!state.snapshot) return;
  const index = state.snapshot.services.findIndex((item) => item.id === service.id);
  if (index < 0) return;
  const services = [...state.snapshot.services];
  services[index] = service;
  state.snapshot = { ...state.snapshot, services };
  state.lastFetchedAt = new Date();
  renderServices();
}

function setActionError(serviceId, operation, error) {
  const snapshotState = state.snapshot?.services.find((item) => item.id === serviceId)?.state;
  state.actionErrors.set(serviceId, {
    operation,
    message: error.message,
    code: error.code,
    details: error.details,
    stateAtError: snapshotState || "Unknown",
  });
}

function pruneActionErrors() {
  for (const [id, entry] of state.actionErrors) {
    if (state.busy.has(id)) continue;
    const service = state.snapshot?.services.find((item) => item.id === id);
    if (entry.code === "port_conflict") {
      const conflict = entry.details?.port_conflict;
      const occupant = service?.port_occupant;
      const stillSameOccupant = Boolean(
        conflict
        && occupant
        && Number(conflict.port) === Number(occupant.port)
        && Number(conflict.pid || 0) === Number(occupant.pid || 0),
      );
      if (!stillSameOccupant) state.actionErrors.delete(id);
      continue;
    }
    if (!service || service.state !== entry.stateAtError) state.actionErrors.delete(id);
  }
}

function actionHasSettled(operation, service) {
  if (operation === "stop" || operation === "stopExternal") {
    // A stop still settles when the freed port is immediately re-occupied by
    // an external process: the managed stop itself has completed.
    return ["Stopped", "Disabled", "Error", "Unknown", "External Running"].includes(service.state);
  }
  if (operation === "takeover") {
    return ["Healthy", "Managed Running", "Unhealthy", "Error", "Unknown"].includes(service.state);
  }
  return ["Healthy", "Managed Running", "Unhealthy", "Error", "Unknown", "External Running"].includes(service.state);
}

function operationNeedsStartupConfirmation(operation) {
  return ["start", "restart", "takeover"].includes(operation);
}

async function refreshServiceAfterAction(service, operation) {
  const shouldConfirmStartup = operationNeedsStartupConfirmation(operation);
  const deadline = Date.now() + (shouldConfirmStartup ? 60000 : 3000);
  let latest = null;

  do {
    await new Promise((resolve) => window.setTimeout(resolve, shouldConfirmStartup ? 700 : 300));
    latest = await fetchJSON(`/api/services/${encodeURIComponent(service.id)}`);
    updateServiceSnapshot(latest);
    if (actionHasSettled(operation, latest)) return { settled: true, service: latest };
  } while (Date.now() < deadline);

  return { settled: false, service: latest };
}

function runtimeItemsFor(service) {
  if (service?.runtime_items?.length) return service.runtime_items;
  if (service) return [{ id: "main", port: service.port, command: service.command }];
  return [{ id: "main", port: "", command: "" }];
}

function newRuntimeId() {
  return `item_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

function updateRuntimeItemRows() {
  const rows = [...$("#runtime-items").querySelectorAll("[data-runtime-item]")];
  rows.forEach((row, index) => {
    row.querySelector("[data-runtime-title]").textContent = index === 0 ? "主运行项" : `运行项 ${index + 1}`;
    row.querySelector("[data-remove-runtime]").hidden = rows.length === 1;
  });
}

function autoResizeRuntimeCommand(textarea) {
  if (!textarea) return;
  textarea.style.height = "auto";
  const styles = window.getComputedStyle(textarea);
  const minHeight = Number.parseFloat(styles.minHeight) || 41;
  const maxHeight = Number.parseFloat(styles.maxHeight) || 180;
  const contentHeight = Math.max(textarea.scrollHeight, minHeight);
  textarea.style.height = `${Math.min(contentHeight, maxHeight)}px`;
  textarea.style.overflowY = contentHeight > maxHeight ? "auto" : "hidden";
}

function resizeRuntimeCommands(container = document) {
  container.querySelectorAll('textarea[name="runtime_command"]').forEach(autoResizeRuntimeCommand);
}

function appendRuntimeItem(item = {}) {
  const container = $("#runtime-items");
  if (container.children.length >= 12) { toast("每个项目最多支持 12 个运行项", true); return; }
  const row = element("div", "runtime-item");
  row.dataset.runtimeItem = "true";
  row.dataset.runtimeId = item.id || newRuntimeId();
  const heading = element("div", "runtime-item-heading");
  heading.append(element("strong", "", "运行项"));
  heading.firstChild.dataset.runtimeTitle = "true";
  const remove = element("button", "button button-danger-quiet runtime-remove", "移除");
  remove.type = "button"; remove.dataset.removeRuntime = "true";
  heading.append(remove);
  const fields = element("div", "runtime-item-fields");
  const portField = element("label", "field runtime-port-field");
  portField.append(element("span", "", "端口 *"));
  const portInput = element("input");
  portInput.name = "runtime_port"; portInput.type = "number"; portInput.min = "1"; portInput.max = "65535"; portInput.required = true;
  portInput.placeholder = "8790"; portInput.value = item.port ?? "";
  portField.append(portInput);
  const commandField = element("label", "field runtime-command-field");
  commandField.append(element("span", "", "启动命令 *"));
  const commandInput = element("textarea");
  commandInput.name = "runtime_command"; commandInput.required = true; commandInput.rows = 1;
  commandInput.placeholder = "npm run dev -- --port 8790"; commandInput.value = item.command ?? "";
  commandField.append(commandInput);
  fields.append(portField, commandField); row.append(heading, fields); container.append(row);
  updateRuntimeItemRows();
  autoResizeRuntimeCommand(commandInput);
}

function renderRuntimeItems(items) {
  $("#runtime-items").replaceChildren();
  (items?.length ? items : runtimeItemsFor(null)).forEach((item) => appendRuntimeItem(item));
}

function runtimeItemsFromForm(form) {
  return [...form.querySelectorAll("[data-runtime-item]")].map((row, index) => ({
    id: index === 0 ? "main" : row.dataset.runtimeId,
    port: Number(row.querySelector('[name="runtime_port"]').value),
    command: row.querySelector('[name="runtime_command"]').value.trim(),
  }));
}

function servicePayload(form) {
  const data = new FormData(form);
  const healthCheckType = String(data.get("health_check_type") || "tcp");
  const runtimeItems = runtimeItemsFromForm(form);
  const payload = {
    name: String(data.get("name") || "").trim(),
    port: runtimeItems[0].port,
    working_dir: String(data.get("working_dir") || "").trim(),
    command: runtimeItems[0].command,
    url: String(data.get("url") || "").trim() || null,
    type: String(data.get("type") || "other"),
    note: String(data.get("note") || "").trim(),
    health_url: healthCheckType === "http" ? String(data.get("health_url") || "").trim() || null : null,
    health_check_type: healthCheckType,
    dependencies: [...form.querySelectorAll('input[name="dependencies"]:checked')].map((input) => input.value),
    runtime_items: runtimeItems,
    enabled: form.elements.namedItem("enabled").checked,
  };
  if (healthCheckType === "http") {
    payload.health_expected_status = Number(data.get("health_expected_status") || 200);
  }
  return payload;
}

function syncHealthCheckFields(form) {
  const mode = form.elements.namedItem("health_check_type").value;
  const isHttp = mode === "http";
  form.querySelectorAll("[data-health-http]").forEach((field) => {
    field.hidden = !isHttp;
    field.setAttribute("aria-hidden", String(!isHttp));
  });
  const healthUrl = form.elements.namedItem("health_url");
  const expectedStatus = form.elements.namedItem("health_expected_status");
  healthUrl.required = isHttp;
  healthUrl.disabled = !isHttp;
  expectedStatus.required = isHttp;
  expectedStatus.disabled = !isHttp;
}

function renderServiceChecklist(container, { selected = [], exclude = null, name = "dependencies" } = {}) {
  container.replaceChildren();
  const services = (state.snapshot?.services || []).filter((service) => service.id !== exclude);
  if (!services.length) {
    container.append(element("div", "checklist-empty", "暂无其他可选服务"));
    return;
  }
  services.forEach((service) => {
    const label = element("label", "service-check-option");
    const input = element("input");
    input.type = "checkbox"; input.name = name; input.value = service.id; input.checked = selected.includes(service.id);
    const ports = runtimeItemsFor(service).map((item) => `:${item.port}`).join(" · ");
    label.append(input, element("span", "", `${service.name} · ${ports}`));
    container.append(label);
  });
}

function openServiceForm(service = null, port = null) {
  state.editing = service;
  state.urlAutofill = !service;
  const form = $("#service-form"); form.reset();
  $("#form-error").hidden = true;
  $("#service-dialog-title").textContent = service ? "编辑已登记服务" : "登记新服务";
  $("#save-service").textContent = service ? "保存修改" : "保存登记";
  if (service) {
    ["name", "working_dir", "url", "type", "note", "health_url", "health_check_type", "health_expected_status"].forEach((key) => { form.elements.namedItem(key).value = service[key] ?? ""; });
    form.elements.namedItem("enabled").checked = service.enabled;
    renderRuntimeItems(runtimeItemsFor(service));
  } else {
    renderRuntimeItems([{ id: "main", port: port || "", command: "" }]);
  }
  if (!service && port) {
    form.elements.namedItem("url").value = `http://127.0.0.1:${port}`;
  }
  if (!service) {
    form.elements.namedItem("health_check_type").value = "tcp";
    form.elements.namedItem("health_expected_status").value = "200";
  }
  renderServiceChecklist($("#service-dependencies"), {
    selected: service?.dependencies || [],
    exclude: service?.id || null,
  });
  syncHealthCheckFields(form);
  $("#service-dialog").showModal();
  resizeRuntimeCommands(form);
  form.elements.namedItem("name").focus();
}

function chooseRestart() {
  return new Promise((resolve) => {
    const dialog = $("#restart-choice-dialog");
    const onClick = (event) => {
      const button = event.target.closest("[data-choice]");
      if (!button) return;
      dialog.removeEventListener("click", onClick);
      dialog.removeEventListener("close", onClose);
      dialog.close();
      resolve(button.dataset.choice);
    };
    // Esc and any other close path count as "cancel"; the listeners must be
    // removed either way or a later submit would run twice.
    const onClose = () => {
      dialog.removeEventListener("click", onClick);
      resolve("cancel");
    };
    dialog.addEventListener("click", onClick);
    dialog.addEventListener("close", onClose);
    dialog.showModal();
  });
}

async function saveService(event) {
  event.preventDefault();
  const form = event.currentTarget; const payload = servicePayload(form);
  let restartChoice = null;
  if (state.editing && managedStates.has(state.editing.state)) {
    if (state.editing.enabled && !payload.enabled) {
      if (!window.confirm(`“${state.editing.name}”正在运行。禁用前必须先确认进程退出并释放端口，是否继续？`)) return;
      restartChoice = "stop";
    }
    const previousItems = runtimeItemsFor(state.editing).map(({ port, command }) => ({ port: Number(port), command }));
    const nextItems = payload.runtime_items.map(({ port, command }) => ({ port: Number(port), command }));
    const critical = payload.working_dir !== state.editing.working_dir || JSON.stringify(previousItems) !== JSON.stringify(nextItems);
    if (critical && restartChoice !== "stop") {
      restartChoice = await chooseRestart();
      if (restartChoice === "cancel") return;
    }
  }
  const save = $("#save-service"); const errorNode = $("#form-error");
  save.disabled = true; save.textContent = "保存中…"; errorNode.hidden = true;
  try {
    let result;
    if (state.editing) {
      const query = restartChoice ? `?restart=${["restart", "stop"].includes(restartChoice)}` : "";
      result = await fetchJSON(`/api/services/${encodeURIComponent(state.editing.id)}${query}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      result = await fetchJSON("/api/services", { method: "POST", body: JSON.stringify(payload) });
    }
    $("#service-dialog").close();
    toast(result.disabled_after_stop ? "服务已确认停止并禁用" : result.restart_deferred ? "配置已保存，将在下次重启时生效" : "服务登记已保存");
    if (result.config_sync_warning) toast(`配置已落盘；控制器将在恢复后加载：${result.config_sync_warning}`, true);
    await Promise.all([refreshServices({ announceError: true }), refreshPorts()]);
  } catch (error) { errorNode.textContent = error.message; errorNode.hidden = false; }
  finally { save.disabled = false; save.textContent = state.editing ? "保存修改" : "保存登记"; }
}

function openGroupForm(group = null) {
  state.editingGroup = group;
  const form = $("#group-form"); form.reset();
  $("#group-form-error").hidden = true;
  $("#group-dialog-title").textContent = group ? "编辑服务组" : "新建服务组";
  $("#save-group").textContent = group ? "保存修改" : "保存服务组";
  if (group) {
    form.elements.namedItem("name").value = group.name;
    form.elements.namedItem("description").value = group.description || "";
  }
  renderServiceChecklist($("#group-services"), {
    selected: group?.services || [],
    name: "group_services",
  });
  $("#group-dialog").showModal();
  form.elements.namedItem("name").focus();
}

async function saveGroup(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    name: form.elements.namedItem("name").value.trim(),
    description: form.elements.namedItem("description").value.trim(),
    services: [...form.querySelectorAll('input[name="group_services"]:checked')].map((input) => input.value),
  };
  const errorNode = $("#group-form-error");
  const button = $("#save-group");
  errorNode.hidden = true; button.disabled = true; button.textContent = "保存中…";
  try {
    const url = state.editingGroup ? `/api/groups/${encodeURIComponent(state.editingGroup.id)}` : "/api/groups";
    await fetchJSON(url, { method: state.editingGroup ? "PUT" : "POST", body: JSON.stringify(payload) });
    $("#group-dialog").close();
    toast(state.editingGroup ? "服务组已更新" : "服务组已创建");
    await refreshServices({ announceError: true });
  } catch (error) {
    errorNode.textContent = error.message; errorNode.hidden = false;
  } finally {
    button.disabled = false; button.textContent = state.editingGroup ? "保存修改" : "保存服务组";
  }
}

function groupPlanServices(group) {
  const byId = new Map((state.snapshot?.services || []).map((service) => [service.id, service]));
  const result = [];
  const visited = new Set();
  const visit = (serviceId) => {
    if (visited.has(serviceId)) return;
    const service = byId.get(serviceId);
    if (!service) return;
    (service.dependencies || []).forEach(visit);
    visited.add(serviceId); result.push(service);
  };
  (group.services || []).forEach(visit);
  return result;
}

function renderGroupProgress(services, resultPlan = null, failedServiceId = null) {
  const results = new Map((resultPlan || []).map((item) => [item.id, item.status]));
  const list = $("#group-progress-list"); list.replaceChildren();
  services.forEach((planned) => {
    const current = state.snapshot?.services.find((item) => item.id === planned.id) || planned;
    let status = results.get(planned.id);
    if (!status) {
      if (failedServiceId === planned.id) status = "error";
      else if (["Healthy", "Managed Running", "External Running"].includes(current.state)) status = "running";
      else if (current.state === "Starting") status = "starting";
      else status = "pending";
    }
    const row = element("div", "progress-row"); row.dataset.state = status;
    row.append(element("strong", "", planned.name), element("span", "progress-state", planStatusLabels[status] || status));
    list.append(row);
  });
}

async function startGroup(group) {
  const services = groupPlanServices(group);
  state.groupBusy = group.id; renderGroups();
  $("#group-progress-title").textContent = `启动 ${group.name}`;
  $("#group-progress-message").textContent = `正在按依赖顺序处理 0 / ${services.length}`;
  renderGroupProgress(services);
  $("#group-progress-dialog").showModal();
  let done = false; let response = null; let requestError = null;
  const request = fetchJSON(`/api/groups/${encodeURIComponent(group.id)}/start`, { method: "POST" })
    .then((value) => { response = value; })
    .catch((error) => { requestError = error; })
    .finally(() => { done = true; });
  try {
    while (!done) {
      await new Promise((resolve) => window.setTimeout(resolve, 700));
      if (!done) {
        await refreshServices();
        renderGroupProgress(services);
        const ready = services.filter((item) => {
          const current = state.snapshot?.services.find((service) => service.id === item.id);
          return ["Healthy", "Managed Running", "External Running"].includes(current?.state);
        }).length;
        $("#group-progress-message").textContent = `正在按依赖顺序处理 ${ready} / ${services.length}`;
      }
    }
    await request;
    if (requestError) throw requestError;
    await refreshServices({ announceError: true });
    renderGroupProgress(services, response?.plan || []);
    $("#group-progress-message").textContent = `${group.name} 已完成：${response?.plan?.length || 0} 个服务已处理`;
    toast(`${group.name}：场景启动完成`);
  } catch (error) {
    await refreshServices();
    renderGroupProgress(services, response?.plan || [], error.details?.failed_service_id);
    $("#group-progress-message").textContent = `启动失败：${error.message}`;
    toast(`${group.name}：${error.message}`, true);
  } finally {
    state.groupBusy = null; renderGroups();
  }
}

async function removeGroup(group) {
  if (!window.confirm(`确认移除服务组“${group.name}”吗？服务登记和运行状态不会受到影响。`)) return;
  try {
    await fetchJSON(`/api/groups/${encodeURIComponent(group.id)}`, { method: "DELETE" });
    toast("服务组已移除"); await refreshServices({ announceError: true });
  } catch (error) { toast(error.message, true); }
}

function toastActionOutcome(service, operation, finalState, planLength) {
  if (planLength > 1) {
    toast(`${service.name}：已按依赖顺序处理 ${planLength} 个服务`);
    return;
  }
  const actionName = operationLabels[operation] || "操作";
  if (operation === "stop" || operation === "stopExternal") {
    if (finalState === "External Running") toast(`${service.name}：已停止；端口随即被新的外部进程占用`);
    else toast(`${service.name}：${actionName}已完成`);
    return;
  }
  if (["Healthy", "Managed Running"].includes(finalState)) {
    toast(`${service.name}：${actionName}完成，服务运行正常`);
    return;
  }
  if (["Unhealthy", "Error"].includes(finalState)) {
    toast(`${service.name}：${actionName}已完成，但健康检查未通过`, true);
    return;
  }
  toast(`${service.name}：${actionName}已提交，当前状态：${statePresentation[finalState]?.label || finalState}`);
}

async function runAction(service, operation) {
  state.busy.set(service.id, operation);
  state.actionErrors.delete(service.id);
  renderServices();
  let submitted = false;
  try {
    const actionResult = await fetchJSON(`/api/services/${encodeURIComponent(service.id)}/${operation}`, { method: "POST" });
    submitted = true;
    const result = await refreshServiceAfterAction(service, operation);
    if (!result.settled) toast(`${service.name}：操作已提交，仍在确认状态，页面会自动更新`);
    else toastActionOutcome(service, operation, result.service?.state, actionResult.plan?.length || 0);
  } catch (error) {
    if (submitted) {
      toast(`${service.name}：操作已提交，但自动刷新失败：${error.message}`, true);
    } else if (operation === "restart" && ["external_running", "not_managed"].includes(error.code)) {
      try {
        const current = await fetchJSON(`/api/services/${encodeURIComponent(service.id)}`);
        updateServiceSnapshot(current);
        if (["External Running", "Mixed Running"].includes(current.state)) {
          toast(`${service.name} 包含外部运行项，请确认是否统一纳入管理`);
          await previewTakeover(current, { title: "确认统一纳入管理" });
        } else {
          setActionError(service.id, operation, error);
          toast(`${service.name}：${error.message}`, true);
        }
      } catch (refreshError) {
        setActionError(service.id, operation, error);
        toast(`${service.name}：${refreshError.message}`, true);
      }
    } else {
      setActionError(service.id, operation, error);
      toast(`${service.name}：${error.message}`, true);
    }
  } finally {
    state.busy.delete(service.id);
    renderServices();
  }
}

function requestRemove(service) {
  state.removeService = service;
  $("#remove-title").textContent = `移除“${service.name}”？`;
  const managed = managedStates.has(service.state);
  const external = service.state === "External Running";
  $("#remove-message").textContent = managed
    ? "服务当前正在运行。确认后会先安全停止服务，再移除登记。"
    : external
      ? "该服务由外部进程启动。移除登记后，外部进程仍会继续运行。"
      : "确认从 Service Hub 中移除这条服务登记吗？";
  $("#confirm-remove").textContent = managed ? "停止并移除登记" : "移除登记";
  $("#remove-dialog").showModal();
}

async function confirmRemove() {
  const service = state.removeService;
  if (!service) return;
  const button = $("#confirm-remove");
  button.disabled = true; button.textContent = "正在移除…";
  state.busy.set(service.id, "remove");
  renderServices();
  try {
    const query = managedStates.has(service.state) ? "?stop=true" : "";
    const result = await fetchJSON(`/api/services/${encodeURIComponent(service.id)}${query}`, { method: "DELETE" });
    $("#remove-dialog").close();
    toast(result.external_process_continues ? "登记已移除；外部进程仍在运行" : "服务登记已移除");
    await Promise.all([refreshServices({ announceError: true }), refreshPorts()]);
  } catch (error) {
    setActionError(service.id, "remove", error);
    toast(error.message, true);
  } finally {
    state.busy.delete(service.id);
    button.disabled = false;
    button.textContent = managedStates.has(service.state) ? "停止并移除登记" : "移除登记";
    renderServices();
  }
}

async function previewTakeover(service, { title = "纳入 Process Compose 管理" } = {}) {
  state.busy.set(service.id, "takeover");
  renderServices();
  try {
    const data = await fetchJSON(`/api/services/${encodeURIComponent(service.id)}/takeover`, { method: "POST", body: JSON.stringify({ confirm: false }) });
    const processes = data.processes?.length ? data.processes : data.process ? [data.process] : [];
    const pids = [...new Set(processes.map((process) => Number(process.pid)).filter((pid) => pid > 0))];
    state.takeover = { service, pids };
    const details = $("#takeover-details"); details.replaceChildren();
    details.append(element("dt", "", "服务"), element("dd", "", data.service.name));
    processes.forEach((process) => {
      const source = process.source === "managed" ? "管理器" : process.source === "external" ? "外部进程" : "未运行";
      const identity = process.pid ? `PID ${process.pid} · ${process.process_name || "未知进程"}` : "无当前 PID";
      const command = process.command_line || process.command || "命令不可用";
      details.append(
        element("dt", "", `端口 :${process.port}`),
        element("dd", "", `${source} · ${identity} · ${command}`),
      );
    });
    details.append(element("dt", "", "登记目录"), element("dd", "", data.service.working_dir));
    $("#takeover-dialog-title").textContent = service.state === "Mixed Running" ? "统一纳入 Process Compose 管理" : title;
    $("#takeover-dialog").showModal();
  } catch (error) { setActionError(service.id, "takeover", error); toast(error.message, true); }
  finally { state.busy.delete(service.id); renderServices(); }
}

async function confirmTakeover() {
  if (!state.takeover) return;
  const service = state.takeover.service;
  const button = $("#confirm-takeover"); button.disabled = true; button.textContent = "正在纳入管理…";
  state.busy.set(service.id, "takeover"); renderServices();
  try {
    const confirmation = { confirm: true, pids: state.takeover.pids };
    if (state.takeover.pids.length === 1) confirmation.pid = state.takeover.pids[0];
    await fetchJSON(`/api/services/${encodeURIComponent(service.id)}/takeover`, { method: "POST", body: JSON.stringify(confirmation) });
    $("#takeover-dialog").close();
    const result = await refreshServiceAfterAction(service, "takeover");
    if (result.settled) toast("外部实例已停止，服务已由本管理器启动");
    else toast(`${service.name}：操作已提交，仍在确认状态，页面会自动更新`);
  } catch (error) { setActionError(service.id, "takeover", error); toast(error.message, true); }
  finally { state.busy.delete(service.id); button.disabled = false; button.textContent = "确认统一纳管"; renderServices(); }
}

async function previewStopExternal(service) {
  state.busy.set(service.id, "stopExternal");
  renderServices();
  try {
    const data = await fetchJSON(`/api/services/${encodeURIComponent(service.id)}/stop-external`, { method: "POST", body: JSON.stringify({ confirm: false }) });
    state.stopExternal = { service, pids: data.processes.map((process) => process.pid) };
    const details = $("#external-stop-details"); details.replaceChildren();
    data.processes.forEach((process) => {
      const summary = `PID ${process.pid} · ${process.process_name || "未知进程"}`;
      details.append(
        element("dt", "", `端口 :${process.port}`),
        element("dd", "", process.command_line ? `${summary} · ${process.command_line}` : summary),
      );
    });
    $("#external-stop-dialog").showModal();
  } catch (error) { setActionError(service.id, "stopExternal", error); toast(error.message, true); }
  finally { state.busy.delete(service.id); renderServices(); }
}

async function confirmStopExternal() {
  if (!state.stopExternal) return;
  const service = state.stopExternal.service;
  const button = $("#confirm-stop-external"); button.disabled = true; button.textContent = "正在停止…";
  state.busy.set(service.id, "stopExternal"); renderServices();
  try {
    await fetchJSON(`/api/services/${encodeURIComponent(service.id)}/stop-external`, { method: "POST", body: JSON.stringify({ confirm: true, pids: state.stopExternal.pids }) });
    $("#external-stop-dialog").close();
    const result = await refreshServiceAfterAction(service, "stopExternal");
    if (result.settled) toast(`${service.name}：外部进程已停止，服务回到已停止状态`);
    else toast(`${service.name}：停止请求已提交，仍在确认状态，页面会自动更新`);
  } catch (error) { setActionError(service.id, "stopExternal", error); toast(error.message, true); }
  finally { state.busy.delete(service.id); button.disabled = false; button.textContent = "确认停止外部进程"; renderServices(); }
}

async function dismissLastRun(service) {
  try {
    await fetchJSON(`/api/services/${encodeURIComponent(service.id)}/last-run`, { method: "DELETE" });
    const current = await fetchJSON(`/api/services/${encodeURIComponent(service.id)}`);
    updateServiceSnapshot(current);
    toast(`${service.name}：已清除运行记录，异常状态已归位`);
  } catch (error) { toast(error.message, true); }
}

function formatStartedAt(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}

function openDetails(service) {
  const current = state.snapshot?.services.find((item) => item.id === service.id) || service;
  state.detailsService = current;
  $("#details-title").textContent = current.name;
  const status = $("#details-status"); status.replaceChildren(statusBlock(current));
  const localFeedback = state.actionErrors.get(current.id);
  if (localFeedback) {
    status.append(element("p", "details-warning", localFeedback.message));
  } else if (faultStates.has(current.state) && (current.last_error || current.error)) {
    status.append(element("p", "details-error", current.last_error || current.error));
  } else if (current.state === "Mixed Running" && current.error) {
    status.append(element("p", "details-warning", current.error));
  }
  const list = $("#details-list"); list.replaceChildren();
  const presentation = presentationFor(current);
  const healthMode = current.effective_health_check_type || current.health_check_type;
  const runtimeViews = current.runtime_views?.length ? current.runtime_views : runtimeItemsFor(current);
  const runtimeSummary = runtimeViews.map((item) => `:${item.port} ${statePresentation[item.state]?.label || item.state || ""}`.trim()).join(" · ");
  const commandSummary = runtimeItemsFor(current).map((item) => `:${item.port} → ${item.command}`).join("；");
  const rows = [
    ["服务名称", current.name],
    ["描述", current.note || "—"],
    ["状态", presentation.label],
    ["启动来源", presentation.origin || "—"],
    ["运行端口", runtimeSummary],
    ["访问 URL", current.effective_url || current.url || "—"],
    ["Health URL", current.effective_health_url || current.health_url || "—"],
    ["健康检查", healthTypeLabels[healthMode] || "—"],
    ["检查目标", current.health_check?.target || "—"],
    ["检查结果", healthStatusLabels[current.health_check?.status] || "未知"],
    ["检查详情", current.health_check?.detail || "—"],
    ["预期状态码", healthMode === "http" ? String(current.effective_health_expected_status || current.health_expected_status || 200) : "—"],
    ["工作目录", current.working_dir],
    ["启动命令", commandSummary],
    ["PID", current.pids?.length ? current.pids.join("、") : current.pid ? String(current.pid) : "—"],
    ["启动时间", formatStartedAt(current.started_at)],
    ["类型", typeLabels[current.type] || current.type],
    ["依赖服务", current.dependency_services?.length ? current.dependency_services.map((item) => item.name).join(" → ") : "无"],
    ["登记状态", current.enabled ? "已启用" : "已禁用"],
  ];
  if (current.last_run) {
    rows.push(
      ["上次启动", formatStartedAt(current.last_run.started_at)],
      ["上次结束", formatStartedAt(current.last_run.stopped_at)],
      ["上次运行时长", formatDuration(current.last_run.duration_seconds)],
      ["上次退出方式", exitTypeLabels[current.last_run.exit_type] || current.last_run.exit_type || "—"],
      ["上次 Exit Code", current.last_run.exit_code ?? "—"],
      ["上次 PID", current.last_run.pid ?? "—"],
      ["上次错误", current.last_run.last_error || "—"],
    );
  }
  if (current.pending_restart && current.active_config) {
    const activeItems = runtimeItemsFor(current.active_config);
    rows.push(
      ["当前运行端口", activeItems.map((item) => String(item.port)).join("、")],
      ["当前运行目录", current.active_config.working_dir],
      ["当前运行命令", activeItems.map((item) => `:${item.port} → ${item.command}`).join("；")],
      ["当前运行 URL", current.active_config.url || "—"],
      ["下次启动端口", runtimeItemsFor(current).map((item) => String(item.port)).join("、")],
      ["下次启动目录", current.working_dir],
      ["下次启动命令", commandSummary],
      ["下次启动 URL", current.url || "—"],
    );
  }
  const conflict = state.actionErrors.get(current.id)?.details?.port_conflict || current.port_occupant;
  if (conflict) {
    rows.push(
      ["端口占用", `:${conflict.port}`],
      ["占用 PID", conflict.pid ? String(conflict.pid) : "不可用"],
      ["占用进程", conflict.process_name || "不可用"],
      ["可执行文件", conflict.executable || "不可用"],
      ["对应登记", conflict.registered_service?.name || "未匹配到登记"],
    );
  }
  rows.forEach(([label, value]) => { list.append(element("dt", "", label), element("dd", "", value)); });
  $("#details-open-logs").hidden = !current.enabled;
  $("#details-dialog").showModal();
}

async function openLogs(service) {
  state.logService = service; state.logData = null; state.logView = "current";
  $("#logs-title").textContent = `${service.name} · 日志诊断`;
  $("#logs-external-note").hidden = !["External Running", "Mixed Running"].includes(service.state);
  $("#logs-output").textContent = "正在载入…"; $("#logs-dialog").showModal(); await refreshLogs();
}

function cleanLogText(value) {
  return String(value ?? "").replace(/\x1B\[[0-?]*[ -/]*[@-~]/g, "");
}

function renderLogs() {
  const data = state.logData;
  if (!data) return;
  const previous = state.logView === "previous";
  const entries = previous ? data.previous_entries : data.entries;
  $("#logs-dialog").querySelectorAll("[data-log-view]").forEach((button) => button.classList.toggle("is-active", button.dataset.logView === state.logView));
  $("#clear-logs").hidden = previous;
  $("#logs-counts").textContent = previous ? `${entries.length} 行归档` : `stdout ${data.stdout_lines} · stderr ${data.stderr_lines}`;
  const summary = $("#logs-error-summary");
  const selectedError = previous ? data.previous_last_error : data.current_last_error;
  summary.hidden = !selectedError;
  summary.textContent = selectedError ? `${previous ? "上次运行错误" : "当前运行错误"}：${selectedError}` : "";
  const output = $("#logs-output"); output.replaceChildren();
  if (!entries.length) {
    output.append(element("div", "logs-empty", previous ? "暂无上一轮运行日志" : "暂无当前运行日志"));
    return;
  }
  entries.forEach((entry) => {
    const row = element("div", "log-line"); row.dataset.level = entry.level;
    row.append(element("span", "log-stream", entry.stream), element("span", "", cleanLogText(entry.text)));
    output.append(row);
  });
  output.scrollTop = output.scrollHeight;
}

async function refreshLogs() {
  if (!state.logService) return;
  try {
    state.logData = await fetchJSON(`/api/services/${encodeURIComponent(state.logService.id)}/logs?limit=200`);
    renderLogs();
  } catch (error) { $("#logs-output").textContent = `日志读取失败：${error.message}`; }
}

async function copyLogs() {
  if (!state.logData) return;
  const entries = state.logView === "previous" ? state.logData.previous_entries : state.logData.entries;
  const text = entries.map((entry) => `[${entry.stream}] ${cleanLogText(entry.text)}`).join("\n");
  if (!text) { toast("当前没有可复制的日志", true); return; }
  try { await navigator.clipboard.writeText(text); toast("日志已复制"); }
  catch { toast("浏览器未允许复制日志", true); }
}

async function clearLogs() {
  if (!state.logService || !window.confirm(`确认清空“${state.logService.name}”当前运行日志吗？上一轮归档会保留。`)) return;
  try {
    await fetchJSON(`/api/services/${encodeURIComponent(state.logService.id)}/logs`, { method: "DELETE" });
    toast("当前运行日志已清空；上一轮日志已保留");
    await refreshLogs();
  } catch (error) { toast(error.message, true); }
}

async function copyServiceUrl(service) {
  const url = service.effective_url || service.url;
  if (!url) { toast("该服务没有可复制的 URL", true); return; }
  try {
    await navigator.clipboard.writeText(url);
    toast(`已复制：${url}`);
  } catch { toast("浏览器未允许复制，请在详情中手动复制 URL", true); }
}

async function openDirectory(service) {
  try {
    await fetchJSON(`/api/services/${encodeURIComponent(service.id)}/open-directory`, { method: "POST" });
    toast(`已打开项目目录：${service.working_dir}`);
  } catch (error) { toast(error.message, true); }
}

async function shutdownHub() {
  const confirmed = window.confirm("只退出 Local Service Hub 管理网页，正在运行的业务服务会继续运行。确认退出吗？");
  if (!confirmed) return;
  const button = $("#shutdown-hub"); button.disabled = true; button.textContent = "正在退出…";
  try {
    const result = await fetchJSON("/api/hub/shutdown", { method: "POST" });
    state.shuttingDown = true;
    $("#refresh-services").disabled = true; $("#refresh-ports").disabled = true;
    const pill = $("#controller-pill"); pill.className = "controller-pill is-offline";
    pill.querySelector("span:last-child").textContent = "Service Hub 正在退出";
    toast(result.message || "Service Hub 正在退出；业务服务将继续运行");
  } catch (error) { button.disabled = false; button.textContent = "退出 Service Hub"; toast(error.message, true); }
}

async function waitForHubRestart(previousInstanceId, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch("/health", { cache: "no-store", headers: { Accept: "application/json" } });
      const health = await response.json().catch(() => ({}));
      if (response.ok && health.instance_id && health.instance_id !== previousInstanceId) return;
    } catch {}
    await new Promise((resolve) => window.setTimeout(resolve, 500));
  }
  throw new Error("Service Hub 重启超时，请稍后刷新页面确认");
}

async function restartHub() {
  const confirmed = window.confirm("重启 Local Service Hub 管理网页？正在运行的业务服务不会受到影响。");
  if (!confirmed) return;
  const button = $("#restart-hub");
  const shutdownButton = $("#shutdown-hub");
  button.disabled = true; button.textContent = "正在提交…"; shutdownButton.disabled = true;
  try {
    const result = await fetchJSON("/api/hub/restart", { method: "POST" });
    state.restarting = true;
    button.textContent = "正在重启…";
    $("#refresh-services").disabled = true; $("#refresh-ports").disabled = true;
    const pill = $("#controller-pill"); pill.className = "controller-pill is-loading";
    pill.querySelector("span:last-child").textContent = "Service Hub 正在重启";
    toast(result.message || "Service Hub 正在重启；业务服务将继续运行");
    await waitForHubRestart(result.instance_id);
    window.location.reload();
  } catch (error) {
    state.restarting = false;
    button.disabled = false; button.textContent = "重启 Service Hub"; shutdownButton.disabled = false;
    toast(error.message, true);
  }
}

function closeActionMenus(except = null) {
  document.querySelectorAll(".action-menu[open]").forEach((menu) => { if (menu !== except) menu.removeAttribute("open"); });
}

$("#ports-grid").addEventListener("click", (event) => { const button = event.target.closest("[data-port]"); if (button) openServiceForm(null, Number(button.dataset.port)); });
$("#groups-grid").addEventListener("click", async (event) => {
  if (event.target.closest("[data-empty-group]")) { openGroupForm(); return; }
  const action = event.target.closest("[data-group-action]");
  if (!action) return;
  const group = state.snapshot?.groups?.find((item) => item.id === action.dataset.group);
  if (!group) return;
  if (action.dataset.groupAction === "start") await startGroup(group);
  else if (action.dataset.groupAction === "edit") openGroupForm(group);
  else if (action.dataset.groupAction === "remove") await removeGroup(group);
});
$("#services-grid").addEventListener("click", async (event) => {
  if (event.target.closest("[data-empty-create]")) { openServiceForm(); return; }
  const actionNode = event.target.closest("[data-action]");
  const card = event.target.closest("[data-service-card]");
  const serviceId = actionNode?.dataset.service || card?.dataset.serviceCard;
  const service = state.snapshot?.services.find((item) => item.id === serviceId);
  if (!service) return;
  if (!actionNode) {
    if (!event.target.closest(".action-menu")) openDetails(service);
    return;
  }
  const action = actionNode.dataset.action;
  closeActionMenus();
  if (action === "open") window.open(service.effective_url || service.url, "_blank", "noopener,noreferrer");
  else if (["start", "stop", "restart"].includes(action)) await runAction(service, action);
  else if (action === "edit") openServiceForm(service);
  else if (action === "remove") requestRemove(service);
  else if (action === "takeover") await previewTakeover(service);
  else if (action === "stop-external") await previewStopExternal(service);
  else if (action === "logs") await openLogs(service);
  else if (action === "details") openDetails(service);
  else if (action === "copy-url") await copyServiceUrl(service);
  else if (action === "open-directory") await openDirectory(service);
  else if (action === "dismiss-last-run") await dismissLastRun(service);
  else if (action === "dismiss-action-error") { state.actionErrors.delete(service.id); renderServices(); }
});
document.addEventListener("click", (event) => { const menu = event.target.closest(".action-menu"); if (!menu) closeActionMenus(); else if (event.target.closest(".menu-item")) menu.removeAttribute("open"); });
document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => $(`#${button.dataset.close}`).close()));
$("#new-service").addEventListener("click", () => openServiceForm());
$("#new-group").addEventListener("click", () => openGroupForm());
$("#restart-hub").addEventListener("click", restartHub);
$("#shutdown-hub").addEventListener("click", shutdownHub);
$("#service-form").addEventListener("submit", saveService);
$("#group-form").addEventListener("submit", saveGroup);
$("#add-runtime-item").addEventListener("click", () => appendRuntimeItem());
$("#runtime-items").addEventListener("click", (event) => {
  const remove = event.target.closest("[data-remove-runtime]");
  if (!remove) return;
  remove.closest("[data-runtime-item]").remove();
  updateRuntimeItemRows();
});
$("#runtime-items").addEventListener("input", (event) => {
  if (event.target.name === "runtime_command") {
    autoResizeRuntimeCommand(event.target);
    return;
  }
  if (event.target.name !== "runtime_port" || !state.urlAutofill) return;
  const firstPort = $("#runtime-items").querySelector('[name="runtime_port"]');
  if (event.target === firstPort && event.target.value) $("#service-form").elements.namedItem("url").value = `http://127.0.0.1:${event.target.value}`;
});
let runtimeResizeFrame = null;
window.addEventListener("resize", () => {
  if (runtimeResizeFrame) window.cancelAnimationFrame(runtimeResizeFrame);
  runtimeResizeFrame = window.requestAnimationFrame(() => resizeRuntimeCommands($("#runtime-items")));
});
$("#service-form").elements.namedItem("url").addEventListener("input", () => { state.urlAutofill = false; });
$("#service-form").elements.namedItem("health_check_type").addEventListener("change", (event) => syncHealthCheckFields(event.currentTarget.form));
$("#refresh-ports").addEventListener("click", () => refreshPorts({ announceError: true }));
$("#refresh-services").addEventListener("click", () => refreshServices({ announceError: true }));
$("#controller-alert").addEventListener("click", (event) => { if (event.target.closest('[data-action="retry"]')) refreshServices({ announceError: true }); });
$("#restore-backup").addEventListener("click", async () => { if (!window.confirm("确认使用 services.json.bak 恢复登记吗？损坏文件会另存保留。")) return; try { await fetchJSON("/api/store/restore-backup", { method: "POST" }); toast("已从备份恢复"); await refreshServices({ announceError: true }); } catch (error) { toast(error.message, true); } });
$("#confirm-takeover").addEventListener("click", confirmTakeover);
$("#confirm-stop-external").addEventListener("click", confirmStopExternal);
$("#takeover-dialog").addEventListener("close", () => { state.takeover = null; });
$("#external-stop-dialog").addEventListener("close", () => { state.stopExternal = null; });
$("#confirm-remove").addEventListener("click", confirmRemove);
$("#refresh-logs").addEventListener("click", refreshLogs);
$("#copy-logs").addEventListener("click", copyLogs);
$("#clear-logs").addEventListener("click", clearLogs);
$("#logs-dialog").addEventListener("click", (event) => {
  const button = event.target.closest("[data-log-view]"); if (!button) return;
  state.logView = button.dataset.logView === "previous" ? "previous" : "current"; renderLogs();
});
$("#details-open-logs").addEventListener("click", async () => { if (!state.detailsService) return; $("#details-dialog").close(); await openLogs(state.detailsService); });
$("#details-edit").addEventListener("click", () => { if (!state.detailsService) return; $("#details-dialog").close(); openServiceForm(state.detailsService); });
$("#service-search").addEventListener("input", (event) => { state.query = event.target.value; renderServices(); });
$("#service-filters").addEventListener("click", (event) => {
  const button = event.target.closest("[data-filter]"); if (!button) return;
  state.filter = button.dataset.filter;
  $("#service-filters").querySelectorAll("[data-filter]").forEach((item) => item.classList.toggle("is-active", item === button));
  renderServices();
});
$("#view-switch").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]"); if (!button) return;
  state.view = button.dataset.view === "compact" ? "compact" : "cards";
  try { window.localStorage.setItem("service-hub-view", state.view); } catch {}
  renderServices();
});

syncHealthCheckFields($("#service-form"));
Promise.all([refreshServices({ announceError: true }), refreshPorts({ announceError: true })]);

const AUTO_REFRESH_INTERVAL_MS = 5000;
let autoRefreshTimer = null;
let deferredRefreshTimer = null;

function resumeDeferredRefresh() {
  if (!state.autoRefreshDeferred || document.hidden || interactionBlocksAutoRefresh()) return;
  if (deferredRefreshTimer) window.clearTimeout(deferredRefreshTimer);
  deferredRefreshTimer = window.setTimeout(async () => {
    deferredRefreshTimer = null;
    if (interactionBlocksAutoRefresh()) return;
    await refreshServices({ automatic: true });
  }, 0);
}

function scheduleAutoRefresh() {
  if (autoRefreshTimer) window.clearTimeout(autoRefreshTimer);
  autoRefreshTimer = window.setTimeout(async () => {
    if (!document.hidden) await refreshServices({ automatic: true });
    scheduleAutoRefresh();
  }, AUTO_REFRESH_INTERVAL_MS);
}
document.addEventListener("close", resumeDeferredRefresh, true);
document.addEventListener("toggle", (event) => {
  if (event.target.matches?.(".action-menu") && !event.target.open) resumeDeferredRefresh();
}, true);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    state.autoRefreshDeferred = true;
    resumeDeferredRefresh();
  }
});
scheduleAutoRefresh();
