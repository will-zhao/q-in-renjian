# Q在人间 —— 发布工作流引擎

把「任意文件夹里的 Markdown + 图片」自动发布为 Hugo 静态网站,并(后续)推送到微信公众号草稿箱。

## 架构(三层解耦)

```
① 写作层(可替换,非依赖)   →   ② 本引擎(独立 git 工程)   →   ③ 发布层
  Obsidian / VS Code / ...        规范化 + Hugo + Action          GitHub Pages 网站
  产出:一个 md+图片文件夹                                        + 微信公众号草稿(VM 固定IP)
```

写作工具与本引擎的**唯一连接点**是 `config.json` 里的 `source_dir`。换编辑器或换存放位置,只改这一行。

## 目录

- `config.json` —— 配置(source_dir / 排除目录 等)
- `scripts/publish.py` —— 把源文件夹规范化为 Hugo page bundle(补 frontmatter、统一图片语法、拷贝图片)
- `publish.sh` —— 一键:转换 → 本地构建校验 → commit → push
- `content/posts/` —— 自动生成的文章(请勿手改,会被覆盖)
- `.github/workflows/deploy.yml` —— GitHub Action:构建 Hugo 并部署到 Pages

## 日常使用

在写作工具里写完文章后:

```bash
./publish.sh "可选的提交说明"
```

## 输入约定

- 一级子文件夹名 = 文章分类(诗歌集 / 亲子集 / 小说集 / 散文集)
- 文件名 = 文章标题
- 图片支持两种写法:Obsidian `![[图片.png]]` 或标准 `![](图片.png)`
