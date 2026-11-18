; (function () {
  'use strict';

  let currentParams = {};
  const changeListeners = [];

  // Active route state store with dynamic getters so .path is ALWAYS in sync with the current URL
  const routeStore = {
    get path() {
      return window.location.pathname;
    },
    set path(_) {},
    get query() {
      return Object.fromEntries(new URLSearchParams(window.location.search));
    },
    set query(_) {},
    get hash() {
      return window.location.hash;
    },
    set hash(_) {},
    get params() {
      return currentParams;
    },
    set params(val) {
      currentParams = val || {};
    }
  };

  // Convert route pattern (/users/:id/edit or *) to Regular Expression
  const parsePattern = (pattern) => {
    if (!pattern || pattern === '*') return { regex: /.*/, keys: [] };
    const keys = [];
    const normalized = pattern.replace(/\/$/, '') || '/';
    const parts = normalized.split('/');
    const regexParts = parts.map(part => {
      if (part.startsWith(':')) {
        keys.push(part.slice(1));
        return '([^/]+)';
      }
      return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    });
    return { regex: new RegExp('^' + regexParts.join('/') + '$'), keys };
  };

  // Match current path against pattern
  const matchPattern = (pattern, path) => {
    const normPath = path.replace(/\/$/, '') || '/';
    const { regex, keys } = parsePattern(pattern);
    const match = normPath.match(regex);
    if (!match) return null;
    const params = {};
    keys.forEach((key, idx) => {
      if (key !== '__proto__' && key !== 'constructor' && key !== 'prototype') {
        params[key] = decodeURIComponent(match[idx + 1]);
      }
    });
    return params;
  };

  // Update current active route and render matching view templates
  const updateRouter = () => {
    const currentPath = window.location.pathname;
    const currentSearch = window.location.search;
    const currentHash = window.location.hash;

    routeStore.params = {};

    const routerContainers = document.querySelectorAll('[asok-router], [data-asok-router], [data-router]');

    routerContainers.forEach(container => {
      const routeElements = Array.from(container.querySelectorAll('[asok-route], [data-asok-route], [data-route]'));
      let newMatchingEl = null;
      let matchedParams = null;
      let activeTrans = null;

      for (const el of routeElements) {
        const pattern = el.getAttribute('asok-route') || el.getAttribute('data-asok-route') || el.getAttribute('data-route');
        const params = matchPattern(pattern, currentPath);
        if (params !== null) {
          newMatchingEl = el;
          matchedParams = params;
          break;
        }
      }

      if (newMatchingEl) {
        routeStore.params = matchedParams || {};
      }

      // Hide and transition out all inactive routes FIRST before inserting new DOM elements
      routeElements.forEach(el => {
        if (el !== newMatchingEl) {
          hideRouteElement(el, container);
        }
      });

      // Render matching route SECOND so entering view stacks cleanly over leaving view
      if (newMatchingEl) {
        activeTrans = getTransitionInfo(newMatchingEl, container);
        renderRouteElement(newMatchingEl, container);
      }

      // While views cross-fade, mark the container `asok-transitioning`
      if (activeTrans) {
        container.classList.add('asok-transitioning');
        clearTimeout(container._asokTransTimer);
        container._asokTransTimer = setTimeout(() => {
          container.classList.remove('asok-transitioning');
          container.style.minHeight = '';
        }, activeTrans.duration + 40);
      }
    });

    updateActiveLinks(currentPath);

    const routeDetail = {
      path: currentPath,
      query: routeStore.query,
      hash: currentHash,
      params: routeStore.params
    };

    window.dispatchEvent(new CustomEvent('asok:routechange', {
      detail: routeDetail
    }));

    changeListeners.forEach(cb => {
      try {
        cb(routeDetail);
      } catch (err) {
        console.error('[AsokRouter] onRouteChange error:', err);
      }
    });
  };

  const updateMetaTag = (selector, nameAttr, attrValue, content) => {
    let meta = document.querySelector(selector);
    if (!meta) {
      meta = document.createElement('meta');
      meta.setAttribute(nameAttr, attrValue);
      document.head.appendChild(meta);
    }
    meta.setAttribute('content', content);
  };

  const processMetaDirectives = (el) => {
    if (!el) return;
    const innerNodes = el.content ? Array.from(el.content.querySelectorAll('*')) : Array.from(el.querySelectorAll('*'));
    const allNodes = [el, ...innerNodes];
    allNodes.forEach(node => {
      if (!node || !node.attributes) return;
      Array.from(node.attributes).forEach(attr => {
        const name = attr.name;
        if (name.startsWith('asok-meta:') || name.startsWith('data-asok-meta:')) {
          const key = name.replace(/^(data-)?asok-meta:/, '');
          const val = attr.value;
          if (key === 'title') {
            document.title = val;
          } else if (!/^[a-zA-Z0-9_:-]+$/.test(key)) {
            return;
          } else if (key.startsWith('og:')) {
            updateMetaTag(`meta[property="${key}"]`, 'property', key, val);
          } else {
            updateMetaTag(`meta[name="${key}"]`, 'name', key, val);
          }
        }
      });
    });
  };

  const getTransitionInfo = (el, container) => {
    const raw = el.getAttribute('asok-transition') ||
                el.getAttribute('data-asok-transition') ||
                container.getAttribute('asok-transition') ||
                container.getAttribute('data-asok-transition');
    if (!raw) return null;
    const parts = raw.trim().split(/\s+/);
    const type = parts[0] || 'fade';
    const duration = parseInt(parts[1]) || 200;
    return { type, duration };
  };

  const applyEnterTransition = (viewEl, trans) => {
    if (!viewEl || !trans) return;
    const { type, duration } = trans;
    viewEl.style.transitionDuration = duration + 'ms';
    if (!viewEl.classList.contains('asok-' + type + '-in')) {
      viewEl.classList.add('asok-' + type + '-in');
    }
    // Force layout reflow so the browser registers the initial hidden state (opacity: 0)
    void viewEl.offsetWidth;

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        viewEl.classList.add('is-entering');
        setTimeout(() => {
          viewEl.classList.remove('asok-' + type + '-in', 'is-entering');
          viewEl.style.transitionDuration = '';
        }, duration);
      });
    });
  };

  // During a route transition the entering and leaving views would otherwise
  // both sit in normal flow, stacking vertically and doubling the container
  // height until the leave completes — the "ugly" layout jump. Taking the
  // leaving view out of flow (absolute) lets the entering view occupy its slot
  // immediately, producing a clean in-place crossfade. Order of enter/leave in
  // updateRouter no longer matters visually.
  const beginLeavePositioning = (viewEl, container) => {
    if (!viewEl || !container) return;
    const currHeight = container.offsetHeight;
    if (currHeight > 0) {
      container.style.minHeight = currHeight + 'px';
    }
    if (window.getComputedStyle(container).position === 'static') {
      container.style.position = 'relative';
    }
    viewEl.style.top = '0px';
    viewEl.style.left = '0px';
    viewEl.style.width = '100%';
    viewEl.style.position = 'absolute';
    viewEl.style.zIndex = '1';
    viewEl.style.pointerEvents = 'none';
  };

  const endLeavePositioning = (viewEl) => {
    if (!viewEl) return;
    viewEl.style.position = '';
    viewEl.style.top = '';
    viewEl.style.left = '';
    viewEl.style.width = '';
    viewEl.style.pointerEvents = '';
  };

  const applyLeaveTransition = (viewEl, trans, onComplete) => {
    if (!viewEl || !trans) {
      if (onComplete) onComplete();
      return;
    }
    const { type, duration } = trans;
    viewEl.style.transitionDuration = duration + 'ms';
    viewEl.classList.add('asok-' + type + '-out');
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        viewEl.classList.add('is-leaving');
        setTimeout(() => {
          viewEl.classList.remove('asok-' + type + '-out', 'is-leaving');
          viewEl.style.transitionDuration = '';
          if (onComplete) onComplete();
        }, duration);
      });
    });
  };

  // Helper to render active route element
  const renderRouteElement = (el, container) => {
    processMetaDirectives(el);
    const trans = getTransitionInfo(el, container);

    const titleAttr = el.getAttribute('asok-title') || el.getAttribute('data-asok-title') || el.getAttribute('data-title');
    if (titleAttr) {
      document.title = titleAttr;
    }

    const descAttr = el.getAttribute('asok-description') || el.getAttribute('data-asok-description') || el.getAttribute('data-description');
    if (descAttr) {
      updateMetaTag('meta[name="description"]', 'name', 'description', descAttr);
    }

    if (el.tagName.toLowerCase() === 'template') {
      const currentRoutePath = window.location.pathname;
      let renderId = el._asokRenderedId;
      let renderedView = renderId ? container.querySelector(`[data-asok-route-view="${renderId}"]`) : null;
      if (renderedView && el._asokRenderedPath && el._asokRenderedPath !== currentRoutePath) {
        renderedView.remove();
        renderedView = null;
        renderId = null;
      }
      if (!renderedView) {
        renderId = 'view-' + Math.random().toString(36).substring(2, 9);
        el._asokRenderedId = renderId;
        el._asokRenderedPath = currentRoutePath;
        const clone = el.content.cloneNode(true);
        const embeddedTitle = clone.querySelector('title');
        if (embeddedTitle && embeddedTitle.textContent) {
          document.title = embeddedTitle.textContent.trim();
          embeddedTitle.remove();
        }
        const embeddedMetas = clone.querySelectorAll('meta');
        embeddedMetas.forEach(meta => {
          const name = meta.getAttribute('name');
          const property = meta.getAttribute('property');
          const content = meta.getAttribute('content');
          if (name && content && /^[a-zA-Z0-9_:-]+$/.test(name)) {
            updateMetaTag(`meta[name="${name}"]`, 'name', name, content);
            meta.remove();
          } else if (property && content && /^[a-zA-Z0-9_:-]+$/.test(property)) {
            updateMetaTag(`meta[property="${property}"]`, 'property', property, content);
            meta.remove();
          }
        });
        const wrapper = document.createElement('div');
        wrapper.setAttribute('data-asok-route-view', renderId);
        wrapper.style.position = 'relative';
        wrapper.style.zIndex = '2';
        wrapper.style.width = '100%';

        // Pre-apply transition classes BEFORE inserting into DOM to prevent flash
        if (trans) {
          wrapper.style.transitionDuration = trans.duration + 'ms';
          wrapper.classList.add('asok-' + trans.type + '-in');
        }

        wrapper.appendChild(clone);
        el.parentNode.insertBefore(wrapper, el.nextSibling);
        renderedView = wrapper;

        // Re-execute any executable script tags inside the dynamically rendered route template
        wrapper.querySelectorAll('script').forEach(oldScript => {
          const type = (oldScript.getAttribute('type') || '').toLowerCase();
          if (type && !['text/javascript', 'application/javascript', 'module'].includes(type)) {
            return;
          }
          const newScript = document.createElement('script');
          Array.from(oldScript.attributes).forEach(attr => newScript.setAttribute(attr.name, attr.value));
          const nonce = oldScript.nonce || oldScript.getAttribute('nonce');
          if (nonce && !newScript.getAttribute('nonce')) {
            newScript.setAttribute('nonce', nonce);
          }
          if (oldScript.src) {
            newScript.src = oldScript.src;
          } else {
            newScript.textContent = oldScript.textContent;
          }
          oldScript.parentNode.replaceChild(newScript, oldScript);
        });

        if (window.Asok && typeof window.Asok.init === 'function') {
          window.Asok.init(renderedView);
        }
      }
      renderedView.style.display = '';
      if (trans) applyEnterTransition(renderedView, trans);
    } else {
      if (trans) {
        el.style.transitionDuration = trans.duration + 'ms';
        el.classList.add('asok-' + trans.type + '-in');
      }
      el.style.display = '';
      if (trans) applyEnterTransition(el, trans);
    }
  };

  // Helper to hide/unmount inactive route element
  const hideRouteElement = (el, container) => {
    const trans = getTransitionInfo(el, container);
    if (el.tagName.toLowerCase() === 'template') {
      const renderId = el._asokRenderedId;
      if (renderId) {
        const renderedView = container.querySelector(`[data-asok-route-view="${renderId}"]`);
        if (renderedView) {
          if (trans) beginLeavePositioning(renderedView, container);
          applyLeaveTransition(renderedView, trans, () => {
            renderedView.querySelectorAll('[data-asok-component]').forEach(comp => {
              delete comp.__asokWsReady;
              delete comp.__asokIniting;
              const cid = comp.id ? comp.id.replace('asok-', '') : null;
              if (cid && window.Asok && typeof window.Asok.leaveComponent === 'function') {
                window.Asok.leaveComponent(cid);
              }
            });
            if (window.AsokDirectives && typeof window.AsokDirectives.cleanupOld === 'function') {
              window.AsokDirectives.cleanupOld(renderedView);
            }
            renderedView.remove();
          });
        }
        delete el._asokRenderedId;
      }
    } else {
      if (trans) beginLeavePositioning(el, container);
      applyLeaveTransition(el, trans, () => {
        el.style.display = 'none';
        endLeavePositioning(el);
      });
    }
  };

  const isSafeNavUrl = (url) => {
    if (!url || typeof url !== 'string') return false;
    const sec = window['Asok_Security'.replace('_', '')];
    if (sec && typeof sec.isSafeUrl === 'function') {
      return sec.isSafeUrl(url);
    }
    let clean = url.replace(/[\x00-\x1F\x7F]/g, '').trim();
    try { clean = decodeURIComponent(clean); } catch (_) {}
    clean = clean.toLowerCase();
    if (clean.startsWith('javascript:') || clean.startsWith('data:') || clean.startsWith('vbscript:')) {
      return false;
    }
    if (clean.startsWith('//') || clean.startsWith('\\\\') || clean.startsWith('/\\') || clean.startsWith('\\/')) return false;
    return true;
  };

  // Update active CSS classes on link elements & auto-populate href on <a> for a11y/SEO
  const updateActiveLinks = (currentPath) => {
    const links = document.querySelectorAll('[asok-link], [data-asok-link], [data-link]');
    links.forEach(link => {
      const rawAttr = link.getAttribute('asok-link') || link.getAttribute('data-asok-link') || link.getAttribute('data-link');
      const href = (rawAttr && rawAttr !== 'true' && rawAttr !== '') ? rawAttr : link.getAttribute('href');
      if (!href) return;

      if (link.tagName.toLowerCase() === 'a' && !link.hasAttribute('href')) {
        if (isSafeNavUrl(href)) {
          link.setAttribute('href', href);
        }
      }

      const activeAttr = link.getAttribute('asok-active-class') || link.getAttribute('data-asok-active-class') || 'active';
      const activeClasses = activeAttr.trim().split(/\s+/).filter(Boolean);
      const targetPath = href.split('?')[0].split('#')[0];
      if (activeClasses.length > 0) {
        if (targetPath === currentPath) {
          link.classList.add(...activeClasses);
        } else {
          link.classList.remove(...activeClasses);
        }
      }
    });
  };

  // Programmatic navigation
  const navigate = (to, replace = false) => {
    if (!to) return;
    if (!isSafeNavUrl(to)) {
      console.warn('[AsokRouter] Blocked unsafe navigation URL:', to);
      return;
    }
    if (replace) {
      window.history.replaceState(null, '', to);
    } else {
      window.history.pushState(null, '', to);
    }
    updateRouter();
    if (!window.location.hash && window.scrollY > 0) {
      requestAnimationFrame(() => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
      });
    }
  };

  // Global click delegator for links
  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
      return;
    }
    const link = e.target.closest('[asok-link], [data-asok-link], [data-link]');
    if (link) {
      const target = link.getAttribute('target');
      if (target && target !== '_self') return;
      if (link.hasAttribute('download')) return;
      if (link.getAttribute('rel') === 'external') return;

      const rawAttr = link.getAttribute('asok-link') || link.getAttribute('data-asok-link') || link.getAttribute('data-link');
      const href = (rawAttr && rawAttr !== 'true' && rawAttr !== '') ? rawAttr : link.getAttribute('href');
      if (href) {
        if (href.startsWith('mailto:') || href.startsWith('tel:') || href.startsWith('javascript:')) return;
        try {
          const targetUrl = new URL(href, window.location.origin);
          if (targetUrl.origin === window.location.origin) {
            e.preventDefault();
            navigate(targetUrl.pathname + targetUrl.search + targetUrl.hash);
          }
        } catch (_) {}
      }
    }
  });

  // Listen to popstate (back/forward browser buttons)
  window.addEventListener('popstate', updateRouter);

  // Auto-init router on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', updateRouter);
  } else {
    updateRouter();
  }

  const onRouteChange = (callback) => {
    if (typeof callback !== 'function') return;
    changeListeners.push(callback);
    try {
      callback({
        path: window.location.pathname,
        query: routeStore.query,
        hash: window.location.hash,
        params: routeStore.params
      });
    } catch (err) {
      console.error('[AsokRouter] onRouteChange initial call error:', err);
    }
  };

  // Export AsokRouter
  window.AsokRouter = {
    get path() { return window.location.pathname; },
    get query() { return routeStore.query; },
    get hash() { return window.location.hash; },
    get params() { return routeStore.params; },
    route: routeStore,
    navigate,
    update: updateRouter,
    matchPattern,
    onRouteChange,
    afterEach: onRouteChange
  };

  if (!window.Asok) window.Asok = {};
  window.Asok.$route = routeStore;
  window.Asok.navigate = navigate;
})();
