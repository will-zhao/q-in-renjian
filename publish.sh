#!/usr/bin/env bash
# publish.sh —— 一键发布:同步转换 → 本地构建校验 → 提交 → 推送
# 用法: ./publish.sh ["提交说明"]
set -euo pipefail

cd "$(dirname "$0")"

MSG="${1:-publish: $(date '+%Y-%m-%d %H:%M')}"

echo "==> 1/4 规范化文章(读取 config.json 里的 source_dir)"
python3 scripts/publish.py

echo "==> 2/4 本地构建校验(hugo)"
hugo --gc --minify >/dev/null
echo "    构建 OK"

echo "==> 3/4 提交改动"
git add -A
if git diff --cached --quiet; then
  echo "    没有内容变化,无需提交。"
  exit 0
fi
git commit -m "$MSG"

echo "==> 4/4 推送到 GitHub(触发网站部署 + 公众号推送)"
git push

echo "✅ 完成。GitHub Action 将自动构建网站并推送公众号草稿。"
