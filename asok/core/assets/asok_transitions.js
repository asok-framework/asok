/*
 *  Asok Transitions - Transitions support for Asok framework
 *  author: Asok Team
 *  license: MIT
 *  version: 2.0.0
*/
(function () {
  window.Asok = window.Asok || {};

  window.Asok.swap = function (target, html, mode, callback) {
    const rawSwap = function (t, h, m) {
      m = m || "innerHTML";
      if (m === "delete") {
        t.remove();
        return [];
      }
      if (m === "none") return [];
      // Content passed to swap is server-rendered (same-origin, trusted), so it
      // is inserted as-is to preserve scoped scripts/styles/forms. Sanitization
      // is reserved for genuinely untrusted values (asok-html, WYSIWYG input).
      if (m === "outerHTML" || m === "replaceWith") {
        // Build nodes via a temp div, insert them before the target, then remove
        // the target — this guarantees newNodes are live in the DOM when returned.
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = h;
        const newNodes = Array.from(tempDiv.childNodes);
        if (t.parentNode) {
          newNodes.forEach(function (node) {
            t.parentNode.insertBefore(node, t);
          });
          t.remove();
        } else {
          // Detached target: best-effort replaceWith
          const fragment = document.createRange().createContextualFragment(h);
          t.replaceWith(fragment);
        }
        return newNodes;
      }
      if (m === "innerHTML") {
        t.innerHTML = h;
        return Array.from(t.childNodes);
      }
      const fragment = document.createRange().createContextualFragment(h);
      const newNodes = Array.from(fragment.childNodes);
      if (m === "beforebegin") {
        if (t.parentNode) t.parentNode.insertBefore(fragment, t);
      } else if (m === "afterbegin") {
        t.insertBefore(fragment, t.firstChild);
      } else if (m === "beforeend") {
        t.appendChild(fragment);
      } else if (m === "afterend") {
        if (t.parentNode) t.parentNode.insertBefore(fragment, t.nextSibling);
      } else {
        t.insertAdjacentHTML(m, h);
      }
      return newNodes;
    };

    if (target.hasAttribute("asok-transition")) {
      const transitionAttr = target.getAttribute("asok-transition") || "fade";
      const parts = transitionAttr.split(" ");
      const type = parts[0];
      // SECURITY: Cap transition duration to prevent DoS
      const rawDuration = parseInt(parts[1]) || 300;
      const duration = window.AsokSecurity && window.AsokSecurity.safeDuration
        ? window.AsokSecurity.safeDuration(rawDuration, 5000)
        : Math.min(rawDuration, 5000);
      // Only override the CSS animation speed when a numeric duration is given,
      // so each effect keeps its own default timing otherwise.
      const durationSpecified = !isNaN(parseInt(parts[1], 10));

      // Start transition out
      if (durationSpecified) target.style.transitionDuration = duration + 'ms';
      target.classList.add("asok-" + type + "-out");
      requestAnimationFrame(() => {
        target.classList.add("is-leaving");
      });

      setTimeout(() => {
        // Swap content — for outerHTML/replaceWith, target is now detached.
        const newNodes = rawSwap(target, html, mode);
        if (callback) callback(newNodes);

        // Apply entering transition to the *inserted* nodes.
        // For innerHTML/insert modes, target is still in the DOM and we animate it.
        // For outerHTML/replaceWith, target is detached — animate the new nodes instead.
        const isReplaceMode = (mode === 'outerHTML' || mode === 'replaceWith');
        const transitionTargets = isReplaceMode ? newNodes : [target];

        transitionTargets.forEach(function (el) {
          if (!el || !el.classList) return;
          el.classList.remove("asok-" + type + "-out", "is-leaving");
          el.classList.add("asok-" + type + "-in");
          requestAnimationFrame(() => {
            el.classList.add("is-entering");
            setTimeout(() => {
              el.classList.remove("asok-" + type + "-in", "is-entering");
              if (durationSpecified) el.style.transitionDuration = '';
            }, duration);
          });
        });
      }, duration);
    } else {
      const newNodes = rawSwap(target, html, mode);
      if (callback) callback(newNodes);
    }
  };
})();

