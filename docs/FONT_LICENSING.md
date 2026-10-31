# PDF 与邮件字体来源记录

更新日期：2026-10-26

## 当前实现

- 网页和 HTML 邮件继续使用设备系统字体栈，不附带网页字体或邮件字体文件。
- PDF 优先采用用户配置的 `PDF_FONT_PATH`；未配置或无法加载时，采用项目附带的文泉驿微米黑。ReportLab 将本次文档使用的字体子集嵌入 PDF，阅读端无需安装中文字体。
- 附带字体位于 `app/reports/fonts/wqy-microhei.ttc`，两种 Windows PyInstaller 配置均包含整个 fonts 目录及许可证。通过 `RESOURCE_DIR` 查找，兼容源码运行、文件夹版和单文件解包路径。
- 保留原有系统字体发现及最后的 STSong-Light CID 回退以兼容旧安装；回退缺乏内嵌字形，不能作为中文视觉验收依据。字体加载失败和最终 CID 回退均输出日志。
- 自定义 `PDF_FONT_PATH` 仍由部署方确认字形覆盖、格式和嵌入许可。项目不复制或分发 Windows / macOS 系统字体。

## 附带字体

- 名称：WenQuanYi Micro Hei（文泉驿微米黑），0.2.0-beta。
- 来源：Debian `fonts-wqy-microhei` 0.2.0-beta-3.1 包；仅提取原始 TTC 字体，不安装或执行包脚本，不修改字体文件。
- 下载：<https://deb.debian.org/debian/pool/main/f/fonts-wqy-microhei/fonts-wqy-microhei_0.2.0-beta-3.1_all.deb>
- 上游来源：<https://downloads.sourceforge.net/project/wqy/wqy-microhei/0.2.0-beta/wqy-microhei-0.2.0-beta.tar.gz>
- TTC SHA-256：`2420e8078af796b19a3f6ef13de527a1a91c1e7171eea115926c614ced1009b3`
- 第一字体面的 OpenType `fsType`：8；ReportLab 使用第一字体面。
- 上游声明 Apache-2.0 或 GPL-3+ with Font exception 双许可；项目选择 Apache-2.0 使用和分发此未修改字体。完整 Apache 2.0 文本、来源包版权记录、上游说明保留于同目录的 `LICENSE-Apache-2.0.txt`、`COPYRIGHT.txt`、`UPSTREAM-README.txt`。

## 验证及限制

在没有系统中文字体的 Linux 环境中，默认字体的简体中文及常用测量符号有字形，生成 PDF 含嵌入字体流；PDFium 实际渲染检查中文、英文、数字、长文本、多页表格及证据图。此检查不代表所有 Unicode 字符均有覆盖，也不代表 Windows 安装包已经在 Windows 主机完成运行验收。

更换字体时重新记录来源、版本、校验值、许可证和嵌入标志，并重新渲染所有代表性样张。不得以文件生成成功或文本提取成功替代视觉检查。
