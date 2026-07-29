; (function () {
  // Map of DOM elements to their Asok Context (state, refs, cleanup list, etc.)
  const contexts = new WeakMap();
  // Map of state property names to the Set of elements subscribing to them
  const dependencyMap = new Map();
  // The element currently being evaluated, for reactive dependency tracking
  let currentSubscriber = null;

  // Global store reactive proxy
  const globalStore = new Proxy({}, {
    get(target, prop) {
      if (currentSubscriber && !prop.startsWith('_')) {
        if (!dependencyMap.has(prop)) {
          dependencyMap.set(prop, new Set());
        }
        dependencyMap.get(prop).add(currentSubscriber);
      }
      return target[prop];
    },
    set(target, prop, value) {
      if (target[prop] === value) return true;
      target[prop] = value;
      if (dependencyMap.has(prop)) {
        dependencyMap.get(prop).forEach(el => {
          if (!document.body.contains(el)) {
            dependencyMap.get(prop).delete(el);
            return;
          }
          const ctx = contexts.get(el);
          if (ctx) {
            updateScope(el);
          }
        });
      }
      return true;
    }
  });

  // Find the nearest ancestor element that defines/holds Asok state
  const findStateOwner = (el) => {
    while (el && el !== document.documentElement) {
      if (contexts.has(el)) return el;
      el = el.parentElement;
    }
    return null;
  };

  // Build arguments to inject into the executed directive functions
  const getArgumentsForExpression = (state, el, event) => {
    const owner = findStateOwner(el);
    const ctx = owner ? contexts.get(owner) : { refs: {} };
    const localState = ctx.state || state;

    return [
      localState,
      window.Asok.store,
      el,
      event,
      ctx.refs || {},
      (callback) => Promise.resolve().then(callback) // nextTick implementation
    ];
  };

  // Evaluate a standard compiled expression function
  const evaluateExpression = (ref, state, el) => {
    const fn = (window.__asok_registry || {})[ref];
    if (!fn) return;
    try {
      return fn(...getArgumentsForExpression(state, el));
    } catch (e) {
      console.error("Asok evaluation error:", e);
    }
  };

  // Evaluate an event compiled expression function
  const executeEventExpression = (ref, state, event, el) => {
    const fn = (window.__asok_registry || {})[ref];
    if (!fn) return;
    try {
      return fn(...getArgumentsForExpression(state, el, event));
    } catch (e) {
      console.error("Asok event execution error:", e);
    }
  };

  // Helper to handle built-in and custom CSS transitions for showing/hiding elements
  const applyTransition = (el, show, callback) => {
    const transitionAttr = el.getAttribute('asok-transition');
    if (transitionAttr === null) {
      if (callback) callback();
      return;
    }

    const tokens = transitionAttr.trim().split(/\s+/);
    let enterName = 'fade';
    let enterDuration = 300;
    let leaveName = 'fade';
    let leaveDuration = 300;

    if (tokens.length > 0) {
      enterName = tokens[0];
      leaveName = tokens[0];
    }

    if (tokens.length > 1) {
      const t1 = parseInt(tokens[1]);
      if (!isNaN(t1)) {
        enterDuration = t1;
        leaveDuration = t1;
        if (tokens.length > 2) {
          const t2 = parseInt(tokens[2]);
          if (!isNaN(t2)) {
            leaveDuration = t2;
          } else {
            leaveName = tokens[2];
            if (tokens.length > 3) {
              const t3 = parseInt(tokens[3]);
              if (!isNaN(t3)) {
                leaveDuration = t3;
              }
            }
          }
        }
      } else {
        leaveName = tokens[1];
        if (tokens.length > 2) {
          const t2 = parseInt(tokens[2]);
          if (!isNaN(t2)) {
            enterDuration = t2;
            leaveDuration = t2;
          }
        }
        if (tokens.length > 3) {
          const t3 = parseInt(tokens[3]);
          if (!isNaN(t3)) {
            leaveDuration = t3;
          }
        }
      }
    }

    // Whether the author explicitly provided a numeric duration token. Only then
    // do we override the CSS animation speed, so each effect keeps its own default.
    const durationSpecified = tokens.slice(1).some(t => !isNaN(parseInt(t, 10)));
    const activeName = show ? enterName : leaveName;
    const activeDuration = show ? enterDuration : leaveDuration;

    const builtIns = [
      'fade', 'slide', 'scale', 'fly', 'blur', 'bounce', 'page',
      'slide-left', 'slide-right', 'slide-up', 'slide-down'
    ];
    if (builtIns.includes(activeName) || activeName.startsWith('asok-')) {
      let baseName = activeName;
      if (activeName.startsWith('asok-')) {
        baseName = activeName.replace('asok-', '').replace('-in', '').replace('-out', '');
      }

      if (show) {
        el.classList.add(`asok-${baseName}-in`);
        if (callback) callback();
        el.offsetHeight; // Force reflow
        requestAnimationFrame(() => {
          el.classList.add('is-entering');
          // SECURITY: Validate and cap duration to prevent timing attacks
          const safeDur = window.AsokSecurity && window.AsokSecurity.safeDuration ?
            window.AsokSecurity.safeDuration(activeDuration, 5000) : Math.min(activeDuration, 5000);
          if (durationSpecified) el.style.transitionDuration = safeDur + 'ms';
          setTimeout(() => {
            el.classList.remove(`asok-${baseName}-in`, 'is-entering');
            if (durationSpecified) el.style.transitionDuration = '';
          }, safeDur);
        });
      } else {
        el.classList.add(`asok-${baseName}-out`);
        el.offsetHeight; // Force reflow
        requestAnimationFrame(() => {
          el.classList.add('is-leaving');
          // SECURITY: Validate and cap duration to prevent timing attacks
          const safeDur = window.AsokSecurity && window.AsokSecurity.safeDuration ?
            window.AsokSecurity.safeDuration(activeDuration, 5000) : Math.min(activeDuration, 5000);
          if (durationSpecified) el.style.transitionDuration = safeDur + 'ms';
          setTimeout(() => {
            if (callback) callback();
            el.classList.remove(`asok-${baseName}-out`, 'is-leaving');
            if (durationSpecified) el.style.transitionDuration = '';
          }, safeDur);
        });
      }
    } else {
      // Custom Tailwind/CSS classes transition fallback
      if (show) {
        if (callback) callback();
        if (tokens.length) {
          el.classList.add(...tokens);
          el.addEventListener('transitionend', () => el.classList.remove(...tokens), { once: true });
        }
      } else {
        if (tokens.length) {
          el.classList.add(...tokens);
          el.addEventListener('transitionend', () => {
            if (callback) callback();
            el.classList.remove(...tokens);
          }, { once: true });
        } else {
          if (callback) callback();
        }
      }
    }
  };

  // Update DOM bindings for standard directives (text, html, show, class, bind)
  const updateBindings = (el, state) => {
    if (!el || !state) return;

    const getAttr = el.getAttribute.bind(el);

    // asok-text
    if (el.hasAttribute('asok-text-ref')) {
      const val = evaluateExpression(getAttr('asok-text-ref'), state, el);
      if (val !== undefined) {
        el.textContent = String(val);
      }
    }

    // asok-html
    if (el.hasAttribute('asok-html-ref')) {
      const val = evaluateExpression(getAttr('asok-html-ref'), state, el);
      if (val !== undefined) {
        // SECURITY: sanitize untrusted HTML. Prefer inserting sanitized *nodes*
        // (single parse) over re-serializing to a string, which avoids mutation-XSS.
        if (window.AsokSecurity && window.AsokSecurity.sanitizeToFragment) {
          el.replaceChildren(window.AsokSecurity.sanitizeToFragment(String(val)));
        } else if (window.AsokSecurity && window.AsokSecurity.sanitizeHtml) {
          el.innerHTML = window.AsokSecurity.sanitizeHtml(String(val));
        } else {
          // Fallback: use textContent for safety if security utils not loaded
          el.textContent = String(val);
        }
      }
    }

    // asok-show
    if (el.hasAttribute('asok-show-ref')) {
      const isShown = evaluateExpression(getAttr('asok-show-ref'), state, el);
      if (!el._asokShowInitialized) {
        el._asokShowInitialized = true;
        // Override the visibility:hidden set by anti-FOUC critical CSS.
        // The [asok-show-ref] attribute stays on the element, so the stylesheet
        // rule keeps matching; clearing the inline style ('') is not enough —
        // we must set an inline 'visible' to win over the stylesheet.
        el.style.visibility = 'visible';
        el.style.display = isShown ? '' : 'none';
      } else {
        const isCurrentlyShown = el.style.display !== 'none';
        if (isShown) {
          if (!isCurrentlyShown || el.hasAttribute('data-hide-active')) {
            el._showStartTime = Date.now();
            el.removeAttribute('data-hide-active');
            el.setAttribute('data-show-active', '');
            applyTransition(el, true, () => {
              el.style.display = '';
            });
          }
        } else {
          if (isCurrentlyShown || el.hasAttribute('data-show-active')) {
            el.removeAttribute('data-show-active');
            el.setAttribute('data-hide-active', '');
            applyTransition(el, false, () => {
              el.style.display = 'none';
              el.removeAttribute('data-hide-active');
            });
          }
        }
      }
    }

    // asok-hide
    if (el.hasAttribute('asok-hide-ref')) {
      const isHidden = evaluateExpression(getAttr('asok-hide-ref'), state, el);
      if (!el._asokHideInitialized) {
        el._asokHideInitialized = true;
        // Override the visibility:hidden set by anti-FOUC critical CSS.
        // The [asok-hide-ref] attribute stays on the element, so the stylesheet
        // rule keeps matching; clearing the inline style ('') is not enough —
        // we must set an inline 'visible' to win over the stylesheet.
        el.style.visibility = 'visible';
        el.style.display = isHidden ? 'none' : '';
      } else {
        const isCurrentlyHidden = el.style.display === 'none';
        if (!isHidden) {
          if (isCurrentlyHidden || el.hasAttribute('data-hide-active')) {
            el.removeAttribute('data-hide-active');
            el.setAttribute('data-show-active', '');
            applyTransition(el, true, () => {
              el.style.display = '';
            });
          }
        } else {
          if (!isCurrentlyHidden || el.hasAttribute('data-show-active')) {
            el.removeAttribute('data-show-active');
            el.setAttribute('data-hide-active', '');
            applyTransition(el, false, () => {
              el.style.display = 'none';
              el.removeAttribute('data-hide-active');
            });
          }
        }
      }
    }

    // Dynamic classes and attributes binding
    Array.from(el.attributes).forEach(attr => {
      // asok-class
      if (attr.name === 'asok-class-ref') {
        const val = evaluateExpression(attr.value, state, el);
        if (typeof val === 'string') {
          const prevClasses = (el._asokPrevClasses || '').split(' ').filter(c => c);
          const currClasses = val.split(' ').filter(c => c);
          prevClasses.forEach(c => {
            if (!currClasses.includes(c)) el.classList.remove(c);
          });
          currClasses.forEach(c => el.classList.add(c));
          el._asokPrevClasses = val;
        } else if (typeof val === 'object' && val) {
          Object.keys(val).forEach(key => {
            const list = key.split(' ').filter(c => c);
            list.forEach(c => el.classList[val[key] ? 'add' : 'remove'](c));
          });
        }
      }

      // asok-class:name
      if (attr.name.startsWith('asok-class-ref:')) {
        const className = attr.name.substring(15);
        const shouldAdd = evaluateExpression(attr.value, state, el);
        el.classList[shouldAdd ? 'add' : 'remove'](className);
      }

      // asok-bind:name
      if (attr.name.startsWith('asok-bind-ref:')) {
        const attrName = attr.name.substring(14);

        // SECURITY: Validate attribute name to prevent event handler injection
        if (window.AsokSecurity && !window.AsokSecurity.isSafeAttribute(attrName)) {
          console.warn('[Asok] Blocked unsafe attribute binding:', attrName);
          return;
        }

        const val = evaluateExpression(attr.value, state, el);
        const isTruthy = val !== undefined && val !== null && val !== false;
        if (isTruthy) {
          const strVal = String(val);

          // SECURITY: Validate URLs in href/src attributes
          if ((attrName === 'href' || attrName === 'src') &&
            window.AsokSecurity && !window.AsokSecurity.isSafeUrl(strVal)) {
            console.warn('[Asok] Blocked unsafe URL in attribute:', attrName);
            return;
          }

          el.setAttribute(attrName, strVal);
        } else {
          el.removeAttribute(attrName);
        }
        if (attrName === 'checked' && (el.type === 'checkbox' || el.type === 'radio')) {
          el.checked = !!isTruthy;
        }
      }
    });
  };

  // Update asok-if, asok-elif, asok-else directives
  const updateIfDirective = (el, state) => {
    const chain = [el];
    let sibling = el.nextElementSibling;
    while (sibling) {
      if (sibling.tagName === 'TEMPLATE') {
        if (sibling.hasAttribute('asok-if-ref')) break;
        if (sibling.hasAttribute('asok-elif-ref') || sibling.hasAttribute('asok-else')) {
          chain.push(sibling);
        }
      }
      sibling = sibling.nextElementSibling;
    }

    let conditionMet = 0;
    chain.forEach(item => {
      item._ai = 1; // Mark as initialized
      let val = item.hasAttribute('asok-else')
        ? !conditionMet
        : evaluateExpression(item.getAttribute(item.hasAttribute('asok-if-ref') ? 'asok-if-ref' : 'asok-elif-ref'), state, item);

      if (val && !conditionMet) {
        if (!item._n) {
          const fragment = item.content.cloneNode(true);
          const child = fragment.firstElementChild;
          // Guard: only insert (and track) if the template is still in the DOM.
          // If parentNode is null the template was removed by an outerHTML swap;
          // skip silently so item._n stays null and won't become an orphan.
          if (item.parentNode) {
            item._n = child;
            item.parentNode.insertBefore(fragment, item.nextSibling);
            contexts.set(item._n, contexts.get(el) || { state: state, refs: {} });
            if (window.Asok && window.Asok.init) window.Asok.init(item._n); else init(item._n);
          }
        }
        conditionMet = 1;
      } else if (item._n) {
        item._n.remove();
        item._n = null;
      }
    });
  };

  // Evaluate the asok-key expression for one item. The compiled registry fn uses
  // `with($||{})`, so passing a scope object exposing the loop var/index resolves
  // expressions like "item.id". Falls back to the positional index if unavailable.
  const evalForKey = (keyRef, itemVar, indexVar, item, index, el) => {
    const fn = (window.__asok_registry || {})[keyRef];
    if (!fn) return index;
    try {
      return fn(
        { [itemVar]: item, [indexVar]: index },
        window.Asok.store, el, null, {},
        (cb) => Promise.resolve().then(cb)
      );
    } catch (e) {
      return index;
    }
  };

  // Keyed reconciliation for asok-for (opt-in via asok-key). Reuses and reorders
  // existing DOM nodes by key instead of destroying/rebuilding the whole list —
  // this preserves input focus, running transitions and nested component state.
  const updateForKeyed = (el, state, items, itemVar, indexVar, keyRef) => {
    const oldByKey = el._keyed || new Map();
    const newChildren = [];
    const newByKey = new Map();
    const seen = new Set();

    items.forEach((item, index) => {
      const key = String(evalForKey(keyRef, itemVar, indexVar, item, index, el));
      let child = oldByKey.get(key);
      if (child && !seen.has(key)) {
        // Reuse: mutate the raw scope in place (bypassing the reactive proxy, so
        // no re-entrant updateScope), then refresh this child's bindings below.
        if (child.__asokScope) {
          child.__asokScope[itemVar] = item;
          child.__asokScope[indexVar] = index;
        }
        child.__asokReused = 1;
      } else {
        const fragment = el.content.cloneNode(true);
        child = fragment.firstElementChild;
        if (!child) return;
        const rawScope = { [itemVar]: item, [indexVar]: index };
        const subState = createReactiveProxy(rawScope, () => updateScope(findStateOwner(el)), state);
        child.__asokScope = rawScope;
        contexts.set(child, { state: subState, refs: {}, cleanup: [] });
        child.__asokNew = 1;
      }
      newChildren.push(child);
      newByKey.set(key, child);
      seen.add(key);
    });

    // Remove children whose key disappeared (cleaning up their listeners first).
    const newSet = new Set(newChildren);
    (el._children || []).forEach(child => {
      if (!newSet.has(child)) {
        cleanupOld(child);
        child.remove();
      }
    });

    // Reorder: lay children out in the new order, ending right before the marker.
    // Walk back-to-front so each node lands immediately before the next one;
    // skip nodes already in place to avoid needless DOM moves.
    let anchor = el._marker;
    for (let i = newChildren.length - 1; i >= 0; i--) {
      const child = newChildren[i];
      if (child.nextSibling !== anchor && el._marker.parentNode) {
        el._marker.parentNode.insertBefore(child, anchor);
      }
      anchor = child;
    }

    // Init freshly created children (now connected); refresh reused ones in place.
    newChildren.forEach(child => {
      if (child.__asokNew) {
        delete child.__asokNew;
        if (window.Asok && window.Asok.init) window.Asok.init(child); else init(child);
      } else if (child.__asokReused) {
        delete child.__asokReused;
        updateScope(child, 0);
      }
    });

    el._children = newChildren;
    el._keyed = newByKey;
  };

  // Update asok-for loops
  const updateForDirective = (el, state) => {
    el._ai = 1; // Mark as initialized
    const ref = el.getAttribute('asok-for-ref');
    const varName = el.getAttribute('asok-for-var');
    const keyRef = el.getAttribute('asok-key-ref');
    const items = evaluateExpression(ref, state, el) || [];
    let itemsJSON;
    try {
      itemsJSON = JSON.stringify(items);
    } catch (e) {
      itemsJSON = 'circular-' + Date.now();
    }

    if (el._lastItems === itemsJSON) return;
    el._lastItems = itemsJSON;

    let itemVar = varName;
    let indexVar = 'index';
    if (itemVar.startsWith('(') && itemVar.endsWith(')')) {
      const parts = itemVar.slice(1, -1).split(',').map(s => s.trim());
      itemVar = parts[0];
      if (parts.length > 1) indexVar = parts[1];
    }

    if (!el._marker) {
      el._marker = document.createComment('for');
      // Guard: el may be detached after an outerHTML swap of its parent
      if (el.parentNode) {
        el.parentNode.insertBefore(el._marker, el.nextSibling);
      }
    }

    // If the template is detached, skip rendering children
    if (!el.parentNode && !el._marker.parentNode) return;

    // Opt-in keyed path: reuse/reorder nodes by key instead of full rebuild.
    if (keyRef) {
      updateForKeyed(el, state, items, itemVar, indexVar, keyRef);
      return;
    }

    (el._children || []).forEach(child => child.remove());
    el._children = [];

    items.forEach((item, index) => {
      const fragment = el.content.cloneNode(true);
      const child = fragment.firstElementChild;
      const subState = createReactiveProxy({ [itemVar]: item, [indexVar]: index }, () => updateScope(findStateOwner(el)), state);

      contexts.set(child, { state: subState, refs: {}, cleanup: [] });
      if (el._marker.parentNode) {
        el._marker.parentNode.insertBefore(fragment, el._marker);
      } else if (el.parentNode) {
        el.parentNode.insertBefore(fragment, el._marker);
      }
      el._children.push(child);
      if (window.Asok && window.Asok.init) window.Asok.init(child); else init(child);
    });
  };

  // Re-run directives inside a scope when reactive state changes
  const updateScope = (scope, isRootCall = 1) => {
    const ctx = contexts.get(scope);
    if (!ctx) return;

    currentSubscriber = scope;
    if (scope.tagName === 'TEMPLATE') {
      if (scope.hasAttribute('asok-if-ref')) updateIfDirective(scope, ctx.state);
      if (scope.hasAttribute('asok-for-ref')) updateForDirective(scope, ctx.state);
      if (scope._n) updateScope(scope._n, 0);
      if (scope._children) scope._children.forEach(n => updateScope(n, 0));
      currentSubscriber = null;
      return;
    }

    updateBindings(scope, ctx.state);
    scope.querySelectorAll('*').forEach(el => {
      if (el._updateValue) el._updateValue();
      if (el.tagName === 'TEMPLATE') {
        let parent = el.parentElement;
        while (parent && parent !== scope) {
          if (parent && parent.hasAttribute('asok-state-ref')) return;
          parent = parent.parentElement;
        }
        const owner = findStateOwner(el);
        const ownerState = owner ? contexts.get(owner).state : ctx.state;
        if (el.hasAttribute('asok-if-ref')) updateIfDirective(el, ownerState);
        if (el.hasAttribute('asok-for-ref')) updateForDirective(el, ownerState);
        return;
      }

      let parent = el.parentElement;
      while (parent && parent !== scope) {
        if (parent && parent.hasAttribute('asok-state-ref')) return;
        parent = parent.parentElement;
      }

      const owner = findStateOwner(el);
      if (owner) {
        updateBindings(el, contexts.get(owner).state);
      }
    });

    currentSubscriber = null;
    if (isRootCall && ctx._teleportedScopes) {
      ctx._teleportedScopes.forEach(t => updateScope(t, 0));
    }
  };

  // Create reactive proxy wrapper around state objects
  const createReactiveProxy = (obj, onChange, parentState) => {
    if (!obj || typeof obj !== 'object' || obj._isProxy) return obj;

    return new Proxy(obj, {
      get(target, prop) {
        if (prop === '_isProxy') return true;
        const val = (prop in target) ? target[prop] : (parentState ? parentState[prop] : undefined);
        if (typeof val === 'function') {
          if (['push', 'pop', 'splice', 'shift', 'unshift', 'reverse', 'sort'].includes(prop)) {
            return (...args) => {
              const res = val.apply(target, args);
              onChange();
              return res;
            };
          }
          return val.bind(target);
        }
        return createReactiveProxy(val, onChange, parentState);
      },
      has(target, prop) {
        return prop in target || (parentState && prop in parentState);
      },
      set(target, prop, value) {
        if (prop in target) {
          if (target[prop] === value) return true;
          target[prop] = value;
          onChange();
          return true;
        }
        if (parentState && prop in parentState) {
          parentState[prop] = value;
          return true;
        }
        target[prop] = value;
        onChange();
        return true;
      }
    });
  };

  // Initialize a state scope
  const initState = (el) => {
    if (el._stateInitialized) return;
    const ref = el.getAttribute('asok-state-ref');
    try {
      const rawState = evaluateExpression(ref, {}, el) || {};
      const proxyState = createReactiveProxy(rawState, () => updateScope(el));
      contexts.set(el, { state: proxyState, cleanup: [], refs: {}, _teleportedScopes: [] });
      el._stateInitialized = 1;

      if (el.hasAttribute('asok-init-ref')) {
        executeEventExpression(el.getAttribute('asok-init-ref'), proxyState, null, el);
      }
      updateScope(el);
    } catch (e) {
      console.error("Asok state initialization error:", e);
    }
  };

  // Two-way binding for asok-model
  const initModel = (el) => {
    if (el._modelInitialized) return;
    const modelAttr = el.getAttribute('asok-model');
    const owner = findStateOwner(el);
    if (!modelAttr || !owner) return;

    const state = contexts.get(owner).state;
    el._modelInitialized = 1;

    const getValue = (obj, path) => path.replace(/\[([^\]]+)\]/g, '.$1').split('.').reduce((acc, k) => acc && acc[k], obj);
    const setValue = (obj, path, val) => {
      const keys = path.replace(/\[([^\]]+)\]/g, '.$1').split('.');
      const lastKey = keys.pop();
      const target = keys.reduce((acc, x) => acc[x] = acc[x] || {}, obj);
      target[lastKey] = val;
    };

    el._updateValue = () => {
      const val = getValue(state, modelAttr);
      const displayVal = (val !== undefined && val !== null) ? val : '';
      if (el.value !== String(displayVal)) {
        if (el.type === 'checkbox') {
          el.checked = !!displayVal;
        } else if (el.type === 'radio') {
          el.checked = el.value === displayVal;
        } else {
          const isFocused = document.activeElement === el;
          if (isFocused && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) {
            let hasSelection = false;
            let selectionStart, selectionEnd;
            try {
              selectionStart = el.selectionStart;
              selectionEnd = el.selectionEnd;
              hasSelection = typeof selectionStart === 'number' && typeof selectionEnd === 'number';
            } catch (e) { }

            el.value = displayVal;

            if (hasSelection) {
              try {
                el.setSelectionRange(selectionStart, selectionEnd);
              } catch (e) { }
            }
            try { el.focus(); } catch (e) { }
          } else {
            el.value = displayVal;
          }
        }
      }
    };

    el._updateValue();
    const handleInput = () => {
      if (el.type === 'checkbox') setValue(state, modelAttr, el.checked);
      else if (el.type === 'radio') {
        if (el.checked) setValue(state, modelAttr, el.value);
      } else {
        setValue(state, modelAttr, el.value);
      }
    };

    el.addEventListener('input', handleInput);
    el.addEventListener('change', handleInput);

    contexts.get(owner).cleanup.push(() => {
      el.removeEventListener('input', handleInput);
      el.removeEventListener('change', handleInput);
    });
  };

  // Event handlers (asok-on)
  const initEvents = (el) => {
    if (el._eventsInitialized) return;
    const owner = findStateOwner(el);
    if (!owner) return;

    const state = contexts.get(owner).state;
    el._eventsInitialized = 1;

    Array.from(el.attributes).forEach(attr => {
      if (!attr.name.startsWith('asok-on-ref:')) return;

      const eventName = attr.name.substring(12);
      const ref = attr.value;
      const [eventBase, ...modifiers] = eventName.split('.');

      const handler = (e) => {
        if (modifiers.includes('prevent')) e.preventDefault();
        if (modifiers.includes('stop')) e.stopPropagation();

        const hasKeyMod = modifiers.some(m => ['enter', 'escape', 'space', 'tab'].includes(m));
        if (hasKeyMod) {
          const keyMatches = modifiers.some(m => {
            const key = e.key.toLowerCase();
            if (m === 'space') return key === ' ' || key === 'spacebar';
            return key === m;
          });
          if (!keyMatches) return;
        }

        executeEventExpression(ref, state, e, el);
      };

      if (modifiers.includes('outside')) {
        const outsideHandler = (e) => {
          if (el.offsetWidth > 0 && !el.contains(e.target) && (!el._showStartTime || Date.now() - el._showStartTime > 50)) {
            handler(e);
          }
        };
        document.addEventListener('click', outsideHandler);
        contexts.get(owner).cleanup.push(() => document.removeEventListener('click', outsideHandler));
      } else {
        const debounceMod = modifiers.find(m => m.startsWith('debounce'));
        const delay = debounceMod ? parseInt(debounceMod.split('-')[1]) || 300 : 0;

        if (delay) {
          let timeoutId;
          const debouncedHandler = (e) => {
            clearTimeout(timeoutId);
            timeoutId = setTimeout(() => handler(e), delay);
          };
          el.addEventListener(eventBase, debouncedHandler);
          contexts.get(owner).cleanup.push(() => el.removeEventListener(eventBase, debouncedHandler));
        } else {
          el.addEventListener(eventBase, handler);
          contexts.get(owner).cleanup.push(() => el.removeEventListener(eventBase, handler));
        }
      }
    });
  };

  // Declarative fetch (asok-fetch)
  const initFetch = (el) => {
    if (el._fetchInitialized) return;
    const url = el.getAttribute('asok-fetch');
    const targetProp = el.getAttribute('asok-fetch-as') || 'data';
    const trigger = el.getAttribute('asok-fetch-on') || 'load';
    const owner = findStateOwner(el);
    if (!url || !owner) return;

    const state = contexts.get(owner).state;
    el._fetchInitialized = 1;

    const doFetch = async () => {
      try {
        state.loading = true;
        state.error = null;
        const res = await fetch(url);
        if (!res.ok) throw new Error(res.statusText);
        const data = await res.json();
        state[targetProp] = data;
        state.loading = false;
      } catch (e) {
        state.error = e.message;
        state.loading = false;
      }
    };

    if (trigger === 'load') {
      doFetch();
    } else {
      const handler = () => doFetch();
      el.addEventListener(trigger, handler);
      contexts.get(owner).cleanup.push(() => el.removeEventListener(trigger, handler));
    }
  };

  // Async action fetching (asok-fetch-async)
  const initFetchAsync = (el) => {
    if (el._fetchAsyncInitialized) return;
    const ref = el.getAttribute('asok-fetch-async-ref');
    const trigger = el.getAttribute('asok-fetch-on') || 'click';
    const owner = findStateOwner(el);
    if (!ref || !owner) return;

    const state = contexts.get(owner).state;
    el._fetchAsyncInitialized = 1;

    const doAsync = async () => {
      try {
        state.loading = true;
        state.error = null;
        await executeEventExpression(ref, state, null, el);
        state.loading = false;
      } catch (e) {
        state.error = e.message;
        state.loading = false;
      }
    };

    if (trigger === 'load') {
      doAsync();
    } else {
      const handler = () => doAsync();
      el.addEventListener(trigger, handler);
      contexts.get(owner).cleanup.push(() => el.removeEventListener(trigger, handler));
    }
  };

  // Clean up references and event listeners
  const cleanupOld = (root) => {
    if (!root) return;
    const elements = [root, ...root.querySelectorAll('*')];
    elements.forEach(el => {
      // Collect keys to delete after the forEach to avoid mutating the Map
      // during iteration (non-deterministic on some older JS engines).
      const keysToDelete = [];
      dependencyMap.forEach((set, key) => {
        set.delete(el);
        if (set.size === 0) {
          keysToDelete.push(key);
        }
      });
      keysToDelete.forEach(key => dependencyMap.delete(key));
      const ctx = contexts.get(el);
      if (ctx && ctx.cleanup) {
        ctx.cleanup.forEach(fn => {
          try {
            fn();
          } catch (e) { }
        });
        ctx.cleanup = [];
      }
    });
  };

  // Reset all state tags
  const resetFlags = (root) => {
    if (!root) return;
    const elements = [root, ...root.querySelectorAll('*')];
    elements.forEach(el => {
      delete el._ai;
      delete el._stateInitialized;
      delete el._modelInitialized;
      delete el._eventsInitialized;
      delete el._refInitialized;
      delete el._teleportInitialized;
      delete el._fetchInitialized;
      delete el._fetchAsyncInitialized;
      delete el._updateValue;
      delete el._asokPrevClasses;
      delete el._asokShowInitialized;
      delete el._asokHideInitialized;
    });
  };

  const forceInit = (root) => {
    if (!root) return;
    cleanupOld(root);
    resetFlags(root);
    init(root);
  };

  const isInsideUnhydratedIsland = (el) => {
    let current = el;
    while (current && current !== document.documentElement) {
      if (current.hasAttribute('client:load') || current.hasAttribute('client:visible') || current.hasAttribute('client:idle')) {
        if (!current.__asokIslandInited) {
          return true;
        }
      }
      current = current.parentElement;
    }
    return false;
  };

  // Main compilation and initialization
  const init = (root = document) => {
    // Islands check
    if (root === document) {
      const islands = Array.from(document.querySelectorAll('[client\\:load], [client\\:visible], [client\\:idle]'));
      if (islands.length > 0) {
        // Handle cloaking globally immediately for non-island elements
        if (document.documentElement) {
          document.documentElement.removeAttribute('asok-cloak');
          document.documentElement.removeAttribute('data-asok-cloak');
          document.documentElement.removeAttribute('data-cloak');
        }
        document.querySelectorAll('[asok-cloak], [data-asok-cloak], [data-cloak]').forEach(e => {
          if (!isInsideUnhydratedIsland(e)) {
            e.removeAttribute('asok-cloak');
            e.removeAttribute('data-asok-cloak');
            e.removeAttribute('data-cloak');
          }
        });
        document.querySelectorAll('script').forEach(s => s.dataset.run = '1');

        const initIsland = (island) => {
          if (island._stateInitialized || island.__asokIslandInited) return;
          island.__asokIslandInited = true;
          if (island.hasAttribute('asok-state-ref')) {
            initState(island);
          }
          init(island);
        };

        islands.forEach(island => {
          if (island.hasAttribute('client:load')) {
            initIsland(island);
          } else if (island.hasAttribute('client:visible')) {
            const observer = new IntersectionObserver((entries) => {
              entries.forEach(entry => {
                if (entry.isIntersecting) {
                  initIsland(island);
                  observer.unobserve(island);
                }
              });
            });
            observer.observe(island);
          } else if (island.hasAttribute('client:idle')) {
            const run = () => initIsland(island);
            if (window.requestIdleCallback) {
              window.requestIdleCallback(run, { timeout: 2000 });
            } else {
              setTimeout(run, 100);
            }
          }
        });
      }
    }

    const elements = root === document ? document.querySelectorAll('*') : [root, ...root.querySelectorAll('*')];

    // First pass: scan structures and state scopes
    elements.forEach(el => {
      if (isInsideUnhydratedIsland(el)) return;
      if (el.hasAttribute('asok-state-ref')) {
        initState(el);
      }

      // Handle refs registry
      if (el.hasAttribute('asok-ref') && !el._refInitialized) {
        const owner = findStateOwner(el);
        if (owner) {
          contexts.get(owner).refs[el.getAttribute('asok-ref')] = el;
          el._refInitialized = 1;
        }
      }

      // Handle teleportation
      if (el.hasAttribute('asok-teleport') && !el._teleportInitialized) {
        const selector = el.getAttribute('asok-teleport');
        const target = document.querySelector(selector);
        const owner = findStateOwner(el);
        if (target && owner) {
          const ownerCtx = contexts.get(owner);
          const fragment = el.content.cloneNode(true);
          const child = fragment.firstElementChild;

          contexts.set(child, {
            state: ownerCtx.state,
            refs: ownerCtx.refs,
            cleanup: [],
            _teleportedScopes: []
          });
          ownerCtx._teleportedScopes.push(child);

          target.appendChild(fragment);
          if (window.Asok && window.Asok.init) window.Asok.init(child); else init(child);
          el._teleportInitialized = 1;
          el.style.display = 'none';
        }
      }

      // Pre-evaluate loops/conditions inside templates
      if (el.tagName === 'TEMPLATE' && !el._ai) {
        const owner = findStateOwner(el);
        if (owner) {
          const state = contexts.get(owner).state;
          if (el.hasAttribute('asok-if-ref')) updateIfDirective(el, state);
          if (el.hasAttribute('asok-for-ref')) updateForDirective(el, state);
        }
      }
    });

    // Second pass: initialize bindings and events
    elements.forEach(el => {
      if (isInsideUnhydratedIsland(el)) return;
      const owner = findStateOwner(el);
      if (owner) {
        updateBindings(el, contexts.get(owner).state);
      }
      if (el.hasAttribute('asok-model')) {
        initModel(el);
      }
      if (el.hasAttribute('asok-fetch')) {
        initFetch(el);
      }
      if (el.hasAttribute('asok-fetch-async-ref')) {
        initFetchAsync(el);
      }
      if (Array.from(el.attributes).some(attr => attr.name.startsWith('asok-on-ref:'))) {
        initEvents(el);
      }
    });

    // Cloaking cleanup
    const cleanRoot = root === document ? document : root;
    if (cleanRoot.querySelectorAll) {
      ['asok-cloak', 'data-asok-cloak', 'data-cloak'].forEach(attr => {
        if (cleanRoot.hasAttribute && cleanRoot.hasAttribute(attr)) {
          if (!isInsideUnhydratedIsland(cleanRoot)) {
            cleanRoot.removeAttribute(attr);
          }
        }
      });
      cleanRoot.querySelectorAll('[asok-cloak], [data-asok-cloak], [data-cloak]').forEach(e => {
        if (!isInsideUnhydratedIsland(e)) {
          e.removeAttribute('asok-cloak');
          e.removeAttribute('data-asok-cloak');
          e.removeAttribute('data-cloak');
        }
      });
    }
    if (root === document) {
      document.querySelectorAll('script').forEach(s => s.dataset.run = '1');
    }
  };

  // DOMContentLoaded hook
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => init());
  } else {
    init();
  }

  // Built-in Declarative Theme System
  const getStoredTheme = () => {
    try { return localStorage.getItem('asok-theme') || 'system'; } catch (e) { return 'system'; }
  };

  const getSystemTheme = () => (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';

  const applyTheme = (mode) => {
    const resolved = mode === 'system' ? getSystemTheme() : mode;
    document.documentElement.style.colorScheme = resolved;
    document.documentElement.setAttribute('data-theme', resolved);
    if (resolved === 'dark') {
      document.documentElement.classList.add('dark');
      document.documentElement.classList.remove('light');
    } else {
      document.documentElement.classList.add('light');
      document.documentElement.classList.remove('dark');
    }
    globalStore.theme = resolved;
    globalStore.themeMode = mode;
    document.dispatchEvent(new CustomEvent('asok:theme', { detail: { theme: resolved, mode } }));
  };

  const setTheme = (mode) => {
    const valid = ['light', 'dark', 'system'].includes(mode) ? mode : 'system';
    try { localStorage.setItem('asok-theme', valid); } catch (e) {}
    applyTheme(valid);
  };

  const toggleTheme = () => {
    const current = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    setTheme(current);
  };

  applyTheme(getStoredTheme());

  if (window.matchMedia) {
    try {
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if (getStoredTheme() === 'system') applyTheme('system');
      });
    } catch (e) {}
  }

  document.addEventListener('click', (e) => {
    const toggleEl = e.target.closest('[asok-theme-toggle], [data-asok-theme-toggle], [data-theme-toggle]');
    if (!toggleEl) return;
    const attrVal = (toggleEl.getAttribute('asok-theme-toggle') || toggleEl.getAttribute('data-asok-theme-toggle') || toggleEl.getAttribute('data-theme-toggle') || '').trim();
    if (attrVal && ['light', 'dark', 'system'].includes(attrVal)) {
      setTheme(attrVal);
    } else {
      toggleTheme();
    }
  });

  // Built-in Declarative Cookie Consent System
  const getStoredConsent = () => {
    try { return localStorage.getItem('asok-cookie-consent') || 'pending'; } catch (e) { return 'pending'; }
  };

  const updateConsentUI = (status) => {
    globalStore.cookieConsent = status;
    document.querySelectorAll('[asok-cookie-banner], [data-asok-cookie-banner], [data-cookie-banner]').forEach(banner => {
      if (status === 'pending') {
        banner.style.display = '';
        banner.removeAttribute('hidden');
      } else {
        banner.style.display = 'none';
        banner.setAttribute('hidden', '');
      }
    });
    if (status === 'accepted') {
      document.querySelectorAll('script[type="text/plain"][asok-cookie-script], script[type="text/plain"][data-cookie-script]').forEach(s => {
        if (s.dataset.cookieActivated) return;
        s.dataset.cookieActivated = '1';
        const newScript = document.createElement('script');
        const nonce = s.nonce || window.Asok?.nonce || document.querySelector('script[nonce]')?.getAttribute('nonce') || '';
        if (nonce) newScript.nonce = nonce;
        if (s.src) newScript.src = s.src;
        newScript.textContent = s.textContent;
        s.parentNode.replaceChild(newScript, s);
      });
    }
    document.dispatchEvent(new CustomEvent('asok:cookie-consent', { detail: { status } }));
  };

  const setCookieConsent = (status) => {
    const valid = ['accepted', 'rejected'].includes(status) ? status : 'rejected';
    try { localStorage.setItem('asok-cookie-consent', valid); } catch (e) {}
    updateConsentUI(valid);
  };

  const resetCookieConsent = () => {
    try { localStorage.removeItem('asok-cookie-consent'); } catch (e) {}
    updateConsentUI('pending');
  };

  updateConsentUI(getStoredConsent());

  document.addEventListener('click', (e) => {
    const acceptEl = e.target.closest('[asok-cookie-accept], [data-asok-cookie-accept], [data-cookie-accept]');
    if (acceptEl) {
      setCookieConsent('accepted');
      return;
    }
    const rejectEl = e.target.closest('[asok-cookie-reject], [data-asok-cookie-reject], [data-cookie-reject]');
    if (rejectEl) {
      setCookieConsent('rejected');
      return;
    }
    const resetEl = e.target.closest('[asok-cookie-reset], [data-asok-cookie-reset], [data-cookie-reset]');
    if (resetEl) {
      resetCookieConsent();
      return;
    }
  });

  // Built-in Dismissible Elements System (Announcements, Banners, Alerts)
  const isDismissed = (key) => {
    if (!key) return false;
    try { return localStorage.getItem('asok-dismiss:' + key) === 'true'; } catch (e) { return false; }
  };

  const dismissElement = (key, targetEl) => {
    if (key) {
      try { localStorage.setItem('asok-dismiss:' + key, 'true'); } catch (e) {}
    }
    const container = targetEl || (key ? document.querySelector(`[asok-dismiss="${key}"], [data-asok-dismiss="${key}"], [data-dismiss="${key}"]`) : null);
    if (container) {
      container.style.display = 'none';
      container.setAttribute('hidden', '');
    }
    document.dispatchEvent(new CustomEvent('asok:dismiss', { detail: { key } }));
  };

  const initDismissables = (root = document) => {
    const selector = '[asok-dismiss], [data-asok-dismiss], [data-dismiss]';
    const els = root.querySelectorAll ? root.querySelectorAll(selector) : [];
    els.forEach(el => {
      const key = el.getAttribute('asok-dismiss') || el.getAttribute('data-asok-dismiss') || el.getAttribute('data-dismiss');
      if (key && isDismissed(key)) {
        el.style.display = 'none';
        el.setAttribute('hidden', '');
      }
    });
  };

  initDismissables();

  document.addEventListener('click', (e) => {
    const triggerEl = e.target.closest('[asok-dismiss-trigger], [data-asok-dismiss-trigger], [data-dismiss-trigger]');
    if (!triggerEl) return;
    const parentContainer = triggerEl.closest('[asok-dismiss], [data-asok-dismiss], [data-dismiss]');
    const key = triggerEl.getAttribute('asok-dismiss-trigger') || triggerEl.getAttribute('data-asok-dismiss-trigger') || triggerEl.getAttribute('data-dismiss-trigger') || (parentContainer ? (parentContainer.getAttribute('asok-dismiss') || parentContainer.getAttribute('data-asok-dismiss') || parentContainer.getAttribute('data-dismiss')) : '');
    dismissElement(key, parentContainer);
  });

  // Global namespace — must be created before we wrap init so the chain works
  // regardless of script load order (directives may load before or after spa.js).
  window.Asok = window.Asok || {};

  window.Asok.setTheme = setTheme;
  window.Asok.toggleTheme = toggleTheme;
  window.Asok.getTheme = () => document.documentElement.getAttribute('data-theme') || 'light';
  window.Asok.acceptCookies = () => setCookieConsent('accepted');
  window.Asok.rejectCookies = () => setCookieConsent('rejected');
  window.Asok.resetCookieConsent = resetCookieConsent;
  window.Asok.getCookieConsent = getStoredConsent;
  window.Asok.dismiss = dismissElement;
  window.Asok.isDismissed = isDismissed;

  // ── 1. Copy to Clipboard Directive (asok-copy) ───────────────
  const copyToClipboard = (text, triggerEl) => {
    let contentToCopy = text;
    if (text && text.startsWith('#')) {
      const target = document.querySelector(text);
      if (target) contentToCopy = target.value || target.textContent || '';
    } else if ((!contentToCopy || contentToCopy === 'true') && triggerEl) {
      const parentBlock = triggerEl.closest('.code-block, code-block, [asok-code-block]');
      const preEl = parentBlock ? parentBlock.querySelector('pre, code') : triggerEl.nextElementSibling;
      if (preEl) {
        contentToCopy = preEl.value || preEl.textContent || '';
      } else {
        contentToCopy = triggerEl.value || triggerEl.textContent || '';
      }
    }
    if (!contentToCopy) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(contentToCopy).then(() => {
        if (triggerEl) {
          triggerEl.classList.add('asok-copied');
          const copiedText = triggerEl.getAttribute('asok-copied-text') || triggerEl.getAttribute('data-copied');
          const origText = triggerEl.getAttribute('data-label') || triggerEl.getAttribute('data-orig-text') || triggerEl.textContent;
          if (!triggerEl.hasAttribute('data-orig-text')) {
            triggerEl.setAttribute('data-orig-text', origText);
          }
          if (copiedText) {
            triggerEl.textContent = copiedText;
          }
          setTimeout(() => {
            triggerEl.classList.remove('asok-copied');
            if (copiedText) {
              triggerEl.textContent = triggerEl.getAttribute('data-orig-text') || origText;
            }
          }, 2000);
        }
        document.dispatchEvent(new CustomEvent('asok:copied', { detail: { text: contentToCopy, element: triggerEl } }));
      }).catch(() => {});
    }
  };

  // ── 2. Accessible Modal Directive (asok-modal) ───────────────
  const openModal = (modalId) => {
    const modal = document.querySelector(`[asok-modal="${modalId}"], [data-asok-modal="${modalId}"], [data-modal="${modalId}"]`);
    if (!modal) return;
    if (modal.tagName === 'DIALOG' && modal.showModal) {
      modal.showModal();
    } else {
      modal.style.display = '';
      modal.removeAttribute('hidden');
      modal.classList.add('is-open');
    }
    document.dispatchEvent(new CustomEvent('asok:modal-open', { detail: { modalId } }));
  };

  const closeModal = (targetEl) => {
    const modal = targetEl ? targetEl.closest('[asok-modal], [data-asok-modal], [data-modal]') : null;
    if (!modal) return;
    if (modal.tagName === 'DIALOG' && modal.close) {
      modal.close();
    } else {
      modal.style.display = 'none';
      modal.setAttribute('hidden', '');
      modal.classList.remove('is-open');
    }
    document.dispatchEvent(new CustomEvent('asok:modal-close', { detail: { modal } }));
  };

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const openModals = document.querySelectorAll('[asok-modal].is-open, [data-asok-modal].is-open, [data-modal].is-open');
      openModals.forEach(m => closeModal(m));
    }
  });

  // ── 3. Smooth Scroll Helpers (asok-scroll-top / asok-scroll-to)
  const scrollToElement = (target) => {
    if (target === 'top' || !target) {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } else {
      const el = typeof target === 'string' ? document.querySelector(target) : target;
      if (el) el.scrollIntoView({ behavior: 'smooth' });
    }
  };

  const initScrollTopButtons = (root = document) => {
    const selector = '[asok-scroll-top], [data-asok-scroll-top], [data-scroll-top]';
    const buttons = (root.querySelectorAll ? root.querySelectorAll(selector) : []);
    if (!buttons.length) return;

    const updateVisibility = () => {
      const currentScroll = window.scrollY || document.documentElement.scrollTop || 0;
      buttons.forEach(btn => {
        const rawAttr = btn.getAttribute('asok-scroll-top') || btn.getAttribute('data-asok-scroll-top') || btn.getAttribute('data-scroll-top') || '';
        const threshold = parseInt(rawAttr, 10) || 200;
        const isScrollable = document.documentElement.scrollHeight > window.innerHeight + 50;

        if (isScrollable && currentScroll >= threshold) {
          btn.style.display = '';
          btn.removeAttribute('hidden');
          btn.classList.add('is-visible', 'active');
        } else {
          btn.style.display = 'none';
          btn.setAttribute('hidden', '');
          btn.classList.remove('is-visible', 'active');
        }
      });
    };

    window.addEventListener('scroll', updateVisibility, { passive: true });
    window.addEventListener('resize', updateVisibility, { passive: true });
    updateVisibility();
  };

  initScrollTopButtons();

  // ── 4. Character Counter Directive (asok-char-count) ─────────
  const initCharCounters = (root = document) => {
    const counters = root.querySelectorAll('[asok-char-count], [data-asok-char-count], [data-char-count]');
    counters.forEach(counter => {
      const targetSelector = counter.getAttribute('asok-char-count') || counter.getAttribute('data-asok-char-count') || counter.getAttribute('data-char-count');
      const input = document.querySelector(targetSelector);
      if (!input) return;
      const update = () => {
        const max = parseInt(input.getAttribute('maxlength') || '0', 10);
        const current = input.value ? input.value.length : 0;
        if (max > 0) {
          counter.textContent = `${current} / ${max}`;
        } else {
          counter.textContent = `${current}`;
        }
      };
      input.addEventListener('input', update);
      update();
    });
  };

  initCharCounters();

  // ── 5. Declarative Tabs Directive (asok-tabs) ────────────────
  const initTabs = (root = document) => {
    document.addEventListener('click', (e) => {
      const tabBtn = e.target.closest('[asok-tab], [data-asok-tab], [data-tab]');
      if (!tabBtn) return;
      const tabId = tabBtn.getAttribute('asok-tab') || tabBtn.getAttribute('data-asok-tab') || tabBtn.getAttribute('data-tab');
      const tabContainer = tabBtn.closest('[asok-tabs], [data-asok-tabs], [data-tabs]') || document;
      
      tabContainer.querySelectorAll('[asok-tab], [data-asok-tab], [data-tab]').forEach(btn => {
        const id = btn.getAttribute('asok-tab') || btn.getAttribute('data-asok-tab') || btn.getAttribute('data-tab');
        if (id === tabId) {
          btn.classList.add('is-active', 'active');
          btn.setAttribute('aria-selected', 'true');
        } else {
          btn.classList.remove('is-active', 'active');
          btn.setAttribute('aria-selected', 'false');
        }
      });

      tabContainer.querySelectorAll('[asok-tab-panel], [data-asok-tab-panel], [data-tab-panel]').forEach(panel => {
        const pid = panel.getAttribute('asok-tab-panel') || panel.getAttribute('data-asok-tab-panel') || panel.getAttribute('data-tab-panel');
        if (pid === tabId) {
          panel.style.display = '';
          panel.removeAttribute('hidden');
        } else {
          panel.style.display = 'none';
          panel.setAttribute('hidden', '');
        }
      });
    });
  };

  initTabs();

  // ── 7. Declarative Top Navigation Progress Bar (asok-progress) ────
  const startProgress = () => {
    const selector = '[asok-progress], [data-asok-progress], [data-progress]';
    const bars = document.querySelectorAll(selector);
    bars.forEach(bar => {
      const fill = bar.firstElementChild || bar;
      bar.style.opacity = '1';
      fill.style.width = '0%';
      void fill.offsetWidth; // Force reflow
      const maxVal = bar.getAttribute('asok-progress') || bar.getAttribute('data-asok-progress') || bar.getAttribute('data-progress');
      const maxPercent = parseInt(maxVal, 10) || 90;
      fill.style.width = maxPercent + '%';
    });
  };

  const finishProgress = () => {
    const selector = '[asok-progress], [data-asok-progress], [data-progress]';
    const bars = document.querySelectorAll(selector);
    bars.forEach(bar => {
      const fill = bar.firstElementChild || bar;
      fill.style.width = '100%';
      setTimeout(() => {
        bar.style.opacity = '0';
        setTimeout(() => { fill.style.width = '0%'; }, 200);
      }, 250);
    });
  };

  // Auto-listen to navigation events & link clicks
  document.addEventListener('asok:before', startProgress);
  document.addEventListener('asok:success', finishProgress);
  document.addEventListener('asok:error', finishProgress);

  document.addEventListener('click', (e) => {
    const link = e.target.closest('a[href]:not([target="_blank"]):not([href^="#"]):not([href^="javascript:"])');
    if (link && link.href && !link.hasAttribute('asok-no-progress')) {
      const url = new URL(link.href, window.location.href);
      if (url.origin === window.location.origin && (url.pathname !== window.location.pathname || url.search !== window.location.search)) {
        startProgress();
      }
    }
  });

  window.addEventListener('beforeunload', startProgress);

  window.Asok.startProgress = startProgress;
  window.Asok.finishProgress = finishProgress;

  // Global click delegator for copy, modal-open, modal-close, scroll
  document.addEventListener('click', (e) => {
    const copyEl = e.target.closest('[asok-copy], [data-asok-copy], [data-copy]');
    if (copyEl) {
      const text = copyEl.getAttribute('asok-copy') || copyEl.getAttribute('data-asok-copy') || copyEl.getAttribute('data-copy');
      copyToClipboard(text, copyEl);
    }
    const modalOpenEl = e.target.closest('[asok-modal-open], [data-asok-modal-open], [data-modal-open]');
    if (modalOpenEl) {
      const modalId = modalOpenEl.getAttribute('asok-modal-open') || modalOpenEl.getAttribute('data-asok-modal-open') || modalOpenEl.getAttribute('data-modal-open');
      openModal(modalId);
    }
    const modalCloseEl = e.target.closest('[asok-modal-close], [data-asok-modal-close], [data-modal-close]');
    if (modalCloseEl) {
      closeModal(modalCloseEl);
    }
    const scrollTopEl = e.target.closest('[asok-scroll-top], [data-asok-scroll-top], [data-scroll-top]');
    if (scrollTopEl) {
      scrollToElement('top');
    }
    const scrollToEl = e.target.closest('[asok-scroll-to], [data-asok-scroll-to], [data-scroll-to]');
    if (scrollToEl) {
      const target = scrollToEl.getAttribute('asok-scroll-to') || scrollToEl.getAttribute('data-asok-scroll-to') || scrollToEl.getAttribute('data-scroll-to');
      scrollToElement(target);
    }
  });

  // Always wrap window.Asok.init unconditionally (not inside `if (window.Asok)`
  // which silently skips when directives.js loads before spa.js and window.Asok
  // does not yet exist). This guarantees AsokDirectives.init is always called.
  const _prevDirectivesInit = window.Asok.init;
  window.Asok.init = (el) => {
    if (_prevDirectivesInit) _prevDirectivesInit(el);
    init(el);
  };

  window.Asok.store = globalStore;

  window.AsokDirectives = {
    init,
    forceInit,
    cleanupOld,
    resetFlags,
    version: '2.0.0',
    w: contexts
  };
})();
