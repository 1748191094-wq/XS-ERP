<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import InlineNotice from '@/components/InlineNotice.vue'
import SearchableSelect from '@/components/SearchableSelect.vue'
import { api, idempotencyKey, unwrap } from '@/api/client'
import type { Address, RepairOrder } from '@/types'

interface Device {id:number;brand:string;model:string;serial_number:string}
const devices=ref<Device[]>([]),addresses=ref<Address[]>([]),files=ref<File[]>([]),loading=ref(false),error=ref(''),result=ref<RepairOrder|null>(null)
const form=reactive({device_id:0,brand:'DJI',model:'',serial_number:'',fault_type:'',fault_description:'',service_mode:'shipping',has_water_damage:false,has_crash_damage:false,was_disassembled:false,current_state:'',accessories:'',contact_name:'',contact_phone:'',address_id:0,notes:''})
const useExisting=computed(()=>form.device_id>0)
const deviceOptions=computed(()=>[{value:0,label:'填写新设备'},...devices.value.map(item=>({value:item.id,label:`${item.brand} ${item.model} · ${item.serial_number}`}))])
const addressOptions=computed(()=>[{value:0,label:'请先在“我的”添加地址',disabled:true},...addresses.value.map(item=>({value:item.id,label:`${item.recipient_name} · ${item.city}${item.detail}`}))])
onMounted(async()=>{devices.value=unwrap(await api.get('/devices'));addresses.value=unwrap(await api.get('/addresses'));form.address_id=addresses.value.find(a=>a.is_default)?.id||addresses.value[0]?.id||0})
async function submit(){if(loading.value)return;loading.value=true;error.value='';try{const payload={...form,device_id:useExisting.value?form.device_id:null,brand:useExisting.value?null:form.brand,model:useExisting.value?null:form.model,serial_number:useExisting.value?null:form.serial_number,address_id:form.service_mode==='shipping'?form.address_id:null,accessories:form.accessories.split(/[、,，]/).map(x=>x.trim()).filter(Boolean)};const created=unwrap<RepairOrder>(await api.post('/repair',payload,{headers:{'Idempotency-Key':idempotencyKey('repair')}}));result.value=created;for(const file of files.value){const data=new FormData();data.append('file',file);await api.post(`/repair/${created.id}/attachments`,data,{timeout:120000})}}catch(e){error.value=e instanceof Error?e.message:'提交失败'}finally{loading.value=false}}
</script>

<template>
  <PageHeader title="维修申请" back subtitle="提交后自动进入门店维修工单"/>
  <div v-if="result" class="form-card"><span class="pill blue">{{result.status_label}}</span><h2>申请已提交</h2><p>维修单号：<strong>{{result.order_no}}</strong></p><p class="muted">可以在“工单”页面持续查看检测、报价、维修和物流进度。</p><RouterLink class="button" to="/work-orders">查看工单</RouterLink></div>
  <form v-else class="form-card form-grid" @submit.prevent="submit">
    <div class="field"><label>从我的设备选择（选填）</label><SearchableSelect v-model="form.device_id" :options="deviceOptions" search-placeholder="检索型号或序列号"/></div>
    <div v-if="!useExisting" class="form-grid two"><div class="field"><label>品牌</label><input v-model="form.brand" required/></div><div class="field"><label>型号</label><input v-model="form.model" required/></div><div class="field"><label>序列号</label><input v-model="form.serial_number" required/></div></div>
    <div class="field"><label>故障类型</label><input v-model="form.fault_type" placeholder="例如：云台异常、无法开机" required/></div><div class="field"><label>故障描述</label><textarea v-model="form.fault_description" minlength="5" maxlength="5000" required/></div>
    <div class="checkbox-row"><label class="choice"><input v-model="form.has_water_damage" type="checkbox"/>进水</label><label class="choice"><input v-model="form.has_crash_damage" type="checkbox"/>炸机/碰撞</label><label class="choice"><input v-model="form.was_disassembled" type="checkbox"/>曾拆修</label></div>
    <div class="field"><label>设备当前状态</label><input v-model="form.current_state" placeholder="能否开机、云台是否自检等"/></div><div class="field"><label>随附物品</label><input v-model="form.accessories" placeholder="电池、遥控器、充电器，用逗号分隔"/></div>
    <div class="field"><label>服务方式</label><select v-model="form.service_mode"><option value="shipping">寄修</option><option value="store">到店</option></select></div><div v-if="form.service_mode==='shipping'" class="field"><label>寄回地址</label><SearchableSelect v-model="form.address_id" :options="addressOptions" search-placeholder="检索收件人或地址" required/></div>
    <div class="form-grid two"><div class="field"><label>联系人</label><input v-model="form.contact_name" required/></div><div class="field"><label>联系电话</label><input v-model="form.contact_phone" inputmode="tel" required/></div></div>
    <div class="field"><label>照片 / 视频（最多 12 个）</label><input type="file" multiple accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime" @change="files=Array.from(($event.target as HTMLInputElement).files||[]).slice(0,12)"/><small class="muted">图片单张不超过 10MB；视频不超过 100MB</small></div><div class="field"><label>备注</label><textarea v-model="form.notes" maxlength="2000"/></div>
    <InlineNotice :message="error" type="error"/><button class="button wide" :disabled="loading||form.service_mode==='shipping'&&!form.address_id">{{loading?'正在提交和上传…':'提交维修申请'}}</button>
  </form>
</template>
