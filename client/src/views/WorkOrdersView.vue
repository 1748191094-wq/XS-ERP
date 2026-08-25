<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ChevronRight, Copy, PackageCheck, Recycle, RefreshCw, Repeat2, ShoppingBag, Wrench } from '@lucide/vue'
import PageHeader from '@/components/PageHeader.vue'
import { api, unwrap } from '@/api/client'
import type { ClientWorkItem } from '@/types'

const items = ref<ClientWorkItem[]>([])
const filter = ref<'all'|'repair'|'retail'|'recycle'|'replacement'>('all')
const selected = ref<any>(null)
const selectedType = ref<ClientWorkItem['type']|null>(null)
const loading = ref(false)
const operationMessage = ref('')
const visibleItems = computed(() => filter.value === 'all' ? items.value : items.value.filter(item => item.type === filter.value))
const typeIcons = { repair:Wrench, retail:ShoppingBag, recycle:Recycle, replacement:Repeat2 }
const filters: Array<{v:'all'|'repair'|'retail'|'recycle'|'replacement';l:string}> = [{v:'all',l:'全部'},{v:'repair',l:'维修'},{v:'retail',l:'商城'},{v:'recycle',l:'回收'},{v:'replacement',l:'置换'}]
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

async function load(){ items.value = unwrap<ClientWorkItem[]>(await api.get('/work-items')) }
async function open(item:ClientWorkItem){
  loading.value=true; operationMessage.value=''; selectedType.value=item.type
  try{
    const path = item.type==='repair' ? `/repair/${item.id}` : item.type==='retail' ? `/orders/${item.id}` : item.type==='replacement' ? `/replacement/${item.id}` : `/recycle/${item.id}`
    selected.value=unwrap(await api.get(path))
  } finally { loading.value=false }
}
function close(){selected.value=null;selectedType.value=null}
async function refreshSelected(){
  if(!selected.value||!selectedType.value)return
  const item=items.value.find(row=>row.type===selectedType.value&&row.id===selected.value.id)
  if(item)await open(item)
}
async function quoteDecision(decision:'accepted'|'rejected'){
  if(!selected.value)return
  selected.value=unwrap(await api.post(`/repair/${selected.value.id}/quote-decision`,{decision})); await load()
}
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
function time(value:string){return new Date(value).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}
onMounted(load)
</script>

<template>
  <PageHeader title="订单与工单" subtitle="维修、商城、回收和置换记录按时间统一呈现"/>
  <div class="work-filter" role="tablist">
    <button v-for="item in filters" :key="item.v" :class="{active:filter===item.v}" @click="filter=item.v">{{item.l}}</button>
  </div>

  <div v-if="loading" class="empty-state section">正在加载详情…</div>
  <div v-else-if="selected" class="work-detail section">
    <button class="button secondary" @click="close">返回全部记录</button>

    <template v-if="selectedType==='repair'">
      <div class="two-column section"><section class="form-card"><p class="eyebrow">维修工单</p><h2>{{selected.device.brand}} {{selected.device.model}}</h2><p class="muted">{{selected.order_no}}</p><span class="pill blue">{{selected.status_label}}</span><p>{{selected.fault_description}}</p><div class="timeline section"><div v-for="item in selected.timeline" :key="item.time" class="timeline-item"><strong>{{item.label}}</strong><p class="muted">{{time(item.time)}}</p></div></div></section><aside v-if="selected.current_quote" class="form-card"><p class="eyebrow">当前有效报价 · V{{selected.current_quote.version}}</p><h2>¥{{selected.current_quote.total_amount}}</h2><p class="muted">{{selected.current_quote.assessment_result}}</p><div class="list"><div v-for="item in selected.current_quote.items" :key="item.id" class="list-item"><div class="list-item-main"><h3>{{item.name}}</h3><p>{{item.quantity}} × ¥{{item.unit_price}}</p></div><strong>¥{{item.amount}}</strong></div></div><div v-if="!['confirmed','rejected'].includes(selected.current_quote.status)" class="button-row section"><button class="button" @click="quoteDecision('accepted')">接受报价</button><button class="button danger" @click="quoteDecision('rejected')">拒绝报价</button></div></aside></div>
    </template>

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

  <div v-else class="work-timeline section">
    <button v-for="item in visibleItems" :key="item.key" class="work-row" @click="open(item)">
      <span :class="['work-icon',item.type]"><component :is="typeIcons[item.type]" :size="20"/></span>
      <span class="work-main"><span class="work-meta"><strong>{{item.type_label}}</strong><small>{{time(item.updated_at)}}</small></span><b>{{item.title}}</b><small>{{item.number}} · {{item.summary}}</small></span>
      <span class="work-side"><span class="pill blue">{{item.status_label}}</span><strong v-if="item.amount">¥{{item.amount}}</strong></span>
      <ChevronRight :size="19" class="muted"/>
    </button>
    <div v-if="!visibleItems.length" class="empty-state">这里还没有记录。</div>
  </div>
</template>
