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

### 运行模式与鉴权

- 默认只绑定 `127.0.0.1`，不会创建 Windows 防火墙入站规则。
- 如需局域网访问，在 `backend/data/app_config.json` 配置 `{"lan_mode": true, "api_token": "your-token"}`，或开发启动时使用 `python app.py --lan`。LAN 模式没有 API Token 时会拒绝启动。
- `--debug` 始终只绑定 localhost，不开放网络调试器。
- 配置了 API Token 后，所有修改类 `/api/*` 请求使用 `X-API-Token` 或 `Authorization: Bearer ...`；GET 图片下载仍按当前媒体访问策略开放。

## 功能概览

### 扫描系统

- **文件夹扫描** — 添加扫描根目录，按文件名正则解析（严格匹配 `条码_主图/详情图_序号.ext` + 模糊匹配 `条码_序号.ext`）
- **全量 / 增量模式** — 全量扫描清理不存在的记录，增量扫描仅处理变更文件
- **真实进度追踪** — 统计阶段 → 扫描阶段 → 缩略图阶段 → 版本更新阶段，显示速度和预计剩余时间
- **扫描取消** — 任意阶段可安全取消，已提交的扫描结果保留，仅丢弃当前未提交的变更
- **扫描历史** — 最近 10 条扫描记录，含新增/跳过/拒绝/耗时统计
- **GTIN 校验** — 自动校验条码有效性，拒绝非标品条码并归档
- **目录冲突保护** — 拒绝相同路径和递归父子目录重复建立索引
- **故障恢复** — 可读文件恢复后扫描会把 `broken` 索引恢复为 `active`

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
│       ├── rejected.py     # 被拒绝条码管理
│       ├── settings.py     # 调试与日志设置
│       ├── _utils.py       # JSON、路径与导出共享校验
│       ├── export.py       # Excel 导出
│       └── pending.py      # 待确认列表
└── frontend/
    └── src/
        ├── components/
        │   ├── BarcodeTable.tsx      # 条码表格
        │   ├── DetailPanel.tsx       # 图片详情
        │   ├── RejectedPanel.tsx     # 被拒绝条码
        │   ├── TaskRail.tsx          # 任务状态
        │   └── ...
        ├── views/                    # 图片库、扫描、待确认、批量、导出和日志页面
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

扫描根会保存规范化路径键，启动时回填旧数据库并检查外键孤儿记录。删除扫描根只删除索引/拒绝元数据，不删除磁盘上的拒绝文件；删除期间若该根正在扫描会返回 409。

## 验证

在仓库根目录执行：

```bash
python -m compileall -q backend
python -m pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning -W error::sqlalchemy.exc.SAWarning
python -m ruff check backend --select F821,F811,F841,E902
cd frontend && npm run build && npm audit --omit=dev --audit-level=high
```

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
