# 商品图片管理系统 / Product Image Management System

[中文](#chinese) | [English](#englishv)

---

<a name="chinese"></a>

## 中文

企业内部使用的商品图片管理系统。图片按 `条码_类型_序号.扩展名` 命名，散落在各文件夹中。系统通过扫描文件夹建立索引，支持版本管理、缩略图预览、Excel 批量导出。

### 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python Flask + SQLAlchemy + Pillow + openpyxl |
| 数据库 | SQLite (WAL 模式) |
| 前端 | React 18 + TypeScript + Ant Design 5 + Vite |

### 快速开始

**后端** (Python 3.10+):

```bash
cd backend
pip install -r requirements.txt
python app.py          # 启动在 http://localhost:5000
```

**前端** (Node 18+):

```bash
cd frontend
npm install
npm run dev            # 启动在 http://localhost:3000
```

### 功能

- **文件夹扫描** — 添加扫描根目录，按文件名正则解析（严格匹配 + 模糊匹配）
- **版本管理** — 按文件夹修改时间 + 内容哈希自动版本化，支持历史版本查看
- **缩略图** — 200×200 自动生成，保持比例，白色背景
- **批量操作** — 表格 + 卡片双视图勾选，批量删除/导出
- **Excel 导出** — 上传 Excel，按条码匹配图片，打包 ZIP 下载
- **待确认列表** — 模糊匹配的图片需手动确认类型后入库

### 项目结构

```
├── backend/
│   ├── app.py           # Flask 入口
│   ├── config.py        # 配置
│   ├── models.py        # SQLAlchemy 模型
│   ├── scanner.py       # 扫描 & 文件名解析
│   ├── versioning.py    # 版本计算
│   ├── thumbnail.py     # 缩略图生成
│   └── routes/          # API 路由
└── frontend/
    └── src/
        ├── pages/       # 页面
        ├── components/  # 组件
        └── services/    # API 层
```

---

<a name="english"></a>

## English

An internal enterprise image management system. Images are named by the pattern `barcode_type_sequence.extension` and scattered across folders. The system scans folders to build an index, with version management, thumbnail previews, and Excel batch export.

### Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python Flask + SQLAlchemy + Pillow + openpyxl |
| Database | SQLite (WAL mode) |
| Frontend | React 18 + TypeScript + Ant Design 5 + Vite |

### Quick Start

**Backend** (Python 3.10+):

```bash
cd backend
pip install -r requirements.txt
python app.py          # Starts at http://localhost:5000
```

**Frontend** (Node 18+):

```bash
cd frontend
npm install
npm run dev            # Starts at http://localhost:3000
```

### Features

- **Folder Scanning** — Add scan roots, parse filenames by regex (strict + fuzzy matching)
- **Version Management** — Auto-version by folder mtime + content hash, with history browsing
- **Thumbnails** — 200×200 auto-generated, aspect-ratio preserved, white background
- **Batch Operations** — Table + card dual-view selection, batch delete/export
- **Excel Export** — Upload Excel, match images by barcode, download as ZIP
- **Pending Review** — Fuzzy-matched images require manual type confirmation

### Project Structure

```
├── backend/
│   ├── app.py           # Flask entry point
│   ├── config.py        # Configuration
│   ├── models.py        # SQLAlchemy models
│   ├── scanner.py       # Scan & filename parsing
│   ├── versioning.py    # Version computation
│   ├── thumbnail.py     # Thumbnail generation
│   └── routes/          # API routes
└── frontend/
    └── src/
        ├── pages/       # Pages
        ├── components/  # Components
        └── services/    # API layer
```
