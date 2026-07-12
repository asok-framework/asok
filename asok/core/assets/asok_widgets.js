/*
 *  Asok Widgets - Widgets for Asok framework
 *  author: Asok Team
 *  license: MIT
 *  version: 2.0.0
*/
(function () {
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
    // Release object URLs from a previous selection to avoid leaking them.
    if (Array.isArray(state.files)) {
      state.files.forEach(f => { if (f && f.url) URL.revokeObjectURL(f.url); });
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

    // SECURITY: Sanitize WYSIWYG content to prevent Stored XSS
    // Note: Server-side validation is still required for defense-in-depth
    const sanitized = window.AsokSecurity && window.AsokSecurity.sanitizeHtml ?
      window.AsokSecurity.sanitizeHtml(html) : html;

    state.content = sanitized;
    if (inputEl) {
      inputEl.value = sanitized;
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
    const isLight = document.body.classList.contains('light-mode');
    ctx.strokeStyle = isLight ? '#0f172a' : '#f8fafc';
  };

  window.Asok.drawSignature = (event, state, canvasEl) => {
    if (state.drawing) {
      const ctx = canvasEl.getContext('2d');
      const rect = canvasEl.getBoundingClientRect();
      const isLight = document.body.classList.contains('light-mode');
      ctx.strokeStyle = isLight ? '#0f172a' : '#f8fafc';
      ctx.lineWidth = 2;
      ctx.lineCap = 'round';
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
})();
