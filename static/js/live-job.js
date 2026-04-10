(function () {
    function parseJobPayload(text) {
        const lines = String(text || "").split("\n");
        const data = {};
        let currentKey = null;

        for (const rawLine of lines) {
            const line = rawLine.replace(/\r$/, "");
            if (!line.trim()) continue;

            if (line.startsWith("  - ")) {
                if (currentKey && Array.isArray(data[currentKey])) {
                    data[currentKey].push(line.replace(/^  - /, ""));
                }
                continue;
            }

            const idx = line.indexOf(":");
            if (idx > -1) {
                const key = line.slice(0, idx).trim();
                let value = line.slice(idx + 1).trim();
                if (value === "") {
                    data[key] = [];
                } else {
                    value = value.replace(/^["']|["']$/g, "");
                    if (/^\d+$/.test(value)) value = parseInt(value, 10);
                    if (value === "true") value = true;
                    if (value === "false") value = false;
                    data[key] = value;
                }
                currentKey = key;
            }
        }
        return data;
    }

    function workflowSummary(data) {
        const state = data.workflow_state || "";
        if (state === "staged_reboot_required") return "Changes are staged only. Reboot is required to continue the storage workflow.";
        if (state === "reboot_requested") return "Reboot has been requested. Waiting for the reboot workflow to begin.";
        if (state === "waiting_for_reboot_start") return "The reboot request was sent. Waiting for the server to leave its current running state.";
        if (state === "waiting_for_server_return") return "The server has started rebooting. Waiting for Redfish and the system inventory to come back.";
        if (state === "post_reboot_validation_pending") return "The server is back. Capturing post-reboot storage discovery and validation now.";
        if (state === "post_reboot_validation_complete") return "Post-reboot validation is complete for this storage run.";
        if (state === "reboot_failed") return "Reboot failed. Review the storage progress log and reboot artifacts.";
        if (state === "apply_complete") return "Storage apply completed without requiring reboot.";
        if (state === "apply_failed") return "Storage apply failed. Review the storage progress log and artifacts.";
        return "Storage workflow state will update here while the run is active.";
    }

    function workflowLabel(data) {
        const state = data.workflow_state || "";
        if (state === "staged_reboot_required") return "Staging complete / reboot required";
        if (state === "reboot_requested") return "Reboot requested";
        if (state === "waiting_for_reboot_start") return "Waiting for reboot start";
        if (state === "waiting_for_server_return") return "Waiting for server to return";
        if (state === "post_reboot_validation_pending") return "Post-reboot validation pending";
        if (state === "post_reboot_validation_complete") return "Fully complete";
        if (state === "apply_complete") return "Fully complete";
        if (state === "reboot_failed") return "Reboot failed";
        if (state === "apply_failed") return "Apply failed";
        if (state === "running_apply") return "Staging in progress";
        if (state === "queued") return "Queued";
        return data.status || "Idle";
    }

    function setText(id, value) {
        const node = document.getElementById(id);
        if (node) node.textContent = value;
    }

    function setVisible(id, visible) {
        const node = document.getElementById(id);
        if (node) node.style.display = visible ? "" : "none";
    }

    function updateActionStatusCard(payload) {
        const card = document.getElementById("page-action-status");
        if (!card) return;
        const title = document.getElementById("page-action-status-title");
        const label = document.getElementById("page-action-status-label");
        const summary = document.getElementById("page-action-status-summary");
        card.style.display = "";
        if (title) title.textContent = payload.title || "Working";
        if (label) {
            label.textContent = payload.label || "Working";
            label.className = `status ${payload.tone || "progress"}`;
        }
        if (summary) summary.textContent = payload.summary || "";
    }

    function deriveActionPayload(source) {
        const trigger = source && source.triggeringEvent && source.triggeringEvent.detail ? source.triggeringEvent.detail.elt : null;
        const form = trigger && typeof trigger.closest === "function" ? trigger.closest("form") : null;
        const node = trigger || form || source;
        const buttonLabel = trigger && trigger.textContent ? trigger.textContent.trim() : "";
        const title = (node && node.dataset && node.dataset.actionTitle) || buttonLabel || "Working";
        const summary = (node && node.dataset && node.dataset.actionStart) || `${buttonLabel || "The action"} is running.`;
        const complete = (node && node.dataset && node.dataset.actionComplete) || `${buttonLabel || "The action"} finished.`;
        return { title: title, start: summary, complete: complete };
    }

    function scrollIntoViewSoon(node) {
        if (!node || typeof node.scrollIntoView !== "function") return;
        window.setTimeout(function () {
            node.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 60);
    }

    function scrollToHashTargetSoon() {
        const hash = window.location.hash || "";
        if (!hash || hash.length < 2) return;
        const target = document.getElementById(hash.slice(1));
        if (!target) return;
        scrollIntoViewSoon(target);
    }

    window.bindStorageProgressCard = function bindStorageProgressCard(opts) {
        const card = document.getElementById("storage-progress-card");
        if (!card || !opts || !opts.kitName) return;
        let lastWorkflowState = card.dataset.workflowState || "";
        let lastScope = card.dataset.jobScope || "";

        const proto = window.location.protocol === "https:" ? "wss" : "ws";
        const wsUrl = `${proto}://${window.location.host}/ws/job/${encodeURIComponent(opts.kitName)}`;
        const ws = new WebSocket(wsUrl);

        ws.onmessage = function (event) {
            const data = parseJobPayload(event.data);
            const scope = String(data.scope || "");
            if (!(scope.startsWith("storage-apply:") || scope === "storage-reboot")) return;

            setText("storage-progress-status", "Status: " + workflowLabel(data));
            setText("storage-progress-scope", scope);
            setText("storage-progress-stage", data.current_stage || "");
            setText("storage-progress-completed", `${data.completed_steps || 0} / ${data.total_steps || 0}`);
            setText("storage-progress-percent", `${data.progress_percent || 0}%`);
            setText("storage-progress-apply-path", data.apply_path || card.dataset.applyPath || "Pending selection");
            setText("storage-progress-reboot-required", data.reboot_required === true ? "Yes" : (data.reboot_required === false ? "No" : "Unknown"));
            setText("storage-progress-reboot-status", data.reboot_status || card.dataset.rebootStatus || "Not requested");
            setText("storage-workflow-state", workflowLabel(data));
            setText("storage-progress-summary", workflowSummary(data));

            const rebootPromptVisible = data.workflow_state === "staged_reboot_required";
            const rebootActionVisible = data.reboot_required === true && data.reboot_status !== "Running" && data.reboot_status !== "Completed";
            setVisible("storage-reboot-modal-shell", rebootPromptVisible);
            setVisible("storage-reboot-actions", rebootActionVisible);
            const rebootModal = document.getElementById("storage-reboot-modal-shell");
            const rebootActionButton = document.getElementById("storage-reboot-action-button");
            if (rebootActionButton) {
                rebootActionButton.textContent = data.reboot_status === "Failed" ? "Retry Reboot Now" : "Reboot Now";
            }

            const bar = document.getElementById("storage-progress-bar");
            if (bar) bar.style.width = `${data.progress_percent || 0}%`;
            const logs = document.getElementById("storage-progress-logs");
            if (logs && Array.isArray(data.logs)) logs.textContent = data.logs.join("\n");

            if (scope && scope !== lastScope && (scope.startsWith("storage-apply:") || scope === "storage-reboot")) {
                scrollIntoViewSoon(card);
            }
            if (data.workflow_state === "staged_reboot_required" && lastWorkflowState !== "staged_reboot_required") {
                scrollIntoViewSoon(rebootModal || card);
            } else if (
                data.workflow_state &&
                data.workflow_state !== lastWorkflowState &&
                (
                    data.workflow_state === "running_apply" ||
                    data.workflow_state === "reboot_requested" ||
                    data.workflow_state === "waiting_for_reboot_start" ||
                    data.workflow_state === "waiting_for_server_return" ||
                    data.workflow_state === "post_reboot_validation_pending"
                )
            ) {
                scrollIntoViewSoon(card);
            }

            lastWorkflowState = data.workflow_state || "";
            lastScope = scope;
        };
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", scrollToHashTargetSoon);
    } else {
        scrollToHashTargetSoon();
    }
    document.body && document.body.addEventListener("htmx:afterSwap", scrollToHashTargetSoon);
    if (document.body) {
        document.body.addEventListener("htmx:beforeRequest", function (event) {
            const target = event.detail && event.detail.target;
            if (!target || target.id !== "main-content") return;
            const payload = deriveActionPayload(event.detail.requestConfig || {});
            updateActionStatusCard({ title: payload.title, summary: payload.start, label: "Working", tone: "progress" });
        });
        document.body.addEventListener("htmx:afterRequest", function (event) {
            const target = event.detail && event.detail.target;
            if (!target || target.id !== "main-content") return;
            const payload = deriveActionPayload(event.detail.requestConfig || {});
            const successful = event.detail.xhr && event.detail.xhr.status >= 200 && event.detail.xhr.status < 400;
            updateActionStatusCard({
                title: payload.title,
                summary: successful ? payload.complete : "The action needs attention. Review the message on the page for details.",
                label: successful ? "Done" : "Warning",
                tone: successful ? "ready" : "pending",
            });
        });
    }
})();
