from app.integrations.dji_sn_query.parser import normalize_device_response, parse_device_page_text


def test_normalizes_nested_device_response() -> None:
    payload = {
        "code": 0,
        "data": {
            "device": {
                "productName": "DJI 测试设备",
                "productCode": "TEST-001",
                "activationTime": 1_700_000_000_000,
                "warrantyStatus": "有效",
                "warrantyEndDate": "2027-01-01",
                "repairCount": 2,
                "flyAwayCount": 0,
                "djiCareStatus": "未购买",
                "remainingReplacementCount": 1,
            }
        },
    }

    result = normalize_device_response("abc123", payload)

    assert result.serial_number == "ABC123"
    assert result.product_name == "DJI 测试设备"
    assert result.product_model == "TEST-001"
    assert result.activation_date.startswith("2023-")
    assert result.repair_count == "2"
    assert result.flyaway_count == "0"
    assert result.care_replacement_remaining == "1"
    assert result.status == "查询成功"


def test_does_not_report_unknown_payload_as_success() -> None:
    result = normalize_device_response("ABC123", {"code": 400, "message": "序列号错误"})

    assert result.status == "未识别结果"
    assert result.message == "序列号错误"


def test_parses_real_device_detail_labels() -> None:
    page_text = """
    设备信息查询
    Osmo Action 4
    序列号： 6S6XN2E00A4AVX
    激活时间：2025-10-07
    查询其他设备
    保修期
    状态
    在保
    预计截止日期
    2026/10/08
    DJI Care 随心换
    服务有效期
    2025/10/07 - 2026/10/06
    剩余置换次数
    2
    状态
    正常
    售后服务记录
    维修或服务记录
    无
    飞丢申报记录
    无
    """

    result = parse_device_page_text("6s6xn2e00a4avx", page_text)

    assert result.product_name == "Osmo Action 4"
    assert result.activation_date == "2025-10-07"
    assert result.warranty_status == "在保"
    assert result.warranty_end_date == "2026/10/08"
    assert result.care_status == "正常（2025/10/07 - 2026/10/06）"
    assert result.care_replacement_remaining == "2"
    assert result.repair_count == "无"
    assert result.flyaway_count == "无"
    assert result.status == "查询成功"
