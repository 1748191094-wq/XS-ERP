/* Workflow extensions loaded after app.js. Reuses the established components and visual language. */
let orderSearchValue = '';
let orderStatusValue = '';
let financeSearchValue = '';
let callQuickPhone = '';
let ticketSearchValue = '';
const workflowReadOnlyRole = () => ['viewer', 'call_operator'].includes(currentUser?.role);

/*
 * Long owner/group selectors are enhanced here instead of changing app.js so the
 * workflow layer remains independently deployable. The native select stays in
 * place (and therefore keeps the existing FormData/API contract); the search
 * input only rebuilds its options from an immutable snapshot.
 */
const workflowBaseSelectField = selectField;
const workflowBaseOpenModal = openModal;
const workflowSearchableSelectNames = new Set([
  'engineer_id',
  'current_owner_id',
  'owner_id',
  'assigned_to',
  'assigned_specialist_id',
  'processing_group_id',
  'specialist_group_id',
  'member_id',
  'user_id',
  'service_ticket_id',
]);
const workflowPersonSelectNames = new Set([
  'engineer_id',
  'current_owner_id',
  'owner_id',
  'assigned_to',
  'assigned_specialist_id',
  'member_id',
  'user_id',
]);

function workflowSearchableOptions(name, options) {
  if (!workflowPersonSelectNames.has(name)) return options;
  return options.map(option => {
    const [value, originalLabel] = option;
    const member = cache.team.find(item => item.id === Number(value));
    if (!member) return option;
    const label = String(originalLabel ?? '');
    const details = [];
    if (member.employee_no && !label.includes(member.employee_no)) details.push(member.employee_no);
    const roleLabel = roleLabels[member.role] || member.role;
    if (roleLabel && !label.includes(roleLabel)) details.push(roleLabel);
    return details.length ? [value, `${label} · ${details.join(' · ')}`] : option;
  });
}

selectField = function (name, label, options, extra = '') {
  const searchable = workflowSearchableSelectNames.has(name)
    || /(负责人|成员|服务组|处理组|专员组|关联服务工单)/.test(String(label));
  const html = workflowBaseSelectField(
    name,
    label,
    searchable ? workflowSearchableOptions(name, options) : options,
    extra,
  );
  if (!searchable) return html;
  const selectId = formFieldId(name);
  const search = `<input id="${selectId}-search" type="search" autocomplete="off" data-workflow-select-search="${esc(name)}" aria-label="检索${esc(label)}" aria-controls="${selectId}" placeholder="输入关键词筛选${esc(label)}">`;
  return html.replace(
    `<select id="${selectId}"`,
    `${search}<select id="${selectId}" data-workflow-searchable-select`,
  );
};

function bindWorkflowSearchableSelects(root = $('#modal-form')) {
  if (!root) return;
  root.querySelectorAll('select[data-workflow-searchable-select]').forEach(select => {
    const input = root.querySelector(`[data-workflow-select-search="${CSS.escape(select.name)}"]`);
    if (!input) return;

    // The workflow transfer dialog already has a richer name/employee-number
    // search which also coordinates owner choices with the selected group.
    if (select.name === 'current_owner_id' && root.elements?.member_search) {
      input.remove();
      select.removeAttribute('data-workflow-searchable-select');
      return;
    }

    const completeOptions = [...select.options].map(option => ({
      value: option.value,
      label: option.textContent || '',
      disabled: option.disabled,
    }));
    const normalise = value => String(value || '').trim().toLocaleLowerCase();

    const rebuild = () => {
      const query = normalise(input.value);
      const selectedValue = select.value;
      const matches = completeOptions.filter(option => (
        option.value === ''
        || option.value === selectedValue
        || !query
        || normalise(option.label).includes(query)
      ));
      const fragment = document.createDocumentFragment();
      matches.forEach(snapshot => {
        const option = document.createElement('option');
        option.value = snapshot.value;
        option.textContent = snapshot.label;
        option.disabled = snapshot.disabled;
        fragment.appendChild(option);
      });
      select.replaceChildren(fragment);
      if (matches.some(option => option.value === selectedValue)) select.value = selectedValue;
    };

    input.addEventListener('input', rebuild);
  });
}

openModal = function (title, body, onSubmit, options = {}) {
  const result = workflowBaseOpenModal(title, body, onSubmit, options);
  bindWorkflowSearchableSelects($('#modal-form'));
  return result;
};

function serviceGroupOptions() {
  return cache.groups
    .filter(x => x.group.group_type === 'service' && x.group.enabled)
    .map(x => [x.group.id, x.group.name]);
}

function softDeleteRecord(label, url) {
  if (!confirm(`确认删除“${label}”？\n\n记录会进入回收站，可由管理员恢复。`)) return;
  api(url, { method: 'DELETE' })
    .then(() => { toast('已移入回收站'); loadCurrent(); })
    .catch(error => toast(error.message, true));
}

customerTable = function (data) {
  return table(['客户', '联系方式', '近 1 个月服务记录', '登记时间', '操作'], data.map(customer => {
    const services = (customer.recent_services || []).map(item => `
      <div class="service-note"><small>${fmtTime(item.updated_at)}</small><span>${recordRef('service_ticket', item.ticket_id, item.ticket_no)} · ${esc(item.title)}</span></div>
    `).join('') || '<small class="muted">近 1 个月暂无服务记录</small>';
    const editAction = workflowReadOnlyRole() ? '' : ` <button class="ghost tiny" onclick="editCustomer(${customer.id})">编辑</button>`;
    return `<tr><td>${recordRef('customer', customer.id, customer.customer_no)}<br><strong>${esc(customer.name)}</strong><br><small>${esc(customer.company_name || customer.customer_type)}</small></td><td>${esc(customer.phone || '-')}<br><small>${esc(customer.email || '-')}</small></td><td>${services}</td><td>${fmtTime(customer.created_at)}</td><td><button class="ghost tiny" onclick="showCustomer(${customer.id})">档案</button>${editAction} ${customer.phone ? `<button class="ghost tiny" onclick="openCallQuick('${esc(customer.phone)}')">话务快捷</button> ` : ''}${currentUser?.role === 'admin' ? `<button class="danger tiny" onclick="deleteAdminResource('customer',${customer.id},'${esc(customer.customer_no)}','/api/customers/${customer.id}')">删除</button>` : ''}</td></tr>`;
  }));
};

newCustomer = async function (defaults = {}) {
  openModal('新增客户', customerFields(), async f => api('/api/customers', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(f)),
  }));
  const form = $('#modal-form');
  for (const [key, value] of Object.entries(defaults || {})) if (form.elements[key]) form.elements[key].value = value ?? '';
};

newDevice = async function (customerId = null) {
  await warm();
  openModal('绑定设备', selectField('customer_id', '本次归属客户', cache.customers.map(x => [x.id, `${x.name} ${x.phone || ''}`])) + field('brand', '品牌', 'text', 'value="DJI"') + field('model', '型号', 'text', 'required') + field('serial_number', '序列号（允许重复）', 'text', 'required') + field('warranty_status', '质保状态') + selectField('is_temporary', '设备类型', [['false', '正式设备'], ['true', '临时设备']]) + field('remarks', '备注', 'textarea', 'full'), async f => {
    const payload = Object.fromEntries(f);
    payload.customer_id = Number(payload.customer_id);
    payload.is_temporary = payload.is_temporary === 'true';
    return api('/api/devices', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  });
  if (customerId) $('#modal-form [name=customer_id]').value = String(customerId);
};

newOrder = async function (preselectedCustomerId = null) {
  await warm();
  [cache.team, cache.groups] = await Promise.all([api('/api/team-members'), api('/api/processing-groups')]);
  openModal('新建维修工单', customerPickerField('customer_id', '关联客户', { required: true, selectedId: preselectedCustomerId }) + selectField('device_id', '关联设备', [['', '请先选择客户']], 'required disabled') + selectField('engineer_id', '负责人（可空）', [['', '暂不分派'], ...cache.team.map(x => [x.id, `${x.display_name} · ${x.employee_no}`])]) + selectField('processing_group_id', '服务组（可空）', [['', '自动匹配 / 不分组'], ...serviceGroupOptions()]) + selectField('priority', '优先级', [['normal', '普通'], ['high', '加急'], ['urgent', '紧急']]) + field('fault_description', '故障描述', 'textarea', 'full required') + field('intake_condition', '收机状态', 'textarea', 'full') + field('intake_accessories', '随件附件', 'textarea', 'full'), async f => {
    const payload = requiredFormValues(f, { customer_id: '关联客户', device_id: '关联设备', fault_description: '故障描述' });
    payload.customer_id = Number(payload.customer_id);
    payload.device_id = Number(payload.device_id);
    payload.engineer_id = payload.engineer_id ? Number(payload.engineer_id) : null;
    payload.processing_group_id = payload.processing_group_id ? Number(payload.processing_group_id) : null;
    return api('/api/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  });
  const form = $('#modal-form');
  const deviceSelect = form.elements.device_id;
  const groupSelect = form.elements.processing_group_id;
  const ownerSelect = form.elements.engineer_id;
  const updateDevices = customer => {
    const devices = customer ? cache.devices.filter(device => device.customer_id === customer.id) : [];
    deviceSelect.innerHTML = devices.length ? `<option value="">请选择设备</option>${devices.map(device => `<option value="${device.id}">${esc(device.serial_number)} · ${esc(device.model)}</option>`).join('')}` : '<option value="">该客户暂无设备，请先绑定设备</option>';
    deviceSelect.disabled = !devices.length;
  };
  bindCustomerPicker(form, updateDevices);
  updateDevices(cache.customers.find(x => x.id === Number(preselectedCustomerId)));
  ownerSelect.addEventListener('change', () => {
    if (groupSelect.value) return;
    const owner = cache.team.find(x => x.id === Number(ownerSelect.value));
    if (owner?.group_ids?.length === 1) groupSelect.value = String(owner.group_ids[0]);
  });
};

render.customers = async () => {
  cache.customers = await api('/api/customers');
  const createAction = workflowReadOnlyRole() ? '<span class="badge status-blue">只读模式</span>' : '<button class="primary" onclick="newCustomer()">新增客户</button>';
  $('#content').innerHTML = `<div class="toolbar"><input class="search" id="customer-q" type="search" placeholder="姓名、电话、客户编号"><div><button class="ghost" onclick="openCallQuick()">话务快捷</button> ${createAction}</div></div><div id="customer-list">${customerTable(cache.customers)}</div>`;
  let timer;
  $('#customer-q').oninput = event => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      cache.customers = await api(`/api/customers?q=${encodeURIComponent(event.target.value.trim())}`);
      $('#customer-list').innerHTML = customerTable(cache.customers);
    }, 180);
  };
};

showCustomer = async function (id) {
  const [detail, timeline, notes] = await Promise.all([
    api(`/api/customers/${id}`), api(`/api/customers/${id}/timeline`), api(`/api/customers/${id}/notes`),
  ]);
  const events = timeline.events.map(x => `<article class="timeline-event"><span></span><div><small>${fmtTime(x.occurred_at)} · ${esc(x.event_type)}</small><strong>${esc(x.title)}</strong>${x.content ? `<p>${esc(x.content)}</p>` : ''}${x.status ? badge(x.status) : ''}</div></article>`).join('') || '<div class="empty">暂无沟通与业务记录</div>';
  const small = notes.small.map(x => `<div class="note-card"><div><small>${esc(x.service_group_name)} · 组内可见</small><p>${esc(x.content || '尚未填写')}</p></div><div><button class="ghost tiny" onclick="editCustomerNote(${id},'small',${x.service_group_id})">编辑</button> <button class="ghost tiny" onclick="showCustomerNoteHistory(${id},'small',${x.service_group_id})">历史</button></div></div>`).join('') || '<div class="muted">当前账号未加入服务组，因此没有可见的小备注。</div>';
  openModal('客户全景档案', `<div class="field full"><div class="kv"><small>客户</small><strong>${esc(detail.customer.name)} · ${esc(detail.customer.phone || '无电话')}</strong><span>${esc(detail.customer.email || '无邮箱')}</span></div></div><div class="field full customer-detail-actions"><button type="button" class="primary" onclick="closeModal();editCustomer(${id})">编辑客户资料</button>${detail.customer.phone ? `<button type="button" class="ghost" onclick="closeModal();openCallQuick('${esc(detail.customer.phone)}')">话务快捷</button>` : ''}<button type="button" class="ghost" onclick="closeModal();composeOutboundEmail(null,null,${id})">发送邮件</button></div><div class="field full"><label>内部大备注</label><div class="note-card"><p>${esc(notes.large.content || '尚未填写')}</p><div><button class="ghost tiny" onclick="editCustomerNote(${id},'large')">编辑</button> <button class="ghost tiny" onclick="showCustomerNoteHistory(${id},'large')">历史</button></div></div></div><div class="field full"><label>本服务组小备注</label>${small}</div><div class="field full"><label>设备</label>${detail.devices.map(x => `<div>${esc(deviceLabel(x))} · ${esc(x.serial_number)}</div>`).join('') || '暂无'}</div><div class="field full"><label>维修工单</label>${detail.orders.map(x => `<div>${recordRef('repair_order', x.id, x.order_no)} · ${badge(x.status)}</div>`).join('') || '暂无'}</div><div class="field full"><label>沟通与服务时间线</label><div class="customer-timeline">${events}</div></div>`, async () => ({ silent: true }), { readOnly: true, variant: 'wide' });
};

async function editCustomerNote(customerId, type, serviceGroupId = null) {
  const data = await api(`/api/customers/${customerId}/notes`);
  const scope = type === 'large' ? data.large : data.small.find(x => x.service_group_id === Number(serviceGroupId));
  openModal(type === 'large' ? '编辑内部大备注' : `编辑小备注 · ${scope?.service_group_name || ''}`, field('content', '备注内容', 'textarea', 'full maxlength="20000"'), async f => {
    const body = { content: String(f.get('content') || '') };
    if (type === 'small') body.service_group_id = Number(serviceGroupId);
    const result = await api(`/api/customers/${customerId}/notes/${type}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return { ...result, silent: true, afterClose: () => showCustomer(customerId) };
  });
  $('#modal-form [name=content]').value = scope?.content || '';
}

async function showCustomerNoteHistory(customerId, type, serviceGroupId = null) {
  const data = await api(`/api/customers/${customerId}/notes`);
  const history = type === 'large' ? data.large.history : (data.small.find(x => x.service_group_id === Number(serviceGroupId))?.history || []);
  openModal('备注修改历史', `<div class="field full note-history">${history.map(x => `<article><small>${fmtTime(x.changed_at)} · ${esc(x.changed_by_name)}</small><p>${esc(x.content || '（已清空）')}</p></article>`).join('') || '<div class="empty">暂无历史版本</div>'}</div>`, async () => ({ silent: true }), { readOnly: true, variant: 'wide' });
}

async function loadOrderResults() {
  const params = new URLSearchParams();
  if (orderSearchValue) params.set('q', orderSearchValue);
  if (orderStatusValue) params.set('status', orderStatusValue);
  cache.orders = await api(`/api/orders?${params}`);
  $('#order-list').innerHTML = orderTable(cache.orders);
  $('#order-export').href = `/api/exports/orders.csv?${params}`;
}

render.orders = async () => {
  await warm();
  const states = ['pending_inspection', 'inspecting', 'pending_quote', 'quoted', 'customer_confirmed', 'repairing', 'pending_test', 'pending_shipping', 'completed', 'cancelled'];
  const writeActions = workflowReadOnlyRole() ? '<span class="badge status-blue">只读模式</span>' : '<button class="ghost" onclick="openNewWorkOrderGroup()">组成工单组</button> <button class="primary" onclick="newOrder()">新建工单</button>';
  $('#content').innerHTML = `<div class="toolbar"><div class="filter-row"><input class="search" id="order-q" type="search" value="${esc(orderSearchValue)}" placeholder="工单号、客户、电话、SN 或故障"><select id="order-status"><option value="">全部状态</option>${states.map(key => `<option value="${key}">${esc(labels[key])}</option>`).join('')}</select></div><div><a id="order-export" class="ghost tiny link-btn" href="/api/exports/orders.csv">导出当前结果 CSV</a> ${writeActions}</div></div><div id="order-list"></div>`;
  $('#order-status').value = orderStatusValue;
  let timer;
  $('#order-q').oninput = event => { orderSearchValue = event.target.value.trim(); clearTimeout(timer); timer = setTimeout(loadOrderResults, 180); };
  $('#order-status').onchange = event => { orderStatusValue = event.target.value; loadOrderResults(); };
  await loadOrderResults();
};

function serviceTicketTable(data) {
  return table(['工单号', '类型', '标题', '关联维修工单', '状态', '优先级', '负责人', '处理组', '时限', '催办', '操作'], data.map(ticket => `
    <tr>
      <td>${recordRef('service_ticket', ticket.id, ticket.ticket_no)}</td>
      <td>${esc(ticketTypeLabels[ticket.ticket_type] || ticket.ticket_type)}</td>
      <td><strong>${esc(ticket.title)}</strong></td>
      <td>${ticket.repair_order_id ? recordRef('repair_order', ticket.repair_order_id, orderNo(ticket.repair_order_id)) : '-'}</td>
      <td>${badge(ticket.status)}</td>
      <td>${esc(ticket.priority)}</td>
      <td>${esc(memberName(ticket.current_owner_id))}</td>
      <td>${esc(groupName(ticket.processing_group_id))}</td>
      <td class="${ticket.overdue ? 'badge red' : ''}">${ticket.due_at ? fmtTime(ticket.due_at) : '未设置'}</td>
      <td>${ticket.reminder_count || 0}</td>
      <td>${currentUser?.role === 'admin' ? `<button class="danger tiny" onclick="deleteAdminResource('service_ticket',${ticket.id},'${esc(ticket.ticket_no)}','/api/service-tickets/${ticket.id}')">删除</button>` : '-'}</td>
    </tr>
  `));
}

async function loadServiceTicketResults() {
  const requestedSearch = ticketSearchValue;
  const params = new URLSearchParams();
  if (requestedSearch) params.set('q', requestedSearch);
  const query = params.toString();
  const data = await api(`/api/service-tickets${query ? `?${query}` : ''}`);
  if (requestedSearch !== ticketSearchValue || current !== 'tickets') return;
  cache.tickets = data;
  const root = $('#service-ticket-list');
  if (root) root.innerHTML = serviceTicketTable(data);
}

render.tickets = async () => {
  [cache.team, cache.groups] = await Promise.all([
    api('/api/team-members'),
    api('/api/processing-groups'),
  ]);
  const createGroupButton = ['admin', 'manager'].includes(currentUser?.role)
    ? '<button class="ghost" onclick="newProcessingGroup()">新建处理组</button> '
    : '';
  const createTicketButton = workflowReadOnlyRole()
    ? '<span class="badge status-blue">只读模式</span>'
    : '<button class="primary" onclick="newServiceTicket()">新建服务工单</button>';
  $('#content').innerHTML = `
    <div class="toolbar">
      <div class="filter-row">
        <input class="search" id="service-ticket-q" type="search" autocomplete="off" value="${esc(ticketSearchValue)}" placeholder="工单号、标题、客户、电话、维修单号或 SN" aria-label="检索服务工单">
      </div>
      <div>${createGroupButton}${createTicketButton}</div>
    </div>
    <div class="muted">维修、零售、置换、咨询、报价跟进、投诉、物流异常和技术支持共用负责人、SLA 与时间线；单号可单击或双击打开。</div>
    <div id="service-ticket-list"><div class="loading">正在检索服务工单…</div></div>
  `;
  let timer;
  $('#service-ticket-q').addEventListener('input', event => {
    ticketSearchValue = event.target.value.trim();
    clearTimeout(timer);
    timer = setTimeout(loadServiceTicketResults, 180);
  });
  await loadServiceTicketResults();
};

function workOrderGroupTable(groups) {
  const canManage = ['admin', 'manager'].includes(currentUser?.role);
  return table(['工单组', '组内工单', '更新时间', '操作'], groups.map(group => `<tr><td><button class="record-ref" onclick="showWorkOrderGroup(${group.id})"><strong>${esc(group.name)}</strong></button></td><td>${group.orders.map(order => recordRef('repair_order', order.id, order.order_no)).join(' · ')}</td><td>${fmtTime(group.updated_at)}</td><td>${canManage ? `<button class="ghost tiny" onclick="openNewWorkOrderGroup(${group.id})">编辑</button> <button class="danger tiny" onclick="softDeleteRecord('${esc(group.name)}','/api/work-order-groups/${group.id}')">删除</button>` : `<button class="ghost tiny" onclick="showWorkOrderGroup(${group.id})">查看</button>`}</td></tr>`));
}

render.work_order_groups = async () => {
  const groups = await api('/api/work-order-groups');
  const createButton = ['admin', 'manager'].includes(currentUser?.role) ? '<button class="primary" onclick="openNewWorkOrderGroup()">新建工单组</button>' : '';
  $('#content').innerHTML = `<div class="toolbar"><input id="group-q" class="search" type="search" placeholder="输入组名或任一组内工单号">${createButton}</div><div id="group-list">${workOrderGroupTable(groups)}</div>`;
  let timer;
  $('#group-q').oninput = event => { clearTimeout(timer); timer = setTimeout(async () => { $('#group-list').innerHTML = workOrderGroupTable(await api(`/api/work-order-groups?q=${encodeURIComponent(event.target.value.trim())}`)); }, 180); };
};

async function openNewWorkOrderGroup(groupId = null) {
  await warm();
  const group = groupId ? await api(`/api/work-order-groups/${groupId}`) : null;
  const selected = new Set((group?.orders || []).map(x => x.id));
  openModal(group ? '编辑工单组' : '新建工单组', field('name', '工单组名称', 'text', 'full required maxlength="160"') + `<div class="field full"><label>选择至少两个独立工单</label><div class="group-order-picker">${cache.orders.map(x => `<label><input data-group-order type="checkbox" value="${x.id}" ${selected.has(x.id) ? 'checked' : ''}><span><strong>${esc(x.order_no)}</strong><small>${esc(customerName(x.customer_id))} · ${esc(x.fault_description.slice(0, 80))}</small></span></label>`).join('')}</div></div>`, async f => {
    const order_ids = [...document.querySelectorAll('[data-group-order]:checked')].map(x => Number(x.value));
    if (order_ids.length < 2) throw new Error('请至少选择两个工单');
    return api(group ? `/api/work-order-groups/${group.id}` : '/api/work-order-groups', { method: group ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: String(f.get('name') || '').trim(), order_ids }) });
  }, { variant: 'wide' });
  $('#modal-form [name=name]').value = group?.name || '';
}

async function showWorkOrderGroup(id) {
  const group = await api(`/api/work-order-groups/${id}`);
  openModal(`工单组 · ${group.name}`, `<div class="field full group-detail">${group.orders.map(order => `<article><div><strong>${recordRef('repair_order', order.id, order.order_no)}</strong><p>${esc(order.fault_description)}</p></div>${badge(order.status)}</article>`).join('')}</div>`, async () => ({ silent: true }), { readOnly: true, variant: 'wide' });
}

render.knowledge = async () => {
  $('#content').innerHTML = `<div class="panel"><div class="panel-title"><div><p class="eyebrow">OFFICIAL SUPPORT</p><h2>知识库快捷入口</h2></div></div><p class="muted">以下链接打开官方帮助中心，不在系统内缓存或改写内容。</p><div class="knowledge-links"><a href="https://support.dji.com/help/search?lang=zh-CN&re=cn&trackId=9bd9a85d-07b3-460d-8f6a-f62a23819c91&limit=10&page=1&spaceId=17&keyword=&folderIdList=0&defaultOpened=,17" target="_blank" rel="noopener noreferrer"><strong>DJI 帮助中心</strong><span>产品支持、下载与服务 →</span></a><a href="https://support.apple.com/zh-cn" target="_blank" rel="noopener noreferrer"><strong>Apple 支持</strong><span>设备与服务支持 →</span></a></div></div>`;
};

render.staff_search = async () => {
  $('#content').innerHTML = `<div class="panel staff-search"><div class="panel-title"><div><p class="eyebrow">STAFF LOOKUP</p><h2>按工号搜索人员</h2></div></div><div class="toolbar"><input id="staff-no" class="search" type="search" placeholder="例如 ST0001"><button class="primary" onclick="runStaffSearch()">搜索</button></div><div id="staff-result" class="empty">输入唯一工号查看账号和关联工单</div></div>`;
  $('#staff-no').onkeydown = event => { if (event.key === 'Enter') runStaffSearch(); };
};

async function runStaffSearch() {
  const value = $('#staff-no').value.trim();
  if (!value) return;
  const data = await api(`/api/staff/search?employee_no=${encodeURIComponent(value)}`);
  const root = $('#staff-result');
  if (!data) { root.className = 'empty'; root.textContent = '未找到该工号'; return; }
  root.className = '';
  root.innerHTML = `<div class="panel"><div class="panel-title"><div><small>${esc(data.user.employee_no)} · ${esc(roleLabels[data.user.role] || data.user.role)}</small><h3>${esc(data.user.display_name)}</h3><p>${esc(data.user.username)}</p></div>${data.user.enabled ? badge('启用') : badge('停用')}</div></div>${table(['工单号', '状态', '故障描述'], data.orders.map(x => `<tr><td>${recordRef('repair_order', x.id, x.order_no)}</td><td>${badge(x.status)}</td><td>${esc(x.fault_description)}</td></tr>`))}`;
}

function canRegisterCallResult(call) {
  if (currentUser?.role === 'viewer') return false;
  if (currentUser?.role === 'call_operator') return Number(call.assigned_to) === Number(currentUser.id);
  return true;
}

function openCallsPage() {
  current = 'calls';
  history.replaceState(null, '', '#calls');
  nav();
  loadCurrent();
}

function callResultRegistrationHtml(calls) {
  const ordered = [...calls].sort((left, right) => {
    const actionDelta = Number(canRegisterCallResult(right)) - Number(canRegisterCallResult(left));
    if (actionDelta) return actionDelta;
    return new Date(left.planned_at || left.created_at || 0) - new Date(right.planned_at || right.created_at || 0);
  });
  const items = ordered.slice(0, 6).map(call => {
    const links = [];
    if (call.repair_order_id) links.push(recordRef('repair_order', call.repair_order_id, orderNo(call.repair_order_id)));
    if (call.service_ticket_id) links.push(recordRef('service_ticket', call.service_ticket_id, ticketNo(call.service_ticket_id)));
    const action = canRegisterCallResult(call)
      ? `<button class="primary call-result-action" onclick="completeOutboundCall(${call.id})">登记结果</button>`
      : '<span class="badge status-blue">仅负责人可登记</span>';
    return `<article class="call-result-item"><div class="call-result-heading"><span><strong>${esc(call.call_no)}</strong><small>${esc(customerName(call.customer_id))} · ${esc(call.contact_number)}</small></span>${action}</div><p>${esc(call.purpose)}</p><div class="call-result-meta"><span>计划 ${fmtTime(call.planned_at)}</span><span>负责人 ${esc(memberName(call.assigned_to))}</span></div>${links.length ? `<div class="call-result-links">${links.join(' · ')}</div>` : ''}</article>`;
  }).join('');
  const empty = currentUser?.role === 'call_operator'
    ? '暂无可见的待登记外呼任务。新任务分配给你后会显示在这里。'
    : '暂无待登记外呼任务。';
  return `<section class="panel call-result-panel" aria-labelledby="call-result-title"><div class="panel-title"><div><p class="eyebrow">CALL RESULTS</p><h3 id="call-result-title">结果登记</h3><small>${calls.length ? `共 ${calls.length} 条待登记` : '当前无需处理'}</small></div><button class="ghost tiny" onclick="openCallsPage()">查看全部</button></div><div class="call-result-list">${items || `<div class="empty call-result-empty">${empty}</div>`}</div>${calls.length > 6 ? `<p class="muted call-result-more">另有 ${calls.length - 6} 条，请到外呼记录查看。</p>` : ''}</section>`;
}

render.call_quick = async () => {
  const [plannedCalls, customers, tickets, team] = await Promise.all([api('/api/outbound-calls?status=planned'), api('/api/customers'), api('/api/service-tickets'), api('/api/team-members')]);
  cache.customers = customers; cache.tickets = tickets; cache.team = team;
  const modeNotice = currentUser?.role === 'call_operator' ? '<div class="alert"><strong>话务模式已启用：</strong>可按号码匹配客户并查看全部维修、服务工单；可登记分配给本人的外呼结果，其他业务保持只读。</div>' : '';
  $('#content').innerHTML = `${modeNotice}<div class="call-quick-grid"><div class="panel"><div class="panel-title"><div><p class="eyebrow">CALL DESK</p><h2>话务快捷</h2></div></div><div class="toolbar"><input id="call-quick-phone" class="search" type="tel" value="${esc(callQuickPhone)}" placeholder="完整手机号、部分号码或后几位"><button class="primary" onclick="runCallQuickMatch()">匹配客户</button></div><div id="call-quick-results" class="empty">输入号码后会同时进行精确与模糊匹配</div></div><div class="call-quick-side">${callResultRegistrationHtml(plannedCalls)}<aside class="panel call-checklist"><h3>话务检查单</h3><p class="muted">仅作当前页面提醒，不保存、不强制完成。</p>${['问候', '关心', '您看还有其他可以帮到您的吗？', '感谢您的来电/接听，祝您生活愉快'].map((text, index) => `<label><input type="checkbox" id="call-check-${index}"><span>${esc(text)}</span></label>`).join('')}</aside></div></div>`;
  $('#call-quick-phone').onkeydown = event => { if (event.key === 'Enter') runCallQuickMatch(); };
  if (callQuickPhone) await runCallQuickMatch();
};

function openCallQuick(phone = '') {
  callQuickPhone = phone || '';
  current = 'call_quick';
  history.replaceState(null, '', '#call_quick');
  nav();
  loadCurrent();
}

async function runCallQuickMatch() {
  const phone = $('#call-quick-phone')?.value.trim();
  if (!phone) return toast('请输入电话号码', true);
  callQuickPhone = phone;
  const data = await api(`/api/call-quick/match?phone=${encodeURIComponent(phone)}`);
  const root = $('#call-quick-results');
  if (!data.matches.length) {
    root.className = '';
    const createAction = workflowReadOnlyRole() ? '<span class="badge status-blue">只读模式</span>' : `<button class="primary" onclick="newCustomer({phone:'${esc(phone)}'})">新增客户</button>`;
    root.innerHTML = `<div class="empty"><strong>未查询到客户</strong><p>${workflowReadOnlyRole()?'当前话务账号可继续检索其他号码。':'可以直接新增客户，后续仍可使用既有客户档案整理流程。'}</p>${createAction}</div>`;
    return;
  }
  root.className = 'call-match-list';
  root.innerHTML = data.matches.map((match, index) => `<button onclick="showCallQuickCustomer(${index})"><span><strong>${esc(match.customer.name)}</strong><small>${esc(match.customer.customer_no)} · ${esc(match.customer.phone || '')}</small></span><em>${match.match_type === 'exact' ? '精确匹配' : '模糊匹配'}</em></button>`).join('');
  root._matches = data.matches;
  if (data.matches.length === 1) showCallQuickCustomer(0);
}

function showCallQuickCustomer(index) {
  const root = $('#call-quick-results');
  const match = root._matches?.[index];
  if (!match) return;
  const records = match.recent_services.map(x => {
    const linked = [];
    if (x.repair_order_id && x.repair_order_no) linked.push(recordRef('repair_order', x.repair_order_id, x.repair_order_no));
    if (x.serial_number) linked.push(`SN：${esc(x.serial_number)}`);
    if (x.device_model) linked.push(esc(x.device_model));
    return `<article><small>${fmtTime(x.updated_at)} · ${recordRef('service_ticket', x.id, x.ticket_no)}</small><strong>${esc(x.title)}</strong>${linked.length ? `<span class="muted">${linked.join(' · ')}</span>` : ''}${badge(x.status)}</article>`;
  }).join('') || '<div class="empty">近 30 天内暂无可见服务记录</div>';
  const writeActions = workflowReadOnlyRole() ? '' : `<button class="primary" onclick="newOrder(${match.customer.id})">新增工单</button><button class="ghost" onclick="newDevice(${match.customer.id})">匹配设备</button>`;
  root.innerHTML = `<div class="panel-title"><div><small>${esc(match.customer.customer_no)}</small><h3>${esc(match.customer.name)} · ${esc(match.customer.phone || '')}</h3></div><button class="ghost tiny" onclick="runCallQuickMatch()">返回匹配列表</button></div><div class="customer-detail-actions">${writeActions}<button class="ghost" onclick="showCustomer(${match.customer.id})">客户档案</button></div><div class="call-service-records"><h4>近 30 天 · 可见服务记录</h4>${records}</div>`;
}

render.calls = async () => {
  const [data, customers, tickets, team] = await Promise.all([api('/api/outbound-calls'), api('/api/customers'), api('/api/service-tickets'), api('/api/team-members')]);
  cache.customers = customers; cache.tickets = tickets; cache.team = team;
  const createAction = workflowReadOnlyRole() ? '<span class="badge status-blue">只读模式</span>' : '<button class="primary" onclick="newOutboundCall()">新建外呼任务</button>';
  $('#content').innerHTML = `<div class="toolbar"><div><strong>外呼任务与通话记录</strong><div class="muted">高频话务动作可直接进入号码匹配、近 30 天记录和检查单。</div></div><div><button class="ghost" onclick="openCallQuick()">话务快捷</button> ${createAction}</div></div>${table(['外呼号', '客户', '号码', '目的', '计划时间', '负责人', '状态 / 结果', '摘要', '操作'], data.map(x => `<tr><td><strong>${esc(x.call_no)}</strong></td><td>${esc(customerName(x.customer_id))}</td><td>${esc(x.contact_number)}</td><td>${esc(x.purpose)}</td><td>${fmtTime(x.planned_at)}</td><td>${esc(memberName(x.assigned_to))}</td><td>${badge(x.status)}${x.result ? `<br><small>${esc(callResultLabels[x.result] || x.result)}</small>` : ''}</td><td>${esc(x.summary || '-')}</td><td><button class="ghost tiny" onclick="openCallQuick('${esc(x.contact_number)}')">话务快捷</button> ${!workflowReadOnlyRole()&&x.status !== 'completed' ? `<button class="primary tiny" onclick="completeOutboundCall(${x.id})">登记结果</button>` : ''}</td></tr>`))}`;
};

render.followups = async () => {
  const data = await api('/api/follow-ups');
  const canEdit = !workflowReadOnlyRole();
  const createAction = canEdit ? '<button class="primary" onclick="newFollowup()">安排回访</button>' : '<span class="badge status-blue">只读模式</span>';
  $('#content').innerHTML = `<div class="toolbar"><div class="muted">完成回访时可直接安排下一次联系，系统会建立新的待办。删除的记录会进入回收站。</div>${createAction}</div>${table(['工单', '客户', '类型', '计划时间', '状态', '内容', '结果', '操作'], data.map(item => {
    const actions = [];
    if (canEdit && item.status === 'pending') actions.push(`<button class="primary tiny" onclick="completeFollowup(${item.id})">完成回访</button>`);
    if (currentUser?.role === 'admin') actions.push(`<button class="danger tiny" onclick="deleteAdminResource('follow_up_task',${item.id},'回访 #${item.id}','/api/follow-ups/${item.id}')">删除</button>`);
    return `<tr><td>${recordRef('repair_order', item.repair_order_id, orderNo(item.repair_order_id))}</td><td>${esc(customerName(item.customer_id))}</td><td>${esc(item.follow_up_type)}</td><td>${fmtTime(item.scheduled_at)}</td><td>${badge(item.status)}</td><td>${esc(item.content || '')}</td><td>${esc(item.result || '-')}</td><td>${actions.join(' ') || '-'}</td></tr>`;
  }))}`;
};

financeFields = function () {
  return selectField('transaction_type', '类型', [['income', '收款'], ['expense', '支出'], ['refund', '退款']]) + selectField('quote_id', '关联报价编号（选填）', [['', '不关联报价'], ...cache.quotes.map(x => [x.id, x.quote_no])]) + field('category', '分类', 'text', 'required placeholder="例如：维修收款、采购支出、客户退款"') + field('amount', '金额', 'number', 'step="0.01" min="0.01" required') + selectField('payment_method', '支付方式', [['wechat', '微信'], ['alipay', '支付宝'], ['bank', '银行'], ['cash', '现金'], ['other', '其他']]) + field('paid_at', '发生时间', 'datetime-local') + field('description', '备注 / 说明', 'textarea', 'full');
};

financePayload = function (formData, existing = null) {
  const payload = Object.fromEntries(formData);
  payload.quote_id = payload.quote_id ? Number(payload.quote_id) : null;
  payload.repair_order_id = payload.quote_id ? null : (existing?.repair_order_id || null);
  payload.customer_id = payload.quote_id ? null : (existing?.customer_id || null);
  if (!payload.paid_at) delete payload.paid_at;
  return payload;
};

newFinance = async function () {
  await warm(); cache.quotes = await api('/api/quotes');
  openModal('记一笔财务流水', financeFields(), async f => {
    const key = globalThis.crypto?.randomUUID?.() || `finance-${Date.now()}-${Math.random()}`;
    return api('/api/finance', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: JSON.stringify(financePayload(f)) });
  }, { submitLabel: '保存流水', busyLabel: '正在保存…' });
};

editFinance = async function (id) {
  await warm(); [cache.quotes, cache.finance] = await Promise.all([api('/api/quotes'), api('/api/finance')]);
  const data = cache.finance.find(x => x.id === Number(id));
  if (!data) return toast('财务流水不存在', true);
  openModal(`编辑流水 · ${data.transaction_no}`, financeFields(), async f => api(`/api/finance/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(financePayload(f, data)) }), { submitLabel: '保存修改', busyLabel: '正在保存…' });
  const form = $('#modal-form');
  for (const key of ['transaction_type', 'quote_id', 'category', 'amount', 'payment_method', 'description']) if (form.elements[key]) form.elements[key].value = data[key] ?? '';
  form.elements.paid_at.value = dateTimeInputValue(data.paid_at);
};

async function loadFinanceResults() {
  cache.finance = await api(`/api/finance?q=${encodeURIComponent(financeSearchValue)}`);
  $('#finance-list').innerHTML = table(['流水号', '类型', '分类', '金额', '报价编号', '历史工单', '时间', '备注', '操作'], cache.finance.map(x => `<tr><td>${esc(x.transaction_no)}</td><td>${badge(x.transaction_type)}</td><td>${esc(x.category)}</td><td class="amount"><strong>${money(x.amount)}</strong></td><td>${x.quote_id ? esc(cache.quotes.find(q => q.id === x.quote_id)?.quote_no || `#${x.quote_id}`) : '-'}</td><td>${x.repair_order_id ? recordRef('repair_order', x.repair_order_id, orderNo(x.repair_order_id)) : '-'}</td><td>${fmtTime(x.paid_at)}</td><td>${esc(x.description || '')}</td><td><button class="ghost tiny" onclick="editFinance(${x.id})">编辑</button> <button class="danger tiny" onclick="softDeleteRecord('${esc(x.transaction_no)}','/api/finance/${x.id}')">删除</button></td></tr>`));
}

render.finance = async () => {
  await warm(); cache.quotes = await api('/api/quotes');
  $('#content').innerHTML = `<div class="toolbar"><input id="finance-q" class="search" type="search" value="${esc(financeSearchValue)}" placeholder="备注关键字或工单号"><button class="primary" onclick="newFinance()">记一笔</button></div><div id="finance-list"></div>`;
  let timer;
  $('#finance-q').oninput = event => { financeSearchValue = event.target.value.trim(); clearTimeout(timer); timer = setTimeout(loadFinanceResults, 180); };
  await loadFinanceResults();
};

render.inventory = async () => {
  cache.inventory = await api('/api/inventory/items');
  const canEdit = ['admin', 'manager', 'warehouse'].includes(currentUser?.role);
  $('#content').innerHTML = `<div class="toolbar"><input class="search" id="inventory-scan" placeholder="扫码或输入 SKU / 名称"><div class="muted">后台专用物料不会出现在商城</div><button class="primary" onclick="newInventory()">新增物料</button></div>${table(['SKU', '名称', '分类', '库存', '安全库存', '客户端展示', '进价', '售价', '库位', '操作'], cache.inventory.map(x => `<tr><td>${esc(x.sku)}</td><td><strong>${esc(x.name)}</strong></td><td>${esc(x.category || '-')}</td><td class="amount">${inventoryQty(x.stock_quantity)} ${esc(x.unit)}</td><td class="amount">${inventoryQty(x.safety_stock)}</td><td>${x.client_visible ? '<span class="badge enabled">可展示</span>' : '<span class="badge disabled">后台专用</span>'}${canEdit ? `<br><button class="ghost tiny" onclick="setInventoryClientVisibility(${x.id},${!x.client_visible})">${x.client_visible ? '设为后台专用' : '允许展示'}</button>` : ''}</td><td class="amount">${money(x.purchase_price)}</td><td class="amount">${money(x.sale_price)}</td><td>${esc(x.location || '-')}</td><td>${canEdit ? `<button class="ghost tiny" onclick="stockChange(${x.id},'stock_in')">入库</button> <button class="ghost tiny" onclick="stockChange(${x.id},'repair_issue')">领料</button> <button class="danger tiny" onclick="softDeleteRecord('${esc(x.sku)}','/api/inventory/items/${x.id}')">删除</button>` : '-'}</td></tr>`))}`;
  if (!canEdit) $('#content .toolbar button')?.remove();
  $('#inventory-scan').oninput = event => {
    const q = event.target.value.trim().toLowerCase();
    document.querySelectorAll('#content tbody tr').forEach(row => row.classList.toggle('hidden', q && !row.textContent.toLowerCase().includes(q)));
  };
};

assignTicket = async function (id) {
  await ensureTicketCaches();
  const body = field('member_search', '搜索人员', 'search', 'full placeholder="姓名或工号"') + selectField('processing_group_id', '所属服务组', [['', '清空服务组'], ...serviceGroupOptions()]) + selectField('current_owner_id', '当前负责人', [['', '清空负责人']]) + field('reason', '转派原因', 'textarea', 'full required');
  openModal('转派服务工单', body, async f => {
    const payload = requiredFormValues(f, { reason: '转派原因' });
    payload.current_owner_id = payload.current_owner_id ? Number(payload.current_owner_id) : null;
    payload.processing_group_id = payload.processing_group_id ? Number(payload.processing_group_id) : null;
    return api(`/api/service-tickets/${id}/assignment`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  });
  const form = $('#modal-form');
  const search = form.elements.member_search;
  const group = form.elements.processing_group_id;
  const owner = form.elements.current_owner_id;
  const refresh = () => {
    const q = search.value.trim().toLowerCase();
    const groupId = Number(group.value || 0);
    const filtered = cache.team.filter(member => {
      const grouped = member.group_ids?.length;
      const groupMatch = !groupId || member.group_ids?.includes(groupId) || !grouped;
      return groupMatch && (!q || `${member.display_name} ${member.employee_no} ${member.role}`.toLowerCase().includes(q));
    });
    const previous = owner.value;
    owner.innerHTML = '<option value="">清空负责人</option>' + filtered.map(x => `<option value="${x.id}">${esc(x.display_name)} · ${esc(x.employee_no)}${x.group_ids?.length ? '' : ' · 未分组'}</option>`).join('');
    if ([...owner.options].some(x => x.value === previous)) owner.value = previous;
  };
  search.addEventListener('input', refresh); group.addEventListener('change', refresh); refresh();
};

const workflowDamageRenderer = render.damage_sop;
render.damage_sop = async () => {
  await workflowDamageRenderer();
  if (!['admin', 'manager', 'engineer', 'technical_support'].includes(currentUser?.role)) return;
  document.querySelectorAll('button[onclick^="showDamageAssessment("]').forEach(button => {
    const id = button.getAttribute('onclick').match(/\d+/)?.[0];
    if (id) button.insertAdjacentHTML('afterend', ` <button class="danger tiny" onclick="softDeleteRecord('定损结果 #${id}','/api/damage-assessments/${id}')">删除</button>`);
  });
};

const workflowRepairWorkbench = workbench;
workbench = async function (id) {
  await workflowRepairWorkbench(id);
  document.querySelectorAll('button[onclick*="recommendedQuote"]').forEach(button => button.remove());
  $('#content .toolbar > div:last-child')?.insertAdjacentHTML('afterbegin', `<button class="ghost" onclick="editOrderServiceGroup(${id})">调整服务组</button> `);
};

async function editOrderServiceGroup(id) {
  cache.groups = await api('/api/processing-groups');
  const detail = await api(`/api/orders/${id}`);
  openModal('调整工单服务组', selectField('processing_group_id', '服务组', [['', '不分组'], ...serviceGroupOptions()]), async f => api(`/api/orders/${id}/service-group`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ processing_group_id: f.get('processing_group_id') ? Number(f.get('processing_group_id')) : null }) }));
  $('#modal-form [name=processing_group_id]').value = detail.order.processing_group_id || '';
}

render.users = async () => {
  const [data, groups] = await Promise.all([api('/api/users'), api('/api/service-groups')]);
  cache.serviceGroups = groups;
  $('#content').innerHTML = `<div class="toolbar"><div class="alert" style="margin:0">账号密码由系统生成并只展示一次；只有管理员可以重置。删除账号会停用账号并撤销会话，历史工单署名仍会保留。</div><button class="primary" onclick="newUser()">新增账号</button></div>${table(['工号', '用户名', '姓名', '角色', '状态', '服务组', '最后登录', '操作'], data.map(x => `<tr><td><strong>${esc(x.employee_no)}</strong></td><td>${esc(x.username)}</td><td>${esc(x.display_name)}</td><td>${esc(roleLabels[x.role] || x.role)}</td><td>${x.enabled ? badge('启用') : badge('停用')}</td><td>${groups.filter(g => g.member_ids.includes(x.id)).map(g => esc(g.name)).join('、') || '<span class="muted">未分组</span>'}</td><td>${fmtTime(x.last_login_at)}</td><td><button class="ghost tiny" onclick='editUser(${JSON.stringify(x)})'>编辑</button> <button class="ghost tiny" onclick="manageUserGroups(${x.id})">服务组</button> <button class="ghost tiny" onclick="resetUserPassword(${x.id},'${esc(x.username)}')">重置密码</button>${x.enabled&&x.id!==currentUser?.id?` <button class="danger tiny" onclick="deleteUserAccount(${x.id},'${esc(x.username)}')">删除账号</button>`:''}</td></tr>`))}`;
};

async function resetUserPassword(userId, username) {
  if (!confirm(`为账号“${username}”生成新的 16 位管理密码？\n\n旧密码和全部已登录会话会立即失效。`)) return;
  try {
    const result = await api(`/api/users/${userId}/password/reset`, { method: 'POST' });
    showManagementCredential(result);
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteUserAccount(userId, username) {
  if (!confirm(`确认删除账号“${username}”？\n\n账号会立即停用并退出所有会话；历史工单、备注和审计署名会继续保留。`)) return;
  try {
    await api(`/api/users/${userId}`, { method: 'DELETE' });
    toast('账号已删除（历史记录已保留）');
    await render.users();
  } catch (error) {
    toast(error.message, true);
  }
}

newUser = async function () {
  openModal('新增门店账号', field('username', '用户名', 'text', 'required') + field('employee_no', '工号（留空自动分配）') + field('display_name', '显示姓名', 'text', 'required') + field('wecom_userid', '企业微信 UserID（可稍后绑定）', 'text', 'autocomplete="off"') + selectField('role', '角色', Object.entries(roleLabels).map(([key, value]) => [key, value])), async f => {
    const payload = Object.fromEntries(f);
    if (!payload.wecom_userid) delete payload.wecom_userid;
    if (!payload.employee_no) delete payload.employee_no;
    const result = await api('/api/users', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    return { ...result, silent: true, afterClose: () => showManagementCredential(result) };
  });
};

editUser = async function (user) {
  openModal(`编辑账号 · ${user.username}`, field('employee_no', '工号', 'text', `value="${esc(user.employee_no)}" required`) + field('display_name', '显示姓名', 'text', `value="${esc(user.display_name)}"`) + field('wecom_userid', '企业微信 UserID', 'text', `value="${esc(user.wecom_userid || '')}" autocomplete="off"`) + selectField('role', '角色', Object.entries(roleLabels).map(([key, value]) => [key, value])) + selectField('enabled', '账号状态', [['true', '启用'], ['false', '停用']]), async f => {
    const payload = Object.fromEntries(f); payload.enabled = payload.enabled === 'true';
    return api(`/api/users/${user.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  });
  $('#modal-form [name=role]').value = user.role; $('#modal-form [name=enabled]').value = String(user.enabled);
};

async function manageUserGroups(userId) {
  const groups = await api('/api/service-groups');
  const groupRows = groups.map(group => `<label data-user-group-row><input data-user-group type="checkbox" value="${group.id}" ${group.member_ids.includes(userId) ? 'checked' : ''}><span><strong>${esc(group.name)}</strong><small>${esc(group.description || '服务组')}</small></span></label>`).join('');
  const body = field('service_group_search', '检索服务组', 'search', 'full autocomplete="off" placeholder="输入服务组名称或描述"')
    + `<div class="field full group-order-picker">${groupRows || '<div class="empty">暂无服务组，请先在服务工单页面创建处理组。</div>'}<div id="user-group-search-empty" class="empty" hidden>没有匹配的服务组</div></div>`;
  openModal('调整人员服务组', body, async () => api(`/api/users/${userId}/service-groups`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group_ids: [...document.querySelectorAll('[data-user-group]:checked')].map(x => Number(x.value)) }) }));
  const search = $('#modal-form [name=service_group_search]');
  const rows = [...document.querySelectorAll('[data-user-group-row]')];
  const empty = $('#user-group-search-empty');
  search?.addEventListener('input', event => {
    const query = event.target.value.trim().toLocaleLowerCase();
    let visible = 0;
    rows.forEach(row => {
      const matches = !query || row.textContent.toLocaleLowerCase().includes(query);
      row.hidden = !matches;
      if (matches) visible += 1;
    });
    if (empty) empty.hidden = visible > 0 || !groups.length;
  });
}

function applyWorkflowReadOnlyWorkbench() {
  if (!workflowReadOnlyRole()) return;
  const content = $('#content');
  if (current === 'orders') {
    const cards = [...(content?.querySelectorAll('.workbench-head .kv') || [])];
    const quoteCard = cards.find(card => card.querySelector('small')?.textContent.includes('报价 / 已收'));
    if (quoteCard) {
      quoteCard.querySelector('small').textContent = '报价';
      const value = quoteCard.querySelector('strong');
      if (value) value.textContent = value.textContent.split(' / ')[0];
    }
    cards.find(card => card.querySelector('small')?.textContent.includes('毛利润'))?.remove();
    [...(content?.querySelectorAll('.section-card h4') || [])]
      .find(heading => heading.textContent.trim().startsWith('收支'))
      ?.closest('.section-card')
      ?.remove();
  }
  const toolbar = content?.querySelector(':scope > .toolbar');
  const toolbarButtons = toolbar ? [...toolbar.querySelectorAll('button')] : [];
  toolbarButtons.slice(1).forEach(button => button.remove());
  content?.querySelectorAll('.panel button:not(.record-ref), .section-action, .action-menu').forEach(control => control.remove());
  const message = currentUser?.role === 'call_operator'
    ? '<strong>话务只读模式：</strong>可查看全部维修工单与服务工单；修改、删除和状态流转需由业务负责人完成。'
    : '<strong>只读模式：</strong>当前账号不能修改业务记录。';
  toolbar?.insertAdjacentHTML('afterend', `<div class="alert">${message}</div>`);
}

const workflowWritableRepairWorkbench = workbench;
workbench = async function (id) {
  await workflowWritableRepairWorkbench(id);
  applyWorkflowReadOnlyWorkbench();
};

const workflowWritableTicketWorkbench = ticketWorkbench;
ticketWorkbench = async function (id) {
  await workflowWritableTicketWorkbench(id);
  applyWorkflowReadOnlyWorkbench();
};

const workflowWritableCustomerDetail = showCustomer;
showCustomer = async function (id) {
  await workflowWritableCustomerDetail(id);
  if (!workflowReadOnlyRole()) return;
  $('#modal-form')?.querySelectorAll('button[onclick*="editCustomer"], button[onclick*="editCustomerNote"], button[onclick*="composeOutboundEmail"]').forEach(button => button.remove());
};

/*
 * The email composer keeps a native select (predictable keyboard and mobile
 * behaviour), while optgroups make the growing template library scannable.
 * Metadata comes from the API so custom templates follow the same grouping.
 */
const emailTemplateBaseSelectField = selectField;
let emailTemplateLibraryItems = [];

function emailTemplateCategories(items) {
  const categories = new Map();
  items.forEach(item => {
    const key = item.category || 'general';
    if (!categories.has(key)) {
      categories.set(key, {
        key,
        label: item.category_label || '通用通知',
        order: Number(item.category_order ?? 999),
        items: [],
      });
    }
    categories.get(key).items.push(item);
  });
  return [...categories.values()]
    .sort((a, b) => a.order - b.order || a.label.localeCompare(b.label, 'zh-CN'));
}

selectField = function (name, label, options, extra = '') {
  if (name !== 'template_type') return emailTemplateBaseSelectField(name, label, options, extra);
  const optionMap = new Map(options.map(option => [String(option[0]), option]));
  const items = (cache.emailTemplates || [])
    .filter(item => optionMap.has(String(item.template_type)) && item.enabled !== false && !item.deleted);
  if (!items.length) return emailTemplateBaseSelectField(name, label, options, extra);
  const full = /(^|\s)full(\s|$)/.test(extra);
  const attrs = extra.replace(/(^|\s)full(?=\s|$)/g, ' ').trim();
  const required = /(^|\s)required(\s|$)/.test(attrs);
  const id = formFieldId(name);
  const grouped = emailTemplateCategories(items).map(category => {
    const categoryOptions = category.items.map(item => {
      const option = optionMap.get(String(item.template_type));
      const suffix = item.is_system ? '' : ' · 自定义';
      return `<option value="${esc(option[0])}">${esc(option[1])}${suffix}</option>`;
    }).join('');
    return `<optgroup label="${esc(category.label)}">${categoryOptions}</optgroup>`;
  }).join('');
  return `<div class="field ${full ? 'full' : ''}" data-field-name="${esc(name)}"><label for="${id}">${esc(label)}${required ? ' *' : ''}</label><select id="${id}" name="${esc(name)}" ${attrs}>${grouped}</select><small class="field-hint">模板已按业务用途分类；“自定义”模板由管理员维护。</small></div>`;
};

function emailTemplateLibraryHtml(items) {
  const admin = currentUser?.role === 'admin';
  const groups = emailTemplateCategories(items);
  const content = groups.map(category => {
    const cards = category.items.map(item => {
      const state = item.deleted
        ? '<span class="badge status-red">已删除</span>'
        : (item.enabled ? '<span class="badge status-green">启用</span>' : '<span class="badge status-gray">停用</span>');
      let actions = '';
      if (admin && !item.is_system) {
        if (item.deleted) {
          actions = `<button class="ghost tiny" type="button" onclick="restoreEmailTemplate('${esc(item.template_type)}')">恢复</button>`;
        } else {
          actions = `<button class="ghost tiny" type="button" onclick="openEmailTemplateEditor('${esc(item.template_type)}')">编辑</button> <button class="ghost tiny" type="button" onclick="setEmailTemplateEnabled('${esc(item.template_type)}',${item.enabled ? 'false' : 'true'})">${item.enabled ? '停用' : '启用'}</button> <button class="danger tiny" type="button" onclick="deleteEmailTemplate('${esc(item.template_type)}')">删除</button>`;
        }
      }
      const placeholders = (item.used_placeholders || []).map(key => `<code>{${esc(key)}}</code>`).join(' ');
      return `<article class="email-template-card ${item.deleted ? 'is-deleted' : ''}"><div class="email-template-card-head"><div><small>${item.is_system ? '系统模板' : '自定义模板'}</small><strong>${esc(item.name)}</strong></div><div>${state}</div></div><p>${esc(item.subject)}</p><div class="email-template-meta"><span>${placeholders || '无动态占位符'}</span><span>${actions}</span></div></article>`;
    }).join('');
    return `<section class="email-template-category"><div class="email-template-category-title"><strong>${esc(category.label)}</strong><small>${category.items.length} 个模板</small></div><div class="email-template-grid">${cards}</div></section>`;
  }).join('') || '<div class="empty">暂无可用邮件模板</div>';
  return `<section class="panel email-template-library" aria-labelledby="email-template-library-title"><div class="panel-title"><div><p class="eyebrow">TEMPLATE LIBRARY</p><h3 id="email-template-library-title">邮件模板库</h3><small class="muted">系统模板保证关键业务措辞；自定义模板可用于门店自己的通知场景。</small></div>${admin ? '<button class="primary" type="button" onclick="openEmailTemplateEditor()">新增自定义模板</button>' : ''}</div>${content}</section>`;
}

async function refreshEmailTemplateLibrary() {
  const query = currentUser?.role === 'admin' ? '?include_disabled=true&include_deleted=true' : '';
  emailTemplateLibraryItems = await api(`/api/email/templates${query}`);
  cache.emailTemplates = emailTemplateLibraryItems.filter(item => item.enabled !== false && !item.deleted);
  return emailTemplateLibraryItems;
}

const emailTemplateBaseEmailsRenderer = render.emails;
render.emails = async () => {
  await emailTemplateBaseEmailsRenderer();
  const items = await refreshEmailTemplateLibrary();
  const status = $('#content .email-status');
  status?.insertAdjacentHTML('afterend', emailTemplateLibraryHtml(items));
};

function emailTemplateCategoryOptions(items) {
  const seen = new Map();
  items.forEach(item => {
    const key = item.category || 'general';
    if (!seen.has(key)) seen.set(key, [key, item.category_label || '通用通知', Number(item.category_order ?? 999)]);
  });
  return [...seen.values()].sort((a, b) => a[2] - b[2]).map(([key, label]) => [key, label]);
}

function insertEmailTemplateToken(token) {
  const form = $('#modal-form');
  const target = form?.querySelector('[data-email-template-target="active"]')
    || form?.elements?.body
    || form?.elements?.subject;
  if (!target) return;
  const start = target.selectionStart ?? target.value.length;
  const end = target.selectionEnd ?? start;
  target.setRangeText(token, start, end, 'end');
  target.dispatchEvent(new Event('input', { bubbles: true }));
  target.focus();
}

async function openEmailTemplateEditor(templateType = null) {
  if (currentUser?.role !== 'admin') return toast('只有管理员可以管理邮件模板', true);
  if (!emailTemplateLibraryItems.length) await refreshEmailTemplateLibrary();
  const template = templateType ? emailTemplateLibraryItems.find(item => item.template_type === templateType) : null;
  if (templateType && (!template || template.is_system || template.deleted)) return toast('该模板当前不可编辑', true);
  const source = template || emailTemplateLibraryItems[0];
  const placeholders = source?.allowed_placeholders || [];
  const tokenButtons = placeholders.map(item => `<button class="template-token" type="button" title="插入${esc(item.label)}" onclick="insertEmailTemplateToken('${esc(item.token)}')"><code>${esc(item.token)}</code><span>${esc(item.label)}</span></button>`).join('');
  const categoryOptions = emailTemplateCategoryOptions(emailTemplateLibraryItems);
  const body = field('name', '模板名称', 'text', 'full required maxlength="120" placeholder="例如：门店到货提醒"')
    + selectField('category', '模板分类', categoryOptions, 'required')
    + selectField('enabled', '使用状态', [['true', '启用，可在写邮件时选择'], ['false', '停用，暂不出现在写邮件列表']])
    + field('subject', '邮件主题', 'text', 'full required maxlength="300" placeholder="可插入下方占位符"')
    + field('body', '邮件正文', 'textarea', 'full required rows="12" maxlength="20000" placeholder="填写发送给客户的正文"')
    + `<section class="field full template-token-panel"><label>可用占位符</label><p>先点选主题或正文中的插入位置，再点击占位符。仅允许下列简单占位符。</p><div>${tokenButtons}</div></section>`;
  openModal(template ? `编辑自定义模板 · ${template.name}` : '新增自定义邮件模板', body, async formData => {
    const values = requiredFormValues(formData, { name: '模板名称', category: '模板分类', subject: '邮件主题', body: '邮件正文' });
    values.enabled = values.enabled === 'true';
    const endpoint = template ? `/api/email/templates/${template.template_type}` : '/api/email/templates';
    const method = template ? 'PATCH' : 'POST';
    await api(endpoint, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
    return { afterClose: () => render.emails() };
  }, { variant: 'wide', submitLabel: template ? '保存模板' : '创建模板', busyLabel: '正在校验模板…' });
  const form = $('#modal-form');
  if (template) {
    form.elements.name.value = template.name;
    form.elements.category.value = template.category;
    form.elements.enabled.value = String(template.enabled);
    form.elements.subject.value = template.subject;
    form.elements.body.value = template.body;
  }
  ['subject', 'body'].forEach(name => {
    const control = form.elements[name];
    control?.addEventListener('focus', () => {
      form.querySelectorAll('[data-email-template-target]').forEach(item => item.removeAttribute('data-email-template-target'));
      control.setAttribute('data-email-template-target', 'active');
    });
  });
  form.elements.body?.setAttribute('data-email-template-target', 'active');
}

async function setEmailTemplateEnabled(templateType, enabled) {
  if (currentUser?.role !== 'admin') return toast('只有管理员可以管理邮件模板', true);
  try {
    await api(`/api/email/templates/${templateType}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) });
    toast(enabled ? '模板已启用' : '模板已停用');
    await render.emails();
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteEmailTemplate(templateType) {
  const item = emailTemplateLibraryItems.find(template => template.template_type === templateType);
  if (!item || currentUser?.role !== 'admin') return toast('只有管理员可以删除自定义模板', true);
  if (!confirm(`确认删除自定义模板“${item.name}”？\n\n已发送邮件仍保留当时的主题和正文快照，模板可由管理员恢复。`)) return;
  try {
    await api(`/api/email/templates/${templateType}`, { method: 'DELETE' });
    toast('模板已删除，可在模板库中恢复');
    await render.emails();
  } catch (error) {
    toast(error.message, true);
  }
}

async function restoreEmailTemplate(templateType) {
  if (currentUser?.role !== 'admin') return toast('只有管理员可以恢复自定义模板', true);
  try {
    await api(`/api/email/templates/${templateType}/restore`, { method: 'POST' });
    toast('模板已恢复');
    await render.emails();
  } catch (error) {
    toast(error.message, true);
  }
}
