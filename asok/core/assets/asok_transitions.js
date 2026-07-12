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
        const fragment = document.createRange().createContextualFragment(h);
        const newNodes = Array.from(fragment.childNodes);
        t.replaceWith(fragment);
        return newNodes;
      }
      if (m === "innerHTML") {
        t.innerHTML = h;
        return Array.from(t.childNodes);
      }
      const fragment = document.createRange().createContextualFragment(h);
      const newNodes = Array.from(fragment.childNodes);
      if (m === "beforebegin") {
        t.parentNode.insertBefore(fragment, t);
      } else if (m === "afterbegin") {
        t.insertBefore(fragment, t.firstChild);
      } else if (m === "beforeend") {
        t.appendChild(fragment);
      } else if (m === "afterend") {
        t.parentNode.insertBefore(fragment, t.nextSibling);
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
        // Swap content
        const newNodes = rawSwap(target, html, mode);
        if (callback) callback(newNodes);

        // Clean up leaving classes and apply entering transition
        target.classList.remove("asok-" + type + "-out", "is-leaving");
        target.classList.add("asok-" + type + "-in");

        requestAnimationFrame(() => {
          target.classList.add("is-entering");
          setTimeout(() => {
            target.classList.remove("asok-" + type + "-in", "is-entering");
            if (durationSpecified) target.style.transitionDuration = '';
          }, duration);
        });
      }, duration);
    } else {
      const newNodes = rawSwap(target, html, mode);
      if (callback) callback(newNodes);
    }
  };
})();
