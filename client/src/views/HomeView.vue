<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { RefreshCcw, Stethoscope, ShoppingBag, MessageCircle, Repeat2 } from '@lucide/vue'
import { api, unwrap } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { ForumPost, Product, RepairOrder } from '@/types'
import { useBranding } from '@/composables/useBranding'

const auth = useAuthStore()
const { brandName, brandMark } = useBranding()
const products = ref<Product[]>([])
const repairs = ref<RepairOrder[]>([])
const posts = ref<ForumPost[]>([])
const loading = ref(true)
const greeting = computed(() => {
  const hour = new Date().getHours()
  return hour < 11 ? '早上好' : hour < 18 ? '下午好' : '晚上好'
})

onMounted(async () => {
  try {
    const jobs: Promise<unknown>[] = [
      api.get('/products?featured=true').then(r => products.value = unwrap<Product[]>(r).slice(0,4)),
      api.get('/forum/posts?sort=hot&page_size=3').then(r => posts.value = unwrap<{items:ForumPost[]}>(r).items),
    ]
    if (auth.isAuthenticated) jobs.push(api.get('/repair').then(r => repairs.value = unwrap<RepairOrder[]>(r).slice(0,2)))
    await Promise.all(jobs)
  } finally { loading.value = false }
})
</script>

<template>
  <section class="hero">
    <p class="eyebrow">{{ brandName }} CARE</p>
    <h1>{{ greeting }}，{{ auth.account?.nickname || '飞手' }}</h1>
    <p>从提交服务到查看结果，都与门店后台实时使用同一套业务数据。</p>
  </section>
  <section class="section">
    <div class="section-heading"><h2>常用服务</h2><span class="muted">三次点击内完成</span></div>
    <div class="card-grid">
      <RouterLink class="service-card" to="/recycle/new"><RefreshCcw/><div><h3>旧机回收</h3><p>查看最高回收价并提交回收工单</p></div></RouterLink>
      <RouterLink class="service-card" to="/repair/new"><Stethoscope/><div><h3>维修申请</h3><p>照片、视频与工单直达门店</p></div></RouterLink>
      <RouterLink class="service-card" to="/mall"><ShoppingBag/><div><h3>商品购买</h3><p>实时价格与库存</p></div></RouterLink>
      <RouterLink class="service-card" to="/community"><MessageCircle/><div><h3>社区交流</h3><p>维修、飞行和设备讨论</p></div></RouterLink>
      <RouterLink class="service-card" to="/replacement/new"><Repeat2/><div><h3>置换服务</h3><p>填写旧机、需求机型和联系方式</p></div></RouterLink>
    </div>
  </section>
  <section v-if="repairs.length" class="section">
    <div class="section-heading"><h2>进行中的维修</h2><RouterLink to="/work-orders">查看全部</RouterLink></div>
    <div class="list"><RouterLink v-for="item in repairs" :key="item.id" to="/work-orders" class="list-item"><div class="list-item-main"><h3>{{ item.device.brand }} {{ item.device.model }}</h3><p>{{ item.order_no }} · {{ item.fault_description }}</p></div><span class="pill blue">{{ item.status_label }}</span></RouterLink></div>
  </section>
  <section class="section">
    <div class="section-heading"><h2>热门商品</h2><RouterLink to="/mall">进入商城</RouterLink></div>
    <div v-if="products.length" class="product-grid card-grid"><RouterLink v-for="item in products" :key="item.id" :to="`/mall/${item.id}`" class="product-card"><img v-if="item.images[0]" class="product-image" :src="item.images[0].url" :alt="item.images[0].alt"/><div v-else class="product-placeholder">{{ brandMark }}</div><h3>{{ item.name }}</h3><p>{{ item.summary }}</p><div class="price">{{ item.price_from ? `¥${item.price_from} 起` : '价格待发布' }}</div></RouterLink></div>
    <div v-else class="empty-state">门店尚未发布客户可见商品。</div>
  </section>
  <section class="section">
    <div class="section-heading"><h2>社区热门</h2><RouterLink to="/community">查看更多</RouterLink></div>
    <div v-if="posts.length" class="list"><RouterLink v-for="post in posts" :key="post.id" :to="`/community/${post.id}`" class="list-item"><div class="list-item-main"><h3>{{ post.title }}</h3><p>{{ post.category.name }} · {{ post.author.nickname }} · {{ post.comment_count }} 条评论</p></div></RouterLink></div>
    <div v-else-if="!loading" class="empty-state">社区还没有帖子，欢迎发布第一篇。</div>
  </section>
</template>
