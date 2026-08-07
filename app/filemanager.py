"""
VPS 文件管理器（仅管理员）— 挂载于 /file
浏览 / 上传 / 下载 / 打包下载 / 新建目录 / 重命名 / 移动 / 删除 VPS 全盘文件。
所有接口均强制 require_admin（未登录 401，非管理员 403），页面路由在 main.py 中做登录 + 管理员校验。
"""
import os
import shutil
import tempfile
import zipfile
from datetime import datetime

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.auth import require_admin

router = APIRouter(prefix="/file/api", tags=["文件管理"])

_CHUNK = 1024 * 1024  # 1MB 流式读写块


# ── 工具函数 ──────────────────────────────────────────────────────────

def _real(raw: str) -> str:
    """规范化路径：必须为绝对路径，返回 realpath"""
    if not raw or not raw.strip():
        raw = "/"
    return os.path.realpath(os.path.expanduser(raw.strip()))


def _exists(p: str) -> bool:
    return os.path.exists(p) or os.path.islink(p)


def _require_path(raw: str) -> str:
    p = _real(raw)
    if not _exists(p):
        raise HTTPException(status_code=404, detail=f"路径不存在: {raw}")
    return p


def _safe_name(name: str) -> str:
    """文件名净化：仅保留 basename，拒绝路径分隔符/空名/特殊项"""
    name = os.path.basename(name.replace("\\", "/")).strip()
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise HTTPException(status_code=400, detail="非法的文件名")
    return name


def _fmt_size(n) -> str:
    if n is None:
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def _fmt_time(ts) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError, TypeError):
        return "-"


def _mode_str(st) -> str:
    try:
        return oct(st.st_mode & 0o777)[2:]
    except (OSError, AttributeError):
        return "-"


# ── 目录列表 ──────────────────────────────────────────────────────────

@router.get("/list")
def list_dir(path: str = "/", admin=Depends(require_admin)):
    p = _require_path(path)
    if not os.path.isdir(p):
        raise HTTPException(status_code=400, detail=f"不是目录: {path}")
    entries = []
    try:
        with os.scandir(p) as it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                    is_dir = e.is_dir(follow_symlinks=False)
                    entries.append({
                        "name": e.name,
                        "path": os.path.join(p, e.name),
                        "is_dir": is_dir,
                        "is_link": e.is_symlink(),
                        "size": st.st_size if not is_dir else None,
                        "size_text": _fmt_size(st.st_size) if not is_dir else "-",
                        "mtime": st.st_mtime,
                        "mtime_text": _fmt_time(st.st_mtime),
                        "mode": _mode_str(st),
                    })
                except OSError:
                    entries.append({
                        "name": e.name, "path": os.path.join(p, e.name),
                        "is_dir": False, "is_link": False, "size": None,
                        "size_text": "-", "mtime": 0, "mtime_text": "-", "mode": "-",
                    })
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"无权限读取目录: {path}")
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return {
        "path": p,
        "parent": os.path.dirname(p) if p != "/" else None,
        "name": os.path.basename(p) or "/",
        "entries": entries,
    }


# ── 下载（文件直下 / 目录打包 zip） ───────────────────────────────────

@router.get("/download")
def download(path: str = "/", admin=Depends(require_admin)):
    p = _require_path(path)
    if os.path.isdir(p) and not os.path.islink(p):
        base = os.path.basename(p) or "folder"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp_path = tmp.name
        tmp.close()
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(p):
                    dirs.sort()
                    for fn in sorted(files):
                        fp = os.path.join(root, fn)
                        arc = os.path.relpath(fp, os.path.dirname(p))
                        try:
                            zf.write(fp, arc)
                        except OSError:
                            continue
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return FileResponse(
            tmp_path,
            filename=f"{base}.zip",
            media_type="application/zip",
            background=BackgroundTask(os.unlink, tmp_path),
        )
    if not os.path.isfile(p):
        raise HTTPException(status_code=400, detail="仅支持下载文件或目录")
    return FileResponse(p, filename=os.path.basename(p))


# ── 上传（流式写盘，同名自动加序号避免覆盖） ─────────────────────────

@router.post("/upload")
async def upload(path: str = "/", files: list[UploadFile] = File(...), admin=Depends(require_admin)):
    p = _require_path(path)
    if not os.path.isdir(p):
        raise HTTPException(status_code=400, detail="上传目标必须是目录")
    if not files:
        raise HTTPException(status_code=400, detail="未选择文件")
    saved = []
    for f in files:
        name = _safe_name(f.filename or "")
        if not name:
            continue
        dest = os.path.join(p, name)
        if _exists(dest):
            stem, ext = os.path.splitext(name)
            i = 1
            while _exists(os.path.join(p, f"{stem} ({i}){ext}")):
                i += 1
            dest = os.path.join(p, f"{stem} ({i}){ext}")
        try:
            async with aiofiles.open(dest, "wb") as out:
                while True:
                    chunk = await f.read(_CHUNK)
                    if not chunk:
                        break
                    await out.write(chunk)
            saved.append({"name": os.path.basename(dest), "path": dest})
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"写入失败 {name}: {e}")
        finally:
            await f.close()
    if not saved:
        raise HTTPException(status_code=400, detail="没有可保存的文件")
    return {"success": True, "saved": saved}


# ── 新建目录 ──────────────────────────────────────────────────────────

class MkdirReq(BaseModel):
    path: str
    name: str


@router.post("/mkdir")
def mkdir(req: MkdirReq, admin=Depends(require_admin)):
    parent = _require_path(req.path)
    if not os.path.isdir(parent):
        raise HTTPException(status_code=400, detail="父路径不是目录")
    name = _safe_name(req.name)
    target = os.path.join(parent, name)
    if _exists(target):
        raise HTTPException(status_code=400, detail=f"已存在: {name}")
    try:
        os.mkdir(target)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"创建失败: {e}")
    return {"success": True, "path": target}


# ── 重命名 / 移动 ─────────────────────────────────────────────────────

class RenameReq(BaseModel):
    path: str
    new_name: str


class MoveReq(BaseModel):
    path: str
    dest_dir: str


@router.post("/rename")
def rename(req: RenameReq, admin=Depends(require_admin)):
    p = _require_path(req.path)
    if p == "/":
        raise HTTPException(status_code=400, detail="不能重命名根目录")
    new_name = _safe_name(req.new_name)
    dest = os.path.join(os.path.dirname(p), new_name)
    if _exists(dest):
        raise HTTPException(status_code=400, detail=f"已存在: {new_name}")
    try:
        os.rename(p, dest)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"重命名失败: {e}")
    return {"success": True, "path": dest}


@router.post("/move")
def move(req: MoveReq, admin=Depends(require_admin)):
    p = _require_path(req.path)
    if p == "/":
        raise HTTPException(status_code=400, detail="不能移动根目录")
    dest_dir = _require_path(req.dest_dir)
    if not os.path.isdir(dest_dir):
        raise HTTPException(status_code=400, detail="目标必须是目录")
    dest = os.path.join(dest_dir, os.path.basename(p))
    if _exists(dest):
        raise HTTPException(status_code=400, detail=f"目标已存在: {dest}")
    try:
        os.rename(p, dest)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"移动失败: {e}")
    return {"success": True, "path": dest}


# ── 删除（目录需 recursive 或为空） ───────────────────────────────────

class DeleteReq(BaseModel):
    path: str
    recursive: bool = False


@router.post("/delete")
def delete(req: DeleteReq, admin=Depends(require_admin)):
    p = _require_path(req.path)
    if p == "/":
        raise HTTPException(status_code=400, detail="不能删除根目录")
    try:
        if os.path.isdir(p) and not os.path.islink(p):
            if req.recursive:
                shutil.rmtree(p)
            else:
                os.rmdir(p)  # 非空目录会抛 OSError
        else:
            os.unlink(p)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")
    return {"success": True, "path": p}


# ── 磁盘信息 ──────────────────────────────────────────────────────────

@router.get("/disk")
def disk(admin=Depends(require_admin)):
    try:
        du = shutil.disk_usage("/")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"磁盘信息获取失败: {e}")
    return {
        "total": du.total,
        "used": du.used,
        "free": du.free,
        "total_text": _fmt_size(du.total),
        "used_text": _fmt_size(du.used),
        "free_text": _fmt_size(du.free),
        "percent": round(du.used / du.total * 100, 1),
    }
