#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wechat_push.py —— 在「固定 IP 的 VM」上运行,把 content/posts 里的文章推送到微信公众号草稿箱。

为什么在 VM 上跑:微信要求调用方 IP 在白名单里,而 VM 有固定公网 IP。
GitHub Action 只负责触发(SSH 进来运行本脚本),真正的微信 API 调用从 VM 发出。

密钥来源(二选一,不进仓库):
  - 环境变量 WECHAT_APPID / WECHAT_APPSECRET
  - 或 ~/.wechat/config.json : {"appid":"...","appsecret":"...","author":"Will"}

状态/缓存(放在 ~/.wechat/,不进仓库):
  - token.json : 缓存 access_token(2 小时有效,避免频繁获取触发限额)
  - state.json : 记录每篇文章的内容 hash 与草稿 media_id,用于增量推送/更新

用法:
  python3 scripts/wechat_push.py            # 只推送“新增或改动”的文章
  python3 scripts/wechat_push.py --all       # 强制重推全部
  python3 scripts/wechat_push.py --only 屋    # 只推送指定标题的文章
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("缺少依赖: 请先运行  pip3 install requests markdown")
try:
    import markdown as md_lib
except ImportError:
    sys.exit("缺少依赖: 请先运行  pip3 install requests markdown")

API = "https://api.weixin.qq.com/cgi-bin"
ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = ROOT / "content" / "posts"
STATE_DIR = Path(os.path.expanduser("~/.wechat"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_CACHE = STATE_DIR / "token.json"
STATE_FILE = STATE_DIR / "state.json"
CONFIG_FILE = STATE_DIR / "config.json"

FM_RE = re.compile(r"^\+\+\+\s*\n(.*?)\n\+\+\+\s*\n(.*)$", re.DOTALL)
MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)\s*\)")


# ---------------- 配置与状态 ----------------
def load_config():
    cfg = {}
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    appid = os.environ.get("WECHAT_APPID", cfg.get("appid"))
    appsecret = os.environ.get("WECHAT_APPSECRET", cfg.get("appsecret"))
    if not appid or not appsecret:
        sys.exit("未找到 appid/appsecret。请设置环境变量或创建 ~/.wechat/config.json")
    cfg["appid"], cfg["appsecret"] = appid, appsecret
    cfg.setdefault("author", "")
    return cfg


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- access_token(带缓存) ----------------
def get_access_token(cfg):
    if TOKEN_CACHE.exists():
        data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
        if data.get("expires_at", 0) > time.time() + 120:
            return data["access_token"]
    r = requests.get(f"{API}/token", params={
        "grant_type": "client_credential",
        "appid": cfg["appid"],
        "secret": cfg["appsecret"],
    }, timeout=20).json()
    if "access_token" not in r:
        sys.exit(f"获取 access_token 失败: {r}")
    TOKEN_CACHE.write_text(json.dumps({
        "access_token": r["access_token"],
        "expires_at": time.time() + r.get("expires_in", 7200),
    }), encoding="utf-8")
    return r["access_token"]


def check_err(r, ctx):
    if isinstance(r, dict) and r.get("errcode", 0) not in (0,):
        sys.exit(f"[{ctx}] 微信接口返回错误: {r}")
    return r


# ---------------- 图片上传 ----------------
def upload_content_image(token, path: Path):
    """上传正文内嵌图片,返回可用于文章正文的微信图片 URL(不占素材库限额)。"""
    with open(path, "rb") as f:
        r = requests.post(f"{API}/media/uploadimg", params={"access_token": token},
                          files={"media": (path.name, f)}, timeout=60).json()
    check_err(r, f"uploadimg {path.name}")
    return r["url"]


def add_permanent_image(token, path: Path):
    """上传为永久图片素材,返回 media_id(用于封面 thumb_media_id)。"""
    with open(path, "rb") as f:
        r = requests.post(f"{API}/material/add_material",
                          params={"access_token": token, "type": "image"},
                          files={"media": (path.name, f)}, timeout=60).json()
    check_err(r, f"add_material {path.name}")
    return r["media_id"]


# ---------------- Markdown -> 公众号 HTML ----------------
STYLE = {
    "p": "margin:0 0 1.2em;line-height:1.9;font-size:16px;color:#333;letter-spacing:0.4px;",
    "h1": "font-size:22px;font-weight:bold;margin:1.4em 0 0.6em;color:#222;",
    "h2": "font-size:20px;font-weight:bold;margin:1.3em 0 0.6em;color:#222;",
    "h3": "font-size:18px;font-weight:bold;margin:1.2em 0 0.5em;color:#222;",
    "blockquote": ("margin:1.2em 0;padding:0.6em 1em;border-left:4px solid #d0d0d0;"
                   "background:#f7f7f7;color:#555;font-size:15px;line-height:1.8;"),
    "img": "max-width:100%;height:auto;display:block;margin:1em auto;border-radius:4px;",
    "li": "line-height:1.9;font-size:16px;color:#333;margin:0.3em 0;",
    "strong": "font-weight:bold;color:#222;",
}


def inject_inline_styles(html: str) -> str:
    """公众号会过滤 class/<style>,所以给常见标签打上内联样式。"""
    for tag, style in STYLE.items():
        html = re.sub(rf"<{tag}>", f'<{tag} style="{style}">', html)
        # 处理带属性的开标签(如 img/blockquote 已含属性的情况)
        html = re.sub(rf"<{tag}(\s+[^>]*?)>",
                      lambda m, s=style, t=tag: f'<{t} style="{s}"{m.group(1)}>'
                      if "style=" not in m.group(1) else m.group(0),
                      html)
    return html


def parse_article(index_md: Path):
    raw = index_md.read_text(encoding="utf-8")
    m = FM_RE.match(raw)
    if not m:
        return None
    fm_text, body = m.group(1), m.group(2)
    meta = {}
    for line in fm_text.splitlines():
        mm = re.match(r'\s*(\w+)\s*=\s*(.+)', line)
        if mm:
            key, val = mm.group(1), mm.group(2).strip()
            meta[key] = val.strip('"').strip("[]").strip('"')
    return meta, body


def build_html(token, slug_dir: Path, body: str):
    """把正文里的本地图片上传换成微信 URL,再转 HTML 并内联样式。"""
    url_map = {}

    def repl(m):
        ref = m.group(1)
        if ref.startswith(("http://", "https://")):
            return m.group(0)
        img_path = slug_dir / ref
        if not img_path.is_file():
            return m.group(0)
        if ref not in url_map:
            url_map[ref] = upload_content_image(token, img_path)
        return f"![]({url_map[ref]})"

    body = MD_IMG_RE.sub(repl, body)
    html = md_lib.markdown(body, extensions=["extra", "nl2br", "sane_lists"])
    html = inject_inline_styles(html)
    wrapper = ('<section style="font-size:16px;color:#333;line-height:1.9;'
               'padding:0 2px;">{}</section>')
    return wrapper.format(html), url_map


def first_local_image(slug_dir: Path, body: str):
    for m in MD_IMG_RE.finditer(body):
        ref = m.group(1)
        if not ref.startswith(("http://", "https://")):
            p = slug_dir / ref
            if p.is_file():
                return p
    return None


def make_digest(body: str, n=100):
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)   # 去图片
    text = re.sub(r"[#*>`\-\[\]()]", "", text)          # 去 markdown 符号
    text = re.sub(r"\s+", "", text)
    return text[:n]


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="强制重推全部文章")
    ap.add_argument("--only", help="只推送指定标题的文章")
    ap.add_argument("--mark-done", nargs="+", metavar="标题",
                    help="把指定标题标记为已处理(记录hash但不推送),自动化将跳过它们")
    args = ap.parse_args()

    cfg = load_config()
    state = load_state()

    # --mark-done: 只更新状态,不调用任何微信接口
    if args.mark_done:
        for name in args.mark_done:
            idx = CONTENT_DIR / name / "index.md"
            if idx.exists():
                state[name] = {"hash": hashlib.md5(idx.read_bytes()).hexdigest(),
                               "title": name, "note": "manually handled"}
                print(f"已标记跳过: {name}")
            else:
                print(f"未找到: {name}(检查标题是否与 content/posts 下目录名一致)")
        save_state(state)
        print("state.json 更新完成")
        return

    token = get_access_token(cfg)
    print(f"[ok] 已获取 access_token(固定IP白名单生效)")

    default_cover_media = None  # 无图文章的封面:复用第一张成功上传的封面

    dirs = sorted([d for d in CONTENT_DIR.iterdir() if d.is_dir()])
    pushed = 0
    for slug_dir in dirs:
        index_md = slug_dir / "index.md"
        if not index_md.exists():
            continue
        parsed = parse_article(index_md)
        if not parsed:
            print(f"[跳过] 无法解析 frontmatter: {slug_dir.name}")
            continue
        meta, body = parsed
        title = meta.get("title", slug_dir.name)

        if args.only and args.only not in (title, slug_dir.name):
            continue

        content_hash = hashlib.md5(index_md.read_bytes()).hexdigest()
        prev = state.get(slug_dir.name, {})
        if not args.all and prev.get("hash") == content_hash:
            print(f"[未变] {title}")
            continue

        print(f"[处理] {title} ...")
        html, _ = build_html(token, slug_dir, body)

        # 封面:优先用文章首图,否则复用一个默认封面
        cover = first_local_image(slug_dir, body)
        if cover:
            thumb_media_id = add_permanent_image(token, cover)
        else:
            if default_cover_media is None:
                dc = ROOT / "assets" / "wechat" / "default-cover.png"
                if not dc.is_file():
                    sys.exit(f"文章「{title}」无配图,且缺少默认封面 {dc}。请提供默认封面图。")
                default_cover_media = add_permanent_image(token, dc)
            thumb_media_id = default_cover_media

        article = {
            "title": title[:64],
            "author": cfg.get("author", ""),
            "digest": make_digest(body),
            "content": html,
            "content_source_url": "",
            "thumb_media_id": thumb_media_id,
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
        }

        if prev.get("media_id") and not args.all:
            # 已有草稿 -> 更新
            r = requests.post(f"{API}/draft/update", params={"access_token": token},
                              data=json.dumps({"media_id": prev["media_id"], "index": 0,
                                               "articles": article}, ensure_ascii=False
                                              ).encode("utf-8"), timeout=60).json()
            check_err(r, f"draft/update {title}")
            media_id = prev["media_id"]
            print(f"       ↳ 已更新草稿")
        else:
            r = requests.post(f"{API}/draft/add", params={"access_token": token},
                              data=json.dumps({"articles": [article]}, ensure_ascii=False
                                              ).encode("utf-8"), timeout=60).json()
            check_err(r, f"draft/add {title}")
            media_id = r["media_id"]
            print(f"       ↳ 已创建草稿 media_id={media_id}")

        state[slug_dir.name] = {"hash": content_hash, "media_id": media_id, "title": title}
        save_state(state)
        pushed += 1

    print(f"\n完成:本次推送 {pushed} 篇到公众号草稿箱。请在手机公众号后台『草稿箱』查看并终审发布。")


if __name__ == "__main__":
    main()
