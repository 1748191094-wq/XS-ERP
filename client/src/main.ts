import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { CLIENT_SESSION_EXPIRED_EVENT } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import './assets/main.css'
import './assets/community.css'
import './assets/interactions.css'

const pinia = createPinia()

window.addEventListener(CLIENT_SESSION_EXPIRED_EVENT, () => {
  const auth = useAuthStore(pinia)
  const wasAuthenticated = auth.isAuthenticated
  auth.clearSession()
  const current = router.currentRoute.value
  if (wasAuthenticated && !current.meta.public && current.name !== 'auth') {
    void router.replace({ name:'auth', query:{ redirect:current.fullPath } })
  }
})

createApp(App).use(pinia).use(router).mount('#app')
