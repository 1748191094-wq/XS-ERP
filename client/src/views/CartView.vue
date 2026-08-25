<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import InlineNotice from '@/components/InlineNotice.vue'
import SearchableSelect from '@/components/SearchableSelect.vue'
import { api, idempotencyKey, unwrap } from '@/api/client'
import type { Address } from '@/types'

interface CartItem { id:number;quantity:number;selected:boolean;valid:boolean;available_stock:number;amount:string;product:{id:number;name:string};sku:{id:number;name:string;price:string} }
const cart = ref<{items:CartItem[];selected_total:string}>({items:[],selected_total:'0.00'})
const addresses=ref<Address[]>([]), addressId=ref<number>(), showAddressForm=ref(false), savingAddress=ref(false), loading=ref(false), message=ref(''), error=ref('')
const addressForm=reactive({recipient_name:'',phone:'',province:'',city:'',district:'',detail:'',is_default:true})
const selected=computed(()=>cart.value.items.filter(i=>i.selected&&i.valid))
const addressOptions=computed(()=>addresses.value.map(item=>({value:item.id,label:`${item.recipient_name} · ${item.city}${item.detail}`})))
async function load(){cart.value=unwrap(await api.get('/cart'));addresses.value=unwrap(await api.get('/addresses'));addressId.value=addresses.value.find(a=>a.is_default)?.id||addresses.value[0]?.id}
async function change(item:CartItem){await api.patch(`/cart/items/${item.id}`,{quantity:item.quantity,selected:item.selected});await load()}
async function remove(id:number){await api.delete(`/cart/items/${id}`);await load()}
async function saveAddress(){if(savingAddress.value)return;savingAddress.value=true;error.value='';try{const data=unwrap<Address>(await api.post('/addresses',addressForm));addresses.value.unshift(data);addressId.value=data.id;showAddressForm.value=false;Object.assign(addressForm,{recipient_name:'',phone:'',province:'',city:'',district:'',detail:'',is_default:true});message.value='新地址已保存并选中'}catch(e){error.value=e instanceof Error?e.message:'地址保存失败'}finally{savingAddress.value=false}}
async function order(){if(!addressId.value||!selected.value.length||loading.value)return;loading.value=true;error.value='';message.value='';try{const data=unwrap<{order_no:string}>(await api.post('/orders',{address_id:addressId.value,delivery_method:'shipping',cart_item_ids:selected.value.map(i=>i.id)},{headers:{'Idempotency-Key':idempotencyKey('order')}}));message.value=`订单 ${data.order_no} 已提交，等待门店确认/收款`;await load()}catch(e){error.value=e instanceof Error?e.message:'下单失败'}finally{loading.value=false}}
onMounted(load)
</script>

<template>
  <PageHeader title="购物车" back subtitle="结算价格以后端实时价格为准"/>
  <div class="two-column"><section><div v-if="cart.items.length" class="list"><article v-for="item in cart.items" :key="item.id" class="list-item"><input v-model="item.selected" type="checkbox" :disabled="!item.valid" @change="change(item)"/><div class="list-item-main"><h3>{{ item.product.name }}</h3><p>{{ item.sku.name }} · ¥{{ item.sku.price }}</p><p v-if="!item.valid" style="color:var(--danger)">商品已下架</p></div><input v-model.number="item.quantity" type="number" min="1" :max="item.available_stock" style="width:64px" @change="change(item)"/><button class="button danger" @click="remove(item.id)">删除</button></article></div><div v-else class="empty-state">购物车还是空的。</div></section>
    <aside class="form-card"><h2>结算</h2><div class="address-heading"><label>收货地址</label><button v-if="addresses.length" type="button" class="text-button" @click="showAddressForm=!showAddressForm">{{showAddressForm?'取消新增':'＋ 新增地址'}}</button></div><div v-if="addresses.length" class="field"><SearchableSelect v-model="addressId" :options="addressOptions" search-placeholder="检索收件人或地址"/></div><form v-if="showAddressForm||!addresses.length" class="form-grid quick-address-form" @submit.prevent="saveAddress"><p class="muted">{{addresses.length?'新增后将自动选中':'先添加收货地址'}}</p><div class="field"><label>收件人</label><input v-model="addressForm.recipient_name" required/></div><div class="field"><label>手机号</label><input v-model="addressForm.phone" inputmode="tel" required/></div><div class="form-grid two"><div class="field"><label>省份</label><input v-model="addressForm.province" required/></div><div class="field"><label>城市</label><input v-model="addressForm.city" required/></div></div><div class="field"><label>区/县（选填）</label><input v-model="addressForm.district"/></div><div class="field"><label>详细地址</label><input v-model="addressForm.detail" required/></div><button class="button secondary" :disabled="savingAddress">{{savingAddress?'正在保存…':'保存并使用此地址'}}</button></form><p style="display:flex;justify-content:space-between;font-size:18px"><span>合计</span><strong>¥{{ cart.selected_total }}</strong></p><p class="muted">当前未接入在线支付。提交后由门店人工确认库存和收款，不会伪造支付成功。</p><InlineNotice :message="message" type="success"/><InlineNotice :message="error" type="error"/><button class="button wide" :disabled="!selected.length||!addressId||loading" @click="order">{{ loading?'正在提交…':'提交订单' }}</button></aside>
  </div>
</template>
