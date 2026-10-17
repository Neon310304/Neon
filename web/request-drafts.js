let requestDraftChatState = null;
let requestDraftChatPreview = null;
let requestDraftChatLoadId = 0;
let requestDraftChatDocumentUrl = "";

function clearRequestDraftChat() {
  requestDraftChatLoadId += 1;
  requestDraftChatState = null;
  requestDraftChatPreview?.remove();
  requestDraftChatPreview = null;
  if (requestDraftChatDocumentUrl) URL.revokeObjectURL(requestDraftChatDocumentUrl);
  requestDraftChatDocumentUrl = "";
}

function isRequestDraftConfirmation(message) {
  return /^(?:xac nhan gui|toi xac nhan gui|dong y gui|gui den bql|gui cho bql)[.!]*$/.test(normalizeProcedureSearch(message).trim());
}

async function requestTicketDraftChat(message, attachments = []) {
  const current = requestDraftChatState;
  if (current?.submitting || current?.preparing) return { handled: true, answer: "Bản nháp đang được xử lý. Bạn chờ kết quả nhé." };
  if (current?.scheduleDirty && isRequestDraftConfirmation(message)) return { handled: true, keep_preview: true, answer: "Bạn vừa sửa ngày giờ. Bấm Áp dụng ngày giờ trong bản nháp rồi xem lại trước khi xác nhận gửi." };
  if (current && !current.ready && !attachments.length && isRequestDraftConfirmation(message)) return { handled: true, keep_preview: true, answer: "Bản nháp chưa đủ thông tin hợp lệ. Bạn bổ sung các mục đang thiếu hoặc chọn ngày giờ rồi bấm Áp dụng ngày giờ trước khi gửi." };
  if (current?.ready && !attachments.length && isRequestDraftConfirmation(message)) return confirmRequestDraftChat(current);
  const generation = authGeneration;
  const loadId = ++requestDraftChatLoadId;
  if (current) { current.preparing = true; current.ready = false; }
  requestDraftChatPreview?.querySelectorAll("button, input, select").forEach(control => { control.disabled = true; });
  const prepare = (requestType, draft, document, text = message) => api(residentPath("/request-drafts/chat"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: text, request_type: requestType || "", draft: draft || {}, document_attached: Boolean(document) }),
  });
  try {
    let document = current?.document || null;
    let result;
    if (attachments.length) {
      const formData = new FormData();
      formData.append("message", message);
      formData.append("form_type", "");
      formData.append("active_form_type", current?.type || "");
      attachments.forEach(item => formData.append("files", item.file));
      const extracted = await api("/agent/form-from-files", { method: "POST", body: formData });
      if (!isSessionCurrent(generation) || loadId !== requestDraftChatLoadId) return { handled: true, answer: "Bản nháp này đã dừng. Chưa gửi yêu cầu mới." };
      if (extracted.policy_code) return { ...extracted, handled: true };
      const type = extracted.form_draft?.form_type;
      if (!["resident_card", "vehicle_registration", "visitor_registration", "temporary_residency", "move_in_schedule", "internet_registration", "card_replacement", "general_support"].includes(type)) {
        return { ...extracted, handled: true, keep_preview: true };
      }
      if (type !== current?.type) document = null;
      if (type === "vehicle_registration") {
        const images = attachments.map(item => item.file).filter(file => ["image/jpeg", "image/png", "image/webp"].includes(file.type) && file.size <= 5 * 1024 * 1024);
        if (images.length !== 1 || attachments.length !== 1) return { handled: true, keep_preview: true, answer: "Tôi đã xem tệp, nhưng chưa thể gắn chắc chắn ảnh giấy đăng ký với đúng xe. Bạn gửi riêng một ảnh giấy đăng ký của một xe để tôi đọc và chuẩn bị bản nháp; mỗi xe một hồ sơ. Chưa gửi BQL." };
        document = images[0];
      }
      const replacedVehicle = type === "vehicle_registration" && current?.document && current.document !== document;
      const previousDraft = type === current?.type && !replacedVehicle ? current.draft : {};
      result = await prepare(type, { ...previousDraft, ...(extracted.form_draft?.values || {}) }, document, "Tôi còn thiếu gì?");
      if (replacedVehicle && result.handled && !result.policy_code) result.answer = `Đã thay ảnh giấy đăng ký xe. Tôi kiểm tra lại thông tin từ ảnh mới, không tự dùng biển số từ ảnh cũ.\n\n${result.answer}`;
      if (result.handled && !result.policy_code && extracted.form_draft?.unreadable_fields?.length) result.answer = `${extracted.answer}\n\n${result.answer}`;
    } else result = await prepare(current?.type, current?.draft, document);
    if (!isSessionCurrent(generation) || loadId !== requestDraftChatLoadId) return { handled: true, answer: "Bản nháp này đã dừng. Chưa gửi yêu cầu mới." };
    if (!result.handled || result.policy_code) return result;
    if (result.clear_draft) { clearRequestDraftChat(); return result; }
    if (!attachments.length && current?.type !== result.request_type) document = null;
    if (result.request_type) {
      const sameDraft = current?.type === result.request_type && current.document === document && JSON.stringify(current.draft) === JSON.stringify(result.draft);
      requestDraftChatState = { type: result.request_type, draft: result.draft, identity: result.identity,
        ready: result.ready_to_confirm, document, generation, key: sameDraft ? current.key : crypto.randomUUID(), submitting: false, preparing: false };
    }
    return result;
  } finally {
    if (current && current === requestDraftChatState) current.preparing = false;
    requestDraftChatPreview?.querySelectorAll("button, input, select").forEach(control => { control.disabled = false; });
    const confirmation = requestDraftChatPreview?.querySelector("[data-draft-confirm]");
    if (confirmation) confirmation.disabled = !requestDraftChatState?.ready;
  }
}

async function confirmRequestDraftChat(state) {
  if (state !== requestDraftChatState || !state.ready || state.scheduleDirty || state.preparing || state.submitting || !isSessionCurrent(state.generation)) return { handled: true, answer: "Bạn cần kiểm tra bản nháp hiện tại trước khi gửi." };
  state.submitting = true;
  requestDraftChatPreview?.querySelectorAll("button, input, select").forEach(control => { control.disabled = true; });
  try {
    const payload = { request_type: state.type, ...state.draft, idempotency_key: state.key };
    let result;
    if (state.type === "vehicle_registration") {
      if (!state.document) throw new Error("Bạn cần đính kèm ảnh giấy đăng ký xe trước khi gửi.");
      const body = new FormData();
      body.append("document", state.document);
      body.append("payload", JSON.stringify(payload));
      body.append("confirmed", "true");
      result = await api("/requests/vehicle", { method: "POST", body });
    } else result = await api("/requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!isSessionCurrent(state.generation) || state !== requestDraftChatState) return { handled: true, answer: "" };
    if ((!result.created && !result.duplicate) || !/^REQ-[A-Z0-9]{8}$/.test(result.request_id || "")) throw new Error("Hệ thống chưa xác nhận gửi ticket. Bản nháp vẫn được giữ để kiểm tra.");
    clearRequestDraftChat();
    await Promise.allSettled([loadResidentTickets(), loadProgress()]);
    return { handled: true, answer: result.duplicate
      ? `Yêu cầu đã tồn tại: **${result.request_id}**. Không tạo thêm ticket mới. Bạn mở Ticket đã gửi để theo dõi.`
      : `Đã gửi **${result.request_id}** đến Ban quản lý. Bạn mở Ticket đã gửi để theo dõi; ticket chưa được duyệt hoặc hoàn tất.`,
      tools_used: [{ tool: "create_management_request", summary: `${result.duplicate ? "existing" : "created"}:${result.request_id}` }] };
  } finally {
    if (state === requestDraftChatState) {
      state.submitting = false;
      requestDraftChatPreview?.querySelectorAll("button, input, select").forEach(control => { control.disabled = false; });
    }
  }
}

async function applyRequestDraftSchedule(state, form, host) {
  if (state !== requestDraftChatState || state.preparing || state.submitting || !isSessionCurrent(state.generation) || !form.reportValidity()) return;
  const dateKey = state.type === "resident_card" ? "pickup_date" : "visit_date";
  const timeKey = state.type === "resident_card" ? "pickup_time" : "move_time";
  const draft = { ...state.draft };
  for (const key of [dateKey, timeKey]) {
    const value = form.elements.namedItem(key).value;
    if (value) draft[key] = value;
    else delete draft[key];
  }
  const article = requestDraftChatPreview;
  const loadId = ++requestDraftChatLoadId;
  state.preparing = true;
  state.ready = false;
  article.querySelectorAll("button, input, select").forEach(control => { control.disabled = true; });
  article.querySelector(".field-error").hidden = true;
  try {
    const result = await api(residentPath("/request-drafts/chat"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: "Tôi còn thiếu gì?", request_type: state.type, draft, document_attached: Boolean(state.document) }),
    });
    if (state !== requestDraftChatState || loadId !== requestDraftChatLoadId || !isSessionCurrent(state.generation)) return;
    if (!result.handled || result.policy_code || !result.draft || result.request_type !== state.type) throw new Error(result.answer || "Chưa kiểm tra được ngày giờ. Vui lòng thử lại.");
    if (JSON.stringify(state.draft) !== JSON.stringify(result.draft)) state.key = crypto.randomUUID();
    state.draft = result.draft;
    state.ready = result.ready_to_confirm;
    state.scheduleDirty = false;
    state.preparing = false;
    renderRequestDraftChat(result, host);
    requestDraftChatPreview?.querySelector("[data-draft-confirm]")?.focus();
  } catch (error) {
    if (state !== requestDraftChatState || !isSessionCurrent(state.generation)) return;
    const notice = article.querySelector(".field-error");
    notice.textContent = error.message;
    notice.hidden = false;
  } finally {
    if (state === requestDraftChatState) {
      state.preparing = false;
      requestDraftChatPreview?.querySelectorAll("button, input, select").forEach(control => { control.disabled = false; });
      const confirmation = requestDraftChatPreview?.querySelector("[data-draft-confirm]");
      if (confirmation) confirmation.disabled = !state.ready || state.scheduleDirty;
    }
  }
}

function renderRequestDraftChat(result, host = null) {
  if (result.keep_preview) return;
  requestDraftChatPreview?.remove();
  requestDraftChatPreview = null;
  if (requestDraftChatDocumentUrl) URL.revokeObjectURL(requestDraftChatDocumentUrl);
  requestDraftChatDocumentUrl = "";
  const state = requestDraftChatState;
  if (!state || result.policy_code || !result.request_type || !isSessionCurrent(state.generation)) return;
  const labels = { person_name: "Người cư trú", citizen_id: "CCCD", resident_type: "Tư cách cư trú", visitor_name: "Tên khách", visit_date: "Ngày đến / bắt đầu", move_time: "Giờ chuyển đồ",
    pickup_date: "Ngày nhận thẻ mong muốn", pickup_time: "Giờ nhận thẻ mong muốn", vehicle_plate: "Biển số", vehicle_type: "Loại xe", details: "Nội dung" };
  labels.visit_date = ({ temporary_residency: "Ngày bắt đầu ở", visitor_registration: "Ngày khách đến", move_in_schedule: "Ngày chuyển đồ" })[state.type] || labels.visit_date;
  const row = (label, value) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`;
  const identity = state.identity || {};
  const scheduled = ["resident_card", "move_in_schedule"].includes(state.type);
  const dateKey = state.type === "resident_card" ? "pickup_date" : "visit_date";
  const timeKey = state.type === "resident_card" ? "pickup_time" : "move_time";
  const rows = row("Người đăng ký", identity.full_name || "") + row("Căn hộ", [identity.site_name, identity.building, identity.apartment].filter(Boolean).join(" · "))
    + Object.entries(state.draft).filter(([key]) => !scheduled || ![dateKey, timeKey].includes(key)).map(([key, value]) => row(labels[key] || key, key === "vehicle_type" ? ({ car: "Ô tô", motorbike: "Xe máy" })[value] || value : key === "resident_type" ? ({ tenant: "Người thuê", family_member: "Thành viên gia đình" })[value] || value : value)).join("")
    + (state.document ? row("Ảnh giấy đăng ký xe", state.document.name) : "");
  const schedule = scheduled ? `<form class="request-draft-schedule"><div class="request-draft-schedule-fields"><label for="draft-${dateKey}"><span>${state.type === "resident_card" ? "Ngày nhận thẻ mong muốn" : "Ngày chuyển đồ"}</span><input id="draft-${dateKey}" name="${dateKey}" type="date" value="${escapeHtml(state.draft[dateKey] || "")}" ${state.type === "move_in_schedule" ? "required" : ""} /></label><label for="draft-${timeKey}"><span>${state.type === "resident_card" ? "Giờ nhận thẻ mong muốn" : "Giờ chuyển đồ"}</span><input id="draft-${timeKey}" name="${timeKey}" type="time" value="${escapeHtml(state.draft[timeKey] || "")}" ${state.type === "move_in_schedule" ? "required" : ""} /></label></div><button type="submit" data-draft-schedule>Áp dụng ngày giờ</button><p class="request-draft-schedule-status" role="status">${state.ready ? "Ngày giờ đã được kiểm tra. Chưa gửi BQL." : "Chọn đủ ngày, giờ rồi bấm Áp dụng ngày giờ; hoặc nhắn trong chat."}</p></form>` : "";
  const questions = result.missing_questions?.length ? `<section class="request-draft-missing" aria-label="Thông tin còn thiếu"><h4>Cần bạn bổ sung</h4><ul>${result.missing_questions.map(item => `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.question)}</span></li>`).join("")}</ul></section>` : "";
  const replies = result.suggested_replies?.length ? `<div class="request-draft-replies" aria-label="Chọn nhu cầu">${result.suggested_replies.map(reply => `<button type="button" data-draft-reply="${escapeHtml(reply)}">${escapeHtml(reply)}</button>`).join("")}</div>` : "";
  if (state.document) requestDraftChatDocumentUrl = URL.createObjectURL(state.document);
  const documentPreview = requestDraftChatDocumentUrl ? `<figure class="request-draft-document"><img src="${escapeHtml(requestDraftChatDocumentUrl)}" alt="Ảnh giấy đăng ký xe đang đính kèm bản nháp" /><figcaption>Đối chiếu biển số và loại xe với ảnh trước khi xác nhận. Nếu đọc sai hoặc ảnh mờ, chọn Điền trực tiếp để sửa; ảnh gốc vẫn được đính kèm.</figcaption></figure>` : "";
  const article = document.createElement("article");
  article.className = "request-draft-chat";
  article.setAttribute("aria-label", "Bản nháp đăng ký, chưa gửi đến BQL");
  article.innerHTML = `<span class="correction-eyebrow">Bản nháp · Chưa gửi</span><h3>${escapeHtml(requestTypeLabels[state.type] || state.type)}</h3><dl class="request-details">${rows}</dl>${documentPreview}${schedule}${questions}${replies}<p>Bạn có thể nhắn thêm thông tin để tôi điền tiếp. Chỉ gửi BQL sau khi bạn xác nhận.</p><div class="request-draft-actions"><button type="button" class="portal-primary" data-draft-confirm ${state.ready ? "" : "disabled"}>Xác nhận gửi</button><button type="button" data-draft-form>Điền trực tiếp</button><button type="button" data-draft-cancel>Dừng đăng ký</button></div><p class="field-error" role="alert" hidden></p>`;
  requestDraftChatPreview = article;
  if (scheduled) {
    if (state.type === "resident_card" && !state.draft[dateKey] && !state.draft[timeKey]) article.querySelector(".request-draft-schedule-status").textContent = "Chưa chọn lịch nhận thẻ. Bạn có thể để trống cả ngày và giờ, hoặc chọn đủ cả hai nếu muốn hẹn.";
    initialize24HourTimeInput(article.querySelector('input[type="time"]'), state.type === "resident_card" ? "Giờ nhận thẻ" : "Giờ chuyển đồ");
    const form = article.querySelector("form");
    const invalidateSchedule = () => {
      if (state !== requestDraftChatState || state.submitting || state.preparing) return;
      state.ready = false;
      state.scheduleDirty = true;
      article.querySelector("[data-draft-confirm]").disabled = true;
      article.querySelector(".request-draft-schedule-status").textContent = "Ngày giờ vừa thay đổi. Bấm Áp dụng ngày giờ để kiểm tra trước khi gửi.";
    };
    form.addEventListener("input", invalidateSchedule);
    form.addEventListener("change", invalidateSchedule);
    form.addEventListener("submit", event => { event.preventDefault(); void applyRequestDraftSchedule(state, form, host); });
  }
  article.querySelector("[data-draft-cancel]").addEventListener("click", () => { if (!state.submitting) clearRequestDraftChat(); });
  article.querySelectorAll("[data-draft-reply]").forEach(button => button.addEventListener("click", () => { if (state === requestDraftChatState && !state.submitting && !state.preparing) sendSuggestedPrompt(button.dataset.draftReply); }));
  article.querySelector("[data-draft-form]").addEventListener("click", async () => {
    await openResidentEntry(state.type);
    if (state === requestDraftChatState && isSessionCurrent(state.generation) && residentEntryState?.type === state.type && residentEntryDialog.open) {
      fillBlankFormFields(residentEntryForm, state.draft);
      if (state.document) carryVehicleImage([state.document]);
      state.formEntry = residentEntryState;
      state.ready = false;
      state.key = residentEntryState.key;
      article.querySelector("[data-draft-confirm]").disabled = true;
    }
  });
  article.querySelector("[data-draft-confirm]")?.addEventListener("click", async () => {
    try {
      const sent = await confirmRequestDraftChat(state);
      if (isSessionCurrent(state.generation) && sent.answer) addMessage("assistant", sent.answer, sent.tools_used || []);
    } catch (error) {
      if (state !== requestDraftChatState || !isSessionCurrent(state.generation)) return;
      const notice = article.querySelector(".field-error");
      notice.textContent = error.message;
      notice.hidden = false;
    }
  });
  const actionHost = host?.querySelector(".a2ui-surface");
  if (actionHost) actionHost.before(article);
  else document.getElementById("messages").appendChild(article);
  article.scrollIntoView({ block: "nearest" });
}

function syncRequestDraftFromEntry() {
  const state = requestDraftChatState;
  if (!state || state.formEntry !== residentEntryState || residentEntryState.submitting || !isSessionCurrent(state.generation)) return;
  const fields = residentEntryCatalog[state.type]?.fields || [];
  const values = Object.fromEntries(fields.map(field => [field.key, residentEntryForm.elements.namedItem(field.key)?.value || ""]));
  state.draft = entryPayload(state.type, values);
  if (state.type === "vehicle_registration") state.document = vehicleDocumentFile || null;
  state.key = crypto.randomUUID();
  state.ready = false;
  state.formEntry = null;
  if (requestDraftChatPreview) renderRequestDraftChat({ request_type: state.type }, requestDraftChatPreview.closest(".message"));
}
