;(function () {
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

  // Retrieve the CSRF token from page meta tag
  function getCsrfToken() {
    const meta = document.querySelector('meta[name=csrf-token]');
    return meta ? meta.content : '';
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
      } catch (e) {}
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
    const realTarget = target._isBlockMarker ? target._startMarker.parentNode : target;

    const cleanNodes = function (nodes) {
      const targets = Array.isArray(nodes) ? nodes : [nodes];
      targets.forEach(function (t) {
        if (!t) return;
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
        const nonce = script.nonce || window.Asok?.nonce || document.querySelector('script[nonce]')?.getAttribute('nonce') || '';
        if (nonce) newScript.nonce = nonce;
        if (script.src) newScript.src = script.src;
        newScript.textContent = script.textContent;
        newScript.dataset.run = '1';
        script.parentNode.replaceChild(newScript, script);
      });

      // Re-initialize directives and lifecycles
      nodes.forEach(function (node) {
        if (node.querySelectorAll) {
          if (window.AsokDirectives && window.AsokDirectives.init) {
            window.AsokDirectives.init(node);
          }
          if (window.Asok && window.Asok.init) {
            window.Asok.init(node);
          }
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

        if (pushData.src && pushData.src.dataset && pushData.src.dataset.pushUrl !== undefined) {
          const pushUrl = pushData.src.dataset.pushUrl || pushData.url;
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

      cleanNodes(nodesToRemove);

      nodesToRemove.forEach(function (node) {
        node.remove();
      });

      const tempContainer = document.createElement('div');
      // SECURITY: HTML comes from server - sanitize to prevent XSS if server is compromised
      // Note: This assumes server responses are trusted but adds defense-in-depth
      const sanitizedHtml = window.AsokSecurity && window.AsokSecurity.sanitizeHtml ?
        window.AsokSecurity.sanitizeHtml(html) : html;
      tempContainer.innerHTML = sanitizedHtml;
      const insertedNodes = Array.from(tempContainer.childNodes);
      insertedNodes.forEach(function (node) {
        startMarker.parentNode.insertBefore(node, endMarker);
      });

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
          afterSwap(newNodes || [target]);
        });
      } else {
        // SECURITY: Fallback implementation with sanitization (defense-in-depth)
        const safeHtml = window.AsokSecurity && window.AsokSecurity.sanitizeHtml ?
          window.AsokSecurity.sanitizeHtml(html) : html;

        if (mode === 'delete') {
          target.remove();
          afterSwap([]);
        } else if (mode === 'outerHTML' || mode === 'replaceWith') {
          const fragment = document.createRange().createContextualFragment(safeHtml);
          const newNodes = Array.from(fragment.childNodes);
          target.replaceWith(fragment);
          afterSwap(newNodes);
        } else if (mode === 'innerHTML') {
          target.innerHTML = safeHtml;
          afterSwap(Array.from(target.childNodes));
        } else {
          const fragment = document.createRange().createContextualFragment(safeHtml);
          const newNodes = Array.from(fragment.childNodes);
          if (mode === 'beforebegin') {
            target.parentNode.insertBefore(fragment, target);
          } else if (mode === 'afterbegin') {
            target.insertBefore(fragment, target.firstChild);
          } else if (mode === 'beforeend') {
            target.appendChild(fragment);
          } else if (mode === 'afterend') {
            target.parentNode.insertBefore(fragment, target.nextSibling);
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

    const headers = Object.assign({ 'X-Block': blockName, 'X-CSRF-Token': getCsrfToken() }, options.headers || {});
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
            // SECURITY: Validate redirect URL to prevent open redirect attacks
            if (window.AsokSecurity && window.AsokSecurity.isSafeUrl) {
              if (!window.AsokSecurity.isSafeUrl(redirectUrl)) {
                console.error('[Asok] Blocked unsafe redirect URL:', redirectUrl);
                return Promise.reject('unsafe_redirect');
              }
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
        window.location.href = url;
        return;
      }

      const tempDiv = document.createElement('div');
      // SECURITY: tempDiv is not inserted into DOM, only used to parse templates
      // The actual content is sanitized when passed through doSwap() -> Asok.swap()
      tempDiv.innerHTML = html;

      // Execute root-level scripts (like the directives registry) before swapping templates/content
      tempDiv.querySelectorAll('script').forEach(function (script) {
        let parent = script.parentNode;
        while (parent && parent !== tempDiv) {
          if (parent.tagName === 'TEMPLATE') return;
          parent = parent.parentNode;
        }
        const newScript = document.createElement('script');
        const nonce = script.nonce || window.Asok?.nonce || document.querySelector('script[nonce]')?.getAttribute('nonce') || '';
        if (nonce) newScript.nonce = nonce;
        if (script.src) newScript.src = script.src;
        newScript.textContent = script.textContent;
        newScript.dataset.run = '1';
        script.dataset.run = '1';
        document.body.appendChild(newScript);
        newScript.remove();
      });

      const templates = tempDiv.querySelectorAll('template[data-block]');
      const shouldPushUrl = (sourceElement && sourceElement.dataset && sourceElement.dataset.pushUrl !== undefined) || (!sourceElement && url);
      const pushData = shouldPushUrl ? { shouldPush: true, src: sourceElement, url: url, b: blockName, sel: selector } : null;

      if (templates.length) {
        for (let i = 0; i < templates.length; i++) {
          const tpl = templates[i];
          const target = findTargetElement(tpl.dataset.block);
          if (target) {
            doSwap(target, tpl.innerHTML, tpl.dataset.swap || 'innerHTML', i === templates.length - 1 ? pushData : null);
          }
        }
      } else {
        const target = findTargetElement(selector);
        if (target) {
          doSwap(target, html, mode, pushData);
        }
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

      // Handle scoped CSS
      const newCss = getScopedTag('#asok-scoped-css');
      const oldCss = document.getElementById('asok-scoped-css');
      if (newCss) {
        if (oldCss) oldCss.remove();
        document.head.appendChild(newCss);
      } else if (oldCss && shouldPushUrl) {
        oldCss.remove();
      }

      // Handle scoped JS
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

      // Handle page-id meta attributes
      const findPageId = function () {
        const it = tempDiv.createNodeIterator(tempDiv.body, NodeFilter.SHOW_COMMENT);
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
    }, function () {});
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
    const blockName = el.dataset.block || (form ? form.dataset.block : null);
    if (!blockName) return null;

    const selector = el.dataset.target || blockName.split(',')[0];
    const swapMode = el.dataset.swap || 'innerHTML';
    let url, method, body = null;
    const actionValue = el.dataset.action || (form ? form.dataset.action : null);

    if (form && (el === form || el.type === 'submit' || el.dataset.action)) {
      url = form.action || location.pathname;
      method = (form.method || 'POST').toUpperCase();
      const formData = new FormData(form);

      if (actionValue) {
        formData.append('_action', actionValue);
      }
      if (el.name && el !== form) {
        formData.append(el.name, el.value);
      }

      // SECURITY: Check for sensitive data before allowing GET method
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
      url = el.dataset.url || location.pathname;
      method = (el.dataset.method || (actionValue ? 'POST' : 'GET')).toUpperCase();
      const formData = new FormData();
      if (el.name) {
        formData.append(el.name, el.value || '');
      }
      if (actionValue) {
        formData.append('_action', actionValue);
      }
      // SECURITY: Check for sensitive data before allowing GET method
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

    const includeSelector = el.dataset.include;
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
    const selector = el.dataset.indicator;
    if (selector === undefined) return [];
    if (selector === '') return [el];
    return Array.prototype.slice.call(document.querySelectorAll(selector));
  }

  // Get elements that should be disabled during a request
  function getDisableElements(el) {
    if (el.dataset.disable === undefined) return [];
    if (el.tagName === 'FORM') {
      return Array.prototype.slice.call(el.querySelectorAll('button,input[type=submit]'));
    }
    return [el];
  }

  // Execute block swap request
  function triggerBlockRequest(el) {
    const confirmMessage = el.dataset.confirm;
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

    const isPageNavigation = (el.dataset && el.dataset.pushUrl !== undefined) || el.tagName === 'A';
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
    if (!form.dataset || !form.dataset.block) return;

    const triggerEvent = (form.dataset.trigger || 'submit').split(/\s+/)[0];
    if (triggerEvent !== 'submit') return;

    e.preventDefault();
    triggerBlockRequest(form);
  });

  document.addEventListener('mouseover', function (e) {
    const link = e.target.closest('[data-block]');
    if (
      link &&
      link.tagName === 'A' &&
      link.dataset.url !== 'none' &&
      (link.dataset.trigger || 'click').split(/\s+/)[0] === 'click'
    ) {
      prefetchBlock(link.href, link.dataset.block);
    }
  });

  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-asok-component]')) return;

    const el = e.target.closest('[data-block]');
    if (!el || el.tagName === 'FORM') return;

    const isInteractive = 
      el.tagName === 'A' || 
      el.tagName === 'BUTTON' || 
      el.tagName === 'INPUT' ||
      el.hasAttribute('data-url') || 
      el.hasAttribute('data-action') ||
      el.hasAttribute('data-trigger');

    if (!isInteractive) return;

    const triggerEvent = (el.dataset.trigger || 'click').split(/\s+/)[0];
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
      if (n.hasAttribute && n.hasAttribute('data-sse')) {
        if (n.__asokSseSetup) return;
        n.__asokSseSetup = 1;

        const eventSource = new EventSource(n.dataset.sse);
        const selector = n.dataset.block || ('#' + n.id);
        const swapMode = n.dataset.swap || 'innerHTML';

        eventSource.onmessage = function (ev) {
          const tempContainer = document.createElement('div');
          // SECURITY: tempContainer is not inserted into DOM, only used to parse templates
          // The actual content is sanitized when passed through doSwap() -> Asok.swap()
          tempContainer.innerHTML = ev.data;

          // Execute root-level scripts (like the directives registry) before swapping templates/content
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

          const templates = tempContainer.querySelectorAll('template[data-block]');

          if (templates.length) {
            for (let i = 0; i < templates.length; i++) {
              const tpl = templates[i];
              const target = findTargetElement(tpl.dataset.block);
              if (target) {
                doSwap(target, tpl.innerHTML, tpl.dataset.swap || 'innerHTML', null);
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
      if (n.hasAttribute && n.hasAttribute('data-block') && n.hasAttribute('data-trigger')) {
        if (n.__asokTriggerSetup) return;
        n.__asokTriggerSetup = 1;

        const trigger = parseTriggerOption(n.dataset.trigger);
        if (trigger.event === 'submit' || trigger.event === 'click') return;

        if (trigger.event === 'load') {
          triggerBlockRequest(n);
          return;
        }

        if (trigger.event === 'every') {
          triggerBlockRequest(n);
          setInterval(function () {
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
