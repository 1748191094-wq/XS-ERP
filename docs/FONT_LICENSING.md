# 报价 PDF 与邮件字体商用审计

审计日期：2026-07-19

## 当前实现

- 管理端网页使用系统字体栈：`-apple-system`、`Segoe UI`、`Microsoft YaHei` 等。系统只声明字体名称，不复制或分发字体文件。
- HTML 邮件使用 `Arial`、`Microsoft YaHei` 等收件端字体，不把字体文件嵌入邮件。
- PDF 默认优先读取本机 Windows 字体 `C:\Windows\Fonts\Deng.ttf`，并通过 ReportLab 嵌入 PDF；本机该文件的 OpenType `fsType` 为 `8`，即 Editable Embedding。
- 项目目录当前没有自带 `.ttf`、`.otf`、`.ttc`、`.woff` 或 `.woff2` 字体文件。
- PDF 找不到本机字体时会回退到 ReportLab 的 `STSong-Light` CID 字体。该方式可移植性和中文显示稳定性不足，不建议作为正式交付标准。

## 商用判断

1. 网页和邮件仅引用用户设备已有字体，通常不构成字体文件再分发。
2. Windows 字体可以用于屏幕显示和商业打印输出，但不能把字体文件从 Windows 复制进安装包、服务器或网页字体目录。
3. 文档嵌入必须遵守字体内部的 OpenType/TrueType 嵌入标志。当前 `Deng.ttf` 的 `fsType=8` 允许可编辑文档嵌入，但这一结论只适用于从已授权 Windows 主机生成文档，不等于允许随软件分发 `Deng.ttf`。
4. 正式跨平台部署建议改用明确采用 SIL Open Font License 1.1 的 Noto Sans CJK / Noto Sans SC，并在安装包中保留原始 LICENSE 文件。OFL 允许字体与商业软件捆绑和嵌入，但字体本身不得单独出售，修改版还需遵守保留字体名等条件。

## 推荐落地方案

- 当前门店 Windows 主机：可以继续使用本机 `Deng.ttf` 生成客户 PDF，但不得把该字体复制进项目或发给成员端。
- 正式发布安装包：加入经过来源校验的 Noto Sans CJK 字体及其 LICENSE，并通过 `PDF_FONT_PATH` 指向该文件。
- 每次替换字体时记录文件来源、版本、SHA-256、许可证文本和 `fsType`。
- 邮件继续使用系统字体栈，不使用来源不明的网页字体，不把商业字体转换为 WOFF/WOFF2 后自托管。

## 官方依据

- Microsoft Font redistribution FAQ: https://learn.microsoft.com/en-us/typography/fonts/font-faq
- Microsoft DengXian font family: https://learn.microsoft.com/en-us/typography/font-list/dengxian
- Noto CJK SIL OFL 1.1 license: https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE

本文件是工程合规审计记录，不替代律师针对具体发行地区、合同和字体来源作出的法律意见。
