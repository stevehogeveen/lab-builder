(function () {
    function parseJobPayload(text) {
        const lines = String(text || "").split("\n");
        const data = {};
        let currentKey = null;
        let currentMapKey = null;

        function parseScalar(value) {
            let parsed = String(value == null ? "" : value).trim();
            parsed = parsed.replace(/^["']|["']$/g, "");
            if (/^\d+$/.test(parsed)) return parseInt(parsed, 10);
            if (parsed === "true") return true;
            if (parsed === "false") return false;
            return parsed;
        }

        for (const rawLine of lines) {
            const line = rawLine.replace(/\r$/, "");
            if (!line.trim()) continue;

            if (/^\s+/.test(line) && currentMapKey && data[currentMapKey] && typeof data[currentMapKey] === "object" && !Array.isArray(data[currentMapKey])) {
                const nestedMatch = line.match(/^\s+([^:\s][^:]*):\s*(.*)$/);
                if (nestedMatch) {
                    const nestedKey = nestedMatch[1].trim();
                    const nestedValue = parseScalar(nestedMatch[2]);
                    data[currentMapKey][nestedKey] = nestedValue;
                    continue;
                }
            }

            if (/^\s+/.test(line) && currentKey && Array.isArray(data[currentKey]) && data[currentKey].length === 0) {
                const nestedMatch = line.match(/^\s+([^:\s][^:]*):\s*(.*)$/);
                if (nestedMatch) {
                    data[currentKey] = {};
                    currentMapKey = currentKey;
                    data[currentMapKey][nestedMatch[1].trim()] = parseScalar(nestedMatch[2]);
                    continue;
                }
            }

            if (/^\s*-\s/.test(line)) {
                if (currentKey && Array.isArray(data[currentKey])) {
                    let item = line.replace(/^\s*-\s/, "").trim();
                    item = parseScalar(item);
                    data[currentKey].push(item);
                }
                continue;
            }

            // YAML list items can wrap onto indented continuation lines.
            // Keep appending those lines to the most recent list entry.
            if (/^\s+/.test(line) && currentKey && Array.isArray(data[currentKey]) && data[currentKey].length) {
                const idx = data[currentKey].length - 1;
                const prev = String(data[currentKey][idx] == null ? "" : data[currentKey][idx]);
                data[currentKey][idx] = `${prev} ${line.trim()}`.trim();
                continue;
            }

            // Only parse top-level keys to avoid misreading wrapped values.
            const keyMatch = line.match(/^([^:\s][^:]*):\s*(.*)$/);
            if (keyMatch) {
                const key = keyMatch[1].trim();
                let value = keyMatch[2].trim();
                if (value === "") {
                    data[key] = [];
                    currentMapKey = null;
                } else {
                    data[key] = parseScalar(value);
                    currentMapKey = null;
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


    function updateExecutionLogViews(logs) {
        const rawLogs = Array.isArray(logs) ? logs.map(cleanLogLine).filter(Boolean) : [];
        const rawNode = document.getElementById("execution-live-logs");
        if (rawNode) {
            rawNode.textContent = rawLogs.length
                ? rawLogs.join("\n")
                : "Nothing is running right now. Start a preview or a real run to see live updates here.";
        }
    }

    function renderExecutionFailureDetails(data) {
        const shell = document.getElementById("execution-failure-details");
        if (!shell) return;
        const status = String(data.status || "").toLowerCase();
        const scope = String(data.scope || "").toLowerCase();
        const stage = String(data.current_stage || "").toLowerCase();
        const area = String(data.failure_area || "").toLowerCase();
        const isStorageFailure = status === "failed" && (area === "storage" || scope.includes("storage") || stage.includes("storage"));
        shell.style.display = isStorageFailure ? "" : "none";
        if (!isStorageFailure) return;
        setText("execution-failure-reason", data.failure_reason || "Storage run failed.");
        setText("execution-failure-explanation", data.failure_explanation || "Storage stage failed before completion.");
        setText("execution-failure-fix", data.failure_recommended_fix || "Run storage discovery again, re-approve storage, then rerun the stage.");
        setText("execution-failure-codex", data.failure_codex_handoff || "");
    }

    function parseScopeStages(scope, currentStage) {
        const orderedTokens = ["ilo", "storage", "esxi", "windows", "qnap", "iosafe", "cisco_switch"];
        const labels = {
            ilo: "iLO",
            storage: "Storage",
            esxi: "ESXi",
            windows: "Windows",
            qnap: "QNAP",
            iosafe: "ioSafe",
            cisco_switch: "Cisco Switch",
        };
        const chosen = [];
        const raw = String(scope || "");
        const parts = raw.split("__");
        for (const token of orderedTokens) {
            if (parts.includes(token)) chosen.push(token);
        }
        if (parts.includes("included") && chosen.length === 0) {
            chosen.push("ilo", "storage", "esxi");
        }
        if (!chosen.length) {
            const stage = String(currentStage || "").toLowerCase();
            if (stage.includes("ilo")) chosen.push("ilo");
            if (stage.includes("storage")) chosen.push("storage");
            if (stage.includes("esxi")) chosen.push("esxi");
            if (stage.includes("windows")) chosen.push("windows");
            if (stage.includes("qnap")) chosen.push("qnap");
            if (stage.includes("iosafe")) chosen.push("iosafe");
            if (stage.includes("cisco")) chosen.push("cisco_switch");
        }
        return chosen.map(function (token) {
            return { token: token, label: labels[token] || token };
        });
    }

    function detectActiveStageToken(currentStage) {
        const stage = String(currentStage || "").toLowerCase();
        if (stage.includes("storage")) return "storage";
        if (stage.includes("esxi")) return "esxi";
        if (stage.includes("windows")) return "windows";
        if (stage.includes("qnap")) return "qnap";
        if (stage.includes("iosafe")) return "iosafe";
        if (stage.includes("cisco")) return "cisco_switch";
        if (stage.includes("ilo")) return "ilo";
        return "";
    }

    function buildExecutionChecklist(data) {
        const status = String(data.status || "Idle");
        const stages = parseScopeStages(data.root_scope || data.scope, data.current_stage);
        const activeToken = detectActiveStageToken(data.current_stage);
        const isFailed = status.toLowerCase() === "failed";
        const isComplete = ["complete", "completed", "preview complete"].includes(status.toLowerCase()) || status.toLowerCase() === "finished";
        const stageStatuses = data.stage_statuses && typeof data.stage_statuses === "object" ? data.stage_statuses : {};

        return stages.map(function (stage, idx) {
            const explicit = String(stageStatuses[stage.token] || "").toLowerCase();
            let state = "pending";
            if (explicit === "completed") {
                state = "done";
            } else if (explicit === "running") {
                state = "running";
            } else if (explicit === "failed") {
                state = "failed";
            } else if (explicit === "skipped") {
                state = "pending";
            } else if (isComplete) {
                state = "done";
            } else if (isFailed && activeToken && stage.token === activeToken) {
                state = "failed";
            } else if (!explicit && activeToken && stage.token === activeToken) {
                state = "running";
            }
            return { label: stage.label, state: state, token: stage.token };
        });
    }

    function stageKeywords(token) {
        if (token === "ilo") return ["validating ilo", "reset ilo", "finish ilo stage", "verify ilo", "dns", "snmp", "local user"];
        if (token === "storage") return ["storage", "raid", "volume", "reboot"];
        if (token === "esxi") return ["esxi", "iso", "kickstart", "virtual media", "boot"];
        return [token];
    }

    function lastRelevantStageLog(token, logs) {
        const items = Array.isArray(logs) ? logs.map(cleanLogLine).filter(Boolean) : [];
        const keys = stageKeywords(token);
        for (let i = items.length - 1; i >= 0; i -= 1) {
            const line = String(items[i] || "");
            const lower = line.toLowerCase();
            if (keys.some(function (key) { return lower.includes(key); })) {
                return line;
            }
        }
        return "";
    }

    function stageStepSummary(item, data) {
        const currentStage = String(data.current_stage || "");
        const active = detectActiveStageToken(currentStage);
        if (active && active === item.token) {
            return currentStage || "Running";
        }
        if (item.state === "done") return "Stage complete.";
        if (item.state === "failed") return "Stage failed.";
        if (item.state === "running") return currentStage || "Running";
        return "Waiting to start.";
    }

    function stageSteps(token) {
        if (token === "ilo") {
            return [
                "Validate iLO config",
                "Connect to Redfish",
                "Apply iLO network/DNS/SNMP/users",
                "Finalize iLO stage",
            ];
        }
        if (token === "storage") {
            return [
                "Validate approved storage plan",
                "Ensure server power is On",
                "Apply storage layout",
                "Reboot/validate storage result",
            ];
        }
        if (token === "esxi") {
            return [
                "Validate ESXi inputs",
                "Build and stage ESXi ISO",
                "Prepare boot/virtual media",
                "Power on and wait for ESXi network",
            ];
        }
        return ["Run stage"];
    }

    function stageCurrentStepIndex(token, data) {
        const stageText = String((data || {}).current_stage || "").toLowerCase();
        if (token === "ilo") {
            if (stageText.includes("validating ilo")) return 0;
            if (stageText.includes("connecting to")) return 1;
            if (stageText.includes("apply") || stageText.includes("dns") || stageText.includes("snmp") || stageText.includes("local user")) return 2;
            if (stageText.includes("finish ilo") || stageText.includes("reset ilo") || stageText.includes("verify ilo")) return 3;
            return 0;
        }
        if (token === "storage") {
            if (stageText.includes("run storage stage") || stageText.includes("choose storage")) return 0;
            if (stageText.includes("power on") || stageText.includes("power state")) return 1;
            if (stageText.includes("apply storage") || stageText.includes("delete") || stageText.includes("create")) return 2;
            if (stageText.includes("reboot") || stageText.includes("post-reboot") || stageText.includes("validation")) return 3;
            return 0;
        }
        if (token === "esxi") {
            if (stageText.includes("validation failed") || stageText.includes("validate esxi")) return 0;
            if (stageText.includes("build iso") || stageText.includes("ks.cfg") || stageText.includes("generated iso")) return 1;
            if (stageText.includes("mount iso") || stageText.includes("boot override") || stageText.includes("virtual media") || stageText.includes("power off")) return 2;
            if (stageText.includes("power on") || stageText.includes("wait for esxi network") || stageText.includes("esxi error")) return 3;
            return 0;
        }
        return 0;
    }

    function renderStageTimeline(token, state, data) {
        const steps = stageSteps(token);
        const activeIndex = stageCurrentStepIndex(token, data);
        return steps.map(function (step, idx) {
            let badge = "up next";
            let klass = "progress";
            let symbol = "○";
            if (state === "done") {
                badge = "done";
                klass = "ready";
                symbol = "✓";
            } else if (state === "pending") {
                badge = "up next";
            } else if (state === "running") {
                if (idx < activeIndex) {
                    badge = "done";
                    klass = "ready";
                    symbol = "✓";
                } else if (idx === activeIndex) {
                    badge = "running";
                    symbol = "◔";
                }
            } else if (state === "failed") {
                if (idx < activeIndex) {
                    badge = "done";
                    klass = "ready";
                    symbol = "✓";
                } else if (idx === activeIndex) {
                    badge = "failed";
                    klass = "pending";
                    symbol = "×";
                }
            }
            return (
                '<div class="checklist-step-row">' +
                `<span class="checklist-step-symbol">${symbol}</span>` +
                `<span class="checklist-step-label">${escapeHtml(step)}</span>` +
                `<span class="status ${klass}">${badge}</span>` +
                "</div>"
            );
        }).join("");
    }

    function renderExecutionChecklist(data) {
        const items = buildExecutionChecklist(data || {});
        const countNode = document.getElementById("execution-checklist-count");
        if (countNode) {
            const doneCount = items.filter(function (item) { return item.state === "done"; }).length;
            countNode.textContent = `${doneCount} / ${items.length || 0} complete`;
        }
        if (!items.length) {
            return (
                '<div class="checklist-item">' +
                '<span class="checklist-icon checklist-icon-pending">○</span>' +
                '<div class="checklist-label">No run started.</div>' +
                '<span class="status progress">pending</span>' +
                "</div>"
            );
        }
        return items.map(function (item) {
            let icon = "○";
            let badge = "pending";
            let tone = "progress";
            let iconClass = "checklist-icon-pending";
            if (item.state === "running") {
                icon = "◔";
                badge = "running";
                iconClass = "checklist-icon-running";
            } else if (item.state === "done") {
                icon = "✓";
                badge = "done";
                tone = "ready";
                iconClass = "checklist-icon-done";
            } else if (item.state === "failed") {
                icon = "×";
                badge = "failed";
                tone = "pending";
                iconClass = "checklist-icon-failed";
            }
            const stepSummary = stageStepSummary(item, data || {});
            const lastLog = lastRelevantStageLog(item.token, (data || {}).logs);
            return (
                '<div class="checklist-item checklist-item-expanded">' +
                '<div class="checklist-summary-row">' +
                `<span class="checklist-icon ${iconClass}">${icon}</span>` +
                `<div class="checklist-label">${escapeHtml(item.label)}</div>` +
                `<span class="status ${tone}">${badge}</span>` +
                "</div>" +
                '<div class="checklist-details checklist-details-always">' +
                `<div><strong>Current step:</strong> ${escapeHtml(stepSummary)}</div>` +
                `<div><strong>Last relevant log:</strong> ${escapeHtml(lastLog || "No stage-specific log line yet.")}</div>` +
                `<div class="checklist-steps">${renderStageTimeline(item.token, item.state, data || {})}</div>` +
                "</div>" +
                "</div>"
            );
        }).join("");
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
            updateExecutionLogViews(existingRawLogs.textContent.split("\n"));
        }

        const proto = window.location.protocol === "https:" ? "wss" : "ws";
        const wsUrl = `${proto}://${window.location.host}/ws/job/${encodeURIComponent(opts.kitName)}`;
        const ws = new WebSocket(wsUrl);

        ws.onmessage = function (event) {
            try {
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
                    updateExecutionLogViews(data.logs);
                    if (liveLogDetails && !["Idle", "Complete", "Preview complete", "Completed"].includes(status)) {
                        liveLogDetails.open = true;
                    }
                }

                const checklist = document.getElementById("execution-stage-checklist");
                if (checklist) checklist.innerHTML = renderExecutionChecklist(data);

                const stageDetails = document.getElementById("execution-live-stage-details");
                if (stageDetails) {
                    stageDetails.innerHTML = renderLiveStageCards(buildLiveStageCards(data));
                }
                renderExecutionFailureDetails(data);
            } catch (_err) {
                // Keep existing checklist/log view visible if a payload is malformed.
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
