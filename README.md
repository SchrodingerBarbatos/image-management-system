# 商品图片管理系统

企业内部使用的商品图片管理系统。图片按 `条码_类型_序号.扩展名` 命名，散落在各文件夹中。系统通过扫描文件夹建立索引，支持版本管理、缩略图预览、批量操作、Excel 批量导出。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python Flask + SQLAlchemy + Pillow + openpyxl |
| 数据库 | SQLite (WAL 模式) |
| 前端 | React 18 + TypeScript + Ant Design 5 + Vite |

## 快速开始

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

## 功能概览

### 扫描系统

- **文件夹扫描** — 添加扫描根目录，按文件名正则解析（严格匹配 `条码_主图/详情图_序号.ext` + 模糊匹配 `条码_序号.ext`）
- **全量 / 增量模式** — 全量扫描清理不存在的记录，增量扫描仅处理变更文件
- **真实进度追踪** — 统计阶段 → 扫描阶段 → 缩略图阶段 → 版本更新阶段，显示速度和预计剩余时间
- **扫描取消** — 任意阶段可安全取消，已处理数据自动回滚
- **扫描历史** — 最近 10 条扫描记录，含新增/跳过/拒绝/耗时统计
- **GTIN 校验** — 自动校验条码有效性，拒绝非标品条码并归档

### 版本管理

- **自动版本化** — 按文件夹修改时间 + 内容哈希自动分组
- **重复版本检测** — 标记内容相同但路径不同的重复版本
- **低版本清理** — 按阈值筛选图片数不足的版本，批量删除

### 批量操作

- **重复文件夹删除** — 扫描重复版本，批量删除冗余文件夹
- **低版本删除** — 配置主图/详情图阈值，批量清理低图片数版本
- **异步任务** — 所有批量操作异步执行，支持进度追踪和取消
- **删除失败统计** — 文件删除失败时保留索引，记录失败原因（权限不足/文件被占用等）

### 其他功能

- **缩略图** — 200×200 自动生成，保持比例，白色背景
- **Excel 导出** — 上传 Excel，按条码匹配图片，打包 ZIP 下载
- **待确认列表** — 模糊匹配的图片需手动确认类型后入库
- **表格 / 卡片双视图** — 图片列表支持表格和卡片两种浏览模式

## 项目结构

```
├── backend/
│   ├── app.py              # Flask 入口 + 数据库迁移
│   ├── config.py           # 配置
│   ├── models.py           # SQLAlchemy 模型
│   ├── scanner.py          # 扫描引擎 & 文件名解析
│   ├── versioning.py       # 版本计算
│   ├── thumbnail.py        # 缩略图生成
│   ├── task_engine.py      # 异步任务框架
│   └── routes/
│       ├── scan.py         # 扫描 API
│       ├── batch.py        # 批量操作（同步）
│       ├── batch_tasks.py  # 批量操作（异步任务）
│       ├── images.py       # 图片 CRUD
│       ├── barcodes.py     # 条码查询
│       ├── export.py       # Excel 导出
│       └── pending.py      # 待确认列表
└── frontend/
    └── src/
        ├── components/
        │   ├── ScanManager.tsx       # 扫描管理
        │   ├── BatchOperations.tsx   # 批量操作
        │   ├── TaskList.tsx          # 任务列表
        │   ├── ImageList.tsx         # 图片列表
        │   └── ...
        ├── services/api.ts           # API 层
        └── hooks/
            └── useTaskPolling.ts     # 任务轮询 Hook
```

## 数据库

SQLite，启动时自动建表和迁移。主要表：

| 表 | 说明 |
|---|---|
| `scan_root` | 扫描根目录配置 |
| `image` | 图片索引（条码、类型、路径、指纹） |
| `image_version` | 版本记录（按条码+类型+内容哈希分组） |
| `batch_task` | 异步任务（扫描、删除、导出） |
| `scan_log` | 扫描日志 |
| `rejected_barcode` | 非标品条码归档 |

## API 端点

<details>
<summary>点击展开完整 API 列表</summary>

### 扫描
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/scan` | 触发扫描 |
| GET | `/api/scan/status` | 获取运行中的扫描状态 |
| GET | `/api/scan/status/<job_id>` | 获取指定扫描状态 |
| POST | `/api/scan/cancel/<job_id>` | 取消扫描 |
| GET | `/api/scan/history` | 最近扫描记录 |

### 扫描目录
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/scan-roots` | 列表 |
| POST | `/api/scan-roots` | 添加 |
| PUT | `/api/scan-roots/<id>` | 更新 |
| DELETE | `/api/scan-roots/<id>` | 删除 |

### 批量操作
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/batch/duplicate-scan/tasks` | 创建重复扫描任务 |
| POST | `/api/batch/delete-duplicates/tasks` | 创建批量删除重复任务 |
| POST | `/api/batch/low-version-scan/tasks` | 创建低版本扫描任务 |
| POST | `/api/batch/delete-low-versions/tasks` | 创建批量删除低版本任务 |
| POST | `/api/images/batch-delete-task` | 创建批量删除图片任务 |

### 任务管理
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tasks` | 任务列表 |
| GET | `/api/tasks/<id>` | 任务详情（含进度、失败统计） |
| POST | `/api/tasks/<id>/cancel` | 取消任务 |
| DELETE | `/api/tasks/<id>` | 删除任务记录 |

</details>

## 许可

内部项目，未公开发布。
