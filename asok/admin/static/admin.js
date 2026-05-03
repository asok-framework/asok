/**
 * ASOK Reactive Runtime v0.1.2
 * - Full implementation of the Asok SPA spec
 * - Event-driven, attribute-based reactivity
 * - Support for OOB swaps, SSE, and complex triggers
 * - Smart Extraction: Prevents Nested Shell bug by extracting target from full HTML
 */
(function() {
    'use strict';

    const X_BLOCK = 'X-Block';
    const X_CSRF  = 'X-CSRF-Token';

    /**
     * UTILITY: Custom Premium Modal
     */
    async function showAlertModal(title, message, confirmText, cancelText) {
        title = title || window.ASOK_I18N?.confirmation || 'Confirmation';
        confirmText = confirmText || window.ASOK_I18N?.confirm || 'Confirm';
        cancelText = cancelText || window.ASOK_I18N?.cancel || 'Cancel';

        const modal = document.getElementById('asok-modal');
        if (!modal) return confirm(message); // Fallback

        const titleEl = document.getElementById('modal-title');
        const bodyEl  = document.getElementById('modal-body');
        const confBtn = document.getElementById('modal-confirm');
        const cancBtn = document.getElementById('modal-cancel');

        titleEl.textContent = title;
        bodyEl.textContent  = message;
        confBtn.textContent = confirmText;
        cancBtn.textContent = cancelText;

        modal.classList.add('is-visible');

        return new Promise((resolve) => {
            const cleanup = (val) => {
                modal.classList.remove('is-visible');
                confBtn.removeEventListener('click', onConfirm);
                cancBtn.removeEventListener('click', onCancel);
                modal.removeEventListener('click', onOverlay);
                resolve(val);
            };
            const onConfirm = () => cleanup(true);
            const onCancel  = () => cleanup(false);
            const onOverlay = (e) => { if (e.target === modal) cleanup(false); };

            confBtn.addEventListener('click', onConfirm);
            cancBtn.addEventListener('click', onCancel);
            modal.addEventListener('click', onOverlay);
        });
    }

    /**
     * UTILITY: Flash Toast
     */
    function dismissFlash(msg, delay = 0) {
        setTimeout(() => {
            msg.style.opacity = '0';
            msg.style.transform = 'translateY(-10px)';
            msg.style.transition = 'all 0.4s cubic-bezier(0.16, 1, 0.3, 1)';
            setTimeout(() => msg.remove(), 400);
        }, delay);
    }

    window.flash = function(type, message, ttl = 6000) {
        const zone = document.getElementById('flash-zone');
        if (!zone) return;

        const msg = document.createElement('div');
        msg.className = `flash-msg ${type}`;
        msg.setAttribute('data-ttl', ttl);

        let iconSvg = '';
        if (type === 'success') iconSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
        else if (type === 'error') iconSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
        else iconSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';

        msg.innerHTML = `${iconSvg}<span>${message}</span>`;
        if (zone.firstChild) zone.insertBefore(msg, zone.firstChild);
        else zone.appendChild(msg);
        dismissFlash(msg, ttl);
    };

    /**
     * CORE: Fetch & Swap
     */
    async function performAction(el, config = {}) {
        const url      = config.url    || el.getAttribute('data-spa-search') || el.getAttribute('data-url')    || (el.tagName === 'A' ? el.href : (el.tagName === 'FORM' ? el.getAttribute('action') : (el.form ? el.form.getAttribute('action') : location.href)));
        const method   = config.method || el.getAttribute('data-method') || (el.tagName === 'FORM' ? el.getAttribute('method') : 'GET');
        const target   = config.target || el.getAttribute('data-target') || el.getAttribute('data-block');
        const swapMode = el.getAttribute('data-swap') || 'innerHTML';
        const confirmMsg = el.getAttribute('data-confirm');

        if (confirmMsg) {
            const confirmed = await showAlertModal(null, confirmMsg);
            if (!confirmed) return;
        }


        // Loading states
        const indicator = el.getAttribute('data-indicator') === '' ? el : document.querySelector(el.getAttribute('data-indicator'));
        if (indicator) indicator.classList.add('is-loading');

        const disable = el.hasAttribute('data-disable');
        const disableNodes = disable ? (el.tagName === 'FORM' ? el.querySelectorAll('button, input[type="submit"]') : [el]) : [];
        disableNodes.forEach(n => n.disabled = true);

        // Data collection
        let finalUrl = url;
        const formData = new FormData();

        // Include current element if it's an input
        if (el.name && el.value !== undefined && el.tagName !== 'FORM') {
            formData.append(el.name, el.value);
        } else if (el.tagName === 'FORM') {
            new FormData(el).forEach((v, k) => formData.append(k, v));
            if (config.submitterName) {
                formData.append(config.submitterName, config.submitterValue || '1');
            }
        } else if (el.form) {
            new FormData(el.form).forEach((v, k) => formData.append(k, v));
        }

        // data-include
        const include = el.getAttribute('data-include');
        if (include) {
            document.querySelectorAll(include).forEach(inc => {
                if (inc.name) formData.append(inc.name, inc.value);
                else if (inc.tagName === 'FORM') new FormData(inc).forEach((v, k) => formData.append(k, v));
            });
        }

        const headers = {
            'X-Requested-With': 'XMLHttpRequest',
            [X_CSRF]: document.querySelector('meta[name="csrf-token"]')?.content || ''
        };

        if (target) headers[X_BLOCK] = target.replace('#', '');

        const fetchOptions = { method, headers };
        if (method.toUpperCase() === 'POST') {
            fetchOptions.body = formData;
        } else {
            const params = new URLSearchParams(formData);
            const sep = finalUrl.includes('?') ? '&' : '?';
            const qs = params.toString();
            if (qs) finalUrl += sep + qs;
        }

        try {
            const response = await fetch(finalUrl, fetchOptions);
            const html = await response.text();
            
            // If we redirected, ensure we update the URL
            const shouldPush = el.hasAttribute('data-push-url') || el.hasAttribute('data-spa') || (target && target.includes('#page-body'));

            if (response.redirected || (shouldPush && el.getAttribute('data-push-url') !== 'false')) {
                const pushVal = el.getAttribute('data-push-url');
                let nextUrl = (pushVal && pushVal !== 'true') ? pushVal : (response.redirected ? response.url : finalUrl);
                history.pushState({ target, swapMode }, '', nextUrl);
            }

            // Force target to #page-body on redirects if no templates found
            const finalTarget = (response.redirected && !html.includes('data-block=')) ? '#page-body' : target;
            processResponse(html, finalTarget, swapMode);
        } catch (err) {
            console.error('[Asok] Action failed:', err);
        } finally {
            if (indicator) indicator.classList.remove('is-loading');
            disableNodes.forEach(n => n.disabled = false);
        }
    }

    function processResponse(html, defaultTarget, defaultSwap) {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const templates = doc.querySelectorAll('template[data-block]');
        let primaryHandled = false;
        
        // 1. Out-of-band swaps
        if (templates.length > 0) {
            templates.forEach(tpl => {
                const targetSel = tpl.getAttribute('data-block');
                if (targetSel === defaultTarget) primaryHandled = true;

                // Only inherit defaultSwap if it's the primary target, otherwise default to innerHTML
                const isPrimary = (targetSel === defaultTarget);
                const swapMode  = tpl.getAttribute('data-swap') || (isPrimary ? (defaultSwap || 'innerHTML') : 'innerHTML');
                
                applySwap(targetSel, tpl.innerHTML, swapMode);
            });
        }

        // 2. Primary Target fallback / Smart Extraction
        if (!primaryHandled && defaultTarget) {
            let finalContent = html;
            const targetId = defaultTarget.startsWith('#') ? defaultTarget.slice(1) : null;
            
            // If response is a full page, find the target ID within it
            if (targetId && (html.toLowerCase().includes('<body') || html.toLowerCase().includes('<html'))) {
                const inner = doc.getElementById(targetId);
                if (inner) {
                    finalContent = (defaultSwap === 'outerHTML') ? inner.outerHTML : inner.innerHTML;
                }
            }
            
            applySwap(defaultTarget, finalContent, defaultSwap);
            if (defaultTarget === '#page-body') window.scrollTo({top: 0, behavior: 'smooth'});
        } else if (!primaryHandled && !defaultTarget) {
            // 3. Global Fallback: Entire Body
            const body = doc.querySelector('#page-body');
            if (body) {
                applySwap('#page-body', body.innerHTML, 'innerHTML');
                window.scrollTo(0, 0);
            }
        }
        
        // Refresh CSRF meta
        const newToken = doc.querySelector('meta[name="csrf-token"]')?.content;
        if (newToken) {
           const meta = document.querySelector('meta[name="csrf-token"]');
           if (meta) meta.content = newToken;
        }

        // Synchronize Sidebar Active State
        const newNav = doc.querySelector('.admin-nav');
        if (newNav) {
            const currentNav = document.querySelector('.admin-nav');
            if (currentNav) currentNav.innerHTML = newNav.innerHTML;
        }

        // Re-init newly added content
        initElements(document.body);
        updateBulkBar();

        // Auto-close sidebar on mobile after navigation
        document.querySelector('.admin-sidebar')?.classList.remove('is-open');
        document.querySelector('.sidebar-overlay')?.classList.remove('is-visible');

        // Global lifecycle hook
        window.dispatchEvent(new CustomEvent('asok:load'));
    }

    function applySwap(selector, html, mode) {
        const target = document.querySelector(selector);
        if (!target) return;

        // Trigger premium page transition for main content area
        if (selector === '#page-body' || selector === 'main') {
            target.classList.remove('page-transition');
            void target.offsetWidth; // Refresh reflow to restart animation
            target.classList.add('page-transition');
        }

        switch (mode) {
            case 'outerHTML':   target.outerHTML = html; break;
            case 'beforebegin': target.insertAdjacentHTML('beforebegin', html); break;
            case 'afterbegin':  target.insertAdjacentHTML('afterbegin', html); break;
            case 'beforeend':   target.insertAdjacentHTML('beforeend', html); break;
            case 'afterend':    target.insertAdjacentHTML('afterend', html); break;
            case 'delete':      target.remove(); break;
            case 'none':        break;
            default:            target.innerHTML = html; break;
        }
    }

    /**
     * INITIALIZATION
     */
    const timers = new Map();

    function initCustomSelects(root) {
        root.querySelectorAll('select').forEach(select => {
            if (select.dataset.customInit || select.style.display === 'none') return;
            select.dataset.customInit = 'true';
            select.style.display = 'none';

            const wrap = document.createElement('div');
            wrap.className = 'custom-select-wrap';
            
            const btnId = 'sel-' + Math.random().toString(36).substr(2, 9);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn btn-default custom-select-btn';
            btn.style.height = '42px'; // Match standard input height
            btn.style.padding = '0 12px';
            btn.setAttribute('data-toggle', btnId);
            btn.setAttribute('aria-expanded', 'false');
            
            const label = document.createElement('span');
            label.className = 'custom-select-label';
            const selectedOpt = select.options[select.selectedIndex];
            label.textContent = selectedOpt ? selectedOpt.text : '—';
            
            btn.appendChild(label);
            btn.insertAdjacentHTML('beforeend', `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="muted dd-chevron"><polyline points="6 9 12 15 18 9"/></svg>`);
            
            const menu = document.createElement('div');
            menu.className = 'custom-select-options card';
            menu.id = btnId;
            menu.setAttribute('data-dropdown', '');
            menu.setAttribute('hidden', '');
            menu.style.position = 'absolute';
            menu.style.top = '100%';
            menu.style.left = '0';
            menu.style.width = '100%';
            menu.style.marginTop = '4px';
            menu.style.zIndex = '100';
            menu.style.maxHeight = '300px';
            menu.style.overflowY = 'auto';

            Array.from(select.options).forEach((opt, idx) => {
                const item = document.createElement('div');
                item.className = 'custom-opt' + (opt.selected ? ' is-active' : '');
                item.style.cursor = 'pointer';
                item.textContent = opt.text;
                
                if (opt.selected) {
                    item.insertAdjacentHTML('beforeend', `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto"><polyline points="20 6 9 17 4 12"/></svg>`);
                }

                item.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    select.value = opt.value;
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                    label.textContent = opt.text;
                    
                    // Update active states
                    menu.querySelectorAll('.custom-opt').forEach(o => {
                        o.classList.remove('is-active');
                        const svg = o.querySelector('svg');
                        if (svg) svg.remove();
                    });
                    item.classList.add('is-active');
                    item.insertAdjacentHTML('beforeend', `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto"><polyline points="20 6 9 17 4 12"/></svg>`);
                    
                    // Close menu
                    menu.setAttribute('hidden', '');
                    btn.setAttribute('aria-expanded', 'false');
                });
                
                menu.appendChild(item);
            });

            wrap.appendChild(btn);
            wrap.appendChild(menu);
            select.parentNode.insertBefore(wrap, select.nextSibling);
        });
    }

    function initElements(root) {
        const selector = '[data-block], [data-sse], [data-spa], [data-trigger], [data-url], [data-spa-search], [data-indicator], [data-target]';
        root.querySelectorAll(selector).forEach(el => {
            if (el.dataset.asokInit) return;
            el.dataset.asokInit = 'true';

            // 1. SSE Support
            if (el.hasAttribute('data-sse')) {
                const url = el.getAttribute('data-sse');
                const source = new EventSource(url);
                source.onmessage = (e) => {
                    const block = el.getAttribute('data-block') || el.getAttribute('data-target');
                    const target = block || '#' + el.id;
                    const swap = el.getAttribute('data-swap') || 'innerHTML';
                    processResponse(e.data, target, swap);
                };
                el._asokSSE = source;
            }

            // 2. SPA Navigation
            if (el.hasAttribute('data-spa')) {
                el.addEventListener('click', (e) => {
                    e.preventDefault();
                    performAction(el, { url: el.href, method: 'GET', target: '#page-body' });
                });
            }

            // 3. Triggers (including Search)
            const triggerAttr = el.getAttribute('data-trigger');
            if (triggerAttr) {
                if (triggerAttr === 'load') {
                    performAction(el);
                } else if (triggerAttr.startsWith('every ')) {
                    const match = triggerAttr.match(/every (\d+)(s|ms)/);
                    if (match) {
                        const val = parseInt(match[1]);
                        const unit = match[2] === 's' ? 1000 : 1;
                        const interval = setInterval(() => performAction(el), val * unit);
                        timers.set(el, interval);
                    }
                } else {
                    const [event, ...opts] = triggerAttr.split(' ');
                    const delayOpt = opts.find(o => o.startsWith('delay:'));
                    let timeout = null;

                    el.addEventListener(event, (e) => {
                        // Skip local click handler for setters to let the global handler (line 401)
                        // update the value first and then trigger the action.
                        if (event === 'click' && el.hasAttribute('data-set-value')) return;

                        if (event === 'submit') e.preventDefault();

                        if (delayOpt) {
                            const ms = parseInt(delayOpt.split(':')[1]);
                            clearTimeout(timeout);
                            timeout = setTimeout(() => performAction(el), ms);
                        } else {
                            performAction(el);
                        }
                    });
                }
            } else if (el.tagName === 'FORM' && (el.hasAttribute('data-block') || el.hasAttribute('data-target') || el.hasAttribute('data-spa'))) {
                el.addEventListener('submit', (e) => {
                    e.preventDefault();
                    const overrides = {};
                    if (e.submitter && e.submitter.name) {
                        overrides.submitterName = e.submitter.name;
                        overrides.submitterValue = e.submitter.value;
                    }
                    performAction(el, overrides);
                });
            } else if (el.tagName === 'A' && (el.hasAttribute('data-block') || el.hasAttribute('data-target'))) {
                el.addEventListener('click', (e) => {
                    e.preventDefault();
                    performAction(el);
                });
            }
        });

        // 4. WYSIWYG Editors (Quill)
        root.querySelectorAll('.wysiwyg-container').forEach(container => {
            if (container.dataset.asokInit) return;
            container.dataset.asokInit = 'true';

            const editorEl = container.querySelector('.wysiwyg-editor');
            const textarea = container.querySelector('textarea');
            if (!editorEl || !textarea) return;

            if (typeof Quill === 'undefined') {
                console.error('[Asok] Quill library not found. Rich text editor disabled.');
                return;
            }

            const quill = new Quill(editorEl, {
                theme: 'snow',
                modules: {
                    toolbar: [
                        [{ 'header': [1, 2, 3, false] }],
                        ['bold', 'italic', 'underline', 'strike'],
                        ['link', 'blockquote'],
                        [{ 'list': 'ordered'}, { 'list': 'bullet' }],
                        ['clean']
                    ]
                }
            });

            // Sync HTML to hidden textarea
            quill.on('text-change', () => {
                const html = quill.root.innerHTML;
                textarea.value = html === '<p><br></p>' ? '' : html;
            });
        });

        // 4. M2M Checkbox Selection
        root.querySelectorAll('[data-m2m]').forEach(container => {
            const name = container.dataset.m2m;
            const hidden = container.closest('form')?.querySelector(`input[name="m2m_${name}"]`);
            if (!hidden) return;

            const updateHidden = () => {
                const checked = Array.from(container.querySelectorAll('input[type="checkbox"]:checked'))
                    .map(cb => cb.value);
                hidden.value = checked.join(',');
            };

            container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                cb.addEventListener('change', updateHidden);
            });

            // Initial sync
            updateHidden();
        });

        // 5. Permission Matrix Handling
        const permInput = document.getElementById('perm-input');
        const permWildcard = document.getElementById('perm-wildcard');

        if (permInput && permWildcard) {
            const updatePermissions = () => {
                if (permWildcard.checked) {
                    permInput.value = '*';
                    // Disable all individual checkboxes when wildcard is active
                    document.querySelectorAll('.perm-cb, .perm-row-all').forEach(cb => {
                        cb.disabled = true;
                        cb.checked = true;
                    });
                } else {
                    // Re-enable checkboxes
                    document.querySelectorAll('.perm-cb, .perm-row-all').forEach(cb => {
                        cb.disabled = false;
                    });

                    // Collect individual permissions
                    const perms = Array.from(document.querySelectorAll('.perm-cb:checked'))
                        .map(cb => cb.dataset.perm)
                        .filter(p => p);
                    permInput.value = perms.join(',');
                }
            };

            // Wildcard checkbox
            permWildcard.addEventListener('change', updatePermissions);

            // Individual permission checkboxes
            document.querySelectorAll('.perm-cb').forEach(cb => {
                cb.addEventListener('change', updatePermissions);
            });

            // Row "select all" checkboxes
            document.querySelectorAll('.perm-row-all').forEach(rowCb => {
                rowCb.addEventListener('change', (e) => {
                    const slug = rowCb.dataset.slug;
                    const checked = rowCb.checked;
                    document.querySelectorAll(`.perm-cb[data-perm^="${slug}."]`).forEach(cb => {
                        cb.checked = checked;
                    });
                    updatePermissions();
                });
            });

            // Initial sync
            updatePermissions();
        }

        // Sidebar Search Filtering
        const navSearch = document.getElementById('nav-search');
        if (navSearch) {
            navSearch.addEventListener('input', (e) => {
                const q = e.target.value.toLowerCase();
                document.querySelectorAll('.nav-section').forEach(section => {
                    let hasMatch = false;
                    section.querySelectorAll('.nav-item').forEach(item => {
                        const text = item.querySelector('span')?.textContent.toLowerCase() || "";
                        const match = text.includes(q);
                        item.style.display = match ? 'flex' : 'none';
                        if (match) hasMatch = true;
                    });
                    section.style.display = hasMatch ? 'block' : 'none';
                    if (q && hasMatch) section.setAttribute('open', '');
                });
            });
        }

        // Flash dismiss
        root.querySelectorAll('.flash-msg[data-ttl]').forEach(msg => {
            const ttl = parseInt(msg.getAttribute('data-ttl')) || 6000;
            dismissFlash(msg, ttl);
        });

        // 5. Custom Select Dropdowns
        initCustomSelects(root);
    }

    /**
     * BULK ACTIONS
     */
    function updateBulkBar() {
        const bar = document.getElementById('bulk-bar');
        if (!bar) return;
        const checked = document.querySelectorAll('.row-check:checked');
        const ids = Array.from(checked).map(c => c.value);
        if (ids.length > 0) {
            bar.classList.add('is-visible');
            const countEl = document.getElementById('bulk-count');
            const label = countEl.dataset.labelSelected || 'selected';
            countEl.textContent = ids.length + ' ' + label;
            document.querySelectorAll('.bulk-ids-field').forEach(f => f.value = ids.join(','));
        } else {
            bar.classList.remove('is-visible');
        }
    }

    document.addEventListener('change', (e) => {
        if (e.target.id === 'check-all') {
            document.querySelectorAll('.row-check').forEach(c => c.checked = e.target.checked);
            updateBulkBar();
        } else if (e.target.classList.contains('row-check')) {
            const master = document.getElementById('check-all');
            if (master) {
                const all = document.querySelectorAll('.row-check');
                master.checked = Array.from(all).every(c => c.checked);
            }
            updateBulkBar();
        }
    });

    /**
     * INTERACTION HANDLER
     */
    document.addEventListener('click', function (e) {
        // 1. Dropdown Toggles
        const toggleBtn = e.target.closest('[data-toggle]');
        if (toggleBtn) {
            e.preventDefault();
            e.stopPropagation();
            const targetId = toggleBtn.getAttribute('data-toggle');
            const target = document.getElementById(targetId);
            if (target) {
                const isHidden = target.hasAttribute('hidden');
                document.querySelectorAll('[data-dropdown]').forEach(d => {
                    if (d.id !== targetId && !d.contains(toggleBtn)) {
                        d.setAttribute('hidden', '');
                        const otherBtn = document.querySelector(`[data-toggle="${d.id}"]`);
                        if (otherBtn) otherBtn.setAttribute('aria-expanded', 'false');
                    }
                });
                if (isHidden) {
                    target.removeAttribute('hidden');
                    toggleBtn.setAttribute('aria-expanded', 'true');
                } else {
                    target.setAttribute('hidden', '');
                    toggleBtn.setAttribute('aria-expanded', 'false');
                }
            }
            return;
        }

        // 2. Set Value Utility
        const setter = e.target.closest('[data-set-value]');
        if (setter) {
            const name = setter.getAttribute('data-set-name');
            const val = setter.getAttribute('data-set-value');
            document.querySelectorAll(`input[name="${name}"]`).forEach(input => {
                input.value = val;
                const container = setter.closest('.filter-item');
                if (container) {
                    const label = container.querySelector('.custom-select-label');
                    if (label) label.textContent = setter.textContent.trim();
                }
            });
            if (setter.hasAttribute('data-trigger')) performAction(setter);
            
            // Close the inner dropdown after selection
            const parentDropdown = setter.closest('.custom-select-options');
            if (parentDropdown) {
                parentDropdown.setAttribute('hidden', '');
                const parentBtn = document.querySelector(`[data-toggle="${parentDropdown.id}"]`);
                if (parentBtn) parentBtn.setAttribute('aria-expanded', 'false');
            }
            
            return;
        }

        // 3. Global Close
        document.querySelectorAll('[data-dropdown]').forEach(d => {
            if (!d.contains(e.target)) {
                d.setAttribute('hidden', '');
                const b = document.querySelector(`[data-toggle="${d.id}"]`);
                if (b) b.setAttribute('aria-expanded', 'false');
            }
        });
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') document.querySelectorAll('[data-dropdown]').forEach(d => d.setAttribute('hidden', ''));
    });

    window.addEventListener('popstate', (e) => {
        if (e.state && (e.state.target || e.state.block)) {
            performAction(document.body, { url: location.href, method: 'GET', target: e.state.target || e.state.block });
        } else {
            location.reload();
        }
    });

    function initTheme() {
        const saved = localStorage.getItem('asok-theme') || 'light';
        document.documentElement.setAttribute('data-theme', saved);
    }

    document.addEventListener('click', (e) => {
        const btn = e.target.closest('#theme-toggle');
        if (!btn) return;
        const current = document.documentElement.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('asok-theme', next);
    });

    function initMobileNav() {
        const sidebar = document.querySelector('.admin-sidebar');
        const toggle = document.querySelector('#sidebar-toggle');
        if (!sidebar || !toggle) return;

        // Ensure overlay exists
        let overlay = document.querySelector('.sidebar-overlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.className = 'sidebar-overlay';
            document.body.appendChild(overlay);
        }

        const closeNav = () => {
            sidebar.classList.remove('is-open');
            overlay.classList.remove('is-visible');
        };

        toggle.addEventListener('click', () => {
            sidebar.classList.toggle('is-open');
            overlay.classList.toggle('is-visible');
        });

        overlay.addEventListener('click', closeNav);
    }

    initTheme();
    document.addEventListener('DOMContentLoaded', () => {
        initElements(document.body);
        initMobileNav();
    });

})();
