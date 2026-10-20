# 进迭时空开源案例结构调研

调研日期：2026-10-19

## 核验范围

进迭时空社区案例页为 <https://www.spacemit.com/community>。官网同域项目接口 `https://www.spacemit.com/api-server/hub/getProjects?page=0&size=6` 返回 6 个项目；六个详情页 Markdown、六个 GitHub 仓库及递归文件树均可访问。官网是动态 SPA，本次根据官网接口、路由包、Markdown 内容和 GitHub REST API 核对，不将搜索摘要当作来源。

## 官网案例

| 案例 | 官网提供的内容 | 源码仓库与目录观察 |
| --- | --- | --- |
| [AGV 小车语音交互与智能跟随](https://www.spacemit.com/community/eco-software/projectDetail/0c90a7c8-e878-4e15-831e-d3e7f2ee835e) | 硬件清单、接线图、ROS 2 框架和控制流程、雷达与 ROS 包、udev、Python 依赖、底盘/跟随节点启动和交互说明 | [riscv-agv-voice-follow](https://github.com/spacemit-dev/riscv-agv-voice-follow)：6 项，仅双语 README 和图片，没有完整运行源码 |
| [LeRobot 端到端机械臂演示](https://www.spacemit.com/community/eco-software/projectDetail/5f86e7c6-7206-48e5-8d3a-45c2a5611a2f) | 物料、环境安装、机械臂标定、遥控、数据采集、ACT 训练部署、SmolVLA 微调/分布式部署和 FAQ | [lerobot](https://github.com/spacemit-dev/lerobot)：9 项，双语 README 和图片 |
| [疲劳感知与分级预警](https://www.spacemit.com/community/eco-software/projectDetail/7168caff-1308-45f4-97a4-397a60d29ed7) | K1 MUSE Pi Pro、本地量化 YOLOv8n 和 PLC；文档覆盖进程划分、数据流、心跳故障安全、隐私、硬件和安装 | [MusePiPro_Driving_Detecte](https://github.com/TheHotSummery/MusePiPro_Driving_Detecte)：384 项，含前端、后端、硬件、PLC/C++、PCB 和资源目录 |
| [小车建图与自主导航](https://www.spacemit.com/community/eco-software/projectDetail/856a9dc8-e258-4a4f-97d6-db47ad8c1e62) | Bianbu OS 2.0、ROS 2 Humble、雷达、SLAM、规划避障；文档包括平台模块、依赖编译、K1/PC 构建和建图导航命令 | [riscv-agv-navigation](https://github.com/spacemit-dev/riscv-agv-navigation)：2 项，仅双语 README；实际 ROS 源码由 README 链接的归档包提供 |
| [点云缺陷检测自动初磨](https://www.spacemit.com/community/eco-software/projectDetail/896c291d-9036-475e-a484-e72308b920ed) | 安装、多 Python 环境、相机服务、C++ 点云、UI 操作、机械臂坐标/打磨步骤、硬件及预构建环境说明 | [AdaptiPolish-Sys](https://github.com/NAKANONAKANOAZUSA/AdaptiPolish-Sys)：778 项；有 `PointCloud/`、`Polish/`、`UI/`、`hardware/`、`vision/` 等模块，也混入 `.idea/`、`__pycache__/`、大量点云和构建产物 |
| [RISC-V 智慧零售](https://www.spacemit.com/community/eco-software/projectDetail/d3f347ee-cc57-41ba-84e0-6efb3b074f14) | Function Call 商品识别、机械臂、扫码、OCR；文档含硬件、依赖、Ollama 模型、音频设备、接线、入口脚本和模块说明 | [riscv-retail-ai-demo](https://github.com/spacemit-dev/riscv-retail-ai-demo)：105 项，含 `doc/`、`spacemit_audio/`、`spacemit_cv/`、`spacemit_llm/`、`spacemit_orc/`、`tools/elephant/`、`requirements.txt` 和 `LICENSE` |

官网案例页的说明范围普遍包括硬件/接线、环境依赖、模型或设备准备、运行入口及操作步骤。零售仓库 README 还细化了四类入口脚本和坐标偏移调参。它是当前智能用药平台最贴近的功能模块参考，但 README 中的克隆命令指向 Elephant Robotics 仓库，案例镜像本身又包含一套源码；其中还有 `/home/er/jobot-ai-elephant/...` 硬编码路径，因此不能将它的部署命令或路径原样照搬。

## GitHub 目录参考

六个官网案例没有共同的仓库模板。有的仓库只放说明和图片，有的把运行源码放在外部归档，也有多语言硬件工程和混入大量开发缓存/构建产物的研究项目。仓库数量和目录名本身不能证明其发布质量。

补充核对了 SpacemiT 相关仓库：

- [ai-support](https://github.com/spacemit-com/ai-support/tree/k1-bl-v2.2.y) 是 K1 C++ AI demos，按 `src/`、`include/`、`python/`、`demo/`、`tests/`、`ci/` 和 `debian/` 分区，并带 `LICENSE`、`README.md`、`VERSION_NUMBER`。
- [model-zoo-vision](https://github.com/spacemit-com/model-zoo-vision) 和 [model-zoo-asr](https://github.com/spacemit-com/model-zoo-asr) 按应用/模型、示例、源码、资源、文档与测试拆分；视觉库还记录 Bianbu、SpaceMIT ONNX Runtime、模型缓存、构建和安装检查。
- [ai-gateway](https://github.com/spacemit-com/ai-gateway) 展示配置、Debian/systemd 包装、前端、分层 Python 源码和集成/单元/WebSocket 测试的组织方式，但 README 的目标是 K3，且依赖内部 apt/PyPI 源，不是本项目可直接使用的 K1 安装方案。

GitHub 组织 `spacemit-com` 的资料链接到 `spacemit.com`，但 GitHub API 返回 `is_verified=false`；因此这里将它作为官网域名关联的 SpacemiT 组织候选，不把 GitHub 认证状态描述为已确认。六个案例本身则是由进迭时空官网案例页直接链接到对应仓库。

## 对本项目的落地

当前候选采用 `backend/`、`frontend/`、`models/`、`data/`、`deployment/`、`docs/`，后端按 gateway、qa、llm、voice、face、cabinet、scheduler 和识别任务拆分。这个布局适合本项目的多进程单仓库，比复制某个 ROS/C++ demo 的 `src/` 树更贴合已有代码边界。

继续按案例中成熟的发布做法补齐硬件/依赖/启动说明、独立许可证与第三方来源清单、干净安装流程和测试目录。候选目前已经有硬件/依赖文档、systemd 模板和静态预检；许可证、第三方素材权属、完整 K1 安装复现及外设烟测仍是发布前工作。开发缓存、IDE 状态、训练数据和未被运行入口引用的试验脚本不应随运行时闭包进入发布包。
