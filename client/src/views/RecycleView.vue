<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import InlineNotice from '@/components/InlineNotice.vue'
import SearchableSelect from '@/components/SearchableSelect.vue'
import { api, idempotencyKey, unwrap } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

interface Catalog {
  id:number
  brand:string
  model:string
  variant?:string|null
  maximum_price:string
}
interface Estimate { maximum_price:string;notice:string }

const auth=useAuthStore()
const catalog=ref<Catalog[]>([]),catalogId=ref(0),step=ref(1),loading=ref(false),error=ref(''),estimate=ref<Estimate|null>(null),requestId=ref<number>(),files=ref<File[]>([])
const form=reactive({contact_name:'',contact_phone:'',contact_wechat:'',device_condition:'',notes:''})
const draftKey='service-client-recycle-draft-v2'
const selectedCatalog=computed(()=>catalog.value.find(item=>item.id===catalogId.value))
const catalogOptions=computed(()=>catalog.value.map(item=>({
  value:item.id,
  label:`${item.brand} ${item.model}${item.variant?` · ${item.variant}`:''} · 最高 ¥${item.maximum_price}`,
})))
const savePayload=computed(()=>({
  catalog_item_id:catalogId.value,
  condition_codes:[],
  details:{},
  contact_name:form.contact_name,
  contact_phone:form.contact_phone,
  contact_wechat:form.contact_wechat||null,
  device_condition:form.device_condition,
  notes:form.notes||null,
}))

onMounted(async()=>{
  await auth.restore()
  catalog.value=unwrap(await api.get('/recycle/catalog'))
  try{
    const draft=JSON.parse(sessionStorage.getItem(draftKey)||'null') as {catalogId?:number;form?:Partial<typeof form>;step?:number}|null
    if(draft?.catalogId&&catalog.value.some(item=>item.id===draft.catalogId)){
      catalogId.value=draft.catalogId
      Object.assign(form,draft.form||{})
      step.value=Math.min(2,Math.max(1,draft.step||1))
    }else catalogId.value=catalog.value[0]?.id||0
  }catch{catalogId.value=catalog.value[0]?.id||0}
  form.contact_name ||= auth.account?.nickname || ''
  form.contact_phone ||= auth.account?.phone || ''
})

watch([catalogId,()=>({...form}),step],()=>{
  if(requestId.value)return
  sessionStorage.setItem(draftKey,JSON.stringify({catalogId:catalogId.value,form:{...form},step:Math.min(step.value,2)}))
},{deep:true})

function continueToForm(){
  if(catalogId.value)step.value=2
}

async function getEstimate(){
  if(!form.contact_name.trim()||!form.contact_phone.trim()||!form.device_condition.trim()){
    error.value='请完整填写联系人、联系电话和设备情况'
    return
  }
  loading.value=true;error.value=''
  try{
    estimate.value=unwrap(await api.post('/recycle/estimate',{catalog_item_id:catalogId.value,condition_codes:[],details:{}}))
    step.value=3
  }catch(e){error.value=e instanceof Error?e.message:'读取最高回收价失败'}
  finally{loading.value=false}
}

async function submit(){
  if(!estimate.value||loading.value)return
  loading.value=true;error.value=''
  try{
    const data=unwrap<{id:number}>(await api.post('/recycle',{...savePayload.value,submit:true},{headers:{'Idempotency-Key':idempotencyKey('recycle')}}))
    requestId.value=data.id
    sessionStorage.removeItem(draftKey)
    for(const file of files.value){
      const body=new FormData();body.append('file',file)
      await api.post(`/recycle/${data.id}/attachments`,body)
    }
  }catch(e){error.value=e instanceof Error?e.message:'提交失败'}
  finally{loading.value=false}
}
</script>

<template>
  <PageHeader title="旧机回收" back subtitle="客户端仅展示该机型的最高回收参考价"/>
  <div class="tabs" style="margin-bottom:18px">
    <button :class="{active:step===1}">1 选择机型</button>
    <button :class="{active:step===2}">2 填写回收工单</button>
    <button :class="{active:step===3}">3 确认提交</button>
  </div>
  <div v-if="requestId" class="form-card">
    <span class="pill blue">已提交</span>
    <h2>回收工单已返回门店后台</h2>
    <p class="muted">工作人员将按联系方式与你沟通，并在实物检测后给出正式报价。</p>
    <RouterLink to="/work-orders" class="button">查看回收工单</RouterLink>
  </div>
  <section v-else class="form-card form-grid">
    <template v-if="step===1">
      <div class="field">
        <label>产品型号</label>
        <SearchableSelect v-model="catalogId" :options="catalogOptions" search-placeholder="检索品牌、型号或版本" />
      </div>
      <div v-if="selectedCatalog" class="recycle-price-card">
        <p class="eyebrow">该机型最高回收价</p>
        <h2>¥{{selectedCatalog.maximum_price}}</h2>
        <small class="muted">实际回收价以设备检测结果为准</small>
      </div>
      <InlineNotice v-if="!catalog.length" message="门店尚未配置回收型号，请联系工作人员。" type="info"/>
      <button class="button" :disabled="!catalogId" @click="continueToForm">填写回收工单</button>
    </template>
    <template v-else-if="step===2">
      <InlineNotice message="请留下可联系到你的信息；工单提交后会直接进入门店后台。" type="info"/>
      <div class="form-grid two">
        <div class="field"><label>联系人</label><input v-model="form.contact_name" maxlength="120" autocomplete="name" required/></div>
        <div class="field"><label>联系电话</label><input v-model="form.contact_phone" maxlength="32" inputmode="tel" autocomplete="tel" required/></div>
      </div>
      <div class="field"><label>微信号（选填）</label><input v-model="form.contact_wechat" maxlength="120" autocomplete="off"/></div>
      <div class="field"><label>设备情况</label><textarea v-model="form.device_condition" minlength="3" maxlength="3000" placeholder="请描述外观、功能、拆修、进水或碰撞情况" required/></div>
      <div class="field"><label>设备照片（选填，最多 12 张）</label><input type="file" multiple accept="image/jpeg,image/png,image/webp" @change="files=Array.from(($event.target as HTMLInputElement).files||[]).slice(0,12)"/></div>
      <div class="field"><label>其他备注（选填）</label><textarea v-model="form.notes" maxlength="2000"/></div>
      <InlineNotice :message="error" type="error"/>
      <div class="button-row"><button class="button secondary" @click="step=1">上一步</button><button class="button" :disabled="loading" @click="getEstimate">{{loading?'正在读取…':'确认最高价'}}</button></div>
    </template>
    <template v-else-if="estimate">
      <div class="recycle-price-card"><p class="eyebrow">最高回收参考价</p><h2>¥{{estimate.maximum_price}}</h2></div>
      <InlineNotice :message="estimate.notice"/>
      <div class="content-card">
        <strong>{{form.contact_name}} · {{form.contact_phone}}</strong>
        <p class="muted">{{form.device_condition}}</p>
      </div>
      <p class="muted">提交后门店将在后台收到机型、联系方式、设备情况和附件；正式报价仍需检测实物。</p>
      <InlineNotice :message="error" type="error"/>
      <div class="button-row"><button class="button secondary" @click="step=2">修改工单</button><button class="button" :disabled="loading" @click="submit">{{loading?'正在提交…':'提交回收工单'}}</button></div>
    </template>
  </section>
</template>
