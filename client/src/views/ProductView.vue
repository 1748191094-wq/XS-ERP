<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ShoppingCart } from '@lucide/vue'
import PageHeader from '@/components/PageHeader.vue'
import InlineNotice from '@/components/InlineNotice.vue'
import { api, unwrap } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { Product, ProductSku } from '@/types'
import { useBranding } from '@/composables/useBranding'

const { brandMark } = useBranding()
const route = useRoute(), router = useRouter(), auth = useAuthStore()
const product = ref<Product | null>(null), sku = ref<ProductSku | null>(null)
const quantity = ref(1), loading = ref(false), message = ref(''), error = ref('')
const price = computed(() => sku.value?.price || product.value?.price_from)
onMounted(async () => { const data=unwrap<Product>(await api.get(`/products/${route.params.id}`));product.value=data;sku.value=data.skus?.[0] || null })
async function add() {
  if (!auth.isAuthenticated) return router.push({name:'auth',query:{redirect:route.fullPath}})
  if (!sku.value || loading.value) return
  loading.value=true; error.value=''; message.value=''
  try { await api.post('/cart/items',{sku_id:sku.value.id,quantity:quantity.value}); message.value='已加入购物车' }
  catch(e){ error.value=e instanceof Error?e.message:'加入失败' } finally{loading.value=false}
}
</script>

<template>
  <PageHeader title="商品详情" back><button class="icon-button" aria-label="购物车" @click="router.push('/cart')"><ShoppingCart/></button></PageHeader>
  <div v-if="product" class="two-column">
    <section><img v-if="product.images[0]" class="product-image" :src="product.images[0].url" :alt="product.images[0].alt"/><div v-else class="product-placeholder">{{ brandMark }}</div></section>
    <section class="form-card"><span class="pill">{{ product.category?.name || '商品' }}</span><h1 style="font-size:30px;margin:14px 0 7px">{{ product.name }}</h1><p class="muted">{{ product.summary }}</p><p style="font-size:25px;font-weight:760">{{ price ? `¥${price}` : '价格待发布' }}</p>
      <div v-if="product.skus?.length" class="field"><label>选择规格</label><div class="checkbox-row"><button v-for="item in product.skus" :key="item.id" :class="['choice',{active:sku?.id===item.id}]" @click="sku=item">{{ item.name }} · ¥{{ item.price }}</button></div></div>
      <div class="field" style="margin-top:15px"><label>数量</label><input v-model.number="quantity" type="number" min="1" :max="sku?.stock || 1"/></div>
      <InlineNotice :message="message" type="success"/><InlineNotice :message="error" type="error"/>
      <button class="button wide" :disabled="!sku || sku.stock<1 || loading" @click="add">{{ sku && sku.stock<1?'暂时缺货':loading?'正在加入…':'加入购物车' }}</button>
      <div class="section"><h3>商品说明</h3><p class="muted" style="white-space:pre-wrap">{{ product.description || '门店暂未填写详细说明。' }}</p><h3>售后信息</h3><p class="muted" style="white-space:pre-wrap">{{ product.after_sales || '具体售后政策请联系门店确认。' }}</p></div>
    </section>
  </div>
</template>
