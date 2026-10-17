const API = "/api/v1";
let conversationId = crypto.randomUUID();
const AUTH_SESSION_PREFIX = "moveinmate.local.session.";
const AUTH_DEMO_ROLE_KEY = "moveinmate.demo-role";
const AUTO_TTS_STORAGE_KEY = "moveinmate-auto-tts";
const speechSupported = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
const recordingSupported = Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
const allowedExtensions = new Set(["jpg", "jpeg", "png", "webp", "pdf", "docx", "txt"]);
const imageExtensions = new Set(["jpg", "jpeg", "png", "webp"]);
const speechAudioCache = new Map();
const interfaceIcons = Object.freeze({
  speaker: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5 6.8 8.5H3.5v7h3.3L11 19V5Z"/><path d="M15 9a4.2 4.2 0 0 1 0 6M17.8 6.5a7.7 7.7 0 0 1 0 11"/></svg>',
  microphone: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M8.5 21h7"/></svg>',
  stop: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>',
  loading: '<svg class="ui-icon icon-spinner" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/></svg>',
});
let selectedAttachments = [];
let activeSpeechButton = null;
let activeAudio = null;
let activeAudioUrl = "";
let speechAudioContext = null;
let activeAudioSource = null;
let speechPlaybackId = 0;
let speechProvider = "";
let streamedSpeech = null;
let speechWaitTimer = null;
let mediaRecorder = null;
let microphoneStream = null;
let recordingChunks = [];
let recordingTimer = null;
let recordingActive = false;
let recordingBaseText = "";
let omicxConfigured = false;
let geminiTtsConfigured = false;
let autoTtsEnabled = loadAutoTtsPreference();
let managementRequests = [];
let managementPage = { offset: 0, limit: 20, total: 0, loadId: 0 };
let residentTicketPage = { offset: 0, limit: 20, total: 0, loadId: 0 };
let pendingDecision = null;
let notificationPollInProgress = false;
let residentOwnershipState = { ready: false, profile: null };
let residentChecklistState = null;
let householdLoadId = 0;
let ownershipToastTimer = null;

function ownershipRequired() {
  return authReady && hasRole("resident") && (!residentOwnershipState.ready || !residentOwnershipState.profile?.ownership_confirmed_at);
}

function showOwnershipWarning(message = "Hãy xác nhận thông tin cư trú") {
  const toast = document.getElementById("ownership-toast");
  clearTimeout(ownershipToastTimer);
  document.getElementById("ownership-toast-message").textContent = message;
  toast.hidden = false;
  ownershipToastTimer = setTimeout(() => { toast.hidden = true; }, 1500);
}

function closeOwnershipWarning() {
  clearTimeout(ownershipToastTimer);
  document.getElementById("ownership-toast").hidden = true;
}

function synchronizeOwnershipUi() {
  document.getElementById("ownership-onboarding").hidden = !ownershipRequired();
  const profile = residentOwnershipState.profile;
  const button = document.getElementById("personal-residency");
  button.hidden = !authReady || !hasRole("resident") || !profile || Boolean(profile.ownership_confirmed_at);
  button.textContent = profile?.resident_type === "owner" ? "Xác thực quyền sở hữu căn hộ" : "Xác nhận thông tin cư trú";
  document.getElementById("ownership-onboarding-title").textContent = button.textContent;
  synchronizeProcedureCards();
}

document.addEventListener("click", event => {
  if (!ownershipRequired()) return;
  const button = event.target.closest("button");
  if (!button) return;
  if (button.closest("#ownership-toast, #personal-dialog, #password-change-dialog, #password-result-dialog") || ["user-role", "logout-button", "agent-motion-toggle", "refresh-progress", "tab-agent", "tab-progress"].includes(button.id)) return;
  if (button.dataset.form === "verify_residency") return;
  if (button.closest("#resident-entry-dialog") && residentEntryState?.type === "ownership") return;
  if (!button.closest("main, header, #chat-widget, #chat-launcher, #procedure-picker")) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  showOwnershipWarning();
}, true);
document.querySelector("#ownership-onboarding button").addEventListener("click", () => { void openResidentEntry("verify_residency"); });
document.getElementById("ownership-toast-close").addEventListener("click", closeOwnershipWarning);
document.addEventListener("keydown", event => { if (event.key === "Escape") closeOwnershipWarning(); });

let authConfig = null;
let authSession = null;
let authContext = null;
let authReady = false;
let authGeneration = 0;
let authAbortController = new AbortController();
let notificationIntervalId = null;
let demoRole = "resident";

const authRoleLabels = { resident: "Cư dân", manager: "Ban quản lý" };

function authStorageKey() {
  return `${AUTH_SESSION_PREFIX}${encodeURIComponent(window.location.origin)}`;
}

function readStoredSession() {
  try {
    const raw = sessionStorage.getItem(authStorageKey());
    if (!raw) return null;
    const value = JSON.parse(raw);
    return value?.access_token ? value : null;
  } catch {
    return null;
  }
}

function persistSession(session) {
  authSession = session ? {
    access_token: session.access_token,
    expires_in: session.expires_in,
    expires_at: session.expires_at || (session.expires_in ? Math.floor(Date.now() / 1000) + Number(session.expires_in) : undefined),
    token_type: session.token_type || "bearer",
  } : null;
  try {
    const key = authStorageKey();
    if (authSession) sessionStorage.setItem(key, JSON.stringify(authSession));
    else sessionStorage.removeItem(key);
  } catch {
    // Session remains in memory for browsers with disabled storage.
  }
}

function clearStoredSession() {
  persistSession(null);
}

function isSessionCurrent(generation = authGeneration) {
  return authReady && generation === authGeneration;
}

function currentResidentId() {
  const residents = Array.isArray(authContext?.resident_ids) ? authContext.resident_ids : authContext?.resident_ids ? [authContext.resident_ids] : [];
  const explicit = authContext?.resident_id || residents[0];
  if (explicit) return String(explicit);
  return authConfig?.auth_mode === "demo" ? "R001" : "";
}

function residentPath(suffix = "") {
  const residentId = currentResidentId();
  return residentId ? `/residents/${encodeURIComponent(residentId)}${suffix}` : "";
}

function hasRole(role) {
  const roles = Array.isArray(authContext?.roles) ? authContext.roles : authContext?.roles ? [authContext.roles] : [];
  return Boolean(roles.some((value) => String(value).toLowerCase() === role));
}

function authHeaders(headers = {}) {
  const result = new Headers(headers);
  if (authSession?.access_token) result.set("Authorization", `Bearer ${authSession.access_token}`);
  if (authConfig?.auth_mode === "demo") result.set("X-Demo-Role", demoRole);
  return result;
}

function cloneRequestOptions(options = {}) {
  return { ...options, headers: authHeaders(options.headers || {}) };
}

function loadAutoTtsPreference() {
  try {
    return localStorage.getItem(AUTO_TTS_STORAGE_KEY) === "on";
  } catch {
    return false;
  }
}

function saveAutoTtsPreference() {
  try {
    localStorage.setItem(AUTO_TTS_STORAGE_KEY, autoTtsEnabled ? "on" : "off");
  } catch {
    // The toggle still works for this tab when browser storage is unavailable.
  }
}

const residentTypeLabels = { owner: "Chủ sở hữu", tenant: "Người thuê", family_member: "Thành viên gia đình" };
const requestTypeLabels = {
  vehicle_registration: "Đăng ký phương tiện",
  resident_card: "Thẻ cư dân",
  card_replacement: "Báo mất / cấp lại thẻ",
  visitor_registration: "Khách và người nhà",
  temporary_residency: "Đăng ký tạm trú",
  verify_residency: "Xác nhận cư trú",
  move_in_schedule: "Lịch chuyển đồ",
  handover_inspection: "Bàn giao căn hộ",
  emergency_contact: "Liên hệ khẩn cấp",
  account_registration: "Đăng ký tài khoản cư dân phụ thuộc",
  internet_registration: "Đăng ký internet",
  general_support: "Yêu cầu khác",
};
const taskStatusLabels = {
  pending: "Chưa làm",
  in_progress: "Đang thực hiện",
  submitted: "Đã gửi BQL",
  in_review: "Đã tiếp nhận",
  needs_info: "Cần bổ sung",
  approved: "Đã duyệt",
  rejected: "Bị từ chối",
  cancelled_by_resident: "Đã hủy",
  completed: "Đã hoàn tất",
};
const requestStatusLabels = {
  submitted: "Đã gửi",
  in_review: "Đã tiếp nhận",
  needs_info: "Cần bổ sung",
  approved: "Đã duyệt",
  rejected: "Từ chối",
  completed: "Hoàn tất",
  cancelled_by_resident: "Đã hủy bởi người dùng",
};
const requestNextSteps = {
  submitted: "Đang chờ Ban quản lý tiếp nhận. Bạn chưa cần gửi lại.",
  in_review: "Ban quản lý đang xem xét. Bạn chờ kết quả xử lý.",
  needs_info: "Bạn cần xem lý do bên dưới, bổ sung thông tin và gửi lại yêu cầu.",
  approved: "Ban quản lý đã duyệt; yêu cầu vẫn chờ bước hoàn tất.",
  rejected: "Yêu cầu đã bị từ chối. Xem lý do trong lịch sử xử lý.",
  completed: "Yêu cầu đã hoàn tất. Bạn không cần thao tác thêm.",
  cancelled_by_resident: "Yêu cầu đã hủy. Bạn không cần thao tác thêm.",
};
const requestActionMap = {
  submitted: [
    { status: "in_review", label: "Tiếp nhận", style: "secondary" },
    { status: "needs_info", label: "Cần bổ sung", style: "warning" },
    { status: "approved", label: "Duyệt", style: "primary" },
    { status: "rejected", label: "Từ chối", style: "danger" },
  ],
  in_review: [
    { status: "needs_info", label: "Cần bổ sung", style: "warning" },
    { status: "approved", label: "Duyệt", style: "primary" },
    { status: "rejected", label: "Từ chối", style: "danger" },
  ],
  needs_info: [
    { status: "rejected", label: "Từ chối", style: "danger" },
  ],
  approved: [{ status: "completed", label: "Hoàn thành", style: "primary" }],
  rejected: [],
  completed: [],
  cancelled_by_resident: [],
};
const toolLabels = {
  get_resident_profile: "Đã xem hồ sơ cư dân",
  build_checklist: "Đã lập danh sách việc cần làm",
  get_onboarding_progress: "Đã kiểm tra tiến độ",
  search_regulations: "Đã tra cứu hướng dẫn và nguồn",
  update_task_status: "Đã cập nhật tiến độ",
  create_management_request: "Đã gửi yêu cầu đến Ban quản lý",
  list_resident_requests: "Đã đối chiếu lịch sử yêu cầu",
  get_request_status: "Đã kiểm tra yêu cầu",
  cancel_management_request: "Đã hủy ticket",
  update_management_request: "Đã bổ sung vào ticket cũ",
  get_household_dashboard: "Đã xem thông tin căn hộ",
};
const apiErrorLabels = {
  project_scope_required: "Trợ lý hiện chỉ hỗ trợ dự án MoveInMate.",
  resident_role_required: "Bạn cần đăng nhập bằng tài khoản cư dân để dùng trợ lý.",
  demo_reset_disabled: "Reset chỉ được phép trên DB demo và tài khoản Ban quản lý đúng khu.",
  invalid_status_transition: "Không thể chuyển sang trạng thái này ở bước hiện tại.",
  task_no_need_not_allowed: "Mục này không hỗ trợ lựa chọn Chưa có nhu cầu.",
  task_has_active_request: "Mục này đang có ticket được xử lý. Hãy theo dõi hoặc hủy đúng quy trình, không đánh dấu Chưa có nhu cầu.",
  checklist_changed: "Thông tin đã thay đổi. Đóng và mở lại biểu mẫu để kiểm tra trước khi xác nhận.",
  task_already_completed: "Mục này đã hoàn tất, không cần xác nhận Chưa có nhu cầu.",
  attachment_required: "Vui lòng chọn ít nhất một tệp.",
  agent_not_configured: "Trợ lý đang tạm ngưng. Vui lòng thử lại sau.",
  llm_authentication_failed: "Trợ lý đang tạm ngưng. Vui lòng báo người quản lý hệ thống.",
  llm_provider_error: "Trợ lý chưa thể trả lời lúc này. Vui lòng thử lại sau.",
  gemini_tts_not_configured: "Giọng đọc hiện chưa sẵn sàng.",
  omicx_not_configured: "Nhập bằng giọng nói hiện chưa sẵn sàng.",
  ptit_tts_not_configured: "Giọng đọc hiện chưa sẵn sàng.",
  ptit_stt_not_configured: "Nhập bằng giọng nói hiện chưa sẵn sàng.",
};

function speechActionMarkup() {
  return `<div class="message-actions"><button class="speak-button" type="button" aria-label="Đọc câu trả lời">${interfaceIcons.speaker}<span>Nghe</span></button></div>`;
}

function setSpeechButtonAppearance(button, state) {
  const states = {
    idle: { icon: "speaker", label: "Nghe", ariaLabel: "Đọc câu trả lời" },
    loading: { icon: "loading", label: "Đang tạo", ariaLabel: "Đang tạo giọng đọc" },
    playing: { icon: "stop", label: "Dừng", ariaLabel: "Dừng đọc câu trả lời" },
  };
  const current = states[state] || states.idle;
  button.innerHTML = `${interfaceIcons[current.icon]}<span>${current.label}</span>`;
  button.setAttribute("aria-label", current.ariaLabel);
}

const escapeHtml = (value) => value.replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[char]));

function renderInlineMarkdown(value) {
  const codeSpans = [];
  const withPlaceholders = value.replace(/`([^`\n]+)`/g, (_, code) => {
    const index = codeSpans.push(`<code>${escapeHtml(code)}</code>`) - 1;
    return `\u0000CODE${index}\u0000`;
  });

  return escapeHtml(withPlaceholders)
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/\u0000CODE(\d+)\u0000/g, (_, index) => codeSpans[Number(index)]);
}

function renderMarkdown(value) {
  const lines = value.replace(/\r\n?/g, "\n").split("\n");
  const output = [];
  let paragraph = [];
  let listType = null;
  let codeBlock = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    output.push(`<p>${paragraph.map((line) => `<span class="answer-text-line">${renderInlineMarkdown(line)}</span>`).join("")}</p>`);
    paragraph = [];
  };

  const closeList = () => {
    if (!listType) return;
    output.push(`</${listType}>`);
    listType = null;
  };

  lines.forEach((line) => {
    if (/^```/.test(line.trim())) {
      flushParagraph();
      closeList();
      if (codeBlock === null) {
        codeBlock = [];
      } else {
        output.push(`<pre><code>${escapeHtml(codeBlock.join("\n"))}</code></pre>`);
        codeBlock = null;
      }
      return;
    }

    if (codeBlock !== null) {
      codeBlock.push(line);
      return;
    }

    if (!line.trim()) {
      flushParagraph();
      return;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length;
      output.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      return;
    }

    const unorderedItem = line.match(/^\s*[-+*]\s+(.+)$/);
    const orderedItem = line.match(/^\s*(\d+)[.)]\s+(.+)$/);
    const item = unorderedItem || orderedItem;
    if (item) {
      flushParagraph();
      const nextListType = unorderedItem ? "ul" : "ol";
      if (listType !== nextListType) {
        closeList();
        const start = orderedItem ? Number(orderedItem[1]) : 1;
        const startAttribute = orderedItem && Number.isSafeInteger(start) && start !== 1 ? ` start="${start}"` : "";
        output.push(`<${nextListType}${startAttribute}>`);
        listType = nextListType;
      }
      output.push(`<li>${renderInlineMarkdown(unorderedItem ? unorderedItem[1] : orderedItem[2])}</li>`);
      return;
    }

    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      closeList();
      output.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
      return;
    }

    closeList();
    paragraph.push(line);
  });

  if (codeBlock !== null) {
    output.push(`<pre><code>${escapeHtml(codeBlock.join("\n"))}</code></pre>`);
  }
  flushParagraph();
  closeList();
  return output.join("");
}

async function authorizedFetch(url, options = {}) {
  const path = new URL(url, window.location.origin).pathname;
  if (!authReady && ![`${API}/me/context`, `${API}/auth/password`, `${API}/auth/password/first-login`, `${API}/auth/username-availability`, `${API}/auth/password/status`, `${API}/auth/logout`].includes(path)) {
    throw new Error("Bạn cần hoàn tất đăng nhập và đổi mật khẩu được cấp trước khi sử dụng ứng dụng.");
  }
  const generation = authGeneration;
  const requestOptions = cloneRequestOptions(options);
  if (!requestOptions.signal) requestOptions.signal = authAbortController.signal;
  const response = await fetch(url, requestOptions);
  if (response.status === 403 && generation === authGeneration) {
    const data = await response.clone().json().catch(() => ({}));
    if (data.detail?.code === "password_change_required") {
      await finishAuthentication({ ...authContext, password_change_required: true });
    } else if (data.detail?.code === "membership_required") {
      showLogin("Tài khoản đang tạm ngừng sử dụng hoặc chưa được cấp quyền. Vui lòng liên hệ Ban quản lý.");
    }
  }
  return response;
}

function presentAuthError(message, { retry = false } = {}) {
  const error = document.getElementById("auth-error");
  const retryButton = document.getElementById("auth-retry");
  if (error) {
    error.textContent = message;
    error.hidden = !message;
  }
  if (retryButton) retryButton.hidden = !retry;
}

function showLogin(message = "") {
  residentOwnershipState = { ready: false, profile: null };
  residentChecklistState = null;
  clearTimeout(ownershipToastTimer);
  document.getElementById("ownership-toast").hidden = true;
  document.getElementById("ownership-onboarding").hidden = true;
  if (document.getElementById("password-change-dialog").open) document.getElementById("password-change-dialog").close();
  if (typeof resetManagementAgent === "function") resetManagementAgent();
  if (typeof clearPortalSession === "function") clearPortalSession();
  if (typeof clearNotificationSession === "function") clearNotificationSession();
  notificationPollInProgress = false;
  resetChatPresentation();
  document.getElementById("messages").replaceChildren();
  document.getElementById("chat-input").value = "";
  document.getElementById("file-input").value = "";
  selectedAttachments = [];
  renderAttachmentPreview();
  conversationId = crypto.randomUUID();
  authReady = false;
  authContext = null;
  closeRequiredPasswordDialog();
  clearStoredSession();
  authSession = null;
  authGeneration += 1;
  authAbortController.abort();
  authAbortController = new AbortController();
  document.getElementById("auth-screen").hidden = false;
  document.getElementById("main-content").hidden = true;
  document.getElementById("application-footer").hidden = true;
  document.getElementById("login-reset").hidden = true;
  document.getElementById("header-actions").hidden = true;
  document.getElementById("chat-launcher").hidden = true;
  document.getElementById("chat-widget").hidden = true;
  document.getElementById("auth-loading").hidden = true;
  document.getElementById("login-form").hidden = false;
  document.getElementById("auth-retry").hidden = true;
  presentAuthError(message);
}

async function api(path, options = {}) {
  const generation = authGeneration;
  const response = await authorizedFetch(`${API}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && generation === authGeneration && authConfig?.auth_mode !== "demo") {
      showLogin("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.");
    }
    const detail = data.detail;
    const code = typeof detail === "string" ? detail : detail?.code;
    const fieldLabels = { citizen_id: "số CCCD (12 chữ số)", person_name: "họ tên người cư trú", vehicle_plate: "biển số xe", vehicle_type: "loại xe", visit_date: "ngày đến/cư trú", visitor_name: "họ tên khách", move_time: "giờ chuyển đồ", pickup_date: "ngày nhận thẻ", pickup_time: "giờ nhận thẻ", details: "nội dung yêu cầu" };
    const invalid = detail?.invalid_fields || detail?.missing_fields;
    const fieldMessage = Array.isArray(invalid) && invalid.length ? `Kiểm tra ${invalid.map((field) => fieldLabels[field] || field).join(", ")}.` : "";
    const message = apiErrorLabels[code] || (typeof detail === "string" ? detail : detail?.message) || fieldMessage;
    const error = new Error(message || "Yêu cầu thất bại");
    error.code = code;
    throw error;
  }
  return data;
}

const usernameChecks = new WeakMap();

function showUsernameError(input, errorId, message = "") {
  const error = document.getElementById(errorId);
  error.textContent = message;
  error.hidden = !message;
  input.setCustomValidity(message);
  input.setAttribute("aria-invalid", String(Boolean(message)));
}

function resetUsernameCheck(input, errorId) {
  clearTimeout(usernameChecks.get(input)?.timer);
  usernameChecks.set(input, {});
  input.removeAttribute("aria-busy");
  showUsernameError(input, errorId);
}

async function validateUsernameInput(input, errorId) {
  clearTimeout(usernameChecks.get(input)?.timer);
  const state = {};
  usernameChecks.set(input, state);
  const username = input.value.trim().toLowerCase();
  const generation = authGeneration;
  const current = () => generation === authGeneration && Boolean(authContext);
  showUsernameError(input, errorId);
  if (!/^[a-z0-9_.@-]{1,80}$/.test(username)) {
    showUsernameError(input, errorId, "Dùng chữ không dấu, số hoặc dấu _ . @ -, tối đa 80 ký tự.");
    return false;
  }
  if (input.id === "first-password-username" && authContext?.username_change_required && username === authContext.username.toLowerCase()) {
    showUsernameError(input, errorId, "Hãy chọn tên đăng nhập mới, khác tài khoản Ban quản lý đã cấp.");
    return false;
  }
  input.setAttribute("aria-busy", "true");
  try {
    const result = await api("/auth/username-availability", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username }) });
    if (usernameChecks.get(input) !== state || input.value.trim().toLowerCase() !== username || !current()) return false;
    showUsernameError(input, errorId, result.available ? "" : "Tên đăng nhập này đã được sử dụng. Hãy chọn tên khác.");
    return Boolean(result.available);
  } catch (error) {
    if (usernameChecks.get(input) === state && current()) showUsernameError(input, errorId, error.message || "Chưa kiểm tra được tên đăng nhập. Vui lòng thử lại.");
    return false;
  } finally {
    if (usernameChecks.get(input) === state) input.removeAttribute("aria-busy");
  }
}

function bindUsernameValidation(input, errorId) {
  input.addEventListener("input", () => {
    resetUsernameCheck(input, errorId);
    if (input.value.trim()) usernameChecks.get(input).timer = setTimeout(() => void validateUsernameInput(input, errorId), 400);
  });
  input.addEventListener("blur", () => { if (input.value.trim() && !input.readOnly) void validateUsernameInput(input, errorId); });
}

async function streamApi(path, options = {}, onEvent = () => {}) {
  const generation = authGeneration;
  const response = await authorizedFetch(`${API}${path}`, options);
  if (!response.ok) {
    if (response.status === 401 && generation === authGeneration && authConfig?.auth_mode !== "demo") {
      showLogin("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.");
    }
    const data = await response.json().catch(() => ({}));
    const detail = data.detail;
    const code = typeof detail === "string" ? detail : detail?.code;
    const message = apiErrorLabels[code] || (typeof detail === "string" ? detail : detail?.message);
    throw new Error(message || "Yêu cầu thất bại");
  }
  if (!response.body) throw new Error("Trình duyệt không hỗ trợ nhận câu trả lời trực tiếp.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = null;

  const consumeLine = (line) => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === "error") {
      if (event.code === "session_reset" && generation === authGeneration) showLogin("Hệ thống đã Reset. Vui lòng đăng nhập lại.");
      throw new Error(apiErrorLabels[event.code] || event.message || "Luồng trả lời bị gián đoạn.");
    }
    onEvent(event);
    if (event.type === "done") completed = event;
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    lines.forEach(consumeLine);
    if (done) break;
  }
  if (buffer.trim()) consumeLine(buffer);
  if (!completed) throw new Error("Câu trả lời kết thúc chưa đầy đủ. Vui lòng thử lại.");
  return completed;
}

// AG-UI SSE client. A2UI surfaces arrive as a CUSTOM event named "a2ui".
async function streamAGUI(path, options = {}, onEvent = () => {}) {
  const generation = authGeneration;
  const response = await authorizedFetch(`${API}${path}`, options);
  if (!response.ok) {
    if (response.status === 401 && generation === authGeneration && authConfig?.auth_mode !== "demo") {
      showLogin("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.");
    }
    const data = await response.json().catch(() => ({}));
    const detail = data.detail;
    const code = typeof detail === "string" ? detail : detail?.code;
    throw new Error(apiErrorLabels[code] || (typeof detail === "string" ? detail : detail?.message) || "Yêu cầu thất bại");
  }
  if (!response.body) throw new Error("Trình duyệt không hỗ trợ nhận câu trả lời trực tiếp.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = null;
  let a2ui = null;

  const consumeBlock = (block) => {
    const data = block.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim()).join("\n");
    if (!data) return;
    const event = JSON.parse(data);
    if (event.type === "RUN_ERROR") {
      if (event.code === "session_reset" && generation === authGeneration) showLogin("Hệ thống đã Reset. Vui lòng đăng nhập lại.");
      throw new Error(event.message || "Luồng trả lời bị gián đoạn.");
    }
    if (event.type === "TEXT_MESSAGE_CONTENT") onEvent({ type: "token", content: event.delta || "" });
    if (event.type === "CUSTOM" && event.name === "speech_segment") onEvent({ ...event.value, type: "speech_segment" });
    if (event.type === "CUSTOM" && event.name === "stream_warning") onEvent({ ...event.value, type: "warning" });
    if (event.type === "CUSTOM" && event.name === "a2ui") a2ui = event.value;
    if (event.type === "RUN_FINISHED") completed = { ...(event.result || {}), ...(a2ui ? { a2ui } : {}) };
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";
    blocks.forEach(consumeBlock);
    if (done) break;
  }
  if (buffer.trim()) consumeBlock(buffer);
  if (!completed) throw new Error("Câu trả lời kết thúc chưa đầy đủ. Vui lòng thử lại.");
  return completed;
}

function a2uiValue(value, data) {
  if (value && typeof value === "object" && typeof value.path === "string") {
    return value.path.split("/").filter(Boolean).reduce((current, key) => current?.[key], data);
  }
  if (value && typeof value === "object" && Object.prototype.hasOwnProperty.call(value, "literalString")) return value.literalString;
  return value ?? "";
}

function checklistStatusLabel(task) {
  return task.coming_soon ? "Coming soon" : task.completion_reason === "not_needed" ? "Đã hoàn tất · Chưa có nhu cầu" : taskStatusLabels[task.status] || "Chưa thực hiện";
}

function synchronizeProcedureCards(data, ownershipOnly = false) {
  if (data?.all_tasks) residentChecklistState = data;
  const tasks = new Map((ownershipOnly ? [] : residentChecklistState?.all_tasks || []).map(task => [task.code, task]));
  const profile = residentOwnershipState.profile;
  if (profile) tasks.set("verify_residency", {
    ...tasks.get("verify_residency"), code: "verify_residency", required: true,
    title: profile.resident_type === "owner" ? "Xác thực quyền sở hữu căn hộ" : "Xác nhận thông tin cư trú",
    status: profile.ownership_confirmed_at ? "completed" : "pending",
  });
  document.querySelectorAll("[data-procedure-row]").forEach(row => {
    const task = tasks.get(row.dataset.procedureRow);
    if (!task) return;
    row.dataset.status = task.status;
    const status = row.querySelector(".a2ui-status");
    const requirement = row.querySelector(".a2ui-requirement");
    const title = row.querySelector(".a2ui-title");
    if (status) status.textContent = checklistStatusLabel(task);
    if (requirement) requirement.textContent = task.required ? "Bắt buộc" : "Tùy chọn";
    if (title) title.textContent = task.title;
  });
}

async function refreshChecklistCards() {
  if (!authReady || !hasRole("resident")) return;
  const generation = authGeneration;
  try {
    const data = await api(residentPath("/progress"));
    if (!isSessionCurrent(generation)) return;
    synchronizeProcedureCards(data);
    filterProcedureDirectory();
  } catch { }
}

function renderA2UI(envelope, host) {
  const messages = envelope?.messages || [];
  const surfaces = new Map();
  messages.forEach((message) => {
    if (message.createSurface) {
      const id = message.createSurface.surfaceId;
      surfaces.set(id, { components: new Map(), data: {} });
    }
    if (message.updateComponents) {
      const state = surfaces.get(message.updateComponents.surfaceId);
      if (state) message.updateComponents.components.forEach((component) => state.components.set(component.id, component));
    }
    if (message.updateDataModel) {
      const state = surfaces.get(message.updateDataModel.surfaceId);
      if (state && (!message.updateDataModel.path || message.updateDataModel.path === "/")) state.data = message.updateDataModel.value || {};
    }
  });
  const surface = surfaces.get(envelope.surface_id) || surfaces.values().next().value;
  if (!surface) return;
  const build = (id, seen = new Set()) => {
    if (!id || seen.has(id)) return document.createDocumentFragment();
    const item = surface.components.get(id);
    if (!item) return document.createDocumentFragment();
    const nextSeen = new Set(seen).add(id);
    const props = item;
    let node;
    if (props.component === "Card") node = document.createElement("section");
    else if (props.component === "Column") node = document.createElement("div");
    else if (props.component === "Row") node = document.createElement("div");
    else if (props.component === "Text") { node = document.createElement("p"); node.textContent = String(a2uiValue(props.text, surface.data)); }
    else if (props.component === "Divider") { node = document.createElement("hr"); }
    else if (props.component === "Button") {
      node = document.createElement("button");
      node.type = "button";
      node.textContent = String(a2uiValue(props.text, surface.data));
      if (typeof props.ariaLabel === "string") node.setAttribute("aria-label", props.ariaLabel);
      const event = props.action?.event;
      const context = event?.context || {};
      if (typeof context.task_code === "string") node.dataset.procedure = context.task_code;
      node.dataset.chatAction = event?.name || "";
      const allowed = ["send_message", "open_tab", "open_procedures", "focus_input", "open_form"].includes(event?.name);
      if (props.disabled === true) node.dataset.fixedDisabled = "true";
      node.disabled = props.disabled === true || !allowed || (["send_message", "focus_input"].includes(event?.name) && Boolean(document.getElementById?.("chat-input")?.disabled));
      node.addEventListener("click", () => handleUIAction(event?.name, context));
    } else return document.createDocumentFragment();
    node.className = `a2ui-${String(props.component).toLowerCase()}`;
    if (["procedure", "procedure-copy", "title", "muted", "requirement", "status", "primary", "secondary", "actions"].includes(props.variant)) node.className += ` a2ui-${props.variant}`;
    if (props.variant === "procedure" && typeof props.taskCode === "string") {
      const choice = (surface.data.procedures || []).find((item) => item.code === props.taskCode);
      if (choice) {
        node.dataset.procedureRow = choice.code;
        node.dataset.status = choice.status_code || "pending";
        node.dataset.search = normalizeProcedureSearch(`${choice.title} ${choice.description}`);
      }
    }
    if (props.child) node.appendChild(build(props.child, nextSeen));
    if (Array.isArray(props.children)) props.children.forEach((child) => node.appendChild(build(child, nextSeen)));
    return node;
  };
  host.replaceChildren(build("root"));
  synchronizeProcedureCards(null, true);
}

function handleUIAction(name, context = {}) {
  if (name === "send_message" && typeof context.message === "string") sendSuggestedPrompt(context.message);
  else if (name === "open_procedures") void openProcedurePicker();
  else if (name === "open_tab" && ["agent", "progress", "tickets", "regulations", "household", "notifications"].includes(context.tab)) {
    if (context.tab === "tickets" && /^REQ-[A-Z0-9]{8}$/.test(context.request_id || "")) void openRelatedTicket(context.request_id);
    else openResidentTab(context.tab);
  }
  else if (name === "focus_input" && typeof context.message === "string") focusChatDraft(context.message);
  else if (name === "open_form" && ["profile", "resident_card", "vehicle_registration", "verify_residency", "temporary_residency", "card_replacement", "visitor_registration", "move_in_schedule", "internet_registration", "general_support", "account_registration"].includes(context.form)) void openResidentEntry(context.form, context.form === "general_support" && typeof context.draft_question === "string" ? context.draft_question.slice(0, 1800) : "");
}

function normalizeProcedureSearch(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[đĐ]/g, "d").toLowerCase().trim();
}

async function openRelatedTicket(requestId) {
  if (!authReady || !hasRole("resident") || !/^REQ-[A-Z0-9]{8}$/.test(requestId)) return;
  if (ownershipRequired()) { showOwnershipWarning(); return; }
  const generation = authGeneration;
  try {
    const item = await api(`/requests/${requestId}?resident_id=${encodeURIComponent(currentResidentId())}`);
    if (!isSessionCurrent(generation)) return;
    for (const suffix of ["type-filter", "status-filter", "date-from", "date-to"]) {
      document.getElementById(`resident-ticket-${suffix}`).value = suffix.endsWith("filter") ? "all" : "";
    }
    document.getElementById("resident-ticket-search").value = requestId;
    residentTicketPage.offset = 0;
    selectTicketStageForStatus("resident", item.status);
    openResidentTab("tickets");
    await loadResidentTickets();
    if (!isSessionCurrent(generation)) return;
    const ticket = [...document.querySelectorAll("#resident-ticket-list .request-card")].find(card => card.querySelector(".request-identity strong")?.textContent === requestId);
    if (ticket) {
      ticket.tabIndex = -1;
      ticket.focus({ preventScroll: true });
      ticket.scrollIntoView({ block: "center", behavior: "auto" });
    }
  } catch (error) {
    if (isSessionCurrent(generation)) showTransientNotice(error.message || "Không tìm thấy ticket trong tài khoản của bạn.");
  }
}

function matchesProcedure(searchable, status, query, filter) {
  return normalizeProcedureSearch(searchable).includes(normalizeProcedureSearch(query))
    && (filter === "all" || (filter === "completed" ? status === "completed" : status !== "completed"));
}

function setChatBackgroundInert(inert) {
  document.getElementById("main-content").inert = inert;
  const header = document.querySelector("body > header");
  if (header) header.inert = inert;
  if (!inert) document.body?.classList.remove("chat-expanded");
}

function resetChatPresentation() {
  if (typeof clearAutofillSession === "function") clearAutofillSession();
  if (typeof clearTicketCorrectionSession === "function") clearTicketCorrectionSession();
  if (typeof clearCorrectionChatSession === "function") clearCorrectionChatSession();
  if (typeof clearRequestDraftChat === "function") clearRequestDraftChat();
  clearSpeechSession();
  document.getElementById("chat-input").disabled = false;
  document.getElementById("file-input").disabled = false;
  const composer = document.getElementById("chat-form");
  composer.querySelector(".attach-button").classList.remove("disabled");
  const send = composer.querySelector('button[type="submit"]');
  send.disabled = false;
  send.textContent = "Gửi";
  setChatBackgroundInert(false);
  const widget = document.getElementById("chat-widget");
  ["open", "expanded", "choosing-procedure"].forEach((name) => widget.classList.remove(name));
  widget.setAttribute("aria-hidden", "true");
  widget.setAttribute("role", "region");
  widget.removeAttribute?.("aria-modal");
  document.getElementById("chat-launcher").setAttribute("aria-expanded", "false");
  document.getElementById("chat-expand").setAttribute("aria-pressed", "false");
  document.getElementById("chat-procedures").setAttribute("aria-expanded", "false");
  document.getElementById("procedure-picker").hidden = false;
  document.getElementById("messages").hidden = false;
  document.getElementById("chat-form").hidden = false;
  document.getElementById("procedure-list").replaceChildren();
  const entry = document.getElementById("resident-entry-dialog");
  if (entry.open) entry.close?.();
}

async function loadAuthConfig() {
  const response = await fetch(`${API}/auth/config`, { cache: "no-store", signal: authAbortController.signal });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail?.message || data.detail || "Không thể tải cấu hình đăng nhập.");
  authConfig = data;
  const resetButton = document.getElementById("login-reset");
  resetButton.hidden = true;
  return data;
}

function loginHintField(label, value) {
  const row = document.createElement("div");
  row.className = "login-hint-field";
  const caption = document.createElement("span");
  caption.textContent = label;
  const input = document.createElement("input");
  input.type = "text";
  input.readOnly = true;
  input.value = value;
  input.setAttribute("aria-label", label);
  input.setAttribute("autocomplete", "off");
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "login-hint-copy";
  copy.textContent = "Copy";
  copy.setAttribute("aria-label", `Sao chép ${label.toLowerCase()}`);
  copy.addEventListener("click", async () => {
    const status = document.getElementById("login-hints-status");
    try {
      await navigator.clipboard.writeText(value);
      copy.textContent = "Đã copy";
      status.textContent = `Đã sao chép ${label.toLowerCase()}.`;
    } catch {
      input.focus();
      input.select();
      status.textContent = "Đã chọn nội dung. Nhấn Ctrl+C để sao chép.";
    }
  });
  row.append(caption, input, copy);
  return row;
}

async function loadLoginHints() {
  const panel = document.getElementById("login-hints");
  const accounts = document.getElementById("login-hint-accounts");
  panel.hidden = true;
  accounts.replaceChildren();
  try {
    const response = await fetch(`${API}/auth/login-hints`, { cache: "no-store", signal: authAbortController.signal });
    if (!response.ok) return;
    const data = await response.json();
    if (!data.visible || !Array.isArray(data.accounts)) return;
    for (const account of data.accounts) {
      if (typeof account.username !== "string" || typeof account.password !== "string") continue;
      const card = document.createElement("div");
      card.className = "login-hint-account";
      const heading = document.createElement("div");
      heading.className = "login-hint-heading";
      const label = document.createElement("strong");
      label.textContent = account.label || "Tài khoản";
      const fill = document.createElement("button");
      fill.type = "button";
      fill.className = "login-hint-fill";
      fill.textContent = "Điền nhanh";
      fill.addEventListener("click", () => {
        document.getElementById("login-username").value = account.username;
        document.getElementById("login-password").value = account.password;
        document.getElementById("login-username").focus();
        presentAuthError("");
        document.getElementById("login-hints-status").textContent = "Đã điền tài khoản. Bấm Đăng nhập để tiếp tục.";
      });
      heading.append(label, fill);
      card.append(heading, loginHintField("Tài khoản", account.username), loginHintField("Mật khẩu", account.password));
      accounts.append(card);
    }
    panel.hidden = accounts.children.length === 0;
  } catch {
    panel.hidden = true;
  }
}

async function requestAuthContext() {
  return api("/me/context", { cache: "no-store", headers: authConfig?.auth_mode === "demo" ? { "X-Demo-Role": demoRole } : {} });
}

function setAuthenticatedUi() {
  if (typeof configureManagementAgent === "function") configureManagementAgent();
  const manager = hasRole("manager");
  const resident = hasRole("resident");
  document.getElementById("auth-screen").hidden = true;
  document.getElementById("main-content").hidden = false;
  document.getElementById("header-actions").hidden = false;
  document.getElementById("chat-launcher").hidden = !resident;
  document.getElementById("chat-widget").hidden = !resident;
  document.getElementById("open-chat-hero").hidden = !resident;
  document.getElementById("logout-button").hidden = false;
  document.getElementById("login-reset").hidden = !(manager && authContext?.reset_available === true);
  document.getElementById("application-footer").hidden = !(manager && authContext?.reset_available === true);
  document.getElementById("demo-role-wrap").hidden = authConfig?.auth_mode !== "demo";
  document.getElementById("demo-role").value = demoRole;
  document.getElementById("user-role").textContent = resident ? "Thông tin cá nhân" : manager ? authRoleLabels.manager : "Tài khoản";
  document.getElementById("hero-eyebrow").textContent = manager && !resident ? "KHÔNG GIAN LÀM VIỆC CỦA BAN QUẢN LÝ" : "CÙNG BẠN TỪ NGÀY ĐẦU TIÊN";
  document.getElementById("hero-heading-lead").textContent = manager && !resident ? "Mỗi yêu cầu," : "Chuyển vào căn hộ,";
  document.getElementById("hero-heading-accent").textContent = manager && !resident ? "một bước rõ ràng." : "từng bước một.";
  document.getElementById("hero-description").textContent = manager && !resident
    ? "Theo dõi hồ sơ, xem thông tin cư dân và xử lý yêu cầu trong khu được cấp quyền. Mọi quyết định đều có lịch sử để đối chiếu."
    : "Hồ sơ, thủ tục và những việc cần làm — gọn trong một nơi. Chọn việc bạn cần hoặc trò chuyện để được hướng dẫn.";
  const secondaryAction = document.getElementById("hero-secondary-action");
  secondaryAction.disabled = false;
  secondaryAction.setAttribute("aria-disabled", "false");
  secondaryAction.title = manager ? "Đối chiếu và cấp tài khoản cư dân" : "";
  secondaryAction.dataset.tabTarget = manager ? "resident-accounts" : "progress";
  document.getElementById("hero-secondary-label").textContent = manager ? "Tạo tài khoản cư dân" : "Xem việc cần làm";
  document.getElementById("hero-secondary-demo").hidden = true;
  document.getElementById("tab-management").hidden = !manager;
  document.querySelectorAll(".tabs button").forEach((button) => {
    if (button.id === "tab-resident-accounts") { button.hidden = !manager; button.disabled = !manager; button.setAttribute("aria-disabled", String(!manager)); }
    else if (button.id !== "tab-management") button.hidden = manager && !resident;
  });
  const residentId = currentResidentId();
  document.querySelectorAll("[data-resident-label]").forEach((element) => {
    element.textContent = residentId ? `HỒ SƠ CƯ DÂN ${residentId}` : "HỒ SƠ CƯ DÂN";
  });
  if (!manager && document.getElementById("management").classList.contains("active")) {
    document.querySelector('.tabs button[data-tab="agent"]')?.click();
  }
  if (manager && !resident) {
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      if (panel.id !== "management") panel.hidden = true;
    });
    document.getElementById("management").hidden = false;
    document.querySelectorAll(".tabs button").forEach((button) => {
      button.classList.toggle("active", button.dataset.tab === "management");
      button.setAttribute("aria-selected", String(button.dataset.tab === "management"));
    });
    document.getElementById("management").classList.add("active");
  }
}

function closeRequiredPasswordDialog() {
  const dialog = document.getElementById("first-password-dialog");
  const wasOpen = dialog.open;
  if (wasOpen) dialog.close();
  document.body?.classList.remove("password-required");
  setChatBackgroundInert(false);
  document.getElementById("first-password-form").hidden = true;
  return wasOpen;
}

function guardRequiredPasswordNavigation() {
  if (authContext?.password_change_required && window.location.hash) {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  }
}

async function finishAuthentication(context) {
  authContext = context;
  if (context.password_change_required) {
    authReady = false;
    authGeneration += 1;
    authAbortController.abort();
    authAbortController = new AbortController();
    if (notificationIntervalId) window.clearInterval(notificationIntervalId);
    notificationIntervalId = null;
    document.getElementById("auth-loading").hidden = true;
    document.getElementById("login-form").hidden = true;
    setAuthenticatedUi();
    guardRequiredPasswordNavigation();
    const dashboardTab = hasRole("manager") && !hasRole("resident") ? "management" : "agent";
    document.querySelectorAll(".tabs button").forEach(button => {
      button.classList.toggle("active", button.dataset.tab === dashboardTab);
      button.setAttribute("aria-selected", String(button.dataset.tab === dashboardTab));
      button.tabIndex = button.dataset.tab === dashboardTab ? 0 : -1;
    });
    document.querySelectorAll(".tab-panel").forEach(panel => {
      panel.hidden = panel.id !== dashboardTab;
      panel.classList.toggle("active", panel.id === dashboardTab);
    });
    const dialog = document.getElementById("first-password-dialog");
    document.querySelectorAll("dialog[open]").forEach(other => { if (other !== dialog) other.close(); });
    document.getElementById("chat-launcher").hidden = true;
    document.getElementById("chat-widget").hidden = true;
    setChatBackgroundInert(true);
    document.body?.classList.add("password-required");
    document.getElementById("first-password-form").hidden = false;
    if (!dialog.open) dialog.showModal();
    resetFirstPasswordForm();
    const username = document.getElementById("first-password-form").elements.username;
    username.value = context.username_change_required ? "" : context.username || "";
    username.readOnly = !context.username_change_required;
    document.getElementById("first-password-username-label").textContent = context.username_change_required ? "Tên đăng nhập mới" : "Tên đăng nhập";
    document.getElementById("first-password-username-help").textContent = context.username_change_required ? `Tài khoản được cấp: ${context.username}. Chọn tên mới cho riêng bạn; sau bước này không thể tự đổi tên lần nữa.` : "Tên đăng nhập đã được thiết lập. Lần này bạn chỉ cần đặt lại mật khẩu.";
    document.getElementById("first-password-title").textContent = context.username_change_required ? "Thiết lập tài khoản riêng" : "Đặt mật khẩu riêng";
    document.getElementById("first-password-form").elements.username.focus();
    return;
  }
  const passwordDialogWasOpen = closeRequiredPasswordDialog();
  document.getElementById("auth-title").textContent = "Đăng nhập";
  authReady = true;
  authGeneration += 1;
  residentOwnershipState = { ready: true, profile: hasRole("resident") ? await api(residentPath("")) : null };
  setAuthenticatedUi();
  synchronizeOwnershipUi();
  activateInitialTab();
  if (passwordDialogWasOpen) document.querySelector(".tabs button.active")?.focus();
  await startAuthorizedBackgroundTasks();
}

async function localPasswordLogin(username, password) {
  if (!authConfig?.enabled) throw new Error("Hệ thống đăng nhập chưa được cấu hình.");
  const response = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
    signal: authAbortController.signal,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data.detail?.message || "Tên đăng nhập hoặc mật khẩu không đúng.";
    throw new Error(message);
  }
  persistSession(data);
  return data;
}

async function completeRequiredAuthentication() {
  if (!authSession) return false;
  const expiresAt = Number(authSession.expires_at || 0);
  if (expiresAt && expiresAt <= Math.floor(Date.now() / 1000)) return false;
  try {
    const context = await requestAuthContext();
    await finishAuthentication(context);
    return true;
  } catch {
    clearStoredSession();
    authSession = null;
    return false;
  }
}

async function initializeAuth() {
  const loading = document.getElementById("auth-loading");
  const form = document.getElementById("login-form");
  loading.hidden = false;
  form.hidden = true;
  document.getElementById("first-password-form").hidden = true;
  document.getElementById("auth-title").textContent = "Đăng nhập";
  presentAuthError("");
  try {
    await loadAuthConfig();
    await loadLoginHints();
    if (authConfig.auth_mode === "demo") {
      try { demoRole = sessionStorage.getItem(AUTH_DEMO_ROLE_KEY) || "resident"; } catch { demoRole = "resident"; }
      if (!new Set(["resident", "manager"]).has(demoRole)) demoRole = "resident";
      const context = await requestAuthContext();
      await finishAuthentication(context);
      return;
    }
    if (authConfig.enabled === false || authConfig.auth_provider !== "local") {
      throw new Error("Đăng nhập chưa được cấu hình. Vui lòng liên hệ quản trị viên.");
    }
    authSession = readStoredSession();
    if (await completeRequiredAuthentication()) return;
    loading.hidden = true;
    form.hidden = false;
    document.getElementById("login-username").focus();
  } catch (error) {
    loading.hidden = true;
    form.hidden = true;
    presentAuthError(error.name === "AbortError" ? "" : (error.message || "Không thể kết nối hệ thống."), { retry: true });
    document.getElementById("auth-retry").hidden = false;
  }
}

async function handleLoginSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = document.getElementById("login-submit");
  button.disabled = true;
  button.textContent = "Đang đăng nhập…";
  presentAuthError("");
  try {
    await localPasswordLogin(form.elements.username.value.trim(), form.elements.password.value);
    const context = await requestAuthContext();
    form.reset();
    await finishAuthentication(context);
  } catch (error) {
    clearStoredSession();
    authSession = null;
    presentAuthError(error.message || "Không thể đăng nhập. Vui lòng thử lại.");
  } finally {
    button.disabled = false;
    button.textContent = "Đăng nhập";
  }
}

async function signOut() {
  resetChatPresentation();
  const token = authSession?.access_token;
  authReady = false;
  authGeneration += 1;
  authAbortController.abort();
  authAbortController = new AbortController();
  document.getElementById("main-content").hidden = true;
  document.getElementById("header-actions").hidden = true;
  document.getElementById("chat-launcher").hidden = true;
  document.getElementById("chat-widget").hidden = true;
  closeRequiredPasswordDialog();
  try {
    if (token) {
      const headers = authHeaders();
      await fetch(`${API}/auth/logout`, { method: "POST", headers, signal: authAbortController.signal });
    }
  } catch { /* Continue local sign-out even if the API is unavailable. */ }
  clearStoredSession();
  authSession = null;
  try { sessionStorage.removeItem(AUTH_DEMO_ROLE_KEY); } catch { /* Ignore unavailable storage. */ }
  window.location.assign(window.location.pathname);
}

async function changeDemoRole(role) {
  if (authConfig?.auth_mode !== "demo" || !new Set(["resident", "manager"]).has(role)) return;
  demoRole = role;
  try { sessionStorage.setItem(AUTH_DEMO_ROLE_KEY, demoRole); } catch { /* Keep role in memory. */ }
  authGeneration += 1;
  authAbortController.abort();
  authAbortController = new AbortController();
  authReady = false;
  try {
    const context = await requestAuthContext();
    await finishAuthentication(context);
  } catch (error) {
    presentAuthError(error.message || "Không thể đổi vai trò demo.");
  }
}

async function loadHealth() {
  try {
    const data = await api("/health");
    const runtime = document.querySelector(".runtime");
    omicxConfigured = ["omicx", "ptit"].includes(data.speech_to_text);
    geminiTtsConfigured = ["gemini", "ptit"].includes(data.text_to_speech);
    speechProvider = data.text_to_speech;
    updateAutoTtsToggle();
    if (!geminiTtsConfigured && !speechSupported) {
      document.querySelectorAll(".speak-button").forEach((button) => button.remove());
    }
    updateRecordingAvailability();
    if (recordingSupported && !omicxConfigured) micButton.title = "Nhập bằng giọng nói hiện chưa sẵn sàng";
    document.getElementById("health").textContent = data.status === "ready" ? "Trợ lý đang sẵn sàng" : "Trợ lý đang tạm ngưng";
    if (data.status === "ready") runtime.classList.add("ready");
  } catch {
    micButton.disabled = true;
    geminiTtsConfigured = false;
    updateAutoTtsToggle();
    document.getElementById("health").textContent = "Không thể kết nối trợ lý";
  }
}

async function startAuthorizedBackgroundTasks() {
  if (!authReady) return;
  await loadHealth();
  if (!authReady) return;
  if (hasRole("resident")) {
    if (ownershipRequired()) { void loadProgress(); return; }
    if (document.getElementById("agent").classList.contains("active") && !document.getElementById("chat-widget").classList.contains("open")) void openProcedurePicker();
    void loadHousehold(true);
    void pollManagementNotifications();
  }
  if (hasRole("resident") && !notificationIntervalId) notificationIntervalId = window.setInterval(pollManagementNotifications, 6000);
}

document.getElementById("login-form").addEventListener("submit", handleLoginSubmit);
document.getElementById("auth-retry").addEventListener("click", initializeAuth);
document.getElementById("logout-button").addEventListener("click", signOut);
document.getElementById("demo-role").addEventListener("change", (event) => changeDemoRole(event.target.value));
window.addEventListener("hashchange", guardRequiredPasswordNavigation);
window.addEventListener("popstate", guardRequiredPasswordNavigation);
window.addEventListener("pageshow", event => {
  if (event.persisted) {
    authReady = false;
    setChatBackgroundInert(true);
    window.location.reload();
  }
});

const chatWidget = document.getElementById("chat-widget");
const chatLauncher = document.getElementById("chat-launcher");
const chatExpand = document.getElementById("chat-expand");
const chatClose = document.getElementById("chat-close");
let chatReturnFocus = null;

function setChatWidgetExpanded(expanded) {
  chatWidget.classList.toggle("expanded", expanded);
  document.body.classList.toggle("chat-expanded", expanded);
  chatExpand.setAttribute("aria-pressed", String(expanded));
  const label = expanded ? "Thu nhỏ cửa sổ trợ lý" : "Mở rộng cửa sổ trợ lý";
  chatExpand.setAttribute("aria-label", label);
  chatExpand.title = label;
  setChatBackgroundInert(expanded);
  chatWidget.setAttribute("role", expanded ? "dialog" : "region");
  if (expanded) chatWidget.setAttribute("aria-modal", "true");
  else chatWidget.removeAttribute("aria-modal");
}

function setChatWidgetOpen(open) {
  if (open && (!authReady || !hasRole("resident"))) return;
  if (open && ownershipRequired()) { showOwnershipWarning(); return; }
  if (open && !chatWidget.classList.contains("open")) chatReturnFocus = document.activeElement;
  chatWidget.classList.toggle("open", open);
  chatWidget.setAttribute("aria-hidden", String(!open));
  chatLauncher.setAttribute("aria-expanded", String(open));
  if (open) {
    window.setTimeout(() => {
      if (!chatWidget.classList.contains("open")) return;
      document.getElementById("chat-input").focus();
    }, 80);
  } else {
    setProcedurePickerOpen(false, false);
    setChatWidgetExpanded(false);
    const target = chatReturnFocus?.getClientRects().length ? chatReturnFocus : chatLauncher;
    target.focus();
  }
}

let procedureRequestId = 0;
let procedureFilter = "all";

function setProcedurePickerOpen(open, focus = true) {
  document.getElementById("chat-procedures").setAttribute("aria-expanded", String(open));
  if (open && focus) document.getElementById("procedure-search").focus();
}

function resetProcedureFilters() {
  document.getElementById("procedure-search").value = "";
  procedureFilter = "all";
  document.querySelectorAll("[data-procedure-filter]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.procedureFilter === "all")));
}

function filterProcedureDirectory() {
  const list = document.getElementById("procedure-list");
  if (list.getAttribute("aria-busy") === "true") return;
  const rows = Array.from(list.querySelectorAll("[data-procedure-row]"));
  const query = document.getElementById("procedure-search").value;
  let visible = 0;
  rows.forEach((row) => {
    row.hidden = !matchesProcedure(row.dataset.search, row.dataset.status, query, procedureFilter);
    if (!row.hidden) visible += 1;
  });
  document.getElementById("procedure-status").textContent = `${visible} / ${rows.length} thủ tục trong checklist của bạn`;
  document.getElementById("procedure-empty").hidden = visible > 0;
}

async function openProcedurePicker() {
  if (ownershipRequired()) { showOwnershipWarning(); return; }
  if (!authReady || !hasRole("resident")) return;
  if (!document.getElementById("agent").classList.contains("active")) {
    document.getElementById("tab-agent").click();
    return;
  }
  setChatWidgetOpen(false);
  setProcedurePickerOpen(true);
  resetProcedureFilters();
  const requestId = ++procedureRequestId;
  const generation = authGeneration;
  const list = document.getElementById("procedure-list");
  const status = document.getElementById("procedure-status");
  list.replaceChildren();
  list.setAttribute("aria-busy", "true");
  status.textContent = "Đang tải thủ tục từ hồ sơ của bạn…";
  document.getElementById("procedure-empty").hidden = true;
  document.getElementById("procedure-retry").hidden = true;
  try {
    const data = await api(residentPath("/progress"));
    if (!isSessionCurrent(generation) || requestId !== procedureRequestId) return;
    synchronizeProcedureCards(data);
    renderA2UI(data.a2ui, list);
    list.setAttribute("aria-busy", "false");
    filterProcedureDirectory();
    updateChatActionAvailability();
  } catch (error) {
    if (!isSessionCurrent(generation) || requestId !== procedureRequestId) return;
    status.textContent = error.message || "Không thể tải danh sách. Bạn có thể thử lại.";
    document.getElementById("procedure-retry").hidden = false;
  } finally {
    if (requestId === procedureRequestId) list.setAttribute("aria-busy", "false");
  }
}

function openResidentTab(tab) {
  if (!authReady || !hasRole("resident") || !["agent", "progress", "tickets", "regulations", "household", "notifications"].includes(tab)) return;
  if (ownershipRequired() && !["agent", "progress"].includes(tab)) { showOwnershipWarning(); return; }
  const button = document.querySelector(`.tabs button[data-tab="${tab}"]`);
  if (!button || button.disabled || button.hidden) return;
  setChatWidgetOpen(false);
  button.click();
  button.focus();
  button.scrollIntoView({ behavior: "auto", block: "nearest" });
}

function focusChatDraft(message) {
  if (!authReady || !hasRole("resident")) return;
  if (ownershipRequired()) { showOwnershipWarning(); return; }
  const input = document.getElementById("chat-input");
  if (input.disabled) return;
  setProcedurePickerOpen(false, false);
  setChatWidgetOpen(true);
  input.value = message.slice(0, 2000);
  resizeChatInput();
  input.focus();
  document.getElementById("speech-status").textContent = "Nội dung mới chỉ được điền vào ô nhập. Kiểm tra và bấm Gửi khi bạn sẵn sàng.";
}

function updateChatActionAvailability() {
  const busy = document.getElementById("chat-input").disabled;
  document.querySelectorAll('[data-chat-action="send_message"], [data-chat-action="focus_input"], [data-chat-prompt], [data-ui-action="focus_input"]').forEach((button) => { button.disabled = button.dataset.fixedDisabled === "true" || busy; });
}

function renderAnswerFallback(host, question = "", tools = []) {
  const footer = [
    ["Yêu cầu khác", "open_form", { form: "general_support" }],
    ["Xem việc cần làm", "open_tab", { tab: "progress" }],
  ];
  const actions = [];
  const related = tools.find(item => item.tool === "create_management_request" && /^(?:created|existing):REQ-[A-Z0-9]{8}$/.test(item.summary || ""));
  if (related) {
    const requestId = related.summary.split(":")[1];
    actions.unshift([`Xem ${requestId}`, "open_tab", { tab: "tickets", request_id: requestId }]);
  }
  if (question) actions.unshift(["Soạn lại câu hỏi", "focus_input", { message: question }]);
  const rows = [actions, footer].filter(items => items.length).map(items => {
    const row = document.createElement("div");
    row.className = "a2ui-row a2ui-actions";
    items.forEach(([label, name, context]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "a2ui-button a2ui-secondary";
      button.textContent = label;
      button.dataset.chatAction = name;
      button.addEventListener("click", () => handleUIAction(name, context));
      row.appendChild(button);
    });
    return row;
  });
  host.replaceChildren(...rows);
}

document.getElementById("procedure-search").addEventListener("input", filterProcedureDirectory);
document.getElementById("procedure-retry").addEventListener("click", openProcedurePicker);
document.getElementById("procedure-clear").addEventListener("click", () => {
  resetProcedureFilters();
  filterProcedureDirectory();
  document.getElementById("procedure-search").focus();
});
document.querySelectorAll("[data-procedure-filter]").forEach((button) => button.addEventListener("click", () => {
  procedureFilter = button.dataset.procedureFilter;
  document.querySelectorAll("[data-procedure-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  filterProcedureDirectory();
}));

chatLauncher.addEventListener("click", () => {
  setChatWidgetOpen(!chatWidget.classList.contains("open"));
});
chatExpand.addEventListener("click", () => {
  setChatWidgetExpanded(!chatWidget.classList.contains("expanded"));
});
chatClose.addEventListener("click", () => setChatWidgetOpen(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && chatWidget.classList.contains("open")) {
    event.preventDefault();
    if (chatWidget.classList.contains("expanded")) setChatWidgetExpanded(false);
    else setChatWidgetOpen(false);
  }
  if (event.key === "Tab" && chatWidget.classList.contains("open") && chatWidget.classList.contains("expanded")) {
    const controls = Array.from(chatWidget.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]')).filter((node) => node.getClientRects().length);
    const first = controls[0];
    const last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
});
const initialChatRequested = window.location.hash === "#chat";

document.querySelectorAll(".tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    if (!authReady || button.disabled || button.hidden) return;
    document.querySelectorAll(".tabs button").forEach((item) => {
      item.classList.remove("active");
      item.setAttribute("aria-selected", "false");
      item.tabIndex = -1;
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.remove("active");
      panel.hidden = true;
    });
    button.classList.add("active");
    button.setAttribute("aria-selected", "true");
    button.tabIndex = 0;
    const panel = document.getElementById(button.dataset.tab);
    panel.hidden = false;
    panel.classList.add("active");
    if (button.dataset.tab === "agent") void openProcedurePicker();
    if (button.dataset.tab === "notifications") void loadNotificationFeed();
    if (button.dataset.tab === "progress") loadProgress();
    if (button.dataset.tab === "tickets") loadResidentTickets();
    if (button.dataset.tab === "household") loadHousehold();
    if (button.dataset.tab === "regulations") loadRegulations();
    if (button.dataset.tab === "management") loadRequests();
    if (button.dataset.tab === "resident-accounts" && typeof loadAccountWorkspace === "function") void loadAccountWorkspace();
  });
});
document.querySelector(".tabs").addEventListener("keydown", (event) => {
  if (!authReady) return;
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  const tabs = Array.from(document.querySelectorAll(".tabs button")).filter((button) => !button.hidden && !button.disabled);
  const currentIndex = tabs.indexOf(document.activeElement);
  if (currentIndex < 0) return;
  event.preventDefault();
  let nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : currentIndex;
  if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
  tabs[nextIndex].focus();
  tabs[nextIndex].click();
});
const initialTab = window.location.hash.slice(1);
function activateInitialTab() {
  if (!authReady) return;
  const requested = ownershipRequired() ? "progress" : hasRole("manager") && !hasRole("resident")
    ? "management"
    : ["progress", "tickets", "household", "regulations", "management"].includes(initialTab)
    ? initialTab
    : initialChatRequested ? "agent" : "agent";
  const button = document.querySelector(`.tabs button[data-tab="${requested}"]`);
  if (button && !button.hidden && (requested !== "management" || hasRole("manager"))) {
    button.click();
    window.requestAnimationFrame(() => window.scrollTo({ top: 0, left: 0 }));
  } else {
    document.querySelector('.tabs button[data-tab="agent"]')?.click();
  }
  if (initialChatRequested && hasRole("resident")) setChatWidgetOpen(true);
}

function humanizeToolSummary(summary) {
  summary = typeof summary === "string" ? summary : "";
  if (summary === "error") return "Thao tác không thành công";
  if (summary.startsWith("not_created:")) return ({
    missing_fields: "Chưa tạo yêu cầu vì còn thiếu thông tin",
    invalid_fields: "Chưa tạo yêu cầu vì thông tin chưa hợp lệ",
    confirmation_required: "Đang chờ bạn xác nhận",
    unconfirmed: "Chưa có xác nhận tạo yêu cầu thành công",
  })[summary.slice(12)] || "Chưa tạo yêu cầu";
  if (summary === "checked") return "Đã kiểm tra thông tin";
  if (summary.startsWith("created:")) return `Mã yêu cầu: ${summary.slice(8)}`;
  if (summary.startsWith("existing:")) return `Yêu cầu đang có: ${summary.slice(9)}`;
  if (summary.startsWith("requests:")) return `Tổng cộng: ${summary.slice(9)} yêu cầu`;
  if (summary.startsWith("retrieval:")) return ({
    supported: "Đã tìm thấy nguồn liên quan",
    application_guidance: "Hướng dẫn từ chức năng đang chạy",
    no_valid_source: "Cần xác nhận nguồn · có thể gửi yêu cầu BQL",
    insufficient_site_evidence: "Cần BQL xác nhận quy định riêng của khu",
    insufficient_evidence: "Nguồn chưa đủ xác nhận chi tiết được hỏi",
  })[summary.slice(10)] || "Đã kiểm tra dữ liệu tham khảo";
  if (summary.startsWith("checklist:")) {
    const count = summary.match(/\d+/)?.[0];
    return count ? `${count} việc cần làm` : "Đã có danh sách việc cần làm";
  }
  if (summary.startsWith("progress:")) return `Tiến độ ${summary.slice(9)}`;
  if (summary.startsWith("status:")) {
    const status = summary.slice(7);
    return requestStatusLabels[status] || "Đã kiểm tra trạng thái";
  }
  return "Đã nhận phản hồi từ hệ thống";
}

function officialSourceLink(url, label) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || !parsed.hostname.endsWith(".chinhphu.vn")) return "";
    return '<a href="' + escapeHtml(parsed.href) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(label) + '</a>';
  } catch {
    return "";
  }
}

function renderCitation(item) {
  const label = ({ public_law: "Pháp luật tham khảo", demo_bql: "BQL minh họa · demo", building_policy: "Tài liệu riêng của khu" })[item.data_partition] || "Nguồn";
  return '<div class="citation">' + escapeHtml(label) + ': ' + escapeHtml(item.title) + ' · ' + escapeHtml(item.version) +
    (item.section ? ' · ' + escapeHtml(item.section) : '') + ' ' + officialSourceLink(item.source_url, "Xem nguồn gốc") + '</div>';
}

function toolTraceSymbol(summary) {
  if (summary === "error") return "!";
  return /^(?:not_created:|retrieval:(?:no_valid_source|insufficient_site_evidence|insufficient_evidence)$)/.test(summary) ? "ℹ" : "✓";
}

function renderToolTraces(tools = []) {
  const mutations = {
    create_management_request: { success: /^created:REQ-[a-z0-9]+$/i, pending: "Chưa gửi yêu cầu đến Ban quản lý" },
    update_management_request: { success: /^status:(submitted|in_review|needs_info)$/, pending: "Chưa bổ sung vào ticket" },
    cancel_management_request: { success: /^status:cancelled_by_resident$/, pending: "Chưa hủy ticket" },
    update_task_status: { success: /^status:(completed|done|in_progress|pending)$/, pending: "Chưa cập nhật tiến độ" },
    build_checklist: { success: /^checklist:\d+_items$/, pending: "Chưa lập danh sách việc cần làm" },
  };
  if (!tools.length) return "";
  const traces = tools.map((item, index) => {
    const summary = typeof item.summary === "string" ? item.summary : "";
    const mutation = mutations[item.tool];
    let label = toolLabels[item.tool] || "Đã kiểm tra yêu cầu";
    let symbol = toolTraceSymbol(summary);
    let outcome = symbol === "!" ? "error" : symbol === "ℹ" ? "info" : "success";
    let detail = humanizeToolSummary(summary);
    if (mutation && !mutation.success.test(summary)) {
      label = item.tool === "create_management_request" && summary.startsWith("existing:")
        ? "Yêu cầu đã tồn tại, không gửi mới" : mutation.pending;
      if (outcome !== "error") { outcome = "info"; symbol = "ℹ"; }
    } else if (outcome === "error") label = "Chưa thể hoàn thành thao tác";
    const priorAttempt = mutation && /^(?:not_created:|error$)/.test(summary) && tools.slice(index + 1).some(later =>
      later.tool === item.tool && (mutation.success.test(later.summary || "")
        || (item.tool === "create_management_request" && /^existing:REQ-[a-z0-9]+$/i.test(later.summary || "")))
    );
    if (priorAttempt) {
      label = `Bước ${index + 1} · Kiểm tra trước đó`;
      detail = ({
        "not_created:missing_fields": "Thông tin còn thiếu ở bước này",
        "not_created:invalid_fields": "Thông tin chưa hợp lệ ở bước này",
        "not_created:confirmation_required": "Chưa có xác nhận ở bước này",
        "not_created:unconfirmed": "Chưa được hệ thống xác nhận ở bước này",
        error: "Lần thử này không thành công",
      })[summary] || detail;
    }
    return `<span data-outcome="${outcome}"${priorAttempt ? ' data-phase="prior-attempt"' : ""}>${symbol} ${escapeHtml(label)} · ${escapeHtml(detail)}</span>`;
  }).join("");
  return `<div class="trace" aria-label="Kết quả thao tác của trợ lý">${traces}</div>`;
}

function addMessage(role, text, tools = [], citations = [], attachments = []) {
  const messages = document.getElementById("messages");
  const article = document.createElement("article");
  article.className = `message ${role}`;
  if (role === "user") {
    const attachmentMarkup = attachments.length ? `<div class="sent-attachments">${attachments.map((item) => {
      if (item.previewUrl) {
        return `<figure><img src="${escapeHtml(item.previewUrl)}" alt="Ảnh đính kèm ${escapeHtml(item.file.name)}"><figcaption>${escapeHtml(item.file.name)}</figcaption></figure>`;
      }
      return `<span class="sent-file">📄 ${escapeHtml(item.file.name)}</span>`;
    }).join("")}</div>` : "";
    const messageMarkup = text ? `<div>${escapeHtml(text)}</div>` : "";
    article.innerHTML = `<div>${attachmentMarkup}${messageMarkup}</div>`;
  } else {
    const traces = renderToolTraces(tools);
    const sources = citations.map(renderCitation).join("");
    const speechAction = speechActionMarkup();
    article.innerHTML = `<div class="avatar" aria-hidden="true"><img src="/static/agent-mascot.svg?v=20261010-motion-v3" width="32" height="32" alt="" /></div><div><strong>MoveInMate</strong><div class="markdown-body">${renderMarkdown(text)}</div>${traces}${sources}${speechAction}</div>`;
  }
  messages.appendChild(article);
  if (role === "assistant") {
    const actionHost = document.createElement("div");
    actionHost.className = "a2ui-surface";
    actionHost.setAttribute("aria-label", "Thao tác tiếp theo");
    article.lastElementChild.appendChild(actionHost);
    renderAnswerFallback(actionHost, "", tools);
    const markdownBody = article.querySelector(".markdown-body");
    void revealMarkdownLines(markdownBody, messages);
  }
  if (role === "user") {
    article.querySelectorAll(".sent-attachments img").forEach((image) => {
      image.addEventListener("load", () => URL.revokeObjectURL(image.src), { once: true });
    });
  }
  if (role === "user") messages.scrollTop = messages.scrollHeight;
}

function createLiveAssistantMessage(article) {
  const generation = authGeneration;
  const messages = document.getElementById("messages");
  article.stopWaiting?.();
  article.className = "message assistant streaming-message";
  article.setAttribute("aria-busy", "true");
  const waitingStep = article.querySelector(".typing-step");
  if (waitingStep) waitingStep.textContent = "Đang soạn câu trả lời";
  article.querySelector(".typing-indicator")?.setAttribute("aria-label", "MoveInMate đang soạn câu trả lời");
  let streamedText = "";
  let finished = false;
  let streamButton = null;
  const streamBody = document.createElement("div");
  streamBody.className = "markdown-body streaming";
  article.innerHTML = '<div class="avatar" aria-hidden="true"><img src="/static/agent-mascot.svg?v=20261014-living-v1" width="32" height="32" alt="" /></div><div><strong>MoveInMate</strong></div>';
  article.lastElementChild.appendChild(streamBody);
  article.lastElementChild.insertAdjacentHTML("beforeend", speechActionMarkup());
  streamButton = article.querySelector(".speak-button");
  let speechController = null;

  return {
    append(text) {
      if (finished || !text) return;
      streamedText += text;
      streamBody.textContent = streamedText;
      messages.scrollTop = messages.scrollHeight;
    },
    speech(segment) {
      if (!finished && autoTtsEnabled && streamButton && isSessionCurrent(generation)) {
        if (!speechController) speechController = createStreamedSpeech(streamButton);
        speechController.append(segment);
      }
    },
    cancelSpeech() {
      if (speechController) speechController.cancel();
    },
    async finish(result, { automaticSpeech = true } = {}) {
      if (finished || !isSessionCurrent(generation)) return;
      finished = true;
      article.dataset.agentError = String(Boolean(result.retryQuestion));
      const answer = result.answer || streamedText;
      const changed = Boolean(streamedText && answer !== streamedText);
      if (speechController) {
        if (!automaticSpeech || changed) {
          speechController.cancel();
          if (changed) document.getElementById("speech-status").textContent = "Nội dung đã được cập nhật. Bấm Nghe để đọc bản cuối.";
        } else speechController.finish();
      }
      const markdownBody = article.querySelector(".markdown-body");
      markdownBody.innerHTML = renderMarkdown(answer);
      markdownBody.classList.remove("streaming");

      const tools = result.tools_used || [];
      const citations = result.citations || [];
      const traces = renderToolTraces(tools);
      const sources = citations.map(renderCitation).join("");
      article.lastElementChild.insertAdjacentHTML("beforeend", `${traces}${sources}`);
      const a2uiHost = document.createElement("div");
      a2uiHost.className = "a2ui-surface";
      a2uiHost.setAttribute("aria-label", "Thao tác tiếp theo");
      article.lastElementChild.appendChild(a2uiHost);
      if (result.a2ui) renderA2UI(result.a2ui, a2uiHost);
      else renderAnswerFallback(a2uiHost, result.retryQuestion, tools);
      updateChatActionAvailability();
      if (automaticSpeech && !speechController && isSessionCurrent(generation)) autoSpeakMessage(article);
      if (!streamedText) await revealMarkdownLines(markdownBody, messages);
      article.classList.remove("streaming-message");
      article.setAttribute("aria-busy", "false");
      messages.scrollTo({ top: messages.scrollHeight, behavior: "smooth" });
    },
  };
}

const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

function getAnswerLineElements(markdownBody) {
  const lines = [];
  Array.from(markdownBody.children).forEach((block) => {
    if (block.matches("ul, ol")) {
      lines.push(...Array.from(block.children));
      return;
    }
    if (block.matches("p") && block.querySelector(":scope > .answer-text-line")) {
      lines.push(...Array.from(block.children).filter((child) => child.classList.contains("answer-text-line")));
      return;
    }
    lines.push(block);
  });
  return lines;
}

async function revealMarkdownLines(markdownBody, messages) {
  if (!markdownBody) return;
  const lines = getAnswerLineElements(markdownBody);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion || !lines.length) {
    messages.scrollTop = messages.scrollHeight;
    return;
  }

  let followAnswer = true;
  const stopFollowing = () => { followAnswer = false; };
  messages.addEventListener("wheel", stopFollowing, { passive: true });
  messages.addEventListener("touchstart", stopFollowing, { passive: true });
  lines.forEach((line) => {
    line.hidden = true;
    line.classList.add("answer-reveal-line");
  });

  const lineDelay = Math.max(55, Math.min(125, 1200 / lines.length));
  for (const line of lines) {
    line.hidden = false;
    line.classList.add("visible");
    if (followAnswer) messages.scrollTo({ top: messages.scrollHeight, behavior: "smooth" });
    await wait(lineDelay);
  }

  messages.removeEventListener("wheel", stopFollowing);
  messages.removeEventListener("touchstart", stopFollowing);
}

function resetSpeechButton() {
  if (typeof streamedSpeech !== "undefined" && streamedSpeech) {
    if (streamedSpeech.timer) window.clearTimeout(streamedSpeech.timer);
    streamedSpeech.cancelled = true;
    streamedSpeech = null;
  }
  if (typeof speechWaitTimer !== "undefined" && speechWaitTimer) { window.clearTimeout(speechWaitTimer); speechWaitTimer = null; }
  speechPlaybackId += 1;
  if (speechSupported) window.speechSynthesis.cancel();
  const button = activeSpeechButton;
  speechAudioCache.get(button)?.chunks.forEach((entry, text, chunks) => {
    if (!entry.blob) {
      entry.controller?.abort();
      chunks.delete(text);
    }
  });
  if (activeAudioSource) {
    activeAudioSource.onended = null;
    activeAudioSource.stop();
    activeAudioSource.disconnect();
    activeAudioSource = null;
  }
  if (activeAudio) {
    activeAudio.onended = null;
    activeAudio.onerror = null;
    activeAudio.pause();
    activeAudio.currentTime = 0;
    activeAudio = null;
  }
  if (activeAudioUrl) {
    URL.revokeObjectURL(activeAudioUrl);
    activeAudioUrl = "";
  }
  if (button) {
    setSpeechButtonAppearance(button, "idle");
    button.classList.remove("speaking");
  }
  activeSpeechButton = null;
}

// Resume during a click/submit so delayed network audio can play on mobile too.
function unlockSpeechPlayback() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return;
  try {
    if (!speechAudioContext || speechAudioContext.state === "closed") speechAudioContext = new AudioContextClass();
    if (speechAudioContext.state === "suspended") speechAudioContext.resume().catch(() => {});
  } catch {}
}

function clearSpeechSession() {
  resetSpeechButton();
  speechAudioCache.forEach(answer => answer.chunks.forEach(entry => entry.controller?.abort()));
  speechAudioCache.clear();
  if (speechAudioContext) {
    speechAudioContext.close().catch(() => {});
    speechAudioContext = null;
  }
}

function autoSpeakMessage(message) {
  if (!authReady || !autoTtsEnabled || !geminiTtsConfigured) return;
  const button = message.querySelector(".speak-button");
  const text = button && speechTextForButton(button);
  if (text) void speakResponse(text, button, { automatic: true });
}

function speechTextForButton(button) {
  return button.closest(".message")?.querySelector(".markdown-body")?.innerText.trim() || "";
}

async function requestGeminiSpeech(text, signal) {
  const response = await authorizedFetch(`${API}/speech/synthesize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
    signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = data.detail;
    throw new Error(detail?.message || "Chưa thể tạo giọng đọc.");
  }
  return response.blob();
}

function prepareGeminiSpeech(button, text) {
  let answer = speechAudioCache.get(button);
  if (!answer) {
    answer = { chunks: new Map() };
    speechAudioCache.set(button, answer);
    if (speechAudioCache.size > 12) {
      const oldest = speechAudioCache.keys().next().value;
      speechAudioCache.get(oldest).chunks.forEach(entry => entry.controller?.abort());
      speechAudioCache.delete(oldest);
    }
  }
  const cached = answer.chunks.get(text);
  if (cached?.blob) return Promise.resolve(cached.blob);
  if (cached?.promise) return cached.promise;

  const entry = { blob: null, promise: null, controller: new AbortController() };
  entry.promise = requestGeminiSpeech(text, entry.controller.signal)
    .then((blob) => {
      if (entry.controller.signal.aborted) throw new DOMException("Đã dừng giọng đọc.", "AbortError");
      entry.blob = blob;
      button.title = "Giọng đọc đã sẵn sàng";
      return blob;
    })
    .catch(error => {
      if (answer.chunks.get(text) === entry) answer.chunks.delete(text);
      throw error;
    }).finally(() => {
      entry.promise = null;
      entry.controller = null;
    });
  answer.chunks.set(text, entry);
  return entry.promise;
}

const autoTtsToggle = document.getElementById("auto-tts-toggle");

function updateAutoTtsToggle() {
  const available = geminiTtsConfigured;
  autoTtsToggle.disabled = !available;
  autoTtsToggle.classList.toggle("enabled", available && autoTtsEnabled);
  autoTtsToggle.setAttribute("aria-pressed", String(available && autoTtsEnabled));
  autoTtsToggle.textContent = available
    ? `Giọng đọc tự động: ${autoTtsEnabled ? "Bật" : "Tắt"}`
    : "Giọng đọc hiện chưa sẵn sàng";
}

autoTtsToggle.addEventListener("click", () => {
  if (!geminiTtsConfigured) return;
  autoTtsEnabled = !autoTtsEnabled;
  saveAutoTtsPreference();
  updateAutoTtsToggle();
  if (autoTtsEnabled) {
    unlockSpeechPlayback();
    document.getElementById("speech-status").textContent = "Sẽ tự đọc câu trả lời tiếp theo của trợ lý.";
    return;
  }
  resetSpeechButton();
  document.getElementById("speech-status").textContent = "Giọng đọc tự động đã tắt. Bấm Nghe để đọc từng câu trả lời.";
  speechAudioCache.forEach(answer => answer.chunks.forEach((entry, text, chunks) => {
    if (!entry.blob) { entry.controller?.abort(); chunks.delete(text); }
  }));
});

async function loadSpeechVoices() {
  const current = window.speechSynthesis.getVoices();
  if (current.length) return current;
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve(window.speechSynthesis.getVoices());
    };
    window.speechSynthesis.addEventListener("voiceschanged", finish, { once: true });
    window.setTimeout(finish, 1200);
  });
}

function inferSpeechLanguage(text) {
  if (/[ăâđêôơưĂÂĐÊÔƠƯ]|[àáạảãèéẹẻẽìíịỉĩòóọỏõùúụủũỳýỵỷỹ]/i.test(text)) return "vi-VN";
  if (/[\u3040-\u30ff]/.test(text)) return "ja-JP";
  if (/[\uac00-\ud7af]/.test(text)) return "ko-KR";
  if (/[\u0400-\u04ff]/.test(text)) return "ru-RU";
  if (/[\u0600-\u06ff]/.test(text)) return "ar-SA";
  if (/[\u4e00-\u9fff]/.test(text)) return "zh-CN";
  return "en-US";
}

function chooseSpeechVoice(voices, languageCode) {
  const normalizedLanguage = languageCode.toLowerCase();
  const languagePrefix = normalizedLanguage.split("-")[0];
  return voices
    .filter((voice) => voice.lang.toLowerCase().replace("_", "-").startsWith(languagePrefix))
    .sort((left, right) => {
      const score = (voice) => (voice.localService ? 4 : 0)
        + (voice.lang.toLowerCase().replace("_", "-") === normalizedLanguage ? 2 : 0)
        + (/vietnam|tiếng việt|hoaimy|namminh|microsoft an/i.test(voice.name) && languagePrefix === "vi" ? 1 : 0);
      return score(right) - score(left);
    })[0] || null;
}

function splitSpeechText(text, maxLength = 190) {
  const sentences = text.match(/[^.!?\n]+[.!?]?/g) || [text];
  const chunks = [];
  let current = "";
  sentences.forEach((sentence) => {
    const candidate = `${current} ${sentence.trim()}`.trim();
    if (candidate.length <= maxLength) {
      current = candidate;
      return;
    }
    if (current) chunks.push(current);
    current = sentence.trim();
  });
  if (current) chunks.push(current);
  return chunks;
}

async function speakResponseLocally(text, button, current) {
  text = text.replace(/(?<![\p{L}\p{N}_])BQL(?![\p{L}\p{N}_])/giu, "ban quản lí");
  setSpeechButtonAppearance(button, "loading");
  button.classList.add("speaking");

  const voices = await loadSpeechVoices();
  if (!current()) return;
  const languageCode = inferSpeechLanguage(text);
  const selectedVoice = chooseSpeechVoice(voices, languageCode);
  const status = document.getElementById("speech-status");
  if (!selectedVoice && status) {
    status.textContent = "Thiết bị chưa có giọng phù hợp; đang dùng giọng mặc định.";
  }

  const chunks = splitSpeechText(text);
  let index = 0;
  setSpeechButtonAppearance(button, "playing");

  const speakNext = () => {
    if (!current()) return;
    if (index >= chunks.length) {
      resetSpeechButton();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(chunks[index]);
    index += 1;
    utterance.lang = languageCode;
    utterance.rate = 0.92;
    utterance.pitch = 1;
    if (selectedVoice) utterance.voice = selectedVoice;
    utterance.onend = speakNext;
    utterance.onerror = () => { if (current()) resetSpeechButton(); };
    window.speechSynthesis.speak(utterance);
  };
  speakNext();
}

function remoteSpeechChunks(text) {
  let remaining = text.trim().slice(0, 5000);
  const chunks = [];
  while (remaining) {
    const limit = chunks.length ? 700 : 120;
    let boundary = Math.min(limit, remaining.length);
    if (remaining.length > limit) {
      const prefix = remaining.slice(0, limit);
      const sentence = Math.max(prefix.lastIndexOf(". "), prefix.lastIndexOf("? "), prefix.lastIndexOf("! "), prefix.lastIndexOf("\n"));
      boundary = sentence >= limit / 3 ? sentence + 1 : prefix.lastIndexOf(" ");
      if (boundary < 1) boundary = limit;
    }
    chunks.push(remaining.slice(0, boundary).trim());
    remaining = remaining.slice(boundary).trim();
  }
  return chunks;
}

async function speakResponseWithGemini(text, button, current, chunks = remoteSpeechChunks(text), index = 0) {
  const waitTimer = window.setTimeout?.(() => {
    if (current()) document.getElementById("speech-status").textContent = "PTIT đang xử lý giọng đọc. Bạn có thể bấm Dừng.";
  }, 8000);
  let audioBlob;
  try { audioBlob = await prepareGeminiSpeech(button, chunks[index]); }
  finally { if (waitTimer) window.clearTimeout?.(waitTimer); }
  if (!current()) return;
  const status = document.getElementById("speech-status");
  const ended = () => {
    if (!current()) return;
    if (activeAudioSource) { activeAudioSource.disconnect(); activeAudioSource = null; }
    activeAudio = null;
    if (activeAudioUrl) { URL.revokeObjectURL(activeAudioUrl); activeAudioUrl = ""; }
    if (index + 1 < chunks.length) {
      setSpeechButtonAppearance(button, "loading");
      void speakResponseWithGemini(text, button, current, chunks, index + 1).catch(() => {
        if (!current()) return;
        resetSpeechButton();
        if (status) status.textContent = "Giọng đọc bị gián đoạn. Nội dung vẫn có trên màn hình; bấm Nghe để thử lại.";
      });
      return;
    }
    if (status) status.textContent = "";
    resetSpeechButton();
  };
  const context = speechAudioContext;
  if (context) {
    const buffer = await context.decodeAudioData(await audioBlob.arrayBuffer());
    if (!current()) return;
    if (context.state !== "running") throw new DOMException("Bấm Nghe để cho phép trình duyệt phát giọng đọc.", "NotAllowedError");
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    source.onended = ended;
    activeAudioSource = source;
    source.start();
  } else {
    activeAudioUrl = URL.createObjectURL(audioBlob);
    activeAudio = new Audio(activeAudioUrl);
    activeAudio.onended = ended;
    activeAudio.onerror = () => {
      if (!current()) return;
      if (status) status.textContent = "Không thể phát dữ liệu giọng đọc.";
      resetSpeechButton();
    };
    await activeAudio.play();
  }
  if (!current()) return;
  setSpeechButtonAppearance(button, "playing");
  if (status) status.textContent = "Đang đọc câu trả lời.";
  if (index + 1 < chunks.length) void prepareGeminiSpeech(button, chunks[index + 1]).catch(() => {});
}

async function speakResponse(text, button, { automatic = false } = {}) {
  text = text.trim().slice(0, 5000);
  if (!text) return;
  resetSpeechButton();
  const playbackId = speechPlaybackId;
  const current = () => playbackId === speechPlaybackId && activeSpeechButton === button && authReady && (!automatic || autoTtsEnabled);
  if (automatic && !autoTtsEnabled) return;
  activeSpeechButton = button;
  setSpeechButtonAppearance(button, "loading");
  button.classList.add("speaking");

  if (geminiTtsConfigured) {
    try {
      await speakResponseWithGemini(text, button, current);
      return;
    } catch (error) {
      if (!current()) return;
      const status = document.getElementById("speech-status");
      if (error.name === "NotAllowedError" || error.name === "AbortError" || !speechSupported) {
        resetSpeechButton();
        if (status) status.textContent = error.name === "NotAllowedError" ? "Trình duyệt đang chặn tự phát. Bấm Nghe để cho phép giọng đọc." : "Chưa thể phát giọng đọc. Bấm Nghe để thử lại.";
        return;
      }
      resetSpeechButton();
      if (status) status.textContent = `${error.message} Nội dung vẫn có trên màn hình; bấm Nghe để thử lại.`;
      return;
    }
  }

  if (current()) {
    resetSpeechButton();
    document.getElementById("speech-status").textContent = "Giọng PTIT hiện chưa sẵn sàng. Bạn vẫn có thể đọc nội dung trên màn hình.";
  }
}

function createStreamedSpeech(button) {
  resetSpeechButton();
  const playbackId = speechPlaybackId;
  const state = { cancelled: false, finished: false, playing: false, seen: new Set(), queue: [], timer: null };
  streamedSpeech = state;
  activeSpeechButton = button;
  const current = () => !state.cancelled && playbackId === speechPlaybackId && authReady && autoTtsEnabled && activeSpeechButton === button;
  const status = document.getElementById("speech-status");

  async function pump() {
    if (!current() || state.playing) return;
    const entry = state.queue.shift();
    if (!entry) {
      if (state.finished) { resetSpeechButton(); status.textContent = ""; }
      return;
    }
    state.playing = true;
    setSpeechButtonAppearance(button, "loading");
    const timer = window.setTimeout(() => { if (current()) status.textContent = "PTIT đang xử lý giọng đọc. Bạn có thể bấm Dừng."; }, 8000);
    state.timer = timer;
    try {
      const blob = await prepareGeminiSpeech(button, entry.text);
      window.clearTimeout(timer);
      if (!current()) return;
      const ended = () => {
        if (!current()) return;
        activeAudioSource?.disconnect();
        activeAudioSource = null;
        activeAudio = null;
        if (activeAudioUrl) { URL.revokeObjectURL(activeAudioUrl); activeAudioUrl = ""; }
        state.playing = false;
        void pump();
      };
      if (speechAudioContext) {
        const buffer = await speechAudioContext.decodeAudioData(await blob.arrayBuffer());
        if (!current()) return;
        if (speechAudioContext.state !== "running") throw new DOMException("Bấm Nghe để cho phép phát giọng đọc.", "NotAllowedError");
        const source = speechAudioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(speechAudioContext.destination);
        source.onended = ended;
        activeAudioSource = source;
        source.start();
      } else {
        activeAudioUrl = URL.createObjectURL(blob);
        activeAudio = new Audio(activeAudioUrl);
        activeAudio.onended = ended;
        activeAudio.onerror = () => { if (current()) { resetSpeechButton(); status.textContent = "Không thể phát giọng PTIT. Bấm Nghe để thử lại."; } };
        await activeAudio.play();
      }
      if (!current()) return;
      setSpeechButtonAppearance(button, "playing");
      status.textContent = "Đang đọc câu trả lời.";
      if (state.queue.length) void prepareGeminiSpeech(button, state.queue[0].text).catch(() => {});
    } catch (error) {
      if (current()) {
        resetSpeechButton();
        status.textContent = error.name === "NotAllowedError" ? "Trình duyệt chặn tự phát. Bấm Nghe để cho phép." : "PTIT chưa thể đọc. Nội dung vẫn có trên màn hình; bấm Nghe để thử lại.";
      }
    } finally { window.clearTimeout(timer); state.timer = null; }
  }

  return {
    append(segment) {
      if (!current() || !geminiTtsConfigured || state.seen.has(segment.segment_id)) return;
      state.seen.add(segment.segment_id);
      state.queue.push(segment);
      if (state.playing && state.queue.length === 1) void prepareGeminiSpeech(button, segment.text).catch(() => {});
      void pump();
    },
    finish() { state.finished = true; void pump(); },
    cancel() { if (current()) resetSpeechButton(); state.cancelled = true; },
  };
}

document.getElementById("messages").addEventListener("click", async (event) => {
  const button = event.target.closest(".speak-button");
  if (!button) return;
  unlockSpeechPlayback();
  if (activeSpeechButton === button) {
    if (speechSupported) window.speechSynthesis.cancel();
    resetSpeechButton();
    return;
  }

  resetSpeechButton();
  const text = button.closest(".message").querySelector(".markdown-body")?.innerText.trim();
  if (!text) return;
  await speakResponse(text, button);
});

function formatBytes(bytes) {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderAttachmentPreview(error = "") {
  const preview = document.getElementById("attachment-preview");
  const items = selectedAttachments.map((item, index) => `
    <span class="attachment-chip">
      ${item.previewUrl ? `<img src="${escapeHtml(item.previewUrl)}" alt="">` : "📄"}
      <span>${escapeHtml(item.file.name)} · ${formatBytes(item.file.size)}</span>
      <button type="button" data-remove-attachment="${index}" aria-label="Bỏ tệp ${escapeHtml(item.file.name)}">×</button>
    </span>`).join("");
  preview.innerHTML = `${items}${items ? '<p class="attachment-intent">Gửi không kèm lời nhắn sẽ chỉ đọc giấy tờ và hỏi thủ tục bạn cần; chưa lưu thông tin hoặc gửi hồ sơ đến BQL.</p>' : ""}${error ? `<span class="attachment-error" role="alert">${escapeHtml(error)}</span>` : ""}`;
  preview.classList.toggle("visible", Boolean(items || error));
}

document.getElementById("file-input").addEventListener("change", (event) => {
  const files = Array.from(event.target.files || []);
  selectedAttachments.forEach((item) => {
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
  });
  selectedAttachments = [];

  if (files.length > 4) {
    event.target.value = "";
    renderAttachmentPreview("Chỉ được chọn tối đa 4 tệp.");
    return;
  }
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  if (totalBytes > 16 * 1024 * 1024) {
    event.target.value = "";
    renderAttachmentPreview("Tổng dung lượng tệp vượt quá 16 MB.");
    return;
  }
  for (const file of files) {
    const extension = file.name.split(".").pop()?.toLowerCase() || "";
    if (!allowedExtensions.has(extension)) {
      event.target.value = "";
      renderAttachmentPreview(`Không hỗ trợ định dạng của ${file.name}.`);
      return;
    }
    const limit = imageExtensions.has(extension) ? 5 : 8;
    if (file.size > limit * 1024 * 1024) {
      event.target.value = "";
      renderAttachmentPreview(`${file.name} vượt quá giới hạn ${limit} MB.`);
      return;
    }
    selectedAttachments.push({
      file,
      previewUrl: imageExtensions.has(extension) ? URL.createObjectURL(file) : "",
    });
  }
  renderAttachmentPreview();
});

document.getElementById("attachment-preview").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-attachment]");
  if (!button) return;
  const index = Number(button.dataset.removeAttachment);
  const [removed] = selectedAttachments.splice(index, 1);
  if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
  document.getElementById("file-input").value = "";
  renderAttachmentPreview();
});

const micButton = document.getElementById("mic-button");
const speechStatus = document.getElementById("speech-status");
const chatInput = document.getElementById("chat-input");

function resizeChatInput() {
  chatInput.style.height = "auto";
  chatInput.style.height = `${Math.min(chatInput.scrollHeight, 128)}px`;
}

function handleChatInputKeydown(event) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229 || event.currentTarget.disabled) return;
  event.preventDefault();
  document.getElementById("chat-form").requestSubmit();
}

chatInput.addEventListener("input", resizeChatInput);
chatInput.addEventListener("keydown", handleChatInputKeydown);

function updateRecordingAvailability() {
  micButton.disabled = !recordingSupported || !omicxConfigured;
  if (!recordingSupported) {
    micButton.title = "Trình duyệt này chưa hỗ trợ nhập bằng giọng nói";
  } else if (!omicxConfigured) {
    micButton.title = "Nhập bằng giọng nói hiện chưa sẵn sàng";
  } else {
    micButton.title = "Nhập bằng giọng nói tiếng Việt";
    micButton.setAttribute("aria-label", "Nhập nội dung bằng giọng nói tiếng Việt");
  }
}

function setRecordingState(recording, message = "") {
  recordingActive = recording;
  micButton.classList.toggle("listening", recording);
  micButton.innerHTML = interfaceIcons[recording ? "stop" : "microphone"];
  micButton.setAttribute("aria-label", recording
    ? "Dừng ghi âm và chuyển thành văn bản"
    : "Nhập nội dung bằng giọng nói tiếng Việt");
  micButton.title = recording ? "Dừng ghi âm" : "Nhập bằng giọng nói tiếng Việt";
  speechStatus.textContent = message;
}

function releaseMicrophone() {
  if (recordingTimer) window.clearTimeout(recordingTimer);
  recordingTimer = null;
  microphoneStream?.getTracks().forEach((track) => track.stop());
  microphoneStream = null;
}

async function submitRecording(recorder) {
  releaseMicrophone();
  setRecordingState(false, "Đang chuyển giọng nói thành văn bản…");
  micButton.disabled = true;
  const mimeType = recorder.mimeType || recordingChunks[0]?.type || "audio/webm";
  const extension = mimeType.includes("ogg") ? "ogg" : "webm";
  const audio = new Blob(recordingChunks, { type: mimeType });
  recordingChunks = [];

  try {
    if (!audio.size) throw new Error("Bản ghi âm đang trống.");
    const formData = new FormData();
    formData.append("file", audio, `resident-voice.${extension}`);
    formData.append("language_code", "vi-VN");
    const result = await api("/speech/transcribe", { method: "POST", body: formData });
    chatInput.value = [recordingBaseText, result.transcript].filter(Boolean).join(" ");
    resizeChatInput();
    chatInput.focus();
    speechStatus.textContent = "Đã chuyển giọng nói thành văn bản.";
  } catch (error) {
    speechStatus.textContent = error.message;
  } finally {
    mediaRecorder = null;
    updateRecordingAvailability();
  }
}

function stopResidentRecording() {
  if (!mediaRecorder || mediaRecorder.state === "inactive") return;
  if (recordingTimer) window.clearTimeout(recordingTimer);
  recordingTimer = null;
  micButton.disabled = true;
  speechStatus.textContent = "Đang xử lý bản ghi…";
  mediaRecorder.stop();
}

async function startResidentRecording() {
  if (!omicxConfigured) {
    speechStatus.textContent = "Nhập bằng giọng nói hiện chưa sẵn sàng.";
    return;
  }
  recordingBaseText = chatInput.value.trim();
  speechStatus.textContent = "Đang mở micro…";
  try {
    microphoneStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const preferredTypes = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"];
    const mimeType = preferredTypes.find((type) => MediaRecorder.isTypeSupported(type));
    const recorder = new MediaRecorder(microphoneStream, mimeType ? { mimeType } : undefined);
    mediaRecorder = recorder;
    recordingChunks = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size) recordingChunks.push(event.data);
    };
    recorder.onerror = () => {
      releaseMicrophone();
      setRecordingState(false, "Không thể ghi âm từ micro.");
      updateRecordingAvailability();
    };
    recorder.onstop = () => submitRecording(recorder);
    recorder.start(250);
    setRecordingState(true, "Đang ghi âm · bấm nút dừng khi bạn nói xong.");
    recordingTimer = window.setTimeout(stopResidentRecording, 60_000);
  } catch (error) {
    releaseMicrophone();
    const message = error.name === "NotAllowedError"
      ? "Cần cho phép dùng micro để nhập bằng giọng nói."
      : error.name === "NotFoundError"
        ? "Không tìm thấy micro trên thiết bị."
        : "Không thể mở micro lúc này.";
    setRecordingState(false, message);
  }
}

if (recordingSupported) {
  micButton.addEventListener("click", () => {
    if (recordingActive) stopResidentRecording();
    else startResidentRecording();
  });
} else {
  micButton.disabled = true;
  micButton.classList.add("unsupported");
  micButton.title = "Trình duyệt này chưa hỗ trợ nhập bằng giọng nói";
  speechStatus.textContent = "Trình duyệt này chưa hỗ trợ nhập bằng giọng nói.";
}

function addTypingMessage() {
  const messages = document.getElementById("messages");
  const article = document.createElement("article");
  article.className = "message assistant typing-message";
  article.innerHTML = `
    <div class="avatar" aria-hidden="true"><img src="/static/agent-mascot.svg?v=20261010-motion-v3" width="32" height="32" alt="" /></div>
    <div>
      <strong>MoveInMate</strong>
      <div class="typing-indicator" role="status" aria-label="MoveInMate đang hiểu yêu cầu">
        <span class="thinking-orbit" aria-hidden="true"><i></i></span>
        <span class="typing-copy">
          <span class="typing-step">Đang hiểu yêu cầu</span>
          <small>MoveInMate đang chuẩn bị câu trả lời</small>
        </span>
        <span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span>
        <span class="typing-scan" aria-hidden="true"></span>
      </div>
    </div>`;
  messages.appendChild(article);
  messages.scrollTo({ top: messages.scrollHeight, behavior: "smooth" });

  const waitingSteps = ["Đang hiểu yêu cầu", "Đang tra cứu thông tin", "Đang tổng hợp câu trả lời"];
  let stepIndex = 0;
  const stepElement = article.querySelector(".typing-step");
  const intervalId = window.setInterval(() => {
    if (!article.isConnected) {
      window.clearInterval(intervalId);
      return;
    }
    stepIndex = (stepIndex + 1) % waitingSteps.length;
    stepElement.classList.remove("changing");
    void stepElement.offsetWidth;
    stepElement.textContent = waitingSteps[stepIndex];
    stepElement.classList.add("changing");
    article.querySelector(".typing-indicator")?.setAttribute("aria-label", `MoveInMate ${waitingSteps[stepIndex].toLowerCase()}`);
  }, 1500);
  article.stopWaiting = () => window.clearInterval(intervalId);
  return article;
}

document.getElementById("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (recordingActive) {
    stopResidentRecording();
    return;
  }
  const input = document.getElementById("chat-input");
  const fileInput = document.getElementById("file-input");
  const attachButton = event.currentTarget.querySelector(".attach-button");
  const submitButton = event.currentTarget.querySelector('button[type="submit"]');
  const message = attachmentChatMessage(input.value, selectedAttachments.length);
  const attachments = selectedAttachments;
  if (!message && !attachments.length) return;
  const generation = authGeneration;
  resetSpeechButton();
  if (autoTtsEnabled) unlockSpeechPlayback();
  addMessage("user", message, [], [], attachments);
  input.value = "";
  resizeChatInput();
  selectedAttachments = [];
  fileInput.value = "";
  renderAttachmentPreview();
  input.disabled = true;
  fileInput.disabled = true;
  attachButton.classList.add("disabled");
  micButton.disabled = true;
  submitButton.disabled = true;
  submitButton.textContent = "Đang trả lời…";
  updateChatActionAvailability();
  const typingMessage = addTypingMessage();
  let liveMessage = null;
  try {
    let result;
    const correctionRequested = typeof shouldHandleTicketCorrectionChat === "function" && shouldHandleTicketCorrectionChat(message);
    const draftResult = typeof requestTicketDraftChat === "function" && (requestDraftChatState || !correctionRequested)
      ? await requestTicketDraftChat(message, attachments) : null;
    if (!isSessionCurrent(generation)) return;
    if (draftResult?.handled) {
      result = draftResult;
      liveMessage = createLiveAssistantMessage(typingMessage);
      await liveMessage.finish(result);
      if (result.form_draft) renderAttachmentDraft(result, attachments, typingMessage);
      else renderRequestDraftChat(result, typingMessage);
    } else if (correctionRequested) {
      result = await requestTicketCorrectionChat(message, attachments);
      if (!isSessionCurrent(generation)) return;
      liveMessage = createLiveAssistantMessage(typingMessage);
      liveMessage.append(result.answer);
      await liveMessage.finish(result);
      renderCorrectionChatResult(result, typingMessage);
    } else if (attachments.length) {
      const formData = new FormData();
      formData.append("project_id", "moveinmate");
      if (authConfig?.auth_mode === "demo") formData.append("actor_id", currentResidentId());
      formData.append("conversation_id", conversationId);
      formData.append("message", message);
      formData.append("language", "auto");
      attachments.forEach((item) => formData.append("files", item.file));
      result = await api("/agent/form-from-files", { method: "POST", body: formData });
      if (!isSessionCurrent(generation)) return;
      liveMessage = createLiveAssistantMessage(typingMessage);
      liveMessage.append(result.answer);
      await liveMessage.finish(result);
      renderAttachmentDraft(result, attachments, typingMessage);
    } else {
      result = await streamAGUI("/agent/chat/ag-ui", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: "moveinmate",
          ...(authConfig?.auth_mode === "demo" ? { actor_id: currentResidentId() } : {}),
          conversation_id: conversationId,
          message,
          language: "auto",
        }),
      }, (streamEvent) => {
        if (!isSessionCurrent(generation)) return;
        if (!liveMessage) liveMessage = createLiveAssistantMessage(typingMessage);
        if (streamEvent.type === "token") liveMessage.append(streamEvent.content);
        if (streamEvent.type === "speech_segment") liveMessage.speech(streamEvent);
        if (streamEvent.type === "warning") {
          if (streamEvent.code === "public_answer_changed") liveMessage.cancelSpeech();
          document.getElementById("speech-status").textContent = streamEvent.message;
        }
      });
      if (!isSessionCurrent(generation)) return;
      if (!liveMessage) liveMessage = createLiveAssistantMessage(typingMessage);
      await liveMessage.finish(result);
    }
  } catch (error) {
    if (!isSessionCurrent(generation)) return;
    liveMessage?.cancelSpeech();
    const errorResult = { answer: error.message || "Trợ lý chưa thể trả lời lúc này. Vui lòng thử lại sau.", retryQuestion: message };
    if (!liveMessage) liveMessage = createLiveAssistantMessage(typingMessage);
    await liveMessage.finish(errorResult, { automaticSpeech: false });
  } finally {
    if (typingMessage.classList.contains("typing-message")) {
      typingMessage.stopWaiting?.();
      typingMessage.remove();
    }
    if (!isSessionCurrent(generation)) return;
    input.disabled = false;
    fileInput.disabled = false;
    attachButton.classList.remove("disabled");
    updateRecordingAvailability();
    submitButton.disabled = false;
    submitButton.textContent = "Gửi";
    updateChatActionAvailability();
    if (chatWidget.classList.contains("open")) input.focus();
  }
});

function attachmentChatMessage(value) {
  return value.trim();
}

function sendSuggestedPrompt(prompt) {
  const chatInputElement = document.getElementById("chat-input");
  if (chatInputElement.disabled) {
    document.getElementById("speech-status").textContent = "Trợ lý đang trả lời yêu cầu trước. Vui lòng chờ một chút.";
    return;
  }
  setProcedurePickerOpen(false, false);
  setChatWidgetOpen(true);
  chatInputElement.value = prompt;
  resizeChatInput();
  document.getElementById("chat-form").requestSubmit();
}

document.querySelectorAll("[data-ui-action]").forEach((button) => button.addEventListener("click", () => {
  handleUIAction(button.dataset.uiAction, { tab: button.dataset.uiTab, message: button.dataset.uiMessage, form: button.dataset.uiForm });
}));
document.querySelectorAll("[data-chat-prompt]").forEach((button) => button.addEventListener("click", () => {
  sendSuggestedPrompt(button.dataset.chatPrompt || button.textContent.trim());
}));
document.getElementById("open-chat-hero").addEventListener("click", openProcedurePicker);
document.getElementById("open-chat-main").addEventListener("click", () => focusChatDraft(""));
document.querySelector(".brand").addEventListener("click", () => {
  document.querySelector('.tabs button[data-tab="agent"]')?.click();
});
document.querySelectorAll(".tab-shortcut").forEach((button) => button.addEventListener("click", () => {
  if (!authReady || button.disabled || button.hidden) return;
  const target = document.querySelector(`.tabs button[data-tab="${button.dataset.tabTarget}"]`);
  target?.click();
  document.querySelector(".tabs")?.scrollIntoView({ behavior: "smooth", block: "start" });
}));

async function loadProgress() {
  if (!authReady || !hasRole("resident")) return;
  const generation = authGeneration;
  const list = document.getElementById("task-list");
  const refreshButton = document.getElementById("refresh-progress");
  refreshButton.disabled = true;
  refreshButton.innerHTML = '<span class="refresh-spinner" aria-hidden="true">↻</span> Đang tải';
  list.innerHTML = '<div class="task-loading" role="status"><i></i><i></i><i></i><span>Đang tải danh sách việc…</span></div>';
  try {
    const data = await api(residentPath("/progress"));
    if (!isSessionCurrent(generation)) return;
    synchronizeProcedureCards(data);
    const progressBar = document.querySelector(".progress-bar");
    document.getElementById("progress-fill").style.width = `${data.percent}%`;
    progressBar.setAttribute("aria-valuenow", String(data.percent));
    progressBar.setAttribute("aria-valuetext", `Đã hoàn thành ${data.completed} trên ${data.total} việc bắt buộc`);
    document.getElementById("progress-copy").innerHTML = `Đã xong <strong>${data.completed}/${data.total}</strong> việc bắt buộc <span>(${data.percent}%)</span>`;
    list.innerHTML = data.all_tasks.map((task, index) => {
      const status = checklistStatusLabel(task);
      const comingSoon = ["handover_inspection", "safety_briefing"].includes(task.code);
      const label = comingSoon ? "Coming soon" : task.required ? status : `Tùy chọn · ${status}`;
      const completed = task.status === "completed";
      const ownershipDone = task.code === "verify_residency" && Boolean(residentOwnershipState.profile?.ownership_confirmed_at);
      const guidePrompt = `Hướng dẫn tôi làm việc: ${task.title}`;
      return `<article class="task ${completed ? "completed" : ""}" data-status="${escapeHtml(task.status)}">
        <span class="task-marker" aria-hidden="true">${completed ? "✓" : index + 1}</span>
        <div class="task-copy"><strong>${escapeHtml(task.title)}</strong><div class="task-meta"><span class="badge">${task.required ? "Bắt buộc" : "Tùy chọn"}</span><span class="badge">${escapeHtml(comingSoon ? label : status)}</span></div><small>${escapeHtml(task.reason)}</small>${task.updated_at && task.status !== "pending" ? `<small>Cập nhật ${escapeHtml(new Date(task.updated_at).toLocaleString("vi-VN"))}${task.request_id ? ` · Ticket ${escapeHtml(task.request_id)}` : ""}</small>` : ""}</div>
        <div class="task-side">${task.code === "confirm_regulations" ? `<button class="task-regulations" type="button">${completed ? "Xem xác nhận đã đọc" : "Đọc và xác nhận"}</button>` : comingSoon || ownershipDone ? "" : `<button class="task-help" type="button" data-chat-prompt="${escapeHtml(guidePrompt)}">Xem hướng dẫn</button>${residentEntryCatalog[task.code] ? `<button class="task-entry" type="button" data-form="${escapeHtml(task.code)}">Nhập thông tin</button>` : ""}`}</div>
      </article>`;
    }).join("");
  } catch (error) {
    list.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  } finally {
    refreshButton.disabled = false;
    refreshButton.innerHTML = '<span aria-hidden="true">↻</span> Làm mới';
  }
}

document.getElementById("task-list").addEventListener("click", (event) => {
  if (event.target.closest(".task-regulations")) { openResidentTab("regulations"); return; }
  const entry = event.target.closest(".task-entry");
  if (entry) { void openResidentEntry(entry.dataset.form); return; }
  const button = event.target.closest(".task-help");
  if (button) sendSuggestedPrompt(button.dataset.chatPrompt);
});

const residentEntryCatalog = {
  ownership: { title: "Xác thực quyền sở hữu căn hộ", fields: [
    { key: "full_name", label: "Họ và tên cư dân", readonly: true },
    { key: "resident_type", label: "Tư cách cư trú", readonly: true },
    { key: "site_name", label: "Khu dân cư", readonly: true },
    { key: "building", label: "Tòa / khu vực", readonly: true },
    { key: "apartment", label: "Căn hộ BQL đã cấp", readonly: true },
  ] },
  profile: { title: "Thông tin cư dân", fields: [
    { key: "full_name", label: "Họ và tên cư dân", readonly: true },
    { key: "resident_type", label: "Tư cách cư trú", readonly: true },
    { key: "site_name", label: "Khu dân cư", readonly: true },
    { key: "building", label: "Tòa / khu vực", readonly: true },
    { key: "apartment", label: "Căn hộ BQL đã cấp", readonly: true },
    { key: "family_members", label: "Số người khai báo ban đầu (chưa tính người tạm trú BQL đã duyệt)", type: "number", required: true, min: 1, max: 20 },
    { key: "vehicle", label: "Nhu cầu phương tiện", options: [["none", "Không có xe"], ["motorbike", "Xe máy"], ["car", "Ô tô"]] },
    { key: "needs_internet", label: "Cần đăng ký internet", options: [["false", "Không"], ["true", "Có"]] },
  ] },
  verify_residency: { title: "Xác nhận thông tin cư trú", fields: [
    { key: "full_name", label: "Họ và tên", readonly: true },
    { key: "site_name", label: "Khu dân cư", readonly: true },
    { key: "target_location", label: "Tòa / khu vực · căn hộ", options: [] },
    { key: "residency_purpose", label: "Bạn cần xác nhận điều gì?", options: [["current", "Xác nhận thông tin đang cư trú"], ["supplement", "Bổ sung hồ sơ cư trú"], ["correction", "Đề nghị kiểm tra thông tin căn hộ"]] },
    { key: "details", label: "Ghi chú thêm (không bắt buộc)", type: "textarea", max: 600, suggestions: ["Vui lòng đối chiếu thông tin căn hộ đã cấp.", "Tôi cần bổ sung thông tin vào hồ sơ cư trú."] },
  ] },
  resident_card: { title: "Đăng ký thẻ cư dân", fields: [
    { key: "pickup_date", label: "Ngày mong muốn nhận thẻ (nếu đã xác định)", type: "date" },
    { key: "pickup_time", label: "Giờ mong muốn nhận thẻ", type: "time" },
    { key: "details", label: "Thông tin hoặc ghi chú gửi Ban quản lý", type: "textarea", max: 1000 },
  ] },
  vehicle_registration: { title: "Đăng ký thẻ xe", fields: [
    { key: "vehicle_type", label: "Loại phương tiện", options: [["motorbike", "Xe máy"], ["car", "Ô tô"]] },
    { key: "vehicle_plate", label: "Biển số xe", required: true, max: 20, placeholder: "Nhập biển số đúng trên giấy đăng ký xe" },
  ] },
  temporary_residency: { title: "Thêm người cư trú", fields: [
    { key: "person_name", label: "Họ và tên người cư trú", required: true, max: 120 },
    { key: "resident_type", label: "Tư cách cư trú", options: [["family_member", "Thành viên gia đình"], ["tenant", "Người thuê"]] },
    { key: "citizen_id", label: "Số CCCD (12 chữ số)", required: true, max: 12, pattern: "[0-9]{12}", inputmode: "numeric" },
    { key: "visit_date", label: "Ngày bắt đầu cư trú (nếu đã xác định)", type: "date" },
    { key: "details", label: "Ghi chú", type: "textarea", max: 1000 },
  ] },
  account_registration: { title: "Đăng ký tài khoản cư dân phụ thuộc", fields: [
    { key: "household_member_id", label: "Người cư trú được cấp tài khoản", options: [], required: true },
  ] },
  card_replacement: { title: "Báo mất / cấp lại thẻ", fields: [{ key: "details", label: "Mô tả thẻ bị mất hoặc cần cấp lại", type: "textarea", required: true, max: 1000 }] },
  visitor_registration: { title: "Đăng ký khách", fields: [
    { key: "visitor_name", label: "Họ và tên khách", required: true, max: 120 },
    { key: "visit_date", label: "Ngày đến", type: "date", required: true },
    { key: "details", label: "Ghi chú", type: "textarea", max: 1000 },
  ] },
  move_in_schedule: { title: "Đăng ký lịch chuyển đồ", fields: [
    { key: "visit_date", label: "Ngày chuyển đồ", type: "date", required: true },
    { key: "move_time", label: "Giờ chuyển đồ", type: "time", required: true },
    { key: "details", label: "Ghi chú", type: "textarea", max: 1000 },
  ] },
  internet_registration: { title: "Đăng ký internet", fields: [
    { key: "service_need", label: "Nhu cầu", options: [["Đăng ký lắp mới", "Đăng ký lắp mới"], ["Tư vấn gói internet", "Tư vấn gói internet"], ["Hỗ trợ kết nối hiện tại", "Hỗ trợ kết nối hiện tại"]] },
    { key: "details", label: "Thông tin liên hệ hoặc ghi chú (không bắt buộc)", type: "textarea", max: 700 },
  ] },
  general_support: { title: "Gửi câu hỏi / phản ánh đến Ban quản lý", fields: [
    { key: "support_category", label: "Nhóm vấn đề", options: [["Hỏi thủ tục", "Hỏi thủ tục"], ["Kỹ thuật căn hộ", "Kỹ thuật căn hộ"], ["Vệ sinh", "Vệ sinh"], ["An ninh", "An ninh"], ["Tiếng ồn", "Tiếng ồn"], ["Tiện ích chung", "Tiện ích chung"], ["Khác", "Khác"]] },
    { key: "details", label: "Vấn đề cần hỗ trợ", type: "textarea", required: true, minlength: 5, max: 700, suggestions: ["Nhờ BQL kiểm tra sự cố tại căn hộ của tôi.", "Tôi muốn phản ánh tiếng ồn tại khu vực sinh hoạt.", "Nhờ BQL hỗ trợ vấn đề vệ sinh khu vực chung."] },
  ] },
};

const residentProcedureNotices = {
  resident_card: "Bạn có thể chọn ngày và giờ mong muốn nhận thẻ; nếu chọn lịch, hãy điền đủ cả hai. BQL sẽ xem xét và xác nhận lịch nhận. Đã gửi hoặc đã duyệt chưa có nghĩa thẻ đã được bàn giao.",
  vehicle_registration: "Mỗi xe cần một hồ sơ và ảnh giấy đăng ký riêng, kể cả khi nhập tay. Kiểm tra đúng chiều ảnh, biển số và loại xe trước khi gửi. BQL đối chiếu giấy tờ; duyệt hồ sơ chưa đồng nghĩa đã cấp xong thẻ xe.",
  temporary_residency: "Chỉ chủ sở hữu đăng ký thêm người cư trú. Kiểm tra họ tên, tư cách và CCCD trước khi gửi BQL. Hồ sơ bị từ chối không được tính vào danh sách căn hộ; ticket này không thay việc đăng ký tạm trú với cơ quan nhà nước.",
  account_registration: "Chủ sở hữu chọn người thuê hoặc người nhà đã được BQL duyệt cư trú và chưa có tài khoản. BQL xét duyệt, cấp và bàn giao tài khoản riêng; không chia sẻ tài khoản hoặc mật khẩu của chủ sở hữu.",
  card_replacement: "Nêu rõ loại thẻ và tình huống cần cấp lại để BQL đối chiếu. Gửi ticket không tự khóa thẻ vật lý hoặc phát hành thẻ mới. Theo dõi phản hồi của BQL trên chính ticket này.",
  visitor_registration: "Chọn ngày khách đến từ hôm nay trở đi. Khách chuyển đến ở cùng hoặc ở qua đêm cần đăng ký Thêm người cư trú. BQL xem xét yêu cầu; gửi ticket không tự cấp quyền ra vào.",
  move_in_schedule: "Ngày và giờ chuyển đồ là lịch đề nghị, cần BQL xem xét trước khi thực hiện. Ghi chú đồ cồng kềnh nếu có; khi cần điều chỉnh, bổ sung trên ticket đang mở thay vì gửi trùng.",
  internet_registration: "Sẽ có người lên tư vấn sau khi BQL tiếp nhận và phối hợp sắp xếp. BQL chỉ đánh dấu Hoàn tất khi vấn đề đã được giải quyết và được xác nhận giữa BQL với người tư vấn. Ticket không phải hợp đồng hoặc lịch lắp đặt đã được nhà mạng xác nhận.",
  general_support: "Mô tả rõ vấn đề và vị trí cần hỗ trợ. BQL tiếp nhận, có thể yêu cầu bổ sung rồi xử lý; theo dõi và phản hồi trên cùng ticket. Nếu có tình huống khẩn cấp, liên hệ đầu mối khẩn cấp thay vì chờ ticket.",
  verify_residency: "Yêu cầu đối chiếu không tự thay đổi họ tên hoặc căn hộ BQL đã cấp. Nếu thông tin sai, chờ BQL kiểm tra và xác nhận điều chỉnh; không xác nhận thông tin chưa đúng.",
  handover_inspection: "BQL cần đối chiếu hiện trạng và biên bản bàn giao thực tế trước khi xác nhận xử lý xong. Ticket không thay thế biên bản bàn giao.",
  emergency_contact: "BQL kiểm tra và đối chiếu thông tin liên hệ được cung cấp. Thông tin này không thay thế các số điện thoại khẩn cấp.",
};

function internetTicketNotice(item, role) {
  if (item.request_type !== "internet_registration" || !["submitted", "in_review", "needs_info", "approved"].includes(item.status)) return "";
  const messages = {
    resident: "BQL sẽ cử người lên tư vấn.",
    manager: "BQL cần cử người phụ trách lên tư vấn.",
  };
  return messages[role] ? `<p class="internet-ticket-notice"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><use href="/static/icons.svg#wifi" /></svg><span>${escapeHtml(messages[role])}</span></p>` : "";
}

let residentEntryState = null;
let residentEntryLoadId = 0;
const residentEntryDialog = document.getElementById("resident-entry-dialog");
const residentEntryForm = document.getElementById("resident-entry-form");

function entryPayload(type, values) {
  if (type === "ownership") {
    return { full_name: values.full_name.trim(), apartment: values.apartment.trim(), building: values.building.trim() };
  }
  if (type === "profile") return {
    family_members: Number(values.family_members), vehicle: values.vehicle,
    needs_internet: values.needs_internet === "true",
  };
  if (type === "verify_residency" && values.target_location) {
    const location = JSON.parse(values.target_location);
    return { residency_purpose: values.residency_purpose, target_apartment: location.apartment, target_building: location.building, details: values.details.trim() };
  }
  if (type === "general_support" || type === "internet_registration") return { details: `${values.support_category || values.service_need}: ${values.details.trim()}`.trim() };
  return Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value.trim()]).filter(([, value]) => value));
}

function setEntryError(message = "") {
  const node = document.getElementById("resident-entry-error");
  const labels = { ownership_information_locked: "Thông tin do BQL cấp không thể tự thay đổi. Nếu có sai sót, hãy liên hệ BQL đối chiếu và xác nhận điều chỉnh.", ownership_profile_changed: "Hồ sơ vừa thay đổi. Đóng và mở lại bảng để kiểm tra thông tin mới nhất.", ownership_location_invalid: "Chọn đúng căn hộ trong danh sách của khu được cấp.", ownership_information_invalid: "Kiểm tra lại họ tên và căn hộ trước khi xác nhận." };
  node.textContent = labels[message] || (message === "apartment_owner_exists" ? "Căn hộ đã có chủ sở hữu khác. Không thể lưu thêm chủ sở hữu. Quay lại Sửa thông tin để chọn đúng tư cách cư trú hoặc liên hệ Ban quản lý để đối chiếu." : message);
  node.hidden = !message;
  if (message) node.focus();
}

function initialize24HourTimeInput(input, label) {
  const required = input.required;
  input.type = "hidden";
  input.required = false;
  const control = document.createElement("div");
  control.className = "time24-control";
  control.setAttribute("role", "group");
  control.setAttribute("aria-label", `${label}, định dạng 24 giờ`);
  const selects = [];
  for (const [part, count, caption] of [["hour", 24, "Giờ"], ["minute", 60, "Phút"]]) {
    const select = document.createElement("select");
    select.id = `${input.id}-${part}`;
    select.required = required;
    select.setAttribute("aria-label", `${label}: ${caption.toLowerCase()} (00–${count - 1})`);
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "--";
    select.append(placeholder);
    for (let value = 0; value < count; value += 1) {
      const option = document.createElement("option");
      option.value = String(value).padStart(2, "0");
      option.textContent = option.value;
      select.append(option);
    }
    if (selects.length) {
      const separator = document.createElement("span");
      separator.textContent = ":";
      separator.setAttribute("aria-hidden", "true");
      control.append(separator);
    }
    selects.push(select);
    control.append(select);
  }
  let updating = false;
  const synchronize = () => {
    if (updating) return;
    const parts = /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(input.value) ? input.value.split(":") : ["", ""];
    selects.forEach((select, index) => { select.value = parts[index]; });
  };
  selects.forEach(select => select.addEventListener("change", event => {
    selects.forEach(part => { part.required = required || selects.some(option => option.value !== ""); });
    if (event?.isTrusted) {
      input.dataset.userEdited = "true";
      input.classList.remove("autofilled");
    }
    updating = true;
    input.value = selects.every(part => part.value !== "") ? selects.map(part => part.value).join(":") : "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    updating = false;
  }));
  input.addEventListener("input", synchronize);
  input.addEventListener("change", synchronize);
  const fieldLabel = input.closest("label");
  if (fieldLabel) {
    const field = document.createElement("div");
    field.className = "time24-field";
    fieldLabel.before(field);
    field.append(fieldLabel, control);
    fieldLabel.htmlFor = selects[0].id;
  } else input.after(control);
  synchronize();
}

function renderEntryFields(config, profile) {
  const host = document.getElementById("resident-entry-fields");
  host.replaceChildren();
  config.fields.forEach((field) => {
    const label = document.createElement("label");
    label.textContent = `${field.label}${field.required ? " *" : ""}`;
    const input = document.createElement(field.options && field.key !== "target_location" ? "select" : field.type === "textarea" ? "textarea" : "input");
    input.id = `entry-${field.key}`;
    input.name = field.key;
    label.htmlFor = input.id;
    if (field.options && field.key !== "target_location") field.options.forEach(([value, caption]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = caption;
      input.appendChild(option);
    });
    else if (field.type === "textarea") input.rows = 3;
    else input.type = field.key === "target_location" ? "hidden" : field.type || "text";
    input.required = field.key !== "target_location" && Boolean(field.required || field.options);
    if (field.max) { if (field.type === "number") input.max = field.max; else input.maxLength = field.max; }
    if (field.min) input.min = field.min;
    if (field.minlength) input.minLength = field.minlength;
    if (field.pattern) input.pattern = field.pattern;
    if (field.inputmode) input.inputMode = field.inputmode;
    if (field.placeholder) input.placeholder = field.placeholder;
    if (field.readonly) { input.readOnly = true; input.value = profile[field.key] || ""; }
    if (field.value !== undefined) input.value = field.value;
    if (residentEntryState.type === "profile") input.value = String(field.key === "family_members" ? profile.declared_family_members ?? profile.family_members : profile[field.key] ?? "");
    if (field.key === "family_members" && profile.resident_type !== "owner") input.readOnly = true;
    if (field.key === "resident_type" && field.readonly) input.value = ({ owner: "Chủ sở hữu", tenant: "Người thuê", family_member: "Thành viên gia đình" })[profile.resident_type] || profile.resident_type;
    if (field.key === "vehicle_type") {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Chọn loại phương tiện";
      input.prepend(placeholder);
      input.value = "";
    }
    label.appendChild(input);
    host.appendChild(label);
    if (field.type === "time") initialize24HourTimeInput(input, field.label);
    if (field.key === "target_location") {
      const search = document.createElement("input");
      search.type = "text";
      search.id = "entry-apartment-query";
      search.maxLength = 40;
      search.required = true;
      search.placeholder = "Nhập mã hoặc chọn căn hộ trong khu";
      search.value = profile.apartment;
      label.htmlFor = search.id;
      label.append(search);
      const panel = document.createElement("div");
      panel.id = "entry-apartment-suggestions";
      panel.className = "apartment-suggestions";
      panel.hidden = true;
      host.append(panel);
      const choices = residentEntryState.locations || [];
      const error = document.createElement("p");
      error.id = "ownership-location-error";
      error.className = "field-error apartment-owner-error";
      error.setAttribute("role", "alert");
      error.hidden = true;
      if (residentEntryState.type === "ownership") { host.append(error); search.setAttribute("aria-describedby", error.id); }
      const select = item => {
        const conflict = residentEntryState.type === "ownership" && locationHasOwner(choices, profile.site_id, item.building, item.apartment);
        const message = conflict ? `Căn hộ ${item.apartment} đã có chủ sở hữu khác. Chọn đúng căn hộ hoặc liên hệ Ban quản lý để đối chiếu.` : "";
        input.value = conflict ? "" : JSON.stringify({ apartment: item.apartment, building: item.building });
        search.setCustomValidity(message);
        search.setAttribute("aria-invalid", String(conflict));
        error.textContent = message;
        error.hidden = !conflict;
      };
      attachApartmentPicker(search, panel, {
        items: () => choices,
        onSelect: select,
        onInput: value => {
          const matches = choices.filter(item => normalizeApartmentCode(item.apartment) === normalizeApartmentCode(value));
          if (matches.length === 1) select(matches[0]);
          else {
            input.value = "";
            error.hidden = true;
            search.setCustomValidity("Chọn một căn hộ trong bảng gợi ý để xác định đúng tòa / khu vực.");
            search.setAttribute("aria-invalid", "true");
          }
        },
      });
    }
    if (field.key === "resident_type" && !field.readonly) {
      const error = document.createElement("p");
      error.id = "entry-owner-error";
      error.className = "field-error apartment-owner-error";
      error.setAttribute("role", "alert");
      input.setAttribute("aria-describedby", error.id);
      label.append(error);
      const validate = () => {
        const conflict = input.value === "owner" && locationHasOwner(residentEntryState.locations || [], profile.site_id, profile.building, profile.apartment);
        const message = conflict ? `Căn hộ ${profile.apartment} đã có chủ sở hữu. Không thể đăng ký thêm chủ sở hữu; chọn đúng tư cách cư trú hoặc liên hệ BQL.` : "";
        input.setCustomValidity(message);
        input.setAttribute("aria-invalid", String(conflict));
        error.textContent = message;
        error.hidden = !conflict;
      };
      input.addEventListener("change", validate);
      validate();
    }
    if (field.key === "residency_purpose" && field.options) {
      const choices = document.createElement("div");
      choices.className = "suggestion-chips";
      choices.setAttribute("aria-label", "Chọn nhanh nội dung xác nhận");
      field.options.forEach(([value, caption]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = caption;
        button.setAttribute("aria-pressed", String(input.value === value));
        button.addEventListener("click", () => { input.value = value; input.dispatchEvent(new Event("change")); });
        choices.appendChild(button);
      });
      input.addEventListener("change", () => Array.from(choices.children).forEach((button, index) => button.setAttribute("aria-pressed", String(input.value === field.options[index][0]))));
      host.appendChild(choices);
    }
    if (field.suggestions) {
      const suggestions = document.createElement("div");
      suggestions.className = "suggestion-chips";
      suggestions.setAttribute("aria-label", `Gợi ý ${field.label}`);
      field.suggestions.forEach((text) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = text;
        button.addEventListener("click", () => { input.value = text; input.focus(); input.dispatchEvent(new Event("input", { bubbles: true })); });
        suggestions.appendChild(button);
      });
      host.appendChild(suggestions);
    }
  });
}

async function openResidentEntry(type, questionDraft = "") {
  if (!authReady || !hasRole("resident") || !Object.hasOwn(residentEntryCatalog, type)) return;
  if (type === "verify_residency") type = "ownership";
  if (ownershipRequired() && type !== "ownership") { showOwnershipWarning(); return; }
  if (["account_registration", "temporary_residency"].includes(type) && residentOwnershipState.profile?.resident_type !== "owner") { showOwnershipWarning("Chỉ chủ sở hữu mới được quyền đăng ký tính năng này"); return; }
  if (residentEntryState?.submitting) return;
  const returnFocus = document.activeElement;
  if (chatWidget.classList.contains("open")) setChatWidgetOpen(false);
  const loadId = ++residentEntryLoadId;
  const generation = authGeneration;
  residentEntryState = { type, generation, key: crypto.randomUUID(), profile: null, review: false, submitting: false, returnFocus };
  resetFormAutofill();
  const config = residentEntryCatalog[type];
  residentEntryDialog.dataset.formType = type;
  residentEntryForm.reset();
  document.getElementById("resident-entry-need").hidden = true;
  document.getElementById("resident-entry-need").disabled = false;
  document.getElementById("resident-entry-request-fields").disabled = false;
  document.getElementById("resident-entry-request-fields").hidden = false;
  setupVehicleUpload(false);
  document.getElementById("resident-entry-title").textContent = config.title;
  document.getElementById("resident-entry-context").textContent = "Đang tải hồ sơ của bạn…";
  document.getElementById("resident-entry-fields").replaceChildren();
  document.getElementById("resident-entry-review").hidden = true;
  document.getElementById("resident-entry-fields").hidden = false;
  document.getElementById("resident-entry-back").hidden = true;
  document.getElementById("resident-entry-status").textContent = "";
  document.getElementById("resident-entry-notice").classList.remove("ownership-warning");
  document.getElementById("resident-entry-submit").textContent = "Xem lại thông tin";
  document.getElementById("resident-entry-submit").disabled = true;
  document.getElementById("resident-entry-consent").required = false;
  setEntryError();
  if (!residentEntryDialog.open) residentEntryDialog.showModal();
  try {
    const profile = await api(residentPath(""));
    if (!isSessionCurrent(generation) || loadId !== residentEntryLoadId || !residentEntryDialog.open) return;
    residentEntryState.profile = profile;
    if (["temporary_residency", "move_in_schedule", "vehicle_registration"].includes(type)) {
      const progress = await api(residentPath("/progress"));
      if (!isSessionCurrent(generation) || loadId !== residentEntryLoadId || !residentEntryDialog.open) return;
      synchronizeProcedureCards(progress);
      residentEntryState.task = progress.all_tasks.find(task => task.code === type);
      document.getElementById("resident-entry-need").hidden = !residentEntryState.task?.can_mark_not_needed;
    }
    let formConfig = config;
    residentOwnershipState = { ready: true, profile };
    synchronizeOwnershipUi();
    if (["temporary_residency", "account_registration"].includes(type) && profile.resident_type !== "owner") { residentEntryDialog.close(); showOwnershipWarning("Chỉ chủ sở hữu mới được quyền đăng ký tính năng này"); return; }
    if (type === "account_registration") {
      if (profile.resident_type !== "owner") { residentEntryDialog.close(); showOwnershipWarning("Chỉ chủ sở hữu mới được quyền đăng ký tính năng này"); return; }
      const household = await api(residentPath("/household"));
      if (!isSessionCurrent(generation) || loadId !== residentEntryLoadId || !residentEntryDialog.open) return;
      const people = household.account_candidates || [];
      if (!people.length) {
        document.getElementById("resident-entry-notice").textContent = "Chưa có người thuê hoặc thành viên gia đình đủ điều kiện cấp tài khoản. Đăng ký người cư trú và chờ BQL duyệt trước; người đã có tài khoản không cần đăng ký lại.";
        document.getElementById("form-autofill").hidden = true;
        return;
      }
      formConfig = { ...config, fields: config.fields.map(field => ({ ...field, options: [["", "Chọn người cư trú"], ...people.map(person => [person.household_member_id, `${person.person_name} · ${person.resident_type === "tenant" ? "Người thuê" : "Thành viên gia đình"}`])] })) };
    }
    if (type === "ownership" && profile.ownership_confirmed_at) {
      document.getElementById("resident-entry-context").textContent = `${profile.full_name} · ${profile.building} · ${profile.apartment} · ${profile.site_name}`;
      document.getElementById("resident-entry-notice").textContent = "Thông tin cư trú đã được bạn xác nhận. Thông tin đã khóa và không thể tự chỉnh sửa.";
      document.getElementById("resident-entry-fields").replaceChildren();
      document.getElementById("form-autofill").hidden = true;
      document.getElementById("resident-entry-submit").disabled = true;
      return;
    }
    if (type === "verify_residency") {
      const locations = await api("/locations");
      if (!isSessionCurrent(generation) || loadId !== residentEntryLoadId || !residentEntryDialog.open) return;
      const choices = locations.items.filter((item) => item.site_id === profile.site_id);
      residentEntryState.locations = choices;
      formConfig = { ...config, fields: config.fields.map((field) => field.key === "target_location" ? { ...field, options: choices.map((item) => [JSON.stringify({ apartment: item.apartment, building: item.building }), `${item.building ? item.building + " · " : ""}Căn hộ ${item.apartment}`]), value: JSON.stringify({ apartment: profile.apartment, building: profile.building || "" }) } : field) };
    }
    residentEntryState.config = formConfig;
    document.getElementById("resident-entry-context").textContent = `${profile.full_name} · Căn hộ ${profile.apartment} · ${profile.site_name}. Khu và căn hộ do hệ thống cấp, không thay đổi trong form.`;
    document.getElementById("resident-entry-notice").textContent = type === "profile"
      ? "Thông tin dùng để cá nhân hóa checklist. Chỉ lưu khi bạn kiểm tra và xác nhận. Việc khai báo hồ sơ không đồng nghĩa Ban quản lý đã xác nhận cư trú."
      : residentProcedureNotices[type] || "Kiểm tra thông tin và xác nhận gửi Ban quản lý để được xem xét.";
    renderEntryFields(formConfig, profile);
    if (type === "ownership") {
      const owner = profile.resident_type === "owner";
      document.getElementById("form-autofill").hidden = true;
      residentEntryForm.elements.namedItem("full_name").value = profile.full_name;
      document.getElementById("resident-entry-title").textContent = owner ? config.title : "Xác nhận thông tin cư trú";
      document.getElementById("resident-entry-context").textContent = `${profile.site_name} · Tư cách được cấp: ${({ owner: "Chủ sở hữu", tenant: "Người thuê", family_member: "Thành viên gia đình" })[profile.resident_type]}`;
      document.getElementById("resident-entry-notice").textContent = "Hãy kiểm tra kỹ trước khi xác nhận. Họ tên, tư cách, khu, tòa và căn hộ do BQL cấp, bạn không thể tự sửa. Nếu có sai sót, chưa xác nhận và liên hệ BQL để đối chiếu, xác nhận điều chỉnh. Việc lưu không thay thế chứng nhận quyền sở hữu pháp lý.";
      document.getElementById("resident-entry-notice").classList.add("ownership-warning");
    } else document.getElementById("resident-entry-notice").classList.remove("ownership-warning");
    if (type === "account_registration") {
      document.getElementById("form-autofill").hidden = true;
      document.getElementById("resident-entry-notice").textContent = "Chọn người thuê hoặc người nhà đã được BQL duyệt cư trú tại căn hộ của bạn. Yêu cầu chỉ gửi sau khi xác nhận; BQL duyệt và bàn giao tài khoản riêng, không chia sẻ mật khẩu của bạn.";
    }
    if (type === "profile") {
      document.getElementById("resident-entry-notice").textContent = "Họ tên, tư cách, khu, tòa và căn hộ do BQL cấp, chỉ được xem. Muốn điều chỉnh, cần BQL đối chiếu và xác nhận. Bạn chỉ cập nhật nhu cầu và thông tin sinh hoạt tại đây.";
    }
    if (type === "general_support" && questionDraft) {
      residentEntryForm.elements.namedItem("details").value = questionDraft.slice(0, 700);
      residentEntryForm.elements.namedItem("support_category").value = "Hỏi thủ tục";
      document.getElementById("resident-entry-notice").textContent = "Câu hỏi đã được soạn vào ticket hỗ trợ. Bạn có thể chỉnh nội dung; chỉ gửi đến BQL sau khi xem lại và xác nhận. Phản hồi xuất hiện trong Ticket đã gửi và Thông báo BQL.";
    }
    setupVehicleUpload(type === "vehicle_registration");
    residentEntryState.notice = document.getElementById("resident-entry-notice").textContent;
    document.getElementById("resident-entry-submit").disabled = false;
    document.querySelector("#resident-entry-fields input, #resident-entry-fields select, #resident-entry-fields textarea")?.focus();
  } catch (error) {
    if (isSessionCurrent(generation) && loadId === residentEntryLoadId) setEntryError(error.message);
  }
}

function setEntryNeed(noNeed) {
  const state = residentEntryState;
  if (!state?.profile || state.submitting || state.review) return;
  state.noNeed = Boolean(noNeed && state.task?.can_mark_not_needed);
  const fields = document.getElementById("resident-entry-request-fields");
  fields.disabled = state.noNeed;
  fields.hidden = state.noNeed;
  if (state.noNeed) {
    resetFormAutofill();
    if (state.type === "vehicle_registration") setupVehicleUpload(false);
  } else if (state.type === "vehicle_registration") setupVehicleUpload(true);
  document.getElementById("resident-entry-notice").textContent = state.noNeed
    ? "Chưa có nhu cầu: chỉ ghi nhận hoàn thành checklist sau khi bạn xem lại và xác nhận. Không tạo ticket, không gửi hồ sơ tới BQL."
    : state.notice;
  setEntryError();
}

document.getElementById("resident-entry-need").addEventListener("change", event => {
  if (event.target.name === "procedure_need") setEntryNeed(event.target.value === "none");
});

function reviewResidentEntry() {
  const state = residentEntryState;
  document.getElementById("form-autofill").hidden = true;
  const config = state.config || residentEntryCatalog[state.type];
  const values = state.noNeed ? {} : Object.fromEntries(config.fields.map((field) => [field.key, residentEntryForm.elements.namedItem(field.key).value]));
  state.payload = state.noNeed ? null : entryPayload(state.type, values);
  const summary = document.getElementById("resident-entry-summary");
  summary.replaceChildren();
  const summaryFields = state.noNeed ? [{ key: "need", label: residentEntryCatalog[state.type].title }] : config.fields;
  summaryFields.forEach((field) => {
    const title = document.createElement("dt");
    const value = document.createElement("dd");
    title.textContent = field.label;
    value.textContent = state.noNeed ? "Chưa có nhu cầu · Hoàn thành checklist, không gửi ticket" : field.options?.find(([option]) => option === values[field.key])?.[1] || values[field.key] || "Không cung cấp";
    summary.append(title, value);
  });
  if (state.type === "vehicle_registration" && !state.noNeed) {
    const title = document.createElement("dt");
    const value = document.createElement("dd");
    title.textContent = "Giấy đăng ký xe";
    value.textContent = `${vehicleDocumentFile?.name || "Chưa có ảnh"} · BQL đối chiếu trước khi duyệt`;
    summary.append(title, value);
    document.getElementById("vehicle-upload").hidden = true;
  }
  document.getElementById("resident-entry-fields").hidden = true;
  document.getElementById("resident-entry-need").hidden = true;
  document.getElementById("resident-entry-review").hidden = false;
  document.getElementById("resident-entry-back").hidden = false;
  const consent = document.getElementById("resident-entry-consent");
  consent.checked = false;
  consent.required = true;
  document.getElementById("resident-entry-consent-copy").textContent = state.type === "profile"
    ? "Tôi đã kiểm tra, đồng ý lưu thông tin và cập nhật checklist của mình."
    : "Tôi đã kiểm tra và đồng ý gửi những thông tin hiển thị này đến Ban quản lý của khu.";
  if (state.type === "ownership") document.getElementById("resident-entry-consent-copy").textContent = "Tôi đã kiểm tra kỹ họ tên, tư cách cư trú và căn hộ BQL đã cấp; hiểu rằng thông tin sẽ bị khóa, không thể tự thay đổi sau khi xác nhận.";
  if (state.noNeed) document.getElementById("resident-entry-consent-copy").textContent = "Tôi xác nhận hiện chưa có nhu cầu. Hệ thống chỉ hoàn thành mục checklist này, không gửi yêu cầu tới BQL. Khi cần, tôi có thể đăng ký sau.";
  document.getElementById("resident-entry-review").classList.toggle("ownership-warning", state.type === "ownership");
  document.getElementById("resident-entry-submit").textContent = state.type === "profile" ? "Xác nhận lưu hồ sơ" : "Xác nhận gửi BQL";
  if (state.type === "ownership") document.getElementById("resident-entry-submit").textContent = "Xác nhận và khóa thông tin";
  if (state.noNeed) document.getElementById("resident-entry-submit").textContent = "Xác nhận chưa có nhu cầu";
  state.review = true;
  consent.focus();
}

document.getElementById("resident-entry-back").addEventListener("click", () => {
  if (residentEntryState?.submitting) return;
  residentEntryState.review = false;
  residentEntryState.payload = null;
  residentEntryState.key = crypto.randomUUID();
  document.getElementById("resident-entry-need").hidden = !residentEntryState.task?.can_mark_not_needed;
  document.getElementById("resident-entry-fields").hidden = false;
  document.getElementById("form-autofill").hidden = ["ownership", "account_registration", "vehicle_registration", "resident_card", "move_in_schedule"].includes(residentEntryState.type);
  document.getElementById("resident-entry-review").hidden = true;
  document.getElementById("vehicle-upload").hidden = residentEntryState.noNeed || residentEntryState.type !== "vehicle_registration";
  document.getElementById("resident-entry-consent").checked = false;
  document.getElementById("resident-entry-consent").required = false;
  document.getElementById("resident-entry-back").hidden = true;
  document.getElementById("resident-entry-submit").textContent = "Xem lại thông tin";
  setEntryError();
});

residentEntryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const state = residentEntryState;
  if (!state?.profile || state.submitting || !isSessionCurrent(state.generation)) return;
  if (!residentEntryForm.reportValidity()) return;
  setEntryError();
  if (!state.review) {
    if (state.type === "resident_card") {
      const date = residentEntryForm.elements.namedItem("pickup_date").value;
      const time = residentEntryForm.elements.namedItem("pickup_time").value;
      if (Boolean(date) !== Boolean(time)) { setEntryError("Vui lòng chọn đủ ngày và giờ mong muốn nhận thẻ, hoặc để trống cả hai nếu chưa xác định lịch."); return; }
    }
    reviewResidentEntry(); return;
  }
  if (!document.getElementById("resident-entry-consent").checked) return;
  state.submitting = true;
  const submit = document.getElementById("resident-entry-submit");
  submit.disabled = true;
  document.getElementById("resident-entry-back").disabled = true;
  document.getElementById("resident-entry-close").disabled = true;
  document.getElementById("resident-entry-need").disabled = true;
  submit.textContent = "Đang lưu…";
  try {
    const result = state.noNeed
      ? await api(residentPath(`/checklist/${state.type}/no-need`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true, expected_updated_at: state.task.updated_at, expected_profile_version: state.profile.profile_version }) })
      : state.type === "ownership"
      ? await api(residentPath("/ownership/confirm"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...state.payload, expected_profile_version: state.profile.profile_version, confirmed: true, immutable_acknowledged: true }) })
      : state.type === "profile"
      ? await api(residentPath(""), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state.payload) })
      : state.type === "vehicle_registration" ? await submitVehicleRequest(state)
      : await api("/requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...state.payload, request_type: state.type, idempotency_key: state.key }) });
    if (!isSessionCurrent(state.generation) || state !== residentEntryState) return;
    if (result.profile) { residentOwnershipState = { ready: true, profile: result.profile }; synchronizeOwnershipUi(); }
    if (result.progress) synchronizeProcedureCards(result.progress);
    if (typeof requestDraftChatState !== "undefined" && requestDraftChatState?.formEntry === state) clearRequestDraftChat();
    residentEntryDialog.close();
    const targetTab = state.noNeed || state.type === "ownership" ? "progress" : state.type === "profile" ? "household" : "tickets";
    openResidentTab(targetTab);
    const notice = document.createElement("p");
    notice.className = "entry-success";
    notice.setAttribute("role", "status");
    notice.textContent = state.noNeed ? "Đã hoàn tất mục checklist: Chưa có nhu cầu. Không tạo ticket và không gửi yêu cầu tới BQL. Bạn có thể đăng ký khi cần."
      : state.type === "ownership" ? "Đã xác nhận thông tin cư trú. Bạn có thể sử dụng các thủ tục khác."
      : state.type === "profile" ? "Đã lưu thông tin cư dân và cập nhật checklist. Hồ sơ này chưa thay thế xác nhận của Ban quản lý."
      : `${result.duplicate ? "Yêu cầu đã có" : "Đã gửi yêu cầu"}: ${result.request_id}. Trạng thái: ${requestStatusLabels[result.status] || "Chờ Ban quản lý xem xét"}. Việc gửi hồ sơ không đồng nghĩa đã được cấp thẻ hoặc hoàn tất thủ tục.`;
    const panel = document.getElementById(targetTab);
    panel.querySelector(".entry-success")?.remove();
    panel.prepend(notice);
    void loadHousehold(true);
    if (state.type === "ownership") void startAuthorizedBackgroundTasks();
    void refreshChecklistCards();
  } catch (error) {
    if (isSessionCurrent(state.generation) && state === residentEntryState) {
      setEntryError(error.message);
      document.getElementById("resident-entry-status").textContent = "Thông tin vẫn được giữ trong form. Kiểm tra lỗi rồi xác nhận lại; không bấm mở form mới nếu đang thử lại yêu cầu cũ.";
    }
  } finally {
    state.submitting = false;
    submit.disabled = false;
    submit.textContent = state.noNeed ? "Xác nhận chưa có nhu cầu" : state.type === "ownership" ? "Xác nhận và khóa thông tin" : state.type === "profile" ? "Xác nhận lưu hồ sơ" : "Xác nhận gửi BQL";
    document.getElementById("resident-entry-back").disabled = false;
    document.getElementById("resident-entry-close").disabled = false;
    document.getElementById("resident-entry-need").disabled = false;
  }
});

document.getElementById("resident-entry-close").addEventListener("click", () => { if (!residentEntryState?.submitting) residentEntryDialog.close(); });
residentEntryDialog.addEventListener("cancel", (event) => { if (residentEntryState?.submitting) event.preventDefault(); });
residentEntryDialog.addEventListener("close", () => {
  residentEntryLoadId += 1;
  autofillLoadId += 1;
  if (typeof syncRequestDraftFromEntry === "function") syncRequestDraftFromEntry();
  const target = residentEntryState?.returnFocus;
  if (target?.getClientRects().length && !target.closest('[aria-hidden="true"]')) target.focus();
});

let regulationsLoadId = 0;
function documentPartition(item) {
  if (item.site_id === "national" || item.source_type === "official_public_document") return "public_law";
  if (item.source_type === "demo_bql") return "demo_bql";
  return item.source_type === "building_policy" ? "building_policy" : "unverified";
}

function renderKnowledgeGuides(data) {
  const guides = (data.guides || []).map(guide => {
    const form = Object.hasOwn(residentEntryCatalog, guide.form) ? guide.form : "";
    const tab = ["agent", "tickets", "regulations", "household", "notifications"].includes(guide.tab) ? guide.tab : "";
    const action = form
      ? `<button type="button" data-knowledge-form="${escapeHtml(form)}">Mở biểu mẫu</button>`
      : tab ? `<button type="button" data-knowledge-tab="${escapeHtml(tab)}">Mở khu vực liên quan</button>` : "";
    return `<details class="regulation-item"><summary>${escapeHtml(guide.title)}</summary><div class="regulation-body"><ol>${(guide.steps || []).map(step => `<li>${escapeHtml(step)}</li>`).join("")}</ol>${action}</div></details>`;
  }).join("");
  return `<details class="reading-help"><summary><span class="reading-help-icon" aria-hidden="true"><svg class="ui-icon" viewBox="0 0 24 24"><use href="/static/icons.svg#book" /></svg></span><span class="reading-help-copy"><strong>Hướng dẫn thao tác trong ứng dụng</strong><small>Mở để xem từng bước và đi đến chức năng cần dùng</small></span></summary><div class="reading-help-content"><p>Các bước dựa trên biểu mẫu và chức năng MoveInMate đang chạy.</p><div class="reading-guides">${guides}</div></div></details>`;
}

async function loadRegulations() {
  if (!authReady || !hasRole("resident")) return;
  const target = document.getElementById("regulations-list");
  const generation = authGeneration;
  const loadId = ++regulationsLoadId;
  renderReadingActions([], target);
  target.innerHTML = '<div class="request-loading" role="status"><i></i><i></i><i></i><span>Đang tải tài liệu…</span></div>';
  target.setAttribute("aria-busy", "true");
  document.getElementById("reading-empty").hidden = true;
  document.getElementById("reading-confirm-selected").disabled = true;
  try {
    const data = await api(residentPath("/regulations"));
    if (!isSessionCurrent(generation) || loadId !== regulationsLoadId) return;
    const guidesHost = document.getElementById("knowledge-guides");
    if (guidesHost) {
      guidesHost.innerHTML = renderKnowledgeGuides(data);
      guidesHost.querySelectorAll("[data-knowledge-form], [data-knowledge-tab]").forEach(button => {
        button.addEventListener("click", () => handleUIAction(button.dataset.knowledgeForm ? "open_form" : "open_tab", {
          form: button.dataset.knowledgeForm, tab: button.dataset.knowledgeTab,
        }));
      });
    }
    const orderedDocuments = [...data.items].sort((first, second) => documentPartition(first).localeCompare(documentPartition(second)) || first.title.localeCompare(second.title, "vi"));
    const groupedDocuments = orderedDocuments.map((item) => {
      const labels = { building_policy: "Quy định riêng của khu", demo_bql: "Tài liệu minh họa", public_law: "Pháp luật tham khảo", unverified: "Tài liệu chưa xác minh" };
      const notice = item.legal_status === "not_verified"
        ? "Nguồn pháp luật công khai; chưa kiểm tra đầy đủ hiệu lực/sửa đổi, không phải nội quy riêng của khu."
        : item.source_type === "demo_bql" ? "Tài liệu minh họa của bản demo, không phải chính sách đã xác minh của một tòa nhà thật."
        : "Hiệu lực " + (item.effective_date || "chưa xác minh");
      const source = officialSourceLink(item.source_url, "Xem nguồn gốc tài liệu");
      const body = item.body.split(/\n\n+/).map((part) => part.startsWith("#")
        ? "<h4>" + escapeHtml(part.replace(/^#+\s*/, "")) + "</h4>"
        : "<p>" + escapeHtml(part) + "</p>").join("");
      return '<details class="regulation-item"><summary><span class="regulation-icon" aria-hidden="true"><svg class="ui-icon" viewBox="0 0 24 24"><use href="/static/icons.svg#book" /></svg></span><span class="regulation-title">' + escapeHtml(item.title) +
        '</span><span class="regulation-meta"><small>' + escapeHtml(item.version || "Chưa ghi phiên bản") + '</small><span class="regulation-source">' + escapeHtml(labels[documentPartition(item)]) + '</span><span class="regulation-read-status">' + (item.acknowledged_at ? "Đã đọc" : "Chưa đọc") + '</span></span></summary><div class="regulation-body"><p>' +
        escapeHtml(notice) + '</p><p>' + source + '</p>' + body + '</div></details>';
    }).join("");
    target.innerHTML = groupedDocuments || "<p>Hướng dẫn thao tác có ở mục trên. BQL có thể bổ sung văn bản riêng của khu khi sẵn sàng.</p>";
    if (typeof renderReadingActions === "function") renderReadingActions(orderedDocuments, target);
  } catch (error) {
    if (!isSessionCurrent(generation) || loadId !== regulationsLoadId) return;
    target.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  } finally {
    if (loadId === regulationsLoadId) target.removeAttribute("aria-busy");
  }
}
document.getElementById("refresh-regulations").addEventListener("click", loadRegulations);

async function loadResidentTickets() {
  if (!authReady || !hasRole("resident")) return;
  const list = document.getElementById("resident-ticket-list");
  const loadId = ++residentTicketPage.loadId;
  const generation = authGeneration;
  setTicketLoading("resident");
  list.innerHTML = '<p>Đang tải ticket…</p>';
  try {
    const data = await api(residentPath(`/requests?${ticketQuery("resident")}`));
    if (!isSessionCurrent(generation) || loadId !== residentTicketPage.loadId) return;
    if (alignTicketPage("resident", data.total)) return await loadResidentTickets();
    updateTicketPagination("resident", data);
    if (typeof updateTicketDeleteCount === "function") updateTicketDeleteCount("tickets", data.counts_by_status);
    if (!data.requests.length) {
      list.innerHTML = '<div class="empty-state"><strong>Không có ticket phù hợp</strong><p>Thử đổi từ khóa, khoảng ngày hoặc loại yêu cầu. Bạn có thể xóa bộ lọc để xem lại tất cả.</p></div>';
      return;
    }
    list.innerHTML = data.requests.map((item) => {
      const canCancel = ["submitted", "in_review", "needs_info", "approved"].includes(item.status);
      const history = (item.timeline || []).map((step, index, timeline) => {
        const timestamp = new Date(step.created_at);
        const validTime = !Number.isNaN(timestamp.getTime());
        const time = validTime ? `<time datetime="${escapeHtml(step.created_at)}">${escapeHtml(timestamp.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" }))}</time>` : "";
        const current = index === timeline.length - 1;
        return `<li class="ticket-timeline-step${current ? " current" : ""}" data-status="${escapeHtml(step.status)}"${current ? ' aria-current="step"' : ""}><div class="ticket-timeline-heading"><strong>${escapeHtml(requestStatusLabels[step.status] || step.status)}</strong>${time}</div>${step.reason ? `<p>${escapeHtml(step.reason)}</p>` : ""}</li>`;
      }).join("");
      const editable = ["submitted", "in_review", "needs_info"].includes(item.status);
      const moreInfo = editable ? `<button class="ticket-chat-action" type="button" data-request-id="${escapeHtml(item.request_id)}" aria-controls="ticket-correction-dialog">Bổ sung thông tin</button>` : "";
      return `<article class="request-card resident-ticket status-border-${escapeHtml(item.status)}"><div class="ticket-overview"><header class="request-card-header"><div class="request-identity"><span class="request-type-icon">${requestTypeIcon(item.request_type)}</span><div><span class="request-kind">${escapeHtml(requestTypeLabels[item.request_type] || item.request_type)}</span><strong>${escapeHtml(item.request_id)}</strong></div></div><span class="request-status status-${escapeHtml(item.status)}">${escapeHtml(requestStatusLabels[item.status] || item.status)}</span></header>${requestDetails(item, true)}${internetTicketNotice(item, "resident")}${item.status === "completed" ? (item.decision_reason ? "" : `<div class="ticket-completed-mark" role="img" aria-label="${escapeHtml(requestNextSteps.completed)}"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 12 4 4 8-8"/></svg></div>`) : `<p class="ticket-next-step"><strong>Việc tiếp theo:</strong> ${escapeHtml(requestNextSteps[item.status] || "Xem lịch sử xử lý để biết trạng thái mới nhất.")}</p>`}</div><details class="ticket-timeline"><summary><span class="ticket-timeline-title">Lịch sử xử lý</span><span class="ticket-timeline-count">${item.timeline?.length || 0}</span></summary>${history ? `<ol>${history}</ol>` : '<p>Chưa có cập nhật trạng thái.</p>'}</details><div class="ticket-footer"><div class="ticket-actions">${moreInfo}${canCancel ? `<button class="cancel-ticket" type="button" data-request-id="${escapeHtml(item.request_id)}">Hủy ticket</button>` : ""}</div></div></article>`;
    }).join("");
    addVehicleDocumentActions(list, data.requests);
    if (typeof addTicketDeliveryActions === "function") addTicketDeliveryActions(list, data.requests, "tickets");
    list.querySelectorAll(".resident-ticket").forEach((card, index) => {
      const created = document.createElement("p");
      created.className = "request-meta";
      created.textContent = `Gửi lúc ${new Date(data.requests[index].created_at).toLocaleString("vi-VN")}`;
      card.querySelector(".request-card-header").after(created);
    });
  } catch (error) {
    if (isSessionCurrent(generation) && loadId === residentTicketPage.loadId) list.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

function managementNotificationText(item) {
  const ticket = `**${item.request_id}**`;
  const kind = requestTypeLabels[item.request_type] || "yêu cầu";
  const reason = item.reason ? ` Ghi chú: ${item.reason}.` : "";
  if (item.status === "in_review") return `Ban quản lý đã tiếp nhận ${kind.toLowerCase()} ${ticket}.${reason} Bạn mở **Ticket đã gửi** để theo dõi.`;
  if (item.status === "needs_info") return `Ban quản lý cần bạn bổ sung cho ${kind.toLowerCase()} ${ticket}.${reason} Mở **Ticket đã gửi**, chọn **Thêm thông tin** hoặc **Gửi lại** để tôi giúp bạn cập nhật đúng ticket này.`;
  if (item.status === "rejected") return `Ban quản lý đã từ chối ${kind.toLowerCase()} ${ticket}.${reason} Bạn mở **Ticket đã gửi** để xem lý do và hỏi tôi nếu cần hỗ trợ.`;
  if (item.request_type === "temporary_residency" && item.status === "approved") return `Ban quản lý đã duyệt đăng ký tạm trú ${ticket}.${reason} Số người cư trú trong ứng dụng đã cập nhật. Bạn mở **Quản lý căn hộ** để xem; mục **Ticket đã gửi** cho biết tiến độ tiếp theo.`;
  if (item.request_type === "temporary_residency" && item.status === "completed") return `Ban quản lý đã đánh dấu hoàn tất đăng ký tạm trú ${ticket}.${reason} Bạn mở **Quản lý căn hộ** để xem số người và người đã đăng ký.`;
  if (item.status === "approved") return `Ban quản lý đã duyệt ${kind.toLowerCase()} ${ticket}.${reason} Bạn mở **Ticket đã gửi** để theo dõi đến khi hoàn tất.`;
  return `Ban quản lý đã đánh dấu hoàn tất ${kind.toLowerCase()} ${ticket}.${reason} Bạn mở **Ticket đã gửi** để kiểm tra kết quả.`;
}

async function pollManagementNotifications() {
  if (!authReady || !hasRole("resident") || notificationPollInProgress) return;
  notificationPollInProgress = true;
  const generation = authGeneration;
  try {
    await loadNotificationFeed({ silent: true });
    if (isSessionCurrent(generation) && !document.hidden && document.getElementById("household").classList.contains("active")) await loadHousehold(true);
  }
  finally { if (generation === authGeneration) notificationPollInProgress = false; }
}

document.getElementById("management-notice").addEventListener("click", () => openResidentTab("notifications"));
document.getElementById("resident-ticket-list").addEventListener("click", async (event) => {
  const chatButton = event.target.closest(".ticket-chat-action");
  if (chatButton) { void openTicketCorrection(chatButton.dataset.requestId, chatButton); return; }
  const button = event.target.closest(".cancel-ticket");
  if (!button || !window.confirm(`Hủy ticket ${button.dataset.requestId}? Ban quản lý sẽ thấy trạng thái đã hủy.`)) return;
  button.disabled = true;
  try {
    await api(`${residentPath("/requests")}/${encodeURIComponent(button.dataset.requestId)}/cancel`, { method: "POST" });
    await loadResidentTickets();
  } catch (error) {
    showTransientNotice(error.message);
    button.disabled = false;
  }
});

function displayDate(value) {
  if (!value) return "Chưa đặt";
  const parts = value.split("-");
  return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : value;
}

function householdDeleteAction(item, action, name) {
  if (!item.can_remove) return "";
  return `<button class="household-delete" type="button" data-household-action="${escapeHtml(action)}" data-request-id="${escapeHtml(item.request_id)}" data-updated-at="${escapeHtml(item.updated_at)}" data-name="${escapeHtml(name)}" aria-label="Delete ${escapeHtml(name)}"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><use href="/static/icons.svg?v=20261016-household-actions-v1#trash" /></svg><span>Delete</span></button>`;
}

async function loadHousehold(silent = false) {
  if (!authReady || !hasRole("resident")) return;
  const generation = authGeneration;
  const loadId = ++householdLoadId;
  const summary = document.getElementById("household-summary");
  if (!silent) summary.textContent = "Đang tải thông tin căn hộ…";
  try {
    const data = await api(residentPath("/household"));
    if (!isSessionCurrent(generation) || loadId !== householdLoadId) return;
    summary.innerHTML = `<strong>Căn hộ ${escapeHtml(data.apartment)}</strong><span>Cập nhật theo ngày ${escapeHtml(displayDate(data.as_of_date))}</span>`;
    const occupants = data.occupants.temporary_residency_requests.filter(item => !["approved", "completed"].includes(item.status));
    const people = data.occupants.people || [];
    document.getElementById("household-occupants").innerHTML = `
      <p class="household-big-number">${data.occupants.recorded_count} <small>người theo hồ sơ hiện tại</small></p>
      <p class="household-hint">Số người gồm hồ sơ khai báo ban đầu và người đăng ký thêm đã được BQL duyệt; ticket mới gửi chưa làm tăng số người.</p>
      ${people.length ? `<ul class="household-roster">${people.map(person => `<li><span class="household-person-icon" aria-hidden="true"><svg class="ui-icon" viewBox="0 0 24 24"><use href="/static/icons.svg#users" /></svg></span><div class="household-person-copy"><strong>${escapeHtml(person.person_name)}</strong><span> — ${escapeHtml(ticketResidentTypeLabels[person.resident_type] || "Cư dân")}${person.resident_type !== "owner" ? ` · ${person.stay_start ? `Vào từ ${escapeHtml(displayDate(person.stay_start))}` : "Chưa ghi nhận ngày vào"}` : ""}</span></div>${householdDeleteAction(person, "remove-person", person.person_name)}</li>`).join("")}</ul>` : '<p class="household-hint">Chưa có hồ sơ cư trú được xác nhận.</p>'}
      ${data.can_add_occupants ? `
      ${occupants.length ? `<p class="household-hint">Yêu cầu cư trú chưa được duyệt</p><ul class="household-list">${occupants.map((item) => `<li><span class="household-person-icon" aria-hidden="true"><svg class="ui-icon" viewBox="0 0 24 24"><use href="/static/icons.svg#users" /></svg></span><div class="household-person-copy"><strong>${escapeHtml(item.person_name || "Chưa rõ tên")}</strong><span class="household-person-state">${escapeHtml(requestStatusLabels[item.status] || item.status)}${item.stay_start ? ` · từ ${escapeHtml(displayDate(item.stay_start))}` : ""}</span></div>${householdDeleteAction(item, "remove-person", item.person_name || "người cư trú")}</li>`).join("")}</ul>` : ""}
      <button class="household-chat-action" type="button" data-chat-prompt="Tôi muốn đăng ký tạm trú cho người ở cùng căn hộ.">Đăng ký tạm trú với trợ lý</button>` : '<p class="household-hint">Thông tin do Ban quản lý cấp và đã được xác nhận. Chỉ chủ sở hữu được đăng ký thêm người cư trú.</p>'}`;

    const vehicleTypeLabels = { car: "Ô tô", motorbike: "Xe máy" };
    document.getElementById("household-vehicles").innerHTML = [...data.vehicles, ...data.pending_vehicles].map(item => `<article class="vehicle-card status-border-${escapeHtml(item.status)}"><div class="household-vehicle-copy"><strong>${escapeHtml(vehicleTypeLabels[item.vehicle_type] || item.vehicle_type || "Phương tiện")} · ${escapeHtml(item.vehicle_plate || "Chưa có biển số")}</strong><p>Người đăng ký: <strong>${escapeHtml(item.owner_name)}</strong> · ${escapeHtml(residentTypeLabels[item.owner_resident_type] || item.owner_resident_type)}</p><small>${escapeHtml(requestStatusLabels[item.status] || item.status)} · ${escapeHtml(item.request_id)}</small></div>${householdDeleteAction(item, "remove-vehicle", item.vehicle_plate || "phương tiện")}</article>`).join("") || '<p class="household-hint">Chưa có phương tiện. Chọn Thêm phương tiện để nhập hồ sơ và đính kèm giấy đăng ký xe.</p>';

    const internetLabel = data.internet.request_id ? (requestStatusLabels[data.internet.status] || data.internet.status) : data.internet.needed ? "Chưa gửi đăng ký" : "Chưa khai báo nhu cầu";
    document.getElementById("household-internet").innerHTML = `<p class="household-big-number internet-state">${escapeHtml(internetLabel)}</p><p class="household-hint">${data.internet.request_id ? `Ticket ${escapeHtml(data.internet.request_id)} đang được theo dõi trong mục Ticket đã gửi.` : "Nếu cần lắp internet, hãy gửi yêu cầu để BQL xem xét."}</p><div class="household-internet-actions"><button class="household-chat-action" type="button" data-chat-prompt="Tôi muốn đăng ký internet cho căn hộ.">Hỏi trợ lý về internet</button>${data.internet.can_cancel ? `<button class="household-delete" type="button" data-household-action="cancel-internet" data-request-id="${escapeHtml(data.internet.request_id)}" data-updated-at="${escapeHtml(data.internet.updated_at)}" data-name="dịch vụ internet"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><use href="/static/icons.svg?v=20261016-household-actions-v1#trash" /></svg><span>Hủy dịch vụ internet</span></button>` : ""}</div>`;
    if (!data.can_add_occupants) document.querySelector("#household-internet .household-chat-action")?.remove();

  } catch (error) {
    if (!silent && isSessionCurrent(generation) && loadId === householdLoadId) summary.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

document.getElementById("household").addEventListener("click", async (event) => {
  const remove = event.target.closest("[data-household-action]");
  if (remove) {
    const action = remove.dataset.householdAction;
    if (!["remove-person", "remove-vehicle", "cancel-internet"].includes(action) || remove.disabled) return;
    const generation = authGeneration;
    const message = action === "cancel-internet"
      ? "Hủy dịch vụ internet trong ứng dụng? Ticket và lịch sử vẫn được lưu. Bạn cần liên hệ nhà mạng để xác nhận hủy hợp đồng thực tế."
      : action === "remove-person"
      ? `Delete ${remove.dataset.name} khỏi danh sách căn hộ? Tài khoản web được cấp cho người này (nếu có) sẽ tạm ngừng sử dụng ngay, kể cả phiên đang đăng nhập. Tài khoản, ticket, giấy tờ và lịch sử vẫn được giữ lại.`
      : `Delete ${remove.dataset.name} khỏi danh sách căn hộ? Không cần hủy ticket trước. Ticket, giấy tờ và lịch sử vẫn được lưu để BQL đối chiếu.`;
    if (!window.confirm(message) || !isSessionCurrent(generation)) return;
    const path = `${residentPath("/requests")}/${encodeURIComponent(remove.dataset.requestId)}/${action}`;
    remove.disabled = true;
    try {
      await api(path, { method: "POST", body: JSON.stringify({ confirmed: true, expected_updated_at: remove.dataset.updatedAt }) });
      if (!isSessionCurrent(generation)) return;
      await Promise.all([loadHousehold(), loadProgress(), loadResidentTickets(), loadNotificationFeed({ silent: true })]);
      if (isSessionCurrent(generation)) showTransientNotice(action === "cancel-internet" ? "Đã hủy đăng ký internet trong ứng dụng." : "Đã gỡ khỏi danh sách căn hộ.");
    } catch (error) { if (isSessionCurrent(generation)) { showTransientNotice(error.message); remove.disabled = false; } }
    return;
  }
  const button = event.target.closest(".household-chat-action");
  if (button) sendSuggestedPrompt(button.dataset.chatPrompt);
});

function renderRequestStats(items, counts = null) {
  const stats = [
    { status: "all", label: "Tất cả", count: items.length },
    { status: "submitted", label: "Chờ xử lý", count: items.filter((item) => item.status === "submitted").length },
    { status: "in_review", label: "Đang xem xét", count: items.filter((item) => item.status === "in_review").length },
    { status: "needs_info", label: "Cần bổ sung", count: items.filter((item) => item.status === "needs_info").length },
    { status: "approved", label: "Đã duyệt", count: items.filter((item) => item.status === "approved").length },
    { status: "closed", label: "Đã kết thúc", count: items.filter((item) => ["completed", "rejected", "cancelled_by_resident"].includes(item.status)).length },
    { status: "cancelled_by_resident", label: "Người dùng đã hủy", count: items.filter((item) => item.status === "cancelled_by_resident").length },
  ];
  const stage = ticketStages.manager;
  const selectedStats = stats.filter(item => item.status === "all" || (stage === "finished" ? item.status === "cancelled_by_resident" : !["closed", "cancelled_by_resident"].includes(item.status)));
  if (stage === "finished") selectedStats.splice(1, 0,
    { status: "completed", label: "Hoàn tất", count: items.filter(item => item.status === "completed").length },
    { status: "rejected", label: "Từ chối", count: items.filter(item => item.status === "rejected").length });
  const groupCount = counts ? Object.entries(counts).filter(([status]) => ticketStatusInStage(status, stage)).reduce((total, [, count]) => total + count, 0) : items.length;
  document.getElementById("request-stats").innerHTML = selectedStats.map((item) => `
    <button class="request-stat" type="button" data-status-filter="${item.status}" aria-pressed="false">
      <strong>${counts ? item.status === "all" ? groupCount : item.status === "closed" ? ["completed", "rejected", "cancelled_by_resident"].reduce((total, status) => total + (counts[status] || 0), 0) : counts[item.status] || 0 : item.count}</strong><span>${item.label}</span>
    </button>`).join("");
}

function requestTypeIcon(type) {
  if (type === "vehicle_registration") {
    return '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 11 1.6-4.2A2 2 0 0 1 8.5 5h7a2 2 0 0 1 1.9 1.3L19 11"/><path d="M4 11h16a1 1 0 0 1 1 1v5H3v-5a1 1 0 0 1 1-1ZM5 17v2M19 17v2"/><circle cx="7" cy="14" r="1"/><circle cx="17" cy="14" r="1"/></svg>';
  }
  if (type === "visitor_registration") {
    return '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0M16 8h5M18.5 5.5v5"/></svg>';
  }
  const symbols = {
    resident_card: "card", card_replacement: "card-replacement", temporary_residency: "users",
    verify_residency: "home", move_in_schedule: "moving", handover_inspection: "checklist",
    emergency_contact: "phone", account_registration: "user-key", internet_registration: "wifi",
    general_support: "message",
  };
  if (symbols[type]) return `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><use href="/static/icons.svg?v=20261016-ticket-types#${symbols[type]}" /></svg>`;
  return '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h8M8 12h5M8 16h7"/></svg>';
}

function requestDetails(item, showCompletedMark = false) {
  const details = [];
  const vehicleTypeLabels = { motorbike: "xe máy", car: "ô tô" };
  if (item.payload?.vehicle_type) {
    details.push(["Loại xe", vehicleTypeLabels[item.payload.vehicle_type] || item.payload.vehicle_type]);
  }
  if (item.payload?.vehicle_plate) details.push(["Biển số", item.payload.vehicle_plate]);
  if (item.payload?.visitor_name) details.push(["Khách", item.payload.visitor_name]);
  if (item.payload?.person_name) details.push(["Người tạm trú", item.payload.person_name]);
  if (item.payload?.citizen_id) details.push(["Số CCCD", item.payload.citizen_id]);
  if (item.payload?.stay_start) details.push(["Bắt đầu ở", item.payload.stay_start]);
  if (item.payload?.move_date) details.push(["Ngày chuyển đồ", item.payload.move_date]);
  if (item.payload?.move_time) details.push(["Giờ chuyển đồ", item.payload.move_time]);
  if (item.payload?.pickup_date) details.push(["Ngày mong muốn nhận thẻ", displayDate(item.payload.pickup_date)]);
  if (item.payload?.pickup_time) details.push(["Giờ mong muốn nhận thẻ", item.payload.pickup_time]);
  if (item.payload?.visit_date) details.push(["Ngày đến", item.payload.visit_date]);
  if (item.payload?.reason === "lost_card") details.push(["Lý do", "Mất thẻ cư dân"]);
  if (item.payload?.details) details.push(["Chi tiết", item.payload.details]);
  if (item.decision_reason) details.push(["Ghi chú xử lý", item.decision_reason]);
  return details.length ? `<dl class="request-details">${details.map(([label, value], index) => {
    const completedNote = showCompletedMark && item.status === "completed" && item.decision_reason && index === details.length - 1;
    const mark = completedNote ? `<span class="ticket-completed-mark" role="img" aria-label="${escapeHtml(requestNextSteps.completed)}"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 12 4 4 8-8"/></svg></span>` : "";
    const documentSlot = item.request_type === "vehicle_registration" && label === "Biển số" ? '<div class="vehicle-document-slot"><dt>Giấy tờ đính kèm</dt><dd></dd></div>' : "";
    return `<div${completedNote ? ' class="ticket-completed-note"' : ""}><dt>${escapeHtml(label)}</dt><dd>${completedNote ? `<span>${escapeHtml(String(value))}</span>${mark}` : escapeHtml(String(value))}</dd></div>${documentSlot}`;
  }).join("")}</dl>` : "";
}

function residentProfileDetails(item) {
  const profile = item.resident_profile;
  if (!profile) return "";
  const residentTypes = { owner: "Chủ sở hữu", tenant: "Người thuê", family_member: "Thành viên gia đình" };
  return `<details class="resident-profile"><summary>Xem hồ sơ người gửi</summary><dl class="request-details"><div><dt>Họ tên</dt><dd>${escapeHtml(profile.full_name)}</dd></div><div><dt>Căn hộ</dt><dd>${escapeHtml(profile.apartment)}</dd></div><div><dt>Vai trò</dt><dd>${escapeHtml(residentTypes[profile.resident_type] || profile.resident_type)}</dd></div><div><dt>Số người theo hồ sơ</dt><dd>${escapeHtml(String(profile.family_members))}</dd></div><div><dt>Phương tiện khai báo</dt><dd>${escapeHtml(profile.vehicle)}</dd></div></dl></details>`;
}

function requestMatchesStatus(item, statusFilter) {
  if (statusFilter === "all") return true;
  if (statusFilter === "closed") return ["completed", "rejected", "cancelled_by_resident"].includes(item.status);
  return item.status === statusFilter;
}

function updateRequestFilterState(filteredCount) {
  const typeFilter = document.getElementById("request-type-filter");
  const statusFilter = document.getElementById("request-status-filter");
  const query = document.getElementById("request-search").value.trim();
  document.querySelectorAll(".request-stat").forEach((button) => {
    const active = button.dataset.statusFilter === statusFilter.value;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  document.getElementById("request-result-count").textContent = `${filteredCount} yêu cầu`;
  const filters = [];
  if (typeFilter.value !== "all") filters.push(typeFilter.options[typeFilter.selectedIndex].text);
  if (statusFilter.value !== "all") filters.push(statusFilter.options[statusFilter.selectedIndex].text);
  if (query) filters.push(`“${query}”`);
  const dateFrom = document.getElementById("request-date-from").value;
  const dateTo = document.getElementById("request-date-to").value;
  if (dateFrom) filters.push(`Từ ${displayDate(dateFrom)}`);
  if (dateTo) filters.push(`Đến ${displayDate(dateTo)}`);
  document.getElementById("request-result-summary").textContent = filters.length
    ? filters.join(" · ")
    : "Tất cả yêu cầu";
  document.getElementById("clear-request-filters").disabled = !filters.length;
}

function renderRequests() {
  const list = document.getElementById("request-list");
  const filtered = managementRequests;

  updateRequestFilterState(managementPage.total);

  if (!filtered.length) {
    list.innerHTML = `<div class="empty-state"><span aria-hidden="true"><svg class="ui-icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M8.5 11h5"/></svg></span><strong>Không tìm thấy yêu cầu</strong><p>Thử đổi từ khóa hoặc xóa bộ lọc hiện tại.</p><button class="clear-list-filters" type="button">Xóa bộ lọc</button></div>`;
    return;
  }

  list.innerHTML = filtered.map((item) => {
    const accountTicket = item.request_type === "account_registration";
    const actions = (requestActionMap[item.status] || []).filter(action => !accountTicket || !["approved", "completed"].includes(action.status)).map((action) => `
      <button class="decision ${action.style}" type="button" data-id="${escapeHtml(item.request_id)}" data-status="${action.status}">${action.status === "in_review" && item.payload?.resubmission_count ? "Tiếp nhận lại" : action.label}</button>`).join("");
    const updated = new Date(item.updated_at).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
    const statusClass = item.status.replace(/[^a-z_]/g, "");
    const finishedLabel = item.request_type === "account_registration" && item.status === "approved" ? "Đã duyệt, đang chờ cấp tài khoản" : item.status === "rejected" ? "Yêu cầu đã bị từ chối" : item.status === "cancelled_by_resident" ? "Cư dân đã hủy ticket" : "Yêu cầu đã xử lý xong";
    return `<article class="request-card management-ticket status-border-${statusClass}">
      <div class="ticket-overview">
      <header class="request-card-header">
        <div class="request-identity">
          <span class="request-type-icon">${requestTypeIcon(item.request_type)}</span>
          <div><span class="request-kind">${escapeHtml(requestTypeLabels[item.request_type] || item.request_type)}</span><strong>${escapeHtml(item.request_id)}</strong></div>
        </div>
        <span class="request-status status-${statusClass}">${escapeHtml(requestStatusLabels[item.status] || item.status)}</span>
      </header>
      <div class="request-meta"><span>Cư dân <strong>${escapeHtml(item.resident_id)}</strong></span><span>Cập nhật ${escapeHtml(updated)}</span></div>
      ${requestDetails(item)}
      ${internetTicketNotice(item, "manager")}
      </div>
      ${residentProfileDetails(item)}
      <footer class="request-card-footer">
        <div class="request-actions">${actions || `<span class="no-actions"><i aria-hidden="true">✓</i>${finishedLabel}</span>`}</div>
      </footer>
    </article>`;
  }).join("");
  if (typeof addTicketDeliveryActions === "function") addTicketDeliveryActions(list, filtered, "management");
}

const decisionDialog = document.getElementById("decision-dialog");
const decisionForm = document.getElementById("decision-form");
const decisionReasonWrap = document.getElementById("decision-reason-wrap");
const decisionReason = document.getElementById("decision-reason");
const decisionReasonError = document.getElementById("decision-reason-error");
const decisionConfirm = document.getElementById("decision-confirm");
const decisionCancel = document.getElementById("decision-cancel");

const decisionDialogContent = {
  in_review: {
    title: "Tiếp nhận yêu cầu",
    copy: "Xác nhận Ban quản lý đã tiếp nhận và bắt đầu xem xét yêu cầu này?",
    confirm: "Bắt đầu xem xét",
    icon: "→",
  },
  needs_info: {
    title: "Yêu cầu bổ sung",
    copy: "Ghi rõ thông tin hoặc giấy tờ cư dân cần bổ sung.",
    confirm: "Gửi yêu cầu bổ sung",
    icon: "!",
  },
  approved: {
    title: "Duyệt yêu cầu",
    copy: "Xác nhận hồ sơ hợp lệ và chuyển yêu cầu sang trạng thái đã duyệt?",
    confirm: "Duyệt yêu cầu",
    icon: "✓",
  },
  completed: {
    title: "Hoàn tất yêu cầu",
    copy: "Xác nhận Ban quản lý đã xử lý xong yêu cầu này?",
    confirm: "Đánh dấu hoàn tất",
    icon: "✓",
  },
  rejected: {
    title: "Từ chối yêu cầu",
    copy: "Ghi rõ lý do để cư dân biết vì sao yêu cầu chưa được chấp nhận.",
    confirm: "Xác nhận từ chối",
    icon: "!",
  },
};

function closeDecisionDialog(force = false) {
  if (decisionConfirm.disabled && !force) return;
  if (decisionDialog.open) decisionDialog.close();
  pendingDecision?.trigger?.focus();
  pendingDecision = null;
  decisionReason.value = "";
  decisionReasonError.textContent = "";
}

function decide(requestId, status, trigger) {
  const needsReason = ["needs_info", "rejected"].includes(status);
  const defaultReasons = {
    in_review: "BQL đã tiếp nhận và đang xem xét",
    approved: "BQL xác nhận hồ sơ hợp lệ",
    completed: "BQL xác nhận đã xử lý xong",
  };
  const content = decisionDialogContent[status];
  if (!content) return;
  pendingDecision = { requestId, status, needsReason, defaultReason: defaultReasons[status] || "", trigger };
  document.getElementById("decision-dialog-title").textContent = content.title;
  const item = managementRequests.find(request => request.request_id === requestId);
  document.getElementById("decision-dialog-copy").textContent = status === "completed" && item?.request_type === "internet_registration"
    ? "Chỉ đánh dấu Hoàn tất khi vấn đề đã được giải quyết và được xác nhận giữa BQL với người tư vấn. Bạn xác nhận đã đối chiếu xong?"
    : content.copy;
  document.querySelector(".dialog-icon").textContent = content.icon;
  decisionReasonWrap.hidden = !needsReason;
  decisionReason.value = "";
  decisionReasonError.textContent = "";
  decisionConfirm.textContent = content.confirm;
  decisionConfirm.classList.toggle("danger", status === "rejected");
  decisionDialog.showModal();
  window.setTimeout(() => (needsReason ? decisionReason : decisionConfirm).focus(), 0);
}

decisionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!pendingDecision) return;
  const { requestId, status, needsReason, defaultReason } = pendingDecision;
  const reason = needsReason ? decisionReason.value.trim() : defaultReason;
  if (needsReason && reason.length < 3) {
    decisionReasonError.textContent = "Vui lòng nhập nội dung ít nhất 3 ký tự.";
    decisionReason.focus();
    return;
  }
  decisionReasonError.textContent = "";
  decisionConfirm.disabled = true;
  decisionCancel.disabled = true;
  decisionConfirm.textContent = "Đang cập nhật…";
  try {
    await api(`/management/requests/${requestId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, reason }),
    });
    closeDecisionDialog(true);
    await loadRequests();
    await pollManagementNotifications();
  } catch (error) {
    decisionReasonError.textContent = error.message;
  } finally {
    decisionConfirm.disabled = false;
    decisionCancel.disabled = false;
    decisionConfirm.textContent = decisionDialogContent[status].confirm;
  }
});

decisionCancel.addEventListener("click", () => closeDecisionDialog());
decisionDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeDecisionDialog();
});
decisionDialog.addEventListener("click", (event) => {
  if (event.target === decisionDialog) closeDecisionDialog();
});

async function loadRequests() {
  if (!authReady || !hasRole("manager")) return;
  const list = document.getElementById("request-list");
  const loadId = ++managementPage.loadId;
  const generation = authGeneration;
  setTicketLoading("manager");
  const refreshButton = document.getElementById("refresh-requests");
  refreshButton.disabled = true;
  refreshButton.innerHTML = '<span class="refresh-spinner" aria-hidden="true">↻</span> Đang tải';
  list.innerHTML = '<div class="request-loading" role="status"><i></i><i></i><i></i><span>Đang tải hàng đợi…</span></div>';
  try {
    const data = await api(`/management/requests?${ticketQuery("manager")}`);
    if (!isSessionCurrent(generation) || loadId !== managementPage.loadId) return;
    if (alignTicketPage("manager", data.total)) return await loadRequests();
    updateTicketPagination("manager", data);
    if (typeof updateTicketDeleteCount === "function") updateTicketDeleteCount("management", data.counts_by_status);
    managementRequests = data.items;
    renderRequestStats(managementRequests, data.counts_by_status);
    renderRequests();
    addVehicleDocumentActions(list, managementRequests);
  } catch (error) {
    if (isSessionCurrent(generation) && loadId === managementPage.loadId) list.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  } finally {
    if (isSessionCurrent(generation) && loadId === managementPage.loadId) {
      refreshButton.disabled = false;
      refreshButton.innerHTML = '<span aria-hidden="true">↻</span> Làm mới';
    }
  }
}

function clearRequestFilters() {
  document.getElementById("request-search").value = "";
  document.getElementById("request-type-filter").value = "all";
  document.getElementById("request-status-filter").value = "all";
  document.getElementById("request-date-from").value = "";
  document.getElementById("request-date-to").value = "";
  reloadTicketFilters("manager");
}

document.getElementById("refresh-progress").addEventListener("click", loadProgress);
document.getElementById("refresh-tickets").addEventListener("click", loadResidentTickets);
document.getElementById("refresh-household").addEventListener("click", () => loadHousehold());
document.getElementById("refresh-requests").addEventListener("click", loadRequests);
document.getElementById("request-search").addEventListener("input", () => reloadTicketFilters("manager", true));
document.getElementById("request-type-filter").addEventListener("change", () => reloadTicketFilters("manager"));
document.getElementById("request-status-filter").addEventListener("change", () => reloadTicketFilters("manager"));
document.getElementById("clear-request-filters").addEventListener("click", clearRequestFilters);
document.getElementById("request-stats").addEventListener("click", (event) => {
  const button = event.target.closest(".request-stat");
  if (!button) return;
  selectTicketStageForStatus("manager", button.dataset.statusFilter);
  document.getElementById("request-status-filter").value = button.dataset.statusFilter;
  reloadTicketFilters("manager");
});
document.getElementById("request-list").addEventListener("click", (event) => {
  if (event.target.closest(".clear-list-filters")) {
    clearRequestFilters();
    return;
  }
  const button = event.target.closest(".decision");
  if (button) decide(button.dataset.id, button.dataset.status, button);
});


if (document.readyState === "complete") void initializeAuth();
else document.addEventListener("DOMContentLoaded", () => { void initializeAuth(); }, { once: true });
