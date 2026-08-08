# 数据库 Schema 与迁移（维护者参考）

> 说明数据库的表结构、schema 变更策略与前后向兼容保证。
> 相关：`app/models.py`（ORM 定义）、`app/database.py`（`init_db` / 迁移函数）。

## 变更策略

SQLite 单文件数据库（默认 `data/workbench.db`，桌面版在 appdata）。schema 变更
分两类，都通过 `init_db()` 在**每次启动时自动执行**（幂等，跑过就跳过）：

1. **建表**：`Base.metadata.create_all(bind=engine)` 只创建**缺失**的表，
   已存在的表不动 → 新增表对所有存量库自动生效。
2. **加列**：`app/database.py` 的 `_migrate_2026_08_05()` 用 `_column_exists()`
   守卫，对已有表 `ALTER TABLE ... ADD COLUMN`。列不存在才加，天然幂等。

SQLite 对 `ALTER TABLE DROP COLUMN` 支持有限，因此**尽量只加不删**；非删不可时
用 try/except 降级（见该函数内 `experiments.config` 列的处理）。

## 兼容保证

- **向前兼容（旧库 → 新版本）**：老版本数据库打开新版本程序，缺失的表/列自动补齐，
  数据无损迁移。首次启动日志无报错即成功。
- **向后兼容（新库 → 旧版本）**：旧版本 ORM 只查询它自己认识的表，多余的
  表/列被忽略，不影响旧版本运行。因此新版本写入的数据在旧版本里表现为
  「多出的表/列」，不会破坏旧版本功能。

## 思维导图新增表（v0.4.2+）

上游在 v0.4.2 引入项目思维导图，新增 3 张表 + 2 列（均由上述幂等机制自动落库）：

| 表 | 用途 |
|---|---|
| `mindmaps` | 每个项目一张导图（`project_id` 唯一，一对一） |
| `mindmap_nodes` | 画布节点（`kind` = auto 自动树 / manual 手绘；含坐标、宽高、形状、字号、字体） |
| `mindmap_edges` | 手动连线（source → target 有向边，可带箭头） |

迁移记录（`_migrate_2026_08_05`，幂等）：
- `mindmap_nodes.font_size` INTEGER NOT NULL DEFAULT 13
- `mindmap_nodes.font_family` VARCHAR(20) NOT NULL DEFAULT 'system'

> 该迁移函数名为 `_migrate_2026_08_05`（首次编写日期），后纳入思维导图列，
> 函数名未改，仅内部追加了幂等 `ADD COLUMN` 块。

## 其他历史迁移

- `experiments.config_md`（Text，替代旧 JSON `config` 列）：新建时把旧 `config`
  内容原样拷入，随后尝试 DROP 旧列。
- `metrics.note`（Text NOT NULL DEFAULT ''）：指标备注。
- 新表 `artifacts`（统一文件存储）、`weekly_reviews`（周复盘）由 `create_all`
  自动创建。

## 存储位置统一（v0.4.4）

v0.4.4 起数据库与上传文件统一放在同一「数据文件夹」（`<folder>/workbench.db` +
`<folder>/artifacts`）。老版本允许分开放置，升级后由
`settings_service.auto_unify_storage_dirs()` 在启动时自动把 artifacts 迁到
db 所在目录下（该兼容迁移保留若干版本）。详见 `docs/troubleshooting.md` 与
`app/services/settings_service.py`。
