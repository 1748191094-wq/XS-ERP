<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import InlineNotice from '@/components/InlineNotice.vue'
import SearchableSelect from '@/components/SearchableSelect.vue'
import { api, idempotencyKey, unwrap } from '@/api/client'
import type { Address, RepairOrder } from '@/types'

interface Device {id:number;brand:string;model:string;serial_number:string}
const devices=ref<Device[]>([]),addresses=ref<Address[]>([]),files=ref<File[]>([]),loading=ref(false),error=ref(''),result=ref<RepairOrder|null>(null)
const form=reactive({device_id:0,brand:'DJI',model:'',serial_number:'',fault_type:'',fault_description:'',service_mode:'shipping',has_water_damage:false,has_crash_damage:false,was_disassembled:false,current_state:'',accessories:'',contact_name:'',contact_phone:'',address_id:0,notes:''})
const initialForm=ref('')
const needsLeaveWarning=computed(()=>loading.value || (!result.value && (files.value.length>0 || Boolean(initialForm.value && JSON.stringify(form)!==initialForm.value))))
function preventUnload(event:BeforeUnloadEvent){
  if(needsLeaveWarning.value){event.preventDefault();event.returnValue=''}
}
onMounted(()=>window.addEventListener('beforeunload',preventUnload))
onBeforeUnmount(()=>window.removeEventListener('beforeunload',preventUnload))
onBeforeRouteLeave(()=>{
  if(!needsLeaveWarning.value)return true
  return window.confirm(loading.value
    ? '申请正在提交或附件正在上传。离开后可能无法确认结果，请留在此页等待完成。确定离开？'
    : '维修申请尚未提交，离开会丢失已填写的内容和所选附件。确定离开？')
})
const initialLoading=ref(true), initialError=ref(''), uploadIndex=ref(0), uploaded=ref(0)
const uploadProblems=ref<string[]>([])
const resultHeading=ref<HTMLElement|null>(null)
const submitError=ref<HTMLElement|null>(null)
function reviewForm(){document.querySelector<HTMLElement>('#repair-device')?.focus()}
const attachmentInput=ref<HTMLInputElement|null>(null)
const attachmentTypes:Record<string,string[]>={
  'image/jpeg':['jpg','jpeg'],'image/png':['png'],'image/webp':['webp'],
  'video/mp4':['mp4'],'video/quicktime':['mov'],
}
function fileProblem(file:File){
  const extension=file.name.split('.').pop()?.toLowerCase()||''
  if(!attachmentTypes[file.type]?.includes(extension))return '格式不支持或与扩展名不符，请选择 JPG、PNG、WebP、MP4 或 MOV。'
  const limit=file.type.startsWith('video/')?100:10
  if(file.size>limit*1024*1024)return `文件超过 ${limit}MB，请压缩后重新选择。`
  if(file.size===0)return '文件为空，请重新选择。'
  return ''
}
const attachmentProblems=computed(()=>files.value.map(fileProblem))
const attachmentsInvalid=computed(()=>files.value.length>12||attachmentProblems.value.some(Boolean))
function selectFiles(event:Event){
  const selected=Array.from((event.target as HTMLInputElement).files||[])
  if(selected.length)files.value=selected
}
async function removeFile(index:number){
  files.value.splice(index,1)
  if(attachmentInput.value)attachmentInput.value.value=''
  await nextTick()
  const buttons=document.querySelectorAll<HTMLButtonElement>('.repair-file-remove')
  if(buttons.length)buttons[Math.min(index,buttons.length-1)].focus()
  else attachmentInput.value?.focus()
}
function fileSize(size:number){return size<1024*1024?`${Math.ceil(size/1024)} KB`:`${(Math.ceil(size/1024/1024*100)/100).toFixed(2)} MB`}
async function loadOptions(){
  if(!initialLoading.value)initialLoading.value=true
  initialError.value=''
  try{
    const [deviceResponse,addressResponse]=await Promise.all([api.get('/devices'),api.get('/addresses')])
    devices.value=unwrap(deviceResponse);addresses.value=unwrap(addressResponse)
    if(!form.address_id)form.address_id=addresses.value.find(a=>a.is_default)?.id||addresses.value[0]?.id||0
    if(!initialForm.value)initialForm.value=JSON.stringify(form)
  }catch(e){initialError.value=e instanceof Error?e.message:'设备和地址读取失败'}
  finally{initialLoading.value=false}
}
const useExisting=computed(()=>form.device_id>0)
const deviceOptions=computed(()=>[{value:0,label:'填写新设备'},...devices.value.map(item=>({value:item.id,label:`${item.brand} ${item.model} · ${item.serial_number}`}))])
const addressOptions=computed(()=>[{value:0,label:'请先在“我的”添加地址',disabled:true},...addresses.value.map(item=>({value:item.id,label:`${item.recipient_name} · ${item.city}${item.detail}`}))])
onMounted(loadOptions)
async function submit(){
  if(loading.value)return
  if(attachmentsInvalid.value){attachmentInput.value?.focus();return}
  loading.value=true;error.value=''
  try{
    const payload={...form,device_id:useExisting.value?form.device_id:null,brand:useExisting.value?null:form.brand,model:useExisting.value?null:form.model,serial_number:useExisting.value?null:form.serial_number,address_id:form.service_mode==='shipping'?form.address_id:null,accessories:form.accessories.split(/[、,，]/).map(x=>x.trim()).filter(Boolean)}
    result.value=unwrap<RepairOrder>(await api.post('/repair',payload,{headers:{'Idempotency-Key':idempotencyKey('repair')}}))
    await nextTick();resultHeading.value?.focus()
    for(const [index,file] of files.value.entries()){
      uploadIndex.value=index+1
      const data=new FormData();data.append('file',file)
      try{await api.post(`/repair/${result.value.id}/attachments`,data,{timeout:120000});uploaded.value++}
      catch(e){uploadProblems.value.push(`${file.name}：${e instanceof Error?e.message:'未能确认上传结果'}`)}
    }
  }catch(e){error.value=e instanceof Error?e.message:'提交失败，请重试'}
  finally{
    loading.value=false
    if(error.value){await nextTick();submitError.value?.focus();submitError.value?.scrollIntoView({block:'center'})}
  }
}
</script>

<template>
  <PageHeader title="维修申请" back subtitle="填写设备与故障信息，提交后可查看服务进度"/>
  <section v-if="result" class="form-card repair-result" aria-labelledby="repair-result-title">
    <span class="pill blue">{{result.status_label}}</span>
    <h2 id="repair-result-title" ref="resultHeading" tabindex="-1">维修申请已提交</h2>
    <p class="muted">请保留工单号。无需再次提交维修申请。</p>
    <p class="repair-order-number">{{result.order_no}}</p>
    <section class="repair-upload-summary" aria-label="附件上传结果">
      <h3>照片与视频</h3>
      <p role="status" aria-live="polite">{{loading ? `正在上传第 ${uploadIndex} / ${files.length} 个附件，请保持页面打开…` : files.length ? `已确认上传 ${uploaded} / ${files.length} 个附件` : '本次未选择附件'}}</p>
      <progress v-if="loading && files.length" :value="uploadIndex - 1" :max="files.length" aria-label="已处理附件数"/>
      <template v-if="uploadProblems.length">
        <p class="repair-upload-warning">以下附件未能确认上传成功。工单已保留，请联系门店核对或补充，避免重复申请。</p>
        <ul><li v-for="(problem,index) in uploadProblems" :key="index">{{problem}}</li></ul>
      </template>
    </section>
    <p class="muted">下一步：查看这笔维修的当前状态、报价及需要你完成的操作。</p>
    <button v-if="loading" class="button wide" disabled>附件上传中…</button>
    <RouterLink v-else class="button wide" :to="{path:'/work-orders',query:{repair:String(result.id)}}">查看服务进度</RouterLink>
  </section>
  <section v-else-if="initialLoading || initialError" class="form-card" :aria-busy="initialLoading">
    <p v-if="initialLoading" role="status">正在读取你的设备和地址…</p>
    <template v-else><h2>暂时无法读取设备和地址</h2><InlineNotice :message="initialError" type="error"/><button class="button" @click="loadOptions">重新读取</button></template>
  </section>
  <form v-else class="repair-application" @submit.prevent="submit" :aria-busy="loading">
    <p v-if="needsLeaveWarning" class="repair-leave-notice" role="status">当前内容尚未提交。离开前会提醒确认；取消离开可继续填写。此页暂不保存草稿。</p>
    <p class="repair-form-intro">按下面四组信息填写。标有“必填”的项目需要填写，其余可按实际情况补充。</p>
    <fieldset class="form-card form-grid repair-group" :disabled="loading">
      <legend>1 · 送修设备</legend>
      <p class="repair-group-help">选择已有设备，或填写新设备的品牌、型号和序列号。</p>
      <div class="field"><label for="repair-device">从我的设备选择</label><SearchableSelect id="repair-device" v-model="form.device_id" :options="deviceOptions" search-placeholder="检索型号或序列号"/></div>
      <div v-if="!useExisting" class="form-grid two">
        <div class="field"><label for="repair-brand">品牌 <span class="repair-required">必填</span></label><input id="repair-brand" v-model="form.brand" required/></div>
        <div class="field"><label for="repair-model">型号 <span class="repair-required">必填</span></label><input id="repair-model" v-model="form.model" placeholder="例如：Neo、Avata 2" required/></div>
        <div class="field"><label for="repair-serial">序列号 <span class="repair-required">必填</span></label><input id="repair-serial" v-model="form.serial_number" aria-describedby="repair-serial-help" required/><small id="repair-serial-help" class="muted">请按设备上的 SN 标识填写。</small></div>
      </div>
    </fieldset>
    <fieldset class="form-card form-grid repair-group" :disabled="loading">
      <legend>2 · 故障与设备情况</legend>
      <p class="repair-group-help">描述你遇到的问题；不确定原因也可以直接写现象。</p>
      <div class="field"><label for="repair-fault">故障类型 <span class="repair-required">必填</span></label><input id="repair-fault" v-model="form.fault_type" placeholder="例如：云台异常、无法开机" required/></div>
      <div class="field"><label for="repair-description">故障描述 <span class="repair-required">必填</span></label><textarea id="repair-description" v-model="form.fault_description" minlength="5" maxlength="5000" aria-describedby="repair-description-help" required/><small id="repair-description-help" class="muted">至少 5 个字。可以写何时发生、出现什么现象、是否持续出现。</small></div>
      <fieldset class="repair-history"><legend>设备经历（选填，可多选）</legend><div class="checkbox-row"><label class="choice"><input v-model="form.has_water_damage" type="checkbox"/>进水</label><label class="choice"><input v-model="form.has_crash_damage" type="checkbox"/>炸机/碰撞</label><label class="choice"><input v-model="form.was_disassembled" type="checkbox"/>曾拆修</label></div></fieldset>
      <div class="field"><label for="repair-state">设备当前状态（选填）</label><input id="repair-state" v-model="form.current_state" placeholder="能否开机、云台是否自检等"/></div>
      <div class="field"><label for="repair-accessories">随附物品（选填）</label><input id="repair-accessories" v-model="form.accessories" placeholder="电池、遥控器、充电器，用逗号分隔"/></div>
    </fieldset>
    <fieldset class="form-card form-grid repair-group" :disabled="loading">
      <legend>3 · 送修方式与联系信息</legend>
      <p class="repair-group-help">寄修需要选择寄回地址；到店服务无需填写地址。</p>
      <div class="field"><label for="repair-mode">服务方式 <span class="repair-required">必填</span></label><select id="repair-mode" v-model="form.service_mode"><option value="shipping">寄修</option><option value="store">到店</option></select></div>
      <div v-if="form.service_mode==='shipping'" class="field"><label for="repair-address">寄回地址 <span class="repair-required">必填</span></label><SearchableSelect id="repair-address" v-model="form.address_id" :options="addressOptions" search-placeholder="检索收件人或地址" :aria-describedby="!form.address_id ? 'repair-address-help' : undefined" required/></div>
      <p v-if="form.service_mode==='shipping'&&!form.address_id" id="repair-address-help" class="repair-group-help">寄修需要寄回地址。请先到<RouterLink class="repair-profile-link" to="/profile">“我的”添加地址</RouterLink>，或选择到店服务。离开此页不会保存未提交的内容。</p>
      <div class="form-grid two">
        <div class="field"><label for="repair-contact">联系人 <span class="repair-required">必填</span></label><input id="repair-contact" v-model="form.contact_name" autocomplete="name" required/></div>
        <div class="field"><label for="repair-phone">联系电话 <span class="repair-required">必填</span></label><input id="repair-phone" v-model="form.contact_phone" inputmode="tel" autocomplete="tel" required/></div>
      </div>
    </fieldset>
    <fieldset class="form-card form-grid repair-group" :disabled="loading">
      <legend>4 · 补充材料（选填）</legend>
      <p class="repair-group-help">照片或视频有助于描述故障，没有材料也可以提交申请。</p>
      <div class="field">
        <label for="repair-files">照片 / 视频（选填，最多 12 个）</label>
        <div class="repair-file-picker"><input id="repair-files" ref="attachmentInput" type="file" multiple accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime" :aria-invalid="attachmentsInvalid || undefined" aria-describedby="repair-files-help repair-files-status" @change="selectFiles"/><span aria-hidden="true">选择或替换附件</span></div>
        <small id="repair-files-help" class="muted">支持 JPG、PNG、WebP 图片（每张不超过 10MB）及 MP4、MOV 视频（每个不超过 100MB）。重新选择会替换下方清单。</small>
        <p id="repair-files-status" class="repair-files-status" role="status">{{files.length ? `本次已选择 ${files.length} 个附件` : '尚未选择附件，可直接提交申请'}}{{files.length>12 ? '，超过 12 个上限，请移除多余文件。' : attachmentsInvalid ? '，请移除或替换有问题的文件。' : ''}}</p>
        <ul v-if="files.length" class="repair-file-list" aria-label="本次提交的附件">
          <li v-for="(file,index) in files" :key="index">
            <div class="repair-file-description"><strong>{{file.name}}</strong><small class="muted">{{fileSize(file.size)}}</small><p v-if="attachmentProblems[index]" class="repair-file-error">{{attachmentProblems[index]}}</p></div>
            <button type="button" class="repair-file-remove" :aria-label="`移除附件 ${index+1}：${file.name}`" @click="removeFile(index)">移除</button>
          </li>
        </ul>
      </div>
      <div class="field"><label for="repair-notes">备注（选填）</label><textarea id="repair-notes" v-model="form.notes" maxlength="2000"/></div>
    </fieldset>
    <div class="repair-submit">
      <section v-if="error" ref="submitError" class="repair-submit-error" tabindex="-1" aria-labelledby="repair-submit-error-title" aria-describedby="repair-submit-error-detail repair-submit-error-help">
        <h2 id="repair-submit-error-title">未能确认提交结果</h2>
        <p id="repair-submit-error-detail">{{error}}</p>
        <p id="repair-submit-error-help">填写内容和所选附件仍保留在此页。请检查提示后修改；若遇到网络中断，请先向门店核对是否已有工单，避免重复申请。</p>
        <button type="button" class="button secondary" @click="reviewForm">返回检查填写内容</button>
      </section>
      <p v-if="form.service_mode==='shipping'&&!form.address_id" class="muted">还需要选择寄回地址，或将服务方式改为到店。</p>
      <p v-if="attachmentsInvalid" class="repair-file-error" role="status">附件尚需调整，请在“补充材料”中移除或替换有问题的文件。</p>
      <button class="button wide" :disabled="loading||attachmentsInvalid||form.service_mode==='shipping'&&!form.address_id">{{loading?'正在创建维修工单…':'提交维修申请'}}</button>
    </div>
  </form>
</template>

<style scoped>
.repair-submit-error{border:1px solid var(--danger);border-radius:14px;padding:20px;background:var(--card);color:var(--ink);margin-bottom:20px;scroll-margin-block:100px;overflow-wrap:anywhere}
.repair-submit-error h2{font-size:20px;line-height:1.4;margin:0 0 12px;color:var(--ink)}
.repair-submit-error p{margin:0 0 14px}
.repair-submit-error #repair-submit-error-detail{color:var(--danger)}
.repair-submit-error .button{min-height:44px;white-space:normal}

.repair-leave-notice{padding:14px 16px;border:1px solid var(--line);border-radius:12px;background:var(--blue-soft);color:var(--ink);line-height:1.6;margin:0 0 20px}
.repair-application{max-width:820px;margin-inline:auto;display:grid;gap:24px}
.repair-form-intro{margin:0;color:var(--muted);font-size:14px;line-height:1.65}
.repair-group{margin:0;min-width:0;padding:20px;border:1px solid var(--line)}
.repair-group>legend{padding:0 8px;font-size:19px;font-weight:700;color:var(--ink)}
.repair-group-help{margin:0;color:var(--muted);font-size:14px;line-height:1.65}
.repair-required{font-size:12px;font-weight:400;color:var(--muted);margin-left:5px}
.repair-history{border:0;padding:0;margin:0;min-width:0}
.repair-history legend{font-size:13px;font-weight:700;margin-bottom:10px}
.repair-application .field,.repair-application .form-grid{min-width:0}
.repair-application .choice{min-height:44px}
.repair-application small{line-height:1.6}
.repair-application :focus-visible{outline:3px solid var(--blue);outline-offset:3px}
.repair-file-picker{position:relative;display:flex;align-items:center;justify-content:center;width:fit-content;max-width:100%;min-height:48px;padding:10px 16px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--blue);font-weight:600}
.repair-file-picker input{position:absolute;inset:0;opacity:0;cursor:pointer;padding:0}
.repair-file-picker:focus-within{outline:3px solid var(--blue);outline-offset:3px}
.repair-file-list{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.repair-file-list li{display:flex;gap:12px;align-items:flex-start;border:1px solid var(--line);border-radius:12px;padding:12px}
.repair-file-description{min-width:0;flex:1;display:grid;gap:4px}
.repair-file-description strong{font-size:14px;font-weight:600;overflow-wrap:anywhere}
.repair-file-error{margin:0;color:var(--danger);font-size:14px;line-height:1.6;overflow-wrap:anywhere}
.repair-files-status{margin:0;font-size:14px;line-height:1.6}
.repair-file-remove{min-width:52px;min-height:44px;flex-shrink:0;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--blue)}
.repair-submit p{font-size:14px;line-height:1.6}
@media(max-width:480px){.repair-group{padding:16px}.repair-group>legend{font-size:18px}}
.repair-result{max-width:720px;margin-inline:auto}
.repair-result h2{font-size:26px;margin-bottom:8px}
.repair-result p,.repair-result li{line-height:1.65;overflow-wrap:anywhere}
.repair-order-number{font-size:20px;font-weight:700;letter-spacing:.02em}
.repair-upload-summary{border-block:1px solid var(--line);padding-block:18px;margin-block:24px}
.repair-upload-summary h3{margin:0;font-size:17px}
.repair-upload-summary ul{padding-left:22px;font-size:14px}
.repair-upload-summary progress{width:100%;accent-color:var(--blue)}
.repair-upload-warning{color:var(--danger)}
.repair-profile-link{color:var(--blue);text-decoration:underline}
.repair-result :focus-visible{outline:3px solid var(--blue);outline-offset:4px}
</style>
