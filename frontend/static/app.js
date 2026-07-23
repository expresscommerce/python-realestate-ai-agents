document.addEventListener('DOMContentLoaded', () => {
  const toggleBtn = document.getElementById('theme-toggle');
  const themeIcon = document.getElementById('theme-icon');
  const themeText = document.getElementById('theme-text');

  if (toggleBtn && themeIcon && themeText) {
    // 1. Check for saved preference or fallback to system settings
    const savedTheme = localStorage.getItem('theme');
    const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const initialTheme = savedTheme || (systemPrefersDark ? 'dark' : 'light');

    // 2. Apply the theme and update button text immediately
    applyTheme(initialTheme);

    // 3. Listen for clicks to switch themes
    toggleBtn.addEventListener('click', () => {
      const currentTheme = document.documentElement.getAttribute('data-theme');
      const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

      applyTheme(newTheme);
      localStorage.setItem('theme', newTheme);
    });

    // Helper function to update both HTML attribute and button UI
    function applyTheme(theme) {
      document.documentElement.setAttribute('data-theme', theme);

      if (theme === 'dark') {
        themeIcon.textContent = '☀️';
        themeText.textContent = 'Light Mode';
      } else {
        themeIcon.textContent = '🌙';
        themeText.textContent = 'Dark Mode';
      }
    }
  }

  const sendBtn = document.getElementById('send-btn');
  const input = document.getElementById('msg-input');

  if (sendBtn && input) {
    sendBtn.addEventListener('click', () => send(input.value));
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send(input.value);
      }
    });
    input.addEventListener('input', () => {
      input.style.height = '44px';
      input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    });
  }

  const chat = document.getElementById('chat');
  if (chat) {
    // Safety net: force every link inside the chat to open in a new tab
    chat.addEventListener('click', (e) => {
      const a = e.target.closest('a');
      if (!a) return;
      const href = a.getAttribute('href');
      if (!href || !/^https?:\/\//i.test(href)) return;
      e.preventDefault();
      window.open(href, '_blank', 'noopener,noreferrer');
    });

    // Initial welcome message
    addMsg('bot',
      "**Welcome to PropertyBot!**\n\n" +
      "I can help you:\n" +
      "- Find homes for sale or rent\n" +
      "- Compare properties\n" +
      "- Schedule visits\n\n" +
      "Just tell me what you're looking for.\n\n" +
      "Example:\n" +
      "\"Find a 3-bedroom house in Miami under $600,000.\""
    );
  }
});

let sessionId = localStorage.getItem('pb_sid') || null;
let busy = false;

function scrollBottom() {
  const chat = document.getElementById('chat');
  if (chat) {
    requestAnimationFrame(() => chat.scrollTop = chat.scrollHeight);
  }
}

function formatText(raw) {
  let t = raw
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^[-•]\s+(.+)$/gm, '<li>$1</li>')
    .replace(/^(\d+)\.\s+(.+)$/gm, '<li>$2</li>');

  // Markdown links [label](url) -> open in new tab
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

  // Bare URLs not already inside an href -> open in new tab
  t = t.replace(/(^|[^"'>])(https?:\/\/[^\s<)]+)/g,
    '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');

  t = t.replace(/((?:<li>.*<\/li>\s*)+)/g, '<ul>$1</ul>');

  // Parse markdown tables
  t = t.replace(/((?:^\|.+\|[ ]*\n?)+)/gm, (block) => {
    const rows = block.trim().split('\n').filter(r => r.trim());
    if (rows.length < 2) return block;

    const parseCells = (row) => row.split('|').slice(1, -1).map(c => c.trim());
    const isSeparator = (r) => /^\|[\s\-:|]+\|$/.test(r.trim());

    const headerCells = parseCells(rows[0]);
    let html = '<table><thead><tr>';
    headerCells.forEach(c => { html += '<th>' + c + '</th>'; });
    html += '</tr></thead><tbody>';

    for (let i = 1; i < rows.length; i++) {
      if (isSeparator(rows[i])) continue;
      const cells = parseCells(rows[i]);
      const feature = cells[0] || '';
      html += '<tr>';
      cells.forEach((c, ci) => {
        html += '<td' + (ci > 0 ? ' data-label="' + feature + '"' : '') + '>' + c + '</td>';
      });
      html += '</tr>';
    }
    return html + '</tbody></table>';
  });

  t = t.split('\n\n').map(p => {
    if (p.startsWith('<ul>') || p.startsWith('<li>') || p.startsWith('<table>')) return p;
    return '<p>' + p + '</p>';
  }).join('');

  // Convert "View listing" and "View on map" links into icon + text action buttons
  t = t.replace(
    /<li>\s*<strong>View listing:<\/strong>\s*<a href="([^"]+)"[^>]*>[^<]*<\/a><\/li>/g,
    '<li class="listing-actions"><a href="$1" target="_blank" rel="noopener noreferrer" class="listing-action-btn listing-view-btn">🏠 View Listing</a></li>'
  );
  t = t.replace(
    /<li>\s*<strong>View on map:<\/strong>\s*<a href="([^"]+)"[^>]*>[^<]*<\/a><\/li>/g,
    '<li class="listing-actions"><a href="$1" target="_blank" rel="noopener noreferrer" class="listing-action-btn listing-map-btn">📍 View on Map</a></li>'
  );

  return t.replace(/<p><\/p>/g, '') || raw;
}

/* ── Listing accordion helpers ── */

function parseListingResponse(text) {
  const parts = text.split(/(\*\*Option\s*\d+\*\*)/);
  if (parts.length < 3) return null;

  const intro = (parts[0] || '').replace(/\n{3,}/g, '\n\n').trim();
  const listings = [];
  for (let i = 1; i < parts.length; i += 2) {
    const header = parts[i];
    const content = (parts[i + 1] || '').trim();
    if (header && content) {
      listings.push(header + '\n' + content);
    }
  }
  return { intro, listings };
}

function renderListings(intro, listings, bubble) {
  const container = document.createElement('div');
  container.className = 'listings-container';

  if (intro) {
    const introDiv = document.createElement('div');
    introDiv.className = 'listings-intro';
    introDiv.innerHTML = formatText(intro);
    container.appendChild(introDiv);
  }

  const BATCH = 10;
  const allCards = [];

  listings.forEach((md, i) => {
    const card = document.createElement('div');
    card.className = 'listing-card' + (i < BATCH ? ' visible' : '');
    card.innerHTML = formatText(md);
    container.appendChild(card);
    allCards.push(card);
  });

  let nextIndex = BATCH;

  function updateBtn() {
    if (nextIndex >= allCards.length) {
      if (btn) btn.remove();
      return;
    }
    const remaining = allCards.length - nextIndex;
    btn.textContent = 'Show ' + Math.min(BATCH, remaining) + ' more (' + remaining + ' remaining)';
  }

  const btn = document.createElement('button');
  btn.className = 'show-more-btn';

  btn.onclick = function () {
    const end = Math.min(nextIndex + BATCH, allCards.length);
    for (let i = nextIndex; i < end; i++) {
      allCards[i].classList.add('visible');
    }
    nextIndex = end;
    updateBtn();
  };

  if (allCards.length > BATCH) {
    updateBtn();
    container.appendChild(btn);
  }

  bubble.appendChild(container);
}

function addMsg(role, text) {
  const chat = document.getElementById('chat');
  const tipToggleBtn = document.getElementById('tip-toggle');

  if (!chat) return;

  const wrap = document.createElement('div');
  wrap.className = 'msg ' + role;

  const av = document.createElement('div');
  av.className = 'avatar';
  av.innerHTML = role === 'bot' ? '&#127968;' : '&#128100;';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  if (role === 'bot' && /\*\*Option\s*\d/.test(text)) {
    if (window.innerWidth <= 480 && tipToggleBtn) {
      tipToggleBtn.classList.add('visible', 'pulse');
    } else {
      showQuickTip();
    }
    text = text.replace(/\n?\n?Say\s+['"]?show more['"]?.*$/i, '').trim();
    const parsed = parseListingResponse(text);
    if (parsed) {
      renderListings(parsed.intro, parsed.listings, bubble);
    } else {
      bubble.innerHTML = formatText(text);
    }
  } else {
    bubble.innerHTML = formatText(text);
  }

  wrap.appendChild(av);
  wrap.appendChild(bubble);
  chat.appendChild(wrap);
  scrollBottom();
}

function showTyping() {
  const chat = document.getElementById('chat');
  if (!chat) return;
  const el = document.createElement('div');
  el.className = 'msg bot';
  el.id = 'typing';
  el.innerHTML = '<div class="avatar">&#127968;</div><div class="bubble typing"><span></span><span></span><span></span></div>';
  chat.appendChild(el);
  scrollBottom();
}

function hideTyping() {
  const el = document.getElementById('typing');
  if (el) el.remove();
}

async function send(text) {
  text = text.trim();
  const sendBtn = document.getElementById('send-btn');
  const quickDiv = document.getElementById('quick-actions');
  const tipToggleBtn = document.getElementById('tip-toggle');
  const input = document.getElementById('msg-input');

  if (!text || busy) return;
  busy = true;
  if (sendBtn) sendBtn.disabled = true;
  if (quickDiv) quickDiv.style.display = 'none';

  addMsg('user', text);
  hideQuickTip();
  if (tipToggleBtn) tipToggleBtn.classList.remove('visible');
  if (input) {
    input.value = '';
    input.style.height = '44px';
  }
  showTyping();

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    const data = await res.json();
    sessionId = data.session_id;
    localStorage.setItem('pb_sid', sessionId);
    hideTyping();
    addMsg('bot', data.response);
  } catch {
    hideTyping();
    addMsg('bot', 'Connection issue — please try again.');
  }

  busy = false;
  if (sendBtn) sendBtn.disabled = false;
  if (input) input.focus();
}

function sendQuick(t) {
  send(t);
}

function showQuickTip() {
  const tip = document.getElementById("quick-tip");
  if (tip) tip.classList.remove("hidden");
}

function hideQuickTip() {
  const tip = document.getElementById("quick-tip");
  const tipToggleBtn = document.getElementById('tip-toggle');
  if (tip) tip.classList.add("hidden");
  if (tipToggleBtn) tipToggleBtn.classList.remove("pulse");
}

function toggleQuickTip() {
  const tip = document.getElementById("quick-tip");
  if (!tip) return;
  if (tip.classList.contains("hidden")) {
    showQuickTip();
  } else {
    hideQuickTip();
  }
}

function fillInput(text) {
  const input = document.getElementById('msg-input');
  if (input) {
    input.value = text;
    input.focus();
  }
}
