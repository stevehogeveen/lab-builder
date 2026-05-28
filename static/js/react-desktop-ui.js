(function () {
    const h = React.createElement;
    const root = document.getElementById("lab-builder-react-root");
    const serverState = window.LAB_BUILDER_REACT || {};

    const pageCopy = {
        dashboard: {
            title: "Dashboard",
            eyebrow: "Overview",
            what: "Monitor the active kit, run readiness, live job state, and next operator decision.",
            legacy: "/dashboard",
        },
        global_settings: {
            title: "Global Settings",
            eyebrow: "Setup Modules",
            what: "Edit the shared kit defaults that feed every setup module: network, address plan, DNS, included modules, and SNMPv3 users.",
            legacy: "/global-settings",
        },
        upgrade_helper: {
            title: "Upgrade Helper",
            eyebrow: "Setup Modules",
            what: "Review discovered firmware versions and available media before execution gates allow the build to continue.",
            legacy: "/upgrade-helper",
        },
        ilo: {
            title: "iLO setup",
            eyebrow: "Setup Modules",
            what: "Set the controller target and saved sign-in values used by Run Center.",
            legacy: "/ilo",
        },
        storage: {
            title: "Storage setup",
            eyebrow: "Setup Modules",
            what: "Review server storage discovery, RAID planning, approval, and the saved target used by execution.",
            legacy: "/storage",
        },
        esxi: {
            title: "ESXi setup",
            eyebrow: "Setup Modules",
            what: "Review ESXi install inputs, media readiness, and Run Center launch state.",
            legacy: "/esxi",
        },
        windows: {
            title: "Windows setup",
            eyebrow: "Setup Modules",
            what: "Review Windows VM identity, vSphere target, image source, and WinRM settings.",
            legacy: "/windows",
        },
        ovf_templates: {
            title: "OVF Templates",
            eyebrow: "Setup Modules",
            what: "Track reusable OVF and OVA template registration for VM workflows.",
            legacy: "/modules/ovf-templates",
        },
        qnap: {
            title: "QNAP setup",
            eyebrow: "Setup Modules",
            what: "Review QNAP hostname, address, and saved credentials used by run planning.",
            legacy: "/qnap",
        },
        netapp: {
            title: "NetApp setup",
            eyebrow: "Setup Modules",
            what: "Review ONTAP target state, safe apply status, and migration actions.",
            legacy: "/modules/netapp",
        },
        cisco: {
            title: "Cisco setup",
            eyebrow: "Setup Modules",
            what: "Review switch management, console, SSH, config preview, and approval actions.",
            legacy: "/cisco",
        },
        execution: {
            title: "Run Center",
            eyebrow: "Run",
            what: "Prepare, preview, and run the selected kit workflows while monitoring live backend status.",
            legacy: "/execution",
        },
        configuration: {
            title: "Configuration / Kits",
            eyebrow: "Manage",
            what: "Manage current kit selection, shared network values, and included modules.",
            legacy: "/configuration",
        },
        reports: {
            title: "Reports / History",
            eyebrow: "Records",
            what: "Review run history, operator events, reports, summaries, and generated artifacts.",
            legacy: "/configs",
        },
        "action-map": {
            title: "Action catalog",
            eyebrow: "Coverage",
            what: "Map React screens to preserved backend routes, compatibility forms, downloads, and live streams.",
            legacy: "/configuration",
        },
        technical: {
            title: "Technical details",
            eyebrow: "Diagnostics",
            what: "Keep logs, traces, artifact paths, and troubleshooting details separate from setup flow.",
            legacy: "/configs",
        },
    };

    function pageFromHash() {
        const key = String(window.location.hash || "#/dashboard").replace(/^#\/?/, "") || "dashboard";
        return pageCopy[key] ? key : "dashboard";
    }

    function apiGet(url) {
        return fetch(url, { headers: { Accept: "application/json" } }).then(function (response) {
            if (!response.ok) {
                throw new Error("GET " + url + " failed with HTTP " + response.status);
            }
            return response.json();
        });
    }

    function apiPost(url, body) {
        return fetch(url, {
            method: "POST",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: JSON.stringify(body || {}),
        }).then(function (response) {
            if (!response.ok) {
                throw new Error("POST " + url + " failed with HTTP " + response.status);
            }
            return response.json();
        });
    }

    function apiFormPost(url, formData) {
        return fetch(url, {
            method: "POST",
            headers: { Accept: "application/json" },
            body: formData,
        }).then(function (response) {
            if (!response.ok) {
                throw new Error("POST " + url + " failed with HTTP " + response.status);
            }
            return response.json();
        });
    }

    function legacyHtmlWarningMessage(text) {
        if (!text || typeof DOMParser === "undefined") return "";
        try {
            const doc = new DOMParser().parseFromString(text, "text/html");
            const warning = doc.querySelector(".global-warning-popup");
            if (!warning) return "";
            return warning.textContent.replace(/\s+/g, " ").trim();
        } catch (error) {
            return "";
        }
    }

    function htmlActionPost(url, fields) {
        const form = new FormData();
        Object.entries(fields || {}).forEach(function (entry) {
            const value = entry[1];
            if (Array.isArray(value)) {
                value.forEach(function (item) { form.append(entry[0], item); });
            } else {
                form.append(entry[0], value);
            }
        });
        return fetch(url, { method: "POST", body: form }).then(function (response) {
            if (!response.ok) {
                throw new Error("POST " + url + " failed with HTTP " + response.status);
            }
            return response.text();
        }).then(function (text) {
            const warning = legacyHtmlWarningMessage(text);
            if (warning) {
                throw new Error(warning);
            }
            return text;
        });
    }

    function toneClass(tone) {
        if (tone === "ready" || tone === "good") return "good";
        if (tone === "pending" || tone === "warn") return "warn";
        if (tone === "bad" || tone === "failed") return "red";
        return "blue";
    }

    function messageClass(message) {
        const tone = (message || {}).tone;
        if (tone === "info" || tone === "blue" || tone === "pending") return "message-info";
        if (tone === "error" || tone === "failed" || tone === "bad") return "message-error";
        return (message || {}).ok ? "message-good" : "message-warn";
    }

    function Pill(props) {
        return h("span", { className: "pill pill-" + toneClass(props.tone) }, props.children);
    }

    function Button(props) {
        const className = "button" + (props.primary ? " button-primary" : "");
        const isLink = props.href && !props.disabled;
        return h(
            isLink ? "a" : "button",
            {
                className: className,
                href: isLink ? props.href : undefined,
                type: isLink ? undefined : (props.type || "button"),
                onClick: props.onClick,
                disabled: isLink ? undefined : props.disabled,
            },
            props.children
        );
    }

    function DownloadButton(props) {
        return h(Button, { href: props.href, primary: props.primary, disabled: props.disabled }, props.children);
    }

    function pageKeyForHref(href, appState) {
        const clean = String(href || "").split("#")[0].replace(/\/$/, "") || "/";
        const directMap = {
            "/": "dashboard",
            "/dashboard": "dashboard",
            "/global-settings": "global_settings",
            "/upgrade-helper": "upgrade_helper",
            "/ilo": "ilo",
            "/storage": "storage",
            "/esxi": "esxi",
            "/windows": "windows",
            "/qnap": "qnap",
            "/modules/netapp": "netapp",
            "/cisco": "cisco",
            "/modules/cisco": "cisco",
            "/execution": "execution",
            "/configuration": "configuration",
            "/configs": "reports",
            "/history": "reports",
            "/modules/ovf-templates": "ovf_templates",
        };
        if (directMap[clean]) return directMap[clean];
        const pages = (appState || {}).pages || [];
        const match = pages.find(function (page) {
            return String(page.legacy_href || "").replace(/\/$/, "") === clean;
        });
        return match ? match.key : "";
    }

    function ReactAwareButton(props) {
        const key = pageKeyForHref(props.href, props.appState);
        if (key && props.onNavigate) {
            return h(Button, {
                onClick: function () { props.onNavigate(key); },
                primary: props.primary,
                disabled: props.disabled,
            }, props.children);
        }
        return h(Button, { href: props.href, primary: props.primary, disabled: props.disabled }, props.children);
    }

    function Panel(props) {
        return h("section", { className: "panel " + (props.className || "") },
            h("div", { className: "panel-header" },
                h("div", null,
                    props.label ? h("div", { className: "panel-label" }, props.label) : null,
                    h("h2", { className: "panel-title" }, props.title),
                    props.subtitle ? h("p", { className: "panel-subtitle" }, props.subtitle) : null
                ),
                props.action || null
            ),
            h("div", { className: "panel-body" }, props.children)
        );
    }

    function SetupStrip(props) {
        const items = [
            ["What this page is for", props.what || ""],
            ["What to do next", props.next || "Review this workspace and continue from the next clear action."],
            ["What happened last", props.last || "No recent activity has been recorded yet."],
        ];
        return h("section", { className: "summary-strip" }, items.map(function (item) {
            return h("div", { className: "strip-item", key: item[0] },
                h("div", { className: "strip-label" }, item[0]),
                h("div", { className: "strip-value" }, item[1])
            );
        }));
    }

    function statusDotClass(status) {
        const value = String(status || "").toLowerCase();
        if (value.includes("fail") || value.includes("block")) return "dot-warn";
        if (value.includes("running") || value.includes("progress")) return "dot-blue";
        if (value.includes("complete") || value.includes("ready")) return "dot-good";
        return "";
    }

    const sidebarMeta = {
        dashboard: { title: "Dashboard", subtitle: "Mission control", icon: "DB", tone: "progress" },
        global_settings: { title: "Global", subtitle: "Shared defaults", icon: "GL" },
        upgrade_helper: { title: "Upgrade Helper", subtitle: "Firmware planning", icon: "UP", tone: "progress" },
        ilo: { title: "iLO", subtitle: "Controller access", icon: "IL" },
        storage: { title: "Storage", subtitle: "RAID layout", icon: "ST" },
        esxi: { title: "ESXi", subtitle: "Install target", icon: "EX" },
        windows: { title: "Windows", subtitle: "VM install", icon: "WN" },
        cisco: { title: "Cisco", subtitle: "Switch setup", icon: "CS" },
        netapp: { title: "NetApp", subtitle: "ONTAP setup", icon: "NA" },
        ovf_templates: { title: "OVF Templates", subtitle: "VM media", icon: "OV", tone: "progress" },
        qnap: { title: "QNAP", subtitle: "Extended storage", icon: "QP" },
        execution: { title: "Run Center", subtitle: "Launch workflow", icon: "RC", tone: "progress" },
        reports: { title: "Reports", subtitle: "Technical details", icon: "RP", tone: "progress" },
        configuration: { title: "Configuration", subtitle: "Kits and files", icon: "CF", tone: "progress" },
        "action-map": { title: "Action catalog", subtitle: "Route coverage", icon: "AM", tone: "progress" },
        technical: { title: "Technical details", subtitle: "Logs and events", icon: "TD", tone: "progress" },
    };

    function normalizeTone(tone) {
        const value = String(tone || "progress").toLowerCase();
        if (value === "ready" || value === "good" || value === "complete") return "ready";
        if (value === "failed" || value === "blocked" || value === "warn") return "blocked";
        if (value === "pending" || value === "muted") return value;
        return "progress";
    }

    function moduleSummaryForPage(appState, pageKey) {
        const modules = (appState || {}).modules || [];
        return modules.find(function (item) { return item.key === pageKey; }) || {};
    }

    function sidebarTone(appState, pageKey) {
        const module = moduleSummaryForPage(appState, pageKey);
        const included = ((appState || {}).kit || {}).included || {};
        if ((pageKey === "windows" || pageKey === "qnap" || pageKey === "netapp") && included[pageKey] === false) return "muted";
        if (pageKey === "cisco" && included.cisco_switch === false) return "muted";
        return normalizeTone(module.tone || (sidebarMeta[pageKey] || {}).tone);
    }

    function Sidebar(props) {
        const appState = props.appState || {};
        const pagesByKey = {};
        (props.pages || []).forEach(function (page) { pagesByKey[page.key] = page; });
        const dashboard = appState.dashboard || {};
        const modules = appState.modules || [];
        const blockers = modules.reduce(function (total, item) { return total + ((item.blockers || []).length || 0); }, 0);
        const ready = modules.filter(function (item) { return normalizeTone(item.tone) === "ready"; }).length;
        const total = modules.length || 1;
        const readiness = Math.max(0, Math.min(100, Number(dashboard.readiness_percent || Math.round((ready / total) * 100)) || 0));
        const navGroups = [
            { label: "Overview", keys: ["dashboard", "global_settings", "upgrade_helper"] },
            { label: "Setup Modules", keys: ["ilo", "storage", "esxi", "windows", "cisco", "netapp", "ovf_templates"] },
            { label: "Run", keys: ["execution", "reports"] },
            { label: "Manage", keys: ["configuration", "action-map", "technical"] },
            { label: "Extended", keys: ["qnap"] },
        ];
        function navItem(pageKey) {
            const page = pagesByKey[pageKey];
            if (!page) return null;
            const meta = sidebarMeta[pageKey] || {};
            const tone = sidebarTone(appState, pageKey);
            const active = props.activePage === pageKey;
            return h("a", {
                key: pageKey,
                className: "nav-link" + (active ? " nav-link-active" : ""),
                href: "#/" + pageKey,
                onClick: function (event) {
                    event.preventDefault();
                    props.onNavigate(pageKey);
                },
            },
                h("span", { className: "nav-link-shell" },
                    h("span", { className: "nav-dot nav-dot-" + tone }),
                    h("span", { className: "nav-icon icon-tone-" + tone, "aria-hidden": "true" }, meta.icon || page.label.slice(0, 2).toUpperCase()),
                    h("span", { className: "nav-link-copy" },
                        h("span", { className: "nav-link-title" }, meta.title || page.label),
                        h("span", { className: "nav-link-subtitle" }, meta.subtitle || page.legacy_href)
                    )
                )
            );
        }
        function focusQuickJump() {
            const input = document.querySelector(".command-input");
            if (input) input.focus();
        }
        function toggleCompact() {
            document.body.classList.toggle("compact-view");
        }
        function openIssues() {
            props.onNavigate("dashboard");
            setTimeout(function () {
                const warn = document.querySelector(".message-warn, .dot-warn, .dot-blocked");
                if (warn && warn.scrollIntoView) warn.scrollIntoView({ behavior: "smooth", block: "center" });
            }, 50);
        }
        return h("aside", { className: "app-sidebar", "aria-label": "React desktop navigation" },
            h("div", { className: "brand-block" },
                h("div", { className: "eyebrow" }, "Offline Provisioning"),
                h("div", { className: "brand" }, "Lab Builder"),
                h("div", { className: "muted" }, "Work through one kit at a time using the setup pages below.")
            ),
            h("div", { className: "sidebar-kit-card" },
                h("div", { className: "sidebar-kit-top" },
                    h("span", { className: "sidebar-kit-label" }, "Kit state"),
                    h("span", { className: "status " + normalizeTone(dashboard.tone || (blockers ? "blocked" : "ready")) }, dashboard.label || (blockers ? "Needs review" : "Ready"))
                ),
                h("div", { className: "sidebar-kit-name", title: (props.kit || {}).name || serverState.currentKit || "Kit-01" }, (props.kit || {}).name || serverState.currentKit || "Kit-01"),
                h("div", { className: "sidebar-kit-meter", style: { "--meter": readiness + "%" } }, h("span", null)),
                h("div", { className: "sidebar-kit-meta" },
                    h("span", null, ready + " / " + total + " ready"),
                    h("span", null, blockers ? blockers + " blocker" + (blockers === 1 ? "" : "s") : "No blockers")
                )
            ),
            h("div", { className: "sidebar-utility-grid", "aria-label": "Operator shortcuts" },
                h("button", { className: "sidebar-tool-button", type: "button", onClick: focusQuickJump },
                    h("span", null, "Quick jump"),
                    h("span", { className: "kbd" }, "Ctrl K")
                ),
                h("button", { className: "sidebar-tool-button", type: "button", onClick: openIssues },
                    h("span", null, "Open issues"),
                    h("span", { className: "status " + (blockers ? "blocked" : "ready") }, String(blockers))
                ),
                h("button", { className: "sidebar-tool-button", type: "button", onClick: toggleCompact },
                    h("span", null, "Compact view"),
                    h("span", { className: "kbd" }, "View")
                )
            ),
            navGroups.map(function (group) {
                const items = group.keys.map(navItem).filter(Boolean);
                if (!items.length) return null;
                return h("div", { className: "nav-group", key: group.label },
                    h("div", { className: "nav-label" }, group.label),
                    items
                );
            }),
            h("div", { className: "sidebar-footer" },
                h("strong", null, (props.kit || {}).name || serverState.currentKit || "Kit-01"),
                h("span", null, "Active kit"),
                h("span", null, "Version " + ((props.app || {}).version || serverState.version || "unknown"))
            )
        );
    }

    function TopStatus(props) {
        const job = props.job || {};
        return h("header", { className: "top-status" },
            h("div", { className: "top-status-left" },
                h("div", { className: "status-cluster" },
                    h("span", { className: "status-dot dot-good", "aria-hidden": "true" }),
                    "Backend connected"
                ),
                h("div", { className: "status-cluster" },
                    h("span", { className: "status-dot dot-blue", "aria-hidden": "true" }),
                    (props.kit || {}).name || "Current kit"
                ),
                h("div", { className: "status-cluster" }, "React workspace")
            ),
            h("div", { className: "top-status-right" },
                h("div", { className: "status-cluster" },
                    h("span", { className: "status-dot " + statusDotClass(job.status), "aria-hidden": "true" }),
                    "Job: " + (job.status || "Idle")
                ),
                h("div", { className: "status-cluster" }, (job.progress_percent || 0) + "%")
            )
        );
    }

    function WorkspaceHeading(props) {
        const copy = pageCopy[props.activePage] || pageCopy.dashboard;
        return h("div", { className: "workspace-heading" },
            h("div", null,
                h("div", { className: "eyebrow" }, copy.eyebrow),
                h("h1", null, copy.title),
                h("p", { className: "heading-summary" }, copy.what)
            ),
            h("div", { className: "toolbar" },
                h(Button, { onClick: props.onRefresh }, "Refresh"),
                h(ReactAwareButton, { href: copy.legacy, appState: props.appState, onNavigate: props.onNavigate }, "Open page"),
                h(Button, { onClick: props.onToggleTechnical, primary: props.technicalOpen }, props.technicalOpen ? "Hide details" : "Show details")
            )
        );
    }

    function CommandBar(props) {
        const state = props.appState || {};
        const query = String(props.query || "").trim().toLowerCase();
        const pages = state.pages || [];
        const routes = (((state.action_catalog || {}).routes) || []);
        const pageResults = query ? pages.filter(function (page) {
            return (page.label + " " + page.key + " " + page.legacy_href).toLowerCase().indexOf(query) >= 0;
        }).slice(0, 4) : [];
        const routeResults = query ? routes.filter(function (route) {
            return (route.path + " " + route.method + " " + route.category + " " + route.migration_status).toLowerCase().indexOf(query) >= 0;
        }).slice(0, 6) : [];
        const coverage = (state.action_catalog || {}).coverage || {};
        return h("section", { className: "command-bar", "aria-label": "Workspace command bar" },
            h("div", { className: "command-main" },
                h("div", { className: "command-search" },
                    h("span", { className: "command-search-label" }, "Search"),
                    h("input", {
                        className: "command-input",
                        value: props.query || "",
                        onChange: function (event) { props.onSearch(event.target.value); },
                        placeholder: "Pages, routes, actions",
                    })
                ),
                h("div", { className: "command-actions" },
                    h(Button, { onClick: function () { props.onNavigate("execution"); } }, "Run Center"),
                    h(Button, { onClick: function () { props.onNavigate("action-map"); } }, "Action catalog"),
                    h(Button, { onClick: props.onToggleTechnical }, "Technical details")
                )
            ),
            query ? h("div", { className: "command-results" },
                pageResults.map(function (page) {
                    return h("button", {
                        className: "command-result",
                        key: "page-" + page.key,
                        type: "button",
                        onClick: function () {
                            props.onNavigate(page.key);
                            props.onSearch("");
                        },
                    },
                        h("span", { className: "command-result-title" }, page.label),
                        h("span", { className: "command-result-meta" }, page.legacy_href)
                    );
                }),
                routeResults.map(function (route) {
                    return h("button", {
                        className: "command-result",
                        key: "route-" + route.method + route.path,
                        type: "button",
                        onClick: function () {
                            props.onNavigate("action-map");
                        },
                    },
                        h("span", { className: "command-result-title" }, route.method + " " + route.path),
                        h("span", { className: "command-result-meta" }, route.category + " - " + route.migration_status)
                    );
                }),
                !pageResults.length && !routeResults.length ? h("div", { className: "command-empty" }, "No matching page or route.") : null
            ) : h("div", { className: "command-summary" },
                h("span", null, String(coverage.total_routes || 0) + " backend routes"),
                h("span", null, String(coverage.legacy_routes || 0) + " HTML action routes"),
                h("span", null, String(coverage.react_api_routes || 0) + " React APIs")
            )
        );
    }

    function LiveJobPanel(props) {
        const job = props.job || {};
        const total = job.total_steps || 0;
        const completed = job.completed_steps || 0;
        return h(Panel, {
            label: "Live job",
            title: "Run Center",
            subtitle: job.current_stage || "Nothing is running right now.",
            action: h(Pill, { tone: statusDotClass(job.status) === "dot-warn" ? "warn" : "blue" }, job.status || "Idle"),
        },
            h("div", { className: "job-hero" },
                h("div", null,
                    h("div", { className: "job-title" }, job.scope ? "Scope: " + job.scope : "No active run"),
                    h("p", { className: "job-summary" }, job.last_message || "Start a preview or prepare a run from the controls below.")
                ),
                h("div", { className: "progress-ring", role: "img", "aria-label": (job.progress_percent || 0) + " percent complete", style: { background: "conic-gradient(var(--blue) 0 " + (job.progress_percent || 0) + "%, var(--surface-3) " + (job.progress_percent || 0) + "% 100%)" } },
                    h("div", { className: "progress-ring-inner" },
                        h("div", { className: "progress-ring-value" }, (job.progress_percent || 0) + "%"),
                        h("div", { className: "progress-ring-label" }, "complete")
                    )
                )
            ),
            h("div", { className: "metric-row" },
                [["Mode", job.execution_mode_label || job.execution_mode || "Not running"], ["Current step", job.current_stage || "Idle"], ["Completed", completed + " / " + total], ["Updated", job.updated_at || "Not yet"]].map(function (metric) {
                    return h("div", { className: "metric", key: metric[0] },
                        h("div", { className: "metric-label" }, metric[0]),
                        h("div", { className: "metric-value", title: metric[1] }, metric[1])
                    );
                })
            ),
            h("div", { className: "wide-progress", "aria-hidden": "true" }, h("div", { className: "wide-progress-bar", style: { width: (job.progress_percent || 0) + "%" } })),
            props.children
        );
    }

    function ModuleCard(props) {
        const module = props.module || {};
        const isNext = props.nextHref && module.legacy_href === props.nextHref;
        return h("div", { className: "module-card" },
            h("div", { className: "data-row" },
                h("div", null,
                    h("div", { className: "data-name" }, module.label),
                    h("div", { className: "data-value" }, module.target || "Not set")
                ),
                h(Pill, { tone: module.tone }, module.state_label || "Review")
            ),
            h("p", { className: "panel-subtitle" }, module.planned_summary || "Review setup."),
            h("div", { className: "job-actions" },
                h(ReactAwareButton, { href: module.legacy_href, appState: props.appState, onNavigate: props.onNavigate, primary: isNext }, isNext ? "Open next" : "Open"),
                h(Pill, { tone: "blue" }, "Actions available")
            )
        );
    }

    function JobTimelinePanel(props) {
        const job = props.job || {};
        const modules = props.modules || [];
        const stageStatuses = job.stage_statuses || {};
        let items = Object.keys(stageStatuses).map(function (key) {
            return {
                key: key,
                title: key.replace(/_/g, " "),
                status: String(stageStatuses[key] || "waiting"),
                detail: key === job.current_stage ? (job.last_message || "Current stage") : "Stage status from the active job.",
            };
        });
        if (!items.length) {
            items = modules.slice(0, 5).map(function (module) {
                return {
                    key: module.key,
                    title: module.label,
                    status: module.state_label || "Review",
                    detail: module.planned_summary || module.last_summary || "Review setup readiness.",
                };
            });
        }
        function rowClass(status) {
            const value = String(status || "").toLowerCase();
            if (value.indexOf("complete") >= 0 || value.indexOf("ready") >= 0) return "timeline-done";
            if (value.indexOf("run") >= 0 || value.indexOf("progress") >= 0) return "timeline-running";
            if (value.indexOf("fail") >= 0 || value.indexOf("block") >= 0 || value.indexOf("warn") >= 0) return "timeline-warn";
            return "";
        }
        return h(Panel, { label: "Progress", title: "Timeline", subtitle: job.current_stage || "Stages appear here as runs update." },
            h("div", { className: "timeline" }, items.map(function (item, index) {
                return h("div", { className: "timeline-item " + rowClass(item.status), key: item.key },
                    h("div", { className: "timeline-marker" }, String(index + 1)),
                    h("div", null,
                        h("div", { className: "timeline-title" }, item.title),
                        h("div", { className: "timeline-detail" }, item.detail)
                    ),
                    h("div", { className: "timeline-time" }, item.status)
                );
            }))
        );
    }

    function WarningsPanel(props) {
        const modules = props.modules || [];
        const blockers = [];
        modules.forEach(function (module) {
            (module.blockers || []).forEach(function (blocker) {
                blockers.push({
                    key: module.key + "-" + blocker.label,
                    title: module.label + ": " + (blocker.label || "Review required"),
                    detail: blocker.fix || blocker.details || "Review the full setup page for the full form state.",
                });
            });
        });
        return h(Panel, { label: "Warnings", title: "Operator attention", subtitle: blockers.length ? String(blockers.length) + " item(s) need review before a clean run." : "No blocking warnings are reported by the summary API." },
            blockers.length ? h("div", { className: "warning-list" }, blockers.slice(0, 8).map(function (item) {
                return h("div", { className: "warning-row", key: item.key },
                    h("div", null,
                        h("div", { className: "warning-title" }, item.title),
                        h("div", { className: "warning-detail" }, item.detail)
                    ),
                    h(Pill, { tone: "warn" }, "Review")
                );
            })) : h("div", { className: "empty-state" }, "Run readiness checks are clean for the currently summarized modules.")
        );
    }

    function RecentActivityPanel(props) {
        const activity = props.activity || [];
        return h(Panel, { label: "Recent activity", title: "Latest operator and run events", subtitle: activity.length ? "Most recent events for the active kit." : "No events recorded for this kit yet." },
            activity.length ? h("div", { className: "activity-list" }, activity.slice(0, 8).map(function (item, index) {
                return h("div", { className: "activity-row", key: index },
                    h("div", null,
                        h("div", { className: "activity-title" }, item.display_title || item.title || item.event || "Activity"),
                        h("div", { className: "activity-detail" }, item.display_summary || item.summary || item.status || "")
                    ),
                    h("div", { className: "timeline-time" }, item.time || item.created_at || "")
                );
            })) : h("div", { className: "empty-state" }, "No activity has been recorded yet.")
        );
    }

    function KitSummaryPanel(props) {
        const kit = props.kit || {};
        const site = kit.site || {};
        const included = kit.included || {};
        const includedNames = Object.keys(included).filter(function (key) { return included[key]; }).join(", ") || "None";
        function summaryRow(label, value) {
            return h("div", { className: "data-row" },
                h("div", null,
                    h("div", { className: "data-name" }, label),
                    h("div", { className: "data-value" }, value || "Not set")
                )
            );
        }
        return h(Panel, { label: "Active kit", title: kit.name || "Current kit", subtitle: site.location || site.customer || "Kit configuration loaded from the backend." },
            h("div", { className: "data-list" },
                summaryRow("Site", site.name || kit.name),
                summaryRow("Network", (kit.ip_plan || {}).subnet),
                summaryRow("Included", includedNames)
            )
        );
    }

    function DashboardPage(props) {
        const state = props.appState || {};
        const dashboard = state.dashboard || {};
        const modules = state.modules || [];
        const kit = state.kit || {};
        const available = kit.available || [];
        const otherKits = available.filter(function (name) { return name !== kit.name; });
        const selectedKit = props.selectedKit || otherKits[0] || "";
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: "Monitor readiness, current job state, and the next operator decision for the active kit.",
                    next: ((dashboard.next_step || {}).title || "Review the run") + ": " + ((dashboard.next_step || {}).summary || "Open Run Center when ready."),
                    last: (dashboard.latest_result || {}).label || "No completed runs yet",
                }),
                h(Panel, { label: "Active kit", title: kit.name || "Current kit", subtitle: "Choose a saved kit or create a new one before continuing setup." },
                    h("div", { className: "section-grid" },
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Current kit"), h("div", { className: "strip-value" }, kit.name || "Not set")),
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Saved kits"), h("div", { className: "strip-value" }, String(available.length))),
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Network"), h("div", { className: "strip-value" }, (kit.ip_plan || {}).subnet || "Not set"))
                    ),
                    h("div", { className: "kit-management-grid dashboard-kit-management-grid" },
                        h("div", { className: "setup-card kit-tool-card" },
                            h("div", { className: "data-name" }, "Use an existing kit"),
                            h("select", {
                                className: "select-input",
                                value: selectedKit,
                                disabled: !otherKits.length || props.kitWorking,
                                onChange: function (event) { props.onSelectedKitChange(event.target.value); },
                            }, otherKits.length ? otherKits.map(function (name) {
                                return h("option", { key: name, value: name }, name);
                            }) : h("option", { value: "" }, "No other saved kits found")),
                            h("div", { className: "job-actions" },
                                h(Button, { onClick: function () { props.onLoadKit(selectedKit); }, disabled: !selectedKit || props.kitWorking }, props.kitWorking ? "Working..." : "Switch active kit")
                            )
                        ),
                        h("div", { className: "setup-card kit-tool-card" },
                            h("div", { className: "data-name" }, "Create a new kit"),
                            h("input", {
                                className: "text-input",
                                value: props.newKitName || "",
                                onChange: function (event) { props.onNewKitNameChange(event.target.value); },
                                placeholder: "New kit name",
                            }),
                            h("div", { className: "job-actions" },
                                h(Button, { primary: true, onClick: props.onCreateKit, disabled: !String(props.newKitName || "").trim() || props.kitWorking }, props.kitWorking ? "Working..." : "Create new kit")
                            )
                        )
                    )
                ),
                h(LiveJobPanel, { job: state.job },
                    h("div", { className: "job-actions" },
                        h(Button, { onClick: props.onPrepareReview }, "Prepare review"),
                        h(Button, { onClick: props.onStartPreview, primary: true }, "Start preview run"),
                        h(Button, { onClick: function () { props.onNavigate("execution"); } }, "Open Run Center")
                    )
                ),
                h(Panel, { label: "Readiness", title: dashboard.headline || "Dashboard", subtitle: dashboard.summary || "" },
                    h("div", { className: "section-grid" },
                        [["Readiness", (dashboard.readiness_percent || 0) + "%"], ["Ready checks", (dashboard.ready_checks || 0) + " / " + (dashboard.total_checks || 0)], ["Blockers", dashboard.total_blockers || 0]].map(function (item) {
                            return h("div", { className: "setup-card", key: item[0] },
                                h("div", { className: "metric-label" }, item[0]),
                                h("div", { className: "strip-value" }, String(item[1]))
                            );
                        })
                    )
                ),
                h(Panel, { label: "Modules", title: "Setup workspaces", subtitle: "Each workspace shows saved values, readiness, and mapped operator actions from the backend." },
                    h("div", { className: "module-grid" }, modules.map(function (module) {
                        return h(ModuleCard, { key: module.key, module: module, appState: state, nextHref: (dashboard.next_step || {}).href, onNavigate: props.onNavigate });
                    }))
                ),
                h("div", { className: "dashboard-lower-grid" },
                    h(JobTimelinePanel, { job: state.job, modules: modules }),
                    h(WarningsPanel, { modules: modules })
                ),
                h(RecentActivityPanel, { activity: state.recent_activity || [] }),
                h(ActionInventoryPanel, { activePage: "dashboard", appState: state, actions: ((state.actions || {}).dashboard || []), onNavigate: props.onNavigate })
            ),
            h(ContextPanel, { activePage: "dashboard", appState: state, onNavigate: props.onNavigate })
        );
    }

    function IloPage(props) {
        const ilo = props.iloState || {};
        const values = ilo.values || {};
        const review = ilo.review || {};
        const page = ilo.page || pageCopy.ilo;
        const sameIp = props.iloForm.current_ip && props.iloForm.target_ip && props.iloForm.current_ip === props.iloForm.target_ip;
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, { what: page.what, next: page.next, last: page.last }),
                props.message ? h("div", { className: "message " + messageClass(props.message) }, props.message.text) : null,
                h(Panel, {
                    label: "Real backend form",
                    title: "Saved iLO target",
                    subtitle: "This saves through /api/ui/ilo/settings and reuses the server-side iLO settings service.",
                    action: h(Pill, { tone: (ilo.status || {}).tone }, (ilo.status || {}).label || "Review"),
                },
                    h("form", {
                        onSubmit: function (event) {
                            event.preventDefault();
                            props.onSaveIlo();
                        },
                    },
                        h("div", { className: "form-grid" },
                            h(Field, { label: "Current reachable iLO IP", name: "current_ip", value: props.iloForm.current_ip, onChange: props.onIloChange, help: "This must be the address the app can reach right now." }),
                            h(Field, { label: "Final iLO IP to set", name: "target_ip", value: props.iloForm.target_ip, onChange: props.onIloChange, help: sameIp ? "Current and final IP match, so no address change will occur." : "This is the static address that will be applied." }),
                            h(Field, { label: "Gateway", name: "gateway", value: props.iloForm.gateway, onChange: props.onIloChange, help: "Used with the final static iLO IP." }),
                            h(Field, { label: "Hostname", name: "hostname", value: props.iloForm.hostname, onChange: props.onIloChange }),
                            h(Field, { label: "Username", name: "username", value: props.iloForm.username, onChange: props.onIloChange }),
                            h(Field, { label: "Password", name: "password", type: "password", value: props.iloForm.password, onChange: props.onIloChange, help: values.password_saved ? "Leave blank to keep the saved password." : "Enter the iLO password to save it." })
                        ),
                        h("div", { className: "job-actions" },
                            h(Button, { primary: true, type: "submit", disabled: props.savingIlo }, props.savingIlo ? "Saving..." : "Save iLO setup"),
                            h(Button, { onClick: props.onSetupIloIp, disabled: props.setupIpWorking }, props.setupIpWorking ? "Starting..." : "Setup iLO IP"),
                            h(Button, { onClick: function () { props.onNavigate("ilo"); } }, "Refresh iLO page")
                        )
                    )
                ),
                h(IloStatusPanel, { iloState: ilo, form: props.iloForm, review: review, appState: props.appState }),
                h(ActionLogPanel, { title: "Setup action log", appState: props.appState, entries: props.setupActionLog }),
                h(ActionInventoryPanel, { activePage: "ilo", appState: props.appState, actions: ilo.actions || ((props.appState || {}).actions || {}).ilo || [], onNavigate: props.onNavigate })
            ),
            h(ContextPanel, { activePage: "ilo", appState: props.appState, actions: ilo.actions, onNavigate: props.onNavigate })
        );
    }

    function Field(props) {
        return h("div", { className: "field" },
            h("label", { htmlFor: props.name }, props.label),
            h("input", {
                className: "text-input",
                id: props.name,
                name: props.name,
                type: props.type || "text",
                value: props.value || "",
                onChange: function (event) { props.onChange(props.name, event.target.value); },
            }),
            props.help ? h("div", { className: "field-help" }, props.help) : null
        );
    }

    function SelectField(props) {
        return h("div", { className: "field" },
            h("label", { htmlFor: props.name }, props.label),
            h("select", {
                className: "select-input",
                id: props.name,
                name: props.name,
                value: props.value || "",
                onChange: function (event) { props.onChange(props.name, event.target.value); },
            }, (props.options || []).map(function (option) {
                return h("option", { key: option.value, value: option.value }, option.label);
            })),
            props.help ? h("div", { className: "field-help" }, props.help) : null
        );
    }

    function ToggleField(props) {
        return h("label", { className: "toggle-field" },
            h("input", {
                type: "checkbox",
                checked: !!props.checked,
                onChange: function (event) { props.onChange(props.name, event.target.checked); },
            }),
            h("span", null,
                h("strong", null, props.label),
                props.help ? h("small", null, props.help) : null
            )
        );
    }

    function StatusStep(props) {
        const tone = props.tone || "good";
        const symbol = tone === "error" ? "!" : tone === "warn" ? "?" : "OK";
        return h("div", { className: "status-step status-step-" + tone },
            h("div", { className: "status-icon", "aria-hidden": "true" }, symbol),
            h("div", null,
                h("div", { className: "status-step-title" }, props.title),
                h("div", { className: "status-step-detail" }, props.detail)
            )
        );
    }

    function IloStatusPanel(props) {
        const review = props.review || {};
        const form = props.form || {};
        const state = props.appState || {};
        const job = state.job || {};
        const checks = review.checks || [];
        const runBlockers = checks.filter(function (check) { return !check.ok; });
        const hasInputErrors = !!(review.errors && review.errors.length);
        const hasSavedPassword = !!(((props.iloState || {}).values || {}).password_saved);
        const setupFieldsReady = !!(form.current_ip && form.target_ip && form.gateway && form.username && hasSavedPassword);
        const sameIp = form.current_ip && form.target_ip && form.current_ip === form.target_ip;
        const jobIsIloSetup = job.scope === "ilo-ip-setup";
        let lastActionTone = "good";
        let lastActionTitle = "No setup IP action is running";
        let lastActionDetail = "Use Setup iLO IP after the current reachable IP, final IP, gateway, username, and password are saved.";
        if (jobIsIloSetup) {
            if (job.status === "Failed") {
                lastActionTone = "error";
                lastActionTitle = "Last iLO IP setup failed";
                lastActionDetail = job.last_message || "The setup action failed. Check the action log below.";
            } else if (String(job.status || "").toLowerCase().indexOf("running") >= 0 || String(job.status || "").toLowerCase().indexOf("queued") >= 0) {
                lastActionTone = "warn";
                lastActionTitle = "iLO IP setup is in progress";
                lastActionDetail = job.last_message || job.current_stage || "The backend job is running.";
            } else if (job.status === "Complete") {
                lastActionTitle = "Last iLO IP setup verified";
                lastActionDetail = job.last_message || "The backend verified the final iLO IP is reachable.";
            }
        }
        return h(Panel, { label: "Status", title: "iLO setup status", subtitle: "Saved inputs, live IP setup, and full-run blockers are shown separately." },
            h("div", { className: "status-step-list" },
                h(StatusStep, {
                    tone: hasInputErrors ? "error" : setupFieldsReady ? "good" : "warn",
                    title: hasInputErrors ? "Fix saved iLO inputs" : setupFieldsReady ? "iLO fields are saved" : "Finish iLO fields",
                    detail: hasInputErrors ? review.errors.join(" ") : setupFieldsReady ? "The current IP, final IP, gateway, username, and saved password are present." : "Save the reachable iLO IP, final IP, gateway, username, and password before setup."
                }),
                sameIp ? h(StatusStep, {
                    tone: "warn",
                    title: "Current and final IP are the same",
                    detail: "Setup iLO IP will connect to this address and reapply the same static IP. Change Final iLO IP to move the controller to a new address."
                }) : null,
                h(StatusStep, {
                    tone: lastActionTone,
                    title: lastActionTitle,
                    detail: lastActionDetail
                }),
                h(StatusStep, {
                    tone: runBlockers.length ? "warn" : "good",
                    title: runBlockers.length ? "Full run still has blockers" : "Full iLO run is ready",
                    detail: runBlockers.length ? runBlockers.map(function (item) { return item.label + ": " + (item.details || item.fix || "Review required."); }).join(" ") : "No full-run blockers are reported."
                })
            )
        );
    }

    function GlobalSettingsPage(props) {
        const globalState = props.globalState || {};
        const page = globalState.page || pageCopy.global_settings;
        const form = props.globalForm || null;
        if (!form) {
            return h("div", { className: "page-layout" },
                h("div", { className: "page-main" },
                    h(SetupStrip, { what: page.what, next: page.next, last: page.last }),
                    h(Panel, { label: "Loading", title: "Global Settings", subtitle: "Reading saved shared defaults from the backend." },
                        h("div", { className: "empty-state" }, "Loading global settings...")
                    )
                ),
                h(ContextPanel, { activePage: "global_settings", appState: props.appState, actions: globalState.actions, onNavigate: props.onNavigate })
            );
        }
        const values = form.values || {};
        const included = form.included || {};
        const snmpUsers = form.snmp_users || [];
        const dnsSummary = [values.dns1, values.dns2, values.dns3, values.dns4].filter(Boolean).join(", ") || "Not set";
        const protocolOptions = [
            { value: "SHA", label: "SHA" },
            { value: "SHA256", label: "SHA256" },
            { value: "SHA384", label: "SHA384" },
            { value: "SHA512", label: "SHA512" },
            { value: "MD5", label: "MD5" },
        ];
        const privacyOptions = [
            { value: "AES", label: "AES" },
            { value: "AES256", label: "AES256" },
            { value: "DES", label: "DES" },
        ];
        function changeExtra(index, key, value) {
            props.onUpdateSnmpUser(index, key, value);
        }
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, { what: page.what, next: page.next, last: page.last }),
                props.message ? h("div", { className: "message " + messageClass(props.message) }, props.message.text) : null,
                h("form", {
                    className: "global-settings-form",
                    onSubmit: function (event) {
                        event.preventDefault();
                        props.onSaveGlobal();
                    },
                },
                    h(Panel, { label: "Summary", title: "Shared defaults", subtitle: "These values are read from and saved to the active kit." },
                        h("div", { className: "section-grid" },
                            h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Kit"), h("div", { className: "strip-value" }, values.site_name || "Not set")),
                            h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Subnet"), h("div", { className: "strip-value" }, values.shared_subnet || "Not set")),
                            h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Gateway"), h("div", { className: "strip-value" }, values.gateway_ip || "Not set")),
                            h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "DNS servers"), h("div", { className: "strip-value" }, dnsSummary))
                        )
                    ),
                    h(Panel, { label: "Shared basics", title: "Kit and network", subtitle: "These shared fields must be trusted before any setup page can run hardware actions." },
                        h("div", { className: "form-grid" },
                            h(Field, { label: "Kit name", name: "site_name", value: values.site_name, onChange: props.onValueChange, help: "Use a single printable name, 32 characters or less." }),
                            h(Field, { label: "Shared subnet", name: "shared_subnet", value: values.shared_subnet, onChange: props.onValueChange }),
                            h(Field, { label: "Gateway", name: "gateway_ip", value: values.gateway_ip, onChange: props.onValueChange })
                        )
                    ),
                    h(Panel, {
                        label: "Address plan",
                        title: "Device addresses",
                        subtitle: "These map to the same iLO, ESXi, Windows, switch, NetApp, QNAP, and ioSafe fields used by the setup workflow.",
                        action: h(Button, { onClick: props.onAutofillGlobalIps }, "Autofill default IPs")
                    },
                        h("div", { className: "form-grid" },
                            h(Field, { label: "iLO target IP", name: "ilo_target_ip", value: values.ilo_target_ip, onChange: props.onValueChange }),
                            h(Field, { label: "ESXi IP", name: "esxi_ip", value: values.esxi_ip, onChange: props.onValueChange }),
                            h(Field, { label: "Windows IP", name: "windows_ip", value: values.windows_ip, onChange: props.onValueChange }),
                            h(Field, { label: "Switch IP", name: "switch_ip", value: values.switch_ip, onChange: props.onValueChange }),
                            h(Field, { label: "NetApp IP", name: "netapp_ip", value: values.netapp_ip, onChange: props.onValueChange }),
                            h(Field, { label: "QNAP IP", name: "qnap_ip", value: values.qnap_ip, onChange: props.onValueChange }),
                            h(Field, { label: "ioSafe IP", name: "iosafe_ip", value: values.iosafe_ip, onChange: props.onValueChange })
                        )
                    ),
                    h(Panel, { label: "Shared DNS and alerts", title: "DNS and primary SNMPv3 user", subtitle: "Passwords stay blank in React when already saved; leaving them blank preserves the saved secret." },
                        h("div", { className: "form-grid" },
                            h(Field, { label: "DNS 1", name: "dns1", value: values.dns1, onChange: props.onValueChange }),
                            h(Field, { label: "DNS 2", name: "dns2", value: values.dns2, onChange: props.onValueChange }),
                            h(Field, { label: "DNS 3", name: "dns3", value: values.dns3, onChange: props.onValueChange }),
                            h(Field, { label: "DNS 4", name: "dns4", value: values.dns4, onChange: props.onValueChange }),
                            h(Field, { label: "SNMPv3 username", name: "snmp_v3_username", value: values.snmp_v3_username, onChange: props.onValueChange }),
                            h(SelectField, { label: "Auth protocol", name: "snmp_v3_auth_protocol", value: values.snmp_v3_auth_protocol, onChange: props.onValueChange, options: protocolOptions }),
                            h(Field, { label: "Auth password", name: "snmp_v3_auth_password", type: "password", value: values.snmp_v3_auth_password, onChange: props.onValueChange, help: values.snmp_v3_auth_password_saved ? "Leave blank to keep the saved auth password." : "Enter an SNMPv3 auth password." }),
                            h(SelectField, { label: "Privacy protocol", name: "snmp_v3_priv_protocol", value: values.snmp_v3_priv_protocol, onChange: props.onValueChange, options: privacyOptions }),
                            h(Field, { label: "Privacy password", name: "snmp_v3_priv_password", type: "password", value: values.snmp_v3_priv_password, onChange: props.onValueChange, help: values.snmp_v3_priv_password_saved ? "Leave blank to keep the saved privacy password." : "Enter an SNMPv3 privacy password." })
                        )
                    ),
                    h(Panel, {
                        label: "Advanced SNMPv3 users",
                        title: "Additional users",
                        subtitle: "Add the same extra SNMPv3 user rows that the setup workflow exposes.",
                        action: h(Button, { onClick: props.onAddSnmpUser }, "Add SNMP user")
                    },
                        snmpUsers.length ? h("div", { className: "snmp-user-list" }, snmpUsers.map(function (user, index) {
                            return h("div", { className: "snmp-user-card", key: "snmp-user-" + index },
                                h("div", { className: "panel-header compact-header" },
                                    h("div", null,
                                        h("div", { className: "panel-label" }, "SNMPv3 user " + String(index + 1)),
                                        h("h3", { className: "panel-title" }, user.username || "New user")
                                    ),
                                    h(Button, { onClick: function () { props.onRemoveSnmpUser(index); } }, "Remove")
                                ),
                                h("div", { className: "snmp-user-grid" },
                                    h(Field, { label: "Username", name: "snmp_extra_username_" + index, value: user.username, onChange: function (_, value) { changeExtra(index, "username", value); } }),
                                    h(SelectField, { label: "Auth protocol", name: "snmp_extra_auth_protocol_" + index, value: user.auth_protocol, onChange: function (_, value) { changeExtra(index, "auth_protocol", value); }, options: protocolOptions }),
                                    h(Field, { label: "Auth password", name: "snmp_extra_auth_password_" + index, type: "password", value: user.auth_password, onChange: function (_, value) { changeExtra(index, "auth_password", value); }, help: user.auth_password_saved ? "Leave blank to keep the saved auth password." : "Enter an auth password." }),
                                    h(SelectField, { label: "Privacy protocol", name: "snmp_extra_priv_protocol_" + index, value: user.priv_protocol, onChange: function (_, value) { changeExtra(index, "priv_protocol", value); }, options: privacyOptions }),
                                    h(Field, { label: "Privacy password", name: "snmp_extra_priv_password_" + index, type: "password", value: user.priv_password, onChange: function (_, value) { changeExtra(index, "priv_password", value); }, help: user.priv_password_saved ? "Leave blank to keep the saved privacy password." : "Enter a privacy password." })
                                )
                            );
                        })) : h("div", { className: "empty-state" }, "No additional SNMPv3 users are saved for this kit.")
                    ),
                    h(Panel, { label: "Included modules", title: "Run scope defaults", subtitle: "These toggles feed the same included flags used by Run Center and setup pre-checks." },
                        h("div", { className: "toggle-grid" },
                            h(ToggleField, { label: "iLO", name: "ilo", checked: included.ilo, onChange: props.onIncludedChange }),
                            h(ToggleField, { label: "Storage", name: "storage", checked: included.storage, onChange: props.onIncludedChange }),
                            h(ToggleField, { label: "ESXi", name: "esxi", checked: included.esxi, onChange: props.onIncludedChange }),
                            h(ToggleField, { label: "Windows", name: "windows", checked: included.windows, onChange: props.onIncludedChange }),
                            h(ToggleField, { label: "QNAP", name: "qnap", checked: included.qnap, onChange: props.onIncludedChange }),
                            h(ToggleField, { label: "NetApp", name: "netapp", checked: included.netapp, onChange: props.onIncludedChange }),
                            h(ToggleField, { label: "Cisco switch", name: "cisco_switch", checked: included.cisco_switch, onChange: props.onIncludedChange }),
                            h(ToggleField, { label: "ioSafe", name: "iosafe", checked: included.iosafe, onChange: props.onIncludedChange })
                        ),
                        h("div", { className: "job-actions" },
                            h(Button, { primary: true, type: "submit", disabled: props.savingGlobal }, props.savingGlobal ? "Saving..." : "Save shared defaults"),
                            h(Button, { onClick: function () { props.onNavigate("global_settings"); } }, "Refresh Global Settings")
                        )
                    )
                ),
                h(ActionInventoryPanel, { activePage: "global_settings", appState: props.appState, actions: globalState.actions || ((props.appState || {}).actions || {}).global_settings || [], onNavigate: props.onNavigate })
            ),
            h(ContextPanel, { activePage: "global_settings", appState: props.appState, actions: globalState.actions, onNavigate: props.onNavigate })
        );
    }

    function StoragePage(props) {
        const state = props.appState || {};
        const storage = state.storage || {};
        const target = storage.target || {};
        const credentials = storage.credentials || {};
        const discovery = storage.discovery || {};
        const plan = storage.plan || {};
        const approval = storage.approval || {};
        const apply = storage.apply || {};
        const form = props.storageForm || {};
        const actions = storage.actions || ((state.actions || {}).storage || []);
        const canRead = target.valid && credentials.valid && !props.working;
        const canPlan = discovery.available && !props.working;
        const canApprove = discovery.available && plan.available && plan.valid && !props.working;
        const expectedConfirmation = form.apply_mode === "wipe_rebuild" ? (plan.wipe_rebuild_confirmation || "WIPE STORAGE") : (plan.create_only_confirmation || "CREATE STORAGE");
        const canApply = canApprove && approval.approved && form.acknowledge_apply && String(form.typed_confirmation || "").trim() === expectedConfirmation;
        const canReboot = !!apply.directory && !props.working;
        function change(name, value) {
            props.onChange(name, value);
        }
        function readinessRows() {
            return (storage.readiness || []).map(function (item) {
                return h("div", { className: "data-row", key: item.label },
                    h("div", null,
                        h("div", { className: "data-name" }, item.label),
                        h("div", { className: "data-value" }, item.summary || "")
                    ),
                    h(Pill, { tone: item.tone }, item.status || "Review")
                );
            });
        }
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: pageCopy.storage.what,
                    next: (storage.blockers || []).length ? ((storage.blockers || [])[0].summary || "Resolve the storage blocker before planning.") : "Display current storage setup, build a plan, approve it, then apply only after reviewing the generated plan.",
                    last: storage.review && storage.review.state_label ? ("Storage state: " + storage.review.state_label) : "No storage workflow state yet.",
                }),
                h(Panel, {
                    label: "Storage target",
                    title: target.resolved || target.default_host || "No target resolved",
                    subtitle: target.valid ? ("Using " + (target.source || "saved target") + " with " + (credentials.username || "saved credentials")) : (target.error || credentials.error || "Set the target and sign-in details before reading storage."),
                    action: h(Pill, { tone: target.valid && credentials.valid ? "ready" : "warn" }, target.valid && credentials.valid ? "Ready" : "Needs setup")
                },
                    h("div", { className: "form-grid" },
                        h(Field, { label: "Storage target override", name: "storage_target_host", value: form.target_host, onChange: change, help: "Leave blank and use iLO defaults to target the active iLO endpoint." }),
                        h(Field, { label: "Username", name: "username", value: form.username, onChange: change }),
                        h(Field, { label: "Password", name: "password", type: "password", value: form.password, onChange: change, help: form.password_saved ? "Leave blank to keep the saved storage or iLO password." : "Enter the iLO/storage password." })
                    ),
                    h("div", { className: "job-actions" },
                        h(Button, { primary: true, onClick: function () { props.onSaveTarget("override"); }, disabled: props.working }, props.working ? "Working..." : "Use entered IP"),
                        h(Button, { onClick: function () { props.onSaveTarget("defaults"); }, disabled: props.working }, "Use iLO defaults"),
                        h(Button, { onClick: props.onReadCurrent, disabled: !canRead }, "Display current storage setup"),
                        h(Button, { onClick: props.onProbeCapabilities, disabled: !canRead }, "Probe capabilities")
                    )
                ),
                h(Panel, { label: "Readiness", title: "Storage checks", subtitle: "These are the same gates the storage workflow uses before plan and apply actions." },
                    (storage.readiness || []).length ? h("div", { className: "data-list" }, readinessRows()) : h("div", { className: "empty-state" }, "No storage readiness data returned.")
                ),
                discovery.restore_error ? h("div", { className: "message message-warn" }, "Saved storage artifact could not be restored: " + discovery.restore_error) : null,
                h(Panel, {
                    label: "Discovery",
                    title: discovery.available ? "Latest storage discovery" : "Display current storage setup first",
                    subtitle: discovery.raw_path || "A discovery snapshot is required before a RAID plan can be built.",
                    action: h(Pill, { tone: discovery.available ? "ready" : "warn" }, discovery.available ? "Available" : "Missing")
                },
                    h("div", { className: "section-grid" },
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Controllers"), h("div", { className: "strip-value" }, String(discovery.controllers || 0))),
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Drives"), h("div", { className: "strip-value" }, String(discovery.drives || 0))),
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Volumes"), h("div", { className: "strip-value" }, String(discovery.volumes || 0)))
                    ),
                    h("div", { className: "job-actions" },
                        h(Button, { onClick: props.onBuildPlan, disabled: !canPlan }, "Build storage plan"),
                        h(Button, { onClick: props.onRepairSelection, disabled: !canRead }, "Clear invalid selections and reload inventory")
                    )
                ),
                h(Panel, {
                    label: "Plan",
                    title: plan.available ? "Generated RAID plan" : "No plan built yet",
                    subtitle: plan.path || "Build a plan after discovery. Applying storage without a plan is blocked.",
                    action: h(Pill, { tone: plan.valid ? "ready" : "warn" }, plan.valid ? "Valid" : "Needs plan")
                },
                    (plan.arrays || []).length ? h("div", { className: "data-list" }, (plan.arrays || []).map(function (array) {
                        return h("div", { className: "data-row", key: (array.role || "") + (array.name || "") },
                            h("div", null,
                                h("div", { className: "data-name" }, (array.name || array.role || "Array") + " - " + (array.raid_level || "RAID")),
                                h("div", { className: "data-value" }, "Controller: " + (array.controller || array.controller_path || "not set") + " | Bays: " + (array.bays || "not set"))
                            ),
                            h(Pill, { tone: "blue" }, String(array.drive_count || 0) + " drives")
                        );
                    })) : h("div", { className: "empty-state" }, "No plan arrays are available yet."),
                    h("div", { className: "job-actions" },
                        h(ToggleField, { label: "Include storage in iLO run", name: "include_in_ilo_run", checked: form.include_in_ilo_run, onChange: change }),
                        h(Button, { primary: true, onClick: props.onApprovePlan, disabled: !canApprove }, "Approve this plan"),
                        h(Button, { onClick: props.onClearApproval, disabled: !plan.available || props.working }, "Remove approval")
                    )
                ),
                h(Panel, {
                    label: "Apply",
                    title: approval.approved ? "Approved storage plan" : "Approval required before apply",
                    subtitle: approval.approved ? ("Approved for " + (approval.host || target.resolved || "target")) : "Approve the plan before enabling apply controls.",
                    action: h(Pill, { tone: approval.approved ? "ready" : "warn" }, approval.approved ? "Approved" : "Blocked")
                },
                    h("div", { className: "form-grid" },
                        h(SelectField, { label: "Apply mode", name: "apply_mode", value: form.apply_mode || "create_only", onChange: change, options: [{ value: "create_only", label: "Create only" }, { value: "wipe_rebuild", label: "Wipe and rebuild" }] }),
                        h(Field, { label: "Confirmation", name: "typed_confirmation", value: form.typed_confirmation, onChange: change, help: "Type exactly: " + expectedConfirmation }),
                        h(ToggleField, { label: "I reviewed the generated plan", name: "acknowledge_apply", checked: form.acknowledge_apply, onChange: change })
                    ),
                    h("div", { className: "job-actions" },
                        h(Button, { primary: true, onClick: props.onApplyLayout, disabled: !canApply }, "Apply storage layout"),
                        h(Button, { onClick: props.onRebootNow, disabled: !canReboot }, "Reboot storage now")
                    ),
                    apply.directory ? h("div", { className: "field-help" }, "Apply run folder: " + apply.directory) : null
                ),
                h(ActionLogPanel, { title: "Storage action log", appState: state, entries: props.setupActionLog }),
                h(ActionInventoryPanel, { activePage: "storage", appState: state, actions: actions, onNavigate: props.onNavigate })
            ),
            h(ContextPanel, { activePage: "storage", appState: state, actions: actions, onNavigate: props.onNavigate })
        );
    }

    function MigrationPage(props) {
        const state = props.appState || {};
        const copy = pageCopy[props.page] || pageCopy.esxi;
        const module = (state.modules || []).find(function (item) { return item.key === props.page; }) || {};
        const actions = ((state.actions || {})[props.page] || []);
        const setupValues = ((state.setup_values || {})[props.page] || {});
        const last = module.last_summary || "No recent activity has been recorded for this workspace yet.";
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: copy.what,
                    next: module.blockers && module.blockers.length ? module.blockers[0].fix || "Finish the required setup values before running hardware actions." : "Review saved values, then use the mapped workflow controls or route coverage below for the next action.",
                    last: last,
                }),
                props.page === "netapp" ? h(NetAppStatusPanel, { netappStatus: props.netappStatus, onRefresh: props.onRefreshNetApp }) : null,
                props.page === "cisco" ? h(CiscoSetupIpPanel, { form: (props.setupIpForm || {}).cisco || {}, onChange: props.onSetupIpChange, onSetupIp: props.onSetupCiscoIp, working: props.setupIpWorking }) : null,
                props.page === "netapp" ? h(NetAppSetupIpPanel, { form: (props.setupIpForm || {}).netapp || {}, onChange: props.onSetupIpChange, onSetupIp: props.onSetupNetAppIp, working: props.setupIpWorking }) : null,
                setupValues.summary ? h(ModuleDetailPanel, { page: props.page, detail: setupValues, appState: state, onNavigate: props.onNavigate }) : null,
                h(Panel, {
                    label: "Workflow status",
                    title: copy.title,
                    subtitle: "Backend state, setup readiness, and preserved routes are shown here so this page can be completed without leaving the React workspace.",
                    action: h(Pill, { tone: module.tone || "blue" }, module.state_label || "Mapped")
                },
                    h("div", { className: "section-grid" },
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Target"), h("div", { className: "strip-value" }, module.target || "Not set")),
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Checks"), h("div", { className: "strip-value" }, (module.checks_ready || 0) + " / " + (module.total_checks || 0))),
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Included"), h("div", { className: "strip-value" }, module.included ? "Yes" : "No"))
                    ),
                    module.blockers && module.blockers.length ? h("div", { className: "message message-warn" }, module.blockers[0].label + ": " + (module.blockers[0].fix || module.blockers[0].details || "Review required.")) : h("div", { className: "message message-good" }, "No blockers reported by the summary API.")
                ),
                (props.page === "cisco" || props.page === "netapp") ? h(ActionLogPanel, { title: "Setup action log", appState: state, entries: props.setupActionLog }) : null,
                h(ActionInventoryPanel, { activePage: props.page, appState: state, actions: actions, onNavigate: props.onNavigate })
            ),
            h(ContextPanel, { activePage: props.page, appState: state, actions: actions, onNavigate: props.onNavigate })
        );
    }

    function ModuleDetailPanel(props) {
        const detail = props.detail || {};
        const primary = detail.primary_action || {};
        function rows(items) {
            return (items || []).map(function (item) {
                return h("div", { className: "data-row", key: item.label + item.value },
                    h("div", null,
                        h("div", { className: "data-name" }, item.label),
                        h("div", { className: "data-value", title: item.value }, item.value || "Not set")
                    )
                );
            });
        }
        return h(Panel, {
            label: "Saved setup values",
            title: (pageCopy[props.page] || {}).title || "Setup values",
            subtitle: "Current kit values are shown here so the React page exposes the same operator context as the original form.",
            action: primary.href ? h(Button, { href: primary.href }, primary.label || "Open full form") : null
        },
            h("div", { className: "section-grid" }, (detail.summary || []).map(function (item) {
                return h("div", { className: "setup-card", key: item.label },
                    h("div", { className: "metric-label" }, item.label),
                    h("div", { className: "strip-value", title: item.value }, item.value || "Not set")
                );
            })),
            (detail.details || []).length ? h("div", { className: "data-list module-detail-list" }, rows(detail.details)) : null
        );
    }

    function NetAppStatusPanel(props) {
        const status = props.netappStatus || {};
        return h(Panel, {
            label: "Real backend status",
            title: "NetApp module status",
            subtitle: "Loaded from /modules/netapp/status.",
            action: h(Button, { onClick: props.onRefresh }, "Refresh NetApp")
        },
            status.error ? h("div", { className: "message message-warn" }, status.error) :
                h("div", { className: "section-grid" },
                    h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Status"), h("div", { className: "strip-value" }, status.status || "Unknown")),
                    h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Action"), h("div", { className: "strip-value" }, status.action || "status")),
                    h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Safe apply"), h("div", { className: "strip-value" }, ((status.health || {}).apply) || "Unknown"))
                )
        );
    }

    function CiscoSetupIpPanel(props) {
        const form = props.form || {};
        function change(name, value) {
            props.onChange("cisco", name, value);
        }
        return h(Panel, {
            label: "Setup IP",
            title: "Cisco management access",
            subtitle: "Applies the saved management VLAN, switch IP, gateway, and SSH access config through the console route.",
            action: h(Button, { primary: true, onClick: props.onSetupIp, disabled: props.working }, props.working ? "Applying..." : "Setup IP")
        },
            h("div", { className: "form-grid" },
                h(Field, { label: "Switch IP", name: "management_ip", value: form.management_ip, onChange: change }),
                h(Field, { label: "Gateway", name: "gateway", value: form.gateway, onChange: change }),
                h(Field, { label: "Subnet mask", name: "subnet_mask", value: form.subnet_mask, onChange: change }),
                h(Field, { label: "Management VLAN", name: "management_vlan", value: form.management_vlan, onChange: change }),
                h(Field, { label: "Console port", name: "console_port", value: form.console_port, onChange: change }),
                h(Field, { label: "Console baud", name: "console_baud", value: form.console_baud, onChange: change }),
                h(Field, { label: "Hostname", name: "hostname", value: form.hostname, onChange: change }),
                h(Field, { label: "Domain", name: "domain_name", value: form.domain_name, onChange: change }),
                h(Field, { label: "Username", name: "username", value: form.username, onChange: change }),
                h(Field, { label: "Password", name: "password", type: "password", value: form.password, onChange: change, help: form.password_saved ? "Leave blank to keep the saved password." : "Enter the switch password." }),
                h(Field, { label: "Enable password", name: "enable_password", type: "password", value: form.enable_password, onChange: change, help: form.enable_password_saved ? "Leave blank to keep the saved enable password." : "Enter the enable password." }),
                h(Field, { label: "Bootstrap port", name: "bootstrap_network_port", value: form.bootstrap_network_port, onChange: change })
            )
        );
    }

    function NetAppSetupIpPanel(props) {
        const form = props.form || {};
        function change(name, value) {
            props.onChange("netapp", name, value);
        }
        return h(Panel, {
            label: "Setup IP",
            title: "NetApp management addresses",
            subtitle: "Saves the NetApp bootstrap values. The existing backend route reports that live NetApp IP apply is not implemented yet.",
            action: h(Button, { primary: true, onClick: props.onSetupIp, disabled: props.working }, props.working ? "Saving..." : "Save setup IP values")
        },
            h("div", { className: "form-grid" },
                h(Field, { label: "Cluster management IP", name: "cluster_mgmt_ip", value: form.cluster_mgmt_ip, onChange: change }),
                h(Field, { label: "Gateway", name: "gateway", value: form.gateway, onChange: change }),
                h(Field, { label: "Netmask", name: "netmask", value: form.netmask, onChange: change }),
                h(Field, { label: "ONTAP username", name: "username", value: form.username, onChange: change }),
                h(Field, { label: "ONTAP password", name: "password", type: "password", value: form.password, onChange: change, help: form.password_saved ? "Leave blank to keep the saved password." : "Enter the ONTAP password." }),
                h(Field, { label: "Console port", name: "console_port", value: form.console_port, onChange: change }),
                h(Field, { label: "Console baud", name: "console_baud", value: form.console_baud, onChange: change }),
                h(Field, { label: "Controller A SP IP", name: "sp_a_ip", value: form.sp_a_ip, onChange: change }),
                h(Field, { label: "Controller B SP IP", name: "sp_b_ip", value: form.sp_b_ip, onChange: change }),
                h(Field, { label: "Controller A management IP", name: "node_01_mgmt_ip", value: form.node_01_mgmt_ip, onChange: change }),
                h(Field, { label: "Controller B management IP", name: "node_02_mgmt_ip", value: form.node_02_mgmt_ip, onChange: change }),
                h(Field, { label: "SVM management IP", name: "svm_mgmt_ip", value: form.svm_mgmt_ip, onChange: change })
            )
        );
    }

    function ActionLogPanel(props) {
        const job = ((props.appState || {}).job || {});
        const localEntries = props.entries || [];
        const jobLogs = (job.logs || []).slice(-8);
        return h(Panel, {
            label: "Live feedback",
            title: props.title || "Action log",
            subtitle: job.current_stage ? ((job.status || "Status") + " - " + job.current_stage) : "Recent setup actions and backend job output appear here."
        },
            localEntries.length ? h("div", { className: "data-list action-local-log" }, localEntries.slice(-6).map(function (entry, index) {
                return h("div", { className: "data-row", key: "local-log-" + index },
                    h("div", null,
                        h("div", { className: "data-name" }, entry.label || "Action"),
                        h("div", { className: "data-value" }, entry.text || "")
                    ),
                    h(Pill, { tone: entry.tone || (entry.ok === false ? "warn" : "blue") }, entry.status || "log")
                );
            })) : h("div", { className: "empty-state" }, "No setup action has been started from this page yet."),
            jobLogs.length ? h("pre", { className: "code-mini setup-action-log" }, jobLogs.join("\n")) : null
        );
    }

    function ConfigurationPage(props) {
        const state = props.appState || {};
        const kit = state.kit || {};
        const actions = ((state.actions || {}).configuration || []);
        const available = kit.available || [];
        const otherKits = available.filter(function (name) { return name !== kit.name; });
        const included = kit.included || {};
        const includedItems = Object.keys(included).filter(function (key) { return included[key]; });
        function configRow(label, value) {
            return h("div", { className: "data-row" },
                h("div", null,
                    h("div", { className: "data-name" }, label),
                    h("div", { className: "data-value" }, value || "Not set")
                )
            );
        }
        const selectedKit = props.selectedKit || otherKits[0] || "";
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: pageCopy.configuration.what,
                    next: "Create a kit, load an existing kit, import a kit file, or open the current config before continuing setup.",
                    last: available.length ? String(available.length) + " kit(s) are available." : "No saved kit list is available.",
                }),
                h(Panel, { label: "Kit", title: kit.name || "Current kit", subtitle: "Read from /api/ui/app-state." },
                    h("div", { className: "section-grid" },
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Kit"), h("div", { className: "strip-value" }, kit.name || "Not set")),
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Available kits"), h("div", { className: "strip-value" }, String(available.length))),
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Included modules"), h("div", { className: "strip-value" }, String(includedItems.length)))
                    ),
                    h("div", { className: "data-list config-data-list" },
                        configRow("Subnet", (kit.ip_plan || {}).subnet),
                        configRow("Gateway", (kit.ip_plan || {}).gateway),
                        configRow("Included", includedItems.join(", ") || "None")
                    )
                ),
                h(Panel, { label: "Kit management", title: "Choose a kit", subtitle: "These controls now use React JSON APIs while preserving the existing kit files." },
                    h("div", { className: "kit-management-grid" },
                        h("div", { className: "setup-card kit-tool-card" },
                            h("div", { className: "data-name" }, "Use an existing kit"),
                            h("select", {
                                className: "select-input",
                                value: selectedKit,
                                disabled: !otherKits.length || props.kitWorking,
                                onChange: function (event) { props.onSelectedKitChange(event.target.value); },
                            }, otherKits.length ? otherKits.map(function (name) {
                                return h("option", { key: name, value: name }, name);
                            }) : h("option", { value: "" }, "No other saved kits found")),
                            h("div", { className: "job-actions" },
                                h(Button, { onClick: function () { props.onLoadKit(selectedKit); }, disabled: !selectedKit || props.kitWorking }, props.kitWorking ? "Working..." : "Load existing kit")
                            )
                        ),
                        h("div", { className: "setup-card kit-tool-card" },
                            h("div", { className: "data-name" }, "Create a new kit"),
                            h("input", {
                                className: "text-input",
                                value: props.newKitName || "",
                                onChange: function (event) { props.onNewKitNameChange(event.target.value); },
                                placeholder: "New kit name",
                            }),
                            h("div", { className: "job-actions" },
                                h(Button, { primary: true, onClick: props.onCreateKit, disabled: !String(props.newKitName || "").trim() || props.kitWorking }, props.kitWorking ? "Working..." : "Create new kit")
                            )
                        ),
                        h("div", { className: "setup-card kit-tool-card" },
                            h("div", { className: "data-name" }, "Current kit config"),
                            h("div", { className: "data-value" }, "Open or download the YAML snapshot for the active kit."),
                            h("div", { className: "job-actions" },
                                h(Button, { onClick: props.onViewCurrentConfig, disabled: props.kitWorking }, "Open current config"),
                                h(DownloadButton, { href: "/api/ui/current-kit-config/download" }, "Download current config")
                            )
                        ),
                        h("div", { className: "setup-card kit-tool-card" },
                            h("div", { className: "data-name" }, "Import kit config"),
                            h("input", {
                                className: "file-input",
                                type: "file",
                                accept: ".yml,.yaml,.json,application/x-yaml,application/json",
                                onChange: function (event) { props.onImportFileChange(event.target.files && event.target.files[0] ? event.target.files[0] : null); },
                            }),
                            h("div", { className: "field-help" }, props.importFile ? props.importFile.name : "Choose a YAML or JSON kit config."),
                            h("div", { className: "job-actions" },
                                h(Button, { onClick: props.onImportKit, disabled: !props.importFile || props.kitWorking }, props.kitWorking ? "Working..." : "Import kit config")
                            )
                        )
                    )
                ),
                props.kitConfigView ? h(Panel, {
                    label: "Current config",
                    title: props.kitConfigView.filename || "Current kit config",
                    subtitle: props.kitConfigView.path || "",
                }, h("pre", { className: "code-mini config-preview" }, props.kitConfigView.content || "")) : null,
                h(Panel, { label: "Kit library", title: "Saved kits", subtitle: "Load buttons switch the active kit without leaving the React shell." },
                    available.length ? h("div", { className: "kit-list" }, available.map(function (name) {
                        return h("div", { className: "kit-row", key: name },
                            h("div", null,
                                h("div", { className: "data-name" }, name),
                                h("div", { className: "data-value" }, name === kit.name ? "Active" : "Available")
                            ),
                            name === kit.name ? h(Pill, { tone: "ready" }, "Active") : h(Button, { onClick: function () { props.onLoadKit(name); }, disabled: props.kitWorking }, "Load")
                        );
                    })) : h("div", { className: "empty-state" }, "No kits found.")
                ),
                h(ActionInventoryPanel, { activePage: "configuration", appState: state, actions: actions, onNavigate: props.onNavigate })
            ),
            h(ContextPanel, { activePage: "configuration", appState: state, actions: actions, onNavigate: props.onNavigate })
        );
    }

    function ReportsPage(props) {
        const state = props.appState || {};
        const actions = ((state.actions || {}).reports || []);
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: pageCopy.reports.what,
                    next: "Open a run summary or debug artifact from the Reports page when deeper inspection is needed.",
                    last: (state.recent_activity || [])[0] ? (state.recent_activity || [])[0].title : "No recent activity.",
                }),
                h(Panel, { label: "History", title: "Run and activity records", subtitle: "Read from /api/ui/run-history through the app-state payload." },
                    h("div", { className: "activity-list" },
                        ((state.run_history || []).length ? state.run_history : state.recent_activity || []).slice(0, 14).map(function (item, index) {
                            return h("div", { className: "activity-row", key: index },
                                h("div", null,
                                    h("div", { className: "activity-title" }, item.display_title || item.title || item.scope || "Activity"),
                                    h("div", { className: "activity-detail" }, item.display_summary || item.summary || item.status || "")
                                ),
                                h("div", { className: "timeline-time" }, item.time || "")
                            );
                        })
                    )
                ),
                h(ReportCenterPanel, { reportCenter: state.report_center || {}, onSearchReports: props.onSearchReports }),
                h(ActionInventoryPanel, { activePage: "reports", appState: state, actions: actions, onNavigate: props.onNavigate })
            ),
            h(ContextPanel, { activePage: "reports", appState: state, actions: actions, onNavigate: props.onNavigate })
        );
    }

    function ExecutionPage(props) {
        const state = props.appState || {};
        const actions = ((state.actions || {}).execution || []);
        const included = ((state.kit || {}).included || {});
        const review = state.execution_review || {};
        const stages = review.stages || [];
        const scopes = [
            ["included", "Whole run", true],
            ["ilo", "iLO", included.ilo],
            ["storage", "Storage", included.storage],
            ["esxi", "ESXi", included.esxi],
            ["windows", "Windows", included.windows],
            ["qnap", "QNAP", included.qnap],
            ["iosafe", "ioSafe", included.iosafe],
            ["cisco_switch", "Cisco Switch", included.cisco_switch],
            ["netapp", "NetApp", included.netapp],
        ];
        function scopeInputs() {
            return scopes.map(function (scope) {
                return h("label", { className: "toggle-field run-scope-toggle", key: scope[0] },
                    h("input", { type: "checkbox", name: "selected_scopes", value: scope[0], defaultChecked: scope[0] === "included", disabled: scope[0] !== "included" && !scope[2] }),
                    h("span", null,
                        h("strong", null, scope[1]),
                        h("small", null, scope[0] === "included" ? "Use currently included kit stages" : (scope[2] ? "Included" : "Not included"))
                    )
                );
            });
        }
        function runForm(action, label, primary) {
            return h("form", { className: "run-center-form", action: action, method: "post" },
                h("input", { type: "hidden", name: "return_page", value: "execution" }),
                h("div", { className: "run-scope-grid" }, scopeInputs()),
                h("div", { className: "job-actions" },
                    h("button", { className: "button" + (primary ? " button-primary" : ""), type: "submit" }, label)
                )
            );
        }
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: pageCopy.execution.what,
                    next: "Choose a scope, review the run, then start a preview. Real execution stays behind the full confirmation form.",
                    last: ((state.job || {}).last_message || (state.dashboard || {}).latest_result && (state.dashboard || {}).latest_result.label) || "No run has started yet.",
                }),
                h(LiveJobPanel, { job: state.job },
                    h("div", { className: "job-actions" },
                        h(Button, { onClick: props.onRefresh }, "Refresh"),
                        h(Button, { href: "/execution" }, "Open full confirmation")
                    )
                ),
                h(Panel, { label: "Run controls", title: "Choose run scope", subtitle: "These forms post to the original Run Center routes with the selected scope values." },
                    h("div", { className: "execution-form-grid" },
                        h("div", { className: "setup-card run-tool-card" },
                            h("div", { className: "data-name" }, "Review run"),
                            h("div", { className: "data-value" }, "Validate readiness and build the confirmation state."),
                            runForm("/prepare-execute", "Review run", true)
                        ),
                        h("div", { className: "setup-card run-tool-card" },
                            h("div", { className: "data-name" }, "Preview only"),
                            h("div", { className: "data-value" }, "Start a safety preview without making real hardware changes."),
                            runForm("/execute-preview", "Start preview run", false)
                        )
                    )
                ),
                h(Panel, {
                    label: "Stage readiness",
                    title: "Fix blocked stages before launch",
                    subtitle: ((review.confidence || {}).summary) || "The Run Center review uses the same stage checks and fix links as the original page.",
                    action: h(Pill, { tone: (review.confidence || {}).tone || "progress" }, (review.confidence || {}).label || "Review"),
                },
                    h("div", { className: "data-list" }, stages.length ? stages.map(function (stage) {
                        const blocked = !!stage.blocked_reason;
                        return h("div", { className: "data-row", key: stage.key },
                            h("div", null,
                                h("div", { className: "data-name" }, stage.name),
                                h("div", { className: "data-value" }, blocked ? stage.blocked_reason : (stage.summary || stage.state_used || "Ready for review")),
                                stage.corrective_action ? h("div", { className: "field-help" }, stage.corrective_action) : null
                            ),
                            h("div", { className: "job-actions" },
                                h(Pill, { tone: stage.status_tone }, stage.status_label || "Review"),
                                stage.review_href ? h(ReactAwareButton, { href: stage.review_href, appState: state, onNavigate: props.onNavigate }, "Open setup page") : null,
                                blocked && stage.fix_href ? h(ReactAwareButton, { href: stage.fix_href, appState: state, onNavigate: props.onNavigate, primary: true }, stage.fix_label || "Fix stage") : null
                            )
                        );
                    }) : h("div", { className: "empty-state" }, "No selected run stages yet. Review the current kit before launching."))
                ),
                h(Panel, { label: "Real execution", title: "Confirmation required", subtitle: "The real run keeps the original checkbox and EXECUTE phrase gates." },
                    h("div", { className: "job-actions" },
                        h(Button, { href: "/execution" }, "Open full confirmation"),
                        h(Button, { href: "/debug-bundles/latest" }, "Download latest debug bundle")
                    )
                ),
                h(Panel, { label: "Run summary", title: "Latest execution summary", subtitle: "Open or download the same run summary actions exposed by the original Run Center." },
                    h("div", { className: "job-actions" },
                        h("form", { className: "inline-action-form", action: "/view-run-summary", method: "post" },
                            h("input", { type: "hidden", name: "scope", value: "included" }),
                            h("input", { type: "hidden", name: "return_page", value: "execution" }),
                            h("button", { className: "button", type: "submit" }, "Open summary")
                        ),
                        h("form", { className: "inline-action-form", action: "/download-run-summary", method: "post" },
                            h("input", { type: "hidden", name: "scope", value: "included" }),
                            h("button", { className: "button", type: "submit" }, "Download summary")
                        )
                    )
                ),
                h(ActionInventoryPanel, { activePage: "execution", appState: state, actions: actions, onNavigate: props.onNavigate })
            ),
            h(ContextPanel, { activePage: "execution", appState: state, actions: actions, onNavigate: props.onNavigate })
        );
    }

    function ReportCenterPanel(props) {
        const center = props.reportCenter || {};
        const bundles = center.latest_bundles || [];
        const entries = center.entries_preview || [];
        function reportPostForm(action, label, path) {
            return h("form", { className: "inline-action-form", action: action, method: "post" },
                h("input", { type: "hidden", name: "return_page", value: "configs" }),
                h("input", { type: "hidden", name: "report_path", value: path || "" }),
                h("button", { className: "button", type: "submit", disabled: !path }, label)
            );
        }
        function relatedReportsQuery(bundle) {
            const query = String((bundle || {}).related_reports_query || (bundle || {}).scope || "").trim();
            return query;
        }
        function submitSearch(event) {
            event.preventDefault();
            if (!props.onSearchReports) return;
            const data = new FormData(event.currentTarget);
            props.onSearchReports(String(data.get("report_query") || ""), String(data.get("report_type") || "all"));
        }
        return h(Panel, {
            label: "Report center",
            title: "Run bundles and saved files",
            subtitle: String(center.entries_total || 0) + " matching file(s). Latest bundles and newest files are shown first.",
        },
            h("form", { className: "report-search-form", onSubmit: submitSearch },
                h("input", { className: "input", type: "text", name: "report_query", defaultValue: center.query || "", placeholder: "serial, storage, summary, plan" }),
                h("select", { className: "input", name: "report_type", defaultValue: center.report_type || "all" },
                    h("option", { value: "all" }, "All reports"),
                    h("option", { value: "summary" }, "Summaries"),
                    h("option", { value: "log" }, "Logs"),
                    h("option", { value: "config" }, "Config snapshots"),
                    h("option", { value: "other" }, "Other files")
                ),
                h("button", { className: "button", type: "submit" }, "Search reports")
            ),
            bundles.length ? h("div", { className: "data-list report-bundle-list" }, bundles.slice(0, 6).map(function (bundle, index) {
                return h("div", { className: "data-row report-row", key: "bundle-" + index },
                    h("div", null,
                        h("div", { className: "data-name" }, bundle.name || bundle.scope || "Run bundle"),
                        h("div", { className: "data-value", title: bundle.human_summary || bundle.summary || "" }, bundle.human_summary || bundle.summary || "Open bundle for details."),
                        h("div", { className: "field-help" }, [bundle.time, bundle.target].filter(Boolean).join(" | "))
                    ),
                    h("div", { className: "action-row-controls" },
                        h(Pill, { tone: bundle.tone || "blue" }, bundle.result || "Recorded"),
                        reportPostForm("/view-report", "Open bundle", bundle.run_summary_path),
                        h(Button, { onClick: function () { if (props.onSearchReports) props.onSearchReports(relatedReportsQuery(bundle), "all"); } }, "Related reports")
                    )
                );
            })) : h("div", { className: "empty-state" }, "No run bundles have been recorded for this kit yet."),
            entries.length ? h("div", { className: "data-list report-file-list" }, entries.slice(0, 8).map(function (entry, index) {
                return h("div", { className: "data-row report-row", key: "entry-" + index },
                    h("div", null,
                        h("div", { className: "data-name" }, entry.label || "Report"),
                        h("div", { className: "data-value", title: entry.parent || entry.path || "" }, entry.parent || entry.kind || ""),
                        h("div", { className: "field-help" }, [entry.kind, entry.mtime].filter(Boolean).join(" | "))
                    ),
                    h("div", { className: "action-row-controls" },
                        reportPostForm("/view-report", "View", entry.path),
                        reportPostForm("/download-report", "Download", entry.path)
                    )
                );
            })) : h("div", { className: "empty-state" }, "No saved report files matched the default filter.")
        );
    }

    function TechnicalPage(props) {
        const tech = props.technical || {};
        const job = tech.job || (props.appState || {}).job || {};
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: pageCopy.technical.what,
                    next: "Use this page after an operator-facing warning points to a specific log, trace, artifact, or raw event.",
                    last: job.last_message || "No technical log lines are available for the active job.",
                }),
                h(Panel, { label: "Logs", title: "Live job log", subtitle: "Polled from /api/ui/technical-events." },
                    (tech.logs || []).length ? h("pre", { className: "code-mini" }, (tech.logs || []).join("\n")) : h("div", { className: "empty-state" }, "No job logs yet.")
                ),
                h(Panel, { label: "Artifacts", title: "Paths and generated outputs", subtitle: "Links stay as explicit paths until a download/view action is migrated." },
                    h("div", { className: "data-list" },
                        ((tech.artifacts || []).length ? tech.artifacts : []).map(function (item) {
                            return h("div", { className: "data-row", key: item.label },
                                h("div", null, h("div", { className: "data-name" }, item.label), h("div", { className: "data-value" }, item.value))
                            );
                        })
                    )
                )
            ),
            h(ContextPanel, { activePage: "technical", appState: props.appState, onNavigate: props.onNavigate })
        );
    }

    function ActionInventoryPanel(props) {
        const actions = props.actions || [];
        const pageHref = ((pageCopy[props.activePage] || {}).legacy) || "";
        const returnPage = props.activePage === "reports" ? "configs" : props.activePage;
        function modeDisplay(mode) {
            if (mode === "legacy-html") return "html";
            return mode || "route";
        }
        function isGuarded(action) {
            const text = String((action.label || "") + " " + (action.route || "")).toLowerCase();
            return text.includes("factory reset") || text.includes("start real") || text.includes("run for real") || text.includes("run-upgrade") || text.includes("reboot") || text.includes("apply config") || text.includes("apply cluster") || text.includes("apply netapp page") || text.includes("apply storage") || text.includes("safe apply");
        }
        function needsOriginalFormContext(action) {
            const route = String((action || {}).route || "");
            const label = String((action || {}).label || "").toLowerCase();
            if (route.indexOf("/api/ui/") === 0) return false;
            if (route === "/prepare-execute" || route === "/execute-preview") return false;
            if (route === "/view-current-kit-config" || route === "/download-current-kit-config") return false;
            if (route === "/view-latest-live-summary" || route === "/download-latest-live-summary" || route === "/download-latest-live-raw") return false;
            if (label.indexOf("view ") === 0 || label.indexOf("download ") === 0) return true;
            return action.method === "POST";
        }
        function actionControl(action) {
            if (isGuarded(action) || needsOriginalFormContext(action)) {
                return h(Button, { href: pageHref || action.route }, isGuarded(action) ? "Open confirmation" : "Open full form");
            }
            if (action.method === "GET") {
                if (action.mode === "download") return h(DownloadButton, { href: action.route }, "Download");
                return h(ReactAwareButton, { href: action.route, appState: props.appState, onNavigate: props.onNavigate }, "Open");
            }
            if (action.mode === "legacy-html" || action.mode === "download") {
                return h("form", { className: "inline-action-form", action: action.route, method: "post" },
                    h("input", { type: "hidden", name: "return_page", value: returnPage }),
                    h("button", { className: "button", type: "submit" }, action.label)
                );
            }
            return h(Pill, { tone: "ready" }, "API");
        }
        return h(Panel, { label: "Backend action inventory", title: "Mapped routes", subtitle: "Every preserved workflow is visible here. Context-heavy actions open the full form; simple no-context actions submit to their original route." },
            actions.length ? h("div", { className: "action-list" }, actions.map(function (action) {
                return h("div", { className: "action-row", key: action.method + action.route + action.label },
                    h("div", null,
                        h("div", { className: "data-name" }, action.label),
                        h("div", { className: "data-value" }, action.method + " " + action.route)
                    ),
                    h("div", { className: "action-row-controls" },
                        h(Pill, { tone: action.mode === "json" ? "ready" : "blue" }, modeDisplay(action.mode)),
                        actionControl(action)
                    )
                );
            })) : h("div", { className: "empty-state" }, "No actions mapped yet.")
        );
    }

    function ActionCatalogPage(props) {
        const state = props.appState || {};
        const catalog = state.action_catalog || {};
        const coverage = catalog.coverage || {};
        const queryHook = React.useState("");
        const query = queryHook[0];
        const setQuery = queryHook[1];
        const selectedHook = React.useState("All");
        const selectedCategory = selectedHook[0];
        const setSelectedCategory = selectedHook[1];
        const routes = catalog.routes || [];
        const categories = ["All"].concat(catalog.categories || []);
        const needle = String(query || "").trim().toLowerCase();
        const filtered = routes.filter(function (route) {
            const categoryMatches = selectedCategory === "All" || route.category === selectedCategory;
            if (!categoryMatches) return false;
            if (!needle) return true;
            return (route.path + " " + route.method + " " + route.name + " " + route.mode + " " + route.migration_status).toLowerCase().indexOf(needle) >= 0;
        });
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: pageCopy["action-map"].what,
                    next: "Use this view to confirm each React page has a safe control for high-use actions and retained compatibility coverage for older routes.",
                    last: String(coverage.mapped_actions || 0) + " mapped action routes out of " + String(coverage.total_routes || 0) + " registered backend routes.",
                }),
                h(Panel, { label: "Coverage", title: "Backend route surface", subtitle: "Generated from FastAPI's registered routes at request time." },
                    h("div", { className: "coverage-strip" },
                        [["Total routes", coverage.total_routes || 0], ["React APIs", coverage.react_api_routes || 0], ["HTML actions", coverage.legacy_routes || 0], ["Mapped actions", coverage.mapped_actions || 0], ["Downloads", coverage.download_routes || 0], ["Streams", coverage.websocket_routes || 0]].map(function (item) {
                            return h("div", { className: "coverage-item", key: item[0] },
                                h("div", { className: "metric-label" }, item[0]),
                                h("div", { className: "coverage-value" }, String(item[1]))
                            );
                        })
                    )
                ),
                h(Panel, {
                    label: "Routes",
                    title: "Action and route catalog",
                    subtitle: "This is the current backend route map used to verify React coverage and retained compatibility actions.",
                    action: h(Button, { onClick: function () { props.onNavigate("configuration"); } }, "Open configuration")
                },
                    h("div", { className: "catalog-tools" },
                        h("input", {
                            className: "text-input",
                            value: query,
                            onChange: function (event) { setQuery(event.target.value); },
                            placeholder: "Filter routes",
                        }),
                        h("select", {
                            className: "select-input",
                            value: selectedCategory,
                            onChange: function (event) { setSelectedCategory(event.target.value); },
                        }, categories.map(function (category) {
                            return h("option", { key: category, value: category }, category);
                        }))
                    ),
                    h("div", { className: "route-table" },
                        h("div", { className: "route-row route-row-head" },
                            h("div", null, "Route"),
                            h("div", null, "Category"),
                            h("div", null, "Mode"),
                            h("div", null, "Status")
                        ),
                        filtered.slice(0, 120).map(function (route) {
                            return h("div", { className: "route-row", key: route.method + route.path },
                                h("div", null,
                                    h("div", { className: "route-path" }, route.method + " " + route.path),
                                    h("div", { className: "route-name" }, route.name || "unnamed")
                                ),
                                h("div", null, route.category),
                                h("div", null, h(Pill, { tone: route.mode === "json" ? "ready" : route.mode === "legacy-html" ? "blue" : "warn" }, route.mode === "legacy-html" ? "html" : route.mode)),
                                h("div", null, route.mapped ? h(Pill, { tone: "ready" }, "Mapped") : h(Pill, { tone: "warn" }, route.migration_status))
                            );
                        })
                    ),
                    filtered.length > 120 ? h("div", { className: "field-help catalog-note" }, "Showing first 120 matching routes.") : null,
                    !filtered.length ? h("div", { className: "empty-state" }, "No routes match the current filter.") : null
                )
            ),
            h(ContextPanel, { activePage: "action-map", appState: state, actions: ((state.actions || {})["action-map"] || []), onNavigate: props.onNavigate })
        );
    }

    function ContextPanel(props) {
        const state = props.appState || {};
        const copy = pageCopy[props.activePage] || pageCopy.dashboard;
        const next = (state.dashboard || {}).next_step || {};
        const actions = props.actions || ((state.actions || {})[props.activePage] || []);
        return h("aside", { className: "context-panel" },
            h(Panel, { label: "Next recommended step", title: next.title || "Review this workspace", subtitle: next.summary || "Use the visible checks before running hardware actions." },
                h("div", { className: "job-actions" },
                    next.href ? h(ReactAwareButton, { href: next.href, appState: state, onNavigate: props.onNavigate }, "Open next") : null,
                    h(ReactAwareButton, { href: copy.legacy, appState: state, onNavigate: props.onNavigate }, "Open page")
                )
            ),
            h(KitSummaryPanel, { kit: state.kit || {} }),
            h(Panel, { label: "Page context", title: copy.title, subtitle: copy.what },
                h("div", { className: "data-list" },
                    h("div", { className: "data-row" }, h("div", null, h("div", { className: "data-name" }, "Full page"), h("div", { className: "data-value" }, copy.legacy))),
                    h("div", { className: "data-row" }, h("div", null, h("div", { className: "data-name" }, "Mapped actions"), h("div", { className: "data-value" }, String(actions.length))))
                )
            )
        );
    }

    function TechnicalDrawer(props) {
        const job = ((props.appState || {}).job || {});
        if (!props.open) return null;
        return h("section", { className: "technical-drawer" },
            h("div", { className: "panel-header" },
                h("div", null,
                    h("div", { className: "panel-label" }, "Technical details"),
                    h("h2", { className: "panel-title" }, "Current job diagnostics"),
                    h("p", { className: "panel-subtitle" }, "This drawer keeps raw details out of setup flow.")
                ),
                h(Button, { onClick: props.onClose }, "Close")
            ),
            h("div", { className: "technical-drawer-body" },
                h("div", null,
                    h("div", { className: "panel-label" }, "Recent log"),
                    (job.logs || []).length ? h("pre", { className: "code-mini" }, (job.logs || []).slice(-30).join("\n")) : h("div", { className: "empty-state" }, "No live log lines yet.")
                ),
                h("div", null,
                    h("div", { className: "panel-label" }, "Artifacts"),
                    (job.artifacts || []).length ? h("div", { className: "data-list" }, job.artifacts.map(function (item) {
                        return h("div", { className: "data-row", key: item.label }, h("div", null, h("div", { className: "data-name" }, item.label), h("div", { className: "data-value" }, item.value)));
                    })) : h("div", { className: "empty-state" }, "No artifacts recorded yet.")
                )
            )
        );
    }

    function App() {
        const activeState = React.useState(pageFromHash());
        const activePage = activeState[0];
        const setActivePage = activeState[1];
        const appStateHook = React.useState(null);
        const appState = appStateHook[0];
        const setAppState = appStateHook[1];
        const iloHook = React.useState(null);
        const iloState = iloHook[0];
        const setIloState = iloHook[1];
        const iloFormHook = React.useState({ current_ip: "", target_ip: "", gateway: "", hostname: "", username: "", password: "" });
        const iloForm = iloFormHook[0];
        const setIloForm = iloFormHook[1];
        const globalHook = React.useState(null);
        const globalState = globalHook[0];
        const setGlobalState = globalHook[1];
        const globalFormHook = React.useState(null);
        const globalForm = globalFormHook[0];
        const setGlobalForm = globalFormHook[1];
        const messageHook = React.useState(null);
        const message = messageHook[0];
        const setMessage = messageHook[1];
        const techHook = React.useState(null);
        const technical = techHook[0];
        const setTechnical = techHook[1];
        const netappHook = React.useState(null);
        const netappStatus = netappHook[0];
        const setNetappStatus = netappHook[1];
        const savingHook = React.useState(false);
        const savingIlo = savingHook[0];
        const setSavingIlo = savingHook[1];
        const savingGlobalHook = React.useState(false);
        const savingGlobal = savingGlobalHook[0];
        const setSavingGlobal = savingGlobalHook[1];
        const drawerHook = React.useState(false);
        const technicalOpen = drawerHook[0];
        const setTechnicalOpen = drawerHook[1];
        const commandSearchHook = React.useState("");
        const commandSearch = commandSearchHook[0];
        const setCommandSearch = commandSearchHook[1];
        const selectedKitHook = React.useState("");
        const selectedKit = selectedKitHook[0];
        const setSelectedKit = selectedKitHook[1];
        const newKitNameHook = React.useState("");
        const newKitName = newKitNameHook[0];
        const setNewKitName = newKitNameHook[1];
        const importFileHook = React.useState(null);
        const importFile = importFileHook[0];
        const setImportFile = importFileHook[1];
        const kitConfigHook = React.useState(null);
        const kitConfigView = kitConfigHook[0];
        const setKitConfigView = kitConfigHook[1];
        const kitWorkingHook = React.useState(false);
        const kitWorking = kitWorkingHook[0];
        const setKitWorking = kitWorkingHook[1];
        const setupIpFormHook = React.useState({ cisco: {}, netapp: {} });
        const setupIpForm = setupIpFormHook[0];
        const setSetupIpForm = setupIpFormHook[1];
        const setupIpWorkingHook = React.useState(false);
        const setupIpWorking = setupIpWorkingHook[0];
        const setSetupIpWorking = setupIpWorkingHook[1];
        const storageFormHook = React.useState({ target_host: "", username: "", password: "", target_mode: "defaults", include_in_ilo_run: true, apply_mode: "create_only", typed_confirmation: "", acknowledge_apply: false });
        const storageForm = storageFormHook[0];
        const setStorageForm = storageFormHook[1];
        const storageWorkingHook = React.useState(false);
        const storageWorking = storageWorkingHook[0];
        const setStorageWorking = storageWorkingHook[1];
        const setupActionLogHook = React.useState([]);
        const setupActionLog = setupActionLogHook[0];
        const setSetupActionLog = setupActionLogHook[1];

        function navigate(page) {
            setActivePage(page);
            window.location.hash = "#/" + page;
        }

        function appendSetupAction(label, text, ok, tone) {
            const entryTone = tone || (ok === false ? "warn" : "blue");
            const status = ok === false ? "failed" : (entryTone === "good" || entryTone === "ready" ? "verified" : "sent");
            setSetupActionLog(function (current) {
                return (current || []).concat([{ label: label, text: text, ok: ok, tone: entryTone, status: status }]).slice(-12);
            });
        }

        function loadAppState() {
            return apiGet("/api/ui/app-state").then(function (payload) {
                setAppState(payload);
                if (payload.setup_ip) {
                    setSetupIpForm({
                        cisco: Object.assign({}, (payload.setup_ip || {}).cisco || {}),
                        netapp: Object.assign({}, (payload.setup_ip || {}).netapp || {}),
                    });
                }
                if (payload.storage) applyStoragePayload(payload.storage);
            }).catch(function (error) {
                setMessage({ ok: false, text: error.message });
            });
        }

        function loadIlo() {
            return apiGet("/api/ui/ilo").then(function (payload) {
                applyIloPayload(payload);
            });
        }

        function applyIloPayload(payload) {
            setIloState(payload);
            const values = (payload || {}).values || {};
            setIloForm({
                current_ip: values.current_ip || "",
                target_ip: values.target_ip || "",
                gateway: values.gateway || "",
                hostname: values.hostname || "",
                username: values.username || "",
                password: "",
            });
        }

        function applyGlobalPayload(payload) {
            const data = payload || {};
            setGlobalState(data);
            setGlobalForm({
                values: Object.assign({}, data.values || {}),
                included: Object.assign({}, data.included || {}),
                snmp_users: (data.snmp_users || []).map(function (item) {
                    return Object.assign({}, item);
                }),
            });
        }

        function loadGlobal() {
            return apiGet("/api/ui/global-settings").then(applyGlobalPayload).catch(function (error) {
                setMessage({ ok: false, text: error.message });
            });
        }

        function applyStoragePayload(payload) {
            const values = (payload || {}).values || {};
            setStorageForm(function (current) {
                return Object.assign({}, current || {}, {
                    target_host: values.target_host || "",
                    username: values.username || "",
                    password: "",
                    target_mode: values.target_mode || "defaults",
                    include_in_ilo_run: values.include_in_ilo_run !== false,
                    apply_mode: (current || {}).apply_mode || "create_only",
                    typed_confirmation: (current || {}).typed_confirmation || "",
                    acknowledge_apply: !!((current || {}).acknowledge_apply),
                });
            });
        }

        function loadStorage() {
            return apiGet("/api/ui/storage").then(function (payload) {
                setAppState(function (current) {
                    if (!current) return current;
                    return Object.assign({}, current, { storage: payload });
                });
                applyStoragePayload(payload);
            }).catch(function (error) {
                setMessage({ ok: false, text: error.message });
            });
        }

        function loadReports(query, reportType) {
            const params = new URLSearchParams();
            params.set("report_query", query || "");
            params.set("report_type", reportType || "all");
            return apiGet("/api/ui/reports" + "?" + params.toString()).then(function (payload) {
                setAppState(function (current) {
                    if (!current) return current;
                    return Object.assign({}, current, { report_center: payload });
                });
            }).catch(function (error) {
                setMessage({ ok: false, text: error.message });
            });
        }

        function loadTechnical() {
            return apiGet("/api/ui/technical-events").then(setTechnical).catch(function (error) {
                setTechnical({ error: error.message, logs: [], artifacts: [] });
            });
        }

        function loadNetApp() {
            return apiGet("/modules/netapp/status").then(setNetappStatus).catch(function (error) {
                setNetappStatus({ error: error.message });
            });
        }

        React.useEffect(function () {
            loadAppState();
            loadIlo();
            loadGlobal();
            loadTechnical();
            const onHashChange = function () { setActivePage(pageFromHash()); };
            window.addEventListener("hashchange", onHashChange);
            const poll = window.setInterval(function () {
                apiGet("/api/ui/job-status").then(function (job) {
                    setAppState(function (current) {
                        if (!current) return current;
                        return Object.assign({}, current, { job: job });
                    });
                }).catch(function () {});
            }, 3000);
            return function () {
                window.removeEventListener("hashchange", onHashChange);
                window.clearInterval(poll);
            };
        }, []);

        React.useEffect(function () {
            if (activePage === "ilo") loadIlo();
            if (activePage === "global_settings") loadGlobal();
            if (activePage === "storage") loadStorage();
            if (activePage === "technical") loadTechnical();
            if (activePage === "netapp") loadNetApp();
        }, [activePage]);

        React.useEffect(function () {
            const kit = (appState || {}).kit || {};
            const otherKits = (kit.available || []).filter(function (name) { return name !== kit.name; });
            if (!selectedKit || selectedKit === kit.name || otherKits.indexOf(selectedKit) < 0) {
                setSelectedKit(otherKits[0] || "");
            }
        }, [appState]);

        function refreshAll() {
            setMessage(null);
            return Promise.all([loadAppState(), loadIlo(), loadGlobal(), loadTechnical()]).then(function () {
                if (activePage === "netapp") return loadNetApp();
                if (activePage === "storage") return loadStorage();
                return null;
            });
        }

        function saveIlo() {
            setSavingIlo(true);
            setMessage(null);
            const body = {
                current_ip: iloForm.current_ip,
                target_ip: iloForm.target_ip,
                gateway: iloForm.gateway,
                hostname: iloForm.hostname,
                username: iloForm.username,
            };
            if (iloForm.password) body.password = iloForm.password;
            apiPost("/api/ui/ilo/settings", body).then(function (payload) {
                setSavingIlo(false);
                setMessage({
                    ok: !!payload.ok,
                    tone: payload.ok ? "info" : "warn",
                    text: payload.ok ? "iLO setup saved locally. Reachability is not verified until Setup iLO IP completes." : (payload.message || "iLO save needs attention."),
                });
                if (payload.ilo) setIloState(payload.ilo);
                if (payload.app_state) setAppState(payload.app_state);
                return loadIlo();
            }).catch(function (error) {
                setSavingIlo(false);
                setMessage({ ok: false, text: error.message });
            });
        }

        function onIloChange(name, value) {
            setIloForm(function (current) {
                const next = Object.assign({}, current);
                next[name] = value;
                return next;
            });
        }

        function onGlobalValueChange(name, value) {
            setGlobalForm(function (current) {
                const next = Object.assign({ values: {}, included: {}, snmp_users: [] }, current || {});
                next.values = Object.assign({}, next.values || {});
                next.values[name] = value;
                return next;
            });
        }

        function onGlobalIncludedChange(name, checked) {
            setGlobalForm(function (current) {
                const next = Object.assign({ values: {}, included: {}, snmp_users: [] }, current || {});
                next.included = Object.assign({}, next.included || {});
                next.included[name] = !!checked;
                return next;
            });
        }

        function addSnmpUser() {
            setGlobalForm(function (current) {
                const next = Object.assign({ values: {}, included: {}, snmp_users: [] }, current || {});
                next.snmp_users = (next.snmp_users || []).slice();
                next.snmp_users.push({
                    username: "",
                    auth_protocol: "SHA",
                    auth_password: "",
                    auth_password_saved: false,
                    priv_protocol: "AES",
                    priv_password: "",
                    priv_password_saved: false,
                });
                return next;
            });
        }

        function updateSnmpUser(index, key, value) {
            setGlobalForm(function (current) {
                const next = Object.assign({ values: {}, included: {}, snmp_users: [] }, current || {});
                next.snmp_users = (next.snmp_users || []).map(function (item, itemIndex) {
                    if (itemIndex !== index) return item;
                    const updated = Object.assign({}, item);
                    updated[key] = value;
                    return updated;
                });
                return next;
            });
        }

        function removeSnmpUser(index) {
            setGlobalForm(function (current) {
                const next = Object.assign({ values: {}, included: {}, snmp_users: [] }, current || {});
                next.snmp_users = (next.snmp_users || []).filter(function (_, itemIndex) {
                    return itemIndex !== index;
                });
                return next;
            });
        }

        function saveGlobal() {
            if (!globalForm) return;
            setSavingGlobal(true);
            setMessage(null);
            apiPost("/api/ui/global-settings", globalForm).then(function (payload) {
                setSavingGlobal(false);
                setMessage({
                    ok: !!payload.ok,
                    tone: payload.ok ? "info" : "warn",
                    text: payload.ok ? "Global settings saved locally. Device reachability has not been verified." : (payload.message || "Global settings need attention."),
                });
                if (payload.global) applyGlobalPayload(payload.global);
                if (payload.ilo) applyIloPayload(payload.ilo);
                if (payload.app_state) {
                    setAppState(payload.app_state);
                    if (payload.app_state.setup_ip) {
                        setSetupIpForm({
                            cisco: Object.assign({}, (payload.app_state.setup_ip || {}).cisco || {}),
                            netapp: Object.assign({}, (payload.app_state.setup_ip || {}).netapp || {}),
                        });
                    }
                }
            }).catch(function (error) {
                setSavingGlobal(false);
                setMessage({ ok: false, text: error.message });
            });
        }

        function autofillGlobalIps() {
            const values = (globalForm || {}).values || {};
            setMessage(null);
            apiPost("/api/ui/global-settings/autofill", { shared_subnet: values.shared_subnet || "10.10.8.0/24" }).then(function (payload) {
                if (payload.plan) {
                    setGlobalForm(function (current) {
                        const next = Object.assign({ values: {}, included: {}, snmp_users: [] }, current || {});
                        next.values = Object.assign({}, next.values || {}, {
                            shared_subnet: payload.shared_subnet || next.values.shared_subnet,
                            gateway_ip: payload.plan.gateway || "",
                            switch_ip: payload.plan.switch || "",
                            esxi_ip: payload.plan.esxi || "",
                            ilo_target_ip: payload.plan.ilo || "",
                            windows_ip: payload.plan.windows || "",
                            qnap_ip: payload.plan.qnap || "",
                            iosafe_ip: payload.plan.iosafe || "",
                            netapp_ip: payload.plan.netapp || "",
                        });
                        return next;
                    });
                }
                setMessage({ ok: !!payload.ok, tone: payload.ok ? "info" : "warn", text: payload.message || "Default IP plan generated locally." });
            }).catch(function (error) {
                setMessage({ ok: false, text: error.message });
            });
        }

        function onSetupIpChange(moduleKey, name, value) {
            setSetupIpForm(function (current) {
                const next = Object.assign({ cisco: {}, netapp: {} }, current || {});
                next[moduleKey] = Object.assign({}, next[moduleKey] || {});
                next[moduleKey][name] = value;
                return next;
            });
        }

        function setupIloIp() {
            setSetupIpWorking(true);
            setMessage(null);
            appendSetupAction("iLO setup IP", "Saving iLO fields before starting IP setup.", true, "blue");
            const body = {
                current_ip: iloForm.current_ip,
                target_ip: iloForm.target_ip,
                gateway: iloForm.gateway,
                hostname: iloForm.hostname,
                username: iloForm.username,
            };
            if (iloForm.password) body.password = iloForm.password;
            apiPost("/api/ui/ilo/settings", body).then(function (payload) {
                if (!payload.ok) {
                    throw new Error(payload.message || "iLO setup could not be saved.");
                }
                if (payload.ilo) applyIloPayload(payload.ilo);
                if (payload.app_state) {
                    setAppState(payload.app_state);
                    if (payload.app_state.setup_ip) {
                        setSetupIpForm({
                            cisco: Object.assign({}, (payload.app_state.setup_ip || {}).cisco || {}),
                            netapp: Object.assign({}, (payload.app_state.setup_ip || {}).netapp || {}),
                        });
                    }
                }
                setMessage({ ok: true, tone: "info", text: "Starting iLO IP setup. Success will only be reported after reachability verification." });
                appendSetupAction("iLO setup IP", "POST /api/ui/ilo/setup-ip", true, "blue");
                return apiPost("/api/ui/ilo/setup-ip", {});
            }).then(function (payload) {
                if (!payload.ok) {
                    throw new Error(payload.message || "iLO IP setup could not start.");
                }
                if (payload.ilo) applyIloPayload(payload.ilo);
                if (payload.app_state) setAppState(payload.app_state);
                setSetupIpWorking(false);
                setMessage({ ok: true, tone: "info", text: "iLO IP setup started. Job Monitor will report verified completion only after the final IP responds." });
                appendSetupAction("iLO setup IP", payload.message || "iLO IP setup started.", true, "blue");
                return refreshAll();
            }).catch(function (error) {
                setSetupIpWorking(false);
                setMessage({ ok: false, text: error.message });
                appendSetupAction("iLO setup IP", error.message, false, "warn");
            });
        }

        function setupCiscoIp() {
            const cisco = (setupIpForm || {}).cisco || {};
            setSetupIpWorking(true);
            setMessage(null);
            appendSetupAction("Cisco setup IP", "POST /modules/cisco/bootstrap-management", true, "blue");
            htmlActionPost("/modules/cisco/bootstrap-management", {
                return_page: "cisco",
                cisco_switch_hostname: cisco.hostname || "",
                cisco_switch_username: cisco.username || "",
                cisco_switch_password: cisco.password || "",
                cisco_enable_password: cisco.enable_password || "",
                cisco_console_port: cisco.console_port || "",
                cisco_console_baud: cisco.console_baud || "9600",
                cisco_management_vlan: cisco.management_vlan || "10",
                cisco_management_ip: cisco.management_ip || "",
                cisco_subnet_mask: cisco.subnet_mask || "",
                cisco_gateway: cisco.gateway || "",
                cisco_domain_name: cisco.domain_name || "",
                cisco_bootstrap_network_port: cisco.bootstrap_network_port || "",
                cisco_bootstrap_network_mode: cisco.bootstrap_network_mode || "trunk",
            }).then(function () {
                setSetupIpWorking(false);
                setMessage({ ok: true, tone: "info", text: "Cisco setup request returned. Reachability has not been verified by this action." });
                appendSetupAction("Cisco setup IP", "Cisco management bootstrap route returned without reachability verification.", true, "blue");
                return refreshAll();
            }).catch(function (error) {
                setSetupIpWorking(false);
                setMessage({ ok: false, text: error.message });
                appendSetupAction("Cisco setup IP", error.message, false, "warn");
            });
        }

        function setupNetAppIp() {
            const netapp = (setupIpForm || {}).netapp || {};
            setSetupIpWorking(true);
            setMessage(null);
            appendSetupAction("NetApp setup IP", "POST /modules/netapp/apply-ip-setup", true, "blue");
            htmlActionPost("/modules/netapp/apply-ip-setup", {
                netapp_host: netapp.host || netapp.cluster_mgmt_ip || "",
                netapp_username: netapp.username || "admin",
                netapp_password: netapp.password || "",
                netapp_console_port: netapp.console_port || "",
                netapp_console_baud: netapp.console_baud || "9600",
                management_gateway: netapp.gateway || "",
                management_netmask: netapp.netmask || "",
                netapp_sp_a_ip: netapp.sp_a_ip || "",
                netapp_sp_b_ip: netapp.sp_b_ip || "",
                netapp_cluster_mgmt_ip: netapp.cluster_mgmt_ip || "",
                netapp_node_01_mgmt_ip: netapp.node_01_mgmt_ip || "",
                netapp_node_02_mgmt_ip: netapp.node_02_mgmt_ip || "",
                netapp_svm_mgmt_ip: netapp.svm_mgmt_ip || "",
            }).then(function (text) {
                setSetupIpWorking(false);
                if (String(text || "").indexOf("NetApp IP setup apply backend is not implemented yet") >= 0) {
                    const message = "NetApp IP setup was not applied. The backend saved the values but did not send NetApp commands.";
                    setMessage({ ok: false, text: message });
                    appendSetupAction("NetApp setup IP", message, false, "warn");
                } else {
                    setMessage({ ok: true, tone: "info", text: "NetApp setup request returned. Reachability has not been verified by this action." });
                    appendSetupAction("NetApp setup IP", "NetApp Apply IP setup route returned without reachability verification.", true, "blue");
                }
                return refreshAll();
            }).catch(function (error) {
                setSetupIpWorking(false);
                setMessage({ ok: false, text: error.message });
                appendSetupAction("NetApp setup IP", error.message, false, "warn");
            });
        }

        function storagePathFields(extra) {
            const storage = (appState || {}).storage || {};
            const discovery = storage.discovery || {};
            const plan = storage.plan || {};
            const apply = storage.apply || {};
            return Object.assign({
                return_page: "storage",
                discovery_raw_path: discovery.raw_path || "",
                raid_plan_path: plan.path || "",
                apply_artifact_dir: apply.directory || "",
            }, extra || {});
        }

        function runStorageAction(label, url, fields, options) {
            const opts = options || {};
            setStorageWorking(true);
            setMessage({ ok: true, tone: "info", text: label + " requested. The storage page will refresh after the backend returns." });
            appendSetupAction(label, "POST " + url, true, "blue");
            return htmlActionPost(url, fields).then(function () {
                setStorageWorking(false);
                setMessage({ ok: true, tone: "info", text: opts.success || (label + " returned. Review the refreshed storage state and job log before continuing.") });
                appendSetupAction(label, opts.log || "Backend route returned; refreshed storage state.", true, "blue");
                return refreshAll();
            }).catch(function (error) {
                setStorageWorking(false);
                setMessage({ ok: false, text: error.message });
                appendSetupAction(label, error.message, false, "warn");
            });
        }

        function onStorageFormChange(name, value) {
            setStorageForm(function (current) {
                const next = Object.assign({}, current || {});
                next[name] = value;
                return next;
            });
        }

        function saveStorageTarget(mode) {
            const targetMode = mode || storageForm.target_mode || "defaults";
            return runStorageAction("Save storage target", "/save-storage-target", {
                return_page: "storage",
                storage_target_mode: targetMode,
                storage_target_host: storageForm.target_host || "",
                storage_username: storageForm.username || "",
                storage_password: storageForm.password || "",
            }, { success: "Storage target saved. Use Display current storage setup to verify reachability." });
        }

        function readCurrentStorage() {
            return runStorageAction("Display current storage setup", "/read-current-storage", { return_page: "storage" }, { success: "Storage discovery returned. The page now shows the latest saved discovery state." });
        }

        function probeStorageCapabilities() {
            return runStorageAction("Probe storage capabilities", "/probe-storage-capabilities", { return_page: "storage" }, { success: "Storage capability probe returned. Review controller actions before applying a plan." });
        }

        function repairStorageSelection() {
            return runStorageAction("Clear invalid selections and reload inventory", "/repair-storage-selection", { return_page: "storage" }, { success: "Storage selections were cleared and discovery was refreshed." });
        }

        function buildStoragePlan() {
            return runStorageAction("Build storage plan", "/plan-raid-layout", storagePathFields(), { success: "Storage plan build returned. Review the plan summary before approving." });
        }

        function approveStoragePlan() {
            return runStorageAction("Approve this plan", "/approve-storage-plan", storagePathFields({ include_in_ilo_run: storageForm.include_in_ilo_run ? "on" : "" }), { success: "Storage approval returned. Confirm the approved state before running." });
        }

        function clearStorageApproval() {
            return runStorageAction("Remove approval", "/clear-storage-approval", storagePathFields(), { success: "Storage approval clear returned." });
        }

        function applyStorageLayout() {
            return runStorageAction("Apply storage layout", "/apply-storage-layout", storagePathFields({
                apply_mode: storageForm.apply_mode || "create_only",
                acknowledge_apply: storageForm.acknowledge_apply ? "on" : "",
                typed_confirmation: storageForm.typed_confirmation || "",
            }), { success: "Storage apply route returned. Watch the job log; do not treat this as complete until the workflow reports completion." });
        }

        function rebootStorageNow() {
            return runStorageAction("Reboot storage now", "/reboot-storage-now", storagePathFields(), { success: "Storage reboot request returned. Watch the job log for restart and post-reboot validation." });
        }

        function applyKitPayload(payload) {
            if (payload.app_state) setAppState(payload.app_state);
            if (payload.global) applyGlobalPayload(payload.global);
            if (payload.ilo) applyIloPayload(payload.ilo);
            if (payload.app_state && payload.app_state.storage) applyStoragePayload(payload.app_state.storage);
            if (payload.app_state && payload.app_state.setup_ip) {
                setSetupIpForm({
                    cisco: Object.assign({}, (payload.app_state.setup_ip || {}).cisco || {}),
                    netapp: Object.assign({}, (payload.app_state.setup_ip || {}).netapp || {}),
                });
            }
            setKitConfigView(null);
        }

        function loadKit(name) {
            const kitName = name || selectedKit;
            if (!kitName) return;
            setKitWorking(true);
            setMessage(null);
            apiPost("/api/ui/kits/load", { selected_kit: kitName }).then(function (payload) {
                setKitWorking(false);
                setMessage({ ok: !!payload.ok, text: payload.message || "Kit load finished." });
                applyKitPayload(payload);
            }).catch(function (error) {
                setKitWorking(false);
                setMessage({ ok: false, text: error.message });
            });
        }

        function createKit() {
            const kitName = String(newKitName || "").trim();
            if (!kitName) return;
            setKitWorking(true);
            setMessage(null);
            apiPost("/api/ui/kits/create", { new_kit_name: kitName }).then(function (payload) {
                setKitWorking(false);
                setNewKitName("");
                setMessage({ ok: !!payload.ok, text: payload.message || "Kit create finished." });
                applyKitPayload(payload);
            }).catch(function (error) {
                setKitWorking(false);
                setMessage({ ok: false, text: error.message });
            });
        }

        function viewCurrentConfig() {
            setKitWorking(true);
            setMessage(null);
            apiGet("/api/ui/current-kit-config").then(function (payload) {
                setKitWorking(false);
                setKitConfigView(payload);
                setMessage({ ok: !!payload.ok, text: "Opened current kit config." });
            }).catch(function (error) {
                setKitWorking(false);
                setMessage({ ok: false, text: error.message });
            });
        }

        function importKit() {
            if (!importFile) return;
            const form = new FormData();
            form.append("import_file", importFile);
            setKitWorking(true);
            setMessage(null);
            apiFormPost("/api/ui/kits/import", form).then(function (payload) {
                setKitWorking(false);
                setImportFile(null);
                setMessage({ ok: !!payload.ok, text: payload.message || "Kit import finished." });
                applyKitPayload(payload);
            }).catch(function (error) {
                setKitWorking(false);
                setMessage({ ok: false, text: error.message });
            });
        }

        function prepareReview() {
            setMessage({ ok: true, text: "Preparing run review through /prepare-execute..." });
            htmlActionPost("/prepare-execute", { scope: "included", return_page: "execution" }).then(function () {
                setMessage({ ok: true, text: "Run review prepared by the HTML action route. Open the Run Center for the full confirmation form." });
                return refreshAll();
            }).catch(function (error) {
                setMessage({ ok: false, text: error.message });
            });
        }

        function startPreview() {
            setMessage({ ok: true, text: "Starting preview run through /execute-preview..." });
            htmlActionPost("/execute-preview", { scope: "included", return_page: "execution" }).then(function () {
                setMessage({ ok: true, text: "Preview run requested through the HTML action route." });
                return refreshAll();
            }).catch(function (error) {
                setMessage({ ok: false, text: error.message });
            });
        }

        if (!appState) {
            return h("div", { className: "preview-loading" }, "Loading React desktop UI...");
        }

        let pageContent;
        if (activePage === "dashboard") {
            pageContent = h(DashboardPage, {
                appState: appState,
                selectedKit: selectedKit,
                onSelectedKitChange: setSelectedKit,
                newKitName: newKitName,
                onNewKitNameChange: setNewKitName,
                kitWorking: kitWorking,
                onLoadKit: loadKit,
                onCreateKit: createKit,
                onNavigate: navigate,
                onPrepareReview: prepareReview,
                onStartPreview: startPreview,
            });
        } else if (activePage === "global_settings") {
            pageContent = h(GlobalSettingsPage, {
                appState: appState,
                globalState: globalState,
                globalForm: globalForm,
                onValueChange: onGlobalValueChange,
                onIncludedChange: onGlobalIncludedChange,
                onAddSnmpUser: addSnmpUser,
                onUpdateSnmpUser: updateSnmpUser,
                onRemoveSnmpUser: removeSnmpUser,
                onAutofillGlobalIps: autofillGlobalIps,
                onSaveGlobal: saveGlobal,
                savingGlobal: savingGlobal,
                message: message,
                onNavigate: navigate,
            });
        } else if (activePage === "ilo") {
            pageContent = h(IloPage, { appState: appState, iloState: iloState, iloForm: iloForm, onIloChange: onIloChange, onSaveIlo: saveIlo, onSetupIloIp: setupIloIp, setupIpWorking: setupIpWorking, setupActionLog: setupActionLog, savingIlo: savingIlo, message: message, onNavigate: navigate });
        } else if (activePage === "storage") {
            pageContent = h(StoragePage, {
                appState: appState,
                storageForm: storageForm,
                working: storageWorking,
                setupActionLog: setupActionLog,
                onChange: onStorageFormChange,
                onSaveTarget: saveStorageTarget,
                onReadCurrent: readCurrentStorage,
                onProbeCapabilities: probeStorageCapabilities,
                onRepairSelection: repairStorageSelection,
                onBuildPlan: buildStoragePlan,
                onApprovePlan: approveStoragePlan,
                onClearApproval: clearStorageApproval,
                onApplyLayout: applyStorageLayout,
                onRebootNow: rebootStorageNow,
                onNavigate: navigate,
            });
        } else if (activePage === "execution") {
            pageContent = h(ExecutionPage, { appState: appState, onNavigate: navigate, onRefresh: refreshAll });
        } else if (activePage === "reports") {
            pageContent = h(ReportsPage, { appState: appState, onNavigate: navigate, onSearchReports: loadReports });
        } else if (activePage === "configuration") {
            pageContent = h(ConfigurationPage, {
                appState: appState,
                selectedKit: selectedKit,
                onSelectedKitChange: setSelectedKit,
                newKitName: newKitName,
                onNewKitNameChange: setNewKitName,
                importFile: importFile,
                onImportFileChange: setImportFile,
                kitConfigView: kitConfigView,
                kitWorking: kitWorking,
                onLoadKit: loadKit,
                onCreateKit: createKit,
                onViewCurrentConfig: viewCurrentConfig,
                onImportKit: importKit,
                onNavigate: navigate,
            });
        } else if (activePage === "action-map") {
            pageContent = h(ActionCatalogPage, { appState: appState, onNavigate: navigate });
        } else if (activePage === "technical") {
            pageContent = h(TechnicalPage, { appState: appState, technical: technical, onNavigate: navigate });
        } else {
            pageContent = h(MigrationPage, { page: activePage, appState: appState, netappStatus: netappStatus, setupIpForm: setupIpForm, setupIpWorking: setupIpWorking, setupActionLog: setupActionLog, onSetupIpChange: onSetupIpChange, onSetupCiscoIp: setupCiscoIp, onSetupNetAppIp: setupNetAppIp, onRefreshNetApp: loadNetApp, onNavigate: navigate });
        }

        return h("div", { className: "desktop-preview" },
            h(Sidebar, { pages: appState.pages || [], activePage: activePage, onNavigate: navigate, kit: appState.kit, app: appState.app, appState: appState }),
            h("div", { className: "workspace-shell" },
                h(TopStatus, { kit: appState.kit, job: appState.job }),
                h("main", { className: "workspace" },
                    h(WorkspaceHeading, { activePage: activePage, appState: appState, onNavigate: navigate, onRefresh: refreshAll, onToggleTechnical: function () { setTechnicalOpen(!technicalOpen); }, technicalOpen: technicalOpen }),
                    h(CommandBar, {
                        appState: appState,
                        query: commandSearch,
                        onSearch: setCommandSearch,
                        onNavigate: navigate,
                        onToggleTechnical: function () { setTechnicalOpen(!technicalOpen); },
                    }),
                    message && activePage !== "ilo" && activePage !== "global_settings" ? h("div", { className: "message " + messageClass(message) }, message.text) : null,
                    pageContent,
                    h(TechnicalDrawer, { open: technicalOpen, appState: appState, onClose: function () { setTechnicalOpen(false); } })
                )
            )
        );
    }

    ReactDOM.createRoot(root).render(h(App));
}());
