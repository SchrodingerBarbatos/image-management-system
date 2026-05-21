# 商品图片管理系统 — 设计文档

日期: 2026-05-21 | 状态: 待实施

## 概述

企业内部使用的商品图片管理系统。图片散落在各文件夹中，文件名按 `条码_类型_序号.扩展名` 命名。系统通过扫描文件夹建立索引，支持版本管理、增删改查、缩略图预览、Excel 批量导出。用户无需登录。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python Flask + SQLAlchemy + Pillow + openpyxl |
| 数据库 | SQLite (WAL 模式) |
| 前端 | React + TypeScript + Ant Design + axios |
| 构建 | Vite (前端) |

## 数据库模型

### scan_root — 扫描根目录
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| path | TEXT | 文件夹绝对路径 |
| recursive | BOOL | 是否递归子文件夹 |
| enabled | BOOL | 是否启用 |

### image — 图片索引
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| barcode | TEXT NOT NULL | 条码 |
| image_type | TEXT NOT NULL | main / detail |
| sequence | INTEGER | 序号 (从文件名解析) |
| filename | TEXT | 完整文件名 |
| ext | TEXT | 扩展名 (jpg/png/gif/webp) |
| file_path | TEXT UNIQUE | 文件绝对路径 |
| file_size | INTEGER | 字节数 |
| md5_hash | TEXT | MD5 哈希 |
| folder_path | TEXT | 所在最末级文件夹路径 |
| folder_mtime | TEXT | 最末级文件夹修改时间 (ISO8601) |
| scan_root_id | FK → scan_root.id | 来源扫描目录 |
| confirmed | BOOL | 是否已确认 (模糊匹配默认为 False) |
| created_at | TEXT | |
| updated_at | TEXT | |

索引: (barcode, image_type), (barcode), (md5_hash), (folder_mtime)

### image_version — 版本记录 (按条码聚合)
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| barcode | TEXT | 条码 |
| version_label | TEXT | v1 / v2 / v3 ... |
| folder_mtime | TEXT | 该版本对应文件夹修改时间 |
| content_hash | TEXT | 该版本所有图片 (文件名+MD5) 的集合哈希 |
| is_latest | BOOL | 是否最新版本 |
| created_at | TEXT | |

唯一约束: (barcode, content_hash) — 内容相同的文件夹合并为同一版本

### export_task — 导出任务
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| status | TEXT | pending / processing / done / error |
| zip_path | TEXT | ZIP 文件路径 |
| created_at | TEXT | |

ZIP 文件保留 24 小时后自动清理。

## 核心逻辑

### 文件名解析

**严格匹配** (正则): `^(\d+)_(主图|详情图)_(\d+)\.(jpg|jpeg|png|gif|webp)$`
- 组1=条码, 组2=类型, 组3=序号, 组4=扩展名

**模糊匹配** (正则): `^(\d+)_(\d+)\.(jpg|jpeg|png|gif|webp)$`
- 条码和序号可解析，但类型缺失 → 加入待确认列表
- 模糊匹配仅在开启「允许模糊匹配」时生效
- 确认前必须手动选择主图/详情图

**不匹配**: 跳过，不计入索引。

### 版本判定

1. 每条图片记录 `folder_mtime` = 其所在最末级文件夹的修改时间
2. 同一条码下，按 `folder_mtime` 降序排列 → 时间越新版本号越高
3. 同一版本号下所有图片的 (filename, md5_hash) 集合 → 计算 content_hash
4. 不同文件夹但 content_hash 相同 → 合并为同一版本
5. 默认展示最新版本 (is_latest=True)，历史版本可展开查看

### 扫描流程

1. 用户选择文件夹，设置是否递归 → 添加为 scan_root
2. 系统遍历文件夹，按规则解析图片文件名
3. 严格匹配 → 直接入库，创建/更新版本
4. 模糊匹配 (若开启) → 加入 pending 待确认列表
5. 用户对待确认图片手动选择类型 → 确认入库
6. 扫描支持增量：已有路径跳过，新图片追加

### 缩略图

- 首次访问 `GET /api/thumbnails/:id` 时生成
- 持久化到 `backend/data/thumbnails/{id}.jpg`
- 尺寸: 200×200，保持比例，白色背景填充
- Pillow 生成，JPEG quality=75

### Excel 导出

1. 用户上传 Excel 文件 → API 返回所有列名
2. 用户选择条码所在列 (如 "A-客户")
3. 用户选择导出类型: 主图 / 详情图 / 全部
4. 系统按条码匹配图片 → 打包为 ZIP
5. ZIP 内结构: `{条码}/{条码}_主图_1.jpg` 等
6. 浏览器下载

## API 端点

### 扫描 & 索引
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/scan-roots | 列出扫描根目录 |
| POST | /api/scan-roots | 添加扫描根目录 `{path, recursive}` |
| DELETE | /api/scan-roots/:id | 删除根目录 (同时清除其下的索引) |
| POST | /api/scan | 触发扫描 `{root_id?}` 不传则全量 |

### 图片 CRUD
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/images | 列表, Query: `barcode`, `image_type`, `scan_root_id`, `page`, `page_size`, `sort` |
| GET | /api/images/:id | 单图 + 所有版本 |
| PUT | /api/images/:id | 修改 image_type |
| DELETE | /api/images/:id | 删除索引 (不删文件) |

### 缩略图 & 原图
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/thumbnails/:id | 缩略图 |
| GET | /api/images/:id/file | 原图文件 |

### Excel 导出
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/export/excel | 上传 Excel, 返回 `{columns: [...]}` |
| POST | /api/export/zip | Body: `{barcode_column, image_type, selected_barcodes?}` |
| GET | /api/export/download/:task_id | 下载 ZIP |

### 待确认
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/pending | 待确认列表 |
| POST | /api/pending/confirm | Body: `[{id, image_type}]` 批量确认 |
| DELETE | /api/pending/:id | 忽略某条 (删除记录) |

### 批量操作
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/images/batch-delete | Body: `{ids: [...]}` |
| POST | /api/images/batch-export | Body: `{ids: [...], image_type}` 导出指定图片 ZIP |

## 前端结构

```
frontend/src/
├── App.tsx
├── pages/
│   └── Home.tsx              # 主页面: 左表右卡
├── components/
│   ├── ImageTable.tsx        # 左栏: 表格 (checkbox + 列)
│   ├── ImageCardDetail.tsx   # 右栏: 选中条码的图片卡片
│   ├── SearchBar.tsx         # 顶部搜索
│   ├── ScanManager.tsx       # 扫描目录管理弹窗
│   ├── ExportDialog.tsx      # Excel 导出弹窗 (步骤式)
│   └── PendingList.tsx       # 待确认列表弹窗
└── services/
    └── api.ts                # axios 封装
```

### 批量勾选
所有多选场景均支持全选/反选:
1. 表格列表 — 表头全选 checkbox + 行 checkbox + 批量操作栏
2. 卡片主图区 — "全选主图" + 每张图 checkbox
3. 卡片详情图区 — "全选详情图" + 每张图 checkbox
4. 待确认列表 — 全选 + 批量确认/忽略
5. Excel 导出列选择 — 全选
6. 扫描目录管理 — 全选 + 批量删除/重新扫描

### 界面布局
- 顶部: 搜索栏 + 功能按钮 (添加扫描目录、Excel 导出)
- 左栏 (60%): Ant Design Table, 支持 rowSelection, 分页
- 右栏 (40%): 选中条码的卡片详情, 缩略图网格, 版本历史
- 底部: 状态栏 (扫描目录数、已索引数、数据库大小)
- 批量操作栏: 选中后顶部浮现, 显示数量 + 批量按钮

## 项目文件结构

```
图片库系统/
├── backend/
│   ├── app.py                # Flask 入口
│   ├── config.py             # 配置
│   ├── models.py             # SQLAlchemy 模型
│   ├── scanner.py            # 扫描 & 文件名解析
│   ├── versioning.py         # 版本比较 & 合并
│   ├── thumbnail.py          # 缩略图生成
│   ├── export.py             # Excel 解析 & ZIP 打包
│   ├── routes/
│   │   ├── images.py
│   │   ├── scan.py
│   │   ├── export.py
│   │   └── pending.py
│   └── data/                 # SQLite + thumbnails/ (自动创建)
├── frontend/
│   ├── src/ (如上)
│   ├── package.json
│   └── vite.config.ts
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-05-21-image-management-system-design.md
```

## 非功能需求

- **性能**: 首次全量扫描 10 万张图片预计 < 5 分钟 (瓶颈在 MD5 计算, 非数据库)
- **并发**: 公司内部 < 10 人同时使用, SQLite WAL 足够
- **容错**: 文件被移动/删除后, 索引记录标记为 broken 而非直接删除, 下次扫描时清理
- **启动**: `python app.py` 一键启动后端, `npm run dev` 启动前端, 无其他依赖
