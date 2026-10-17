let vehicleDocumentFile = null;
let vehiclePreviewUrl = null;
let vehicleExtractionId = 0;
let vehicleDocumentViewId = 0;
let vehicleDocumentViewUrl = null;
const ticketFilterTimers = new Map();
let autofillLoadId = 0;

Object.assign(apiErrorLabels, {
  form_extraction_failed: "Chưa đọc được các trường trong tệp. Chụp rõ hơn hoặc nhập tay.",
  form_extraction_timeout: "Dịch vụ đọc tệp đang chậm. Bạn có thể nhập tay hoặc thử lại sau.",
  usage_limit_reached: "Đã đạt giới hạn thao tác trong 10 phút. Bạn vẫn có thể điền form và xem ticket.",
});

function resetFormAutofill() {
  autofillLoadId += 1;
  document.getElementById("form-autofill").hidden = ["ownership", "account_registration", "vehicle_registration", "resident_card", "move_in_schedule"].includes(residentEntryState?.type);
  document.getElementById("form-autofill-files").value = "";
  document.getElementById("form-autofill-read").disabled = false;
  document.getElementById("form-autofill-status").textContent = "";
}

function clearAutofillSession() {
  resetFormAutofill();
  document.querySelectorAll(".attachment-draft").forEach(node => node.remove());
  resetFirstPasswordForm();
  document.getElementById("first-password-form").hidden = true;
  document.getElementById("auth-title").textContent = "Đăng nhập";
}

function fillBlankFormFields(form, values) {
  const filled = [];
  for (const [key, value] of Object.entries(values)) {
    const input = form.elements.namedItem(key);
    if (!input || input.readOnly || input.disabled || input.dataset.userEdited === "true") continue;
    if (input.value.trim()) continue;
    if (input.tagName === "SELECT" && !Array.from(input.options).some(option => option.value === String(value))) continue;
    input.value = String(value);
    input.classList.add("autofilled");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    filled.push(key);
  }
  return filled;
}

function carryVehicleImage(files) {
  const images = files.filter(file => ["image/jpeg", "image/png", "image/webp"].includes(file.type) && file.size <= 5 * 1024 * 1024);
  if (residentEntryState?.type !== "vehicle_registration" || vehicleDocumentFile || images.length !== 1) return;
  const transfer = new DataTransfer();
  transfer.items.add(images[0]);
  const input = document.getElementById("vehicle-document");
  input.files = transfer.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

async function readFormFiles(files, formType = "", message = "") {
  if (!files.length || files.length > 4 || files.reduce((size, file) => size + file.size, 0) > 16 * 1024 * 1024) throw new Error("Chọn 1–4 tệp, tổng tối đa 16 MB.");
  const body = new FormData();
  files.forEach(file => body.append("files", file));
  body.append("form_type", formType);
  body.append("message", message);
  return api("/agent/form-from-files", { method: "POST", body });
}

document.getElementById("resident-entry-fields").addEventListener("input", event => {
  if (event.isTrusted) {
    event.target.dataset.userEdited = "true";
    event.target.classList.remove("autofilled");
  }
});

document.getElementById("form-autofill-files").addEventListener("change", () => {
  autofillLoadId += 1;
  document.getElementById("form-autofill-read").disabled = false;
  document.getElementById("form-autofill-status").textContent = "";
});

document.getElementById("form-autofill-read").addEventListener("click", async event => {
  const state = residentEntryState;
  if (!state || state.review || state.submitting) return;
  const files = Array.from(document.getElementById("form-autofill-files").files);
  const loadId = ++autofillLoadId;
  const button = event.currentTarget;
  const status = document.getElementById("form-autofill-status");
  button.disabled = true;
  status.textContent = "Đang đọc giấy tờ… Bạn vẫn có thể điền các ô khác.";
  try {
    const result = await readFormFiles(files, state.type);
    if (loadId !== autofillLoadId || state !== residentEntryState || !residentEntryDialog.open || state.review || !isSessionCurrent(state.generation)) return;
    const filled = fillBlankFormFields(document.getElementById("resident-entry-form"), result.form_draft?.values || {});
    carryVehicleImage(files);
    status.textContent = filled.length ? `Đã điền ${filled.length} ô trống được đánh dấu. Kiểm tra trước khi gửi; các ô đã nhập được giữ nguyên.` : result.answer;
  } catch (error) {
    if (loadId === autofillLoadId && state === residentEntryState) status.textContent = error.message;
  } finally {
    if (loadId === autofillLoadId) button.disabled = false;
  }
});

function renderAttachmentDraft(result, attachments, host = null) {
  if (!result.form_draft || !authReady) return;
  const generation = authGeneration;
  const draft = result.form_draft;
  const article = document.createElement("article");
  article.className = "attachment-draft";
  const title = document.createElement("strong");
  title.textContent = "Thông tin đọc từ giấy tờ";
  article.appendChild(title);
  const list = document.createElement("dl");
  const config = residentEntryCatalog[draft.form_type];
  Object.entries(draft.values).forEach(([key, value]) => {
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = config?.fields.find(field => field.key === key)?.label || key;
    detail.textContent = String(value);
    list.append(term, detail);
  });
  article.appendChild(list);
  const actions = document.createElement("div");
  actions.className = "portal-inline-actions";
  const choices = config ? [draft.form_type] : ["temporary_residency", "vehicle_registration", "visitor_registration", "profile"];
  choices.forEach(type => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = config ? "Điền vào biểu mẫu" : residentEntryCatalog[type].title;
    button.addEventListener("click", async () => {
      if (!isSessionCurrent(generation)) return;
      button.disabled = true;
      try {
        const proposal = config ? result : await readFormFiles(attachments.map(item => item.file), type);
        if (!isSessionCurrent(generation)) return;
        await openResidentEntry(type);
        if (residentEntryState?.type !== type || !residentEntryDialog.open || !isSessionCurrent(generation)) return;
        const filled = fillBlankFormFields(document.getElementById("resident-entry-form"), proposal.form_draft?.values || {});
        carryVehicleImage(attachments.map(item => item.file));
        document.getElementById(type === "vehicle_registration" ? "vehicle-extract-status" : "form-autofill-status").textContent = `Đã điền ${filled.length} ô. Kiểm tra giấy tờ và nhập các thông tin còn thiếu trước khi gửi.`;
      } catch (error) { button.textContent = error.message; }
      finally { if (button.isConnected) button.disabled = false; }
    });
    actions.appendChild(button);
  });
  article.appendChild(actions);
  const actionHost = host?.querySelector(".a2ui-surface");
  if (actionHost) actionHost.before(article);
  else document.getElementById("messages").appendChild(article);
  document.querySelectorAll(".attachment-draft").forEach((node, index, items) => { if (index < items.length - 3) node.remove(); });
  article.scrollIntoView({ block: "nearest" });
}

Object.assign(apiErrorLabels, {
  vehicle_document_required: "Vui lòng đính kèm ảnh giấy đăng ký xe.",
  vehicle_document_conflict: "Xe này đã có hồ sơ với ảnh khác. Hãy mở ticket cũ để theo dõi hoặc liên hệ BQL.",
  invalid_date_range: "Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.",
  current_password_invalid: "Mật khẩu hiện tại không đúng.",
  new_password_invalid: "Mật khẩu mới cần ít nhất 8 ký tự và khác mật khẩu hiện tại.",
  username_unavailable: "Tên đăng nhập này đã được sử dụng. Hãy chọn tên khác.",
  username_change_required: "Hãy chọn tên đăng nhập mới, khác tài khoản Ban quản lý đã cấp.",
  username_change_not_allowed: "Tên đăng nhập đã được thiết lập và không thể tự đổi lần nữa.",
  invalid_username: "Dùng chữ không dấu, số hoặc dấu _ . @ -, tối đa 80 ký tự.",
  initial_credentials_required: "Hãy thiết lập tên đăng nhập và mật khẩu riêng trước khi sử dụng.",
  username_check_rate_limited: "Bạn đã kiểm tra nhiều tên liên tiếp. Vui lòng thử lại sau ít phút.",
  password_changed_concurrently: "Mật khẩu đã thay đổi ở phiên khác. Vui lòng đăng nhập lại.",
  document_revision_changed: "Tài liệu đã cập nhật. Tải lại, đọc phiên bản mới rồi xác nhận.",
  request_version_conflict: "Ticket vừa có thay đổi. Làm mới danh sách và kiểm tra lại trước khi tiếp tục thao tác.",
});

const ticketStages = { resident: "active", manager: "active" };
const terminalTicketStatuses = new Set(["completed", "rejected", "cancelled_by_resident"]);

function ticketStatusInStage(status, stage) {
  if (["all", "active", "finished", "closed"].includes(status)) return true;
  return terminalTicketStatuses.has(status) === (stage === "finished");
}

function synchronizeTicketStage(role) {
  const stage = ticketStages[role];
  const prefix = role === "manager" ? "request" : "resident-ticket";
  const filter = document.getElementById(`${prefix}-status-filter`);
  [...filter.options].forEach(option => {
    const allowed = option.value === "all" || (option.value === "active" ? stage === "active" : ["finished", "closed"].includes(option.value) ? stage === "finished" : ticketStatusInStage(option.value, stage));
    option.hidden = !allowed;
    option.disabled = !allowed;
  });
  if (filter.selectedOptions[0]?.disabled) filter.value = "all";
  document.querySelectorAll(`[data-ticket-role="${role}"]`).forEach(button => {
    const selected = button.dataset.ticketStage === stage;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  document.getElementById(`${role}-ticket-results`).setAttribute("aria-labelledby", `${role}-stage-${stage}`);
  document.querySelector(`[data-ticket-stage-help="${role}"]`).textContent = stage === "finished"
    ? "Bao gồm Hoàn tất, Từ chối và Đã hủy. Xóa chỉ ẩn khỏi danh sách của bạn; hồ sơ và lịch sử vẫn được giữ."
    : "Các ticket đã gửi, đang xem xét, cần bổ sung hoặc đã duyệt nhưng chưa hoàn tất.";
  const deletion = document.querySelector(`.delivery-bulk-delete[data-hide-list="${role === "manager" ? "management" : "tickets"}"]`);
  if (deletion) deletion.hidden = stage !== "finished";
}

function selectTicketStage(role, stage, reload = true) {
  if (!["resident", "manager"].includes(role) || !["active", "finished"].includes(stage)) return;
  ticketStages[role] = stage;
  const prefix = role === "manager" ? "request" : "resident-ticket";
  document.getElementById(`${prefix}-status-filter`).value = "all";
  synchronizeTicketStage(role);
  if (reload) reloadTicketFilters(role);
}

function selectTicketStageForStatus(role, status) {
  if (status && status !== "all") selectTicketStage(role, terminalTicketStatuses.has(status) || ["finished", "closed"].includes(status) ? "finished" : "active", false);
}

function updateTicketStageCounts(role, counts = {}) {
  document.querySelectorAll(`[data-ticket-role="${role}"]`).forEach(button => {
    const stage = button.dataset.ticketStage;
    const total = Object.entries(counts).filter(([status]) => ticketStatusInStage(status, stage)).reduce((sum, [, count]) => sum + Number(count), 0);
    button.querySelector("[data-stage-count]").textContent = String(total);
  });
}

document.querySelectorAll("[data-ticket-role]").forEach(button => {
  button.addEventListener("click", () => selectTicketStage(button.dataset.ticketRole, button.dataset.ticketStage));
  button.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const siblings = [...button.parentElement.querySelectorAll('[role="tab"]')];
    const index = event.key === "Home" ? 0 : event.key === "End" ? siblings.length - 1 : (siblings.indexOf(button) + (event.key === "ArrowRight" ? 1 : -1) + siblings.length) % siblings.length;
    siblings[index].click();
    siblings[index].focus();
  });
});

function ticketQuery(role) {
  const prefix = role === "manager" ? "request" : "resident-ticket";
  const page = role === "manager" ? managementPage : residentTicketPage;
  const params = new URLSearchParams({ offset: String(page.offset), limit: String(page.limit) });
  for (const [suffix, key] of [["search", "q"], ["type-filter", "request_type"], ["status-filter", "status"], ["date-from", "date_from"], ["date-to", "date_to"]]) {
    const value = document.getElementById(`${prefix}-${suffix}`).value.trim();
    if (value && value !== "all") params.set(key, value);
  }
  const status = params.get("status");
  if (!status || ["active", "finished", "closed"].includes(status) || !ticketStatusInStage(status, ticketStages[role])) params.set("status", ticketStages[role]);
  return params.toString();
}

function updateTicketPagination(role, data) {
  updateTicketStageCounts(role, data.counts_by_status);
  const prefix = role === "manager" ? "request" : "resident-ticket";
  const page = role === "manager" ? managementPage : residentTicketPage;
  page.total = data.total;
  document.getElementById(`${prefix}-prev`).disabled = page.offset === 0;
  document.getElementById(`${prefix}-next`).disabled = !data.has_more;
  document.getElementById(`${prefix}-page`).textContent = `Trang ${Math.floor(page.offset / page.limit) + 1} / ${Math.max(1, Math.ceil(data.total / page.limit))}`;
  if (role === "resident") document.getElementById("resident-ticket-count").textContent = `${data.total} ticket phù hợp · ${data.total ? page.offset + 1 : 0}–${Math.min(page.offset + page.limit, data.total)}`;
}

function alignTicketPage(role, total) {
  const page = role === "manager" ? managementPage : residentTicketPage;
  const lastOffset = Math.max(0, Math.ceil(total / page.limit) - 1) * page.limit;
  if (page.offset <= lastOffset) return false;
  page.offset = lastOffset;
  return true;
}

function setTicketLoading(role) {
  const prefix = role === "manager" ? "request" : "resident-ticket";
  document.getElementById(`${prefix}-prev`).disabled = true;
  document.getElementById(`${prefix}-next`).disabled = true;
}

function reloadTicketFilters(role, debounce = false) {
  const page = role === "manager" ? managementPage : residentTicketPage;
  page.offset = 0;
  page.loadId += 1;
  clearTimeout(ticketFilterTimers.get(role));
  const loader = role === "manager" ? loadRequests : loadResidentTickets;
  if (debounce) ticketFilterTimers.set(role, setTimeout(loader, 300));
  else void loader();
}

function setupTicketFilters() {
  const toolbar = document.getElementById("resident-ticket-toolbar");
  const searchLabel = document.createElement("label");
  searchLabel.textContent = "Tìm ticket";
  const search = document.createElement("input");
  search.id = "resident-ticket-search";
  search.type = "search";
  search.placeholder = "Mã ticket, biển số hoặc nội dung…";
  search.maxLength = 120;
  searchLabel.appendChild(search);
  toolbar.appendChild(searchLabel);
  for (const [suffix, title] of [["type-filter", "Loại yêu cầu"], ["status-filter", "Trạng thái"]]) {
    const label = document.createElement("label");
    label.textContent = title;
    const select = document.getElementById(`request-${suffix}`).cloneNode(true);
    select.id = `resident-ticket-${suffix}`;
    label.appendChild(select);
    toolbar.appendChild(label);
  }
  for (const [suffix, title] of [["date-from", "Từ ngày gửi"], ["date-to", "Đến ngày gửi"]]) {
    const label = document.createElement("label");
    label.textContent = title;
    const input = document.createElement("input");
    input.id = `resident-ticket-${suffix}`;
    input.type = "date";
    label.appendChild(input);
    toolbar.appendChild(label);
  }
  const clear = document.createElement("button");
  clear.type = "button";
  clear.textContent = "Xóa bộ lọc";
  clear.addEventListener("click", () => {
    toolbar.querySelectorAll("input").forEach(input => { input.value = ""; });
    toolbar.querySelectorAll("select").forEach(select => { select.value = "all"; });
    reloadTicketFilters("resident");
  });
  toolbar.appendChild(clear);
  search.addEventListener("input", () => reloadTicketFilters("resident", true));
  toolbar.querySelectorAll("select, input[type=date]").forEach(input => input.addEventListener("change", () => reloadTicketFilters("resident")));
  for (const role of ["resident", "manager"]) {
    const prefix = role === "manager" ? "request" : "resident-ticket";
    for (const [direction, step] of [["prev", -1], ["next", 1]]) {
      document.getElementById(`${prefix}-${direction}`).addEventListener("click", () => {
        const page = role === "manager" ? managementPage : residentTicketPage;
        page.offset = Math.max(0, page.offset + step * page.limit);
        void (role === "manager" ? loadRequests() : loadResidentTickets());
      });
    }
  }
  ["request-date-from", "request-date-to"].forEach(id => document.getElementById(id).addEventListener("change", () => reloadTicketFilters("manager")));
}

function setupVehicleUpload(active) {
  vehicleExtractionId += 1;
  vehicleDocumentFile = null;
  clearVehiclePreview();
  const input = document.getElementById("vehicle-document");
  input.value = "";
  input.disabled = !active;
  input.required = active;
  input.setCustomValidity("");
  document.getElementById("vehicle-upload").hidden = !active;
  document.getElementById("vehicle-document-preview").hidden = true;
  document.getElementById("vehicle-extract-consent").checked = false;
  document.getElementById("vehicle-extract").disabled = true;
  document.getElementById("vehicle-rotate").disabled = true;
  document.getElementById("vehicle-extract-status").textContent = "Chọn ảnh giấy đăng ký. Có thể đọc tự động hoặc nhập tay; BQL đối chiếu trước khi duyệt.";
}

function clearVehiclePreview() {
  if (vehiclePreviewUrl) URL.revokeObjectURL(vehiclePreviewUrl);
  vehiclePreviewUrl = null;
  const preview = document.getElementById("vehicle-document-preview");
  preview.hidden = true;
  preview.removeAttribute("src");
  document.getElementById("vehicle-extract").disabled = true;
  document.getElementById("vehicle-rotate").disabled = true;
}

function previewVehicleDocument() {
  if (vehiclePreviewUrl) URL.revokeObjectURL(vehiclePreviewUrl);
  vehiclePreviewUrl = URL.createObjectURL(vehicleDocumentFile);
  const preview = document.getElementById("vehicle-document-preview");
  preview.src = vehiclePreviewUrl;
  preview.hidden = false;
  document.getElementById("vehicle-rotate").disabled = false;
  document.getElementById("vehicle-extract").disabled = !document.getElementById("vehicle-extract-consent").checked;
}

document.getElementById("vehicle-document").addEventListener("change", event => {
  vehicleExtractionId += 1;
  const file = event.target.files[0];
  vehicleDocumentFile = null;
  clearVehiclePreview();
  event.target.setCustomValidity("");
  if (!file) { setupVehicleUpload(true); return; }
  if (!new Set(["image/jpeg", "image/png", "image/webp"]).has(file.type) || file.size > 5 * 1024 * 1024) {
    event.target.setCustomValidity("Chọn ảnh JPG/PNG/WebP tối đa 5 MB.");
    event.target.reportValidity();
    return;
  }
  vehicleDocumentFile = file;
  previewVehicleDocument();
  document.getElementById("vehicle-extract-status").textContent = "Ảnh đã chọn. Kiểm tra đúng chiều; nhập biển số/loại xe hoặc đồng ý đọc thông tin từ ảnh.";
});

document.getElementById("vehicle-extract-consent").addEventListener("change", event => {
  document.getElementById("vehicle-extract").disabled = !vehicleDocumentFile || !event.target.checked;
});

document.getElementById("vehicle-rotate").addEventListener("click", async event => {
  if (!vehicleDocumentFile || residentEntryState?.review) return;
  const extractionId = ++vehicleExtractionId;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const image = await createImageBitmap(vehicleDocumentFile);
    const scale = Math.min(1, 2400 / Math.max(image.width, image.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(image.height * scale);
    canvas.height = Math.round(image.width * scale);
    const context = canvas.getContext("2d");
    context.translate(canvas.width / 2, canvas.height / 2);
    context.rotate(Math.PI / 2);
    context.drawImage(image, -image.width * scale / 2, -image.height * scale / 2, image.width * scale, image.height * scale);
    image.close();
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", .9));
    if (!blob || extractionId !== vehicleExtractionId || residentEntryState?.review) return;
    vehicleDocumentFile = new File([blob], "vehicle-registration.jpg", { type: "image/jpeg" });
    previewVehicleDocument();
  } catch { if (extractionId === vehicleExtractionId) document.getElementById("vehicle-extract-status").textContent = "Không xoay được ảnh. Hãy xoay trong thư viện ảnh rồi tải lại."; }
  finally { if (extractionId === vehicleExtractionId) button.disabled = !vehicleDocumentFile; }
});

document.getElementById("vehicle-extract").addEventListener("click", async event => {
  if (!vehicleDocumentFile || !document.getElementById("vehicle-extract-consent").checked || residentEntryState?.review) return;
  const extractionId = ++vehicleExtractionId;
  const generation = authGeneration;
  const button = event.currentTarget;
  button.disabled = true;
  document.getElementById("vehicle-extract-status").textContent = "Đang đọc ảnh. Thông tin chỉ là gợi ý, chưa gửi hồ sơ…";
  const form = new FormData();
  form.append("document", vehicleDocumentFile);
  form.append("consent", "true");
  try {
    const result = await api("/vehicles/extract", { method: "POST", body: form });
    if (extractionId !== vehicleExtractionId || !isSessionCurrent(generation) || residentEntryState?.review) return;
    if (result.readable) {
      document.getElementById("entry-vehicle_plate").value = result.vehicle_plate;
      if (result.vehicle_type) document.getElementById("entry-vehicle_type").value = result.vehicle_type;
    }
    document.getElementById("vehicle-extract-status").textContent = result.message;
  } catch (error) {
    if (extractionId === vehicleExtractionId && isSessionCurrent(generation)) document.getElementById("vehicle-extract-status").textContent = `${error.message} Bạn vẫn có thể nhập tay và gửi kèm ảnh.`;
  } finally { if (extractionId === vehicleExtractionId) button.disabled = !vehicleDocumentFile || !document.getElementById("vehicle-extract-consent").checked; }
});

async function submitVehicleRequest(state) {
  if (!vehicleDocumentFile) throw new Error("Vui lòng đính kèm ảnh giấy đăng ký xe.");
  const form = new FormData();
  form.append("document", vehicleDocumentFile);
  form.append("confirmed", "true");
  form.append("payload", JSON.stringify({ ...state.payload, request_type: "vehicle_registration", idempotency_key: state.key }));
  return api("/requests/vehicle", { method: "POST", body: form });
}

residentEntryDialog.addEventListener("close", () => setupVehicleUpload(false));

function addVehicleDocumentActions(list, items) {
  list.querySelectorAll(".request-card").forEach((card, index) => {
    const item = items[index];
    if (item?.request_type !== "vehicle_registration") return;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = item.vehicle_document_available ? "Xem giấy đăng ký xe" : "Chưa có ảnh giấy đăng ký";
    button.disabled = !item.vehicle_document_available;
    button.className = "vehicle-document-action";
    button.addEventListener("click", () => openVehicleDocument(item.request_id, button));
    const slot = card.querySelector(".vehicle-document-slot dd");
    (slot || card.querySelector(".ticket-overview") || card).appendChild(button);
  });
}

async function openVehicleDocument(requestId, returnFocus) {
  const viewId = ++vehicleDocumentViewId;
  const generation = authGeneration;
  const dialog = document.getElementById("vehicle-document-dialog");
  const image = document.getElementById("vehicle-document-image");
  const error = document.getElementById("vehicle-document-error");
  image.hidden = true;
  error.hidden = true;
  dialog.showModal();
  dialog.onclose = () => {
    vehicleDocumentViewId += 1;
    if (vehicleDocumentViewUrl) URL.revokeObjectURL(vehicleDocumentViewUrl);
    vehicleDocumentViewUrl = null;
    image.removeAttribute("src");
    returnFocus?.focus();
  };
  try {
    const response = await authorizedFetch(`${API}/requests/${encodeURIComponent(requestId)}/vehicle-document`, { cache: "no-store" });
    if (!response.ok) throw new Error("Không xem được ảnh; kiểm tra quyền truy cập hoặc tải lại danh sách.");
    const blob = await response.blob();
    if (!isSessionCurrent(generation) || viewId !== vehicleDocumentViewId || !dialog.open) return;
    vehicleDocumentViewUrl = URL.createObjectURL(blob);
    image.src = vehicleDocumentViewUrl;
    image.hidden = false;
  } catch (failure) { if (viewId === vehicleDocumentViewId && dialog.open) { error.textContent = failure.message; error.hidden = false; } }
}

document.getElementById("vehicle-document-close").addEventListener("click", () => document.getElementById("vehicle-document-dialog").close());

let passwordChangeAllowed = false;
const firstPasswordDialog = document.getElementById("first-password-dialog");
function resetFirstPasswordForm() {
  document.getElementById("first-password-form").reset();
  document.getElementById("first-password-error").hidden = true;
  resetUsernameCheck(document.getElementById("first-password-username"), "first-password-username-error");
  firstPasswordDialog.querySelectorAll("[data-password-toggle]").forEach(button => setFirstPasswordVisible(button, false));
}

function setFirstPasswordVisible(button, visible) {
  const input = document.getElementById(button.dataset.passwordToggle);
  input.type = visible ? "text" : "password";
  const label = input.name === "new_password" ? "mật khẩu mới" : "mật khẩu xác nhận";
  button.setAttribute("aria-pressed", String(visible));
  button.setAttribute("aria-label", `${visible ? "Ẩn" : "Hiện"} ${label}`);
}

firstPasswordDialog.addEventListener("click", event => {
  const button = event.target.closest("[data-password-toggle]");
  if (!button || document.getElementById("first-password-submit").disabled) return;
  setFirstPasswordVisible(button, button.getAttribute("aria-pressed") !== "true");
});
firstPasswordDialog.addEventListener("close", resetFirstPasswordForm);
bindUsernameValidation(document.getElementById("first-password-username"), "first-password-username-error");
firstPasswordDialog.addEventListener("cancel", event => event.preventDefault());
firstPasswordDialog.addEventListener("keydown", event => {
  if (event.key !== "Tab") return;
  const controls = Array.from(firstPasswordDialog.querySelectorAll('input:not(:disabled), button:not(:disabled)'));
  const first = controls[0];
  const last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
});
document.getElementById("first-password-cancel").addEventListener("click", async () => {
  if (document.getElementById("first-password-submit").disabled) return;
  await signOut();
});

document.getElementById("first-password-form").addEventListener("submit", async event => {
  event.preventDefault();
  firstPasswordDialog.querySelectorAll("[data-password-toggle]").forEach(button => setFirstPasswordVisible(button, false));
  const form = event.currentTarget;
  const button = document.getElementById("first-password-submit");
  const error = document.getElementById("first-password-error");
  if (button.disabled) return;
  showUsernameError(form.elements.username, "first-password-username-error");
  if (!form.reportValidity()) return;
  error.hidden = true;
  const username = form.elements.username.value.trim();
  const newPassword = form.elements.new_password.value;
  if (newPassword !== form.elements.confirm_password.value) {
    error.textContent = "Hai lần nhập mật khẩu mới chưa khớp.";
    error.hidden = false;
    error.focus();
    return;
  }
  button.disabled = true;
  document.getElementById("first-password-cancel").disabled = true;
  form.setAttribute("aria-busy", "true");
  button.textContent = "Đang cập nhật…";
  let passwordChanged = false;
  try {
    if (!await validateUsernameInput(form.elements.username, "first-password-username-error")) { form.elements.username.focus(); return; }
    await api("/auth/password/first-login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, new_password: newPassword }) });
    passwordChanged = true;
    clearStoredSession();
    authSession = null;
    await localPasswordLogin(username, newPassword);
    resetFirstPasswordForm();
    await finishAuthentication(await requestAuthContext());
  } catch (failure) {
    if (passwordChanged) {
      showLogin("Đã lưu tài khoản. Vui lòng đăng nhập bằng tên đăng nhập và mật khẩu mới.");
      return;
    }
    if (["username_unavailable", "username_change_required", "invalid_username"].includes(failure.code)) showUsernameError(form.elements.username, "first-password-username-error", failure.message);
    error.textContent = failure.message || "Chưa cập nhật được tài khoản. Vui lòng thử lại.";
    error.hidden = false;
    error.focus();
  } finally {
    button.disabled = false;
    document.getElementById("first-password-cancel").disabled = false;
    form.setAttribute("aria-busy", "false");
    button.textContent = "Lưu và tiếp tục";
  }
});

let passwordChangeUsername = "";
let passwordStatusLoadId = 0;

function resetPasswordChangeForm() {
  document.getElementById("password-change-form").reset();
  document.getElementById("password-change-section").hidden = true;
  document.getElementById("password-change-help").hidden = true;
  document.getElementById("password-change-error").hidden = true;
  document.getElementById("password-change-open").setAttribute("aria-expanded", "false");
}

function clearPasswordResult() {
  document.getElementById("password-result-details").replaceChildren();
  const secret = document.getElementById("password-result-secret");
  secret.value = "";
  secret.type = "password";
  const reveal = document.getElementById("password-result-reveal");
  reveal.setAttribute("aria-label", "Hiện mật khẩu mới");
  reveal.setAttribute("aria-pressed", "false");
}

document.getElementById("user-role").addEventListener("click", async () => {
  const generation = authGeneration;
  const loadId = ++passwordStatusLoadId;
  const open = document.getElementById("password-change-open");
  resetPasswordChangeForm();
  passwordChangeAllowed = false;
  passwordChangeUsername = "";
  open.disabled = true;
  open.textContent = "Đang kiểm tra…";
  try {
    const status = await api("/auth/password/status");
    if (!isSessionCurrent(generation) || loadId !== passwordStatusLoadId || !document.getElementById("personal-dialog").open) return;
    passwordChangeAllowed = status.change_allowed;
    passwordChangeUsername = status.username || "";
    open.disabled = false;
    open.textContent = "Đổi mật khẩu";
  } catch {
    if (isSessionCurrent(generation) && loadId === passwordStatusLoadId) open.textContent = "Chưa tải được quyền đổi mật khẩu";
  }
});

document.getElementById("password-change-open").addEventListener("click", () => {
  if (!authReady) return;
  if (!passwordChangeAllowed) {
    document.getElementById("password-change-help").hidden = false;
    return;
  }
  document.getElementById("personal-dialog").close();
  document.getElementById("password-change-section").hidden = false;
  document.getElementById("password-change-dialog").showModal();
  document.getElementById("password-change-open").setAttribute("aria-expanded", "true");
  document.getElementById("password-change-form").elements.namedItem("current_password").focus();
});

document.getElementById("password-change-cancel").addEventListener("click", () => {
  if (document.getElementById("password-change-submit").disabled) return;
  document.getElementById("password-change-dialog").close();
});

document.getElementById("password-change-close").addEventListener("click", () => {
  if (!document.getElementById("password-change-submit").disabled) document.getElementById("password-change-dialog").close();
});
document.getElementById("password-change-dialog").addEventListener("cancel", event => {
  if (document.getElementById("password-change-submit").disabled) event.preventDefault();
});
document.getElementById("password-change-dialog").addEventListener("close", () => {
  resetPasswordChangeForm();
  if (authReady) document.getElementById("user-role").focus();
});
document.getElementById("personal-dialog").addEventListener("close", () => {
  passwordStatusLoadId += 1;
  if (!document.getElementById("password-change-dialog").open) resetPasswordChangeForm();
});

for (const id of ["password-result-close", "password-result-done"]) {
  document.getElementById(id).addEventListener("click", () => document.getElementById("password-result-dialog").close());
}
document.getElementById("password-result-dialog").addEventListener("close", () => {
  clearPasswordResult();
  document.getElementById("login-password").value = "";
  document.getElementById("login-password").focus();
});
document.getElementById("password-result-reveal").addEventListener("click", event => {
  const secret = document.getElementById("password-result-secret");
  const visible = secret.type === "password";
  secret.type = visible ? "text" : "password";
  event.currentTarget.setAttribute("aria-label", `${visible ? "Ẩn" : "Hiện"} mật khẩu mới`);
  event.currentTarget.setAttribute("aria-pressed", String(visible));
});
window.addEventListener("pagehide", () => {
  resetPasswordChangeForm();
  clearPasswordResult();
});

document.getElementById("password-change-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const error = document.getElementById("password-change-error");
  const button = document.getElementById("password-change-submit");
  if (button.disabled || !form.reportValidity()) return;
  const values = Object.fromEntries(new FormData(form));
  error.hidden = true;
  if (values.new_password !== values.confirm_password) {
    error.textContent = "Mật khẩu nhập lại chưa khớp.";
    error.hidden = false;
    form.elements.namedItem("confirm_password").focus();
    return;
  }
  button.disabled = true;
  document.getElementById("password-change-cancel").disabled = true;
  const generation = authGeneration;
  const username = passwordChangeUsername;
  const details = document.getElementById("personal-details");
  const rows = [["Tài khoản", username], ...Array.from(details.querySelectorAll("dt"), term => [term.textContent, term.nextElementSibling.textContent])];
  try {
    await api("/auth/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_password: values.current_password, new_password: values.new_password }) });
    if (!isSessionCurrent(generation)) return;
    form.reset();
    document.getElementById("password-change-dialog").close();
    showLogin("Đã đổi mật khẩu. Đăng nhập lại bằng mật khẩu mới.");
    document.getElementById("login-username").value = username;
    document.getElementById("login-password").value = "";
    portalSummary(document.getElementById("password-result-details"), rows);
    document.getElementById("password-result-secret").value = values.new_password;
    document.getElementById("password-result-dialog").showModal();
  } catch (failure) { if (isSessionCurrent(generation)) { error.textContent = failure.message; error.hidden = false; } }
  finally {
    values.current_password = values.new_password = values.confirm_password = "";
    button.disabled = false;
    document.getElementById("password-change-cancel").disabled = false;
  }
});

setupTicketFilters();
for (const role of ["resident", "manager"]) synchronizeTicketStage(role);

function bindPasswordVisibilityControls() {
  document.querySelectorAll("[data-password-visibility]").forEach(button => {
    const input = document.getElementById(button.getAttribute("aria-controls"));
    if (!input) return;
    const label = button.getAttribute("aria-label").replace(/^Hiện /, "");
    const setVisible = visible => {
      input.type = visible ? "text" : "password";
      button.setAttribute("aria-pressed", String(visible));
      button.setAttribute("aria-label", `${visible ? "Ẩn" : "Hiện"} ${label}`);
    };
    const hide = () => setVisible(false);
    button.addEventListener("click", () => setVisible(input.type === "password"));
    input.form?.addEventListener("reset", hide);
    input.form?.addEventListener("submit", hide);
    input.closest("dialog")?.addEventListener("close", hide);
    window.addEventListener("pagehide", hide);
    hide();
  });
}

bindPasswordVisibilityControls();
