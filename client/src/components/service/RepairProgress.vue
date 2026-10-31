<script setup lang="ts">
import { computed, ref } from 'vue'
import { Check, Copy, Download, RefreshCw, ChevronRight } from '@lucide/vue'
import { api } from '@/api/client'
const props = defineProps<{ order: any; busy: boolean }>()
const emit = defineEmits<{ refresh: []; decision: [value: 'accepted'|'rejected'] }>()
const copyMessage = ref('')
const downloadMessage = ref('')
const downloadFailed = ref(false)
const downloading = ref(false)
const quote = computed(() => props.order.current_quote)
const actionable = computed(() => quote.value && !['confirmed','rejected'].includes(quote.value.status) && props.order.status === 'quoted')
const guidance = computed(() => ({
  pending_inspection:'申请已提交，服务人员将核对设备与故障信息。',
  awaiting_device:'请按与门店确认的方式送修或寄出设备。',
  received:'设备已收到，接下来将进行故障检测。',
  inspecting:'正在检测设备，请等待检测结果和维修报价。',
  pending_quote:'正在核对维修方案，更新后可在此查看报价。',
  quoted: actionable.value ? '请核对检测结果和费用，再确认是否维修。' : '报价已处理，请等待门店更新维修安排。',
  customer_confirmed:'您已确认报价，请等待门店安排维修。确认报价不代表已付款。',
  repairing:'设备正在维修，完成后将进行质量检测。',
  pending_test:'正在检查维修效果，检测完成后将安排交付。',
  pending_shipping:'设备待交付，请与门店确认取件或寄回安排。',
  shipping:'设备已寄出，请关注物流；单号可向门店确认。',
  completed:'本次服务已完成，您仍可查看进度与报价记录。',
  cancelled:'本次服务已取消，如有设备退回事项请与门店确认。',
}[props.order.status as string] || '服务进度更新后将在此显示。'))
function time(value: string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12:false }) : '尚未记录' }
function mask(value: string) { return value && value.length > 5 ? `${value.slice(0,3)}****${value.slice(-2)}` : '****' }
async function copy(value: string) {
  try { await navigator.clipboard.writeText(value); copyMessage.value='工单号已复制' }
  catch { copyMessage.value=`自动复制不可用，请选择并复制工单号：${value}` }
}
async function download() {
  if (!quote.value || downloading.value) return
  downloading.value=true; downloadMessage.value=''; downloadFailed.value=false
  try {
    const response=await api.get(`/repair/quotes/${quote.value.id}/pdf`, { responseType:'blob' })
    const url=URL.createObjectURL(response.data)
    const link=document.createElement('a'); link.href=url; link.download=`报价单-${props.order.order_no}.pdf`; link.click()
    setTimeout(()=>URL.revokeObjectURL(url),10000)
    downloadMessage.value='已请求浏览器保存报价单，请在下载列表中查看。'
  } catch { downloadFailed.value=true; downloadMessage.value='报价单暂时无法下载，页面上的报价仍可查看。请重试；若持续失败，请联系门店。' }
  finally { downloading.value=false }
}
function decide(value:'accepted'|'rejected') {
  if (props.busy || !actionable.value) return
  if (window.confirm(value==='accepted' ? `确认接受 ¥${quote.value.total_amount} 的报价？此操作不扣款。` : '确认拒绝当前报价？门店将重新处理维修方案。')) emit('decision',value)
}
</script>
<template>
  <div class="repair-progress">
    <section class="progress-hero">
      <div class="progress-kicker">维修服务 · {{ order.device.brand }} {{ order.device.model }}</div>
      <div class="progress-title"><h1 tabindex="-1">{{ order.status_label }}</h1><button class="icon-button" :disabled="busy" aria-label="刷新维修进度" @click="emit('refresh')"><RefreshCw :size="19"/></button></div>
      <p>{{ guidance }}</p>
      <a v-if="actionable" href="#repair-quote" class="service-link">查看报价并确认 <ChevronRight :size="17"/></a>
    </section>
    <section class="progress-section">
      <h2>进度详情</h2>
      <ol v-if="order.timeline.length" class="repair-timeline">
        <li v-for="(step,index) in order.timeline" :key="`${step.time}-${index}`" :class="{ current:index===order.timeline.length-1 }">
          <span class="repair-step-dot"><Check :size="15" v-if="Number(index) < order.timeline.length-1"/><span v-else/></span>
          <div><strong>{{ step.label }}</strong><span v-if="index===order.timeline.length-1" class="current-label">最新进度</span><time>{{ time(step.time) }}</time></div>
        </li>
      </ol>
      <p v-else class="muted">暂未记录进度，当前状态以页面上方为准。</p>
    </section>
    <section class="progress-section">
      <h2>服务信息</h2>
      <dl class="service-information">
        <div><dt>工单号</dt><dd>{{order.order_no}} <button class="copy-number" aria-label="复制工单号" @click="copy(order.order_no)"><Copy :size="16"/></button><span v-if="copyMessage" class="copy-feedback" role="status">{{copyMessage}}</span></dd></div>
        <div><dt>产品</dt><dd>{{order.device.brand}} {{order.device.model}}</dd></div>
        <div v-if="order.device.serial_number"><dt>SN 码</dt><dd>{{mask(order.device.serial_number)}}</dd></div>
        <div v-if="order.contact_name"><dt>客户姓名</dt><dd>{{order.contact_name.slice(0,1)}}**</dd></div>
        <div><dt>申请时间</dt><dd>{{time(order.created_at)}}</dd></div>
        <div v-if="order.service_mode"><dt>服务方式</dt><dd>{{order.service_mode==='shipping'?'邮寄送修':order.service_mode==='store'?'到店送修':order.service_mode}}</dd></div>
        <div><dt>故障描述</dt><dd class="preserve-lines">{{order.fault_description || '未填写'}}</dd></div>
      </dl>
    </section>
    <section id="repair-quote" class="progress-section">
      <div class="quote-heading"><h2>维修报价</h2><button v-if="quote" class="service-link" :disabled="downloading" @click="download"><Download :size="17"/>{{downloading?'正在下载…':downloadFailed?'重试下载报价单':'下载报价单'}}</button></div>
      <p v-if="downloadMessage" class="download-feedback" :class="{failed:downloadFailed}" role="status">{{downloadMessage}}</p>
      <template v-if="quote">
        <div class="quote-caption"><span>{{quote.quote_no}} · 第 {{quote.version}} 版</span><span>{{quote.status==='confirmed'?'已接受':quote.status==='rejected'?'已拒绝':'待确认'}}</span></div>
        <div v-if="quote.assessment_result" class="quote-copy"><h3>检测结论</h3><p>{{quote.assessment_result}}</p></div>
        <div v-if="quote.repair_recommendation" class="quote-copy"><h3>维修方案</h3><p>{{quote.repair_recommendation}}</p></div>
        <h3 class="quote-section-title">费用明细</h3>
        <ul class="repair-quote-items"><li v-for="item in quote.items" :key="item.id"><div><strong>{{item.name}}</strong><p v-if="item.specification">{{item.specification}}</p><small>数量 {{item.quantity}} × ¥{{item.unit_price}}</small></div><strong>¥{{item.amount}}</strong></li></ul>
        <dl class="quote-totals"><div><dt>项目小计</dt><dd>¥{{quote.subtotal}}</dd></div><div><dt>维修服务费</dt><dd>¥{{quote.labor_fee}}</dd></div><div><dt>运费</dt><dd>¥{{quote.shipping_fee}}</dd></div><div><dt>优惠</dt><dd>− ¥{{quote.discount}}</dd></div><div class="grand-total"><dt>总计</dt><dd>¥{{quote.total_amount}}</dd></div></dl>
        <div v-if="quote.customer_notice" class="quote-copy customer-notice"><h3>注意事项</h3><p>{{quote.customer_notice}}</p></div>
        <div v-if="actionable" class="quote-decision"><p>请核对费用后再确认。接受报价不会自动付款。</p><div><button class="button secondary" :disabled="busy" @click="decide('rejected')">拒绝报价</button><button class="button" :disabled="busy" @click="decide('accepted')">{{busy?'正在提交…':'接受报价'}}</button></div></div>
      </template>
      <p v-else class="muted">暂无报价。完成检测并出具报价后，您可以在这里查看和确认。</p>
    </section>
  </div>
</template>
<style scoped>
.copy-feedback{display:block;font-size:14px;line-height:1.6;margin-top:8px;overflow-wrap:anywhere}
.download-feedback{padding:14px 16px;border:1px solid var(--border);border-radius:12px;background:var(--surface-soft);line-height:1.65;margin:0 0 24px;overflow-wrap:anywhere}
.download-feedback.failed{border-color:var(--danger)}
.quote-heading{scroll-margin-top:90px}
.quote-heading .service-link,.copy-number{min-height:44px;min-width:44px}
.quote-heading .service-link{color:var(--blue)}
button:focus-visible{outline:3px solid var(--blue);outline-offset:3px}

.repair-progress{max-width:900px;margin:auto;color:var(--text,#202124)}
.progress-hero{padding:32px 0 40px}.progress-kicker{font-size:14px;color:var(--muted,#6b7280)}.progress-title{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:16px 0}.progress-title h1{font-size:38px;letter-spacing:-1px;margin:0}.progress-hero p{font-size:17px;line-height:1.8;color:var(--muted,#60656c);margin:0 0 20px}.service-link{display:inline-flex;gap:7px;align-items:center;color:#066bd6;background:none;border:0;padding:8px 0;font:inherit;cursor:pointer;text-decoration:none}.progress-section{border-top:1px solid var(--border,#e8e9eb);padding:32px 0;scroll-margin-top:90px}.progress-section h2{font-size:24px;margin:0 0 26px}.repair-timeline{list-style:none;padding:0;margin:0}.repair-timeline li{position:relative;display:flex;gap:20px;min-height:92px;padding-bottom:24px}.repair-timeline li:not(:last-child):before{content:'';position:absolute;left:15px;top:30px;bottom:0;width:2px;background:#bbd6f5}.repair-step-dot{height:32px;width:32px;flex-shrink:0;border-radius:50%;display:flex;align-items:center;justify-content:center;background:#e9f4ff;color:#0670da;z-index:1}.current .repair-step-dot{background:#0874df;color:white}.current .repair-step-dot>span{width:8px;height:8px;background:white;border-radius:50%}.repair-timeline strong{display:inline-block;padding-top:4px}.repair-timeline time{display:block;font-size:14px;color:var(--muted,#6b7280);margin-top:9px}.current-label{font-size:12px;color:#0874df;margin-left:12px}.service-information{background:var(--surface-soft,#f6f7f8);border-radius:12px;padding:24px;margin:0}.service-information>div{display:grid;grid-template-columns:100px 1fr;gap:18px;padding:12px 0;font-size:15px;line-height:1.7}.service-information dt{color:var(--muted,#6b7280)}dd{margin:0;overflow-wrap:anywhere}.copy-number{border:0;background:none;color:inherit;vertical-align:middle;cursor:pointer;padding:8px}.preserve-lines,.quote-copy p{white-space:pre-wrap;overflow-wrap:anywhere}.quote-heading{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap}.quote-caption{display:flex;justify-content:space-between;gap:20px;font-size:13px;color:var(--muted,#6b7280);margin-bottom:28px;overflow-wrap:anywhere}.quote-copy{margin:24px 0;line-height:1.9}.quote-copy h3,.quote-section-title{font-size:16px}.repair-quote-items{list-style:none;padding:0;margin:0}.repair-quote-items li{display:flex;gap:24px;justify-content:space-between;border-bottom:1px solid var(--border,#e8e9eb);padding:24px 0;line-height:1.7}.repair-quote-items li>div{min-width:0;overflow-wrap:anywhere}.repair-quote-items li>strong{white-space:nowrap}.repair-quote-items small{display:block;margin-top:8px}.repair-quote-items small,.repair-quote-items p{color:var(--muted,#6b7280)}.quote-totals{max-width:420px;margin:24px 0 24px auto}.quote-totals>div{display:flex;justify-content:space-between;gap:20px;padding:10px 0}.grand-total{border-top:1px solid var(--border,#e8e9eb);margin-top:12px;font-weight:700;font-size:24px}.customer-notice{background:var(--surface-soft,#f6f7f8);padding:16px 24px;border-radius:12px}.quote-decision{border-top:1px solid var(--border,#e8e9eb);padding-top:20px}.quote-decision p{font-size:13px;color:var(--muted,#6b7280)}.quote-decision>div{display:flex;gap:12px;justify-content:flex-end}button:disabled{opacity:.5;cursor:wait}
@media(max-width:600px){.progress-hero{padding:28px 0}.progress-title h1{font-size:32px}.progress-section{padding:28px 0}.service-information{padding:16px}.service-information>div{grid-template-columns:72px 1fr;gap:12px;font-size:14px}.progress-section h2{font-size:22px}.quote-decision>div .button{flex:1}.quote-heading h2{margin-bottom:12px}.quote-caption{margin-top:12px}}
</style>
