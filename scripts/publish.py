#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish.py —— 工作流引擎的「输入规范化」核心。

作用:
  从 config.json 里的 source_dir(任意“装着 md+图片的文件夹”,与写作软件无关)
  读取文章,转换成 Hugo 可直接构建的 page bundle:
    content/posts/<标题>/index.md      (带 frontmatter + 标准 markdown 图片)
    content/posts/<标题>/img-N.ext     (文章引用的图片,拷进来做成 bundle)

设计原则:
  - 不依赖 Obsidian / Google Drive:只认“文件夹里的 .md 和图片”。
  - 同时兼容两种图片写法:Obsidian 的 ![[xxx.png]] 和标准 ![](xxx.png)。
  - 分类 = 一级子文件夹名(诗歌集/亲子集/小说集/散文集)。
  - 标题 = 文件名。
  - 首次发布日期会被记住(重复运行不会重置)。
"""
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 引擎工程根目录
CONFIG_PATH = ROOT / "config.json"

IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

# 单遍匹配两种写法:Obsidian 嵌入 ![[name|别名]] 或 标准 markdown ![alt](path "title")
# 用 alternation 一次扫描,避免转换结果被二次匹配造成误报。
IMAGE_PATTERN = re.compile(
    r"!\[\[(?P<obs>[^\]|]+?)(?:\|[^\]]*)?\]\]"          # Obsidian: ![[xxx.png]]
    r"|!\[[^\]]*\]\(\s*<?(?P<md>[^)>\s]+)>?(?:\s+\"[^\"]*\")?\s*\)"  # 标准 markdown
)
# 已存在的 frontmatter 里的 date 行(用于保持首次发布日期)
DATE_LINE = re.compile(r'^date\s*=\s*"([^"]+)"', re.MULTILINE)


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_file_index(source_dir: Path):
    """把 source_dir 下所有图片按 basename 建索引,方便 Obsidian ![[name]] 定位。"""
    index = {}
    for p in source_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMG_EXTS:
            index.setdefault(p.name, p)  # 同名取第一个
    return index


def sanitize_slug(name: str) -> str:
    """文章目录名:去掉文件系统不安全字符,保留中文。"""
    name = name.strip()
    return re.sub(r'[\\/:*?"<>|]+', "_", name)


def existing_date(index_md: Path):
    if index_md.exists():
        m = DATE_LINE.search(index_md.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return None


def resolve_image(ref: str, md_file: Path, source_dir: Path, file_index: dict):
    """把一个图片引用解析成实际文件路径。"""
    ref = ref.strip()
    # 1) 相对 md 文件
    cand = (md_file.parent / ref).resolve()
    if cand.is_file():
        return cand
    # 2) 相对 source_dir
    cand = (source_dir / ref).resolve()
    if cand.is_file():
        return cand
    # 3) 按 basename 在全库找(Obsidian 常见)
    base = Path(ref).name
    if base in file_index:
        return file_index[base]
    return None


def convert_article(md_file: Path, category: str, cfg: dict, source_dir: Path,
                    file_index: dict, content_root: Path):
    title = md_file.stem
    slug = sanitize_slug(title)
    bundle = content_root / slug
    index_md = bundle / "index.md"

    # 保持首次发布日期
    date = existing_date(index_md) or datetime.now().strftime("%Y-%m-%dT%H:%M:%S+02:00")

    body = md_file.read_text(encoding="utf-8")

    # 重新生成 bundle 目录(先清旧图片,避免残留)
    if bundle.exists():
        for old in bundle.glob("img-*"):
            old.unlink()
    bundle.mkdir(parents=True, exist_ok=True)

    copied = {}   # 源图片路径 -> bundle 内新文件名
    counter = [0]
    missing = []

    def copy_image(src_path: Path) -> str:
        if src_path in copied:
            return copied[src_path]
        counter[0] += 1
        new_name = f"img-{counter[0]}{src_path.suffix.lower()}"
        shutil.copy2(src_path, bundle / new_name)
        copied[src_path] = new_name
        return new_name

    # 单遍处理所有图片引用(Obsidian 嵌入 + 标准 markdown)
    def repl_image(m):
        if m.group("obs") is not None:
            ref = m.group("obs")
        else:
            ref = m.group("md")
            if ref.startswith(("http://", "https://", "data:")):
                return m.group(0)  # 外链保持不动
        src = resolve_image(ref, md_file, source_dir, file_index)
        if src is None:
            missing.append(ref)
            return m.group(0)
        return f"![]({copy_image(src)})"

    body = IMAGE_PATTERN.sub(repl_image, body)

    # 生成 frontmatter(TOML)
    esc_title = title.replace('"', '\\"')
    frontmatter = (
        "+++\n"
        f'title = "{esc_title}"\n'
        f'date = "{date}"\n'
        f'categories = ["{category}"]\n'
        "tags = []\n"
        "draft = false\n"
        "+++\n\n"
    )
    index_md.write_text(frontmatter + body.lstrip("\n"), encoding="utf-8")
    return slug, len(copied), missing


def main():
    cfg = load_config()
    source_dir = Path(cfg["source_dir"]).expanduser()
    if not source_dir.is_dir():
        print(f"[错误] source_dir 不存在: {source_dir}", file=sys.stderr)
        sys.exit(1)

    content_root = ROOT / cfg.get("content_dir", "content/posts")
    exclude = set(cfg.get("exclude_dirs", []))
    file_index = build_file_index(source_dir)

    # 清空旧的 posts(全量重建,保证删除的文章也会消失)——但保留 date 需先读取,已在上面处理
    # 为保留日期,这里不整体删除,而是逐篇覆盖;孤儿目录在最后清理。
    content_root.mkdir(parents=True, exist_ok=True)

    produced = set()
    total = 0
    print(f"源目录: {source_dir}")
    for cat_dir in sorted(source_dir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name in exclude:
            continue
        category = cat_dir.name
        for md_file in sorted(cat_dir.rglob("*.md")):
            slug, n_img, missing = convert_article(
                md_file, category, cfg, source_dir, file_index, content_root
            )
            produced.add(slug)
            total += 1
            note = f"  图片 {n_img} 张" if n_img else ""
            warn = f"  ⚠️ 找不到图片: {missing}" if missing else ""
            print(f"  [{category}] {slug}{note}{warn}")

    # 清理孤儿:content/posts 下不再对应任何源文章的目录
    for d in content_root.iterdir():
        if d.is_dir() and d.name not in produced:
            print(f"  [清理] 删除已不存在的文章: {d.name}")
            shutil.rmtree(d)

    print(f"完成:共处理 {total} 篇文章 -> {content_root}")


if __name__ == "__main__":
    main()
