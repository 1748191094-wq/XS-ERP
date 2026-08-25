<script setup lang="ts">
defineProps<{ message?: string; type?: 'error' | 'success' | 'info' }>()
</script>

<template><p v-if="message" :class="['inline-notice', type || 'info']" role="status">{{ message }}</p></template>
