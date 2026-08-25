import re
from pathlib import Path


WORKFLOW_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "workflow.js"
APP_JS = WORKFLOW_JS.parent / "app.js"
INDEX_HTML = WORKFLOW_JS.parent / "index.html"


def workflow_source() -> str:
    return WORKFLOW_JS.read_text(encoding="utf-8")


def test_long_owner_group_and_ticket_selects_are_searchable_without_mutating_contract():
    source = workflow_source()

    for field_name in (
        "engineer_id",
        "current_owner_id",
        "owner_id",
        "assigned_to",
        "assigned_specialist_id",
        "processing_group_id",
        "specialist_group_id",
        "member_id",
        "user_id",
        "service_ticket_id",
    ):
        assert f"'{field_name}'" in source

    assert "workflowBaseSelectField = selectField" in source
    assert "selectField = function" in source
    assert "workflowBaseOpenModal = openModal" in source
    assert "openModal = function" in source
    assert "const completeOptions = [...select.options]" in source
    assert "select.replaceChildren(fragment)" in source
    assert "option.value === ''" in source
    assert "option.value === selectedValue" in source
    assert "option.hidden" not in source


def test_existing_transfer_search_is_not_duplicated_and_service_groups_are_filterable():
    source = workflow_source()

    assert "select.name === 'current_owner_id' && root.elements?.member_search" in source
    assert "field('service_group_search', '检索服务组'" in source
    assert "data-user-group-row" in source
    assert "row.textContent.toLocaleLowerCase().includes(query)" in source


def test_service_ticket_page_uses_debounced_backend_search_and_keeps_actions():
    source = workflow_source()

    assert "render.tickets = async ()" in source
    assert 'id="service-ticket-q"' in source
    assert "params.set('q', requestedSearch)" in source
    assert "setTimeout(loadServiceTicketResults, 180)" in source
    assert "newProcessingGroup()" in source
    assert "newServiceTicket()" in source
    assert "deleteAdminResource('service_ticket'" in source

    app_source = (WORKFLOW_JS.parent / "app.js").read_text(encoding="utf-8")
    assert "ticket=detail.service_ticket" in app_source
    assert "if(!cache.tickets.length)cache.tickets=await api('/api/service-tickets')" not in app_source


def test_call_quick_service_records_link_tickets_and_show_serial_numbers():
    source = workflow_source()

    assert "recordRef('service_ticket', x.id, x.ticket_no)" in source
    assert "recordRef('repair_order', x.repair_order_id, x.repair_order_no)" in source
    assert "SN：${esc(x.serial_number)}" in source


def test_call_quick_includes_planned_result_registration_and_reuses_completion_flow():
    source = workflow_source()
    call_quick_renderer = source[
        source.index("render.call_quick = async ()") : source.index("function openCallQuick")
    ]

    assert "callResultRegistrationHtml(plannedCalls)" in call_quick_renderer
    assert "/api/outbound-calls?status=planned" in call_quick_renderer
    assert "function callResultRegistrationHtml(calls)" in source
    assert 'id="call-result-title">结果登记' in source
    assert "call-result-panel" in source
    assert "completeOutboundCall(" in source
    assert "Number(call.assigned_to) === Number(currentUser.id)" in source


def test_followup_list_exposes_admin_soft_delete_through_shared_trash_flow():
    source = workflow_source()

    assert "deleteAdminResource('follow_up_task'" in source
    assert "/api/follow-ups/${item.id}" in source


def test_call_operator_navigation_has_no_fallback_to_forbidden_pages():
    app_source = APP_JS.read_text(encoding="utf-8")

    assert "const allowed=new Set(['call_quick','customers','devices','orders','tickets','calls','staff_search'])" in app_source
    assert "function pageMeta(id){const page=pages.find" in app_source
    assert "pages.find(x=>x[0]===id)||allPages.find" not in app_source
    assert "if(currentUser.role==='call_operator')current='call_quick'" in app_source


def test_quote_forms_collect_optional_payment_url_and_select_service_quote_template():
    source = APP_JS.read_text(encoding="utf-8")

    assert "function paymentUrlField(value='')" in source
    assert 'name="payment_url" type="url"' in source
    assert "付款链接（选填）" in source
    assert "+paymentUrlField(base?.payment_url||'')" in source
    assert "${paymentUrlField()}" in source
    assert "o.payment_url=(o.payment_url||'').trim()" in source
    assert "payload.payment_url=(payload.payment_url||'').trim()" in source

    assert "const quoteEmailTemplateTypes=new Set(['quote','retail_quote','replacement_quote'])" in source
    assert "stableEmailTemplateLabels={retail_quote:'服务报价通知',replacement_quote:'置换服务报价通知'}" in source
    assert "return ticketType==='replacement'?'replacement_quote':'retail_quote'" in source
    assert "form.elements.template_type.value=quoteEmailTemplateFor(quote)" in source
    assert "form.elements.template_type.value=quoteEmailTemplateFor(selected)" in source
    assert "form.elements.repair_order_id.value=selected.repair_order_id?String(selected.repair_order_id):''" in source
    assert "form.elements.service_ticket_id.value=selected.service_ticket_id?String(selected.service_ticket_id):''" in source
    assert "quoteEmailTemplateTypes.has(request.template_type)&&!request.quote_id" in source


def test_replacement_ticket_ui_supports_create_display_and_workbench_editing():
    app_source = APP_JS.read_text(encoding="utf-8")
    workflow = workflow_source()
    styles = (APP_JS.parent / "styles.css").read_text(encoding="utf-8")

    assert "replacement:'置换业务'" in app_source
    assert "const replacementTicketFieldNames=['replacement_inspection_result','trade_in_credit','return_reference','outbound_to_customer_tracking_no']" in app_source
    assert "function replacementTicketFields(visible=false)" in app_source
    assert "+replacementTicketFields()" in app_source
    assert "for(const key of replacementTicketFieldNames)delete o[key]" in app_source
    assert "if(o.ticket_type==='replacement')Object.assign(o,replacementTicketPayload(f))" in app_source
    assert 'max="9999999999.99"' in app_source
    assert "const visible=typeSelect.value==='replacement'" in app_source

    assert "function replacementTicketPanelHtml(ticket)" in app_source
    assert "if(ticket.ticket_type==='replacement'){" in app_source
    assert "summary?.insertAdjacentHTML('afterend',replacementTicketPanelHtml(ticket))" in app_source
    assert "insertAdjacentHTML('afterend',replacementQuotePanelHtml(ticket,detail.quotes))" in app_source
    assert "async function editReplacementTicket(id)" in app_source
    assert "openModal('编辑置换业务资料',replacementTicketFields(true)" in app_source
    assert "fillReplacementTicketForm($('#modal-form'),ticket)" in app_source
    assert "`/api/service-tickets/${id}/replacement`" in app_source

    assert "const serviceQuoteTickets=cache.tickets.filter(x=>['retail','replacement'].includes(x.ticket_type))" in app_source
    assert "const defaultDiscount=base?.discount??(preselectedTicket?.ticket_type==='replacement'?(preselectedTicket.trade_in_credit??0):0)" in app_source
    assert "discountLabel.textContent=isReplacement?'旧机抵折 / 优惠':'优惠'" in app_source
    assert "function replacementQuotePanelHtml(ticket,quotes=[])" in app_source
    assert "data-replacement-quote-panel" in app_source
    assert "data-replacement-quote-create" in app_source
    assert "data-replacement-quote-email" in app_source
    assert "replacement_quote:'置换服务报价通知'" in app_source

    assert "维修、零售、置换、咨询" in workflow
    assert ".replacement-ticket-fields" in styles
    assert ".replacement-workbench-panel" in styles
    assert ".replacement-quote-panel" in styles
    assert ".replacement-ticket-form-grid, .replacement-workbench-grid { grid-template-columns: 1fr; }" in styles


def test_email_template_library_groups_the_composer_and_limits_management_to_custom_templates():
    source = workflow_source()
    styles = (APP_JS.parent / "styles.css").read_text(encoding="utf-8")

    assert "const emailTemplateBaseSelectField = selectField" in source
    assert "if (name !== 'template_type') return emailTemplateBaseSelectField" in source
    assert "<optgroup label=\"${esc(category.label)}\">" in source
    assert "item.enabled !== false && !item.deleted" in source
    assert "emailTemplateCategories(items)" in source
    assert "a.order - b.order" in source

    assert "if (admin && !item.is_system)" in source
    assert "onclick=\"openEmailTemplateEditor()\"" in source
    assert "?include_disabled=true&include_deleted=true" in source
    assert "`/api/email/templates/${templateType}`" in source
    assert "`/api/email/templates/${templateType}/restore`" in source
    assert "method: 'DELETE'" in source
    assert "currentUser?.role !== 'admin'" in source

    assert ".email-template-library" in styles
    assert ".email-template-grid" in styles
    assert ".email-template-card.is-deleted" in styles
    assert ".email-template-grid { grid-template-columns: 1fr; }" in styles


def test_custom_email_template_editor_uses_controlled_placeholders_and_refreshes_the_library():
    source = workflow_source()

    assert "const placeholders = source?.allowed_placeholders || []" in source
    assert "data-email-template-target=\"active\"" in source
    assert "target.setRangeText(token, start, end, 'end')" in source
    assert "field('name'" in source
    assert "selectField('category'" in source
    assert "selectField('enabled'" in source
    assert "field('subject'" in source
    assert "field('body'" in source
    assert "values.enabled = values.enabled === 'true'" in source
    assert "const method = template ? 'PATCH' : 'POST'" in source

    assert "const emailTemplateBaseEmailsRenderer = render.emails" in source
    assert "await emailTemplateBaseEmailsRenderer()" in source
    assert "const items = await refreshEmailTemplateLibrary()" in source
    assert "cache.emailTemplates = emailTemplateLibraryItems.filter" in source
    assert "status?.insertAdjacentHTML('afterend', emailTemplateLibraryHtml(items))" in source


def test_static_assets_share_current_cache_token():
    index = INDEX_HTML.read_text(encoding="utf-8")
    tokens = re.findall(r'/static/(?:styles\.css|app\.js|workflow\.js)\?v=([^"\']+)', index)

    assert len(tokens) == 3
    assert len(set(tokens)) == 1
    assert tokens[0] == "20260824-p0-security"
