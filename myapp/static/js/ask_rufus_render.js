/**
 * Ask Rufus 展示：兼容旧版字符串与新版 { questionN: { "问题": "问题\\n解答" } }
 */
(function (global) {
  'use strict';

  function questionSortKey(key) {
    var m = String(key || '').match(/(\d+)/);
    return m ? parseInt(m[1], 10) : 999;
  }

  function splitAnswer(fullText, question) {
    var full = String(fullText || '').trim();
    var q = String(question || '').trim();
    if (!full) return '';
    if (q && full.toLowerCase().indexOf(q.toLowerCase()) === 0) {
      var rest = full.slice(q.length).replace(/^\n+/, '').trim();
      return rest || full;
    }
    var nl = full.indexOf('\n');
    if (nl >= 0) {
      var head = full.slice(0, nl).trim();
      var tail = full.slice(nl + 1).trim();
      if (q && head.toLowerCase() === q.toLowerCase()) return tail;
      return tail || full;
    }
    return full;
  }

  function extractQA(value) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      var keys = Object.keys(value);
      if (keys.length) {
        var question = String(keys[0] || '').trim();
        var full = String(value[keys[0]] || '').trim();
        if (!question && full) {
          var parts = full.split('\n');
          if (parts.length > 1) {
            return { question: parts[0].trim(), answer: parts.slice(1).join('\n').trim() };
          }
          return { question: full, answer: '' };
        }
        return { question: question, answer: splitAnswer(full, question) };
      }
    }
    if (typeof value === 'string') {
      var text = value.trim();
      if (!text) return { question: '', answer: '' };
      var idx = text.indexOf('\n');
      if (idx >= 0) {
        return { question: text.slice(0, idx).trim(), answer: text.slice(idx + 1).trim() };
      }
      return { question: text, answer: '' };
    }
    return { question: '', answer: '' };
  }

  function parseItems(ask) {
    if (!ask || typeof ask !== 'object' || Array.isArray(ask)) return [];
    return Object.keys(ask)
      .sort(function (a, b) {
        var da = questionSortKey(a);
        var db = questionSortKey(b);
        return da === db ? String(a).localeCompare(String(b)) : da - db;
      })
      .map(function (key) {
        var qa = extractQA(ask[key]);
        return { key: key, question: qa.question, answer: qa.answer };
      })
      .filter(function (it) { return it.question || it.answer; });
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function nl2br(text) {
    return escapeHtml(text).replace(/\n/g, '<br>');
  }

  function truncate(text, maxLen) {
    var s = String(text || '');
    if (s.length <= maxLen) return s;
    return s.slice(0, maxLen - 1) + '…';
  }

  /**
   * @param {object} ask
   * @param {{compact?: boolean, maxItems?: number, answerMax?: number, questionsOnly?: boolean}} opts
   */
  function buildListHtml(ask, opts) {
    opts = opts || {};
    var compact = !!opts.compact;
    var questionsOnly = !!opts.questionsOnly;
    var maxItems = opts.maxItems == null ? (compact ? 2 : 999) : opts.maxItems;
    var answerMax = opts.answerMax == null ? (compact && !questionsOnly ? 160 : 0) : opts.answerMax;
    var items = parseItems(ask);
    if (!items.length) {
      return '<span class="muted">暂无</span>';
    }
    var show = items.slice(0, maxItems);
    var html = show.map(function (it, idx) {
      var ans = it.answer || '';
      if (!questionsOnly && answerMax > 0) ans = truncate(ans, answerMax);
      var cls = 'rufus-item' + (compact ? ' rufus-item-compact' : '');
      if (questionsOnly) cls += ' rufus-item-q-only';
      var body = '<div class="rufus-item-question">' + escapeHtml(it.question || ('问题 ' + (idx + 1))) + '</div>';
      if (!questionsOnly && ans) {
        body += '<div class="rufus-item-answer">' + nl2br(ans) + '</div>';
      }
      return '<div class="' + cls + '">' + body + '</div>';
    }).join('');
    if (items.length > show.length) {
      var hint = questionsOnly
        ? '还有 ' + (items.length - show.length) + ' 个问题，点击展开查看解答'
        : '还有 ' + (items.length - show.length) + ' 条，点击展开查看';
      html += '<div class="rufus-more-hint">' + hint + '</div>';
    }
    return '<div class="rufus-list">' + html + '</div>';
  }

  global.AskRufusRender = {
    parseItems: parseItems,
    extractQA: extractQA,
    buildListHtml: buildListHtml,
    escapeHtml: escapeHtml,
    nl2br: nl2br
  };
})(typeof window !== 'undefined' ? window : globalThis);
