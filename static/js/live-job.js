(function () {
    function parseJobPayload(text) {
        const lines = String(text || "").split("\n");
        const data = {};
        let currentKey = null;

        for (const rawLine of lines) {
            const line = rawLine.replace(/\r$/, "");
            if (!line.trim()) continue;

            if (/^\s*-\s/.test(line)) {
                if (currentKey && Array.isArray(data[currentKey])) {
                    let item = line.replace(/^\s*-\s/, "").trim();
                    item = item.replace(/^["']|["']$/g, "");
                    if (/^\d+$/.test(item)) item = parseInt(item, 10);
                    if (item === "true") item = true;
                    if (item === "false") item = false;
                    data[currentKey].push(item);
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

    function modeSummary(data) {
        if (data.execution_mode === "preview") return "Preview only. No real changes are being made.";
        if (data.execution_mode === "real") return "Real execution is running. Changes may be made.";
        return "Choose a review above to see whether the next run is preview-only or real.";
    }

    function statusTone(status) {
        return ["Idle", "Complete", "Preview complete", "Completed"].includes(status) ? "ready" : "progress";
    }

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function liveStageTone(value, positive, negative) {
        const label = String(value || "").trim().toLowerCase();
        const positives = positive || ["verified", "completed", "already correct", "not required", "requested", "mounted", "built", "reachable"];
        const negatives = negative || ["failed", "mismatch", "blocked", "skipped", "timeout", "unreachable"];
        if (positives.includes(label)) return "ready";
        if (negatives.includes(label)) return "pending";
        return "progress";
    }

    function buildLiveStageCards(data) {
        const scope = String(data.scope || "");
        const stageText = String(data.current_stage || "").toLowerCase();
        const cards = [];

        function addCard(name, statusLabel, statusToneValue, summary, rows) {
            if (!rows.length && !summary) return;
            cards.push({
                name: name,
                statusLabel: statusLabel || "In progress",
                statusTone: statusToneValue || "progress",
                summary: summary || `${name} checks will appear here while the run is active.`,
                rows: rows
            });
        }

        const dnsStatus = String(data.dns_apply_status || "");
        const snmpStatus = String(data.snmp_apply_status || "");
        const resetStatus = String(data.ilo_reset_status || "");
        const iloRows = [];
        if (dnsStatus) iloRows.push({ label: "DNS status", value: dnsStatus });
        if (snmpStatus) iloRows.push({ label: "SNMP status", value: snmpStatus });
        if (data.local_account_status) iloRows.push({ label: "Local accounts", value: String(data.local_account_status) });
        if (resetStatus) iloRows.push({ label: "iLO reset", value: resetStatus });
        if (Object.prototype.hasOwnProperty.call(data, "ilo_final_ip_verified")) {
            iloRows.push({ label: "Final iLO IP", value: data.ilo_final_ip_verified === true ? "Verified" : "Waiting for verification" });
        }
        if (data.target_ip) iloRows.push({ label: "Target iLO IP", value: String(data.target_ip) });
        if (data.login_ip) iloRows.push({ label: "Login iLO IP", value: String(data.login_ip) });
        if (iloRows.length || scope.indexOf("ilo") > -1 || stageText.indexOf("ilo") > -1) {
            const iloStatus = resetStatus || snmpStatus || dnsStatus || "In progress";
            const iloSummaryParts = [];
            if (dnsStatus) iloSummaryParts.push(`DNS ${dnsStatus.toLowerCase()}`);
            if (snmpStatus) iloSummaryParts.push(`SNMP ${snmpStatus.toLowerCase()}`);
            if (resetStatus) iloSummaryParts.push(`iLO reset ${resetStatus.toLowerCase()}`);
            if (data.ilo_final_ip_verified === true) iloSummaryParts.push("final IP verified");
            addCard("iLO", iloStatus, liveStageTone(iloStatus), iloSummaryParts.join(", "), iloRows);
        }

        const storageRows = [];
        if (data.apply_path) storageRows.push({ label: "Apply artifact", value: String(data.apply_path) });
        if (data.workflow_state) storageRows.push({ label: "Workflow state", value: String(data.workflow_state) });
        if (Object.prototype.hasOwnProperty.call(data, "reboot_required")) {
            storageRows.push({ label: "Server reboot required", value: data.reboot_required === true ? "Yes" : "No" });
        }
        if (data.storage_server_reboot_status) storageRows.push({ label: "Server reboot status", value: String(data.storage_server_reboot_status) });
        if (storageRows.length || scope.indexOf("storage") > -1 || stageText.indexOf("storage") > -1) {
            const status = String(data.storage_server_reboot_status || data.workflow_state || "In progress");
            const summary = data.workflow_state
                ? `Storage workflow is at ${String(data.workflow_state).replace(/_/g, " ")}.`
                : "Storage progress and restart checks will appear here while the run is active.";
            addCard("Storage", status, liveStageTone(status), summary, storageRows);
        }

        const esxiRows = [];
        if (data.esxi_iso_path) esxiRows.push({ label: "Built ISO path", value: String(data.esxi_iso_path) });
        if (data.esxi_iso_url) esxiRows.push({ label: "Virtual media URL", value: String(data.esxi_iso_url) });
        if (data.esxi_expected_ip) esxiRows.push({ label: "Expected ESXi IP", value: String(data.esxi_expected_ip) });
        if (data.esxi_trace_path) esxiRows.push({ label: "Technical trace", value: String(data.esxi_trace_path) });
        if (esxiRows.length || scope.indexOf("esxi") > -1 || stageText.indexOf("esxi") > -1) {
            const status = data.esxi_iso_path ? "Built" : "In progress";
            const summaryParts = [];
            if (data.esxi_iso_path) summaryParts.push("custom ISO built");
            if (data.esxi_iso_url) summaryParts.push("virtual media prepared");
            addCard("ESXi", status, liveStageTone(status), summaryParts.join(", "), esxiRows);
        }

        return cards;
    }

    function renderLiveStageCards(cards) {
        if (!cards.length) {
            return '<div class="result md:col-span-2 xl:col-span-3" style="margin-top: 0;">Stage-by-stage checks will appear here after the run starts writing confirmed DNS, reboot, boot, and verification state.</div>';
        }
        return cards.map(function (card) {
            const rowsHtml = card.rows.map(function (row) {
                return (
                    '<div class="result" style="margin-top: 0;">' +
                    `<div class="dashboard-kicker">${escapeHtml(row.label)}</div>` +
                    `<div>${escapeHtml(row.value)}</div>` +
                    "</div>"
                );
            }).join("");
            const detailsHtml = rowsHtml
                ? (
                    '<details class="card card-compact mt-3 review-stage-detail">' +
                    '<summary class="summary-row">' +
                    '<span class="text-sm font-bold text-slate-300">Show last confirmed checks</span>' +
                    '<span class="status progress">details</span>' +
                    "</summary>" +
                    `<div class="mt-3 grid grid-cols-1 gap-2 text-sm">${rowsHtml}</div>` +
                    "</details>"
                )
                : "";
            return (
                '<div class="card card-compact review-stage-card">' +
                '<div class="flex items-center justify-between gap-3 mb-2">' +
                `<div class="font-semibold">${escapeHtml(card.name)}</div>` +
                `<span class="status ${escapeHtml(card.statusTone)}">${escapeHtml(card.statusLabel)}</span>` +
                "</div>" +
                `<div class="text-sm">${escapeHtml(card.summary)}</div>` +
                detailsHtml +
                "</div>"
            );
        }).join("");
    }

    function cleanLogLine(line) {
        return String(line == null ? "" : line)
            .trim()
            .replace(/^["']|["']$/g, "")
            .replace(/\s+/g, " ");
    }

    function logParts(line) {
        const cleaned = cleanLogLine(line);
        const match = cleaned.match(/^\[([A-Z_]+)\]\s*(.*)$/);
        if (!match) return { level: "INFO", message: cleaned };
        return { level: match[1], message: match[2] || cleaned };
    }

    function simpleLogTone(level) {
        if (level === "OK" || level === "DONE" || level === "SKIP") return "simple-log-ok";
        if (level === "WARN" || level === "BLOCKED") return "simple-log-warn";
        if (level === "FAILED" || level === "ERROR") return "simple-log-failed";
        if (level === "INFO" || level === "DISCOVER" || level === "COMPARE" || level === "DECISION" || level === "REMAP" || level === "CONFIG") return "simple-log-muted";
        return "";
    }

    function friendlyLogMessage(message, level) {
        const text = cleanLogLine(message);
        const rules = [
            [/Generating KS\.CFG/i, "Preparing the ESXi install answer file."],
            [/KS\.CFG generated/i, "ESXi install answer file is ready."],
            [/Building custom ESXi ISO/i, "Building the ESXi installer ISO."],
            [/Built ESXi ISO/i, "ESXi installer ISO is built."],
            [/BOOT\.CFG patched/i, "ESXi boot file updated."],
            [/EFI\/BOOT\/BOOT\.CFG patched/i, "UEFI boot file updated."],
            [/Ejecting previous virtual media/i, "Clearing any old virtual CD from iLO."],
            [/Mounting custom ESXi ISO/i, "Attaching the ESXi installer ISO to iLO."],
            [/Virtual media.*inserted|Inserted virtual media/i, "Installer ISO is mounted in iLO."],
            [/Setting one-time boot/i, "Telling the server to boot from the installer once."],
            [/Boot override.*read back|Generic Cd override read back/i, "Boot setting was confirmed."],
            [/Powering server off/i, "Turning the server off for a controlled boot."],
            [/Powering server on/i, "Turning the server on."],
            [/Server is off/i, "Server is off."],
            [/Server is on/i, "Server is on."],
            [/Waiting for ESXi management/i, "Waiting for ESXi to come online."],
            [/ESXi management.*reachable|management network.*reachable/i, "ESXi management network is reachable."],
            [/Storage.*preflight/i, "Checking the approved storage plan against the live server."],
            [/Storage apply.*writable|writable.*Volumes/i, "Finding a safe storage apply path."],
            [/Delete existing logical volumes/i, "Preparing to remove old storage volumes."],
            [/Create OS RAID/i, "Preparing the OS storage array."],
            [/Create Data RAID/i, "Preparing the data storage array."],
            [/Assign hot spare/i, "Preparing the hot spare."],
            [/Submit payload|submitted successfully|payload.*OK/i, "Storage changes were sent to iLO."],
            [/Post-change export/i, "Saved the storage result for review."],
            [/Request server reboot|reboot request/i, "Requesting the required server restart."],
            [/Read current storage|storage discovery/i, "Reading the current storage layout."],
            [/iLO.*auth|Signing in|Connecting to iLO/i, "Connecting to iLO."],
            [/iLO final verification|final iLO IP/i, "Confirming iLO is reachable at the expected address."],
            [/closed.*connection|RemoteDisconnected|Connection aborted/i, "iLO closed the connection; the app is checking the live state before deciding."],
        ];

        for (const rule of rules) {
            if (rule[0].test(text)) return prefixFriendly(rule[1], level);
        }

        let fallback = text
            .replace(/^Power reset request:\s*/i, "Power reset details: ")
            .replace(/\s+endpoint=.*$/i, "")
            .replace(/\s+allowed=.*$/i, "")
            .replace(/\s+http=.*$/i, "");
        if (fallback.length > 180) fallback = fallback.slice(0, 177).trimEnd() + "...";
        return prefixFriendly(fallback || "Working.", level);
    }

    function prefixFriendly(message, level) {
        if (level === "RUNNING") return "Working: " + message;
        if (level === "OK" || level === "DONE") return "Done: " + message;
        if (level === "WARN") return "Check: " + message;
        if (level === "FAILED" || level === "ERROR") return "Stopped: " + message;
        if (level === "SKIP") return "Skipped: " + message;
        if (level === "BLOCKED") return "Blocked: " + message;
        if (level === "DISCOVER") return "Found: " + message;
        if (level === "COMPARE") return "Checked: " + message;
        if (level === "DECISION") return "Decision: " + message;
        if (level === "REMAP") return "Adjusted: " + message;
        return message;
    }

    function buildSimpleLogItems(logs, data) {
        const items = [];
        const seen = new Set();
        const hiddenLevels = new Set(["INFO", "CONFIG", "DISCOVER", "COMPARE", "DECISION", "REMAP"]);
        const skipPatterns = [
            /^\s*$/,
            /^endpoint=/i,
            /^allowed=/i,
            /^http=/i,
            /^message_ids=/i,
            /^first_observed=/i,
            /^last_observed=/i,
            /^timeout=/i,
            /^POST\s+https?:/i,
            /^GET\s+https?:/i,
            /^Traceback/i,
        ];

        (logs || []).forEach(function (raw) {
            const cleaned = cleanLogLine(raw);
            if (!cleaned) return;
            if (skipPatterns.some(function (pattern) { return pattern.test(cleaned); })) return;

            const parts = logParts(cleaned);
            if (hiddenLevels.has(parts.level)) return;
            if (!parts.message || parts.message === "Nothing is running right now. Start a preview or a real run to see live updates here.") return;
            if (/BOOT\.CFG patched|EFI\/BOOT\/BOOT\.CFG patched|KS\.CFG generated/i.test(parts.message) && parts.level === "OK") return;
            const message = friendlyLogMessage(parts.message, parts.level);
            const key = `${parts.level}:${message}`;
            if (seen.has(key)) return;
            seen.add(key);
            items.push({ level: parts.level, tone: simpleLogTone(parts.level), message: message });
        });

        if (!items.length && data && data.current_stage) {
            items.push({ level: "INFO", tone: "simple-log-muted", message: `Waiting: ${data.current_stage}` });
        }
        return items.slice(-7);
    }

    function renderSimpleLogItems(logs, data) {
        const items = buildSimpleLogItems(logs, data || {});
        if (!items.length) {
            return (
                '<div class="simple-log-item simple-log-muted">' +
                '<span class="simple-log-dot"></span>' +
                '<div>Nothing is running right now. Start a preview or a real run to see updates here.</div>' +
                "</div>"
            );
        }
        return items.map(function (item) {
            return (
                `<div class="simple-log-item ${escapeHtml(item.tone)}">` +
                '<span class="simple-log-dot"></span>' +
                `<div>${escapeHtml(item.message)}</div>` +
                "</div>"
            );
        }).join("");
    }

    function updateExecutionLogViews(logs, data) {
        const rawLogs = Array.isArray(logs) ? logs.map(cleanLogLine).filter(Boolean) : [];
        const rawNode = document.getElementById("execution-live-logs");
        if (rawNode) {
            rawNode.textContent = rawLogs.length
                ? rawLogs.join("\n")
                : "Nothing is running right now. Start a preview or a real run to see live updates here.";
        }

        const simpleNode = document.getElementById("execution-simple-log");
        if (simpleNode) simpleNode.innerHTML = renderSimpleLogItems(rawLogs, data || {});

        const countNode = document.getElementById("execution-simple-log-count");
        if (countNode) countNode.textContent = rawLogs.length ? `${rawLogs.length} raw lines` : "idle";
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
        scrollIntoViewSoon(card);
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

    window.bindExecutionLiveCard = function bindExecutionLiveCard(opts) {
        const card = document.getElementById("execution-live-card");
        if (!card || !opts || !opts.kitName) return;
        const liveLogDetails = card.querySelector(".execution-live-log");
        const existingRawLogs = document.getElementById("execution-live-logs");
        if (existingRawLogs) {
            updateExecutionLogViews(existingRawLogs.textContent.split("\n"), {});
        }

        const proto = window.location.protocol === "https:" ? "wss" : "ws";
        const wsUrl = `${proto}://${window.location.host}/ws/job/${encodeURIComponent(opts.kitName)}`;
        const ws = new WebSocket(wsUrl);

        ws.onmessage = function (event) {
            const data = parseJobPayload(event.data);
            const status = String(data.status || "Idle");
            const mode = data.execution_mode_label || data.execution_mode || "Not running";
            const currentStep = data.current_stage || "Nothing is running right now.";
            const progress = `${data.progress_percent || 0}%`;
            const completed = `${data.completed_steps || 0} / ${data.total_steps || 0}`;

            setText("execution-live-mode", mode);
            setText("execution-live-step", currentStep);
            setText("execution-live-progress", progress);
            setText("execution-live-completed", completed);

            const statusNode = document.getElementById("execution-live-status");
            if (statusNode) {
                statusNode.textContent = status;
                statusNode.className = `status ${statusTone(status)}`;
            }

            const bar = document.getElementById("execution-live-bar");
            if (bar) bar.style.width = progress;

            if (Array.isArray(data.logs)) {
                updateExecutionLogViews(data.logs, data);
                if (liveLogDetails && status === "Failed") liveLogDetails.open = true;
            }

            const stageDetails = document.getElementById("execution-live-stage-details");
            if (stageDetails) {
                stageDetails.innerHTML = renderLiveStageCards(buildLiveStageCards(data));
            }
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
