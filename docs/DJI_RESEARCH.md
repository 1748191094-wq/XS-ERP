# DJI 云台标定与飞行日志接入研究

更新时间：2026-07-16

## 已确认的官方能力

### 云台标定

- DJI Mobile SDK 4 提供 `Gimbal.startCalibration`。官方要求设备静止、水平，带可调载荷的云台需先安装并配平载荷。
- DJI Mobile SDK 5 提供 `KeyGimbalCalibrate` / `KeyGimbalCalibrationStatus` 一类 Key 能力，但实际可用性仍受 SDK 产品支持列表、机型、固件和 Android 连接链路限制。
- DJI 官方消费者维修指引把常规标定入口放在 DJI Fly 或 DJI GO 4 的“云台自动标定”中。
- Payload SDK 的 `StartCalibrate` 是给开发者自研 PSDK 云台负载实现的回调，不是消费级 DJI 原装云台的通用维修接口。

因此，本项目当前提供“标定能力说明、前置条件、官方工具结果留痕、复测记录”。桌面服务不会直接发送未知私有命令。若要自动化，正确路径是另建 Android Mobile SDK 桥接应用，先确认目标机型受支持，再逐个机型与固件做真机回归。

官方资料：

- [DJI Mobile SDK 4 Gimbal API](https://developer.dji.com/api-reference/android-api/Components/Gimbal/DJIGimbal.html)
- [DJI Mobile SDK 5 GimbalKey](https://developer.dji.com/api-reference-v5/android-api/Components/IKeyManager/Key_Gimbal_GimbalKey.html)
- [DJI 官方云台标定维修指引](https://repair.dji.com/help/content?customId=01700006819&lang=en&paperDocType=ARTICLE&re=US&spaceId=17)
- [DJI Payload SDK 云台功能](https://developer.dji.com/doc/payload-sdk-tutorial/en/function-overview/basic-function/gimbal-function.html)

## DJI 日志格式结论

DJI 的 `.txt` 文件不能一概当作普通文本：

1. 第三方工具导出的 CSV/表格文本可以直接读取并进入规则分析。
2. DJI 二进制 Flight Record TXT 需要对应版本的解析能力。DJI 官方开源 `FlightRecordParsingLib` 只声明支持 Flight Record v13，需要 DJI Developer App Key，并通过 DJI 服务取得解密所需信息。
3. DJI `.DAT` 是另一类机载/飞控日志，存在明显的机型和固件差异；官方 FlightRecordParsingLib 并不等于 DAT 解析器。

项目现在会先识别“结构化文本”和“二进制 Flight Record”，不会再把所有 `.txt` 强行当 CSV。官方 v13 解析器采用外部适配方式：只有配置本机已编译、已授权并验证的包装程序后才启用。DAT 继续安全保留原件并明确标记不支持，不以逆向工具结果直接生成维修结论。

官方日志库：

- [DJI FlightRecordParsingLib](https://github.com/dji-sdk/FlightRecordParsingLib)

## 其他飞控日志

- PX4 `.ulg`：系统已经提供 `pyulog` 可选适配器；未安装依赖时明确提示，不伪造结果。
- ArduPilot `.bin`：系统已经提供 `pymavlink` 可选适配器；未安装依赖时明确提示。

官方项目：

- [PX4 pyulog](https://github.com/PX4/pyulog)
- [ArduPilot pymavlink](https://github.com/ArduPilot/pymavlink)

## 风险隔离

- 不内置或提交 DJI App Key。
- 不把逆向协议能力标为生产可用。
- 不将“解析成功”等同于“诊断结论成立”；规则结果仍必须由维修人员确认。
- 日志解析器有超时、输出体积和采样数量上限。
- 自动标定桥接在没有真机、支持列表和固件回归前保持关闭。

## 下一步验证清单

1. 收集脱敏的真实 DJI Flight Record TXT 样本，并记录生成它的 App、版本、机型和固件。
2. 申请专用 DJI Developer App Key，在隔离测试机编译官方 v13 库与 JSON 包装程序。
3. 对比官方应用展示值与解析输出，建立字段映射和固定回归样本。
4. 明确门店优先支持的 2–3 个 DJI 机型，再决定是否开发 Android MSDK 标定桥接。
5. 标定桥接必须回传开始结果、进度、最终状态和设备/固件信息；超时不得自动重试标定命令。
