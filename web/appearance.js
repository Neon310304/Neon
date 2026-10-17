(() => {
  const root = document.documentElement;
  let preference = "dark";
  try {
    const saved = localStorage.getItem("moveinmate:appearance");
    if (["dark", "light"].includes(saved)) preference = saved;
  } catch {}

  function apply() {
    root.dataset.theme = preference;
    root.style.colorScheme = preference;
    const dark = preference === "dark";
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0b1421" : "#edf6ff");
    const toggle = document.getElementById("appearance-toggle");
    if (!toggle) return;
    toggle.setAttribute("aria-pressed", String(dark));
    toggle.setAttribute("aria-label", dark ? "Chuyển sang giao diện sáng" : "Chuyển sang giao diện tối");
    toggle.title = dark ? "Chuyển sang giao diện sáng" : "Chuyển sang giao diện tối";
    toggle.querySelector("span").textContent = dark ? "Chế độ tối" : "Chế độ sáng";
  }

  apply();
  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("appearance-toggle")?.addEventListener("click", () => {
      preference = preference === "dark" ? "light" : "dark";
      try { localStorage.setItem("moveinmate:appearance", preference); } catch {}
      apply();
    });
    apply();
  }, { once: true });
  window.addEventListener("storage", event => {
    if (event.key !== "moveinmate:appearance") return;
    preference = event.newValue === "light" ? "light" : "dark";
    apply();
  });
})();
