(() => {
  function returnToLogin() {
    clearStoredSession();
    try { localStorage.removeItem(MANAGEMENT_CURSOR_KEY); } catch {}
    window.location.replace(window.location.pathname);
  }

  window.addEventListener("storage", (event) => {
    if (event.key === "moveinmate.reset.event" && event.newValue) returnToLogin();
  });

  let checking = false;
  async function checkSession() {
    if (!authReady || checking || authConfig?.auth_mode === "demo") return;
    checking = true;
    const generation = authGeneration;
    try {
      const response = await authorizedFetch(`${API}/me/context`, { cache: "no-store" });
      if (response.status === 401 && generation === authGeneration) returnToLogin();
    } catch {}
    finally { checking = false; }
  }

  window.setInterval(() => { if (hasRole("manager")) void checkSession(); }, 6000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void checkSession();
  });
})();
