# Q在人间 —— 发布工作流引擎

把「任意文件夹里的 Markdown + 图片」自动发布为 Hugo 静态网站,并推送到微信公众号草稿箱。

- 网站:https://will-zhao.github.io/q-in-renjian/
- 仓库:https://github.com/will-zhao/q-in-renjian

## 架构(三层解耦)

```
① 写作层(可替换,非依赖)        ② 本引擎(独立 git 工程)              ③ 发布层
  Obsidian / VS Code / ...   →   规范化(publish.py)+ Hugo + Action  →   GitHub Pages 网站
  产出:一个 md+图片文件夹          push 到 GitHub                        + 微信公众号草稿(VM 固定IP)
```

写作工具与本引擎的**唯一连接点**是 `config.json` 里的 `source_dir`。换编辑器或换存放位置,只改这一行,引擎代码不动。

微信 API 要求调用方 IP 在白名单里,而 GitHub Action 的 runner 没有固定 IP,所以:
**Action 只负责触发,真正调用微信的动作在固定 IP 的云主机(VM)上执行。**

## 目录结构

- `config.json` —— 配置:`source_dir`(写作文件夹)/ `exclude_dirs`(排除目录)/ `site_url`
- `scripts/publish.py` —— 把源文件夹规范化为 Hugo page bundle(补 frontmatter、统一图片语法、拷贝图片、清理孤儿)
- `scripts/wechat_push.py` —— 在 VM 上运行,把文章推送到公众号草稿箱
- `scripts/requirements.txt` —— VM 端 Python 依赖(requests / markdown)
- `publish.sh` —— 一键:转换 → 本地 Hugo 构建校验 → commit → push
- `content/posts/` —— **自动生成**的文章,请勿手改(每次发布会被覆盖/清理)
- `assets/wechat/default-cover.png` —— 无配图文章的默认封面
- `.github/workflows/deploy.yml` —— 构建 Hugo 并部署到 Pages
- `.github/workflows/wechat.yml` —— SSH 进 VM,拉取最新文章并推送公众号草稿

## 输入约定

- 一级子文件夹名 = 文章**分类**(诗歌集 / 亲子集 / 小说集 / 散文集…)
- 文件名 = 文章**标题**
- 图片放在 `attachments/`,正文用 `![[图片.png]]`(Obsidian)或标准 `![](图片.png)` 都可
- **封面缩略图**:文章有配图时自动取**第一张图**;没配图时用默认封面

---

## 日常使用

### 发布 / 更新文章

在写作工具里写完(或改完)后,在本工程目录执行:

```bash
cd ~/repo/q-in-renjian
./publish.sh "可选的提交说明"
```

它会:规范化 → 本地构建校验 → 提交 → 推送。推送后两个 Action 自动跑:
1. 构建网站并部署到 GitHub Pages;
2. SSH 进 VM,`git pull` 后运行 `wechat_push.py`,把**新增或改动**的文章推成公众号草稿。

最后在**手机公众号后台『草稿箱』**里终审、发布。

> 增量逻辑:`wechat_push.py` 用文章内容的 hash 判断,只推送「新增或改动」的文章,不会重复推送没变的文章。

---

## 删除一篇文章(重要)

删除要分别处理**三处**:源文件、网站、公众号。

### 1. 删掉源文件 + 更新网站(自动)

在写作文件夹(`source_dir`)里删掉那篇 `.md`,然后正常发布:

```bash
./publish.sh "删除文章:XXX"
```

`publish.py` 会检测到 `content/posts/` 下有个目录不再对应任何源文章(称为「孤儿」),
**自动删除它**并重建站点。推送后 GitHub Pages 会重新部署,**网站上这篇随之消失**(通常 1-2 分钟,CDN 可能有短暂缓存)。

终端会显示类似:`[清理] 删除已不存在的文章: XXX`。

### 2. 公众号草稿 —— 需要手动删

`wechat_push.py` **只新增/更新草稿,不会删除草稿**(微信侧删除属于不可逆操作,故意不自动做)。
所以如果这篇之前已经推成过草稿:

- **在手机/公众号后台的『草稿箱』里手动删除**这条草稿。
- 如果它已经**正式群发/发布**过,那属于已发文章,按公众号规则处理(通常只能删除已发内容,不影响本工作流)。

### 3.(可选)清理 VM 上的推送状态

VM 上 `~/.wechat/state.json` 记录了「哪些文章已推过」。删除文章后这条记录会残留(无害)。
只有当你以后**又新建一篇同名文章、希望它被当作全新文章推送**时,才需要清理。清理方法(在 VM 上):

```bash
cd ~/q-in-renjian
.venv/bin/python3 - <<'PY'
import json, os
from pathlib import Path
sf = Path(os.path.expanduser("~/.wechat/state.json"))
state = json.loads(sf.read_text())
state.pop("要删除的文章标题", None)   # 改成实际标题(=目录名)
sf.write_text(json.dumps(state, ensure_ascii=False, indent=2))
print("已清理")
PY
```

---

## 微信推送脚本的常用选项(在 VM 上运行)

```bash
cd ~/q-in-renjian
.venv/bin/python3 scripts/wechat_push.py                 # 只推「新增/改动」的文章(默认,增量)
.venv/bin/python3 scripts/wechat_push.py --all           # 强制重推全部(会为每篇新建草稿)
.venv/bin/python3 scripts/wechat_push.py --only "标题"    # 只推指定标题这一篇
.venv/bin/python3 scripts/wechat_push.py --all --only "标题"   # 强制重推指定这一篇
.venv/bin/python3 scripts/wechat_push.py --mark-done "标题" ["标题2" ...]  # 标记为已处理(记录hash但不推送),自动化将跳过
```

`--mark-done` 用途:某篇你**已经手动在公众号处理过**,不想让自动化再重复建草稿,就用它把这篇"登记"上,之后自动化会跳过。

---

## 环境与密钥(参考)

**Mac(写作/发布端)**
- 已装 `hugo`(extended)、`git`、`gh`
- 本工程在 `~/repo/q-in-renjian`

**VM(固定公网 IP,负责调用微信)**
- 部署在 `~/q-in-renjian`,Python 虚拟环境 `.venv`(依赖见 `scripts/requirements.txt`)
- 密钥放 `~/.wechat/config.json`(**不进仓库**):`{"appid","appsecret","author"}`
- 运行时缓存:`~/.wechat/token.json`(access_token 缓存)、`~/.wechat/state.json`(推送状态)
- 该 VM 的公网 IP 已加入公众号「IP 白名单」

**GitHub 仓库 Secrets**(供 `wechat.yml` 使用)
- `VM_HOST` / `VM_USER` / `VM_SSH_KEY`(专用 CI 密钥,非个人密钥)

---

## 常见维护

- **换写作工具或换存放位置**:只改 `config.json` 的 `source_dir`。
- **手动只更新网站不推公众号**:本地 `hugo` 构建后单独处理,或临时禁用 `wechat.yml`。
- **重跑某个 Action**:GitHub 仓库 → Actions → 选中 workflow → Re-run,或对 `wechat.yml` 用 “Run workflow”(workflow_dispatch)手动触发。
- **默认封面太朴素**:在 VM 装 Pillow 可生成更精致的默认封面,或后续加「每篇指定封面」的约定。
