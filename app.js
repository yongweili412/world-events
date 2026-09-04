/* ===== 全球事件收集网站 - 主逻辑 ===== */

// ===== 工具函数 =====
function formatDate(dateStr) {
  const d = new Date(dateStr);
  return d.toLocaleDateString('zh-CN', { year:'numeric', month:'long', day:'numeric' });
}

function getCategoryColor(cat) {
  const map = {
    '自然灾害': '#185FA5',
    '国际政治': '#534AB7',
    '科技': '#0C447C',
    '环境': '#3B6D11',
    '体育': '#BA7517',
    '经济': '#854F0B',
    '社会': '#A32D2D',
    '其他': '#5F5E5A'
  };
  return map[cat] || '#5F5E5A';
}

function getCategoryBg(cat) {
  const map = {
    '自然灾害': '#E6F1FB',
    '国际政治': '#EEEDFE',
    '科技': '#E6F1FB',
    '环境': '#EAF3DE',
    '体育': '#FAEEDA',
    '经济': '#FAEEDA',
    '社会': '#FCEBEB',
    '其他': '#F1EFE8'
  };
  return map[cat] || '#F1EFE8';
}

// ===== 加载事件数据 =====
let __fullEvents = null; // 全量数据缓存（搜索时按需加载）

async function getFullEvents() {
  // 全量数据：首页内嵌的只是最近 300 条，搜索/筛选时需要全部历史
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
  // 优先使用内嵌数据（build.py 注入），完全不依赖网络请求，免疫缓存问题
  if (window.__EMBEDDED_EVENTS__ && Array.isArray(window.__EMBEDDED_EVENTS__) && window.__EMBEDDED_EVENTS__.length > 0) {
    return window.__EMBEDDED_EVENTS__;
  }
  try {
    const resp = await fetch('./data/events.json?t=' + Date.now());
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const text = await resp.text();
    if (!text.trim()) throw new Error('数据为空');
    return JSON.parse(text);
  } catch (e) {
    console.warn('加载 events.json 失败，使用空数据:', e.message);
    return [];
  }
}

// 地区选项展示顺序（具体在前，大洲在后）
const REGION_ORDER = ['中国', '美国', '俄罗斯', '乌克兰', '欧盟', '印度', '东南亚', '中东', '苏丹',
                      '亚洲', '欧洲', '非洲', '北美洲', '南美洲', '大洋洲', '全球'];
function sortRegions(regions) {
  return regions.sort((a, b) => {
    const ia = REGION_ORDER.indexOf(a), ib = REGION_ORDER.indexOf(b);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });
}

// 错误提示（不再让页面卡在"加载中"）
function showError(msg) {
  const list = document.getElementById('timeline-list');
  if (list) {
    list.innerHTML = `<div class="empty-state"><p>⚠️ ${msg}</p><p style="margin-top:8px;font-size:13px">请尝试强制刷新（Ctrl+F5），若仍不行请联系管理员</p></div>`;
  }
}

// ===== 首页：渲染时间线 =====

// HTML 转义（防注入，也保证高亮标记安全）
function escapeHtml(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// 关键词高亮：先转义再包 mark 标签
function highlight(text, kw) {
  const esc = escapeHtml(text);
  if (!kw) return esc;
  const safeKw = escapeHtml(kw).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  try {
    return esc.replace(new RegExp(safeKw, 'gi'), m => `<mark style="background:#FEF08A;color:#713F12;border-radius:3px;padding:0 1px">${m}</mark>`);
  } catch (e) {
    return esc;
  }
}

// 渲染单张时间线卡片（kw 非空时高亮命中关键词）
function renderEventCard(ev, kw) {
  return `
    <a class="timeline-item" href="./event.html?id=${ev.id}">
      <div class="timeline-dot" style="background:${getCategoryColor(ev.category)};box-shadow:0 0 0 2px ${getCategoryColor(ev.category)}"></div>
      <div class="timeline-date">
        <span class="timeline-category" style="background:${getCategoryBg(ev.category)};color:${getCategoryColor(ev.category)}">${ev.category}</span>
        ${formatDate(ev.date)} · ${ev.region || ''}
      </div>
      <div class="timeline-title">${highlight(ev.title, kw)}</div>
      <div class="timeline-summary">${highlight(ev.summary, kw)}</div>
      <div class="timeline-meta">
        ${(ev.tags||[]).map(t => `<span class="timeline-tag">#${escapeHtml(t)}</span>`).join('')}
      </div>
    </a>`;
}

// 搜索结果单次最多渲染条数（保证大数据量下响应迅速）
const SEARCH_RENDER_LIMIT = 200;

async function renderTimeline() {
  const list = document.getElementById('timeline-list');
  const statsEl = document.getElementById('stats-row');
  if (!list) return;

  try {
  const events = await loadEvents();

  // 统计（用全量数据计算，与静态版一致）
  if (statsEl) {
    const all = await getFullEvents();
    const cats = {};
    all.forEach(ev => { cats[ev.category] = (cats[ev.category]||0) + 1; });
    statsEl.innerHTML = `
      <div class="stat-card"><div class="stat-num">${all.length}</div><div class="stat-label">事件总数</div></div>
      <div class="stat-card"><div class="stat-num">${Object.keys(cats).length}</div><div class="stat-label">事件分类</div></div>
      <div class="stat-card"><div class="stat-num">${[...new Set(all.map(e=>e.region))].length}</div><div class="stat-label">涉及地区</div></div>
    `;
  }

  if (events.length === 0) {
    list.innerHTML = `
      <div class="empty-state">
        <p>暂无事件数据</p>
        <p style="margin-top:8px"><a href="./admin.html" class="btn btn-primary">去添加事件 →</a></p>
      </div>`;
    return;
  }

  // 按日期降序
  events.sort((a, b) => b.date.localeCompare(a.date));

  list.innerHTML = events.map(ev => renderEventCard(ev, '')).join('') + (
    window.__TOTAL_EVENTS__ && events.length < window.__TOTAL_EVENTS__
      ? `<div class="load-more-tip">📄 已显示最近 ${events.length} 条，更早的历史请用上方“月份归档”浏览</div>`
      : ''
  );
  } catch (err) {
    console.error('渲染时间线出错:', err);
    showError('页面渲染出错：' + err.message);
  }
}

// ===== 首页：搜索与筛选 =====
function initFilters() {
  const searchInput = document.getElementById('search-input');
  const catSelect = document.getElementById('cat-filter');
  const regionSelect = document.getElementById('region-filter');

  if (!searchInput) return;

  // 填充分类和地区选项（地区用固定顺序）
  getFullEvents().then(events => {
    const cats = [...new Set(events.map(e => e.category))].sort();
    const regions = sortRegions([...new Set(events.map(e => e.region))]);
    if (catSelect) {
      cats.forEach(c => { catSelect.innerHTML += `<option value="${c}">${c}</option>`; });
    }
    if (regionSelect) {
      regions.forEach(r => { regionSelect.innerHTML += `<option value="${r}">${r}</option>`; });
    }
  });

  // 输入防抖：避免全量数据频繁重渲染
  let debounceTimer = null;
  const doFilter = async () => {
    const keyword = searchInput.value.trim().toLowerCase();
    const catVal = catSelect ? catSelect.value : '';
    const regionVal = regionSelect ? regionSelect.value : '';
    const events = await getFullEvents();

    const filtered = events.filter(ev => {
      if (catVal && ev.category !== catVal) return false;
      if (regionVal && ev.region !== regionVal) return false;
      if (keyword) {
        const hay = [ev.title, ev.summary, ev.content, ev.aiSummary, ev.country, ev.region, ...(ev.tags||[])].join(' ').toLowerCase();
        return hay.includes(keyword);
      }
      return true;
    });

    filtered.sort((a, b) => b.date.localeCompare(a.date));
    const list = document.getElementById('timeline-list');
    if (!list) return;
    if (filtered.length === 0) {
      list.innerHTML = `
        <div class="empty-state">
          <p>🔍 没有找到与“${escapeHtml(searchInput.value.trim())}”相关的新闻</p>
          <p style="margin-top:6px;font-size:13px;color:var(--text-secondary)">试试更换关键词，或检查分类/地区筛选条件</p>
          <button onclick="clearSearch()" class="btn btn-primary" style="margin-top:14px">✕ 一键清空搜索，恢复完整列表</button>
        </div>`;
      return;
    }
    const kw = searchInput.value.trim();
    const shown = filtered.slice(0, SEARCH_RENDER_LIMIT);
    list.innerHTML = shown.map(ev => renderEventCard(ev, kw)).join('') + (
      filtered.length > SEARCH_RENDER_LIMIT
        ? `<div class="load-more-tip">🔍 共命中 ${filtered.length} 条，已显示前 ${SEARCH_RENDER_LIMIT} 条（按日期排序），可继续缩小关键词范围</div>`
        : (keyword && filtered.length > 0 ? `<div class="load-more-tip">🔍 共命中 ${filtered.length} 条</div>` : '')
    );
  };

  // 一键清空搜索：重置输入框与筛选器，恢复完整列表
  window.clearSearch = function() {
    searchInput.value = '';
    if (catSelect) catSelect.value = '';
    if (regionSelect) regionSelect.value = '';
    doFilter();
  };

  if (searchInput) searchInput.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(doFilter, 250);
  });
  if (catSelect) catSelect.addEventListener('change', doFilter);
  if (regionSelect) regionSelect.addEventListener('change', doFilter);
}

// ===== 事件详情页 =====
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
  const ev = events.find(e => e.id === id);
  if (!ev) {
    container.innerHTML = '<div class="empty-state"><p>未找到该事件</p><p><a href="./index.html">返回首页</a></p></div>';
    return;
  }

  // 视频嵌入
  let videoHtml = '';
  if (ev.videoUrl && ev.videoUrl.trim()) {
    const vid = ev.videoUrl.trim();
    if (vid.includes('youtube.com') || vid.includes('youtu.be')) {
      const ytId = vid.includes('v=') ? vid.split('v=')[1].split('&')[0] : vid.split('/').pop();
      videoHtml = `<iframe class="event-video" src="https://www.youtube.com/embed/${ytId}" allowfullscreen></iframe>`;
    } else {
      videoHtml = `<p><a href="${vid}" target="_blank" rel="noopener">📺 观看视频</a></p>`;
    }
  }

  // 图片
  let imgHtml = '';
  if (ev.image && ev.image.trim()) {
    imgHtml = `<img class="event-cover" src="${ev.image}" alt="${ev.title}" onerror="this.style.display='none'">`;
  }

  // 内容段落
  const contentHtml = (ev.content || '').split('\n').filter(l => l.trim()).map(l => `<p>${l.trim()}</p>`).join('');

  // AI 一分钟速览（aiSummary 字段由 AI 生成写入 events.json；summaryFull=true 表示基于原文全文提炼）
  let aiBox = '';
  if (ev.aiSummary && ev.aiSummary.trim()) {
    const aiNote = ev.summaryFull ? '基于全文总结提炼' : '基于正文开头提炼';
    const aiBadge = ev.summaryFull ? '<span style="background:#2563EB;color:#fff;font-size:11px;font-weight:600;border-radius:4px;padding:1px 6px;margin-left:6px;vertical-align:1px">全文版</span>' : '';
    aiBox = `
      <div style="background:linear-gradient(135deg,#EAF3FB,#F0FDF6);border:1px solid #BFDBFE;border-left:4px solid #2563EB;border-radius:10px;padding:16px 18px;margin-bottom:18px">
        <div style="font-size:13px;font-weight:700;color:#1D4ED8;margin-bottom:8px">⚡ 一分钟速览（AI 提炼）${aiBadge}</div>
        <div style="font-size:15px;line-height:1.9;color:#1F2937">${ev.aiSummary}</div>
        <div style="font-size:12px;color:#6B7280;margin-top:8px">${aiNote} · 完整报道请见文末"查看原文"</div>
      </div>`;
  }

  container.innerHTML = `
    ${imgHtml}
    ${aiBox}
    <div class="event-meta">
      <span class="timeline-category" style="background:${getCategoryBg(ev.category)};color:${getCategoryColor(ev.category)}">${ev.category}</span>
      <span class="event-meta-item">📅 ${formatDate(ev.date)}</span>
      <span class="event-meta-item">📍 ${ev.country}（${ev.region}）</span>
    </div>
    <h1 style="font-size:24px;font-weight:700;margin-bottom:12px;line-height:1.5">${ev.title}</h1>
    <div class="event-content">
      ${contentHtml}
    </div>
    ${videoHtml}
    ${ev.source ? `<div class="event-source">📎 来源：${ev.source}${ev.sourceUrl ? ` · <a href="${ev.sourceUrl}" target="_blank" rel="noopener">查看原文</a>` : ''}</div>` : ''}
    <div style="margin-top:20px">
      ${(ev.tags||[]).map(t => `<span class="timeline-tag" style="margin-bottom:4px;display:inline-block">#${t}</span>`).join('')}
    </div>
    <div style="margin-top:24px;padding-top:20px;border-top:1px solid var(--border)">
      <a href="./index.html" style="color:var(--primary);text-decoration:none">← 返回时间线</a>
    </div>
  `;
}

// ===== 管理后台：加载事件列表 =====
async function renderAdminList() {
  const list = document.getElementById('admin-list');
  if (!list) return;

  const events = await loadEvents();
  events.sort((a, b) => b.date.localeCompare(a.date));

  list.innerHTML = events.map(ev => `
    <div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--border);flex-wrap:wrap">
      <span style="color:var(--text-secondary);font-size:13px;min-width:90px">${ev.date}</span>
      <span style="flex:1;font-size:14px">${ev.title}</span>
      <span class="timeline-category" style="background:${getCategoryBg(ev.category)};color:${getCategoryColor(ev.category)}">${ev.category}</span>
      <button onclick="deleteEvent('${ev.id}')" class="btn btn-danger" style="font-size:12px;padding:4px 10px">删除</button>
    </div>
  `).join('');
}

// ===== 管理后台：保存事件 =====
async function saveEvent(formData) {
  const events = await loadEvents();
  const id = 'evt-' + Date.now().toString(36).toUpperCase();

  const newEv = {
    id: id,
    title: formData.get('title') || '无标题',
    date: formData.get('date') || new Date().toISOString().slice(0,10),
    category: formData.get('category') || '其他',
    country: formData.get('country') || '未知',
    region: formData.get('region') || '其他',
    summary: formData.get('summary') || '',
    content: formData.get('content') || '',
    image: formData.get('image') || '',
    videoUrl: formData.get('videoUrl') || '',
    source: formData.get('source') || '',
    sourceUrl: formData.get('sourceUrl') || '',
    tags: (formData.get('tags') || '').split(/[,，]/).map(t => t.trim()).filter(Boolean)
  };

  events.push(newEv);
  await saveEventsFile(events);
  showToast('✅ 事件已保存！');
  setTimeout(() => location.reload(), 800);
}

// ===== 管理后台：删除事件 =====
async function deleteEvent(id) {
  if (!confirm('确定要删除这个事件吗？')) return;
  const events = await loadEvents();
  const filtered = events.filter(e => e.id !== id);
  await saveEventsFile(filtered);
  showToast('🗑️ 已删除');
  setTimeout(() => location.reload(), 800);
}

// ===== 保存 JSON 文件（通过后台脚本） =====
async function saveEventsFile(events) {
  // 由于浏览器安全限制，无法直接写文件
  // 这里提供下载功能，用户手动替换 data/events.json
  const json = JSON.stringify(events, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'events.json';
  a.click();
  URL.revokeObjectURL(url);
  showToast('📥 events.json 已下载，请放入 data/ 文件夹覆盖原文件');
}

// ===== Toast 提示 =====
function showToast(msg) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// ===== 页面初始化 =====
document.addEventListener('DOMContentLoaded', () => {
  const page = location.pathname.split('/').pop() || 'index.html';

  if (page === 'index.html' || page === '') {
    renderTimeline();
    initFilters();
  } else if (page === 'event.html') {
    renderEventDetail();
  } else if (page === 'admin.html') {
    renderAdminList();
    const form = document.getElementById('event-form');
    if (form) {
      form.addEventListener('submit', e => {
        e.preventDefault();
        const fd = new FormData(form);
        saveEvent(fd);
      });
    }
  }
});
