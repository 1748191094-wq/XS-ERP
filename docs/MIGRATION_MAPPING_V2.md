# 旧项目迁移映射与追加需求实施基线

更新时间：2026-07-19

## 1. 已检查的数据与素材

- 旧程序：`legacy_quote_app.py`
- 旧数据库：`quotation.db`，仅有 `quotations` 表，共 3 条历史报价
- 新数据库：`repair_management.db`，当前 25 个业务/系统表
- 旧 PDF：`records/` 下 4 份，其中 3 份登记在旧库，1 份为孤立历史 PDF
- 新 PDF：`output/pdf/`
- 旧配置：`config.json`，包含爵士蓝报价单标题、页脚、Logo 路径和 SMTP 配置；当前 Logo 路径为空
- 第三方程序：`../../第三方软件/` 共 607 个文件、约 1.01 GiB；本阶段仅完成文件清点，未运行任何 EXE、BAT、驱动或服务

## 2. 安全备份

实施前已经使用 SQLite 在线备份 API 备份新旧数据库。在线备份会读取活动 WAL 中的有效数据，不采用可能遗漏 WAL 的裸文件复制。

- 清单：`backups/pre-migration/backup-manifest-20260719T124728473487Z.json`
- 新库备份：`repair_management-20260719T124728473487Z.db`
- 旧库备份：`quotation-20260719T124728473487Z.db`
- 两份备份均通过 `PRAGMA integrity_check`，并记录 SHA-256

## 3. 旧字段到新结构的映射

| 旧字段 | 新结构 | 当前结果 | 后续处理 |
|---|---|---|---|
| `quote_id` | `quotes.quote_no` | 已迁移，原编号不变 | 继续作为历史报价唯一编号 |
| `customer_name` | `customers.name` | 已迁移 | 按电话优先合并客户 |
| `phone` | `customers.phone` | 已迁移 | 保持可空 |
| `customer_email` | `customers.email` | 已迁移 | 保留邮件入口 |
| `model` | `drone_devices.model` | 已迁移 | 品牌默认为 DJI |
| `sn` | `drone_devices.serial_number` | 已迁移 | “无/查询/未知”等生成临时序列号并标记临时设备 |
| `reason` | `repair_orders.fault_description` | 已迁移 | 作为历史工单主题/故障描述 |
| `remark` | `repair_orders.customer_notes` | 已迁移 | 后续区分内部备注和客户可见备注 |
| `status` | `repair_orders.status`、`quotes.status` | 已迁移 | 旧 `completed` 映射为已完成/已确认，其他为待报价/草稿 |
| `labor_price` | `quotes.labor_fee` | 已迁移 | 历史金额冻结，不从当前价格表回算 |
| `parts_total` | `quotes.subtotal` | 已迁移 | 历史金额冻结 |
| `grand_total` | `quotes.total_amount`、`repair_orders.total_quote_amount` | 已迁移 | 不自动生成收款记录 |
| `parts_json[].name` | `quote_items.item_name` | 已迁移 | 与库存项解耦，防止后续改名影响历史报价 |
| `parts_json[].qty` | `quote_items.quantity` | 已迁移 | 已按历史数量保存 |
| `parts_json[].price` | `quote_items.unit_price`、`amount` | 已迁移 | 允许负数抵扣项，历史价格已核对 |
| `pdf_path` | `attachments`，类型 `legacy_quote_pdf` | 已迁移 3 份 | 原 PDF 保留为不可变历史附件 |
| `engineer` | 暂无可靠用户映射 | 未迁移 | 建立成员账号后按姓名人工关联，避免误绑 |
| `labor_type` | 报价项目/报告说明 | 仅非零人工费时生成项目 | 后续增加报价快照元数据字段 |
| `pdf_title`、`pdf_footer` | PDF 品牌配置/报价快照 | 未结构化迁移 | 先恢复系统级默认样式；新增快照字段后补录 |
| `logo_path` | PDF 品牌配置/报价快照 | 当前旧库均为空 | 等正式 Logo 素材到位后配置 |
| `pay_url` | PDF 二维码/报价快照 | 未结构化迁移 | 使用环境变量或受控设置，生成时固化快照 |
| `created_at` | 各新表时间字段 | 当前迁移未完整保留 | 增加一次性补迁脚本，从旧记录回填可确认的创建时间 |

## 4. 历史价格核对

3 份旧报价均与新库一致：

- `SRV-20260706-A1235D`：项目 5400.00 与抵扣 -2000.00，合计 3400.00
- `SRV-20260706-A90E0C`：项目 50.00，合计 50.00
- `SRV-20260707-106744`：项目 430.00，合计 430.00

新 PDF 必须只读取 `quote_items.unit_price/amount` 和 `quotes` 中的冻结金额，不读取库存当前售价。

## 5. 已发现缺口

1. 新 PDF 的视觉层级明显弱于旧版，缺少统一页眉页脚、品牌区、备注卡片和二维码区域。
2. 旧版生成逻辑在替换旧文件前曾先删除目标文件；新实现必须坚持临时文件加 `os.replace`，生成失败不覆盖旧文件。
3. 报价单、检测报告、完成报告需要共用同一套主题、页眉页脚和可换页表格组件。
4. 孤立历史 PDF `SRV-20260706-95CD8F.pdf` 尚未能可靠关联客户或工单，应进入“待人工归档”，不能猜测关联。
5. 已通过 `0006` 至 `0008` 增加统一服务工单、处理组、协助成员、SLA、催办、备注可见性、高级专员升级、外呼、快照邮件和物流事件；旧维修工单已建立一对一服务工单映射。
6. 邮件配置保留原程序的界面入口；SMTP 授权码在 Windows 使用 DPAPI 本机加密且 API 不回显，部署环境也可改用环境变量。

## 6. 后续迁移原则

- 所有数据库结构变化使用 Alembic 版本迁移，不直接手改生产数据库。
- 所有可变业务信息（库存售价、客户资料、模板内容）在发送或生成时保存快照。
- 财务、库存出入库和退款操作必须有幂等键、操作人和时间线。
- 多机模式只有管理员主机可以直接访问 SQLite；成员端只能通过服务 API 操作。
- 企业微信、邮件、顺丰和外呼在无互联网时进入可靠待发送队列。
- 第三方标定/刷机程序只做静态审计和适配器封装；未完成签名、哈希、机型、电量、连接与并发保护前不得接入一键执行。
