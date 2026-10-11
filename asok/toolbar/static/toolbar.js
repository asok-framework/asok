/* ASOK DEBUG SUITE JS
 * @author: Asok Framework Team
 * @version: 0.9.0 */
(function () {
    const trigger = document.getElementById('asok-debug-trigger');
    const suite = document.getElementById('asok-debug-suite');
    const closeBtn = document.getElementById('asok-close-btn');
    const navItems = document.querySelectorAll('.asok-nav-item[data-tab]');
    const tabs = document.querySelectorAll('.asok-debug-tab');
    const reactiveList = document.getElementById('asok-reactive-list');
    const reactiveCount = document.getElementById('asok-reactive-count');
    const templatesPre = document.getElementById('asok-templates-pre');
    const templatesCount = document.getElementById('asok-templates-count');
    const templatesLabel = document.getElementById('asok-templates-label');
    const routerBadge = document.getElementById('asok-router-badge');
    const clientRouterInfo = document.getElementById('asok-client-router-info');
    const spaRoutesList = document.getElementById('asok-spa-routes-list');
    const spaLogRows = document.getElementById('asok-spa-log-rows');
    const clearSpaLogBtn = document.getElementById('asok-clear-spa-log');

    // Shared data from backend templates
    let templateInfo = [[tpl_json]];
    let routingInfo = [[routing_json]];
    window.Asok = window.Asok || {};
    window.Asok.wsStates = window.Asok.wsStates || {};

    // SPA history records
    const spaEvents = [];
    let pendingSwapTime = null;
    let pendingSwapUrl = null;

    const formatTime = (date) => {
        const d = date || new Date();
        const pad = (n) => String(n).padStart(2, '0');
        return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    };

    const addSpaLog = (type, target, blockOrMode, status, duration) => {
        const entry = {
            time: formatTime(),
            type,
            target: String(target || '/'),
            blockOrMode: String(blockOrMode || '-'),
            status: String(status || 'OK'),
            duration: duration ? Math.round(duration) + 'ms' : '-'
        };
        spaEvents.unshift(entry);
        if (spaEvents.length > 50) spaEvents.pop();
        if (routerBadge) routerBadge.textContent = spaEvents.length;
        renderSpaLog();
    };

    const renderSpaLog = () => {
        if (!spaLogRows) return;
        if (!spaEvents.length) {
            spaLogRows.innerHTML = `<tr><td colspan="5"><div class="asok-empty" style="padding:24px 0;"><span class="asok-empty-text">No SPA navigation or block swap recorded yet.</span></div></td></tr>`;
            return;
        }

        let html = '';
        spaEvents.forEach((ev) => {
            let badgeClass = 'asok-badge-neutral';
            if (ev.type === 'route') badgeClass = 'asok-badge-route';
            else if (ev.type === 'swap') badgeClass = 'asok-badge-swap';
            else if (ev.type === 'error') badgeClass = 'asok-badge-error';
            else if (ev.type === 'ws') badgeClass = 'asok-badge-ws';

            let statusClass = 'asok-time-fast';
            if (ev.type === 'error' || ev.status.startsWith('Error')) statusClass = 'asok-time-slow';

            html += `<tr>
                <td style="color:var(--fg-3); font-size:11px;">${ev.time}</td>
                <td><span class="asok-spa-pill ${badgeClass}">${ev.type}</span></td>
                <td><div class="asok-query-sql" style="font-size:12px">${ev.target}</div></td>
                <td style="color:var(--fg-2); font-size:11px;">${ev.blockOrMode}</td>
                <td style="text-align:right; padding-right:16px"><span class="${statusClass}">${ev.status} ${ev.duration !== '-' ? '(' + ev.duration + ')' : ''}</span></td>
            </tr>`;
        });
        spaLogRows.innerHTML = html;
    };

    if (clearSpaLogBtn) {
        clearSpaLogBtn.onclick = () => {
            spaEvents.length = 0;
            if (routerBadge) routerBadge.textContent = '0';
            renderSpaLog();
        };
    }

    // Toggle Suite
    trigger.onclick = () => suite.classList.toggle('asok-open');
    if (closeBtn) closeBtn.onclick = () => suite.classList.remove('asok-open');

    // Tab Switching
    navItems.forEach(item => {
        item.onclick = () => {
            const tabName = item.getAttribute('data-tab');
            navItems.forEach(i => i.classList.remove('active'));
            tabs.forEach(t => t.classList.remove('active'));

            item.classList.add('active');
            const targetTab = document.getElementById('tab-' + tabName);
            if (targetTab) targetTab.classList.add('active');
            if (tabName === 'reactive') refreshReactive();
            if (tabName === 'router') refreshRouter();
        };
    });

    // Update Templates Badge
    const updateTemplates = () => {
        const blocks = templateInfo['Partial Blocks'] || [];
        const ws = templateInfo['WS Components'] || [];
        if (blocks.length || ws.length) {
            if (templatesLabel) templatesLabel.textContent = 'Blocks';
            if (templatesCount) templatesCount.textContent = blocks.length + ws.length;
        } else {
            if (templatesLabel) templatesLabel.textContent = 'Templates';
            if (templatesCount) templatesCount.textContent = (templateInfo['All Templates'] || []).length;
        }
        if (templatesPre) templatesPre.textContent = JSON.stringify(templateInfo, null, 2);
    };

    // Client Router inspection
    const refreshRouter = () => {
        const clientRoute = window.AsokRouter ? window.AsokRouter.route : (window.Asok ? window.Asok.$route : null);
        const currentPath = window.location.pathname;

        if (clientRouterInfo) {
            const pathDisplay = clientRoute ? clientRoute.path : currentPath;
            const queryDisplay = clientRoute ? JSON.stringify(clientRoute.query) : '{}';
            const paramsDisplay = clientRoute ? JSON.stringify(clientRoute.params) : '{}';
            const hashDisplay = window.location.hash || '(empty)';

            clientRouterInfo.innerHTML = `
                <div class="asok-router-grid">
                    <div>
                        <div class="asok-meta-label">Client Path</div>
                        <div class="asok-meta-value">${pathDisplay}</div>
                    </div>
                    <div>
                        <div class="asok-meta-label">Extracted Params</div>
                        <div class="asok-meta-value" style="font-size:12px">${paramsDisplay}</div>
                    </div>
                    <div>
                        <div class="asok-meta-label">Query Search</div>
                        <div class="asok-meta-value" style="font-size:12px">${queryDisplay}</div>
                    </div>
                    <div>
                        <div class="asok-meta-label">URL Hash</div>
                        <div class="asok-meta-value" style="font-size:12px">${hashDisplay}</div>
                    </div>
                </div>
            `;
        }

        if (spaRoutesList) {
            const containers = document.querySelectorAll('[asok-router], [data-asok-router], [data-router]');
            if (!containers.length) {
                spaRoutesList.innerHTML = `<div class="asok-empty" style="padding:20px 0;"><span class="asok-empty-text">No [asok-router] container in current DOM view.</span></div>`;
            } else {
                let html = '';
                containers.forEach((ct, i) => {
                    const routes = Array.from(ct.querySelectorAll('[asok-route], [data-asok-route], [data-route]'));
                    html += `<div class="asok-reactive-card" style="margin-bottom:8px">
                        <div class="asok-reactive-header">
                            <div class="asok-reactive-dot"></div>
                            <div class="asok-reactive-name">&lt;${ct.tagName.toLowerCase()}&gt; Router Container #${i + 1} (${routes.length} routes declared)</div>
                        </div>
                        <div style="padding:8px 14px">`;
                    if (!routes.length) {
                        html += `<span style="color:var(--fg-3); font-size:12px;">No route templates inside container.</span>`;
                    } else {
                        routes.forEach(r => {
                            const pattern = r.getAttribute('asok-route') || r.getAttribute('data-asok-route') || r.getAttribute('data-route') || '*';
                            const isMatch = window.AsokRouter && window.AsokRouter.matchPattern ? (window.AsokRouter.matchPattern(pattern, currentPath) !== null) : false;
                            const badge = isMatch ? '<span class="asok-route-badge-active">MATCH / ACTIVE</span>' : '<span class="asok-route-badge-inactive">INACTIVE</span>';
                            html += `<div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.04); font-family:var(--font-mono); font-size:12px;">
                                <span style="color:var(--fg);">${pattern}</span>
                                <div>${badge}</div>
                            </div>`;
                        });
                    }
                    html += `</div></div>`;
                });
                spaRoutesList.innerHTML = html;
            }
        }
        renderSpaLog();
    };

    // Refresh Reactive States
    const refreshReactive = () => {
        const clientStates = document.querySelectorAll('[asok-state], [asok-state-ref]');
        const domComponents = document.querySelectorAll('[data-asok-component], [ws-component]');
        const wsComponents = Object.keys(window.Asok.wsStates);
        const storeKeys = window.Asok && window.Asok.store ? Object.keys(window.Asok.store).filter(k => !k.startsWith('_')) : [];

        if (reactiveCount) {
            reactiveCount.textContent = clientStates.length + Math.max(domComponents.length, wsComponents.length);
        }

        if (!document.getElementById('tab-reactive').classList.contains('active')) return;

        let html = '';

        if (storeKeys.length) {
            html += '<div class="asok-section-header">Global Shared Store (window.Asok.store)</div>';
            const storeObj = {};
            storeKeys.forEach(k => { storeObj[k] = window.Asok.store[k]; });
            html += `<div class="asok-reactive-card">
                <div class="asok-reactive-header">
                    <div class="asok-reactive-dot" style="background:var(--info); box-shadow:0 0 6px var(--info);"></div>
                    <div class="asok-reactive-name">window.Asok.store (${storeKeys.length} properties)</div>
                </div>
                <div class="asok-reactive-body">${JSON.stringify(storeObj, null, 2)}</div>
            </div>`;
        }

        if (clientStates.length) {
            html += '<div class="asok-section-header" style="margin-top:12px">Client States (Directives)</div>';
            clientStates.forEach((el, i) => {
                const ctx = window.AsokDirectives?.w?.get(el);
                if (ctx && ctx.state) {
                    const modelInputs = el.querySelectorAll('[asok-model]');
                    const models = Array.from(modelInputs).map(m => m.getAttribute('asok-model')).join(', ');
                    html += `<div class="asok-reactive-card">
                        <div class="asok-reactive-header">
                            <div class="asok-reactive-dot"></div>
                            <div class="asok-reactive-name">&lt;${el.tagName.toLowerCase()}&gt; #${i + 1} ${models ? '<span style="color:var(--fg-3); font-size:11px;">(models: ' + models + ')</span>' : ''}</div>
                        </div>
                        <div class="asok-reactive-body">${JSON.stringify(ctx.state, null, 2)}</div>
                    </div>`;
                }
            });
        }

        if (wsComponents.length || domComponents.length) {
            html += '<div class="asok-section-header" style="margin-top:12px">Live Components (WebSocket &amp; WS Directives)</div>';
            const seenCids = new Set();
            wsComponents.forEach(cid => {
                seenCids.add(cid);
                const entry = window.Asok.wsStates[cid];
                html += `<div class="asok-reactive-card">
                    <div class="asok-reactive-header">
                        <div class="asok-reactive-dot" style="background:#a855f7; box-shadow:0 0 6px #a855f7;"></div>
                        <div class="asok-reactive-name">${entry.name || 'LiveComponent'} <span style="color:var(--fg-3);font-size:10px">#${cid}</span></div>
                    </div>
                    <div class="asok-reactive-body">${JSON.stringify(entry.state, null, 2)}</div>
                </div>`;
            });

            domComponents.forEach((el) => {
                const cid = el.id ? el.id.replace('asok-', '') : 'dom';
                if (!seenCids.has(cid)) {
                    const compName = el.getAttribute('data-asok-component') || el.getAttribute('ws-component') || 'MountedComponent';
                    const actions = Array.from(el.querySelectorAll('[ws-click], [ws-submit], [ws-change]')).map(a => a.getAttribute('ws-click') || a.getAttribute('ws-submit') || a.getAttribute('ws-change'));
                    html += `<div class="asok-reactive-card">
                        <div class="asok-reactive-header">
                            <div class="asok-reactive-dot" style="background:#a855f7; box-shadow:0 0 6px #a855f7;"></div>
                            <div class="asok-reactive-name">${compName} <span style="color:var(--fg-3);font-size:10px">#${cid}</span></div>
                        </div>
                        <div class="asok-reactive-body" style="padding:10px 14px;">Actions: ${actions.length ? actions.join(', ') : 'None detected'}</div>
                    </div>`;
                }
            });
        }

        if (!html) {
            html = `<div class="asok-empty" style="height:200px">
                <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
                <span class="asok-empty-text">No reactive components found.</span>
            </div>`;
        }

        if (reactiveList) reactiveList.innerHTML = html;
    };

    // Listeners for SPA and Routing
    window.addEventListener('asok:routechange', (e) => {
        const detail = e.detail || {};
        addSpaLog('route', detail.path, detail.params ? JSON.stringify(detail.params) : '-', '200 OK', 0);
        refreshRouter();
    });

    window.addEventListener('popstate', () => {
        addSpaLog('route', window.location.pathname, 'popstate', 'History Nav', 0);
        refreshRouter();
    });

    document.addEventListener('asok:before', (e) => {
        pendingSwapTime = performance.now();
        pendingSwapUrl = e.detail ? e.detail.url : window.location.pathname;
    });

    document.addEventListener('asok:success', (e) => {
        const duration = pendingSwapTime ? (performance.now() - pendingSwapTime) : 0;
        const targetBlock = window.Asok && window.Asok.lastBlocks ? window.Asok.lastBlocks : (e.detail?.mode || 'swap');
        addSpaLog('swap', pendingSwapUrl || window.location.pathname, targetBlock, 'Success', duration);
        pendingSwapTime = null;
        pendingSwapUrl = null;

        if (window.Asok && window.Asok.lastBlocks) {
            templateInfo['Partial Blocks'] = window.Asok.lastBlocks.split(',');
            updateTemplates();
        }

        // Handle AJAX SQL Updates
        const sqlLogRaw = window.Asok && window.Asok.lastSqlLog;
        if (sqlLogRaw) {
            try {
                const newLogs = JSON.parse(sqlLogRaw);
                const sqlRows = document.getElementById('asok-sql-rows');
                const sqlBadge = document.querySelector('.asok-nav-item[data-tab="sql"] .asok-nav-badge');

                if (newLogs.length > 0) {
                    const sep = document.createElement('tr');
                    sep.className = 'asok-ajax-banner';
                    sep.innerHTML = `<td colspan="3">&#9650; AJAX BLOCK &mdash; ${newLogs.length} queries</td>`;
                    sqlRows.appendChild(sep);

                    newLogs.forEach((entry, i) => {
                        const row = document.createElement('tr');
                        const duration = entry.duration || 0;
                        const tc = duration > 50 ? 'asok-time-slow' : 'asok-time-fast';
                        row.innerHTML = `
                            <td style="color:var(--fg-3)">${i + 1}</td>
                            <td>
                                <div class="asok-query-sql">${entry.sql}</div>
                                <div class="asok-query-params">Params: ${JSON.stringify(entry.params || [])}</div>
                            </td>
                            <td style="text-align:right; padding-right:24px"><span class="${tc}">${duration.toFixed(2)}ms</span></td>
                        `;
                        sqlRows.appendChild(row);
                    });

                    const currentCount = parseInt(sqlBadge ? sqlBadge.textContent : '0') || 0;
                    if (sqlBadge) sqlBadge.textContent = currentCount + newLogs.length;
                }
                window.Asok.lastSqlLog = null;
            } catch (err) {
                console.error('Asok Toolbar: Failed to parse SQL logs', err);
            }
        }
        refreshRouter();
    });

    document.addEventListener('asok:error', (e) => {
        addSpaLog('error', e.detail?.url || window.location.pathname, '-', 'Error ' + (e.detail?.status || 500), 0);
    });

    document.addEventListener('asok:ws-update', (e) => {
        if (!templateInfo['WS Components']) templateInfo['WS Components'] = [];
        if (!templateInfo['WS Components'].includes(e.detail.cid)) {
            templateInfo['WS Components'].push(e.detail.cid);
        }
        window.Asok.wsStates[e.detail.cid] = {
            name: e.detail.name,
            state: e.detail.state
        };
        addSpaLog('ws', e.detail.name + ' #' + e.detail.cid, 'state-diff', 'Live Update', 0);
        updateTemplates();
        refreshReactive();
    });

    // Auto-refresh interval
    setInterval(refreshReactive, 1000);

    // Initial state record
    addSpaLog('initial', window.location.pathname, routingInfo ? routingInfo['Render Type'] : 'Full Page', 'Loaded', 0);
    updateTemplates();
    refreshReactive();
    refreshRouter();
})();
