let ticketCorrectionState = null;
let ticketCorrectionLoadId = 0;
let correctionChatState = null;
let correctionChatPreview = null;
let correctionChatLoadId = 0;

Object.assign(apiErrorLabels, {
  request_not_editable: "Ticket đã chuyển trạng thái và không còn cho phép sửa. Hãy tải lại ticket.",
  request_version_conflict: "Ticket vừa được cập nhật ở phiên khác. Tải lại ticket trước khi bổ sung.",
  request_duplicate: "Thông tin này trùng với một hồ sơ đang xử lý. Kiểm tra các ticket đã gửi.",
  update_content_required: "Vui lòng sửa hoặc bổ sung thông tin trước khi gửi lại.",
  invalid_person_name: "Họ tên người cư trú chưa hợp lệ.",
  invalid_details: "Nội dung chưa hợp lệ hoặc vượt quá 1.000 ký tự.",
  request_context_mismatch: "Vui lòng chọn đúng một mã ticket để bổ sung.",
  correction_extraction_failed: "Chưa đọc được thông tin cần sửa. Bạn nhập lại hoặc chọn Điền trực tiếp; ticket chưa thay đổi.",
  invalid_pickup_date: "Chọn ngày nhận thẻ từ hôm nay trở đi.",
  invalid_pickup_time: "Chọn giờ nhận thẻ hợp lệ từ 00:00 đến 23:59.",
  pickup_schedule_incomplete: "Vui lòng chọn đủ ngày và giờ mong muốn nhận thẻ.",
});

function ticketCorrectionFields(item) {
  const fields = {
    resident_card: [
      { key: "pickup_date", label: "Ngày mong muốn nhận thẻ", type: "date" },
      { key: "pickup_time", label: "Giờ mong muốn nhận thẻ", type: "time" },
    ],
    temporary_residency: [
      { key: "person_name", label: "Họ và tên người cư trú", required: true, max: 120 },
      { key: "citizen_id", label: "Số CCCD", required: true, max: 12, pattern: "[0-9]{12}", inputmode: "numeric" },
      { key: "move_date", source: "stay_start", label: "Ngày bắt đầu cư trú", type: "date" },
    ],
    visitor_registration: [
      { key: "visitor_name", label: "Họ và tên khách", required: true, max: 120 },
      { key: "visit_date", label: "Ngày đến", type: "date", required: true },
    ],
    move_in_schedule: [
      { key: "move_date", label: "Ngày chuyển đồ", type: "date", required: true },
      { key: "move_time", label: "Giờ chuyển đồ", type: "time", required: true },
    ],
    vehicle_registration: [
      { key: "vehicle_plate", label: "Biển số xe", required: true, max: 20 },
      { key: "vehicle_type", label: "Loại phương tiện", required: true, options: [["motorbike", "Xe máy"], ["car", "Ô tô"]] },
    ],
  };
  return [...(fields[item.request_type] || []), {
    key: "replacement_details", source: "details", label: "Nội dung / ghi chú gửi BQL", type: "textarea", max: 1000,
    required: ["general_support", "internet_registration"].includes(item.request_type),
  }];
}

function ticketCorrectionChanges(item, values) {
  return Object.fromEntries(ticketCorrectionFields(item).map(field => {
    const value = String(values[field.key] ?? "").trim();
    const previous = String(item.payload?.[field.source || field.key] ?? "").trim();
    return [field.key, value, previous];
  }).filter(([key, value, previous]) => value !== previous && (value || key === "replacement_details"))
    .map(([key, value]) => [key, value]));
}

function clearTicketCorrectionSession() {
  ticketCorrectionLoadId += 1;
  const trigger = ticketCorrectionState?.trigger;
  ticketCorrectionState?.article.remove();
  ticketCorrectionState = null;
  const dialog = document.getElementById("ticket-correction-dialog");
  if (dialog?.open) dialog.close();
  if (trigger?.isConnected) trigger.focus();
}

function ticketCorrectionError(state, message) {
  const error = state.article.querySelector(".correction-error");
  error.textContent = message;
  error.hidden = !message;
  if (message) error.focus();
}

function ticketCorrectionFieldMarkup(field, item) {
  const value = String(item.payload?.[field.source || field.key] ?? "");
  const attributes = `id="correction-${field.key}" name="${field.key}" ${field.required || (value && field.type === "date") ? "required" : ""} ${field.max ? `maxlength="${field.max}"` : ""} ${field.pattern ? `pattern="${field.pattern}"` : ""} ${field.inputmode ? `inputmode="${field.inputmode}"` : ""}`;
  const input = field.options
    ? `<select ${attributes}>${field.options.map(([option, label]) => `<option value="${option}"${value === option ? " selected" : ""}>${label}</option>`).join("")}</select>`
    : field.type === "textarea" ? `<textarea ${attributes} rows="3">${escapeHtml(value)}</textarea>`
      : `<input ${attributes} type="${field.type || "text"}" value="${escapeHtml(value)}" />`;
  return `<label for="correction-${field.key}">${escapeHtml(field.label)}${field.required ? " *" : ""}${input}</label>`;
}

async function openTicketCorrection(requestId, trigger = null) {
  if (!authReady || !hasRole("resident")) return;
  if (ticketCorrectionState?.submitting) {
    showTransientNotice?.("Vui lòng chờ yêu cầu đang xử lý hoàn tất.");
    return;
  }
  if (ticketCorrectionState && !window.confirm("Đóng phần bổ sung đang nhập để mở ticket này?")) return;
  clearTicketCorrectionSession();
  const loadId = ticketCorrectionLoadId;
  const generation = authGeneration;
  if (trigger) trigger.disabled = true;
  try {
    const item = await api(`/requests/${encodeURIComponent(requestId)}?resident_id=${encodeURIComponent(currentResidentId())}`);
    if (!isSessionCurrent(generation) || loadId !== ticketCorrectionLoadId) return;
    if (!["submitted", "in_review", "needs_info"].includes(item.status)) throw new Error(apiErrorLabels.request_not_editable);
    const article = document.createElement("article");
    article.className = "ticket-correction-message";
    const vehicle = item.request_type === "vehicle_registration";
    article.innerHTML = `<div class="ticket-correction-card"><span class="correction-eyebrow">Bổ sung hồ sơ</span><h3>Chỉnh sửa ${escapeHtml(item.request_id)}</h3><p>${escapeHtml(requestTypeLabels[item.request_type] || item.request_type)} · ${escapeHtml(requestStatusLabels[item.status] || item.status)}</p>${item.decision_reason ? `<blockquote><strong>BQL yêu cầu</strong><p>${escapeHtml(item.decision_reason)}</p></blockquote>` : ""}<p>Thông tin hiện tại đã được điền sẵn. Sửa phần cần bổ sung, xem lại rồi gửi BQL. Thông tin mới thay thế thông tin cũ trên chính ticket này.</p><form class="ticket-correction-form"><div class="correction-fields">${ticketCorrectionFields(item).map(field => ticketCorrectionFieldMarkup(field, item)).join("")}${vehicle ? '<label>Ảnh giấy đăng ký xe mới (nếu cần thay ảnh)<input name="document" type="file" accept="image/jpeg,image/png,image/webp" /></label><p class="portal-help">Ảnh cũ vẫn được giữ nếu không chọn ảnh mới. Khi ảnh mờ, đính kèm ảnh mới và nhập tay biển số để BQL đối chiếu.</p><button type="button" data-correction-document>Xem ảnh đang đính kèm</button>' : ""}</div><section class="correction-review" hidden><h4>Kiểm tra phần thay đổi</h4><dl class="request-details"></dl><p>Gửi lại ${escapeHtml(item.request_id)} để BQL tiếp nhận và xem xét. Chưa được duyệt hoặc hoàn thành.</p><label class="correction-consent"><input name="confirmed" type="checkbox" /><span>Tôi đã kiểm tra và đồng ý gửi phần bổ sung đến BQL.</span></label></section><p class="correction-error field-error" role="alert" tabindex="-1" hidden></p><p class="correction-status" role="status"></p><div class="correction-actions"><button type="button" data-correction-cancel>Hủy bổ sung</button><button type="button" data-correction-back hidden>Sửa lại</button><button type="submit" data-correction-submit>Xem lại thay đổi</button></div></form></div>`;
    document.getElementById("ticket-correction-content").replaceChildren(article);
    article.querySelectorAll('input[type="time"]').forEach(input => initialize24HourTimeInput(input, item.request_type === "resident_card" ? "Giờ mong muốn nhận thẻ" : "Giờ chuyển đồ"));
    const state = { item, article, generation, trigger, phase: "edit", submitting: false, changes: null, file: null };
    ticketCorrectionState = state;
    if (chatWidget.classList.contains("open")) setChatWidgetOpen(false);
    document.getElementById("ticket-correction-dialog").showModal();
    article.querySelector("[data-correction-cancel]").addEventListener("click", () => {
      if (state.submitting) return;
      clearTicketCorrectionSession();
    });
    article.querySelector("[data-correction-back]").addEventListener("click", () => {
      state.phase = "edit";
      article.querySelector(".correction-fields").hidden = false;
      article.querySelector(".correction-review").hidden = true;
      article.querySelector("[data-correction-back]").hidden = true;
      article.querySelector("[data-correction-submit]").textContent = "Xem lại thay đổi";
      ticketCorrectionError(state, "");
      article.querySelector("input, textarea, select").focus();
    });
    article.querySelector("[data-correction-document]")?.addEventListener("click", event => void openVehicleDocument(item.request_id, event.currentTarget));
    article.querySelector("form").addEventListener("submit", event => { event.preventDefault(); void submitTicketCorrection(state); });
    article.scrollIntoView({ block: "nearest" });
    article.querySelector("input, textarea, select").focus();
  } catch (error) {
    if (isSessionCurrent(generation) && loadId === ticketCorrectionLoadId) showTransientNotice?.(error.message);
  } finally {
    if (trigger?.isConnected) trigger.disabled = false;
  }
}

async function submitTicketCorrection(state) {
  if (state !== ticketCorrectionState || !isSessionCurrent(state.generation) || state.submitting || state.phase === "sent") return;
  ticketCorrectionError(state, "");
  const form = state.article.querySelector("form");
  if (state.phase === "edit") {
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    state.changes = ticketCorrectionChanges(state.item, values);
    state.file = form.elements.namedItem("document")?.files[0] || null;
    if (state.file && (!new Set(["image/jpeg", "image/png", "image/webp"]).has(state.file.type) || state.file.size > 5 * 1024 * 1024)) {
      ticketCorrectionError(state, "Chọn ảnh JPG/PNG/WebP tối đa 5 MB.");
      return;
    }
    if (!Object.keys(state.changes).length && !state.file) {
      ticketCorrectionError(state, "Chưa có thay đổi. Sửa thông tin hoặc thêm ghi chú trả lời yêu cầu của BQL.");
      return;
    }
    const labels = new Map(ticketCorrectionFields(state.item).map(field => [field.key, field]));
    const rows = Object.entries(state.changes).map(([key, value]) => {
      const field = labels.get(key);
      const previous = state.item.payload?.[field.source || key] || "Chưa có";
      const display = field.options ? field.options.find(([option]) => option === value)?.[1] || value : value;
      return `<div><dt>${escapeHtml(field.label)}</dt><dd><del>${escapeHtml(String(previous))}</del><strong>${escapeHtml(display || "Bỏ ghi chú cũ")}</strong></dd></div>`;
    });
    if (state.file) rows.push(`<div><dt>Ảnh giấy đăng ký xe</dt><dd>${escapeHtml(state.file.name)}</dd></div>`);
    state.article.querySelector(".correction-review dl").innerHTML = rows.join("");
    form.elements.namedItem("confirmed").checked = false;
    state.article.querySelector(".correction-fields").hidden = true;
    state.article.querySelector(".correction-review").hidden = false;
    state.article.querySelector("[data-correction-back]").hidden = false;
    state.article.querySelector("[data-correction-submit]").textContent = "Gửi bổ sung đến BQL";
    state.phase = "review";
    form.elements.namedItem("confirmed").focus();
    return;
  }
  if (!form.elements.namedItem("confirmed").checked) {
    ticketCorrectionError(state, "Vui lòng xác nhận đã kiểm tra thông tin trước khi gửi.");
    return;
  }
  state.submitting = true;
  form.querySelectorAll("button, input, textarea, select").forEach(node => { node.disabled = true; });
  const status = state.article.querySelector(".correction-status");
  status.textContent = "Đang gửi phần bổ sung…";
  try {
    const payload = { ...state.changes, resubmit: true, expected_updated_at: state.item.updated_at };
    const path = `${residentPath("/requests")}/${encodeURIComponent(state.item.request_id)}`;
    let options = { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
    if (state.file) {
      const body = new FormData();
      body.append("document", state.file);
      body.append("confirmed", "true");
      body.append("payload", JSON.stringify(payload));
      options = { method: "PATCH", body };
    }
    const result = await api(path + (state.file ? "/vehicle" : ""), options);
    if (state !== ticketCorrectionState || !isSessionCurrent(state.generation)) return;
    state.article.innerHTML = `<div class="ticket-correction-card"><span class="correction-eyebrow">Đã gửi phần bổ sung</span><h3>${escapeHtml(result.request_id)}</h3><p>Thông tin mới đã thay thế thông tin cũ. BQL sẽ tiếp nhận lại hồ sơ; bạn không cần tạo ticket mới.</p>${requestDetails(result)}<button type="button" data-correction-view>Xem ticket đã cập nhật</button></div>`;
    state.article.querySelector("[data-correction-view]").addEventListener("click", () => { clearTicketCorrectionSession(); openResidentTab("tickets"); });
    state.phase = "sent";
    if (correctionChatState?.item.request_id === state.item.request_id) clearCorrectionChatSession();
    await Promise.allSettled([loadResidentTickets(), loadProgress(), loadHousehold(true), loadNotificationFeed()]);
  } catch (error) {
    if (state === ticketCorrectionState && isSessionCurrent(state.generation)) ticketCorrectionError(state, error.message);
  } finally {
    if (state === ticketCorrectionState && isSessionCurrent(state.generation)) {
      state.submitting = false;
      status.textContent = "";
      form.querySelectorAll("button, input, textarea, select").forEach(node => { node.disabled = false; });
    }
  }
}

function clearCorrectionChatSession() {
  correctionChatLoadId += 1;
  correctionChatState = null;
  correctionChatPreview?.remove();
  correctionChatPreview = null;
}

function shouldHandleTicketCorrectionChat(message) {
  const text = normalizeProcedureSearch(message);
  return Boolean(correctionChatState) || /(?:thieu (?:gi|nhung|thong tin)|bo sung (?:thong tin|ho so|gi)|(?:sua|chinh sua) (?:ticket|ho so)|bql.*(?:yeu cau|bo sung))/.test(text);
}

function isCorrectionChatConfirmation(message) {
  const text = normalizeProcedureSearch(message).replace(/[.!]+$/g, "").trim();
  return /^(?:dong y|ok|xac nhan|toi xac nhan|dong y gui|xac nhan gui|toi dong y gui|gui bo sung|gui bo sung cho bql|gui bo sung den bql|toi xac nhan gui bo sung)$/.test(text);
}

async function requestTicketCorrectionChat(message, attachments = []) {
  if (attachments.length) return prepareCorrectionAttachments(message, attachments);
  if (correctionChatState?.submitting) return { answer: "Phần bổ sung đang được gửi. Bạn chờ kết quả nhé." };
  if (correctionChatState?.preparing) return { answer: "Mình đang điền phần bổ sung mới nhất. Bạn chờ đối chiếu xong rồi xác nhận nhé." };
  if (/^(?:huy|dung|huy bo|dung bo sung|huy bo sung|huy bo sung thong tin|khong gui|chua gui|khong dong y)[.!]*$/.test(normalizeProcedureSearch(message).trim())) {
    clearCorrectionChatSession();
    return { answer: "Đã dừng bổ sung. Ticket vẫn giữ nguyên, chưa gửi thay đổi nào đến BQL." };
  }
  if (correctionChatState?.ready && isCorrectionChatConfirmation(message)) return confirmCorrectionChat(correctionChatState);
  const explicit = message.toUpperCase().match(/\bREQ-[A-Z0-9]{8}\b/)?.[0];
  if (explicit && explicit !== correctionChatState?.item.request_id) clearCorrectionChatSession();
  const generation = authGeneration;
  const loadId = ++correctionChatLoadId;
  const pendingState = correctionChatState;
  if (pendingState) pendingState.preparing = true;
  const confirmButton = correctionChatPreview?.querySelector("[data-correction-chat-confirm]");
  if (confirmButton) confirmButton.disabled = true;
  let result;
  try {
    result = await api(residentPath("/request-corrections/chat"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, request_id: explicit || pendingState?.item.request_id || null,
        draft: pendingState?.draft || {}, expected_updated_at: pendingState?.item.updated_at || null }),
    });
  } finally {
    if (pendingState && pendingState === correctionChatState) pendingState.preparing = false;
    if (confirmButton?.isConnected) confirmButton.disabled = false;
  }
  if (!isSessionCurrent(generation)) return result;
  if (loadId !== correctionChatLoadId) return { answer: "Phần bổ sung này đã dừng hoặc được thay thế. Chưa gửi thay đổi nào từ bản chờ gửi này." };
  if (result.ticket) correctionChatState = { item: result.ticket, draft: result.draft, ready: result.ready_to_confirm, generation, submitting: false };
  return result;
}

async function prepareCorrectionAttachments(message, attachments) {
  if (correctionChatState?.submitting || correctionChatState?.preparing) return { answer: "Phần bổ sung đang xử lý. Bạn chờ một chút nhé." };
  const explicit = message.toUpperCase().match(/\bREQ-[A-Z0-9]{8}\b/)?.[0];
  const result = await requestTicketCorrectionChat(explicit ? `Ticket ${explicit} cần bổ sung gì?` : "Tôi cần bổ sung thông tin gì?");
  const state = correctionChatState;
  if (!result.ticket || !state) return { ...result, answer: `${result.answer}\n\nChọn đúng ticket rồi gửi giấy tờ để mình đọc phần cần bổ sung; chưa gửi thay đổi nào.` };
  state.preparing = true;
  const loadId = ++correctionChatLoadId;
  correctionChatPreview?.querySelectorAll("button").forEach(button => { button.disabled = true; });
  try {
    const files = attachments.map(item => item.file);
    const proposal = await readFormFiles(files, state.item.request_type, message);
    if (state !== correctionChatState || loadId !== correctionChatLoadId || !isSessionCurrent(state.generation)) return { answer: "Đã dừng đọc phần bổ sung; ticket không thay đổi." };
    const values = proposal.form_draft?.values || {};
    const draft = { ...state.draft };
    for (const [key, value] of Object.entries(values)) {
      const target = key === "details" ? "replacement_details" : key === "visit_date" && ["temporary_residency", "move_in_schedule"].includes(state.item.request_type) ? "move_date" : key;
      if (!draft[target]) draft[target] = value;
    }
    const preview = await api(residentPath("/request-corrections/chat"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: `Ticket ${state.item.request_id} cần bổ sung gì?`, request_id: state.item.request_id, draft, expected_updated_at: state.item.updated_at }),
    });
    if (state !== correctionChatState || loadId !== correctionChatLoadId || !isSessionCurrent(state.generation)) return { answer: "Đã dừng bổ sung; ticket không thay đổi." };
    correctionChatState = { item: preview.ticket, draft: preview.draft, ready: preview.ready_to_confirm, generation: state.generation, submitting: false };
    return { ...preview, answer: `Đã đọc giấy tờ và điền bản bổ sung của **${state.item.request_id}**. Thông tin bạn đã nhập được giữ nguyên.\n\n${preview.answer}` };
  } finally {
    if (state === correctionChatState) state.preparing = false;
    correctionChatPreview?.querySelectorAll("button").forEach(button => { button.disabled = false; });
  }
}

async function confirmCorrectionChat(state) {
  if (state !== correctionChatState || !state.ready || state.submitting || state.preparing || !isSessionCurrent(state.generation)) throw new Error("Phần bổ sung không còn ở trạng thái chờ xác nhận.");
  state.submitting = true;
  correctionChatPreview?.querySelectorAll("button").forEach(button => { button.disabled = true; });
  try {
    const result = await api(`${residentPath("/requests")}/${encodeURIComponent(state.item.request_id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...state.draft, resubmit: true, expected_updated_at: state.item.updated_at }),
    });
    if (!isSessionCurrent(state.generation) || state !== correctionChatState) return { answer: "" };
    clearCorrectionChatSession();
    await Promise.allSettled([loadResidentTickets(), loadProgress(), loadHousehold(true), loadNotificationFeed()]);
    return { answer: `Đã cập nhật **${result.request_id}** và gửi phần bổ sung đến BQL. Thông tin mới thay thế thông tin cũ trên chính ticket này; BQL sẽ tiếp nhận lại và xem xét.`, corrected_ticket: result };
  } finally {
    if (state === correctionChatState) {
      state.submitting = false;
      correctionChatPreview?.querySelectorAll("button").forEach(button => { button.disabled = false; });
    }
  }
}

function renderCorrectionChatResult(result, host = null) {
  if (!result.ticket && !result.tickets?.length) return;
  correctionChatPreview?.remove();
  const article = document.createElement("article");
  article.className = host ? "ticket-correction-message" : "message assistant ticket-correction-message";
  if (result.tickets?.length) {
    article.innerHTML = `<div class="ticket-correction-card"><h3>Chọn ticket cần bổ sung</h3>${result.tickets.map(item => `<button type="button" data-correction-choice="${escapeHtml(item.request_id)}">${escapeHtml(item.request_id)} · ${escapeHtml(requestTypeLabels[item.request_type] || item.request_type)}</button><p>${escapeHtml(item.reason || "")}</p>`).join("")}</div>`;
    article.querySelectorAll("[data-correction-choice]").forEach(button => button.addEventListener("click", () => sendSuggestedPrompt(`Ticket ${button.dataset.correctionChoice} cần bổ sung gì?`)));
  } else {
    const state = correctionChatState;
    const fields = new Map(ticketCorrectionFields(result.ticket).map(field => [field.key, field]));
    const rows = Object.entries(result.draft || {}).map(([key, value]) => {
      const field = fields.get(key);
      if (!field) return "";
      const previous = result.ticket.payload?.[field.source || key] || "Chưa có";
      return `<div><dt>${escapeHtml(field.label)}</dt><dd><del>${escapeHtml(String(previous))}</del><strong>${escapeHtml(String(value))}</strong></dd></div>`;
    }).join("");
    article.innerHTML = `<div class="ticket-correction-card"><span class="correction-eyebrow">Đang hỗ trợ ${escapeHtml(result.ticket.request_id)} · Chưa gửi</span>${rows ? `<dl class="request-details correction-chat-values">${rows}</dl>` : ""}<div class="correction-actions">${result.ready_to_confirm ? '<button type="button" data-correction-chat-confirm>Xác nhận gửi bổ sung</button>' : ""}<button type="button" data-correction-direct>Điền trực tiếp</button><button type="button" data-correction-chat-cancel>Dừng bổ sung</button></div><p class="correction-error field-error" role="alert" hidden></p></div>`;
    article.querySelector("[data-correction-direct]").addEventListener("click", event => void openTicketCorrection(result.ticket.request_id, event.currentTarget));
    article.querySelector("[data-correction-chat-cancel]").addEventListener("click", () => { if (!state?.submitting) clearCorrectionChatSession(); });
    article.querySelector("[data-correction-chat-confirm]")?.addEventListener("click", async () => {
      try { const response = await confirmCorrectionChat(state); if (response.answer) addMessage("assistant", response.answer); }
      catch (error) { if (state === correctionChatState && isSessionCurrent(state.generation)) { const node = article.querySelector(".correction-error"); node.hidden = false; node.textContent = error.message; } }
    });
  }
  correctionChatPreview = article;
  const actionHost = host?.querySelector(".a2ui-surface");
  if (actionHost) actionHost.before(article);
  else document.getElementById("messages").appendChild(article);
  article.scrollIntoView({ block: "nearest" });
}

function closeTicketCorrectionDialog() {
  if (!ticketCorrectionState?.submitting) clearTicketCorrectionSession();
}

document.getElementById("ticket-correction-close").addEventListener("click", closeTicketCorrectionDialog);
document.getElementById("ticket-correction-dialog").addEventListener("cancel", event => { event.preventDefault(); closeTicketCorrectionDialog(); });
document.getElementById("ticket-correction-dialog").addEventListener("close", () => { if (ticketCorrectionState) clearTicketCorrectionSession(); });
