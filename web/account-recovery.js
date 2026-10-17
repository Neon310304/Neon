const recoveryDialog = document.getElementById("password-recovery-dialog");
const managerPasswordDialog = document.getElementById("manager-password-dialog");
let recoveryController = null;
let recoveryStep = 0;
let recoveryValues = {};
let managerResetAccount = null;
let managerResetRequest = null;
let managerResetGeneration = 0;
const recoveryFields = [
  ["username", "Tên tài khoản", "Bạn muốn lấy lại mật khẩu tài khoản nào?", 80, 1],
  ["full_name", "Họ và tên", "Họ và tên đã đăng ký với BQL là gì?", 120, 2],
  ["building", "Tòa / khu vực", "Bạn ở tòa hoặc khu vực nào? Có thể bỏ qua nếu không có.", 80, 0],
  ["apartment", "Căn hộ", "Mã căn hộ đã được BQL gắn với tài khoản là gì?", 40, 1],
  ["contact", "Thông tin liên hệ", "Số điện thoại để BQL liên hệ? BQL vẫn cần đối chiếu qua kênh đăng ký trước.", 160, 5],
  ["note", "Ghi chú", "Bạn còn sử dụng số điện thoại đã đăng ký không? Có thể ghi thời gian đến BQL đối chiếu, không gửi số CCCD hoặc ảnh giấy tờ ở đây.", 500, 0],
];
const recoveryErrors = {
  email_delivery_unavailable: "Chưa kết nối được dịch vụ email. Bạn có thể nhờ trợ lý gửi yêu cầu cho BQL.",
  verification_invalid_or_expired: "Mã không đúng, đã hết hạn hoặc đã sử dụng. Kiểm tra mã hoặc yêu cầu mã mới.",
  current_password_invalid: "Mật khẩu hiện tại không đúng.",
  recovery_rate_limited: "Bạn đã thử nhiều lần. Vui lòng đợi 10 phút hoặc liên hệ BQL trực tiếp.",
  account_not_found: "Tài khoản không còn tồn tại hoặc nằm ngoài khu bạn quản lý.",
  recovery_not_pending: "Yêu cầu này đã được xử lý hoặc hết hiệu lực. Hãy làm mới danh sách.",
  new_password_invalid: "Mật khẩu mới cần ít nhất 8 ký tự và khác mật khẩu cũ.",
  issued_account_required: "Tính năng này dành cho tài khoản cư dân được BQL cấp.",
};

function recoveryError(id, message = "") {
  const node = document.getElementById(id);
  node.textContent = recoveryErrors[message] || message;
  node.hidden = !message;
}

async function recoveryPost(path, payload, signal) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal });
}

function recoveryBubble(text, role = "assistant") {
  const node = document.createElement("p");
  node.className = `recovery-bubble ${role}`;
  node.textContent = text;
  document.getElementById("recovery-messages").append(node);
  node.scrollIntoView({ block: "nearest", behavior: "auto" });
}

function renderRecoveryStep() {
  const reviewing = recoveryStep === recoveryFields.length;
  document.getElementById("recovery-agent-form").hidden = reviewing;
  document.getElementById("recovery-review").hidden = !reviewing;
  if (reviewing) {
    const details = document.getElementById("recovery-review-details");
    details.replaceChildren();
    recoveryFields.forEach(([key, label]) => {
      const term = document.createElement("dt");
      const value = document.createElement("dd");
      term.textContent = label;
      value.textContent = recoveryValues[key] || "Không cung cấp";
      details.append(term, value);
    });
    document.getElementById("recovery-consent").checked = false;
    recoveryBubble("Thông tin cần tiếp nhận đã đủ. Tôi không thể xác thực danh tính hoặc tự đổi mật khẩu. Bạn kiểm tra rồi xác nhận gửi BQL nhé.");
    document.getElementById("recovery-consent").focus();
    return;
  }
  const [key, label, question, maximum, minimum] = recoveryFields[recoveryStep];
  const input = document.getElementById("recovery-agent-input");
  input.maxLength = maximum;
  input.minLength = minimum;
  input.required = minimum > 0;
  input.value = recoveryValues[key] || "";
  input.autocomplete = key === "username" ? "username" : "off";
  input.pattern = key === "username" ? "[a-zA-Z0-9_.@-]+" : ".*";
  document.getElementById("recovery-agent-label").textContent = label;
  document.getElementById("recovery-agent-back").disabled = recoveryStep === 0;
  document.getElementById("recovery-agent-next").textContent = minimum === 0 ? "Tiếp tục / bỏ qua" : "Tiếp tục";
  recoveryBubble(question);
  input.focus();
}

function chooseRecoveryMethod() {
  recoveryError("recovery-error");
  document.getElementById("recovery-agent").hidden = false;
  document.getElementById("recovery-result").hidden = true;
  recoveryStep = 0;
  recoveryValues = { username: document.getElementById("login-username").value.trim() };
  document.getElementById("recovery-messages").replaceChildren();
  renderRecoveryStep();
}

document.getElementById("open-password-recovery").addEventListener("click", () => {
  recoveryController?.abort();
  recoveryController = new AbortController();
  recoveryDialog.showModal();
  chooseRecoveryMethod();
});
document.getElementById("recovery-close").addEventListener("click", () => recoveryDialog.close());
recoveryDialog.addEventListener("close", () => {
  recoveryController?.abort(); recoveryController = null; recoveryValues = {};
  document.getElementById("recovery-agent-form").reset();
  document.getElementById("recovery-messages").replaceChildren();
  document.getElementById("recovery-review-details").replaceChildren();
  document.getElementById("open-password-recovery").focus();
});

document.getElementById("recovery-agent-form").addEventListener("submit", event => {
  event.preventDefault();
  const input = document.getElementById("recovery-agent-input");
  if (!event.currentTarget.reportValidity()) return;
  const value = input.value.trim();
  if (recoveryFields[recoveryStep][4] && value.length < recoveryFields[recoveryStep][4]) return;
  recoveryValues[recoveryFields[recoveryStep][0]] = value;
  recoveryBubble(value || "Bỏ qua", "user");
  recoveryStep += 1;
  renderRecoveryStep();
});
document.getElementById("recovery-agent-back").addEventListener("click", () => { recoveryStep = Math.max(0, recoveryStep - 1); renderRecoveryStep(); });
document.getElementById("recovery-edit").addEventListener("click", () => { recoveryStep = 0; renderRecoveryStep(); });
document.getElementById("recovery-send").addEventListener("click", async event => {
  if (!document.getElementById("recovery-consent").checked || event.currentTarget.disabled) { recoveryError("recovery-error", "Vui lòng xác nhận thông tin trước khi gửi."); return; }
  const current = recoveryController;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await recoveryPost("/auth/recovery/requests", { ...recoveryValues, confirmed: true }, current.signal);
    if (current !== recoveryController) return;
    document.getElementById("recovery-review").hidden = true;
    recoveryBubble(result.message);
    recoveryValues = {};
    document.getElementById("recovery-review-details").replaceChildren();
  } catch (error) { if (current === recoveryController) recoveryError("recovery-error", error.message); }
  finally { button.disabled = false; }
});

function openManagerPasswordReset(username, recoveryId = null) {
  if (!authReady || !hasRole("manager")) return;
  managerResetAccount = username;
  managerResetGeneration += 1;
  managerResetRequest = recoveryId;
  managerPasswordDialog.showModal();
  document.getElementById("manager-password-account").textContent = `Tài khoản: ${username}`;
  document.getElementById("manager-password-form").reset();
  document.getElementById("manager-password-form").hidden = false;
  document.getElementById("manager-recovery-reject").hidden = !recoveryId;
  document.getElementById("manager-password-result").hidden = true;
  recoveryError("manager-password-error");
  document.getElementById("manager-password-form").elements.reason.focus();
}
document.getElementById("account-list").addEventListener("click", event => {
  const button = event.target.closest("[data-reset-account]");
  if (button) openManagerPasswordReset(button.dataset.resetAccount);
});
document.getElementById("manager-password-close").addEventListener("click", () => managerPasswordDialog.close());
managerPasswordDialog.addEventListener("close", () => {
  managerResetGeneration += 1;
  managerResetAccount = null; managerResetRequest = null;
  document.getElementById("manager-password-secret").value = "";
  document.getElementById("manager-password-form").reset();
  document.getElementById("manager-password-copy-status").textContent = "";
});
document.getElementById("manager-password-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('[type="submit"]');
  if (!form.reportValidity() || button.disabled || !managerResetAccount) return;
  const generation = authGeneration;
  const username = managerResetAccount;
  const resetGeneration = managerResetGeneration;
  button.disabled = true;
  try {
    const result = await recoveryPost(`/management/resident-accounts/${encodeURIComponent(username)}/reset-password`, { confirmed: true, identity_checked: true, reason: form.elements.reason.value, recovery_id: managerResetRequest });
    if (!isSessionCurrent(generation) || resetGeneration !== managerResetGeneration || managerResetAccount !== username || !managerPasswordDialog.open) return;
    document.getElementById("manager-password-secret").value = result.temporary_password;
    form.hidden = true;
    document.getElementById("manager-password-result").hidden = false;
    void loadPasswordRecoveryRequests();
  } catch (error) { if (isSessionCurrent(generation) && resetGeneration === managerResetGeneration && managerPasswordDialog.open) recoveryError("manager-password-error", error.message); }
  finally { button.disabled = false; }
});
document.getElementById("manager-password-copy").addEventListener("click", async () => {
  const generation = authGeneration;
  if (!authReady || !managerResetAccount) return;
  const username = managerResetAccount;
  const resetGeneration = managerResetGeneration;
  try {
    const latest = await api(`/management/resident-accounts/${encodeURIComponent(username)}/handover`, { cache: "no-store" });
    if (!isSessionCurrent(generation) || resetGeneration !== managerResetGeneration || !managerPasswordDialog.open) return;
    document.getElementById("manager-password-secret").value = latest.temporary_password || "";
    if (!latest.temporary_password) {
      document.getElementById("manager-password-copy-status").textContent = latest.password_change_required ? "Không còn mật khẩu bàn giao" : "Cư dân đã đổi mật khẩu";
      return;
    }
    await navigator.clipboard.writeText(`MoveInMate\nTài khoản: ${username}\nMật khẩu tạm: ${latest.temporary_password}\nĐổi mật khẩu ngay khi đăng nhập lần đầu.`);
    if (isSessionCurrent(generation)) document.getElementById("manager-password-copy-status").textContent = "Đã sao chép. Chỉ bàn giao riêng cho đúng cư dân.";
  } catch { document.getElementById("manager-password-copy-status").textContent = "Hãy chọn và sao chép trực tiếp mật khẩu."; }
});

document.getElementById("manager-recovery-reject").addEventListener("click", async event => {
  const reason = document.getElementById("manager-password-form").elements.reason.value.trim();
  if (!managerResetRequest || reason.length < 5) { recoveryError("manager-password-error", "Nhập lý do từ chối ít nhất 5 ký tự."); return; }
  const requestId = managerResetRequest;
  const generation = authGeneration;
  event.currentTarget.disabled = true;
  try {
    await recoveryPost(`/management/password-recovery/${encodeURIComponent(requestId)}/reject`, { confirmed: true, reason });
    if (!isSessionCurrent(generation) || managerResetRequest !== requestId) return;
    managerPasswordDialog.close();
    void loadPasswordRecoveryRequests();
  } catch (error) { if (isSessionCurrent(generation) && managerPasswordDialog.open) recoveryError("manager-password-error", error.message); }
  finally { document.getElementById("manager-recovery-reject").disabled = false; }
});

async function loadPasswordRecoveryRequests() {
  if (!authReady || !hasRole("manager")) return;
  const generation = authGeneration;
  let list = document.getElementById("password-recovery-list");
  if (!list) {
    const section = document.createElement("section");
    section.id = "password-recovery-section";
    const title = document.createElement("h3");
    title.textContent = "Yêu cầu cấp lại mật khẩu";
    list = document.createElement("div");
    list.id = "password-recovery-list";
    section.append(title, list);
    document.getElementById("account-recovery-requests").appendChild(section);
  }
  try {
    const result = await api("/management/password-recovery");
    if (!isSessionCurrent(generation)) return;
    list.replaceChildren();
    if (!result.items.length) list.textContent = "Chưa có yêu cầu chờ đối chiếu.";
    result.items.forEach(item => {
      const card = document.createElement("article"); card.className = "recovery-request";
      const title = document.createElement("strong"); title.textContent = `${item.username} · ${item.full_name}`;
      const detail = document.createElement("p"); detail.textContent = `Khai báo: ${item.building} · ${item.apartment}. Liên hệ: ${item.contact}. ${item.note}`;
      const registered = document.createElement("p"); registered.textContent = `Hồ sơ đã cấp: ${item.registered_name} · ${item.registered_address}. ${item.matches_profile ? "Tên và căn hộ khớp; chưa xác minh danh tính." : "Thông tin khác hồ sơ — cần đối chiếu riêng."}`;
      const date = document.createElement("small"); date.textContent = new Date(item.created_at).toLocaleString("vi-VN");
      const button = document.createElement("button"); button.type = "button"; button.textContent = "Đối chiếu và cấp lại";
      button.addEventListener("click", () => openManagerPasswordReset(item.username, item.request_id));
      card.append(title, detail, registered, date, button); list.append(card);
    });
  } catch (error) { if (isSessionCurrent(generation)) list.textContent = error.message; }
}

function resetAccountRecovery() {
  managerPasswordDialog.close();
  document.getElementById("password-recovery-section")?.remove();
  document.getElementById("manager-password-secret").value = "";
  managerResetAccount = null; managerResetRequest = null;
}
