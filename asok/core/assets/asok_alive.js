window.asokWS = function (path) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  let wsHost;
  let wsPrefix = window.ASOK_WS_PATH !== undefined ? window.ASOK_WS_PATH : "";

  // SECURITY: Only allow configurable port in development (localhost)
  if (
    location.hostname === "localhost" ||
    location.hostname === "127.0.0.1" ||
    location.hostname === "0.0.0.0" ||
    location.hostname.startsWith("192.168.")
  ) {
    const defaultPort = location.port ? parseInt(location.port, 10) : (location.protocol === "https:" ? 443 : 80);
    const port = window.ASOK_WS_PORT || defaultPort;
    // SECURITY: Validate port range to prevent hijacking
    if (window.AsokSecurity && window.AsokSecurity.isValidPort) {
      if (!window.AsokSecurity.isValidPort(port)) {
        console.error('[Asok Security] Invalid WebSocket port:', port);
        throw new Error('Invalid WebSocket port configuration');
      }
    }
    wsHost = location.hostname + ":" + port;
  } else {
    // Production: use the same host[:port] as the page.
    // A reverse proxy is expected to handle the WebSocket upgrade on wsPrefix+path.
    // Default prefix is "" — set window.ASOK_WS_PATH = "/ws" if your proxy needs it.
    wsHost = location.host;
  }

  return new WebSocket(protocol + "//" + wsHost + wsPrefix + path);
};


(function () {
  let ws;
  // Keyed by DOM element: a plain object would coerce every element to the same
  // "[object HTMLInputElement]" string key and cross-cancel debounce timers.
  const timers = new WeakMap();
  let connecting = false;
  let reconnectAttempts = 0;

  function connect() {
    if (connecting) return;
    if (ws) {
      if (ws.readyState === 0) return; // CONNECTING
      if (ws.readyState === 1) return; // OPEN — keep the healthy socket, don't tear it down
    }
    connecting = true;
    ws = window.asokWS("/asok/live");

    ws.onopen = function () {
      connecting = false;
      reconnectAttempts = 0;
      if (window._asokPendingInits && window._asokPendingInits.length) {
        const pending = window._asokPendingInits.slice();
        window._asokPendingInits = [];
        pending.forEach(function (el) {
          if (document.body.contains(el)) {
            delete el.__asokIniting;
            delete el.__asokWsReady;
            window.Asok._wsInit(el);
          }
        });
      }
      document.querySelectorAll("[data-asok-component]").forEach(setupComponentIsland);
      document.querySelectorAll("[data-subscribe]").forEach(window.Asok._wsSub);
    };

    ws.onmessage = function (e) {
      // SECURITY: Safe JSON parsing with error handling
      const d = window.AsokSecurity && window.AsokSecurity.safeJsonParse ?
        window.AsokSecurity.safeJsonParse(e.data) : JSON.parse(e.data);

      if (!d) {
        console.error('[Asok] Invalid WebSocket message');
        return;
      }

      // SECURITY: Validate message structure
      if (window.AsokSecurity && window.AsokSecurity.validateWsMessage) {
        if (!window.AsokSecurity.validateWsMessage(d)) {
          console.error('[Asok Security] Invalid message structure');
          return;
        }
      }

      if (d.op === "error") {
        console.error('[Asok Live Error]', d.message || d);
        return;
      }

      if (d.op === "render") {
        if (!d.cid || typeof d.cid !== "string" || !/^[a-zA-Z0-9_-]+$/.test(d.cid)) {
          console.warn("[Asok Security] Invalid component ID format in render:", d.cid);
          return;
        }
        const el = document.getElementById("asok-" + d.cid);
        if (el) {
          if (d.registry && typeof d.registry === "object") {
            let code = "";
            for (let h in d.registry) {
              if (
                Object.prototype.hasOwnProperty.call(d.registry, h) &&
                h !== "__proto__" &&
                h !== "constructor" &&
                h !== "prototype"
              ) {
                code += "window.__asok_registry[" + JSON.stringify(h) + "] = (" + d.registry[h] + ");\n";
              }
            }
            if (code) {
              const s = document.createElement("script");
              const nonce = window.Asok?.nonce || document.querySelector('script[nonce]')?.getAttribute('nonce') || '';
              if (nonce) s.nonce = nonce;
              s.textContent = code;
              document.head.appendChild(s);
              s.remove();
            }
          }
          if (d.invalidate_cache) {
            if (window.__asokClearCache) window.__asokClearCache();
          }

          // Component HTML is rendered and HMAC-signed server-side (trusted), so
          // it is NOT run through the untrusted-content sanitizer: doing so would
          // strip legitimate scoped <script>/<style> and embeds and break the
          // scoped-script re-execution below on every live re-render.
          const newEl = new DOMParser().parseFromString(d.html, "text/html").body.firstElementChild;
          if (!newEl) return;
          el.replaceWith(newEl);
          const updated = document.getElementById("asok-" + d.cid);
          if (updated) {
            // Execute nested scripts inside the updated component subtree
            const componentScripts = [];
            if (updated.tagName === 'SCRIPT') {
              componentScripts.push(updated);
            }
            updated.querySelectorAll('script').forEach(function (script) {
              componentScripts.push(script);
            });

            componentScripts.forEach(function (script) {
              if (script.dataset.run || script.id === 'asok-scoped-js') return;
              const newScript = document.createElement('script');
              const nonce = window.Asok?.nonce || document.querySelector('script[nonce]')?.getAttribute('nonce') || '';
              if (nonce) newScript.nonce = nonce;
              if (script.src) newScript.src = script.src;
              newScript.textContent = script.textContent;
              newScript.dataset.run = '1';
              script.parentNode.replaceChild(newScript, script);
            });

            if (window.AsokDirectives && window.AsokDirectives.init) {
              window.AsokDirectives.init(updated);
            }
            initWS(updated, true);
            document.dispatchEvent(
              new CustomEvent("asok:ws-update", {
                detail: { cid: d.cid, name: d.name, state: d.state },
              })
            );
          }
        }
      } else if (d.op === "model_event") {
        document.querySelectorAll("[data-subscribe]").forEach(function (el) {
          const room = el.dataset.subscribe;
          if (room === "model:" + d.model || room === "model:" + d.model + ":" + d.id) {
            if (window.Asok && window.Asok.refresh) {
              window.Asok.refresh(el);
            } else if (typeof fire === "function") {
              fire(el);
            }
          }
        });
      } else if (d.op === "broadcast") {
        document.dispatchEvent(new CustomEvent("asok:ws-broadcast", { detail: d }));
      }
    };

    ws.onclose = function () {
      connecting = false;
      // Exponential backoff with jitter, capped at ~30s, to avoid hammering a
      // downed server with reconnect attempts.
      reconnectAttempts = Math.min(reconnectAttempts + 1, 6);
      const base = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), 30000);
      const delay = base / 2 + Math.random() * (base / 2);
      setTimeout(connect, delay);
    };

    ws.onerror = function () {
      connecting = false;
    };
  }

  function send(msg, el) {
    if (!ws || ws.readyState !== 1) return;
    if (el) el.classList.add("asok-loading");
    ws.send(JSON.stringify(msg));
  }

  function initSub(el) {
    if (el.__asokSubReady) return;
    el.__asokSubReady = true;
    send({ op: "join_room", room: el.dataset.subscribe });
  }

  function getClientStrategy(el) {
    const dataClient = el.getAttribute('data-asok-client');
    if (dataClient) return dataClient.trim();

    if (el.hasAttribute('client:visible') || el.getAttribute('client') === 'visible') return 'visible';
    if (el.hasAttribute('client:idle') || el.getAttribute('client') === 'idle') return 'idle';
    if (el.hasAttribute('client:load') || el.getAttribute('client') === 'load') return 'load';
    if (el.hasAttribute('client:only') || el.getAttribute('client') === 'only') return 'only';
    if (el.hasAttribute('client:none') || el.getAttribute('client') === 'none') return 'none';

    if (el.hasAttribute('client:media')) return 'media:' + el.getAttribute('client:media');

    const clientAttr = el.getAttribute('client');
    if (clientAttr) return clientAttr.trim();

    return null;
  }

  function initWS(el, skipJoin) {
    if (el.__asokIniting) return;
    const strategy = getClientStrategy(el);
    if (strategy && strategy !== 'none') {
      if (!el.__asokReadyToHydrate) return;
    }
    el.__asokIniting = true;
    const cid = el.id.replace("asok-", "");
    if (!cid || !/^[a-zA-Z0-9_-]+$/.test(cid)) {
      delete el.__asokIniting;
      return;
    }
    const base = el.dataset.asokComponent;
    const st = el.dataset.asokState;

    if (!ws || ws.readyState !== 1) {
      if (!window._asokPendingInits) window._asokPendingInits = [];
      window._asokPendingInits.push(el);
      delete el.__asokIniting;
      return;
    }

    if (!skipJoin) {
      send({ op: "join", cid: cid, name: base, state: st });
    }

    ["click", "input", "change", "submit", "keyup", "keydown"].forEach(function (ev) {
      el.querySelectorAll("[ws-" + ev + "]").forEach(function (n) {
        // Guard against stacking duplicate listeners if initWS runs twice on
        // the same node; also lets ws-<ev> and ws-model coexist on one element.
        const flag = "__asokWsBound_" + ev;
        if (n[flag]) return;
        n[flag] = true;
        const attr = n.getAttribute("ws-" + ev);
        const parts = attr.split(".");
        const meth = parts[0];
        const mods = parts.slice(1);

        const handler = function (e) {
          if (mods.includes("prevent")) e.preventDefault();
          if (mods.includes("stop")) e.stopPropagation();
          if (mods.includes("enter") && e.key !== "Enter") return;

          const val = n.value;
          const msg = { op: "call", cid: cid, method: meth, val: val };
          const deb = mods.find(function (m) {
            return m.startsWith("debounce");
          });

          if (deb) {
            const ms = parseInt(deb.split("-")[1]) || 300;
            clearTimeout(timers.get(n));
            timers.set(n, setTimeout(function () {
              send(msg, n);
            }, ms));
          } else {
            send(msg, n);
          }
        };
        n.addEventListener(ev, handler);
      });
    });

    el.querySelectorAll("[ws-model]").forEach(function (n) {
      if (n.__asokWsModelBound) return;
      n.__asokWsModelBound = true;
      const prop = n.getAttribute("ws-model");
      n.addEventListener("input", function () {
        send({ op: "sync", cid: cid, prop: prop, val: n.value }, n);
      });
    });

    el.__asokWsReady = true;
    delete el.__asokIniting;
  }

  const setupComponentIsland = (el) => {
    const strategy = getClientStrategy(el);
    if (!strategy) {
      el.__asokReadyToHydrate = true;
      window.Asok._wsInit(el);
      return;
    }

    if (strategy === 'none') {
      return;
    }

    const doHydrate = () => {
      if (el.__asokReadyToHydrate) return;
      el.__asokReadyToHydrate = true;
      window.Asok._wsInit(el);
    };

    if (strategy === 'visible') {
      if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              doHydrate();
              observer.unobserve(el);
            }
          });
        });
        observer.observe(el);
      } else {
        doHydrate();
      }
    } else if (strategy === 'idle') {
      if ('requestIdleCallback' in window) {
        window.requestIdleCallback(doHydrate, { timeout: 2000 });
      } else {
        setTimeout(doHydrate, 200);
      }
    } else if (strategy === 'load') {
      if (document.readyState === 'complete') {
        doHydrate();
      } else {
        window.addEventListener('load', doHydrate, { once: true });
      }
    } else if (strategy.startsWith('media:')) {
      const query = strategy.replace(/^media:/, '').trim().replace(/^['"]|['"]$/g, '');
      if (query && 'matchMedia' in window) {
        const mql = window.matchMedia(query);
        if (mql.matches) {
          doHydrate();
        } else {
          const handler = (e) => {
            if (e.matches) {
              doHydrate();
              if (mql.removeEventListener) mql.removeEventListener('change', handler);
              else if (mql.removeListener) mql.removeListener(handler);
            }
          };
          if (mql.addEventListener) mql.addEventListener('change', handler);
          else if (mql.addListener) mql.addListener(handler);
        }
      } else {
        doHydrate();
      }
    } else {
      doHydrate();
    }
  };

  window.Asok = window.Asok || {};
  window.Asok._wsInit = initWS;
  window.Asok._wsSub = initSub;

  window.Asok.leaveComponent = function (cid) {
    if (cid && ws && ws.readyState === 1) {
      send({ op: "leave", cid: cid });
    }
  };

  const prevInit = window.Asok.init;
  window.Asok.init = function (root) {
    if (prevInit) prevInit(root);
    const container = root || document;
    if (container.dataset && container.dataset.asokComponent) {
      delete container.__asokWsReady;
      delete container.__asokIniting;
      setupComponentIsland(container);
    }
    if (container.querySelectorAll) {
      container.querySelectorAll("[data-asok-component]").forEach(function (el) {
        delete el.__asokWsReady;
        delete el.__asokIniting;
        setupComponentIsland(el);
      });
      container.querySelectorAll("[data-subscribe]").forEach(initSub);
    }
  };

  window.addEventListener("asok:routechange", function () {
    document.querySelectorAll("[data-asok-component]").forEach(function (el) {
      if (!el.__asokWsReady && !el.__asokIniting) {
        setupComponentIsland(el);
      }
    });
  });

  document.addEventListener("asok:success", function (e) {
    if (e.detail && e.detail.target) {
      const el = e.detail.target;
      // After an outerHTML/replaceWith swap, the event's target is the old
      // (now-detached) element. Only operate on nodes still in the DOM.
      if (document.body.contains(el)) {
        if (el.dataset.asokComponent) setupComponentIsland(el);
        if (el.dataset.subscribe) initSub(el);
      }
      // For child components/subscriptions, always query from body to find
      // the newly-inserted nodes regardless of what the event target was.
      document.body.querySelectorAll("[data-asok-component]").forEach(function (child) {
        if (!child.__asokWsReady && !child.__asokIniting) setupComponentIsland(child);
      });
      document.body.querySelectorAll("[data-subscribe]").forEach(function (child) {
        if (!child.__asokSubReady) initSub(child);
      });
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();
