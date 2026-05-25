;(function() {
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
          setTimeout(() => {
            el.classList.remove(`asok-${baseName}-in`, 'is-entering');
          }, activeDuration);
        });
      } else {
        el.classList.add(`asok-${baseName}-out`);
        el.offsetHeight; // Force reflow
        requestAnimationFrame(() => {
          el.classList.add('is-leaving');
          setTimeout(() => {
            if (callback) callback();
            el.classList.remove(`asok-${baseName}-out`, 'is-leaving');
          }, activeDuration);
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
        // Strip script tags to avoid XSS execution
        el.innerHTML = String(val).replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '');
      }
    }

    // asok-show
    if (el.hasAttribute('asok-show-ref')) {
      const isShown = evaluateExpression(getAttr('asok-show-ref'), state, el);
      if (!el._asokShowInitialized) {
        el._asokShowInitialized = true;
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
        const val = evaluateExpression(attr.value, state, el);
        if (val !== undefined && val !== null && val !== false) {
          el.setAttribute(attrName, String(val));
        } else {
          el.removeAttribute(attrName);
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
          item._n = fragment.firstElementChild;
          item.parentNode.insertBefore(fragment, item.nextSibling);
          contexts.set(item._n, contexts.get(el) || { state: state, refs: {} });
          init(item._n);
        }
        conditionMet = 1;
      } else if (item._n) {
        item._n.remove();
        item._n = null;
      }
    });
  };

  // Update asok-for loops
  const updateForDirective = (el, state) => {
    el._ai = 1; // Mark as initialized
    const ref = el.getAttribute('asok-for-ref');
    const varName = el.getAttribute('asok-for-var');
    const items = evaluateExpression(ref, state, el) || [];
    const itemsJSON = JSON.stringify(items);
    
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
      el.parentNode.insertBefore(el._marker, el.nextSibling);
    }

    (el._children || []).forEach(child => child.remove());
    el._children = [];

    items.forEach((item, index) => {
      const fragment = el.content.cloneNode(true);
      const child = fragment.firstElementChild;
      const subState = createReactiveProxy({ [itemVar]: item, [indexVar]: index }, () => updateScope(findStateOwner(el)), state);
      
      contexts.set(child, { state: subState, refs: {}, cleanup: [] });
      el.parentNode.insertBefore(fragment, el._marker);
      el._children.push(child);
      init(child);
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

    const getValue = (obj, path) => path.split('.').reduce((acc, k) => acc && acc[k], obj);
    const setValue = (obj, path, val) => {
      const keys = path.split('.');
      const lastKey = keys.pop();
      const target = keys.reduce((acc, x) => acc[x] = acc[x] || {}, obj);
      target[lastKey] = val;
    };

    el._updateValue = () => {
      const val = getValue(state, modelAttr);
      const displayVal = (val !== undefined && val !== null) ? val : '';
      if (el.value !== String(displayVal) && document.activeElement !== el) {
        if (el.type === 'checkbox') el.checked = !!displayVal;
        else if (el.type === 'radio') el.checked = el.value === displayVal;
        else el.value = displayVal;
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

    const handler = () => doAsync();
    el.addEventListener(trigger, handler);
    contexts.get(owner).cleanup.push(() => el.removeEventListener(trigger, handler));
  };

  // Clean up references and event listeners
  const cleanupOld = (root) => {
    if (!root) return;
    const elements = [root, ...root.querySelectorAll('*')];
    elements.forEach(el => {
      dependencyMap.forEach((set, key) => {
        set.delete(el);
        if (set.size === 0) {
          dependencyMap.delete(key);
        }
      });
      const ctx = contexts.get(el);
      if (ctx && ctx.cleanup) {
        ctx.cleanup.forEach(fn => {
          try {
            fn();
          } catch (e) {}
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

  // Main compilation and initialization
  const init = (root = document) => {
    const elements = root === document ? document.querySelectorAll('*') : [root, ...root.querySelectorAll('*')];
    
    // First pass: scan structures and state scopes
    elements.forEach(el => {
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
          init(child);
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
    if (root === document) {
      document.querySelectorAll('[asok-cloak]').forEach(e => e.removeAttribute('asok-cloak'));
      document.querySelectorAll('script').forEach(s => s.dataset.run = '1');
    }
  };

  // DOMContentLoaded hook
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => init());
  } else {
    init();
  }

  // Extend Asok lifecycle APIs if present
  if (window.Asok) {
    const oldInit = window.Asok.init;
    window.Asok.init = (el) => {
      if (oldInit) oldInit(el);
      init(el);
    };
  }

  // Global namespace hooks
  window.Asok = window.Asok || {};

  // Form Helper Functions
  window.Asok.previewImage = (event, state) => {
    const f = event.target.files[0];
    if (f) {
      const r = new FileReader();
      r.onload = (e) => {
        state.preview = e.target.result;
      };
      r.readAsDataURL(f);
    }
  };

  window.Asok.selectDropdown = (state, id, title, inputEl) => {
    state.label = title;
    state.open = false;
    if (inputEl) {
      inputEl.value = id;
      inputEl.dispatchEvent(new Event('change'));
    }
  };

  window.Asok.removeTag = (state, tag, inputEl) => {
    state.selected = state.selected.filter(t => t.value !== tag.value);
    if (inputEl) {
      inputEl.value = JSON.stringify(state.selected.map(t => t.value));
      inputEl.dispatchEvent(new Event('change'));
    }
  };

  window.Asok.addTag = (state, option, inputEl) => {
    if (!state.selected.some(t => t.value === option.value)) {
      state.selected.push({ value: option.value, label: option.label });
      if (inputEl) {
        inputEl.value = JSON.stringify(state.selected.map(t => t.value));
        inputEl.dispatchEvent(new Event('change'));
      }
    }
  };

  window.Asok.updateHiddenJson = (inputEl, obj) => {
    if (inputEl) {
      inputEl.value = JSON.stringify(obj);
      inputEl.dispatchEvent(new Event('change'));
    }
  };

  window.Asok.updateHiddenValue = (inputEl, val) => {
    if (inputEl) {
      inputEl.value = val;
      inputEl.dispatchEvent(new Event('change'));
    }
  };

  window.Asok.handleOtpKeyup = (event) => {
    if (event.target.value && event.key !== 'Backspace') {
      const next = event.target.nextElementSibling;
      if (next && next.tagName === 'INPUT') next.focus();
    }
  };

  window.Asok.setRating = (state, rating, inputEl) => {
    state.rating = rating;
    if (inputEl) {
      inputEl.value = rating;
      inputEl.dispatchEvent(new Event('change'));
    }
  };

  window.Asok.handleFilesChange = (event, state, maxFiles) => {
    const fileList = Array.from(event.target.files);
    if (fileList.length > maxFiles) {
      alert('Maximum ' + maxFiles + ' files');
      event.target.value = '';
      return;
    }
    state.files = fileList.map(f => ({
      name: f.name,
      size: f.size,
      url: URL.createObjectURL(f)
    }));
  };

  window.Asok.filterAutocomplete = (state, minChars) => {
    if (state.query.length >= minChars) {
      state.filtered = state.all.filter(item =>
        String(item).toLowerCase().includes(state.query.toLowerCase())
      );
      state.show = true;
    } else {
      state.show = false;
    }
  };

  window.Asok.selectAutocomplete = (state, item, inputEl) => {
    state.query = String(item);
    state.show = false;
    if (inputEl) {
      inputEl.value = state.query;
      inputEl.dispatchEvent(new Event('change'));
    }
  };

  window.Asok.updateWysiwyg = (event, state, inputEl) => {
    const html = event.target.innerHTML;
    state.content = html;
    if (inputEl) {
      inputEl.value = html;
      inputEl.dispatchEvent(new Event('change'));
    }
  };

  window.Asok.handleDropzoneDrop = (event, state, maxFiles, inputEl) => {
    state.dragging = false;
    const fileList = Array.from(event.dataTransfer.files);
    if (fileList.length > maxFiles) {
      alert('Max ' + maxFiles + ' files');
      return;
    }
    const dt = new DataTransfer();
    for (let i = 0; i < fileList.length; i++) {
      dt.items.add(fileList[i]);
    }
    if (inputEl) {
      inputEl.files = dt.files;
    }
    state.files = fileList.map(f => ({
      name: f.name,
      size: f.size,
      _file: f
    }));
  };

  window.Asok.handleDropzoneChange = (event, state, maxFiles) => {
    const fileList = Array.from(event.target.files);
    if (fileList.length > maxFiles) {
      alert('Maximum ' + maxFiles + ' files');
      return;
    }
    state.files = fileList.map(f => ({
      name: f.name,
      size: f.size,
      _file: f
    }));
  };

  window.Asok.removeDropzoneFile = (state, index, inputEl) => {
    state.files = state.files.filter((_, i) => i !== index);
    const dt = new DataTransfer();
    state.files.forEach(f => dt.items.add(f._file));
    if (inputEl) {
      inputEl.files = dt.files;
    }
  };

  // Signature field helpers
  window.Asok.startSignatureDrawing = (event, state, canvasEl) => {
    state.drawing = true;
    const ctx = canvasEl.getContext('2d');
    const rect = canvasEl.getBoundingClientRect();
    ctx.beginPath();
    ctx.moveTo(event.clientX - rect.left, event.clientY - rect.top);
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.strokeStyle = '#000';
  };

  window.Asok.drawSignature = (event, state, canvasEl) => {
    if (state.drawing) {
      const ctx = canvasEl.getContext('2d');
      const rect = canvasEl.getBoundingClientRect();
      ctx.lineTo(event.clientX - rect.left, event.clientY - rect.top);
      ctx.stroke();
    }
  };

  window.Asok.stopSignatureDrawing = (state, canvasEl, inputEl) => {
    state.drawing = false;
    if (inputEl) {
      inputEl.value = canvasEl.toDataURL();
      inputEl.dispatchEvent(new Event('change'));
    }
  };

  window.Asok.clearSignature = (canvasEl, inputEl) => {
    const ctx = canvasEl.getContext('2d');
    ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
    if (inputEl) {
      inputEl.value = '';
      inputEl.dispatchEvent(new Event('change'));
    }
  };

  // Transfer field helpers
  window.Asok.updateTransferSelection = (state, prop, event) => {
    state[prop] = Array.from(event.target.selectedOptions).map(o => o.value);
  };

  window.Asok.moveTransferRight = (state) => {
    const move = state.available.filter(i => state.h_avail.includes(String(i.id !== undefined ? i.id : i)));
    state.selected = [...state.selected, ...move];
    state.available = state.available.filter(i => !move.includes(i));
    state.h_avail = [];
  };

  window.Asok.moveTransferLeft = (state) => {
    const move = state.selected.filter(i => state.h_sel.includes(String(i.id !== undefined ? i.id : i)));
    state.available = [...state.available, ...move];
    state.selected = state.selected.filter(i => !move.includes(i));
    state.h_sel = [];
  };

  window.Asok.moveTransferItemRight = (state, item) => {
    state.selected.push(item);
    state.available = state.available.filter(i => i !== item);
  };

  window.Asok.moveTransferItemLeft = (state, item) => {
    state.available.push(item);
    state.selected = state.selected.filter(i => i !== item);
  };

  // Treeselect field helpers
  window.Asok.selectTreeItem = (state, itemId, inputEl) => {
    state.selected = itemId;
    if (inputEl) {
      inputEl.value = itemId;
      inputEl.dispatchEvent(new Event('change'));
    }
  };

  window.Asok.toggleTreeExpansion = (state, itemId) => {
    if (state.expanded.includes(itemId)) {
      state.expanded = state.expanded.filter(i => i !== itemId);
    } else {
      state.expanded.push(itemId);
    }
  };

  window.AsokDirectives = {
    init,
    forceInit,
    cleanupOld,
    resetFlags,
    version: '1.0.0',
    w: contexts
  };
  window.Asok.store = globalStore;
})();
