(() => {
  const trigger = document.getElementById("login-reset");
  const dialog = document.getElementById("login-reset-dialog");
  const form = document.getElementById("login-reset-form");
  const code = document.getElementById("reset-code");
  const confirmation = document.getElementById("reset-confirm");
  const submit = document.getElementById("reset-submit");
  const cancel = document.getElementById("reset-cancel");
  const error = document.getElementById("reset-error");
  const notice = document.getElementById("login-reset-notice");
  let busy = false;
  const messages = {
    reset_code_invalid: "Mã Reset không đúng. Vui lòng kiểm tra lại.",
    reset_rate_limited: "Bạn đã thử quá nhiều lần hoặc vừa Reset. Vui lòng chờ 10 phút rồi thử lại.",
    reset_disabled: "Reset hiện không khả dụng.",
    reset_unavailable: "Không thể khôi phục trạng thái ban đầu. Vui lòng liên hệ Ban quản lý.",
    reset_confirmation_required: "Vui lòng xác nhận trước khi Reset.",
  };
  try {
    if (sessionStorage.getItem("moveinmate-reset-completed") === "true") {
      sessionStorage.removeItem("moveinmate-reset-completed");
      notice.textContent = "Đã Reset dữ liệu và cuộc trò chuyện. demo001–demo010 trở về tài khoản, mật khẩu ban đầu và phải đổi lại ở lần đăng nhập đầu. user/admin dùng thông tin đăng nhập cấu hình sẵn.";
      notice.hidden = false;
    }
  } catch {}
  trigger.addEventListener("click", () => {
    if (busy || trigger.hidden || trigger.disabled || !authReady || !hasRole("manager")) return;
    form.reset();
    error.hidden = true;
    dialog.showModal();
    code.focus();
  });
  cancel.addEventListener("click", () => { if (!busy) dialog.close(); });
  dialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
  dialog.addEventListener("close", () => { code.value = ""; trigger.focus(); });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !authReady || !hasRole("manager") || !confirmation.checked || !code.value || !form.reportValidity()) return;
    busy = true;
    submit.disabled = true;
    cancel.disabled = true;
    code.disabled = true;
    confirmation.disabled = true;
    form.setAttribute("aria-busy", "true");
    error.hidden = true;
    try {
      const response = await authorizedFetch("/api/v1/management/reset", {
        method: "POST", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code.value, confirm: true }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.reset !== true) {
        throw new Error(messages[result.detail] || "Không thể Reset. Kiểm tra kết nối rồi thử lại.");
      }
      clearStoredSession();
      try { localStorage.removeItem(MANAGEMENT_CURSOR_KEY); } catch {}
      try { localStorage.setItem("moveinmate.reset.event", crypto.randomUUID()); } catch {}
      try { sessionStorage.setItem("moveinmate-reset-completed", "true"); } catch {}
      window.location.replace(window.location.pathname);
    } catch (failure) {
      error.textContent = failure.message || "Không thể Reset. Vui lòng thử lại.";
      error.hidden = false;
    } finally {
      busy = false;
      submit.disabled = false;
      cancel.disabled = false;
      code.disabled = false;
      confirmation.disabled = false;
      form.removeAttribute("aria-busy");
      code.value = "";
      if (dialog.open) code.focus();
    }
  });
})();
