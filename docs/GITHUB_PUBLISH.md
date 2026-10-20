# GitHub 仓库创建与首次发布

本文说明如何把本地整理好的工程发布到一个新的 GitHub 仓库。当前工程目录还没有 Git 历史；截图中的 `AnsoWang924/smart-medicine-box` 是旧版语音输入工程，不要把新工程推到它的 `main` 分支。

## 1. GitHub 新建仓库页面

建议在 GitHub 右上角 `+` → `New repository`，按下表填写：

| 页面字段 | 建议填写 |
| --- | --- |
| Owner | `AnsoWang924` |
| Repository name | `k1-smart-medicine-box` |
| Description | `基于进迭时空 K1 MUSE Pi Pro 的端侧 AI 智能药盒，覆盖药单 OCR、用药方案推理、服药核验、药物问答与 12 路药仓控制。` |
| Choose visibility | `Public` |
| Add README | 关闭。工程根目录已有 README.md |
| Add .gitignore | 选择 `No .gitignore`。工程根目录已有 .gitignore |
| Add license | 选择 `No license`，让新仓库保持空历史；在首次推送前，先把最终选定的 LICENSE 文件放入本地工程根目录 |

仓库名只能使用英文、数字、连字符等 GitHub 支持的 ASCII 字符。截图中自动生成的 `K1-MUSE-Pi-Pro-AI` 也是可用名称；`k1-smart-medicine-box` 更直接表达项目用途。仓库名创建后可以改，但远程地址也会随之改变。

点击 `Create repository` 后复制 GitHub 显示的 HTTPS 地址。下面示例使用：

```text
https://github.com/AnsoWang924/k1-smart-medicine-box.git
```

## 2. GitHub 首页展示文案

GitHub 的 `Description` 是短简介，使用上表文案即可；README 是完整项目说明，当前根目录 README 已包含作品名称、作品简介、硬件清单、主要目录、运行方式和新板复现指南。发布后可在仓库 `About` 区域添加 Topics：

```text
spacemit, k1, riscv, edge-ai, smart-medicine-box, ocr, computer-vision, medication-management
```

仓库首页会自动显示根目录的 `README.md`。完整作品简介和详细硬件型号应放在 README；简介栏不要粘贴整段长介绍。

## 3. Windows 本地首次提交和推送

在 PowerShell 中进入工程根目录。该目录本身就是仓库根目录，不要再新建一层同名目录，也不要用 GitHub 网页的 `Upload files` 代替首次 Git 推送；本工程含有应由 Git LFS 管理的大模型和音频文件。

```powershell
Set-Location -LiteralPath 'D:\智能药盒项目开源\K1智能用药平台_开源整理'
git lfs install
git init -b main
git status --short
git check-attr filter -- models/llm/Qwen3-0.6B-Q4_0.gguf
```

`git check-attr` 应显示该 GGUF 文件的 `filter: lfs`。`.gitattributes` 已为 `.onnx`、`.gguf` 和 `.wav` 配置 Git LFS。如果结果不是 `lfs`，先停止，不要提交大文件；检查 Git LFS 是否安装以及 `.gitattributes` 是否存在。

确认 `git status --short` 没有 `.env`、日志、录音、人脸数据、个人资料或其他本机私有文件。再运行静态预检：

```powershell
python deployment/preflight.py
```

预检通过后，将最终选定的项目许可证文件 `LICENSE` 放在仓库根目录，再提交和推送：

```powershell
git add .
git status --short
git commit -m "Initial release of K1 smart medicine box"
git lfs ls-files
git remote add origin https://github.com/AnsoWang924/k1-smart-medicine-box.git
git push -u origin main
```

如果还没有配置 Git 提交者身份，Git 会提示设置 `user.name` 和 `user.email`。推送时按 Git Credential Manager 的浏览器登录提示授权；不要把 GitHub 密码或 Personal Access Token 写进远程 URL、脚本或提交记录。上面的命令供你执行，本指南编写期间没有创建远程仓库或推送代码。

## 4. 推送后检查

在 GitHub 页面确认以下内容：

- 首页显示项目 README 的中文标题、简介和硬件清单。
- `backend`、`frontend`、`models`、`data`、`deployment`、`docs` 等目录位于仓库根目录，而不是被多包了一层文件夹。
- 页面中存在 `LICENSE`，并且许可证是你为本项目实际选定的版本。
- Git LFS 管理的模型文件在克隆后不是几 KB 的指针文件。

可以在本机另一个临时目录做一次干净克隆：

```powershell
Set-Location $env:TEMP
git clone https://github.com/AnsoWang924/k1-smart-medicine-box.git k1-smart-medicine-box-check
Set-Location .\k1-smart-medicine-box-check
git lfs pull
python deployment/preflight.py
```

这验证了 GitHub 源码和 LFS 文件可下载，以及静态文件闭包正确；它不等于在新 K1 上完成硬件和全功能验收。新板从系统准备、板端依赖到服务启动的步骤见 [新板复现指南](NEW_BOARD_SETUP.md)。该指南标出了需要按具体 Bianbu 镜像和 BSP 核实的厂商组件，当前尚无干净新板的端到端复现记录。

## 5. 许可证说明

本地候选目前没有根目录 `LICENSE`。旧仓库中的 MIT 许可证不能自动决定新项目的许可证，因此在首次推送前必须把团队选定的许可证文件加入本地工程。公开模型权重按项目负责人的决定不作为此发布流程的阻塞项；项目级许可证仍需说明原始代码的授权范围，且不能覆盖第三方组件各自的许可证和通知。
