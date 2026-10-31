<script setup lang="ts">
import { computed, watch, onBeforeUnmount, nextTick, ref } from 'vue'
import { ChevronRight, Copy, PackageCheck, Recycle, RefreshCw, Repeat2, ShoppingBag, Wrench } from '@lucide/vue'
import { useRoute, useRouter } from 'vue-router'
import RepairProgress from '@/components/service/RepairProgress.vue'
import PageHeader from '@/components/PageHeader.vue'
import { api, unwrap } from '@/api/client'
import type { ClientWorkItem } from '@/types'

const route=useRoute(), router=useRouter()
const directRepair=computed(()=>route.query.repair!==undefined)
type WorkTarget=Pick<ClientWorkItem,'id'|'type'|'key'>
let listSequence=0, navigationSequence=0
const items = ref<ClientWorkItem[]>([])
const filter = ref<'all'|'repair'|'retail'|'recycle'|'replacement'>('all')
const selected = ref<any>(null)
const selectedType = ref<ClientWorkItem['type']|null>(null)
const loading = ref(false)
const operationMessage = ref('')
const search = ref('')
const statusFilter = ref('all')
const listLoading = ref(true)
const error = ref('')
const busy = ref(false)
let detailSequence = 0
let lastItem: WorkTarget | null = null
let returnKey = ''
const statuses = [{v:'all',l:'全部案例'},{v:'pending',l:'待处理'},{v:'active',l:'进行中'},{v:'history',l:'历史案例'}]
function group(item:ClientWorkItem) {
  if (['completed','cancelled','closed','resolved','refunded','rejected'].includes(item.status)) return 'history'
  if ((item.type==='repair' && ['quoted','awaiting_device'].includes(item.status)) || (item.type==='retail' && item.status==='pending_payment') || (item.type==='recycle' && ['quoted','pending_customer_confirmation'].includes(item.status)) || item.status==='waiting_customer') return 'pending'
  return 'active'
}
const visibleItems = computed(() => items.value.filter(item =>
  (filter.value==='all'||item.type===filter.value) &&
  (statusFilter.value==='all'||group(item)===statusFilter.value) &&
  `${item.number} ${item.title}`.toLowerCase().includes(search.value.trim().toLowerCase())))
const typeIcons = { repair:Wrench, retail:ShoppingBag, recycle:Recycle, replacement:Repeat2 }
const filters: Array<{v:'all'|'repair'|'retail'|'recycle'|'replacement';l:string}> = [{v:'all',l:'全部'},{v:'repair',l:'维修'},{v:'retail',l:'商城'},{v:'recycle',l:'回收'},{v:'replacement',l:'置换'}]
const hasFilters=computed(()=>Boolean(search.value.trim())||filter.value!=='all'||statusFilter.value!=='all')
const scopeLabel=computed(()=>`${filters.find(item=>item.v===filter.value)?.l} · ${statuses.find(item=>item.v===statusFilter.value)?.l}`)
async function resetFilters(){
  search.value='';filter.value='all';statusFilter.value='all'
  await nextTick();document.querySelector<HTMLInputElement>('.service-search input')?.focus()
}
async function clearSearch(){search.value='';await nextTick();document.querySelector<HTMLInputElement>('.service-search input')?.focus()}
const normalOrderFlow = [
  {key:'pending_payment',label:'订单已提交',description:'等待门店核对库存和收款'},
  {key:'paid',label:'款项已确认',description:'门店已核验支付信息'},
  {key:'processing',label:'正在备货',description:'商品正在打包出库'},
  {key:'shipped',label:'商品已发出',description:'可使用物流单号查询运输'},
  {key:'completed',label:'订单已完成',description:'本次订单流转结束'},
]
const refundOrderFlow = [
  {key:'paid',label:'订单已付款',description:'款项已经确认'},
  {key:'refunding',label:'退款处理中',description:'门店正在处理退款'},
  {key:'refunded',label:'退款已完成',description:'退款流程已经结束'},
]
const retailFlow = computed(()=>{
  if(selectedType.value!=='retail'||!selected.value)return []
  const status=String(selected.value.status)
  const steps=status==='cancelled'
    ? [{key:'pending_payment',label:'订单已提交',description:'订单曾进入门店处理队列'},{key:'cancelled',label:'订单已取消',description:'库存锁定已释放'}]
    : ['refunding','refunded'].includes(status) ? refundOrderFlow : normalOrderFlow
  const currentIndex=Math.max(0,steps.findIndex(step=>step.key===status))
  return steps.map((step,index)=>({...step,state:index<currentIndex?'complete':index===currentIndex?'current':'upcoming'}))
})
const orderAddress = computed(()=>{
  const address=selectedType.value==='retail'?selected.value?.address:null
  if(!address)return ''
  return [address.province,address.city,address.district,address.detail].filter(Boolean).join('')
})

async function load(){
  const sequence=++listSequence
  listLoading.value=true; error.value=''
  try { const data=unwrap<ClientWorkItem[]>(await api.get('/work-items')); if(sequence===listSequence)items.value=data }
  catch(e) { if(sequence===listSequence)error.value=e instanceof Error?e.message:'读取失败，请重试' }
  finally {if(sequence===listSequence)listLoading.value=false}
}
async function open(item:WorkTarget){
  if(busy.value)return
  lastItem=item
  if(!selected.value)returnKey=item.key
  const sequence=++detailSequence
  loading.value=true; error.value=''; operationMessage.value=''; selectedType.value=item.type
  try{
    const path = item.type==='repair' ? `/repair/${item.id}` : item.type==='retail' ? `/orders/${item.id}` : item.type==='replacement' ? `/replacement/${item.id}` : `/recycle/${item.id}`
    const data=unwrap(await api.get(path))
    if(sequence===detailSequence)selected.value=data
  } catch(e) { if(sequence===detailSequence)error.value=e instanceof Error?e.message:'读取失败，请重试' }
  finally { if(sequence===detailSequence){loading.value=false;await nextTick();document.querySelector<HTMLElement>('.progress-title h1')?.focus()} }
}
function close(){if(busy.value)return;if(directRepair.value){const query={...route.query};delete query.repair;void router.replace({query});return;}detailSequence++;loading.value=false;selected.value=null;selectedType.value=null;error.value='';lastItem=null;nextTick(()=>document.querySelector<HTMLElement>(`[data-work-key="${returnKey}"]`)?.focus())}
async function refreshSelected(){
  if(!selected.value||!selectedType.value)return
  const item=items.value.find(row=>row.type===selectedType.value&&row.id===selected.value.id)
  const target=item||lastItem
  if(target)await open(target)
}
async function quoteDecision(decision:'accepted'|'rejected'){
  if(!selected.value||busy.value)return
  busy.value=true;error.value=''
  const sequence=detailSequence
  try {
    const data=unwrap(await api.post(`/repair/${selected.value.id}/quote-decision`,{decision}))
    if(sequence===detailSequence){selected.value=data;await load()}
  } catch(e) { if(sequence===detailSequence)error.value=e instanceof Error?e.message:'提交失败，请重试' }
  finally {if(sequence===detailSequence)busy.value=false}
}
onBeforeUnmount(()=>{detailSequence++;listSequence++;navigationSequence++})
async function recycleDecision(decision:'accepted'|'rejected'){
  if(!selected.value)return
  selected.value=unwrap(await api.post(`/recycle/${selected.value.id}/decision`,{decision})); await load()
}
async function cancelOrder(){
  if(!selected.value)return
  if(!window.confirm(`确认取消订单 ${selected.value.order_no}？`))return
  selected.value=unwrap(await api.post(`/orders/${selected.value.id}/cancel`)); await load()
}
async function copyTracking(){
  const tracking=String(selected.value?.tracking_no||'')
  if(!tracking)return
  try{await navigator.clipboard.writeText(tracking);operationMessage.value='物流单号已复制'}
  catch{operationMessage.value=`物流单号：${tracking}`}
}
function time(value:string){return new Date(value).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})}
watch(()=>route.query.repair,async value=>{
  const navigation=++navigationSequence
  detailSequence++;listSequence++;loading.value=false;busy.value=false
  selected.value=null;selectedType.value=null;lastItem=null;error.value=''
  if(value===undefined){
    await load()
    if(navigation!==navigationSequence)return
    await nextTick()
    if(returnKey)(document.querySelector<HTMLElement>(`[data-work-key="${returnKey}"]`)||document.querySelector<HTMLElement>('.service-search input'))?.focus()
    return
  }
  listLoading.value=false
  if(typeof value!=='string'||! /^[1-9]\d*$/.test(value)||!Number.isSafeInteger(Number(value))){error.value='工单链接无效，请返回服务列表查找。';return}
  await open({id:Number(value),type:'repair',key:`repair:${value}`})
},{immediate:true})
</script>

<template>
  <div class="service-page">
  <template v-if="!directRepair && !selected && !loading">
    <PageHeader title="服务进度" subtitle="查看当前进度，了解下一步需要做什么"/>
    <div class="service-search"><label for="service-search-input">查找我的工单</label><div class="service-search-row"><input id="service-search-input" v-model="search" type="search" placeholder="输入工单号或产品名称" aria-label="搜索工单号或产品名称"/><button v-if="search" type="button" class="button secondary" @click="clearSearch">清除搜索</button></div></div>
    <div class="service-types" aria-label="服务类型"><button v-for="item in filters" :key="item.v" :class="{active:filter===item.v}" :aria-pressed="filter===item.v" @click="filter=item.v">{{item.l}}</button></div>
    <div class="service-tabs" aria-label="案例状态"><button v-for="tab in statuses" :key="tab.v" :class="{active:statusFilter===tab.v}" :aria-pressed="statusFilter===tab.v" @click="statusFilter=tab.v">{{tab.l}}</button></div>
    <div v-if="!listLoading && !error" class="service-results-summary"><p role="status">{{visibleItems.length}} 条结果 · {{scopeLabel}}</p><button v-if="hasFilters && visibleItems.length" class="button secondary" @click="resetFilters">重置全部条件</button></div>
    <p v-if="statusFilter==='pending'" class="muted">需要您确认报价、送出设备或完成门店要求的操作。</p>
  </template>
  <template v-if="directRepair && !selected">
    <PageHeader title="维修服务详情" subtitle="查看这笔维修的当前进度与下一步操作"/>
    <button class="button secondary" :disabled="busy" @click="close">← 返回服务列表</button>
  </template>
  <div v-if="error" class="inline-notice" role="alert">{{error}} <button v-if="lastItem || !directRepair" class="button secondary" :disabled="busy" @click="lastItem?open(lastItem):load()">重新读取</button></div>
  <div v-if="loading" class="empty-state section" role="status">正在加载详情…</div>
  <div v-else-if="selected" class="work-detail section">
    <button class="button secondary" :disabled="busy" @click="close">← 返回服务列表</button>

    <RepairProgress v-if="selectedType==='repair'" :key="selected.id" :order="selected" :busy="busy" @refresh="refreshSelected" @decision="quoteDecision"/>

    <section v-else-if="selectedType==='retail'" class="form-card section retail-order-detail">
      <div class="section-heading"><div><p class="eyebrow">商城订单</p><h2>{{selected.order_no}}</h2><p class="muted">提交于 {{time(selected.created_at)}}</p></div><div class="order-heading-actions"><span class="pill blue">{{selected.status_label}}</span><button class="icon-button" title="刷新订单" aria-label="刷新订单" @click="refreshSelected"><RefreshCw :size="17"/></button></div></div>
      <section class="order-flow" aria-label="订单流转情况">
        <div v-for="step in retailFlow" :key="step.key" :class="['order-flow-step',step.state]">
          <span class="order-flow-dot" aria-hidden="true"></span><div><strong>{{step.label}}</strong><small>{{step.description}}</small></div>
        </div>
      </section>
      <div class="order-info-grid">
        <div><small>配送方式</small><strong>{{selected.delivery_method==='shipping'?'快递配送':'到店自取'}}</strong></div>
        <div><small>支付方式</small><strong>{{selected.payment_provider==='manual'?'门店人工确认':selected.payment_provider}}</strong></div>
        <div><small>收件人</small><strong>{{selected.address?.recipient_name || '-'}}</strong></div>
        <div><small>联系电话</small><strong>{{selected.address?.phone || '-'}}</strong></div>
      </div>
      <div class="list"><article v-for="item in selected.items" :key="item.id" class="list-item"><PackageCheck/><div class="list-item-main"><h3>{{item.product_name}}</h3><p>{{item.sku_name}} · {{item.quantity}} 件</p></div><strong>¥{{item.amount}}</strong></article></div>
      <div class="work-total"><span>订单合计</span><strong>¥{{selected.total_amount}}</strong></div>
      <div v-if="selected.tracking_no" class="tracking-card"><div><small>物流单号</small><strong>{{selected.tracking_no}}</strong></div><button class="button secondary" @click="copyTracking"><Copy :size="16"/>复制</button></div>
      <div v-if="orderAddress" class="order-address"><small>收货地址</small><p>{{orderAddress}}</p></div>
      <p v-if="operationMessage" class="inline-notice">{{operationMessage}}</p>
      <div v-if="selected.status==='pending_payment'" class="sticky-actions"><button class="button danger" @click="cancelOrder">取消订单</button><small class="muted">取消后会释放该订单锁定的库存。</small></div>
    </section>

    <section v-else-if="selectedType==='recycle'" class="form-card section">
      <p class="eyebrow">回收申请</p><div class="section-heading"><div><h2>{{selected.request_no}}</h2><p class="muted">{{time(selected.created_at)}}</p></div><span class="pill blue">{{selected.status_label}}</span></div>
      <div class="work-price-grid"><div><span>最高回收参考价</span><strong>¥{{selected.maximum_price || selected.reference_max}}</strong></div><div v-if="selected.staff_quote"><span>门店正式报价</span><strong>¥{{selected.staff_quote}}</strong></div></div>
      <div v-if="selected.contact_name || selected.contact_phone" class="order-info-grid section">
        <div><small>联系人</small><strong>{{selected.contact_name || '-'}}</strong></div>
        <div><small>联系电话</small><strong>{{selected.contact_phone || '-'}}</strong></div>
        <div><small>微信号</small><strong>{{selected.contact_wechat || '-'}}</strong></div>
      </div>
      <div v-if="selected.device_condition" class="order-address"><small>设备情况</small><p>{{selected.device_condition}}</p></div>
      <p class="muted">{{selected.notice}}</p>
      <div v-if="selected.staff_quote&&['quoted','pending_customer_confirmation'].includes(selected.status)" class="button-row"><button class="button" @click="recycleDecision('accepted')">接受报价</button><button class="button danger" @click="recycleDecision('rejected')">拒绝报价</button></div>
    </section>

    <section v-else class="form-card section">
      <p class="eyebrow">置换工单</p><div class="section-heading"><div><h2>{{selected.ticket_no}}</h2><p class="muted">提交于 {{time(selected.created_at)}}</p></div><span class="pill blue">{{selected.status_label}}</span></div>
      <div class="work-price-grid"><div><span>旧机型</span><strong>{{selected.old_model}}</strong></div><div><span>需求机型</span><strong>{{selected.desired_model}}</strong></div></div>
      <div class="order-info-grid"><div><small>联系人</small><strong>{{selected.contact_name}}</strong></div><div><small>联系电话</small><strong>{{selected.contact_phone}}</strong></div></div>
      <div v-if="selected.address" class="order-address"><small>联系地址</small><p>{{[selected.address.province,selected.address.city,selected.address.district,selected.address.detail].filter(Boolean).join('')}}</p></div>
      <div v-if="selected.notes" class="order-address"><small>补充说明</small><p>{{selected.notes}}</p></div><p class="replacement-promise section">{{selected.notice}}</p>
    </section>
  </div>

  <div v-else-if="!directRepair && listLoading" class="empty-state section" role="status">正在读取服务记录…</div>
  <div v-else-if="!directRepair" class="service-cases">
    <button v-for="item in visibleItems" :key="item.key" class="service-case" :data-work-key="item.key" @click="open(item)">
      <span class="case-content"><span class="case-icon"><component :is="typeIcons[item.type]" :size="28" :stroke-width="1.4"/></span><span class="case-info"><strong>{{item.title}}</strong><span>案例类型：{{item.type_label}}</span><span>工单号：{{item.number}}</span><span>申请时间：{{time(item.created_at)}}</span></span></span>
      <span class="case-footer"><span>{{group(item)==='pending'?'需要您处理':group(item)==='history'?'服务已结束':'服务处理中'}}</span><span>{{item.status_label}} <ChevronRight :size="17"/></span></span>
    </button>
    <section v-if="!visibleItems.length && !error" class="empty-state service-empty" aria-labelledby="service-empty-title">
      <template v-if="items.length">
        <h2 id="service-empty-title">没有符合当前条件的服务记录</h2>
        <p>可以修改工单号或产品名称，也可以重置服务类型和状态，查看全部记录。</p>
        <button class="button" @click="resetFilters">重置全部条件</button>
      </template>
      <template v-else>
        <h2 id="service-empty-title">当前账号还没有服务记录</h2>
        <p>提交维修申请后，可以在这里查看进度与报价。已有线下工单但未显示？请联系门店核对账号关联。</p>
        <RouterLink class="button" to="/repair/new">申请维修</RouterLink>
      </template>
    </section>
  </div>
  </div>
</template>
<style scoped>
.service-search-row{display:flex;align-items:center;gap:8px}.service-search-row input{min-width:0;flex:1}.service-search-row button{flex-shrink:0;padding-inline:12px}.service-results-summary{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-top:16px}.service-results-summary p{margin:0;color:var(--muted);font-size:14px}.service-empty h2{color:var(--ink);font-size:20px;margin:0 0 10px;line-height:1.4}.service-empty p{max-width:540px;margin:0 auto 22px;line-height:1.7;color:var(--muted)}.service-types button{min-height:44px}.service-empty{background:var(--surface);border-style:solid}
.service-page{--text:var(--ink);--border:var(--line);--surface:var(--card);--surface-soft:#f5f5f5;max-width:1000px;margin:0 auto}.service-search{display:block;max-width:480px;margin:28px 0}.service-search>label{display:block;font-size:13px;color:var(--muted,#6b7280);margin-bottom:9px}.service-search input{width:100%;padding:14px 16px;border:1px solid var(--border,#d8dce2);border-radius:10px;background:var(--surface,#fff);color:inherit;font:inherit}.service-types{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}.service-types button{border:0;background:transparent;padding:9px 17px;color:var(--muted,#646b75);font:inherit;cursor:pointer;border-radius:20px}.service-types button.active{background:var(--blue-soft);color:var(--ink);font-weight:650}.service-tabs{display:flex;gap:38px;border-bottom:1px solid var(--border,#e8e9eb);margin-top:26px}.service-tabs button{font:inherit;font-weight:600;white-space:nowrap;padding:16px 0 18px;background:none;border:0;border-bottom:3px solid transparent;color:var(--muted,#6b7280);cursor:pointer}.service-tabs button.active{color:var(--text,#1c1e21);border-bottom-color:currentColor}.service-cases{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin-top:30px}.service-case{padding:0;text-align:left;border:1px solid transparent;border-radius:15px;background:var(--surface-soft,#f5f5f5);color:inherit;overflow:hidden;font:inherit;cursor:pointer;transition:border-color .2s}.service-case:hover{border-color:#bdd6ef}.case-content{display:flex;gap:22px;padding:28px 24px}.case-icon{flex-shrink:0;color:var(--muted,#67717e);padding-top:5px}.case-info{display:flex;flex-direction:column;gap:10px;min-width:0}.case-info strong{font-size:21px;font-weight:650;margin-bottom:6px;overflow-wrap:anywhere}.case-info>span{font-size:14px;line-height:1.6;overflow-wrap:anywhere}.case-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:17px 24px;border-top:1px solid var(--border,#e4e4e6)}.case-footer>span:first-child{font-size:12px;color:var(--muted,#6b7280)}.case-footer>span:last-child{display:flex;align-items:center;gap:7px;color:#0670db;font-size:15px}.work-detail{margin-top:0}.service-cases>.empty-state{grid-column:1/-1}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid #80baff;outline-offset:3px}
@media(max-width:700px){.service-cases{grid-template-columns:1fr;gap:14px;margin-top:24px}.service-tabs{gap:0;justify-content:space-between}.service-tabs button{font-size:15px}.case-content{padding:24px 20px;gap:18px}.case-footer{padding:16px 20px}.case-info strong{font-size:20px}.service-types{gap:2px}.service-types button{padding:8px 13px}}
:global(html[data-theme="dark"] .service-page){--surface-soft:#16181c}
</style>
