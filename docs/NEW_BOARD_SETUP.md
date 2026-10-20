# K1 MUSE Pi Pro 新板复现指南

本指南按“新板系统准备、GitHub 克隆、依赖确认、服务启动、外设验证”的顺序操作。当前已验证的是候选目录的静态文件闭包和代码检查；**没有在干净 K1 上从零安装并完成实体端到端复现**。K1 BSP、厂商推理运行时、相机配置和部分语音运行时由 Bianbu 系统提供，不能靠 Git 克隆替代。

## 0. 确认仓库

截图中的 `AnsoWang924/smart-medicine-box` 当前是公开仓库，但其 `main` 分支仍是旧的 K1 语音输入项目，不是本目录的智能药盒平台。为保留旧项目及其历史，建议在 GitHub 新建一个空仓库（例如 `k1-smart-medicine-box`），不要勾选自动生成 README、许可证或 `.gitignore`。等本项目推送完成后，用新仓库地址替换下方 `<OWNER>/<REPO>`。

仓库创建页面填写建议、README/简介文案和本地首次推送步骤见 [GitHub 发布指南](GITHUB_PUBLISH.md)。

GitHub 新建仓库流程：登录 GitHub → 右上角 `+` → `New repository` → 输入仓库名 → 选 `Public` → 不初始化 README / License / `.gitignore` → `Create repository`。创建后复制 HTTPS 地址，例如 `https://github.com/<OWNER>/<REPO>.git`。

本地首次推送前，必须检查 `git status`、`.gitignore` 和 LFS 清单，确认没有 `.env`、个人录音、人脸向量、日志或本机数据被加入。不要对现有仓库执行强制推送。

## 1. 准备全新 K1

1. 使用面向 K1 MUSE Pi Pro 的官方 Bianbu 镜像完成首次启动，建立可登录的普通用户并接入网络。
2. 如需 K1 本机全屏界面，安装并启动图形桌面，确认触摸显示屏和用户级 systemd 会话正常。只通过 SSH 使用 API 时可以暂不配置 kiosk。
3. 确认系统架构和服务管理器：

```bash
uname -m
cat /etc/os-release
python3 --version
systemctl --user is-system-running
```

期望架构为 `riscv64`。本项目基于开发时的 Bianbu / K1 环境整理，不保证可直接运行在其他 RISC-V 板卡、普通 x86 Linux 或不同厂商 BSP 上。

4. 在 K1 的 SSH 服务和 GitHub 网络访问可用后，准备基础工具。不同 Bianbu 镜像的包名可能变化，先用发行版软件源查询并确认：

```bash
sudo apt update
sudo apt install -y git git-lfs python3 python3-pip python3-gi gir1.2-gstreamer-1.0 \
  gstreamer1.0-tools i2c-tools python3-smbus
git lfs install
```

若软件源找不到某个包，不要从不明来源下载，也不要用通用 pip 包覆盖厂商版 ONNX Runtime / OpenCV；按该 K1 镜像对应的官方软件源和 BSP 文档安装。

## 2. 克隆项目并取回模型

以下命令仅在本项目已推送到新的 GitHub 仓库后生效。当前截图所示旧仓库尚不包含本目录。

```bash
git clone https://github.com/<OWNER>/<REPO>.git
cd <REPO>
git lfs pull
git lfs ls-files
```

本项目约 755 MB 的 ONNX、GGUF 和 WAV 文件使用 Git LFS。克隆完成后，先检查 LFS 文件已下载为真实模型/音频，而不是几 KB 的 LFS pointer，再运行预检：

```bash
python3 deployment/preflight.py
```

预检检查必需文件、Python/JSON/YAML 语法、Qwen 模型大小与 SHA-256、语音资源、安装模板和是否包含人脸运行数据。它不测试 K1 摄像头、外设、推理性能或药柜电气安全。

## 3. 安装板端运行依赖

下表是当前代码明确依赖的系统能力。平台组件需要从匹配本机 Bianbu 镜像的进迭时空官方软件源 / BSP 安装；版本和安装源应按板端系统实际情况核对。

| 功能 | K1 上需要的组件 |
| --- | --- |
| 服务与模型 | Python 3、用户级 systemd、SpaceMIT `llama-server` |
| OCR / YOLO / 人脸 | SpaceMIT 版 ONNX Runtime 和执行提供程序、NumPy、OpenCV、Pillow |
| 相机 | K1 BSP 传感器驱动、`spacemitsrc` GStreamer 插件、GStreamer Python 绑定、匹配传感器的 camera JSON |
| 药单 OCR | 上述视觉依赖、PyYAML、pyclipper、仓库中的 OCR 配置和模型 |
| USB 音频 | ALSA、PipeWire、WirePlumber、USB 音频角色管理器 |
| 语音输入 | K1 的 SpaceMIT ASR 运行环境、SenseVoice 系统组件、USB 麦克风 |
| 全屏界面 | 图形桌面/Wayland 会话、Chromium 兼容浏览器 |
| 药柜 | I2C 已启用、`i2c-tools`、`python3-smbus` 或 `smbus2`、团队 12 路锁控 PCB |

`docs/DEPENDENCIES.md` 记录了开发板观察到的版本，但不是一份已在干净镜像验证的完整锁文件。尤其 `onnxruntime==1.20.1+spacemit` / `spacemit-ort==2.0.0+beta2` 属于 K1 专用构建，不保证能从公共 PyPI 安装。不要直接执行不加判断的 `pip install -r ...`，避免覆盖板端厂商运行时。

当前候选没有自动下载或安装 Bianbu/K1 BSP、SpaceMIT llama.cpp/ORT/ASR/TTS、相机 JSON、GStreamer 厂商插件的脚本；这些系统组件的官方安装源与版本矩阵仍需按实际目标镜像完成确认。没有这些组件时，仓库本身无法让全功能从零启动。

## 4. 对照外设配置

### 摄像头

- 药盒/药单 OCR 和药品 YOLO 共用 OV5647 采集服务，默认使用 `/usr/share/camera_json/csi1_camera_auto.json`。
- 人脸识别独立使用 IMX219，默认使用 `/usr/share/camera_json/csi3_camera_auto.json`。
- 确认两个文件都由已安装的 K1 BSP 提供，型号和 CSI 接口对应，且 `spacemitsrc` 能实际取帧。Linux 识别到相机节点或文件存在不代表传感器已正常成像。

```bash
ls -l /usr/share/camera_json/csi1_camera_auto.json /usr/share/camera_json/csi3_camera_auto.json
gst-inspect-1.0 spacemitsrc
```

### USB 麦克风和扬声器

检查 ALSA 设备：

```bash
arecord -l
aplay -l
```

在 `backend/audio/audio_roles.json` 中把开发时记录的麦克风 / 扬声器 USB VID:PID 和 PipeWire sink 名改成新设备实际值。接入后检查 `systemctl --user status smart-medicine-usb-audio.service` 及 `%t/smart-medicine-audio-roles.json`。ASR 还依赖第 3 节的系统运行时，USB 麦克风被识别本身不能证明语音识别已就绪。

### 12 路药柜

硬件代码默认 K1 I2C bus 3、MCP23017 输出地址 `0x20`、反馈地址 `0x21`。先对照团队 PCB 原理图、K1 40-pin 接口定义、电平和供电；I2C 信号为 3.3 V，不能把 12 V 接到 K1 信号脚。只在锁控板供电和接线确认后扫描：

```bash
sudo i2cdetect -y 3
python3 backend/cabinet/cabinet_controller.py --bus 3 --json status
```

应能读到正确总线与两颗 MCP23017 的地址，再逐路验证门磁反馈和短脉冲开锁。切勿在接线未确认时运行全柜演示命令。

## 5. 配置 USB 角色并安装服务

先编辑 `backend/audio/audio_roles.json` 为自己的麦克风和扬声器 VID:PID / sink 名。随后安装核心服务：

```bash
bash deployment/install-user-services.sh
systemctl --user --no-pager --full status \
  smart-medicine-llm smart-medicine-qa smart-medicine-usb-audio smart-medicine-gateway
bash deployment/health-check.sh
```

安装器会把 `PROJECT_ROOT` 写到当前用户的配置中，并启动 LLM、QA、USB 音频角色管理器和 API 网关。运行期间不要移动项目目录；若改了克隆路径，重新运行安装器。ASR 和 kiosk 单元只安装模板，不由默认安装器启用。

核心 HTTP 检查通过后，在 K1 本机浏览器打开 `http://127.0.0.1:8799/ui/`。通过 SSH 从电脑访问可使用本地转发：

```bash
ssh -L 8799:127.0.0.1:8799 <K1用户名>@<K1地址>
```

然后在电脑浏览器访问 `http://127.0.0.1:8799/ui/`。网关默认只绑定回环地址，不直接暴露到局域网。

如要开机自动全屏，在 K1 图形桌面用户会话中配置浏览器路径并启用：

```bash
mkdir -p ~/.config/smart-medicine-k1
cp deployment/kiosk.env.example ~/.config/smart-medicine-k1/kiosk.env
# 按实际情况编辑 KIOSK_BROWSER
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR
systemctl --user enable --now smart-medicine-kiosk.service
```

### ASR 可选启用

仅在 K1 提供以下系统路径且 USB 麦克风角色已验证后启用；ASR 需要使用系统预装的专用 Python，而非默认 `/usr/bin/python3`：

```bash
test -x /usr/share/asr-llm-tts/.voice2code/bin/python
test -d /opt/spacemit-asr
systemctl --user enable --now smart-medicine-asr.service
```

### 新成员首次使用

仓库不含任何人的人脸特征。系统启动后先在新增成员页面建立成员和药品资料，再通过 UI 录入该成员的人脸；不要把人脸目录提交到 Git。用户确认药单 OCR 草稿后才会将候选药品写入成员表单。完成药柜、相机、显示和音频的逐项验收后，才可宣称整机流程复现成功。

## 6. 验证和排障

先验证服务，再逐项验证能力，不要把基础 HTTP 200 当成全功能就绪：

```bash
systemctl --user --failed
bash deployment/health-check.sh
curl -fsS http://127.0.0.1:8799/api/v2/health/live
curl -fsS http://127.0.0.1:8799/api/v2/health/ready
journalctl --user -u smart-medicine-gateway -n 100 --no-pager
```

`/api/v2/health/ready` 汇报 OCR、YOLO、人脸、OV5647 和启动预热模块状态；若 ASR 系统运行时未提供，语音输入不会就绪。验证时分别实测药品盒识别、药单 OCR、成员人脸登记与核验、12 路柜门开关和闭合反馈、语音输入/播报、触摸屏全屏显示。每项记录板卡镜像版本、相机 JSON、外设型号和服务日志，才能形成可复现的硬件验证记录。
