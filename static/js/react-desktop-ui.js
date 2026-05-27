(function () {
    const h = React.createElement;
    const root = document.getElementById("react-preview-root");
    const serverState = window.LAB_BUILDER_REACT_PREVIEW || {};

    const pageCopy = {
        dashboard: {
            title: "Dashboard / Run Center",
            eyebrow: "Operate",
            what: "Monitor the active kit, run readiness, live job state, and next operator decision.",
            legacy: "/dashboard",
        },
        ilo: {
            title: "iLO setup",
            eyebrow: "Real module",
            what: "Set the controller target and saved sign-in values used by Run Center.",
            legacy: "/ilo",
        },
        esxi: {
            title: "ESXi setup",
            eyebrow: "Migration shell",
            what: "Review ESXi install inputs, media readiness, and Run Center launch state.",
            legacy: "/esxi",
        },
        netapp: {
            title: "NetApp setup",
            eyebrow: "Real status",
            what: "Review ONTAP target state, safe apply status, and migration actions.",
            legacy: "/modules/netapp",
        },
        cisco: {
            title: "Cisco setup",
            eyebrow: "Migration shell",
            what: "Review switch management, console, SSH, config preview, and approval actions.",
            legacy: "/cisco",
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
            what: "Map React screens to preserved backend routes, legacy forms, downloads, and live streams.",
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

    function legacyPost(url, fields) {
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
        });
    }

    function toneClass(tone) {
        if (tone === "ready" || tone === "good") return "good";
        if (tone === "pending" || tone === "warn") return "warn";
        if (tone === "bad" || tone === "failed") return "red";
        return "blue";
    }

    function Pill(props) {
        return h("span", { className: "pill pill-" + toneClass(props.tone) }, props.children);
    }

    function Button(props) {
        const className = "button" + (props.primary ? " button-primary" : "");
        return h(
            props.href ? "a" : "button",
            {
                className: className,
                href: props.href,
                type: props.href ? undefined : (props.type || "button"),
                onClick: props.onClick,
                disabled: props.disabled,
            },
            props.children
        );
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

    function Sidebar(props) {
        const groups = {};
        (props.pages || []).forEach(function (page) {
            groups[page.group] = groups[page.group] || [];
            groups[page.group].push(page);
        });
        const icons = {
            dashboard: "RC",
            ilo: "IL",
            esxi: "EX",
            netapp: "NA",
            cisco: "CS",
            configuration: "CF",
            reports: "RP",
            "action-map": "AM",
            technical: "TD",
        };
        return h("aside", { className: "app-sidebar", "aria-label": "React desktop navigation" },
            h("div", { className: "brand-row" },
                h("div", { className: "brand-mark" }, "LB"),
                h("div", null,
                    h("div", { className: "brand-name" }, "Lab Builder"),
                    h("div", { className: "brand-subtitle" }, "React desktop UI")
                )
            ),
            Object.keys(groups).map(function (group) {
                return h("div", { key: group },
                    h("div", { className: "sidebar-section-title" }, group),
                    h("nav", { className: "nav-list" },
                        groups[group].map(function (page) {
                            const active = props.activePage === page.key;
                            const icon = icons[page.key] || page.key.slice(0, 2).toUpperCase();
                            return h("a", {
                                key: page.key,
                                className: "nav-item" + (active ? " nav-item-active" : ""),
                                href: "#/" + page.key,
                                onClick: function () { props.onNavigate(page.key); },
                            },
                                h("span", { className: "nav-icon", "aria-hidden": "true" }, icon),
                                h("span", { className: "nav-text" },
                                    h("span", { className: "nav-label" }, page.label),
                                    h("span", { className: "nav-meta" }, page.legacy_href)
                                ),
                                active ? h(Pill, { tone: "blue" }, "Open") : null
                            );
                        })
                    )
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
                h("div", { className: "status-cluster" }, "UI: experimental")
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
                h(Button, { href: copy.legacy }, "Legacy page"),
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
                    h(Button, { onClick: function () { props.onNavigate("dashboard"); } }, "Run Center"),
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
                h("span", null, String(coverage.legacy_routes || 0) + " legacy fallbacks"),
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
                h(Button, { onClick: function () { props.onNavigate(module.key); } }, "Open"),
                h(Button, { href: module.legacy_href }, "Legacy")
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
                    detail: blocker.fix || blocker.details || "Review the legacy page for the full form state.",
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
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: "Monitor readiness, current job state, and the next operator decision for the active kit.",
                    next: ((dashboard.next_step || {}).title || "Review the run") + ": " + ((dashboard.next_step || {}).summary || "Open Run Center when ready."),
                    last: (dashboard.latest_result || {}).label || "No completed runs yet",
                }),
                h(LiveJobPanel, { job: state.job },
                    h("div", { className: "job-actions" },
                        h(Button, { onClick: props.onPrepareReview }, "Prepare review"),
                        h(Button, { onClick: props.onStartPreview, primary: true }, "Start preview run"),
                        h(Button, { href: "/execution" }, "Legacy Run Center")
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
                h(Panel, { label: "Modules", title: "Setup workspaces", subtitle: "Real backend state is shown where APIs are wired; legacy pages remain one click away." },
                    h("div", { className: "module-grid" }, modules.map(function (module) {
                        return h(ModuleCard, { key: module.key, module: module, onNavigate: props.onNavigate });
                    }))
                ),
                h("div", { className: "dashboard-lower-grid" },
                    h(JobTimelinePanel, { job: state.job, modules: modules }),
                    h(WarningsPanel, { modules: modules })
                ),
                h(RecentActivityPanel, { activity: state.recent_activity || [] })
            ),
            h(ContextPanel, { activePage: "dashboard", appState: state, onNavigate: props.onNavigate })
        );
    }

    function IloPage(props) {
        const ilo = props.iloState || {};
        const values = ilo.values || {};
        const review = ilo.review || {};
        const page = ilo.page || pageCopy.ilo;
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, { what: page.what, next: page.next, last: page.last }),
                props.message ? h("div", { className: "message " + (props.message.ok ? "message-good" : "message-warn") }, props.message.text) : null,
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
                            h(Field, { label: "Current iLO IP", name: "current_ip", value: props.iloForm.current_ip, onChange: props.onIloChange }),
                            h(Field, { label: "Planned final IP", name: "target_ip", value: props.iloForm.target_ip, onChange: props.onIloChange }),
                            h(Field, { label: "Gateway", name: "gateway", value: props.iloForm.gateway, onChange: props.onIloChange }),
                            h(Field, { label: "Hostname", name: "hostname", value: props.iloForm.hostname, onChange: props.onIloChange }),
                            h(Field, { label: "Username", name: "username", value: props.iloForm.username, onChange: props.onIloChange }),
                            h(Field, { label: "Password", name: "password", type: "password", value: props.iloForm.password, onChange: props.onIloChange, help: values.password_saved ? "Leave blank to keep the saved password." : "Enter the iLO password to save it." })
                        ),
                        h("div", { className: "job-actions" },
                            h(Button, { primary: true, type: "submit", disabled: props.savingIlo }, props.savingIlo ? "Saving..." : "Save iLO setup"),
                            h(Button, { href: "/ilo" }, "Legacy iLO page")
                        )
                    )
                ),
                h(Panel, { label: "Review", title: "Validation and warnings", subtitle: "Only operator-facing checks appear here. Raw logs stay in Technical details." },
                    review.errors && review.errors.length ? h("div", { className: "message message-warn" }, review.errors.join(" ")) : h("div", { className: "message message-good" }, "No blocking iLO input errors."),
                    h("div", { className: "data-list" },
                        (review.checks || []).map(function (check) {
                            return h("div", { className: "data-row", key: check.label },
                                h("div", null,
                                    h("div", { className: "data-name" }, check.label),
                                    h("div", { className: "data-value" }, check.details || check.fix || "")
                                ),
                                h(Pill, { tone: check.ok ? "ready" : "warn" }, check.ok ? "Ready" : "Blocked")
                            );
                        })
                    )
                )
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

    function MigrationPage(props) {
        const state = props.appState || {};
        const copy = pageCopy[props.page] || pageCopy.esxi;
        const module = (state.modules || []).find(function (item) { return item.key === props.page; }) || {};
        const actions = ((state.actions || {})[props.page] || []);
        const last = module.last_summary || "No migrated React action has run yet.";
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: copy.what,
                    next: module.blockers && module.blockers.length ? module.blockers[0].fix || "Open the legacy page to finish required setup." : "Use the legacy page for full action coverage while this React page is migrated.",
                    last: last,
                }),
                props.page === "netapp" ? h(NetAppStatusPanel, { netappStatus: props.netappStatus, onRefresh: props.onRefreshNetApp }) : null,
                h(Panel, {
                    label: "Migration status",
                    title: copy.title,
                    subtitle: "This React workspace is in the shell phase. Backend actions remain available through the legacy page and listed endpoints.",
                    action: h(Pill, { tone: module.tone || "blue" }, module.state_label || "Mapped")
                },
                    h("div", { className: "section-grid" },
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Target"), h("div", { className: "strip-value" }, module.target || "Not set")),
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Checks"), h("div", { className: "strip-value" }, (module.checks_ready || 0) + " / " + (module.total_checks || 0))),
                        h("div", { className: "setup-card" }, h("div", { className: "metric-label" }, "Included"), h("div", { className: "strip-value" }, module.included ? "Yes" : "No"))
                    ),
                    module.blockers && module.blockers.length ? h("div", { className: "message message-warn" }, module.blockers[0].label + ": " + (module.blockers[0].fix || module.blockers[0].details || "Review required.")) : h("div", { className: "message message-good" }, "No blockers reported by the summary API.")
                ),
                h(ActionInventoryPanel, { actions: actions })
            ),
            h(ContextPanel, { activePage: props.page, appState: state, actions: actions, onNavigate: props.onNavigate })
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

    function ConfigurationPage(props) {
        const state = props.appState || {};
        const kit = state.kit || {};
        const actions = ((state.actions || {}).configuration || []);
        const available = kit.available || [];
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
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: pageCopy.configuration.what,
                    next: "Use the legacy Configuration page for saves until each form receives a JSON endpoint.",
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
                h(Panel, { label: "Kit library", title: "Saved kits", subtitle: "The React shell lists kits; create/load/import still goes through the existing forms." },
                    available.length ? h("div", { className: "kit-list" }, available.map(function (name) {
                        return h("div", { className: "kit-row", key: name },
                            h("div", null,
                                h("div", { className: "data-name" }, name),
                                h("div", { className: "data-value" }, name === kit.name ? "Active" : "Available")
                            ),
                            h(Pill, { tone: name === kit.name ? "ready" : "blue" }, name === kit.name ? "Active" : "Saved")
                        );
                    })) : h("div", { className: "empty-state" }, "No kits found.")
                ),
                h(ActionInventoryPanel, { actions: actions })
            ),
            h(ContextPanel, { activePage: "configuration", appState: state, actions: actions, onNavigate: props.onNavigate })
        );
    }

    function ReportsPage(props) {
        const state = props.appState || {};
        return h("div", { className: "page-layout" },
            h("div", { className: "page-main" },
                h(SetupStrip, {
                    what: pageCopy.reports.what,
                    next: "Open a run summary or debug artifact from the legacy Reports page when deeper inspection is needed.",
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
                )
            ),
            h(ContextPanel, { activePage: "reports", appState: state, onNavigate: props.onNavigate })
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
        return h(Panel, { label: "Backend action inventory", title: "Mapped routes", subtitle: "These are the current routes this React page must preserve or consume." },
            actions.length ? h("div", { className: "action-list" }, actions.map(function (action) {
                return h("div", { className: "action-row", key: action.method + action.route + action.label },
                    h("div", null,
                        h("div", { className: "data-name" }, action.label),
                        h("div", { className: "data-value" }, action.method + " " + action.route)
                    ),
                    h(Pill, { tone: action.mode === "json" ? "ready" : "blue" }, action.mode)
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
                    next: "Move one high-use legacy action at a time behind a JSON endpoint, then keep the old page as fallback.",
                    last: String(coverage.mapped_actions || 0) + " mapped action routes out of " + String(coverage.total_routes || 0) + " registered backend routes.",
                }),
                h(Panel, { label: "Coverage", title: "Backend route surface", subtitle: "Generated from FastAPI's registered routes at request time." },
                    h("div", { className: "coverage-strip" },
                        [["Total routes", coverage.total_routes || 0], ["React APIs", coverage.react_api_routes || 0], ["Legacy fallbacks", coverage.legacy_routes || 0], ["Mapped actions", coverage.mapped_actions || 0], ["Downloads", coverage.download_routes || 0], ["Streams", coverage.websocket_routes || 0]].map(function (item) {
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
                    subtitle: "Legacy behavior remains available while React pages move to JSON APIs.",
                    action: h(Button, { href: "/configuration" }, "Legacy configuration")
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
                                h("div", null, h(Pill, { tone: route.mode === "json" ? "ready" : route.mode === "legacy-html" ? "blue" : "warn" }, route.mode)),
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
                    next.href ? h(Button, { href: next.href }, "Open next") : null,
                    h(Button, { href: copy.legacy }, "Legacy page")
                )
            ),
            h(KitSummaryPanel, { kit: state.kit || {} }),
            h(Panel, { label: "Page context", title: copy.title, subtitle: copy.what },
                h("div", { className: "data-list" },
                    h("div", { className: "data-row" }, h("div", null, h("div", { className: "data-name" }, "Fallback"), h("div", { className: "data-value" }, copy.legacy))),
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
        const drawerHook = React.useState(false);
        const technicalOpen = drawerHook[0];
        const setTechnicalOpen = drawerHook[1];
        const commandSearchHook = React.useState("");
        const commandSearch = commandSearchHook[0];
        const setCommandSearch = commandSearchHook[1];

        function navigate(page) {
            setActivePage(page);
            window.location.hash = "#/" + page;
        }

        function loadAppState() {
            return apiGet("/api/ui/app-state").then(setAppState).catch(function (error) {
                setMessage({ ok: false, text: error.message });
            });
        }

        function loadIlo() {
            return apiGet("/api/ui/ilo").then(function (payload) {
                setIloState(payload);
                const values = payload.values || {};
                setIloForm({
                    current_ip: values.current_ip || "",
                    target_ip: values.target_ip || "",
                    gateway: values.gateway || "",
                    hostname: values.hostname || "",
                    username: values.username || "",
                    password: "",
                });
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
            if (activePage === "technical") loadTechnical();
            if (activePage === "netapp") loadNetApp();
        }, [activePage]);

        function refreshAll() {
            setMessage(null);
            return Promise.all([loadAppState(), loadIlo(), loadTechnical()]).then(function () {
                if (activePage === "netapp") return loadNetApp();
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
                setMessage({ ok: !!payload.ok, text: payload.message || "iLO save finished." });
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

        function prepareReview() {
            setMessage({ ok: true, text: "Preparing run review through /prepare-execute..." });
            legacyPost("/prepare-execute", { scope: "included", return_page: "execution" }).then(function () {
                setMessage({ ok: true, text: "Run review prepared by the existing backend route. Open the legacy Run Center for the full confirmation form." });
                return refreshAll();
            }).catch(function (error) {
                setMessage({ ok: false, text: error.message });
            });
        }

        function startPreview() {
            setMessage({ ok: true, text: "Starting preview run through /execute-preview..." });
            legacyPost("/execute-preview", { scope: "included", return_page: "execution" }).then(function () {
                setMessage({ ok: true, text: "Preview run requested through the existing backend route." });
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
            pageContent = h(DashboardPage, { appState: appState, onNavigate: navigate, onPrepareReview: prepareReview, onStartPreview: startPreview });
        } else if (activePage === "ilo") {
            pageContent = h(IloPage, { appState: appState, iloState: iloState, iloForm: iloForm, onIloChange: onIloChange, onSaveIlo: saveIlo, savingIlo: savingIlo, message: message, onNavigate: navigate });
        } else if (activePage === "reports") {
            pageContent = h(ReportsPage, { appState: appState, onNavigate: navigate });
        } else if (activePage === "configuration") {
            pageContent = h(ConfigurationPage, { appState: appState, onNavigate: navigate });
        } else if (activePage === "action-map") {
            pageContent = h(ActionCatalogPage, { appState: appState, onNavigate: navigate });
        } else if (activePage === "technical") {
            pageContent = h(TechnicalPage, { appState: appState, technical: technical, onNavigate: navigate });
        } else {
            pageContent = h(MigrationPage, { page: activePage, appState: appState, netappStatus: netappStatus, onRefreshNetApp: loadNetApp, onNavigate: navigate });
        }

        return h("div", { className: "desktop-preview" },
            h(Sidebar, { pages: appState.pages || [], activePage: activePage, onNavigate: navigate, kit: appState.kit, app: appState.app }),
            h("div", { className: "workspace-shell" },
                h(TopStatus, { kit: appState.kit, job: appState.job }),
                h("main", { className: "workspace" },
                    h(WorkspaceHeading, { activePage: activePage, onRefresh: refreshAll, onToggleTechnical: function () { setTechnicalOpen(!technicalOpen); }, technicalOpen: technicalOpen }),
                    h(CommandBar, {
                        appState: appState,
                        query: commandSearch,
                        onSearch: setCommandSearch,
                        onNavigate: navigate,
                        onToggleTechnical: function () { setTechnicalOpen(!technicalOpen); },
                    }),
                    message && activePage !== "ilo" ? h("div", { className: "message " + (message.ok ? "message-good" : "message-warn") }, message.text) : null,
                    pageContent,
                    h(TechnicalDrawer, { open: technicalOpen, appState: appState, onClose: function () { setTechnicalOpen(false); } })
                )
            )
        );
    }

    ReactDOM.createRoot(root).render(h(App));
}());
