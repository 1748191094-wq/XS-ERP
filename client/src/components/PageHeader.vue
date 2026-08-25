<script setup lang="ts">
import { ChevronLeft } from '@lucide/vue'
import { useRouter } from 'vue-router'

defineProps<{ title: string; subtitle?: string; back?: boolean }>()
const router = useRouter()
</script>

<template>
  <header class="page-header">
    <button v-if="back" class="icon-button" aria-label="返回" @click="router.back()"><ChevronLeft /></button>
    <div><h1>{{ title }}</h1><p v-if="subtitle">{{ subtitle }}</p></div>
    <div class="page-header-action"><slot /></div>
  </header>
</template>
