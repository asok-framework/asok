/*
*  Asok SPA - Single Page Application support for Asok framework
*  author: Asok Team
*  license: MIT
*  version: 2.0.0
*/
; (function () {
  window.Asok = window.Asok || {};

  // In-memory cache for prefetch content
  const responseCache = {};
  const cacheKeys = [];
  const MAX_CACHE = 100;

  window.__asokClearCache = function () {
    Object.keys(responseCache).forEach(key => delete responseCache[key]);
    cacheKeys.length = 0;
  };

  function addCache(key, value) {
    if (cacheKeys.length >= MAX_CACHE) {
      const oldestKey = cacheKeys.shift();
      delete responseCache[oldestKey];
    }
    responseCache[key] = value;
    cacheKeys.push(key);
  }

  function getAttr(el, name) {
    if (!el || !el.getAttribute) return null;
    let val = el.getAttribute('asok-' + name);
    if (val !== null) return val;
    val = el.getAttribute('data-' + name);
    if (val !== null) return val;
    val = el.getAttribute('data-asok-' + name);
    if (val !== null) return val;
    return null;
  }

  function hasAttr(el, name) {
    if (!el || !el.hasAttribute) return false;
    return (
      el.hasAttribute('asok-' + name) ||
      el.hasAttribute('data-' + name) ||
      el.hasAttribute('data-asok-' + name)
    );
  }

  // Release per-element SPA resources (SSE connections, polling intervals) before
  // a node is removed from the DOM. Without this, swapping out content that holds
  // an asok-sse EventSource or an `every ...` trigger leaks the connection/timer,
  // which keeps firing on a detached node for the rest of the session.
  function cleanupSpaResources(node) {
    if (!node || node.nodeType !== 1) return;
    const release = function (el) {
      if (el.__asokSse) {
        try { el.__asokSse.close(); } catch (e) { }
        delete el.__asokSse;
        delete el.__asokSseSetup;
      }
      if (el.__asokEveryInterval) {
        clearInterval(el.__asokEveryInterval);
        delete el.__asokEveryInterval;
        delete el.__asokTriggerSetup;
      }
    };
    release(node);
    if (node.querySelectorAll) node.querySelectorAll('*').forEach(release);
  }

  // Retrieve the CSRF token from meta tag, inputs, cookies, or Asok state
  function getCsrfToken() {
    // Prefer the `asok_csrf` cookie: the server validates against it (double
    // submit), so it is the source of truth. A baked <meta>/<input> can be
    // stale on cached/SPA-fallback pages and cause spurious CSRF failures.
    const cookieMatch = document.cookie.match(/(?:^|; )(?:asok_csrf|csrf_token)=([^;]*)/);
    if (cookieMatch) return decodeURIComponent(cookieMatch[1]);
    const meta = document.querySelector('meta[name=csrf-token]');
    if (meta && meta.content) return meta.content;
    const input = document.querySelector('input[name=csrf_token]');
    if (input && input.value) return input.value;
    return (window.Asok && window.Asok.csrfToken) ? window.Asok.csrfToken : '';
  }

  function isSameOrigin(targetUrl) {
    try {
      return new URL(targetUrl, window.location.href).origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  // Find the target element by selector or block comment markers
  function findTargetElement(selector) {
    if (!selector) return null;
    let targetElement;

    // Check if the selector is a comment block marker (e.g. block name)
    if (/^[a-zA-Z0-9_-]+$/.test(selector)) {
      const it = document.createNodeIterator(document.body, NodeFilter.SHOW_COMMENT);
      let comment;
      while ((comment = it.nextNode())) {
        if (comment.textContent.trim() === 'block:' + selector + ':start') {
          targetElement = {
            _isBlockMarker: true,
            _blockName: selector,
            _startMarker: comment
          };
          break;
        }
      }
    }

    if (!targetElement) {
      try {
        targetElement = document.querySelector(selector);
      } catch (e) { }
    }

    if (!targetElement && /^[a-zA-Z0-9_-]+$/.test(selector)) {
      targetElement = document.getElementById(selector);
    }

    if (!targetElement && selector === 'title') {
      targetElement = document.querySelector('title');
    }

    if (!targetElement && selector === 'description') {
      targetElement = document.querySelector('meta[name=description]');
    }

    return targetElement;
  }

  function doSwap(target, html, mode, pushData) {
    if (mode === 'append') mode = 'beforeend';
    if (mode === 'prepend') mode = 'afterbegin';
    const realTarget = target._isBlockMarker ? target._startMarker.parentNode : target;

    const cleanNodes = function (nodes) {
      const targets = Array.isArray(nodes) ? nodes : [nodes];
      targets.forEach(function (t) {
        if (!t) return;
        cleanupSpaResources(t);
        if (t.querySelectorAll) {
          t.querySelectorAll('[data-asok-component]').forEach(function (el) {
            delete el.__asokWsReady;
            delete el.__asokIniting;
            if (window.Asok && window.Asok.leaveComponent) {
              window.Asok.leaveComponent(el.id.replace('asok-', ''));
            }
          });
        }
        if (t.dataset && t.dataset.asokComponent) {
          delete t.__asokWsReady;
          delete t.__asokIniting;
          if (window.Asok && window.Asok.leaveComponent) {
            window.Asok.leaveComponent(t.id.replace('asok-', ''));
          }
        }
        if (t.querySelectorAll && window.AsokDirectives && window.AsokDirectives.cleanupOld) {
          window.AsokDirectives.cleanupOld(t);
        }
      });
    };

    const afterSwap = function (insertedNodes) {
      const nodes = insertedNodes || [];

      // Execute newly injected scripts
      const scripts = [];
      nodes.forEach(function (node) {
        if (node.tagName === 'SCRIPT') {
          scripts.push(node);
        }
        if (node.querySelectorAll) {
          node.querySelectorAll('script').forEach(function (script) {
            scripts.push(script);
          });
        }
      });

      scripts.forEach(function (script) {
        if (script.dataset.run || script.id === 'asok-scoped-js') return;
        const newScript = document.createElement('script');
        const nonce = script.nonce || script.getAttribute('nonce') || '';
        if (nonce) newScript.nonce = nonce;
        if (script.src) newScript.src = script.src;
        newScript.textContent = script.textContent;
        newScript.dataset.run = '1';
        script.parentNode.replaceChild(newScript, script);
      });

      // Re-initialize directives and lifecycles.
      // window.Asok.init already chains AsokDirectives.init (see the init wrapping
      // in asok_directives.js / asok_spa.js), so calling both would run the
      // directive pass twice on every inserted node. Fall back to a direct
      // AsokDirectives.init only if the Asok.init chain isn't present.
      nodes.forEach(function (node) {
        if (!node.querySelectorAll) return;
        if (window.Asok && window.Asok.init) {
          window.Asok.init(node);
        } else if (window.AsokDirectives && window.AsokDirectives.init) {
          window.AsokDirectives.init(node);
        }
      });

      if (window.lucide && window.lucide.createIcons) {
        window.lucide.createIcons();
      }

      // Handle history state push and page transitions
      if (pushData && pushData.shouldPush) {
        const searchOverlay = document.getElementById('search-overlay');
        if (searchOverlay) searchOverlay.classList.remove('open');

        const mobileMenu = document.getElementById('mobile-menu');
        if (mobileMenu) mobileMenu.classList.add('hidden');

        document.body.style.overflow = '';

        const pushUrlAttr = pushData.src ? getAttr(pushData.src, 'push-url') : null;
        if (pushData.src && (pushUrlAttr !== null || (pushData.src.dataset && pushData.src.dataset.pushUrl !== undefined))) {
          const pushUrl = pushUrlAttr || pushData.src.dataset.pushUrl || pushData.url;
          history.pushState({ b: pushData.b, sel: pushData.sel, mode: mode, url: pushData.url }, '', pushUrl);
        }

        window.scrollTo({ top: 0, behavior: 'instant' });

        const pageContainer = document.querySelector('[data-asok-page-transition]');
        if (pageContainer) {
          const transitionAttr = pageContainer.getAttribute('data-asok-page-transition') || 'page';
          const parts = transitionAttr.split(' ');
          const type = parts[0];
          const duration = parseInt(parts[1]) || 300;

          pageContainer.classList.add('asok-' + type + '-in');
          requestAnimationFrame(() => {
            pageContainer.classList.add('is-entering');
            setTimeout(() => {
              pageContainer.classList.remove('asok-' + type + '-in', 'is-entering');
            }, duration);
          });
        }
      }

      const ev = new CustomEvent('asok:success', { detail: { target: realTarget, mode: mode } });
      document.dispatchEvent(ev);
    };

    if (target._isBlockMarker) {
      const startMarker = target._startMarker;
      const blockName = target._blockName;
      const it = document.createNodeIterator(document.body, NodeFilter.SHOW_COMMENT);
      let comment, endMarker = null;

      while ((comment = it.nextNode())) {
        if (comment === startMarker) {
          while ((comment = it.nextNode())) {
            if (comment.textContent.trim() === 'block:' + blockName + ':end') {
              endMarker = comment;
              break;
            }
          }
          break;
        }
      }

      if (!endMarker) return;

      const nodesToRemove = [];
      let sibling = startMarker.nextSibling;
      while (sibling && sibling !== endMarker) {
        nodesToRemove.push(sibling);
        sibling = sibling.nextSibling;
      }

      // Build new nodes from html (for all insert modes that need new content)
      const buildNewNodes = function (htmlStr) {
        const tempContainer = document.createElement('div');
        tempContainer.innerHTML = htmlStr;
        return Array.from(tempContainer.childNodes);
      };

      var insertedNodes;

      if (mode === 'delete') {
        // Delete mode: remove all nodes between markers, insert nothing
        cleanNodes(nodesToRemove);
        nodesToRemove.forEach(function (node) { node.remove(); });
        insertedNodes = [];
      } else if (mode === 'beforebegin') {
        // Insert before the start marker (outside the block)
        insertedNodes = buildNewNodes(html);
        if (startMarker.parentNode) {
          insertedNodes.forEach(function (node) {
            startMarker.parentNode.insertBefore(node, startMarker);
          });
        }
      } else if (mode === 'afterend') {
        // Insert after the end marker (outside the block)
        insertedNodes = buildNewNodes(html);
        if (endMarker.parentNode) {
          const afterEnd = endMarker.nextSibling;
          insertedNodes.forEach(function (node) {
            endMarker.parentNode.insertBefore(node, afterEnd);
          });
        }
      } else if (mode === 'afterbegin') {
        // Prepend inside block (before existing block content, after start marker)
        insertedNodes = buildNewNodes(html);
        const firstChild = startMarker.nextSibling === endMarker ? endMarker : startMarker.nextSibling;
        insertedNodes.forEach(function (node) {
          startMarker.parentNode.insertBefore(node, firstChild);
        });
      } else if (mode === 'beforeend') {
        // Append inside block (after existing block content, before end marker)
        insertedNodes = buildNewNodes(html);
        insertedNodes.forEach(function (node) {
          endMarker.parentNode.insertBefore(node, endMarker);
        });
      } else {
        // Default (innerHTML / outerHTML / replaceWith): replace block content
        cleanNodes(nodesToRemove);
        nodesToRemove.forEach(function (node) { node.remove(); });
        insertedNodes = buildNewNodes(html);
        insertedNodes.forEach(function (node) {
          startMarker.parentNode.insertBefore(node, endMarker);
        });
      }

      afterSwap(insertedNodes);

    } else if (target.tagName === 'META') {
      target.content = html;
      afterSwap([target]);
    } else {
      if (mode === 'innerHTML') {
        cleanNodes(Array.from(target.childNodes));
      } else if (mode === 'outerHTML' || mode === 'replaceWith' || mode === 'delete') {
        cleanNodes(target);
      }

      if (window.Asok && window.Asok.swap) {
        window.Asok.swap(target, html, mode, function (newNodes) {
          // For replace modes, target is detached — pass inserted nodes, not old target
          afterSwap(newNodes && newNodes.length ? newNodes : (mode === 'outerHTML' || mode === 'replaceWith' || mode === 'delete' ? [] : [target]));
        });
      } else {
        const safeHtml = html;

        if (mode === 'delete') {
          target.remove();
          afterSwap([]);
        } else if (mode === 'outerHTML' || mode === 'replaceWith') {
          // Build the fragment, then capture nodes AFTER transferring them into the DOM
          // so that afterSwap receives live, connected nodes (not detached fragment children).
          const tempDiv = document.createElement('div');
          tempDiv.innerHTML = safeHtml;
          const newNodes = Array.from(tempDiv.childNodes);
          // insertBefore each node from tempDiv before target, then remove target
          if (target.parentNode) {
            newNodes.forEach(function (node) {
              target.parentNode.insertBefore(node, target);
            });
            target.remove();
          } else {
            // target has no parent (detached) — best effort: use replaceWith
            const fragment = document.createRange().createContextualFragment(safeHtml);
            target.replaceWith(fragment);
          }
          // Update <title> if any of the new nodes is or contains a <title>
          newNodes.forEach(function (node) {
            if (node.tagName === 'TITLE') document.title = node.innerText || node.textContent;
          });
          afterSwap(newNodes);
        } else if (mode === 'innerHTML') {
          target.innerHTML = safeHtml;
          afterSwap(Array.from(target.childNodes));
        } else {
          const fragment = document.createRange().createContextualFragment(safeHtml);
          const newNodes = Array.from(fragment.childNodes);
          if (mode === 'beforebegin') {
            if (target.parentNode) {
              target.parentNode.insertBefore(fragment, target);
            }
          } else if (mode === 'afterbegin') {
            target.insertBefore(fragment, target.firstChild);
          } else if (mode === 'beforeend') {
            target.appendChild(fragment);
          } else if (mode === 'afterend') {
            if (target.parentNode) {
              target.parentNode.insertBefore(fragment, target.nextSibling);
            }
          } else {
            target.insertAdjacentHTML(mode, safeHtml);
          }
          afterSwap(newNodes);
        }
      }
      if (target.tagName === 'TITLE') {
        document.title = target.innerText;
      }
    }
  }


  // Fetch the page and perform swapping
  function performBlockSwap(url, blockName, selector, mode, options, sourceElement) {
    if (document.dispatchEvent(new CustomEvent('asok:before', { detail: { url: url, block: blockName } })) === false) {
      return;
    }

    const cleanBlockHeader = (blockName || '').split(',').map(b => b.trim().replace(/^#/, '')).join(',');
    const urlStr = typeof url === 'string'
      ? url
      : (url && typeof url.url === 'string' ? url.url : (url && typeof url.href === 'string' ? url.href : ''));
    const csrfToken = (urlStr && isSameOrigin(urlStr)) ? getCsrfToken() : '';
    const headers = Object.assign({ 'X-Block': cleanBlockHeader }, options.headers || {});
    if (csrfToken && !Object.keys(headers).some(h => h.toLowerCase() === 'x-csrf-token')) {
      headers['X-CSRF-Token'] = csrfToken;
    }
    options.headers = headers;
    options.credentials = 'same-origin';

    const cacheKey = url + blockName;
    const fetchPromise = responseCache[cacheKey]
      ? Promise.resolve(responseCache[cacheKey])
      : fetch(url, options).then(function (res) {
        if (!res.ok) {
          return res.text().then(function (text) {
            const ev = new CustomEvent('asok:error', { detail: { url: url, status: res.status, message: text } });
            document.dispatchEvent(ev);
            console.error((res.status === 400 ? 'Asok Consistency Error: ' : 'Asok Error ' + res.status + ': ') + text);
            throw text;
          });
        }

        const redirectUrl = res.headers.get('X-Asok-Redirect');
        if (redirectUrl) {
          const _isSafeRedirect = function (u) {
            if (!u || typeof u !== 'string') return false;
            if (window.AsokSecurity && window.AsokSecurity.isSafeUrl && !window.AsokSecurity.isSafeUrl(u)) {
              return false;
            }
            var clean = u.replace(/[\x00-\x1F\x7F]/g, '').trim();
            try { clean = decodeURIComponent(clean); } catch (_) {}
            if (
              clean.startsWith('//') ||
              clean.startsWith('\\\\') ||
              clean.startsWith('/\\') ||
              clean.startsWith('\\/')
            ) {
              return false;
            }
            return isSameOrigin(u);
          };
          if (!_isSafeRedirect(redirectUrl)) {
            console.error('[Asok] Blocked unsafe redirect URL:', redirectUrl);
            return Promise.reject('unsafe_redirect');
          }
          window.location.href = redirectUrl;
          return Promise.reject('redirected');
        }

        const token = res.headers.get('X-CSRF-Token');
        const blocks = res.headers.get('X-Asok-Blocks');

        if (token) {
          const csrfMeta = document.querySelector('meta[name=csrf-token]');
          if (csrfMeta) csrfMeta.content = token;
          document.querySelectorAll('input[name=csrf_token]').forEach(function (input) {
            input.value = token;
            input.defaultValue = token;
            input.setAttribute('value', token);
          });
        }

        if (blocks) {
          window.Asok.lastBlocks = blocks;
        }

        const sqlLog = res.headers.get('X-Asok-SQL-Log');
        if (sqlLog) {
          window.Asok.lastSqlLog = sqlLog;
        } else {
          window.Asok.lastSqlLog = null;
        }

        return res.text();
      });

    delete responseCache[cacheKey];

    return fetchPromise.then(function (html) {
      if (!html) return;
      const trimmedHtml = html.trimStart();

      if (trimmedHtml.startsWith('<!DOCTYPE') || trimmedHtml.startsWith('<html')) {
        if (typeof url === 'string' && isSameOrigin(url)) {
          window.location.href = url;
        }
        return;
      }

      const tempDiv = document.createElement('div');
      tempDiv.innerHTML = html;

      const templates = tempDiv.querySelectorAll('template[data-block], template[asok-block]');

      // Pre-execute "loose" scripts (outside block templates) ONLY on a
      // block-template swap. There, afterSwap only runs scripts inside the
      // inserted template content, so top-level scripts must run here.
      // For a full-HTML (non-template) swap, doSwap→afterSwap already inserts and
      // runs these scripts, so pre-executing them here would run them twice.
      if (templates.length) {
        tempDiv.querySelectorAll('script').forEach(function (script) {
          // #asok-scoped-js is (re)injected by the scoped-JS handler below.
          if (script.id === 'asok-scoped-js') return;
          let parent = script.parentNode;
          while (parent && parent !== tempDiv) {
            if (parent.tagName === 'TEMPLATE') return;
            parent = parent.parentNode;
          }
          const newScript = document.createElement('script');
          const nonce = script.nonce || script.getAttribute('nonce') || '';
          if (nonce) newScript.nonce = nonce;
          if (script.src) newScript.src = script.src;
          newScript.textContent = script.textContent;
          newScript.dataset.run = '1';
          script.dataset.run = '1';
          document.body.appendChild(newScript);
          newScript.remove();
        });
      }
      const pushUrlAttr = sourceElement ? getAttr(sourceElement, 'push-url') : null;
      const shouldPushUrl = (sourceElement && (pushUrlAttr !== null || (sourceElement.dataset && sourceElement.dataset.pushUrl !== undefined))) || (!sourceElement && url);
      const pushData = shouldPushUrl ? { shouldPush: true, src: sourceElement, url: url, b: blockName, sel: selector } : null;

      if (templates.length) {
        for (let i = 0; i < templates.length; i++) {
          const tpl = templates[i];
          const tplBlock = getAttr(tpl, 'block') || tpl.dataset.block;
          const target = findTargetElement(tplBlock);
          let tplSwap = getAttr(tpl, 'swap') || tpl.dataset.swap || 'innerHTML';
          if (tplSwap === 'append') tplSwap = 'beforeend';
          if (tplSwap === 'prepend') tplSwap = 'afterbegin';
          if (target) {
            doSwap(target, tpl.innerHTML, tplSwap, i === templates.length - 1 ? pushData : null);
          }
        }
      } else {
        const target = findTargetElement(selector);
        if (target) {
          doSwap(target, html, mode, pushData);
        }
      }

      const currentToken = getCsrfToken();
      if (currentToken) {
        document.querySelectorAll('input[name=csrf_token]').forEach(function (input) {
          input.value = currentToken;
          input.defaultValue = currentToken;
          input.setAttribute('value', currentToken);
        });
      }

      const getScopedTag = function (query) {
        let tag = tempDiv.querySelector(query);
        if (!tag) {
          const templatesList = tempDiv.querySelectorAll('template');
          for (let i = 0; i < templatesList.length; i++) {
            tag = templatesList[i].content.querySelector(query);
            if (tag) break;
          }
        }
        return tag;
      };

      const newCss = getScopedTag('#asok-scoped-css');
      const oldCss = document.getElementById('asok-scoped-css');
      if (newCss) {
        if (oldCss) oldCss.remove();
        document.head.appendChild(newCss);
      } else if (oldCss && shouldPushUrl) {
        oldCss.remove();
      }

      const newJs = getScopedTag('#asok-scoped-js');
      const oldJs = document.getElementById('asok-scoped-js');
      if (newJs) {
        if (oldJs) oldJs.remove();
        const scriptElement = document.createElement('script');
        scriptElement.id = 'asok-scoped-js';
        if (newJs.nonce) scriptElement.nonce = newJs.nonce;
        scriptElement.textContent = newJs.textContent;
        document.body.appendChild(scriptElement);
      } else if (oldJs && shouldPushUrl) {
        oldJs.remove();
      }

      const findPageId = function () {
        const it = document.createNodeIterator(tempDiv, NodeFilter.SHOW_COMMENT);
        let comment;
        while ((comment = it.nextNode())) {
          const match = comment.textContent.match(/^\s*page-id:(.+)$/);
          if (match) return match[1].trim();
        }
        return null;
      };

      const pageId = findPageId();
      if (pageId) {
        document.body.dataset.pageId = pageId;
      } else if (shouldPushUrl) {
        delete document.body.dataset.pageId;
      }
    }, function () { });
  }

  // Prefetch dynamic block content
  function prefetchBlock(url, blockName) {
    if (responseCache[url + blockName] || !url || !blockName) return;

    fetch(url, {
      headers: { 'X-Block': blockName, 'X-Prefetch': '1' },
      credentials: 'same-origin'
    }).then(function (res) {
      if (res.ok) {
        res.text().then(function (html) {
          addCache(url + blockName, html);
        });
      }
    });
  }

  // Resolve form parameters, actions, and blocks
  function resolveRequestParameters(el) {
    const form = el.tagName === 'FORM' ? el : el.closest('form');
    const blockName = getAttr(el, 'block') || (form ? getAttr(form, 'block') : null);
    if (!blockName) return null;

    const targetAttr = getAttr(el, 'target');
    const selector = targetAttr || blockName.split(',')[0];
    let swapMode = getAttr(el, 'swap') || (form ? getAttr(form, 'swap') : null) || 'innerHTML';
    if (swapMode === 'append') swapMode = 'beforeend';
    if (swapMode === 'prepend') swapMode = 'afterbegin';

    let url, method, body = null;
    const actionValue = getAttr(el, 'action') || (form ? getAttr(form, 'action') : null);

    if (form && (el === form || el.type === 'submit' || hasAttr(el, 'action'))) {
      url = form.action || location.pathname;
      method = (form.method || 'POST').toUpperCase();
      const formData = new FormData(form);

      if (actionValue) {
        formData.append('_action', actionValue);
      }
      if (el.name && el !== form) {
        formData.append(el.name, el.value);
      }

      if (method === 'GET' && window.AsokSecurity && window.AsokSecurity.hasSensitiveData) {
        if (window.AsokSecurity.hasSensitiveData(formData)) {
          console.warn('[Asok Security] Forcing POST for form with sensitive data');
          method = 'POST';
          body = formData;
        } else {
          const params = new URLSearchParams(formData).toString();
          if (params) {
            url += (url.indexOf('?') < 0 ? '?' : '&') + params;
          }
        }
      } else if (method === 'GET') {
        const params = new URLSearchParams(formData).toString();
        if (params) {
          url += (url.indexOf('?') < 0 ? '?' : '&') + params;
        }
      } else {
        body = formData;
      }
    } else if (el.tagName === 'A') {
      url = el.href;
      method = 'GET';
      if (actionValue) {
        url += (url.indexOf('?') < 0 ? '?' : '&') + '_action=' + actionValue;
      }
    } else {
      url = getAttr(el, 'url') || location.pathname;
      method = (getAttr(el, 'method') || (actionValue ? 'POST' : 'GET')).toUpperCase();
      const formData = new FormData();
      if (el.name) {
        formData.append(el.name, el.value || '');
      }
      if (actionValue) {
        formData.append('_action', actionValue);
      }
      if (method === 'GET' && window.AsokSecurity && window.AsokSecurity.hasSensitiveData) {
        if (window.AsokSecurity.hasSensitiveData(formData)) {
          console.warn('[Asok Security] Forcing POST for form with sensitive data');
          method = 'POST';
          body = formData;
        } else {
          const params = new URLSearchParams(formData).toString();
          if (params) {
            url += (url.indexOf('?') < 0 ? '?' : '&') + params;
          }
        }
      } else if (method === 'GET') {
        const params = new URLSearchParams(formData).toString();
        if (params) {
          url += (url.indexOf('?') < 0 ? '?' : '&') + params;
        }
      } else {
        body = formData;
      }
    }

    const includeSelector = getAttr(el, 'include');
    if (includeSelector) {
      const extraElements = document.querySelectorAll(includeSelector);
      extraElements.forEach(function (x) {
        if (!x.name) return;
        if (method === 'GET') {
          url += (url.indexOf('?') < 0 ? '?' : '&') + encodeURIComponent(x.name) + '=' + encodeURIComponent(x.value || '');
        } else {
          if (!body) body = new FormData();
          body.append(x.name, x.value || '');
        }
      });
    }

    return { url: url, method: method, body: body, block: blockName, sel: selector, swap: swapMode };
  }

  // Get indicator elements for visual feedback during requests
  function getIndicatorElements(el) {
    const selector = getAttr(el, 'indicator');
    if (selector === null && el.dataset.indicator === undefined) return [];
    if (selector === '' || selector === null) return [el];
    return Array.prototype.slice.call(document.querySelectorAll(selector));
  }

  // Get elements that should be disabled during a request
  function getDisableElements(el) {
    if (!hasAttr(el, 'disable') && el.dataset.disable === undefined) return [];
    if (el.tagName === 'FORM') {
      return Array.prototype.slice.call(el.querySelectorAll('button,input[type=submit]'));
    }
    return [el];
  }

  // Execute block swap request
  function triggerBlockRequest(el) {
    const confirmMessage = getAttr(el, 'confirm');
    if (confirmMessage && !confirm(confirmMessage)) return;

    const resolved = resolveRequestParameters(el);
    if (!resolved) return;

    const requestOptions = { method: resolved.method };
    if (resolved.body) {
      requestOptions.body = resolved.body;
    }

    const indicators = getIndicatorElements(el);
    const disableElements = getDisableElements(el);

    indicators.forEach(function (x) {
      x.classList.add('is-loading');
    });
    disableElements.forEach(function (x) {
      x.disabled = true;
    });

    const isPageNavigation = (el.dataset && el.dataset.pushUrl !== undefined) || hasAttr(el, 'push-url') || el.tagName === 'A';
    const pageContainer = document.querySelector('[data-asok-page-transition]');

    if (isPageNavigation && pageContainer) {
      const transitionAttr = pageContainer.getAttribute('data-asok-page-transition') || 'page';
      const parts = transitionAttr.split(' ');
      const type = parts[0];
      const duration = parseInt(parts[1]) || 250;

      pageContainer.classList.add('asok-' + type + '-out');
      requestAnimationFrame(() => pageContainer.classList.add('is-leaving'));
      setTimeout(() => pageContainer.classList.remove('asok-' + type + '-out', 'is-leaving'), duration);
    }

    return performBlockSwap(resolved.url, resolved.block, resolved.sel, resolved.swap, requestOptions, el).then(
      function () {
        indicators.forEach(function (x) {
          x.classList.remove('is-loading');
        });
        disableElements.forEach(function (x) {
          x.disabled = false;
        });
      },
      function () {
        indicators.forEach(function (x) {
          x.classList.remove('is-loading');
        });
        disableElements.forEach(function (x) {
          x.disabled = false;
        });
      }
    );
  }

  // Parse trigger settings
  function parseTriggerOption(triggerStr) {
    const everyMatch = triggerStr.match(/^every\s+(\d+)(ms|s)$/);
    if (everyMatch) {
      const value = parseInt(everyMatch[1]);
      const multiplier = everyMatch[2] === 's' ? 1000 : 1;
      return { event: 'every', interval: value * multiplier };
    }

    const parts = triggerStr.split(/\s+/);
    const eventName = parts[0];
    let delay = 0;

    for (let i = 1; i < parts.length; i++) {
      const delayMatch = parts[i].match(/^delay:(\d+)(ms|s)?$/);
      if (delayMatch) {
        const value = parseInt(delayMatch[1]);
        const multiplier = delayMatch[2] === 's' ? 1000 : 1;
        delay = value * multiplier;
      }
    }

    return { event: eventName, delay: delay };
  }

  // Listeners setup
  document.addEventListener('submit', function (e) {
    const form = e.target;
    if (!hasAttr(form, 'block')) return;

    const triggerAttr = getAttr(form, 'trigger') || 'submit';
    const triggerEvent = triggerAttr.split(/\s+/)[0];
    if (triggerEvent !== 'submit') return;

    e.preventDefault();
    triggerBlockRequest(form);
  });

  document.addEventListener('mouseover', function (e) {
    const link = e.target.closest('[asok-block], [data-block], [data-asok-block]');
    if (
      link &&
      link.tagName === 'A' &&
      getAttr(link, 'url') !== 'none' &&
      (getAttr(link, 'trigger') || 'click').split(/\s+/)[0] === 'click'
    ) {
      prefetchBlock(link.href, getAttr(link, 'block'));
    }
  });

  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-asok-component]')) return;

    const el = e.target.closest('[asok-block], [data-block], [data-asok-block]');
    if (!el || el.tagName === 'FORM') return;

    const isInteractive =
      el.tagName === 'A' ||
      el.tagName === 'BUTTON' ||
      el.tagName === 'INPUT' ||
      hasAttr(el, 'url') ||
      hasAttr(el, 'action') ||
      hasAttr(el, 'trigger');

    if (!isInteractive) return;

    const triggerAttr = getAttr(el, 'trigger') || 'click';
    const triggerEvent = triggerAttr.split(/\s+/)[0];
    if (triggerEvent !== 'click') return;

    e.preventDefault();
    triggerBlockRequest(el);
  });

  // Setup dynamic components triggers and SSE
  function initSpaDirectives(root) {
    const el = root || document;
    const elements = el === document ? document.querySelectorAll('*') : [el, ...el.querySelectorAll('*')];

    // SSE event sources
    elements.forEach(function (n) {
      if (hasAttr(n, 'sse')) {
        if (n.__asokSseSetup) return;
        n.__asokSseSetup = 1;

        const sseUrl = getAttr(n, 'sse');
        if (!sseUrl || !isSameOrigin(sseUrl)) return;
        const eventSource = new EventSource(sseUrl);
        // Track it so cleanupSpaResources() can close it when the node is swapped out.
        n.__asokSse = eventSource;
        const selector = getAttr(n, 'block') || ('#' + n.id);
        let swapMode = getAttr(n, 'swap') || 'innerHTML';
        if (swapMode === 'append') swapMode = 'beforeend';
        if (swapMode === 'prepend') swapMode = 'afterbegin';

        eventSource.onmessage = function (ev) {
          const tempContainer = document.createElement('div');
          tempContainer.innerHTML = ev.data;

          tempContainer.querySelectorAll('script').forEach(function (script) {
            let parent = script.parentNode;
            while (parent && parent !== tempContainer) {
              if (parent.tagName === 'TEMPLATE') return;
              parent = parent.parentNode;
            }
            const newScript = document.createElement('script');
            if (script.nonce) newScript.nonce = script.nonce;
            if (script.src) newScript.src = script.src;
            newScript.textContent = script.textContent;
            newScript.dataset.run = '1';
            script.dataset.run = '1';
            document.body.appendChild(newScript);
            newScript.remove();
          });

          const templates = tempContainer.querySelectorAll('template[data-block], template[asok-block]');

          if (templates.length) {
            for (let i = 0; i < templates.length; i++) {
              const tpl = templates[i];
              const blockName = getAttr(tpl, 'block') || tpl.dataset.block;
              const target = findTargetElement(blockName);
              let tplSwap = getAttr(tpl, 'swap') || tpl.dataset.swap || 'innerHTML';
              if (tplSwap === 'append') tplSwap = 'beforeend';
              if (tplSwap === 'prepend') tplSwap = 'afterbegin';
              if (target) {
                doSwap(target, tpl.innerHTML, tplSwap, null);
              }
            }
          } else {
            const target = findTargetElement(selector);
            if (target) {
              doSwap(target, ev.data, swapMode, null);
            }
          }
        };
      }
    });

    // Custom triggers
    elements.forEach(function (n) {
      if (hasAttr(n, 'block') && hasAttr(n, 'trigger')) {
        if (n.__asokTriggerSetup) return;
        n.__asokTriggerSetup = 1;

        const triggerAttr = getAttr(n, 'trigger');
        const trigger = parseTriggerOption(triggerAttr);
        if (trigger.event === 'submit' || trigger.event === 'click') return;

        if (trigger.event === 'load') {
          triggerBlockRequest(n);
          return;
        }

        if (trigger.event === 'every') {
          triggerBlockRequest(n);
          // Track it so cleanupSpaResources() can clear it when the node is swapped out.
          n.__asokEveryInterval = setInterval(function () {
            triggerBlockRequest(n);
          }, trigger.interval);
          return;
        }

        let debounceTimer;
        n.addEventListener(trigger.event, function () {
          if (trigger.delay) {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(function () {
              triggerBlockRequest(n);
            }, trigger.delay);
          } else {
            triggerBlockRequest(n);
          }
        });
      }
    });
  }

  window.addEventListener('popstate', function (e) {
    if (e.state && e.state.url && e.state.b) {
      performBlockSwap(e.state.url, e.state.b, e.state.sel, e.state.mode, {}, null);
    } else {
      location.reload();
    }
  });

  window.Asok = window.Asok || {};
  const oldInit = window.Asok.init;
  window.Asok.init = function (el) {
    if (oldInit) oldInit(el);
    initSpaDirectives(el);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      initSpaDirectives(document);
    });
  } else {
    initSpaDirectives(document);
  }
})();
