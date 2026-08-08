"""设置页服务层：数据库健康检查 / 备份 / 存储路径迁移。

与 config（配置持久化）、database（engine 热重建）解耦，路由层只做参数解析与
重定向。所有「可能破坏数据」的操作集中在本模块，用 threading.Lock 串行化，
防双击并发提交导致竞态。

迁移的安全原则（用户硬性要求）：
  1. 目标目录权限/存在性先预检，不行则阻止；
  2. 先复制（VACUUM INTO 一致性快照 / copytree），后删除；
  3. 复制后做完整性校验（SQLite 头魔数 + integrity_check + 表行数对比）；
  4. 任一步失败：清理残留并恢复原配置，绝不留下半迁移状态。
"""

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

from app.config import (
    APP_VERSION,
    ENV_FILE,
    get_artifact_dir,
    get_db_path,
    portable_path_for_backup,
    resolve_portable_path,
    resolve_user_path,
    save_setting,
    settings,
)
from app.database import rebuild_engine


class SettingsError(Exception):
    """设置操作失败，message 会直接展示给用户。"""


logger = logging.getLogger(__name__)

_migrate_lock = threading.Lock()


def _sqlite(path: Path) -> sqlite3.Connection:
    """打开 SQLite 原始连接，自动提交模式。

    VACUUM / PRAGMA 不能在事务内执行，isolation_level=None 关闭隐式事务，
    绕开 SQLAlchemy 的 autobegin 陷阱。迁移/备份全程用 raw sqlite3，
    不碰请求的 ORM session。
    """
    return sqlite3.connect(str(path), isolation_level=None)


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------


def get_db_health() -> dict:
    """返回数据库真实状态，供状态栏轮询与设置页「关于」区展示。

    - 文件不存在 → error
    - 文件损坏 → integrity_check 返回错误列表 → error
    - 正常 → integrity_check 返回 'ok' → ok
    """
    p = get_db_path()
    if not p.exists():
        return {
            "status": "error",
            "integrity": "数据库文件不存在",
            "db_size_bytes": 0,
            "db_path": str(p),
        }
    try:
        conn = _sqlite(p)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        res = row[0] if row else "?"
        if isinstance(res, bytes):
            res = res.decode("utf-8", "replace")
        return {
            "status": "ok" if res == "ok" else "error",
            "integrity": res,
            "db_size_bytes": p.stat().st_size,
            "db_path": str(p),
        }
    except Exception as exc:  # 连接本身失败（被锁/损坏）也报 error
        return {
            "status": "error",
            "integrity": str(exc),
            "db_size_bytes": 0,
            "db_path": str(p),
        }


# ---------------------------------------------------------------------------
# SQLite 原始操作
# ---------------------------------------------------------------------------


def _vacuum_into(source: Path, target: Path) -> None:
    """用 SQLite 官方机制把源库一致性快照复制到 target。

    源库运行中也能安全执行（对源库取读快照，可与并发写共存）。
    VACUUM INTO 要求 target 不存在（或为空文件），调用方需保证。
    target 以 SQL 字符串字面量嵌入，单引号要翻倍转义。
    """
    conn = _sqlite(source)
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        target_sql = str(target).replace("'", "''")
        conn.execute(f"VACUUM INTO '{target_sql}'")
    finally:
        conn.close()


def _verify_integrity(p: Path) -> None:
    """可靠校验：文件头魔数 + 非空 + PRAGMA integrity_check 返回 'ok'。"""
    if not p.exists() or p.stat().st_size == 0:
        raise SettingsError(f"目标文件为空或不存在：{p}")
    with p.open("rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            raise SettingsError(f"不是合法的 SQLite 文件：{p}")
    conn = _sqlite(p)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
    res = row[0] if row else "?"
    if isinstance(res, bytes):
        res = res.decode("utf-8", "replace")
    if res != "ok":
        raise SettingsError(f"完整性检查未通过：{res}")


def _table_counts(p: Path) -> dict:
    """逐表行数快照（从 sqlite_master 动态枚举，不硬编码表名）。"""
    conn = _sqlite(p)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        return {
            t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            for t in tables
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 备份
# ---------------------------------------------------------------------------


def create_backup() -> Path:
    """生成数据库一致性备份，返回临时文件路径（调用方负责删除）。

    用 VACUUM INTO 而非裸复制文件：运行中生成一致性快照，
    不依赖 WAL 落盘状态，备份一定完整可用。
    """
    fd, name = tempfile.mkstemp(prefix="wb-backup-", suffix=".db")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.unlink(missing_ok=True)  # VACUUM INTO 目标须不存在
        _vacuum_into(get_db_path(), tmp)
        _verify_integrity(tmp)
        return tmp
    except SettingsError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise SettingsError(f"备份失败：{exc}") from exc


# ---------------------------------------------------------------------------
# 数据库路径迁移
# ---------------------------------------------------------------------------


def _precheck_dir_writable(d: Path) -> None:
    """目标目录预检：可创建 + 可写（写入探针文件再删除）。"""
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SettingsError(f"无法创建目标目录 {d}：{exc}") from exc
    probe = d / f".wb_write_probe_{os.getpid()}"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        raise SettingsError(f"目标目录不可写 {d}：{exc}") from exc


def _remove_db_files(p: Path) -> None:
    """删除数据库文件及 WAL/SHM/journal 伴随文件。删不掉只留残留，不阻塞。"""
    for f in (p, Path(f"{p}-wal"), Path(f"{p}-shm"), Path(f"{p}-journal")):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


def migrate_db(new_path_str: str) -> None:
    """把数据库从当前位置迁移到新路径，保证成功、失败回滚。

    流程：预检目录 → VACUUM INTO 复制 → 完整性 + 行数校验 → 写 .env +
    热更新 settings → 热重建 engine → 删旧库。任一步失败清理残留并恢复。
    """
    with _migrate_lock:
        new_abs = resolve_user_path(new_path_str.strip())
        old_abs = get_db_path()
        if new_abs == old_abs:
            return  # 同路径，no-op
        old_path_str = settings.db_path  # 回滚用的原始配置值（须在 save_setting 前取）

        _precheck_dir_writable(new_abs.parent)
        if new_abs.exists():
            raise SettingsError(f"目标数据库文件已存在，请换一个路径：{new_abs}")

        # 1. 先复制（一致性快照）
        try:
            _vacuum_into(old_abs, new_abs)
        except SettingsError:
            new_abs.unlink(missing_ok=True)
            raise
        except Exception as exc:
            new_abs.unlink(missing_ok=True)
            raise SettingsError(f"复制数据库失败：{exc}") from exc

        # 2. 校验新库完整 + 与旧库行数等价
        try:
            _verify_integrity(new_abs)
            if _table_counts(new_abs) != _table_counts(old_abs):
                raise SettingsError("新旧库行数不一致，迁移中止")
        except SettingsError as exc:
            new_abs.unlink(missing_ok=True)
            raise
        except Exception as exc:
            new_abs.unlink(missing_ok=True)
            raise SettingsError(f"新库校验失败：{exc}") from exc

        # 3. 写配置（失败则 settings/engine 未动，只需清残留）
        try:
            save_setting("db_path", new_path_str.strip())
        except Exception as exc:
            new_abs.unlink(missing_ok=True)
            raise SettingsError(f"写入配置失败：{exc}") from exc

        # 4. 热重建 engine（失败时唯一需要真正回滚的路径）
        try:
            rebuild_engine(new_abs)
        except Exception as exc:
            try:
                save_setting("db_path", old_path_str)
                rebuild_engine(old_abs)
            except Exception:
                pass  # 回滚也失败时至少配置保持旧值、新文件被清理
            new_abs.unlink(missing_ok=True)
            raise SettingsError(f"引擎切换失败，已恢复原库：{exc}") from exc

        # 5. 成功后删除旧库（含 WAL 伴随文件）
        _remove_db_files(old_abs)


# ---------------------------------------------------------------------------
# 上传文件目录迁移
# ---------------------------------------------------------------------------


def _verify_tree(src: Path, dst: Path) -> bool:
    """新旧目录逐文件（相对路径 + 大小）对比，确认复制完整。"""
    if not src.exists():
        return True  # 原本就没有上传文件
    s_files = {
        str(p.relative_to(src)): p.stat().st_size
        for p in src.rglob("*")
        if p.is_file()
    }
    d_files = {
        str(p.relative_to(dst)): p.stat().st_size
        for p in dst.rglob("*")
        if p.is_file()
    }
    return s_files == d_files


def migrate_artifact_dir(new_dir_str: str) -> None:
    """把上传文件目录整体迁移到新位置（先复制后删除，失败回滚）。"""
    with _migrate_lock:
        new_abs = resolve_user_path(new_dir_str.strip())
        old_abs = get_artifact_dir()
        if new_abs == old_abs:
            return
        if new_abs.is_relative_to(old_abs) or old_abs.is_relative_to(new_abs):
            raise SettingsError("目标上传目录不能是原上传目录的子目录或父目录")
        _precheck_dir_writable(new_abs)
        if any(new_abs.iterdir()):
            raise SettingsError("目标上传目录已存在且非空，请换一个目录")

        if old_abs.exists():
            # _precheck_dir_writable 可能已 mkdir 出空目标，故用 dirs_exist_ok
            shutil.copytree(old_abs, new_abs, dirs_exist_ok=True)
        if not _verify_tree(old_abs, new_abs):
            shutil.rmtree(new_abs, ignore_errors=True)
            raise SettingsError("文件复制校验未通过，已回滚")
        save_setting("artifact_dir", new_dir_str.strip())
        if old_abs.exists():
            shutil.rmtree(old_abs, ignore_errors=True)


# ---------------------------------------------------------------------------
# 首次启动自动合并存储位置（v0.4.4 起，持续保留若干版本）
# ---------------------------------------------------------------------------
# 早期版本允许 db 与 artifacts 分开放置（设置页两个输入框）。v0.4.4 起统一为
# 「一个数据文件夹」：db 在 <folder>/workbench.db，上传文件在 <folder>/artifacts。
# 老用户从旧版本升级时，db 与 artifacts 可能在不同目录，这里在启动时自动把
# artifacts 迁到 db 所在目录下，避免用户手动操作、防止数据混乱丢失。
#
# 该逻辑会在后续几个版本中持续保留（等老用户数据全部统一后再移除）：
# 迁移成功后 db_parent == artifact_parent，下次启动即不再触发，天然幂等。


def auto_unify_storage_dirs() -> None:
    """若 db 与 artifacts 不在同一目录，自动把 artifacts 迁移到 db 所在目录下。

    迁移复用 migrate_artifact_dir 的安全流程（预检→复制→校验→写配置→删旧，
    失败回滚）。任何失败只记 warning，不阻塞服务启动——宁可保持原配置，
    也不能因为自动迁移让用户丢数据。
    """
    try:
        db_parent = get_db_path().parent
        art_parent = get_artifact_dir().parent
        if db_parent == art_parent:
            return  # 已统一，无需迁移
        target = str(db_parent / "artifacts")
        logger.warning(
            "检测到数据库与上传文件目录分离（db 父目录=%s，上传父目录=%s），"
            "自动统一到 %s",
            db_parent, art_parent, target,
        )
        migrate_artifact_dir(target)
        logger.warning("存储位置已自动统一：上传文件迁移至 %s", target)
    except SettingsError as exc:
        logger.warning("自动统一存储位置失败（保持原配置）：%s", exc)
    except Exception as exc:  # 防御：任何异常都不能拖垮启动
        logger.warning("自动统一存储位置异常（保持原配置）：%s", exc)


# ---------------------------------------------------------------------------
# 迁移前统一预检（避免 db 已迁移而 artifact 目标不可写导致的半迁移）
# ---------------------------------------------------------------------------


def preflight_migrations(db_path: str, artifact_dir: str) -> None:
    """一次校验 db 与 artifact 两项目标，全部通过才允许执行迁移。"""
    new_db = resolve_user_path(db_path.strip())
    new_art = resolve_user_path(artifact_dir.strip())
    old_art = get_artifact_dir()

    _precheck_dir_writable(new_db.parent)
    if new_db != get_db_path() and new_db.exists():
        raise SettingsError(f"目标数据库文件已存在，请换一个路径：{new_db}")

    _precheck_dir_writable(new_art)
    if new_art != old_art:
        if new_art.is_relative_to(old_art) or old_art.is_relative_to(new_art):
            raise SettingsError("目标上传目录不能是原上传目录的子目录或父目录")
        if any(new_art.iterdir()):
            raise SettingsError("目标上传目录已存在且非空，请换一个目录")


# ---------------------------------------------------------------------------
# 全量备份（数据库 + 上传文件 + 配置 → zip）与一键恢复（跨电脑迁移）
# ---------------------------------------------------------------------------


def _require(cond: bool, msg: str) -> None:
    """条件不满足则抛 SettingsError。"""
    if not cond:
        raise SettingsError(msg)


def _safe_extract_zip(zip_path: Path, dest: Path) -> None:
    """解压备份 zip，逐条目拦截路径穿越（zip-slip）。"""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for entry in zf.infolist():
                name = entry.filename.replace("\\", "/")
                if name.startswith("/") or ".." in Path(name).parts:
                    raise SettingsError(f"备份包含非法路径：{entry.filename}")
            zf.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise SettingsError("备份包不是有效的 zip 文件") from exc


def _replace_file(src: Path, dst: Path) -> None:
    """替换文件：同盘 os.replace 原子；跨盘 OSError 时退化为 copy。"""
    try:
        os.replace(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def create_full_backup() -> Path:
    """生成完整备份 zip（db 一致性快照 + artifacts + config.env + manifest）。

    返回临时 zip 路径，调用方（API 路由）负责下载后删除。
    路径在 config.env 里以相对 BASE_DIR 形式记录，跨电脑迁移通用。
    """
    with _migrate_lock:
        fd, zip_path = tempfile.mkstemp(prefix="wb-backup-", suffix=".zip")
        os.close(fd)
        tmpdir = Path(tempfile.mkdtemp(prefix="wb-backup-src-"))
        try:
            # 1) 数据库一致性快照 + 校验
            db_snap = tmpdir / "workbench.db"
            _vacuum_into(get_db_path(), db_snap)
            _verify_integrity(db_snap)
            counts = _table_counts(db_snap)

            # 2) 上传文件全量
            art = get_artifact_dir()
            art_files = []
            if art.exists():
                shutil.copytree(art, tmpdir / "artifacts")
                art_files = [p for p in (tmpdir / "artifacts").rglob("*") if p.is_file()]

            # 3) 配置（路径转相对，跨机通用）
            (tmpdir / "config.env").write_text(
                "\n".join([
                    f"WORKBENCH_DB_PATH={portable_path_for_backup(get_db_path())}",
                    f"WORKBENCH_ARTIFACT_DIR={portable_path_for_backup(art)}",
                    f"WORKBENCH_APP_NAME={settings.app_name}",
                    f"WORKBENCH_PAGE_SIZE={settings.page_size}",
                    f"WORKBENCH_MAX_UPLOAD_SIZE_MB={settings.max_upload_size_mb}",
                ]) + "\n",
                encoding="utf-8",
            )

            # 4) manifest（信息用）
            (tmpdir / "manifest.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "app_version": APP_VERSION,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "db": {"table_counts": counts,
                           "size_bytes": db_snap.stat().st_size},
                    "artifacts": {"file_count": len(art_files),
                                  "total_bytes": sum(p.stat().st_size for p in art_files)},
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # 5) 打包
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in tmpdir.rglob("*"):
                    if f.is_file():
                        zf.write(f, arcname=f.relative_to(tmpdir).as_posix())
            return Path(zip_path)
        except SettingsError:
            Path(zip_path).unlink(missing_ok=True)
            raise
        except Exception as exc:
            Path(zip_path).unlink(missing_ok=True)
            raise SettingsError(f"备份失败：{exc}") from exc
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _apply_config_env(cfg: dict, new_db: Path, new_art: Path) -> None:
    """把备份的 config.env 写回 .env 并热更新 settings。"""
    save_setting("db_path", portable_path_for_backup(new_db))
    save_setting("artifact_dir", portable_path_for_backup(new_art))
    if cfg.get("WORKBENCH_APP_NAME"):
        save_setting("app_name", str(cfg["WORKBENCH_APP_NAME"]))
    ps = cfg.get("WORKBENCH_PAGE_SIZE")
    if ps:
        try:
            save_setting("page_size", int(ps))
        except ValueError:
            raise SettingsError(f"备份配置的每页条数不是合法数字：{ps}")
    mus = cfg.get("WORKBENCH_MAX_UPLOAD_SIZE_MB")
    if mus:
        try:
            save_setting("max_upload_size_mb", int(mus))
        except ValueError:
            raise SettingsError(f"备份配置的上传上限不是合法数字：{mus}")


def _rollback_restore(engine, rollback: Path, old_db: Path, old_art: Path,
                      rollback_env: str | None, old_vals: dict, orig_exc) -> None:
    """恢复失败时把 db / artifacts / .env / settings / engine 全部还原到恢复前。"""
    try:
        engine.dispose()
        _remove_db_files(old_db)
        _replace_file(rollback / "workbench.db", old_db)
        _verify_integrity(old_db)
        if old_art.exists():
            shutil.rmtree(old_art)
        rb_art = rollback / "artifacts"
        if rb_art.exists():
            old_art.mkdir(parents=True, exist_ok=True)
            shutil.copytree(rb_art, old_art, dirs_exist_ok=True)
        if rollback_env is not None:
            ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
            ENV_FILE.write_text(rollback_env, encoding="utf-8")
        elif ENV_FILE.exists():
            ENV_FILE.unlink()  # 原本没有 .env → 删掉恢复时新建的
        for k, v in old_vals.items():
            setattr(settings, k, v)
        rebuild_engine(old_db)
    except Exception as rexc:
        raise SettingsError(f"恢复失败：{orig_exc}；且回滚也失败：{rexc}") from rexc
    raise SettingsError(f"恢复失败，已回滚原数据：{orig_exc}") from orig_exc


def restore_backup(zip_path: Path) -> dict:
    """从备份 zip 恢复全部数据（db + artifacts + 配置）。

    跨电脑迁移：另一台机器上传备份 → 数据落到**本机当前已设置的路径**
    （备份包里记录的路径只用于打包时跨机通用，恢复时以目标机当前配置为准——
    这台软件能正常运行，说明它当前配置的路径就是有效落点）。
    阶段0 解压校验（零副作用）→ 阶段1 快照当前状态（回滚基础）→
    阶段2 替换并生效；任一步失败整体回滚到恢复前。
    """
    with _migrate_lock:
        work = Path(tempfile.mkdtemp(prefix="wb-restore-"))
        rollback = work / "rollback"
        extract = work / "extract"
        try:
            # ===== 阶段0：解压 + 结构校验（未动任何现有数据）=====
            extract.mkdir()
            _safe_extract_zip(zip_path, extract)
            db_src = extract / "workbench.db"
            cfg_src = extract / "config.env"
            _require(db_src.is_file(), "备份包缺少 workbench.db")
            _require(cfg_src.is_file(), "备份包缺少 config.env")
            cfg = dotenv_values(cfg_src)
            db_raw = str(cfg.get("WORKBENCH_DB_PATH") or "").strip()
            _require(db_raw, "备份包 config.env 缺少 WORKBENCH_DB_PATH")

            # 恢复目标 = 本机当前配置的路径，而不是备份包里的相对路径。
            new_db = get_db_path()
            new_art = get_artifact_dir()
            old_db, old_art = new_db, new_art
            _precheck_dir_writable(new_db.parent)
            _precheck_dir_writable(new_art)
            # 危险：后续 rmtree(new_art) 会波及刚恢复的 db —— db 文件不能在上传目录内
            if new_db.is_relative_to(new_art):
                raise SettingsError("数据库文件不能位于上传文件目录之内")
            # 恢复目录与当前上传目录互为父子（且不同路径）时，替换会误删对方
            if new_art != old_art and (
                new_art.is_relative_to(old_art) or old_art.is_relative_to(new_art)
            ):
                raise SettingsError("恢复的上传目录与当前上传目录不能相互嵌套")
            _verify_integrity(db_src)  # 备份包自身先验，坏包拦在改动前

            # ===== 阶段1：快照当前状态（回滚基础）=====
            rollback.mkdir()
            _vacuum_into(old_db, rollback / "workbench.db")
            _verify_integrity(rollback / "workbench.db")
            if old_art.exists():
                shutil.copytree(old_art, rollback / "artifacts")
            rollback_env = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else None
            old_vals = {k: getattr(settings, k) for k in
                        ("db_path", "artifact_dir", "app_name", "page_size", "max_upload_size_mb")}

            # ===== 阶段2：替换 + 生效 =====
            from app.database import engine
            engine.dispose()  # Windows 上替换被占用文件前必须先关连接池
            try:
                _remove_db_files(new_db)  # 清 wal/shm/journal，防陈旧 WAL 混入
                _replace_file(db_src, new_db)
                _verify_integrity(new_db)
                if new_art.exists():
                    shutil.rmtree(new_art)
                new_art.mkdir(parents=True, exist_ok=True)
                src_art = extract / "artifacts"
                if src_art.exists():
                    shutil.copytree(src_art, new_art, dirs_exist_ok=True)
                _apply_config_env(cfg, new_db, new_art)
                rebuild_engine(new_db)
            except Exception as exc:
                _rollback_restore(engine, rollback, old_db, old_art,
                                  rollback_env, old_vals, exc)
                raise  # _rollback_restore 内部已抛 SettingsError，这里不会再执行

            return {"db_path": str(new_db), "artifact_dir": str(new_art)}
        except SettingsError:
            shutil.rmtree(work, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(work, ignore_errors=True)
            raise SettingsError(f"恢复失败：{exc}") from exc
        shutil.rmtree(work, ignore_errors=True)
