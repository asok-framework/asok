window.asokWS = function (path) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  let host = location.hostname + ":" + (window.ASOK_WS_PORT || 8001);
  if (
    location.hostname !== "localhost" &&
    location.hostname !== "127.0.0.1" &&
    location.hostname !== "0.0.0.0" &&
    !location.hostname.startsWith("192.168.")
  ) {
    host = location.host + "/ws";
  }
  return new WebSocket(protocol + "//" + host + path);
};

(function () {
  let ws;
  const timers = {};
  let connecting = false;

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
      document.querySelectorAll("[data-asok-component]").forEach(window.Asok._wsInit);
      document.querySelectorAll("[data-subscribe]").forEach(window.Asok._wsSub);
    };

    ws.onmessage = function (e) {
      const d = JSON.parse(e.data);
      if (d.op === "render") {
        const el = document.getElementById("asok-" + d.cid);
        if (el) {
          if (d.registry) {
            let code = "";
            for (let h in d.registry) {
              code += "window.__asok_registry[" + JSON.stringify(h) + "] = (" + d.registry[h] + ");\n";
            }
            const s = document.createElement("script");
            s.nonce = window.Asok.nonce;
            s.textContent = code;
            document.head.appendChild(s);
            s.remove();
          }
          if (d.invalidate_cache) {
            if (window.__asokClearCache) window.__asokClearCache();
          }
          const newEl = new DOMParser().parseFromString(d.html, "text/html").body.firstElementChild;
          el.replaceWith(newEl);
          const updated = document.getElementById("asok-" + d.cid);
          if (updated) {
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
      setTimeout(connect, 2000);
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
            clearTimeout(timers[n]);
            timers[n] = setTimeout(function () {
              send(msg, n);
            }, ms);
          } else {
            send(msg, n);
          }
        };
        n["on" + ev] = handler;
      });
    });

    el.querySelectorAll("[ws-model]").forEach(function (n) {
      const prop = n.getAttribute("ws-model");
      n.oninput = function () {
        send({ op: "sync", cid: cid, prop: prop, val: n.value }, n);
      };
    });

    el.__asokWsReady = true;
    delete el.__asokIniting;
  }

  window.Asok = window.Asok || {};
  window.Asok._wsInit = initWS;
  window.Asok._wsSub = initSub;

  document.addEventListener("asok:success", function (e) {
    if (e.detail && e.detail.target) {
      const el = e.detail.target;
      if (el.dataset.asokComponent) initWS(el);
      if (el.dataset.subscribe) initSub(el);
      el.querySelectorAll("[data-asok-component]").forEach(initWS);
      el.querySelectorAll("[data-subscribe]").forEach(initSub);
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();
