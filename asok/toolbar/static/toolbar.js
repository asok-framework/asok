/* ASOK DEBUG SUITE JS
 * @author: Asok Framework Team
 * @version: 0.1.5 */
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

    // Shared data from template
    let templateInfo = [[tpl_json]];
    window.Asok = window.Asok || {};
    window.Asok.wsStates = window.Asok.wsStates || {};

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
            document.getElementById('tab-' + tabName).classList.add('active');
            if (tabName === 'reactive') refreshReactive();
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

    // Refresh Reactive States
    const refreshReactive = () => {
        const clientStates = document.querySelectorAll('[asok-state]');
        const wsComponents = Object.keys(window.Asok.wsStates);
        if (reactiveCount) reactiveCount.textContent = clientStates.length + wsComponents.length;

        if (!document.getElementById('tab-reactive').classList.contains('active')) return;

        let html = '';

        if (clientStates.length) {
            html += '<div class="asok-section-header">Client States (Directives)</div>';
            clientStates.forEach((el, i) => {
                const ctx = window.AsokDirectives?.w?.get(el);
                if (ctx && ctx.state) {
                    html += `<div class="asok-reactive-card">
                        <div class="asok-reactive-header">
                            <div class="asok-reactive-dot"></div>
                            <div class="asok-reactive-name">&lt;${el.tagName.toLowerCase()}&gt; #${i + 1}</div>
                        </div>
                        <div class="asok-reactive-body">${JSON.stringify(ctx.state, null, 2)}</div>
                    </div>`;
                }
            });
        }

        if (wsComponents.length) {
            html += '<div class="asok-section-header" style="margin-top:8px">Live Components (WS)</div>';
            wsComponents.forEach(cid => {
                const entry = window.Asok.wsStates[cid];
                html += `<div class="asok-reactive-card">
                    <div class="asok-reactive-header">
                        <div class="asok-reactive-dot"></div>
                        <div class="asok-reactive-name">${entry.name} <span style="color:var(--fg-3);font-size:10px">#${cid}</span></div>
                    </div>
                    <div class="asok-reactive-body">${JSON.stringify(entry.state, null, 2)}</div>
                </div>`;
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

    // Listeners
    setInterval(refreshReactive, 1000);

    document.addEventListener('asok:success', (e) => {
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
        updateTemplates();
        refreshReactive();
    });

    // Init
    updateTemplates();
    refreshReactive();
})();
