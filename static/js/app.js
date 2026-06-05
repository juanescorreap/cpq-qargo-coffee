// app.js — Global client behaviour for CPQ Qargo Coffee.
//
// HTMX only swaps the DOM on 2xx responses. On a non-2xx (500, 422, ...) or a
// network failure HTMX does nothing, so without this the user sees a silent
// no-op. We surface those here as a toast (FRONTEND_AUDIT #3). Listeners are
// delegated from document.body, so they keep working across HTMX navigations
// (the body is never swapped).

(function () {
  "use strict";

  // Server replied with a non-2xx status.
  document.body.addEventListener("htmx:responseError", function (e) {
    var xhr = e.detail && e.detail.xhr;
    var code = xhr ? xhr.status : "?";
    showToast("Error " + code + ": la operación falló. Reintenta.", "error");
  });

  // Request never reached the server (offline / DNS / CORS).
  document.body.addEventListener("htmx:sendError", function () {
    showToast("Sin conexión con el servidor.", "error");
  });

  // Request exceeded its timeout.
  document.body.addEventListener("htmx:timeout", function () {
    showToast("La solicitud tardó demasiado. Reintenta.", "error");
  });

  function toastContainer() {
    var c = document.getElementById("toast-container");
    if (!c) {
      c = document.createElement("div");
      c.id = "toast-container";
      c.setAttribute("aria-live", "assertive");
      c.className = "fixed bottom-4 right-4 z-[100] flex flex-col gap-2";
      document.body.appendChild(c);
    }
    return c;
  }

  function showToast(message, kind) {
    var colors = kind === "error" ? "bg-red-600 text-white" : "bg-espresso text-cream";
    var t = document.createElement("div");
    t.setAttribute("role", "alert");
    t.className = "px-4 py-2 rounded-lg shadow-lg text-sm font-medium " + colors;
    t.textContent = message;
    toastContainer().appendChild(t);
    setTimeout(function () {
      t.remove();
    }, 5000);
  }

  // Expose for manual use from templates / Alpine if ever needed.
  window.showToast = showToast;
})();
