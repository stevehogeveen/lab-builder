(function () {
  function getStatusEl() {
    return document.getElementById("live-inventory-status");
  }

  function actionForm(el) {
    if (!el) return null;
    if (el.matches && el.matches("[data-live-inventory-action]")) return el;
    return el.closest ? el.closest("[data-live-inventory-action]") : null;
  }

  function setText(root, selector, text) {
    const el = root.querySelector(selector);
    if (el) el.textContent = text;
  }

  function setStatus(state, title, details, busy) {
    const root = getStatusEl();
    if (!root) return;

    const statusClass = state === "Failed" ? "pending" : state === "Complete" ? "ready" : "progress";
    setText(root, "[data-live-inventory-status-title]", title || state);

    const stateEl = root.querySelector("[data-live-inventory-status-state]");
    if (stateEl) {
      stateEl.textContent = state;
      stateEl.className = "status " + statusClass;
    }

    const spinnerEl = root.querySelector("[data-live-inventory-spinner]");
    if (spinnerEl) {
      spinnerEl.classList.toggle("hidden", !busy);
    }

    const detailsEl = root.querySelector("[data-live-inventory-status-details]");
    if (detailsEl) {
      detailsEl.replaceChildren();
      (details && details.length ? details : ["No additional details."]).forEach(function (detail) {
        const div = document.createElement("div");
        div.textContent = detail;
        detailsEl.appendChild(div);
      });
    }
  }

  function cleanFailureText(text) {
    const firstLine = (text || "The Live Inventory action failed.").split(/\r?\n/).find(Boolean);
    return firstLine || "The Live Inventory action failed.";
  }

  function filenameFromDisposition(disposition, fallback) {
    const match = /filename="?([^"]+)"?/i.exec(disposition || "");
    return match ? match[1] : fallback;
  }

  function downloadDetails(response, filename) {
    const details = ["Downloaded: " + filename];
    const summaryPath = response.headers.get("X-Live-Inventory-Summary-Path");
    const rawPath = response.headers.get("X-Live-Inventory-Raw-Path");
    const label = response.headers.get("X-Live-Inventory-Label");
    const host = response.headers.get("X-Live-Inventory-Host");

    if (summaryPath) details.push("Summary file: " + summaryPath);
    if (rawPath) details.push("Raw file: " + rawPath);
    if (label) details.push("Label: " + label);
    if (host) details.push("Host: " + host);
    return details;
  }

  async function handleDownload(form) {
    const startText = form.dataset.liveInventoryStart || "Starting...";
    const progressText = form.dataset.liveInventoryProgress || startText;
    setStatus(startText, startText, [progressText], true);

    try {
      const response = await fetch(form.action, {
        method: form.method || "POST",
        body: new FormData(form),
      });

      if (!response.ok) {
        const text = await response.text();
        setStatus("Failed", "Failed", [cleanFailureText(text)], false);
        return;
      }

      const blob = await response.blob();
      const filename = filenameFromDisposition(
        response.headers.get("Content-Disposition"),
        "live-inventory-download"
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);

      setStatus("Complete", "Complete", downloadDetails(response, filename), false);
    } catch (error) {
      setStatus("Failed", "Failed", [cleanFailureText(error && error.message)], false);
    }
  }

  document.body.addEventListener("submit", function (evt) {
    const form = actionForm(evt.target);
    if (!form) return;

    const startText = form.dataset.liveInventoryStart || "Starting...";
    const progressText = form.dataset.liveInventoryProgress || startText;
    setStatus(startText, startText, [progressText], true);

    if (form.hasAttribute("data-live-inventory-download")) {
      evt.preventDefault();
      handleDownload(form);
    }
  }, true);

  document.body.addEventListener("htmx:beforeRequest", function (evt) {
    const form = actionForm(evt.detail && evt.detail.elt);
    if (!form || form.hasAttribute("data-live-inventory-download")) return;
    const startText = form.dataset.liveInventoryStart || "Starting...";
    const progressText = form.dataset.liveInventoryProgress || startText;
    setStatus(startText, startText, [progressText], true);
  });

  document.body.addEventListener("htmx:beforeSwap", function (evt) {
    const form = actionForm(evt.detail && evt.detail.requestConfig && evt.detail.requestConfig.elt);
    if (!form || form.hasAttribute("data-live-inventory-download")) return;
    const savingText = form.dataset.liveInventorySaving;
    if (savingText) {
      setStatus("Saving summary/raw files", savingText, [savingText], true);
    }
  });

  document.body.addEventListener("htmx:responseError", function (evt) {
    const form = actionForm(evt.detail && evt.detail.requestConfig && evt.detail.requestConfig.elt);
    if (!form) return;
    const xhr = evt.detail && evt.detail.xhr;
    setStatus("Failed", "Failed", [cleanFailureText(xhr && xhr.responseText)], false);
  });
})();
