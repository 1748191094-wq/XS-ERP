<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Search, ShoppingCart } from '@lucide/vue'
import PageHeader from '@/components/PageHeader.vue'
import { api, unwrap } from '@/api/client'
import type { Product } from '@/types'
import { useBranding } from '@/composables/useBranding'

const { brandMark } = useBranding()
const products = ref<Product[]>([])
const categories = ref<Array<{id:number;name:string;slug:string}>>([])
const active = ref('')
const query = ref('')
const loading = ref(false)
let timer = 0

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (active.value) params.set('category', active.value)
    if (query.value) params.set('q', query.value)
    products.value = unwrap(await api.get(`/products?${params}`))
  } finally { loading.value = false }
}
watch([active, query], () => { window.clearTimeout(timer); timer = window.setTimeout(load, 250) })
onMounted(async () => { categories.value = unwrap(await api.get('/product-categories')); await load() })
</script>

<template>
  <PageHeader title="商城" subtitle="价格与库存由门店后台实时提供"><RouterLink to="/cart" class="icon-button" aria-label="购物车"><ShoppingCart/></RouterLink></PageHeader>
  <div class="field"><div style="position:relative"><Search :size="18" style="position:absolute;left:14px;top:15px;color:#777"/><input v-model="query" type="search" placeholder="搜索商品" style="padding-left:42px"/></div></div>
  <div class="tabs" style="margin-top:13px"><button :class="{active:active===''}" @click="active=''">全部</button><button v-for="item in categories" :key="item.id" :class="{active:active===item.slug}" @click="active=item.slug">{{ item.name }}</button></div>
  <div v-if="products.length" class="product-grid card-grid section"><RouterLink v-for="item in products" :key="item.id" :to="`/mall/${item.id}`" class="product-card"><img v-if="item.images[0]" class="product-image" :src="item.images[0].url" :alt="item.images[0].alt"/><div v-else class="product-placeholder">{{ brandMark }}</div><h3>{{ item.name }}</h3><p>{{ item.summary || item.category?.name }}</p><div class="price">{{ item.price_from ? `¥${item.price_from} 起` : '价格待发布' }}</div></RouterLink></div>
  <div v-else-if="!loading" class="empty-state section">没有找到已发布商品。</div>
</template>
