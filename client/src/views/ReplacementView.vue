<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import InlineNotice from '@/components/InlineNotice.vue'
import SearchableSelect from '@/components/SearchableSelect.vue'
import { api, idempotencyKey, unwrap } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { Address } from '@/types'

interface ReplacementTicket { id:number;ticket_no:string;status:string;status_label:string }
const auth=useAuthStore(),addresses=ref<Address[]>([]),loading=ref(false),error=ref(''),result=ref<ReplacementTicket|null>(null)
const form=reactive({old_model:'',desired_model:'',contact_name:auth.account?.nickname||'',contact_phone:auth.account?.phone||'',address_id:0,notes:''})
const addressOptions=computed(()=>[{value:0,label:'请先在“我的”添加地址',disabled:true},...addresses.value.map(item=>({value:item.id,label:`${item.recipient_name} · ${item.city}${item.detail}`}))])
onMounted(async()=>{addresses.value=unwrap(await api.get('/addresses'));form.address_id=addresses.value.find(item=>item.is_default)?.id||addresses.value[0]?.id||0})
async function submit(){if(loading.value||!form.address_id)return;loading.value=true;error.value='';try{result.value=unwrap(await api.post('/replacement',form,{headers:{'Idempotency-Key':idempotencyKey('replacement')}}))}catch(e){error.value=e instanceof Error?e.message:'置换工单提交失败'}finally{loading.value=false}}
</script>

<template>
  <PageHeader title="置换服务" back subtitle="提交需求后由服务顾问人工跟进"/>
  <section v-if="result" class="form-card replacement-success"><span class="pill blue">{{result.status_label}}</span><h2>置换工单已提交</h2><p>工单号：<strong>{{result.ticket_no}}</strong></p><p class="replacement-promise">服务顾问会在一个工作日内联系您，请保持电话畅通</p><RouterLink class="button" to="/work-orders">查看订单与工单</RouterLink></section>
  <form v-else class="form-card form-grid replacement-form" @submit.prevent="submit">
    <div class="form-grid two"><div class="field"><label>旧机型</label><input v-model="form.old_model" maxlength="200" placeholder="例如：DJI Mini 4 Pro 畅飞套装" required/></div><div class="field"><label>需求机型</label><input v-model="form.desired_model" maxlength="200" placeholder="例如：DJI Air 3S 畅飞套装" required/></div></div>
    <div class="form-grid two"><div class="field"><label>联系人</label><input v-model="form.contact_name" maxlength="120" required/></div><div class="field"><label>联系电话</label><input v-model="form.contact_phone" inputmode="tel" maxlength="32" required/></div></div>
    <div class="field"><label>联系地址</label><SearchableSelect v-model="form.address_id" :options="addressOptions" search-placeholder="检索收件人或地址" required/><small v-if="!addresses.length" class="muted">请先前往“我的”新增收货地址。</small></div>
    <div class="field"><label>补充说明（选填）</label><textarea v-model="form.notes" maxlength="2000" placeholder="可填写旧机成色、附件情况或方便联系的时间"></textarea></div>
    <p class="muted">提交后仅建立人工服务工单，不会自动生成报价或承诺置换金额。</p><InlineNotice :message="error" type="error"/><button class="button wide" :disabled="loading||!form.address_id">{{loading?'正在提交…':'提交置换工单'}}</button>
  </form>
</template>
