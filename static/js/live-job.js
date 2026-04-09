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
        if (state === "reboot_requested") return "Reboot has been requested. Waiting for reboot completion and post-reboot validation.";
        if (state === "post_reboot_validation_complete") return "Post-reboot validation is complete for this storage run.";
        if (state === "reboot_failed") return "Reboot failed. Review the storage progress log and reboot artifacts.";
        if (state === "apply_complete") return "Storage apply completed without requiring reboot.";
        if (state === "apply_failed") return "Storage apply failed. Review the storage progress log and artifacts.";
        return "Storage workflow state will update here while the run is active.";
    }

    function setText(id, value) {
        const node = document.getElementById(id);
        if (node) node.textContent = value;
    }

    window.bindStorageProgressCard = function bindStorageProgressCard(opts) {
        const card = document.getElementById("storage-progress-card");
        if (!card || !opts || !opts.kitName) return;

        const proto = window.location.protocol === "https:" ? "wss" : "ws";
        const wsUrl = `${proto}://${window.location.host}/ws/job/${encodeURIComponent(opts.kitName)}`;
        const ws = new WebSocket(wsUrl);

        ws.onmessage = function (event) {
            const data = parseJobPayload(event.data);
            const scope = String(data.scope || "");
            if (!(scope.startsWith("storage-apply:") || scope === "storage-reboot")) return;

            setText("storage-progress-status", "Status: " + (data.status || "Idle"));
            setText("storage-progress-scope", scope);
            setText("storage-progress-stage", data.current_stage || "");
            setText("storage-progress-completed", `${data.completed_steps || 0} / ${data.total_steps || 0}`);
            setText("storage-progress-percent", `${data.progress_percent || 0}%`);
            setText("storage-progress-apply-path", data.apply_path || card.dataset.applyPath || "Pending selection");
            setText("storage-progress-reboot-required", data.reboot_required === true ? "Yes" : (data.reboot_required === false ? "No" : "Unknown"));
            setText("storage-progress-reboot-status", data.reboot_status || card.dataset.rebootStatus || "Not requested");
            setText("storage-workflow-state", data.workflow_state || card.dataset.workflowState || "idle");
            setText("storage-progress-summary", workflowSummary(data));

            const bar = document.getElementById("storage-progress-bar");
            if (bar) bar.style.width = `${data.progress_percent || 0}%`;
            const logs = document.getElementById("storage-progress-logs");
            if (logs && Array.isArray(data.logs)) logs.textContent = data.logs.join("\n");
        };
    };
})();
