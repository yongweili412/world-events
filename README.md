# 🌍 全球大事记 - 永久存档网站

一个简单、免费、数据完全由你掌控的全球事件收集网站。
数据不会被平台删除，真正永久存档。

---

## ✨ 功能特性

- 📅 **时间线展示**：按日期倒序排列全球大事
- 🔍 **搜索与筛选**：按关键词、分类、地区筛选事件
- 🖼️ **图文并茂**：支持封面图 + 正文图片 + 视频链接
- ✏️ **手动录入**：通过管理后台添加事件
- 🤖 **自动抓取**：Python 脚本定时从 RSS 新闻源抓取（参见 `scraper/scraper.py`）
- 📊 **统计面板**：事件总数、分类数、涉及地区一目了然
- 🔒 **公私切换**：默认公开，加密码即变私有
- 🆓 **完全免费托管**：可部署到 GitHub Pages

---

## 📁 文件结构

```
world-events/
├── index.html          # 首页（时间线）
├── event.html         # 事件详情页
├── admin.html         # 管理后台（手动录入）
├── style.css          # 样式文件
├── app.js             # 前端逻辑
├── data/
│   └── events.json   # 事件数据库（JSON格式）
├── scraper/
│   └── scraper.py    # 自动抓取脚本（Python）
└── README.md         # 本文件
```

---

## 🚀 快速开始

### 1. 本地预览
直接用浏览器打开 `index.html` 即可预览。
（由于浏览器安全限制，需通过本地服务器运行才能正常加载 `events.json`）

```bash
# 在项目目录下运行
python -m http.server 8080
# 然后访问 http://localhost:8080
```

### 2. 添加事件
打开 `admin.html`，填写表单，点击保存。
保存后会自动下载最新的 `events.json`，将其放入 `data/` 文件夹覆盖即可。

### 3. 部署到 GitHub Pages（免费公开托管）

1. 在 GitHub 新建一个仓库（例如 `world-events`）
2. 将整个 `world-events/` 文件夹推送到仓库
3. 在仓库设置 → Pages → Source 选择 `main` 分支
4. 等待 1-2 分钟，你的网站就上线了！
5. 地址格式：`https://你的用户名.github.io/world-events/`

---

## 🤖 自动抓取新闻（进阶）

运行 `scraper/scraper.py` 可以自动从 RSS 源抓取最新新闻并追加到 `events.json`：

```bash
pip install feedparser requests
python scraper/scraper.py
```

可以设置定时任务（每天自动运行）：
- **Windows**：任务计划程序
- **macOS/Linux**：crontab

### 新闻源清单

| 源 | 语言 | 说明 |
|----|------|------|
| BBC World News | 英文 | 自动翻译 |
| The Guardian World | 英文 | 自动翻译 |
| Al Jazeera | 英文 | 自动翻译 |
| NASA News | 英文 | 自动翻译 |
| UN News | 英文 | 自动翻译 |
| SciTech Daily | 英文 | 自动翻译 |
| 人民网国际 | 中文 | 国内直连 |
| 中新网即时新闻 | 中文 | 国内直连 |

在 `scraper/scraper.py` 的 `RSS_SOURCES` 里可自由增删。

### 🌐 自动翻译

英文新闻会自动翻译成中文（MyMemory 免费接口，无需注册）。

- 开关：`scraper.py` 里的 `TRANSLATE_ENABLED`
- **每日免费额度约 5000 字符**，用完后会保留英文，次日自动补翻
- 想提升额度：把 `TRANSLATE_EMAIL` 填成你的邮箱（额度升到 5 万字符/天）
- 补翻历史数据：`python scraper/translate_existing.py`（带缓存，可反复运行）

### 🔨 完整更新流程

```bash
python scraper/scraper.py           # 1. 抓取（含自动翻译）
python scraper/reclassify.py        # 2. 重新分类（可选）
python scraper/build.py             # 3. 静态预渲染 + 内嵌数据
```

> ⚠️ 每次更新后，记得把 HTML 里的 `?v=N` 版本号 +1，避免浏览器用旧缓存。

---

## 🔒 变成私有网站（密码保护）

在 `index.html` 的 `<head>` 中加入：

```html
<script>
  const pwd = prompt('请输入访问密码：');
  if (pwd !== '你的密码') { document.body.innerHTML = '<h1>无权访问</h1>'; }
</script>
```

---

## 📝 数据格式（events.json）

每条事件的格式如下：

```json
{
  "id": "evt-001",
  "title": "事件标题",
  "date": "2026-05-22",
  "category": "科技",
  "country": "美国",
  "region": "北美洲",
  "summary": "一句话摘要",
  "content": "详细正文内容",
  "image": "https://... (图片链接)",
  "videoUrl": "https://... (视频链接)",
  "source": "BBC、路透社",
  "sourceUrl": "https://...",
  "tags": ["标签1", "标签2"]
}
```

---

## 🛠️ 后续改进方向

- [ ] 接入更多 RSS 新闻源（CNN、BBC、新华社、共同社等）
- [ ] 自动翻译（中文/英文切换）
- [ ] 地图可视化（事件地点标注）
- [ ] 评论功能
- [ ] 后端 API（Flask/Django）替代静态 JSON
- [ ] 用户注册/登录

---

**数据主权在你手里，永远不用担心被平台删除。**
