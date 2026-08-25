<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  src?: string | null
  name?: string | null
  size?: number
}>(), { src: null, name: '用户', size: 44 })

const failed = ref(false)
const initial = computed(() => (props.name?.trim().slice(0, 1) || '用').toUpperCase())
watch(() => props.src, () => { failed.value = false })
</script>

<template>
  <span class="user-avatar" :style="{ width: `${size}px`, height: `${size}px` }">
    <img v-if="src && !failed" :src="src" :alt="`${name || '用户'}的头像`" @error="failed=true" />
    <span v-else aria-hidden="true">{{ initial }}</span>
  </span>
</template>
