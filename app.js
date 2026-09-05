/* ===== World Events v2 —— 世界事件档案（事件≠新闻，一事件多来源） ===== */

// ===== 基础工具 =====
function escapeHtml(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function highlight(text, kw) {
  const esc = escapeHtml(text);
  if (!kw) return esc;
  const safeKw = escapeHtml(kw).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  try {
    return esc.replace(new RegExp(safeKw, 'gi'), m => `<mark style="background:#FEF08A;color:#713F12;border-radius:3px;padding:0 1px">${m}</mark>`);
  } catch (e) { return esc; }
}

function catOf(ev) {
  const c = ev.category;
  return Array.isArray(c) ? (c[0] || '其他') : (c || '其他');
}
function regionOf(ev) {
  const loc = ev.location || {};
  return loc.region || ev.region || '全球';
}
function countryOf(ev) {
  const loc = ev.location || {};
  return loc.country || ev.country || '';
}

function getCategoryColor(cat) {
  const map = {
    '自然灾害': '#185FA5', '国际政治': '#534AB7', '科技': '#0C447C',
    '环境': '#3B6D11', '体育': '#BA7517', '经济': '#854F0B',
    '社会': '#A32D2D', '其他': '#5F5E5A'
  };
  return map[cat] || '#5F5E5A';
}
function getCategoryBg(cat) {
  const map = {
    '自然灾害': '#E6F1FB', '国际政治': '#EEEDFE', '科技': '#E6F1FB',
    '环境': '#EAF3DE', '体育': '#FAEEDA', '经济': '#FAEEDA',
    '社会': '#FCEBEB', '其他': '#F1EFE8'
  };
  return map[cat] || '#F1EFE8';
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' });
}

// ===== 数据加载 =====
let __fullEvents = null;

async function getFullEvents() {
  if (__fullEvents) return __fullEvents;
  const embedded = window.__EMBEDDED_EVENTS__ || [];
  if (!window.__TOTAL_EVENTS__ || embedded.length >= window.__TOTAL_EVENTS__) {
    return embedded;
  }
  try {
    const resp = await fetch('./data/events.json?t=' + Date.now());
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = JSON.parse(await resp.text());
    if (Array.isArray(data) && data.length > 0) {
      __fullEvents = data;
      return data;
    }
  } catch (e) {
    console.warn('加载全量数据失败，降级使用内嵌数据:', e.message);
  }
  return embedded;
}

async function loadEvents() {
  if (window.__EMBEDDED_EVENTS__ && Array.isArray(window.__EMBEDDED_EVENTS__) && window.__EMBEDDED_EVENTS__.length > 0) {
    return window.__EMBEDDED_EVENTS__;
  }
  try {
    const resp = await fetch('./data/events.json?t=' + Date.now());
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return JSON.parse(await resp.text());
  } catch (e) {
    console.warn('加载 events.json 失败:', e.message);
    return [];
  }
}

// 按 id 或旧 ID（legacyIds）查找事件，兼容历史分享链接
function findEvent(events, id) {
  return events.find(e => e.id === id)
      || events.find(e => (e.legacyIds || []).includes(id));
}

// 地区固定排序（具体在前，大洲在后）
const REGION_ORDER = ['中国', '美国', '俄罗斯', '乌克兰', '欧盟', '印度', '东南亚', '中东', '苏丹',
                      '亚洲', '欧洲', '非洲', '北美洲', '南美洲', '大洋洲', '全球'];
function sortRegions(regions) {
  return regions.sort((a, b) => {
    const ia = REGION_ORDER.indexOf(a), ib = REGION_ORDER.indexOf(b);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });
}

// ===== 事件卡片（搜索结果/列表通用） =====
function renderEventCard(ev, kw) {
  const cat = catOf(ev);
  const nSrc = (ev.sources || []).length;
  const region = regionOf(ev);
  let extra = '';
  if (ev.status === 'ongoing') extra += ' <span style="color:#B45309;font-size:12px">● 持续发展</span>';
  if (nSrc > 1) extra += ` <span style="color:var(--text-secondary);font-size:12px">📎 ${nSrc} 个来源</span>`;
  return `
    <a class="timeline-item" href="./event.html?id=${escapeHtml(ev.id)}">
      <div class="timeline-dot" style="background:${getCategoryColor(cat)};box-shadow:0 0 0 2px ${getCategoryColor(cat)}"></div>
      <div class="timeline-date">
        <span class="timeline-category" style="background:${getCategoryBg(cat)};color:${getCategoryColor(cat)}">${escapeHtml(cat)}</span>
        ${formatDate(ev.date)} · ${escapeHtml(region)}${extra}
      </div>
      <div class="timeline-title">${highlight(ev.title, kw)}</div>
      <div class="timeline-summary">${highlight(ev.summary, kw)}</div>
      <div class="timeline-meta">
        ${(ev.tags || []).map(t => `<span class="timeline-tag">#${escapeHtml(t)}</span>`).join('')}
      </div>
    </a>`;
}

const SEARCH_RENDER_LIMIT = 200;

// ===== 首页：日期中心视图（"今天世界发生了什么"） =====
async function initDateView() {
  const list = document.getElementById('timeline-list');
  if (!list) return;
  const events = await getFullEvents();
  if (!events.length) {
    list.innerHTML = '<div class="empty-state"><p>暂无事件数据</p></div>';
    return;
  }
  const allDates = [...new Set(events.map(e => e.date))].sort();

  // 当前日期：URL ?date= > 全库最新日期
  const params = new URLSearchParams(location.search);
  let current = params.get('date') || window.__LATEST_DATE__ || allDates[allDates.length - 1];
  if (!allDates.includes(current)) {
    current = allDates.find(d => d >= current) || allDates[allDates.length - 1];
  }

  function renderDay(date) {
    current = date;
    const dayEvents = events.filter(e => e.date === date).sort((a, b) => (a.id > b.id ? 1 : -1));
    const idx = allDates.indexOf(date);
    const prev = idx > 0 ? allDates[idx - 1] : null;
    const next = idx < allDates.length - 1 ? allDates[idx + 1] : null;
    const nav = document.getElementById('date-nav');
    if (nav) {
      nav.innerHTML = `
        <button class="date-btn" ${prev ? `onclick="gotoDate('${prev}')"` : 'disabled'}>← ${prev || '—'}</button>
        <input type="date" id="date-picker" value="${date}" min="${allDates[0]}" max="${allDates[allDates.length-1]}" style="padding:6px 10px;border:1px solid var(--border);border-radius:8px;font-size:14px">
        <button class="date-btn" ${next ? `onclick="gotoDate('${next}')"` : 'disabled'}>${next || '—'} →</button>
        <button class="date-btn" onclick="gotoDate('${allDates[allDates.length-1]}')">最新</button>`;
      const picker = document.getElementById('date-picker');
      if (picker) picker.addEventListener('change', e => { if (e.target.value) gotoDate(e.target.value); });
    }
    const head = document.getElementById('day-title');
    if (head) head.textContent = formatDate(date) + ' · 世界大事';
    const cnt = document.getElementById('day-count');
    if (cnt) cnt.textContent = `当日 ${dayEvents.length} 个事件`;
    if (!dayEvents.length) {
      list.innerHTML = '<div class="empty-state"><p>该日期暂无事件记录</p></div>';
      return;
    }
    list.innerHTML = dayEvents.map(ev => renderEventCard(ev, '')).join('');
    const u = new URL(location.href);
    u.searchParams.set('date', date);
    history.replaceState(null, '', u);
  }
  window.gotoDate = function (d) {
    const si = document.getElementById('search-input');
    if (si) si.value = '';
    const df = document.getElementById('date-from'); if (df) df.value = '';
    const dt = document.getElementById('date-to'); if (dt) dt.value = '';
    renderDay(d);
    window.scrollTo(0, 0);
  };
  renderDay(current);
}

// ===== 首页：搜索（关键词 + 日期范围，命中高亮，一键清空） =====
function initFilters() {
  const searchInput = document.getElementById('search-input');
  const catSelect = document.getElementById('cat-filter');
  const regionSelect = document.getElementById('region-filter');
  const dateFrom = document.getElementById('date-from');
  const dateTo = document.getElementById('date-to');
  if (!searchInput) return;

  getFullEvents().then(events => {
    const cats = [...new Set(events.map(catOf))].sort();
    const regions = sortRegions([...new Set(events.map(regionOf))]);
    if (catSelect) cats.forEach(c => { catSelect.innerHTML += `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`; });
    if (regionSelect) regions.forEach(r => { regionSelect.innerHTML += `<option value="${escapeHtml(r)}">${escapeHtml(r)}</option>`; });
  });

  let debounceTimer = null;
  let lastRenderedDay = true; // 当前是否处于日期视图
  const doFilter = async () => {
    const keyword = searchInput.value.trim().toLowerCase();
    const catVal = catSelect ? catSelect.value : '';
    const regionVal = regionSelect ? regionSelect.value : '';
    const dFrom = dateFrom ? dateFrom.value : '';
    const dTo = dateTo ? dateTo.value : '';
    const events = await getFullEvents();

    // 什么条件都没填 → 恢复日期视图
    if (!keyword && !catVal && !regionVal && !dFrom && !dTo) {
      if (window.gotoDate && !lastRenderedDay) window.gotoDate(window.__LATEST_DATE__ || '');
      lastRenderedDay = true;
      return;
    }

    const filtered = events.filter(ev => {
      if (catVal && catOf(ev) !== catVal) return false;
      if (regionVal && regionOf(ev) !== regionVal) return false;
      if (dFrom && ev.date < dFrom) return false;
      if (dTo && ev.date > dTo) return false;
      if (keyword) {
        const hay = [ev.title, ev.summary, ev.description, ev.aiSummary,
                     (ev.location || {}).country, regionOf(ev),
                     ...(ev.tags || []),
                     ...(ev.sources || []).map(s => s.name + ' ' + (s.title || ''))].join(' ').toLowerCase();
        return hay.includes(keyword);
      }
      return true;
    });
    lastRenderedDay = false;

    filtered.sort((a, b) => b.date.localeCompare(a.date));
    const list = document.getElementById('timeline-list');
    if (!list) return;
    if (filtered.length === 0) {
      list.innerHTML = `
        <div class="empty-state">
          <p>🔍 没有找到与“${escapeHtml(searchInput.value.trim())}”相关的事件</p>
          <p style="margin-top:6px;font-size:13px;color:var(--text-secondary)">试试更换关键词，或调整分类/地区/日期范围</p>
          <button onclick="clearSearch()" class="btn btn-primary" style="margin-top:14px">✕ 一键清空搜索，回到今日事件</button>
        </div>`;
      return;
    }
    const kw = searchInput.value.trim();
    const shown = filtered.slice(0, SEARCH_RENDER_LIMIT);
    list.innerHTML = `
      <div class="load-more-tip">🔍 共命中 ${filtered.length} 个事件（按日期排序）</div>` +
      shown.map(ev => renderEventCard(ev, kw)).join('') +
      (filtered.length > SEARCH_RENDER_LIMIT
        ? `<div class="load-more-tip">已显示前 ${SEARCH_RENDER_LIMIT} 条，可继续缩小范围</div>` : '');
  };

  window.clearSearch = function () {
    searchInput.value = '';
    if (catSelect) catSelect.value = '';
    if (regionSelect) regionSelect.value = '';
    if (dateFrom) dateFrom.value = '';
    if (dateTo) dateTo.value = '';
    if (window.gotoDate) window.gotoDate(window.__LATEST_DATE__ || '');
    else location.href = './index.html';
  };

  searchInput.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(doFilter, 250);
  });
  [catSelect, regionSelect, dateFrom, dateTo].forEach(el => {
    if (el) el.addEventListener('change', doFilter);
  });
}

// ===== 事件详情页：历史档案式 =====
async function renderEventDetail() {
  const container = document.getElementById('event-detail');
  if (!container) return;

  const params = new URLSearchParams(window.location.search);
  const id = params.get('id');
  if (!id) {
    container.innerHTML = '<div class="empty-state"><p>未找到事件ID</p><p><a href="./index.html">返回首页</a></p></div>';
    return;
  }
  const events = await loadEvents();
  const ev = findEvent(events, id);
  if (!ev) {
    container.innerHTML = '<div class="empty-state"><p>未找到该事件</p><p><a href="./index.html">返回首页</a></p></div>';
    return;
  }

  const cat = catOf(ev);
  const loc = ev.location || {};
  const region = loc.region || ev.region || '全球';
  const country = loc.country || ev.country || '';
  const nSrc = (ev.sources || []).length;

  const statusBadge = ev.status === 'ongoing'
    ? '<span style="background:#FEF3C7;color:#B45309;font-size:12px;font-weight:600;border-radius:4px;padding:2px 8px">● 持续发展中</span>'
    : '<span style="background:#F1F5F9;color:#475569;font-size:12px;font-weight:600;border-radius:4px;padding:2px 8px">已记录</span>';

  // AI 速览（含来源标注）
  let aiBox = '';
  if (ev.aiSummary && ev.aiSummary.trim()) {
    const aiNote = ev.summaryFull ? '基于全文总结提炼' : '基于正文开头提炼';
    const aiBadge = ev.summaryFull ? '<span style="background:#2563EB;color:#fff;font-size:11px;font-weight:600;border-radius:4px;padding:1px 6px;margin-left:6px;vertical-align:1px">全文版</span>' : '';
    aiBox = `
      <div style="background:linear-gradient(135deg,#EAF3FB,#F0FDF6);border:1px solid #BFDBFE;border-left:4px solid #2563EB;border-radius:10px;padding:16px 18px;margin-bottom:18px">
        <div style="font-size:13px;font-weight:700;color:#1D4ED8;margin-bottom:8px">⚡ 一分钟速览（AI 提炼）${aiBadge}</div>
        <div style="font-size:15px;line-height:1.9;color:#1F2937">${escapeHtml(ev.aiSummary)}</div>
        <div style="font-size:12px;color:#6B7280;margin-top:8px">${aiNote} · 原始报道见下方"新闻来源"</div>
      </div>`;
  }

  // 事件时间线
  let timelineHtml = '';
  const tl = ev.timeline || [];
  if (tl.length) {
    const nodes = tl.map((t, i) => `
      <div style="display:flex;gap:12px">
        <div style="display:flex;flex-direction:column;align-items:center">
          <div style="width:10px;height:10px;border-radius:50%;background:${getCategoryColor(cat)};flex-shrink:0;margin-top:6px"></div>
          ${i < tl.length - 1 ? '<div style="width:2px;flex:1;background:var(--border);min-height:24px"></div>' : ''}
        </div>
        <div style="padding-bottom:16px">
          <div style="font-size:12px;color:var(--text-secondary);font-weight:600;margin-bottom:2px">${escapeHtml(t.date)}</div>
          <div style="font-size:14px;line-height:1.7">${escapeHtml(t.text || '')}</div>
        </div>
      </div>`).join('');
    timelineHtml = `
      <h2 style="font-size:18px;font-weight:700;margin:24px 0 12px;border-left:4px solid ${getCategoryColor(cat)};padding-left:10px">事件时间线</h2>
      <div>${nodes}</div>`;
  }

  // 新闻来源（原始链接 = 版权合规的证据入口）
  let sourcesHtml = '';
  const srcs = ev.sources || [];
  if (srcs.length) {
    const items = srcs.map(s => `
      <li style="margin-bottom:10px">
        <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener" style="color:var(--primary);text-decoration:none;font-weight:600">
          ${escapeHtml(s.title || s.url || s.name)} ↗
        </a>
        <div style="font-size:12px;color:var(--text-secondary);margin-top:2px">${escapeHtml(s.name || '未知来源')} · ${escapeHtml(s.date || '')}${s.snippet ? ' · ' + escapeHtml(s.snippet) : ''}</div>
      </li>`).join('');
    sourcesHtml = `
      <h2 style="font-size:18px;font-weight:700;margin:24px 0 12px;border-left:4px solid ${getCategoryColor(cat)};padding-left:10px">新闻来源（${srcs.length}）</h2>
      <ul style="padding-left:20px;margin:0;list-style:disc">${items}</ul>
      <p style="font-size:12px;color:var(--text-secondary);margin-top:10px">本页仅收录自行整理的事件摘要与来源链接，完整报道请点击原文阅读，版权属原媒体。</p>`;
  }

  // 相关事件（同地区最近的几个其他事件）
  let relatedHtml = '';
  const related = events.filter(e => e.id !== ev.id && regionOf(e) === region)
                        .sort((a, b) => b.date.localeCompare(a.date)).slice(0, 4);
  if (related.length) {
    relatedHtml = `
      <h2 style="font-size:18px;font-weight:700;margin:24px 0 12px;border-left:4px solid ${getCategoryColor(cat)};padding-left:10px">相关事件（${escapeHtml(region)}）</h2>
      ${related.map(r => `
        <a href="./event.html?id=${escapeHtml(r.id)}" style="display:block;padding:10px 0;border-bottom:1px solid var(--border);text-decoration:none;color:inherit">
          <span style="font-size:12px;color:var(--text-secondary)">${escapeHtml(r.date)}</span>
          <div style="font-size:14px;font-weight:600;margin-top:2px">${escapeHtml(r.title)}</div>
        </a>`).join('')}`;
  }

  container.innerHTML = `
    <div class="event-meta" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px">
      <span class="timeline-category" style="background:${getCategoryBg(cat)};color:${getCategoryColor(cat)}">${escapeHtml(cat)}</span>
      ${statusBadge}
      <span class="event-meta-item">📅 ${formatDate(ev.date)}</span>
      ${(country || region) ? `<span class="event-meta-item">📍 ${escapeHtml(country ? (country + '（' + region + '）') : region)}</span>` : ''}
      ${nSrc > 1 ? `<span class="event-meta-item">📎 ${nSrc} 个来源</span>` : ''}
    </div>
    <h1 style="font-size:24px;font-weight:700;margin-bottom:12px;line-height:1.5">${escapeHtml(ev.title)}</h1>
    ${aiBox}
    <div class="event-content" style="margin-bottom:8px">
      <p style="font-size:15px;line-height:1.9">${escapeHtml(ev.summary || '')}</p>
      ${ev.description && ev.description !== ev.summary ? `<p style="font-size:14px;line-height:1.9;color:var(--text-secondary)">${escapeHtml(ev.description)}</p>` : ''}
    </div>
    ${timelineHtml}
    ${sourcesHtml}
    ${relatedHtml}
    <div style="margin-top:20px">
      ${(ev.tags || []).map(t => `<span class="timeline-tag" style="margin-bottom:4px;display:inline-block">#${escapeHtml(t)}</span>`).join('')}
    </div>
    <div style="margin-top:24px;padding-top:20px;border-top:1px solid var(--border)">
      <a href="./index.html" style="color:var(--primary);text-decoration:none">← 返回今日世界</a>
    </div>
  `;
}

// ===== 地区档案页 =====
async function renderCountry() {
  const container = document.getElementById('country-detail');
  if (!container) return;
  const events = await getFullEvents();
  const regions = sortRegions([...new Set(events.map(regionOf))]);

  const params = new URLSearchParams(window.location.search);
  let region = params.get('region') || '';
  if (!regions.includes(region)) region = regions[0] || '';

  const sel = document.getElementById('region-select');
  if (sel) {
    sel.innerHTML = regions.map(r => `<option value="${escapeHtml(r)}"${r === region ? ' selected' : ''}>${escapeHtml(r)}</option>`).join('');
    sel.addEventListener('change', e => {
      const u = new URL(location.href); u.searchParams.set('region', e.target.value);
      location.href = u.toString();
    });
  }

  const list = events.filter(e => regionOf(e) === region).sort((a, b) => b.date.localeCompare(a.date));
  const cnt = document.getElementById('region-count');
  if (cnt) cnt.textContent = `共 ${list.length} 个事件 · 覆盖 ${new Set(list.map(e => e.date)).size} 天`;

  const byMonth = {};
  list.forEach(e => { (byMonth[e.date.slice(0, 7)] = byMonth[e.date.slice(0, 7)] || []).push(e); });
  container.innerHTML = Object.keys(byMonth).sort().reverse().map(ym => `
    <h2 style="font-size:17px;font-weight:700;margin:24px 0 8px;color:var(--primary)">${ym.replace('-', '年')}月</h2>
    ${byMonth[ym].map(ev => renderEventCard(ev, '')).join('')}`).join('');
}

// ===== 管理后台 =====
async function renderAdminList() {
  const list = document.getElementById('admin-list');
  if (!list) return;
  const events = await loadEvents();
  events.sort((a, b) => b.date.localeCompare(a.date));
  list.innerHTML = events.map(ev => `
    <div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--border);flex-wrap:wrap">
      <span style="color:var(--text-secondary);font-size:13px;min-width:90px">${escapeHtml(ev.date)}</span>
      <span style="flex:1;font-size:14px">${escapeHtml(ev.title)}</span>
      <span class="timeline-category" style="background:${getCategoryBg(catOf(ev))};color:${getCategoryColor(catOf(ev))}">${escapeHtml(catOf(ev))}</span>
      <button onclick="deleteEvent('${escapeHtml(ev.id)}')" class="btn btn-danger" style="font-size:12px;padding:4px 10px">删除</button>
    </div>`).join('');
}

const CC_MAP = {'中国':'CN','美国':'US','俄罗斯':'RU','乌克兰':'UA','欧盟':'EU','印度':'IN','日本':'JP','韩国':'KR','英国':'GB','法国':'FR','德国':'DE','印尼':'ID','印度尼西亚':'ID','泰国':'TH','越南':'VN','菲律宾':'PH','巴基斯坦':'PK','伊朗':'IR','以色列':'IL','巴勒斯坦':'PS','沙特阿拉伯':'SA','土耳其':'TR','埃及':'EG','南非':'ZA','尼日利亚':'NG','苏丹':'SD','巴西':'BR','加拿大':'CA','澳大利亚':'AU','孟加拉国':'BD','尼泊尔':'NP','阿富汗':'AF','伊拉克':'IQ','墨西哥':'MX','阿根廷':'AR','西班牙':'ES','意大利':'IT','瑞典':'SE','瑞士':'CH','荷兰':'NL','波兰':'PL','匈牙利':'HU','捷克':'CZ','塞尔维亚':'RS','克罗地亚':'HR','拉脱维亚':'LV','爱沙尼亚':'EE','海地':'HT','委内瑞拉':'VE','智利':'CL','哥伦比亚':'CO','秘鲁':'PE','古巴':'CU','哈萨克斯坦':'KZ','乌兹别克斯坦':'UZ','吉尔吉斯斯坦':'KG','新加坡':'SG','马来西亚':'MY','蒙古':'MN','新西兰':'NZ','爱尔兰':'IE','奥地利':'AT','比利时':'BE','希腊':'GR','丹麦':'DK','挪威':'NO','芬兰':'FI','葡萄牙':'PT'};

async function saveEvent(formData) {
  const events = await loadEvents();
  const date = formData.get('date') || new Date().toISOString().slice(0, 10);
  const n = events.filter(e => e.date === date).length + 1;
  const country = formData.get('country') || '未知';
  const region = formData.get('region') || '其他';
  const cat = formData.get('category') || '其他';
  const title = formData.get('title') || '无标题';
  const summary = formData.get('summary') || '';

  const newEv = {
    id: `evt_${date.replace(/-/g, '')}_${String(n).padStart(3, '0')}`,
    legacyIds: [],
    title,
    summary,
    description: summary,
    date,
    time: '',
    location: { country, countryCode: CC_MAP[country] || '', region, city: '' },
    category: [cat],
    tags: (formData.get('tags') || '').split(/[,，]/).map(t => t.trim()).filter(Boolean),
    status: 'closed',
    sources: (formData.get('sourceUrl') || formData.get('source')) ? [{
      name: formData.get('source') || '', url: formData.get('sourceUrl') || '',
      date, title, snippet: summary.slice(0, 120)
    }] : [],
    timeline: [{ date, text: summary.slice(0, 90) }],
    relatedEvents: [],
  };
  events.push(newEv);
  await saveEventsFile(events);
  showToast('✅ 事件已保存！');
  setTimeout(() => location.reload(), 800);
}

async function deleteEvent(id) {
  if (!confirm('确定要删除这个事件吗？')) return;
  const events = await loadEvents();
  await saveEventsFile(events.filter(e => e.id !== id));
  showToast('🗑️ 已删除');
  setTimeout(() => location.reload(), 800);
}

async function saveEventsFile(events) {
  const json = JSON.stringify(events, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'events.json'; a.click();
  URL.revokeObjectURL(url);
  showToast('📥 events.json 已下载，请放入 data/ 文件夹覆盖原文件');
}

function showToast(msg) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

function showError(msg) {
  const list = document.getElementById('timeline-list');
  if (list) list.innerHTML = `<div class="empty-state"><p>⚠️ ${msg}</p><p style="margin-top:8px;font-size:13px">请尝试强制刷新（Ctrl+F5）</p></div>`;
}

// ===== 页面初始化 =====
document.addEventListener('DOMContentLoaded', () => {
  const page = location.pathname.split('/').pop() || 'index.html';
  if (page === 'index.html' || page === '') {
    initDateView();
    initFilters();
  } else if (page === 'event.html') {
    renderEventDetail();
  } else if (page === 'country.html') {
    renderCountry();
  } else if (page === 'admin.html') {
    renderAdminList();
    const form = document.getElementById('event-form');
    if (form) {
      form.addEventListener('submit', e => {
        e.preventDefault();
        saveEvent(new FormData(form));
      });
    }
  }
});
