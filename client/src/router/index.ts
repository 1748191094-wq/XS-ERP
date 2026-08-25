import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory('/client/'),
  routes: [
    { path: '/auth', name: 'auth', component: () => import('@/views/AuthView.vue'), meta: { public: true } },
    {
      path: '/',
      component: () => import('@/layouts/ClientLayout.vue'),
      children: [
        { path: '', name: 'home', component: () => import('@/views/HomeView.vue') },
        { path: 'mall', name: 'mall', component: () => import('@/views/MallView.vue'), meta: { public: true } },
        { path: 'mall/:id', name: 'product', component: () => import('@/views/ProductView.vue'), meta: { public: true } },
        { path: 'cart', name: 'cart', component: () => import('@/views/CartView.vue') },
        { path: 'community', name: 'community', component: () => import('@/views/CommunityView.vue'), meta: { public: true } },
        { path: 'community/:id', name: 'post', component: () => import('@/views/PostView.vue') },
        { path: 'repair/new', name: 'repair-new', component: () => import('@/views/RepairView.vue') },
        { path: 'recycle/new', name: 'recycle-new', component: () => import('@/views/RecycleView.vue') },
        { path: 'replacement/new', name: 'replacement-new', component: () => import('@/views/ReplacementView.vue') },
        { path: 'work-orders', name: 'work-orders', component: () => import('@/views/WorkOrdersView.vue') },
        { path: 'profile', name: 'profile', component: () => import('@/views/ProfileView.vue') },
      ],
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

const CHUNK_RELOAD_KEY = 'serviceClientChunkReload'
const DYNAMIC_IMPORT_ERROR = /failed to fetch dynamically imported module|importing a module script failed|error loading dynamically imported module|chunkloaderror|loading chunk .* failed/i

function clientRouteUrl(fullPath: string): string {
  const relative = fullPath.replace(/^\/+/, '')
  return new URL(relative || '.', `${window.location.origin}/client/`).toString()
}

router.beforeEach(async (to) => {
  document.documentElement.classList.add('client-navigating')
  const auth = useAuthStore()
  await auth.restore()
  if (!to.meta.public && !auth.isAuthenticated) return { name: 'auth', query: { redirect: to.fullPath } }
  if (to.name === 'auth' && auth.isAuthenticated) return { name: 'home' }
})

router.afterEach((_to, _from, failure) => {
  document.documentElement.classList.remove('client-navigating')
  if (!failure) sessionStorage.removeItem(CHUNK_RELOAD_KEY)
})

router.onError((error, to) => {
  document.documentElement.classList.remove('client-navigating')
  const message = error instanceof Error ? error.message : String(error)
  if (!DYNAMIC_IMPORT_ERROR.test(message)) return

  const now = Date.now()
  const previous = sessionStorage.getItem(CHUNK_RELOAD_KEY)?.split('|') || []
  const previousAt = Number(previous[1] || 0)
  if (previous[0] === to.fullPath && now - previousAt < 15_000) return

  sessionStorage.setItem(CHUNK_RELOAD_KEY, `${to.fullPath}|${now}`)
  window.location.replace(clientRouteUrl(to.fullPath))
})

export default router
