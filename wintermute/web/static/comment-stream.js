/**
 * Reusable WebSocket comment stream handler.
 *
 * Usage:
 *   var stream = new CommentStream({
 *     wsUrl: '/ws/agents/123/comments',
 *     listEl: document.getElementById('conversation-list'),
 *     typingEl: document.getElementById('typing-indicator'),
 *     lastCommentTs: '2024-01-01T00:00:00Z',
 *     onComment: function(comment) { ... },  // optional callback
 *     formatComment: function(comment) { return html; }  // optional custom formatter
 *   });
 *   stream.connect();
 *   stream.disconnect();
 */
(function(global) {
  'use strict';

  function CommentStream(options) {
    this.wsUrl = options.wsUrl;
    this.listEl = options.listEl;
    this.typingEl = options.typingEl;
    this.lastCommentTs = options.lastCommentTs || null;
    this.onComment = options.onComment || null;
    this.formatComment = options.formatComment || this._defaultFormatComment.bind(this);

    this.ws = null;
    this.reconnectTimer = null;
    this.seenIds = new Set();

    // Initialize seen IDs from existing comments
    if (this.listEl) {
      var existing = this.listEl.querySelectorAll('[data-id]');
      for (var i = 0; i < existing.length; i++) {
        var id = existing[i].getAttribute('data-id');
        if (id) this.seenIds.add(id);
      }
    }
  }

  CommentStream.prototype._defaultFormatComment = function(c) {
    var isAgent = c.author !== 'user';
    var time = c.created_at ? c.created_at.substring(0, 16).replace('T', ' ') : '';
    var div = document.createElement('div');
    div.className = 'comment';
    div.setAttribute('data-id', c.id);
    div.style = 'padding: 8px 12px; margin-bottom: 8px; background: var(--bg-alt); border-radius: 4px; ' +
      (isAgent ? 'border-left: 3px solid var(--accent);' : 'border-left: 3px solid var(--text-muted);');
    div.innerHTML = '<div style="font-size: 11px; color: var(--text-muted); margin-bottom: 4px;">' +
      '<strong>' + (c.author || 'user') + '</strong>' +
      (c.origin ? ' <span>(' + c.origin + ')</span>' : '') +
      '<span style="float: right;">' + time + '</span></div>' +
      '<div style="white-space: pre-wrap;">' + this._escapeHtml(c.body || '') + '</div>';
    return div;
  };

  CommentStream.prototype._escapeHtml = function(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  };

  CommentStream.prototype.appendComment = function(c) {
    if (!this.listEl) return;
    if (this.seenIds.has(c.id)) return;
    this.seenIds.add(c.id);

    // Remove empty placeholder
    var emptyEl = this.listEl.querySelector('.muted');
    if (emptyEl && !this.listEl.querySelector('.comment')) {
      emptyEl.remove();
    }

    var el = this.formatComment(c);
    this.listEl.appendChild(el);
    this.listEl.scrollTop = this.listEl.scrollHeight;

    // Update last timestamp
    if (c.created_at && (!this.lastCommentTs || c.created_at > this.lastCommentTs)) {
      this.lastCommentTs = c.created_at;
    }

    // Call optional callback
    if (this.onComment) {
      this.onComment(c);
    }
  };

  CommentStream.prototype.connect = function() {
    var self = this;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;

    var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = protocol + '//' + window.location.host + this.wsUrl;
    if (this.lastCommentTs) {
      url += (url.indexOf('?') >= 0 ? '&' : '?') + 'since=' + encodeURIComponent(this.lastCommentTs);
    }

    this.ws = new WebSocket(url);

    this.ws.onmessage = function(event) {
      try {
        var msg = JSON.parse(event.data);
        if (msg.type === 'comment' && msg.data) {
          self.appendComment(msg.data);
        } else if (msg.type === 'typing' && msg.data && self.typingEl) {
          if (msg.data.active) {
            self.typingEl.classList.remove('hidden');
          } else {
            self.typingEl.classList.add('hidden');
          }
        }
      } catch (err) {
        console.warn('CommentStream: message parse error', err);
      }
    };

    this.ws.onclose = function() {
      if (!self.reconnectTimer) {
        self.reconnectTimer = setTimeout(function() {
          self.reconnectTimer = null;
          self.connect();
        }, 3000);
      }
    };

    this.ws.onerror = function(err) {
      console.warn('CommentStream: WebSocket error', err);
    };
  };

  CommentStream.prototype.disconnect = function() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  };

  // Export
  global.CommentStream = CommentStream;

})(window);
