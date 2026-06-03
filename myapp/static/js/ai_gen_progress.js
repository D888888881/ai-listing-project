/**
 * 全站 AI 批量生图进度浮窗（按登录用户隔离，可手动关闭）
 */
(function (global) {
  var STORAGE_VERSION = 'v3';
  var STALE_MS = 2 * 60 * 60 * 1000;

  function currentUserId() {
    var body = document.body;
    if (!body) return '0';
    return String(body.getAttribute('data-user-id') || '0');
  }

  function storageKey() {
    return 'ai_gen_batch_progress_' + STORAGE_VERSION + '_u' + currentUserId();
  }

  function readState() {
    try {
      var raw = localStorage.getItem(storageKey());
      if (!raw) return null;
      var st = JSON.parse(raw);
      if (st && st.userId && String(st.userId) !== currentUserId()) {
        localStorage.removeItem(storageKey());
        return null;
      }
      if (st && st.active && st.updatedAt && Date.now() - st.updatedAt > STALE_MS) {
        localStorage.removeItem(storageKey());
        return null;
      }
      return st;
    } catch (e) {
      return null;
    }
  }

  function writeState(state) {
    try {
      state.userId = currentUserId();
      localStorage.setItem(storageKey(), JSON.stringify(state));
    } catch (e) { /* ignore */ }
    render();
  }

  function clearState() {
    try {
      localStorage.removeItem(storageKey());
    } catch (e) { /* ignore */ }
    render();
  }

  function defaultState() {
    return {
      active: false,
      finished: false,
      collapsed: false,
      label: 'AI 批量生图',
      totalExpected: 0,
      completed: 0,
      currentAsin: '',
      asinIndex: 0,
      asinTotal: 0,
      detail: '',
      parallelWorkers: 6,
      imagesPerModule: 3,
      userId: currentUserId(),
      updatedAt: Date.now()
    };
  }

  function ensurePanel() {
    var panel = document.getElementById('ai-gen-progress-panel');
    if (panel) return panel;
    panel = document.createElement('div');
    panel.id = 'ai-gen-progress-panel';
    panel.className = 'ai-gen-progress-panel';
    panel.setAttribute('aria-live', 'polite');
    panel.innerHTML =
      '<div class="ai-gen-progress-inner">' +
      '<div class="ai-gen-progress-head">' +
      '<strong class="ai-gen-progress-title">AI 批量生图</strong>' +
      '<div class="ai-gen-progress-actions">' +
      '<button type="button" class="ai-gen-progress-btn" data-action="toggle" title="收起/展开">收起</button>' +
      '<button type="button" class="ai-gen-progress-btn" data-action="close" title="关闭">关闭</button>' +
      '</div></div>' +
      '<div class="ai-gen-progress-body">' +
      '<div class="ai-gen-progress-line" data-role="summary">准备中…</div>' +
      '<div class="ai-gen-progress-line ai-gen-progress-sub" data-role="detail"></div>' +
      '<div class="ai-gen-progress-bar"><div class="ai-gen-progress-bar-fill" data-role="bar"></div></div>' +
      '</div></div>';
    document.body.appendChild(panel);
    panel.querySelector('[data-action="toggle"]').addEventListener('click', function () {
      var st = readState() || defaultState();
      st.collapsed = !st.collapsed;
      writeState(st);
    });
    panel.querySelector('[data-action="close"]').addEventListener('click', function () {
      clearState();
    });
    return panel;
  }

  function render() {
    var panel = ensurePanel();
    var st = readState();
    if (!st || (!st.active && !st.finished)) {
      panel.classList.remove('is-visible', 'is-finished', 'is-collapsed');
      return;
    }
    panel.classList.add('is-visible');
    panel.classList.toggle('is-collapsed', !!st.collapsed);
    panel.classList.toggle('is-finished', !!st.finished && !st.active);

    var toggleBtn = panel.querySelector('[data-action="toggle"]');
    if (toggleBtn) toggleBtn.textContent = st.collapsed ? '展开' : '收起';

    var total = Math.max(0, parseInt(st.totalExpected, 10) || 0);
    var done = Math.max(0, parseInt(st.completed, 10) || 0);
    if (total > 0 && done > total) done = total;
    var pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : (st.finished ? 100 : 0);

    var summaryEl = panel.querySelector('[data-role="summary"]');
    var detailEl = panel.querySelector('[data-role="detail"]');
    var barEl = panel.querySelector('[data-role="bar"]');

    if (summaryEl) {
      summaryEl.textContent =
        '预计生成 ' + total + ' 张 · 已完成 ' + done + ' 张（' + pct + '%）' +
        ' · 并行 ' + (st.parallelWorkers || 6) + ' 路';
    }
    if (detailEl) {
      var parts = [];
      if (st.asinTotal > 0) {
        parts.push('ASIN ' + (st.asinIndex || 0) + '/' + st.asinTotal);
      }
      if (st.currentAsin) parts.push(st.currentAsin);
      if (st.detail) parts.push(st.detail);
      detailEl.textContent = parts.join(' · ');
    }
    if (barEl) barEl.style.width = pct + '%';
  }

  var api = {
    start: function (opts) {
      opts = opts || {};
      var st = defaultState();
      st.active = true;
      st.finished = false;
      st.collapsed = false;
      st.label = opts.label || 'AI 批量生图';
      st.totalExpected = opts.totalExpected || 0;
      st.completed = opts.completed || 0;
      st.currentAsin = opts.currentAsin || '';
      st.asinIndex = opts.asinIndex || 0;
      st.asinTotal = opts.asinTotal || 0;
      st.detail = opts.detail || '即将开始…';
      st.parallelWorkers = opts.parallelWorkers || 6;
      st.imagesPerModule = opts.imagesPerModule || 3;
      st.updatedAt = Date.now();
      writeState(st);
    },
    update: function (opts) {
      opts = opts || {};
      var st = readState();
      if (!st || (!st.active && !st.finished)) return;
      if (opts.totalExpected != null) st.totalExpected = opts.totalExpected;
      if (opts.completed != null) st.completed = opts.completed;
      if (opts.currentAsin != null) st.currentAsin = opts.currentAsin;
      if (opts.asinIndex != null) st.asinIndex = opts.asinIndex;
      if (opts.asinTotal != null) st.asinTotal = opts.asinTotal;
      if (opts.detail != null) st.detail = opts.detail;
      if (opts.parallelWorkers != null) st.parallelWorkers = opts.parallelWorkers;
      if (opts.collapsed != null) st.collapsed = !!opts.collapsed;
      st.updatedAt = Date.now();
      writeState(st);
    },
    addCompleted: function (n) {
      var st = readState();
      if (!st || !st.active) return;
      st.completed = Math.max(0, (parseInt(st.completed, 10) || 0) + (parseInt(n, 10) || 0));
      var total = parseInt(st.totalExpected, 10) || 0;
      if (total > 0 && st.completed > total) st.completed = total;
      st.updatedAt = Date.now();
      writeState(st);
    },
    finish: function (message) {
      var st = readState() || defaultState();
      st.active = false;
      st.finished = true;
      st.collapsed = false;
      st.detail = message || '任务已完成';
      var total = parseInt(st.totalExpected, 10) || 0;
      if (total > 0 && (parseInt(st.completed, 10) || 0) < total) {
        st.detail = (message || '任务结束') + '（部分图片可能因超时未生成，可再次批量生图补全）';
      }
      st.updatedAt = Date.now();
      writeState(st);
      setTimeout(function () {
        clearState();
      }, 10000);
    },
    fail: function (message) {
      var st = readState() || defaultState();
      st.active = false;
      st.finished = true;
      st.detail = message || '任务已中断';
      st.updatedAt = Date.now();
      writeState(st);
    },
    dismiss: function () {
      clearState();
    },
    collapse: function (on) {
      var st = readState();
      if (!st) return;
      st.collapsed = on !== false;
      writeState(st);
    },
    isActive: function () {
      var st = readState();
      return !!(st && st.active);
    },
    render: render
  };

  global.AiGenProgress = api;
  global.AiGenProgressClear = clearState;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    render();
  }
})(window);
