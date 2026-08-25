import { defineStore } from 'pinia'
import { api, unwrap } from '@/api/client'
import type { Account } from '@/types'

interface AuthState {
  account: Account | null
  checked: boolean
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({ account: null, checked: false }),
  getters: { isAuthenticated: (state) => Boolean(state.account) },
  actions: {
    clearSession() {
      this.account = null
      this.checked = true
      sessionStorage.removeItem('serviceClientCsrf')
    },
    applySession(account: Account) {
      this.account = account
      if (account.csrf_token) sessionStorage.setItem('serviceClientCsrf', account.csrf_token)
    },
    async restore() {
      if (this.checked) return
      try {
        this.applySession(unwrap(await api.get('/auth/me')))
      } catch {
        this.account = null
      } finally {
        this.checked = true
      }
    },
    async login(login: string, password: string) {
      this.applySession(unwrap(await api.post('/auth/login', { login, password })))
      this.checked = true
    },
    async register(payload: Record<string, string>) {
      this.applySession(unwrap(await api.post('/auth/register', payload)))
      this.checked = true
    },
    async logout() {
      try { await api.post('/auth/logout') } finally {
        this.clearSession()
      }
    },
  },
})
