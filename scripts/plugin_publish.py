#!/usr/bin/env python3
"""企业市场插件：打包 plugins/<name>/ 并用 CI 上传凭据传成 draft。

用法：
  python3 scripts/plugin_publish.py pack <name> [--out dist/plugins]   # 只打包，打印 sha256
  MARKET_UPLOAD_TOKEN=mkt_... python3 scripts/plugin_publish.py upload <name> \
      [--base-url https://www.sid-code.cc/traj/api/v1]

只到 draft 为止：发布（publish）要写理由、只能在管理台用 admin 会话做（admin.py 的
publish 挂 require_web_session）。上传凭据本来就只授予「传 draft」这一个动作。

为什么自己控制 tar 头而不是调系统 tar：同一份源码必须打出同一个 sha256，
否则「版本不可覆盖」时没法判断线上那份是不是这份源码打出来的。系统 tar 会写入
mtime / uid / uname，macOS bsdtar 还会塞 `._*` 与 pax 扩展属性。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_ROOT = REPO_ROOT / "plugins"
DEFAULT_BASE_URL = "https://www.sid-code.cc/traj/api/v1"
# 打包时跳过的文件：编辑器 / 系统产物，进包只会让 sha256 随机器变化
_SKIP_NAMES = {".DS_Store"}


def plugin_dir(name: str) -> Path:
    d = PLUGINS_ROOT / name
    if not (d / "plugin.json").is_file():
        raise SystemExit(f"plugins/{name}/plugin.json 不存在")
    return d


def _collect(root: Path) -> list[str]:
    files: list[str] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.name in _SKIP_NAMES or p.name.startswith("._"):
            continue
        if p.is_symlink():
            # 服务端会以 422 拒绝链接，这里提前报错，免得传上去才知道
            raise SystemExit(f"插件里不允许符号链接: {rel}")
        if p.is_file():
            files.append(rel)
    return files


def pack(name: str) -> bytes:
    """确定性打包：条目排序、mtime/uid/gid 归零、只写普通文件与目录、gzip 头不带时间。"""
    root = plugin_dir(name)
    files = _collect(root)
    dirs = sorted({str(Path(f).parent.as_posix()) for f in files if "/" in f} | set())
    # 补齐所有上级目录（a/b/c.md → a、a/b）
    all_dirs: set[str] = set()
    for d in dirs:
        parts = d.split("/")
        for i in range(1, len(parts) + 1):
            all_dirs.add("/".join(parts[:i]))

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for path in sorted(all_dirs | set(files)):
            info = tarfile.TarInfo(path + "/" if path in all_dirs else path)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if path in all_dirs:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            else:
                data = (root / path).read_bytes()
                info.size = len(data)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))

    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0, filename="") as f:
        f.write(raw.getvalue())
    return gz.getvalue()


def manifest_of(name: str) -> dict:
    return json.loads((plugin_dir(name) / "plugin.json").read_text(encoding="utf-8"))


def _multipart(field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: application/gzip\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + content + tail, f"multipart/form-data; boundary={boundary}"


def upload(name: str, base_url: str, token: str) -> dict:
    m = manifest_of(name)
    content = pack(name)
    body, ctype = _multipart("file", f"{name}-{m['version']}.tar.gz", content)
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/marketplace/items/{name}/versions",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": ctype},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # 409 = 版本已存在（不可覆盖）；422 = 包不合格，detail 里是逐条原因
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"上传失败 HTTP {exc.code}: {detail}") from None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_pack = sub.add_parser("pack")
    p_pack.add_argument("name")
    p_pack.add_argument("--out", default=str(REPO_ROOT / "dist" / "plugins"))
    p_up = sub.add_parser("upload")
    p_up.add_argument("name")
    p_up.add_argument("--base-url", default=os.environ.get("MARKET_BASE_URL", DEFAULT_BASE_URL))
    args = ap.parse_args(argv)

    if args.cmd == "pack":
        content = pack(args.name)
        version = manifest_of(args.name)["version"]
        out = Path(args.out) / f"{args.name}-{version}.tar.gz"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
        print(f"{out}  sha256={hashlib.sha256(content).hexdigest()}  size={len(content)}")
        return 0

    token = os.environ.get("MARKET_UPLOAD_TOKEN", "")
    if not token.startswith("mkt_"):
        raise SystemExit("需要环境变量 MARKET_UPLOAD_TOKEN（管理台「市场 → 上传凭据」签发，形如 mkt_...）")
    result = upload(args.name, args.base_url, token)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"已上传为 draft。发布：管理台「市场」页 → {args.name} → {result.get('version')} → 写理由发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
