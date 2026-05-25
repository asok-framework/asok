/**
 * Asok API Documentation - Interactive Client
 * Handles theme switching, request execution, and CSRF token management
 */

// Global state (initialized from template)
let spec;
let csrfToken;

/**
 * Initialize the API docs with spec and CSRF token
 * Called from the template after data injection
 */
function initApiDocs(specData, token) {
    spec = specData;
    csrfToken = token;
    initTheme();
    initNavigation();
}

// ============================================================================
// Theme Management
// ============================================================================

function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('asok-api-theme', theme);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    setTheme(current === 'dark' ? 'light' : 'dark');
}

function initTheme() {
    const savedTheme = localStorage.getItem('asok-api-theme') ||
                      (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    setTheme(savedTheme);
}

// ============================================================================
// Notifications (Toasts)
// ============================================================================

function showToast(message, type = 'success') {
    const zone = document.getElementById('flash-zone');
    const toast = document.createElement('div');
    toast.className = `flash-msg ${type}`;
    toast.innerHTML = `<span>${message}</span>`;
    zone.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        setTimeout(() => toast.remove(), 400);
    }, 3000);
}

// ============================================================================
// Accordion UX
// ============================================================================

function toggleOp(header) {
    const card = header.parentElement;
    const isOpen = card.classList.contains('is-open');
    document.querySelectorAll('.operation-card').forEach(c => c.classList.remove('is-open'));
    if (!isOpen) card.classList.add('is-open');
}

function toggleTry(btn, method, path) {
    const card = btn.closest('.operation-card');
    card.classList.toggle('is-trying');

    // Auto-fill with schema example
    if (card.classList.contains('is-trying')) {
        const textarea = card.querySelector('.try-payload');
        if (textarea && !textarea.value) {
            const schemaName = textarea.dataset.schema;
            let example = {};

            if (schemaName && spec.components.schemas[schemaName]) {
                const schema = spec.components.schemas[schemaName];
                for (const [key, prop] of Object.entries(schema.properties)) {
                    // Skip system-generated fields in the example for request bodies
                    if (['id', 'created_at', 'updated_at', 'slug'].includes(key.toLowerCase())) continue;
                    example[key] = prop.type === 'integer' ? 0 : (prop.type === 'boolean' ? false : "");
                }
            }

            // Add path and query parameters
            const op = spec.paths[path] && spec.paths[path][method.toLowerCase()];
            if (op && op.parameters) {
                op.parameters.forEach(p => {
                    if (!(p.name in example)) {
                        example[p.name] = p.schema && p.schema.type === 'integer' ? 0 : "";
                    }
                });
            } else {
                // Fallback: Look for path parameters directly in the path pattern
                const matches = path.match(/\{.*?\}/g);
                if (matches) {
                    matches.forEach(m => {
                        const name = m.replace('{', '').replace('}', '').split(':')[0];
                        if (!(name in example)) example[name] = "";
                    });
                }
            }

            if (Object.keys(example).length > 0) {
                textarea.value = JSON.stringify(example, null, 2);
            } else {
                textarea.value = "";
            }
        }
    }
}

// ============================================================================
// Request Execution
// ============================================================================

async function executeTry(btn, method, pathPattern) {
    const card = btn.closest('.operation-card');
    const resArea = card.querySelector('.response-area');
    const resCode = card.querySelector('.status-code');
    const resTime = card.querySelector('.res-time');
    const resContent = card.querySelector('.res-content');
    const payloadArea = card.querySelector('.try-payload');

    resArea.classList.add('has-content');
    resContent.textContent = 'Loading...';

    const start = performance.now();
    let finalUrl = pathPattern;
    const queryParams = new URLSearchParams();
    const headers = {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken
    };
    const options = { method, headers };

    let payload = {};
    if (payloadArea && payloadArea.value.trim()) {
        try {
            payload = JSON.parse(payloadArea.value);
        } catch (e) {
            showToast('Invalid JSON in input field', 'error');
            return;
        }
    }

    // Distribute payload keys: Path -> Query/Body
    for (let [key, value] of Object.entries(payload)) {
        // Try to match the key directly or as 'id' matching any '{*_id}' or '{id}'
        const patterns = [`\\{${key}(?::.*?)?\\}`];
        if (key.toLowerCase() === 'id') patterns.push('\\{.*?_id(?::.*?)?\\}');

        let replaced = false;
        for (const p of patterns) {
            const regex = new RegExp(p, 'g');
            if (finalUrl.match(regex)) {
                finalUrl = finalUrl.replace(regex, encodeURIComponent(value));
                replaced = true;
                break;
            }
        }

        if (!replaced && ['GET', 'DELETE'].includes(method)) {
            queryParams.append(key, value);
        }
    }

    // Append remaining payload as body for POST/PUT/PATCH
    if (['POST', 'PUT', 'PATCH'].includes(method)) {
        // Filter out keys already used in path substitution
        const body = {};
        for (const [key, value] of Object.entries(payload)) {
            if (!pathPattern.includes(`{${key}}`)) {
                body[key] = value;
            }
        }
        if (Object.keys(body).length > 0) {
            options.body = JSON.stringify(body);
        }
    }

    const queryStr = queryParams.toString();
    const url = finalUrl + (queryStr ? '?' + queryStr : '');

    try {
        const response = await fetch(url, options);
        const end = performance.now();

        // SECURITY: Update CSRF token from response header for subsequent requests
        const newToken = response.headers.get('X-CSRF-Token');
        if (newToken) {
            csrfToken = newToken;
            console.log('[CSRF] Token refreshed from server response');
        }

        resCode.textContent = response.status;
        resCode.className = 'status-code status-badge ' + (response.status < 400 ? 'success' : 'error');
        resTime.textContent = `${Math.round(end - start)}ms`;

        const data = await response.json().catch(() => null);
        if (data) {
            resContent.innerHTML = syntaxHighlight(JSON.stringify(data, null, 2));
        } else {
            resContent.textContent = '(Empty Response)';
        }

        if (response.ok) {
            showToast('Request completed successfully');
        } else {
            showToast(`Request failed with status ${response.status}`, 'error');
        }
    } catch (err) {
        resCode.textContent = 'Error';
        resCode.className = 'status-code status-badge error';
        resContent.textContent = err.message;
        showToast('Request failed', 'error');
    }
}

// ============================================================================
// Utilities
// ============================================================================

function syntaxHighlight(json) {
    json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
        let cls = 'json-number';
        if (/^"/.test(match)) {
            if (/:$/.test(match)) cls = 'json-key';
            else cls = 'json-string';
        } else if (/true|false/.test(match)) {
            cls = 'json-boolean';
        } else if (/null/.test(match)) {
            cls = 'json-null';
        }
        return '<span class="' + cls + '">' + match + '</span>';
    });
}

function copyResponse(btn) {
    const wrap = btn.closest('.res-body-wrap');
    const code = wrap.querySelector('.res-content').textContent;
    navigator.clipboard.writeText(code).then(() => {
        showToast('Response copied to clipboard!');
    });
}

function openSection(id) {
    const el = document.getElementById(id);
    if (el && el.classList.contains('operation-card')) {
        document.querySelectorAll('.operation-card').forEach(c => c.classList.remove('is-open'));
        el.classList.add('is-open');
    }
}

function copyCurl(method, path) {
    const url = window.location.origin + path;
    const curl = `curl -X ${method} "${url}"`;
    navigator.clipboard.writeText(curl).then(() => {
        showToast('cURL command copied to clipboard!');
    });
}

// ============================================================================
// Navigation Initialization
// ============================================================================

function initNavigation() {
    document.addEventListener('DOMContentLoaded', () => {
        if (window.location.hash) {
            const id = window.location.hash.substring(1);
            openSection(id);
        } else {
            const firstCard = document.querySelector('.operation-card');
            if (firstCard) firstCard.classList.add('is-open');
        }
    });
}
