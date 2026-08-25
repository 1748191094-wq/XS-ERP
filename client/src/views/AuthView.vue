<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import InlineNotice from '@/components/InlineNotice.vue'
import { useBranding } from '@/composables/useBranding'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const { brandName, brandMark } = useBranding()
const mode = ref<'login' | 'register'>('login')
const loading = ref(false)
const error = ref('')
const form = reactive({ login: '', username: '', phone: '', nickname: '', email: '', password: '' })

async function submit() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    if (mode.value === 'login') await auth.login(form.login, form.password)
    else await auth.register({ username: form.username, phone: form.phone, nickname: form.nickname, email: form.email, password: form.password })
    await router.replace(String(route.query.redirect || '/'))
  } catch (e) {
    error.value = e instanceof Error ? e.message : '操作失败，请稍后再试'
  } finally { loading.value = false }
}
</script>

<template>
  <main class="auth-shell">
    <form class="auth-card" @submit.prevent="submit">
      <span class="brand-mark">{{ brandMark }}</span>
      <strong>{{ brandName }}</strong>
      <h1>{{ mode === 'login' ? '欢迎回来' : '创建客户账号' }}</h1>
      <p>维修、回收、商城和社区，使用同一份门店业务数据。</p>
      <div class="tabs"><button type="button" :class="{ active: mode === 'login' }" @click="mode='login'">登录</button><button type="button" :class="{ active: mode === 'register' }" @click="mode='register'">注册</button></div>
      <div class="form-grid" style="margin-top:18px">
        <div v-if="mode==='login'" class="field"><label>识别码 / 手机号 / 邮箱</label><input v-model="form.login" autocomplete="username" placeholder="@识别码或登录信息" required /></div>
        <template v-else>
          <div class="field"><label>识别码</label><div class="identifier-input"><span>@</span><input v-model="form.username" autocomplete="username" minlength="3" maxlength="80" placeholder="xiaoming" required /></div><small class="muted">用于全站识别，每个自然年最多可修改 2 次。</small></div>
          <div class="field"><label>昵称</label><input v-model="form.nickname" autocomplete="nickname" required /></div>
          <div class="field"><label>手机号</label><input v-model="form.phone" inputmode="tel" autocomplete="tel" required /></div>
          <div class="field"><label>邮箱（选填）</label><input v-model="form.email" type="email" autocomplete="email" /></div>
        </template>
        <div class="field"><label>密码</label><input v-model="form.password" type="password" autocomplete="current-password" minlength="8" required /><small class="muted">至少 8 位，同时包含字母和数字</small></div>
        <InlineNotice :message="error" type="error" />
        <button class="button wide" :disabled="loading">{{ loading ? '正在提交…' : (mode==='login' ? '登录' : '注册并登录') }}</button>
      </div>
    </form>
  </main>
</template>
