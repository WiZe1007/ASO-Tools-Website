(() => {
  const root = document.documentElement;
  const storageKey = root.dataset.themeStorage || "wwa-theme";
  try {
    const saved = localStorage.getItem(storageKey) || localStorage.getItem("wwa-theme");
    if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  } catch {}
  const button = document.getElementById("accountsTheme");
  button?.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
    try { localStorage.setItem(storageKey, root.dataset.theme); } catch {}
  });
  const dialog = document.getElementById("accountDeleteDialog");
  const form = document.getElementById("accountDeleteForm");
  const confirmEmail = document.getElementById("accountDeleteConfirmEmail");
  const submit = document.getElementById("accountDeleteSubmit");
  document.querySelectorAll("[data-delete-email]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      document.getElementById("accountDeleteEmail").textContent = trigger.dataset.deleteEmail;
      confirmEmail.value = trigger.dataset.deleteEmail;
      form.action = trigger.dataset.deleteUrl;
      submit.disabled = false;
      dialog.showModal();
    });
  });
  document.getElementById("accountDeleteCancel")?.addEventListener("click", () => dialog.close());
  dialog?.addEventListener("close", () => {
    confirmEmail.value = "";
    form.removeAttribute("action");
    submit.disabled = false;
  });
  form?.addEventListener("submit", (event) => {
    if (!dialog.open || !confirmEmail.value || submit.disabled) {
      event.preventDefault();
      return;
    }
    submit.disabled = true;
  });
})();
