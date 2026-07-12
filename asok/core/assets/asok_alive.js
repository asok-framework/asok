window.asokWS = function (path) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  let host;

  // SECURITY: Only allow configurable port in development (localhost)
  if (
    location.hostname === "localhost" ||
    location.hostname === "127.0.0.1" ||
    location.hostname === "0.0.0.0" ||
    location.hostname.startsWith("192.168.")
  ) {
    const port = window.ASOK_WS_PORT || 8001;
    // SECURITY: Validate port range to prevent hijacking
    if (window.AsokSecurity && window.AsokSecurity.isValidPort) {
      if (!window.AsokSecurity.isValidPort(port)) {
        console.error('[Asok Security] Invalid WebSocket port:', port);
        throw new Error('Invalid WebSocket port configuration');
      }
    }
    host = location.hostname + ":" + port;
  } else {
    // Production: always use same host
    host = location.host + "/ws";
  }

  return new WebSocket(protocol + "//" + host + path);
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
      if (ws.readyState === 1) ws.close(); // OPEN
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

      if (d.op === "render") {
        const el = document.getElementById("asok-" + d.cid);
        if (el) {
          if (d.registry) {
            let code = "";
            for (let h in d.registry) {
              code += "window.__asok_registry[" + JSON.stringify(h) + "] = (" + d.registry[h] + ");\n";
            }
            const s = document.createElement("script");
            const nonce = window.Asok?.nonce || document.querySelector('script[nonce]')?.getAttribute('nonce') || '';
            if (nonce) s.nonce = nonce;
            s.textContent = code;
            document.head.appendChild(s);
            s.remove();
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

  function initWS(el, skipJoin) {
    if (el.__asokIniting) return;
    if (el.hasAttribute('client:visible') || el.hasAttribute('client:idle') || el.hasAttribute('client:load')) {
      if (!el.__asokReadyToHydrate) return;
    }
    el.__asokIniting = true;
    const cid = el.id.replace("asok-", "");
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
    if (el.hasAttribute('client:visible')) {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            el.__asokReadyToHydrate = true;
            window.Asok._wsInit(el);
            observer.unobserve(el);
          }
        });
      });
      observer.observe(el);
    } else if (el.hasAttribute('client:idle')) {
      const run = () => {
        el.__asokReadyToHydrate = true;
        window.Asok._wsInit(el);
      };
      if (window.requestIdleCallback) {
        window.requestIdleCallback(run, { timeout: 2000 });
      } else {
        setTimeout(run, 100);
      }
    } else {
      el.__asokReadyToHydrate = true;
      window.Asok._wsInit(el);
    }
  };

  window.Asok = window.Asok || {};
  window.Asok._wsInit = initWS;
  window.Asok._wsSub = initSub;

  document.addEventListener("asok:success", function (e) {
    if (e.detail && e.detail.target) {
      const el = e.detail.target;
      if (el.dataset.asokComponent) setupComponentIsland(el);
      if (el.dataset.subscribe) initSub(el);
      el.querySelectorAll("[data-asok-component]").forEach(setupComponentIsland);
      el.querySelectorAll("[data-subscribe]").forEach(initSub);
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();
