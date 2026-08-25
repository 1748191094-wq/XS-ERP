<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { Home, ShoppingBag, MessagesSquare, ClipboardList, UserRound } from '@lucide/vue'
import { useClientTheme } from '@/composables/useClientTheme'
import { useBranding } from '@/composables/useBranding'

const route = useRoute()
useClientTheme()
const { brandName, brandMark } = useBranding()
const fullBleed = computed(() => ['product', 'community', 'post'].includes(String(route.name)))
const communityPage = computed(() => ['community', 'post'].includes(String(route.name)))
const communityFeedPage = computed(() => route.name === 'community')
const nav = [
  { to: '/', label: '首页', icon: Home },
  { to: '/mall', label: '商城', icon: ShoppingBag },
  { to: '/community', label: '社区', icon: MessagesSquare },
  { to: '/work-orders', label: '订单', icon: ClipboardList },
  { to: '/profile', label: '我的', icon: UserRound },
]

</script>

<template>
  <div class="app-shell">
    <header class="desktop-header">
      <RouterLink to="/" class="brand"><span class="brand-mark">{{ brandMark }}</span><span>{{ brandName }}</span></RouterLink>
      <nav><RouterLink v-for="item in nav" :key="item.to" :to="item.to">{{ item.label }}</RouterLink></nav>
    </header>
    <main :class="['page-container', { 'page-full': fullBleed, 'page-community': communityPage, 'page-community-feed': communityFeedPage }]">
      <RouterView />
    </main>
    <nav class="bottom-nav" aria-label="主要导航">
      <RouterLink v-for="item in nav" :key="item.to" :to="item.to">
        <component :is="item.icon" :size="21" :stroke-width="1.9" />
        <span>{{ item.label }}</span>
      </RouterLink>
    </nav>
  </div>
</template>
