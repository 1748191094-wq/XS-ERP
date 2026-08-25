import axios, { AxiosError } from 'axios'

export class ApiError extends Error {
  constructor(public code: string, message: string, public status?: number) {
    super(message)
  }
}

export const CLIENT_SESSION_EXPIRED_EVENT = 'service-client-session-expired'

export const api = axios.create({
  baseURL: '/api/client',
  timeout: 20_000,
  withCredentials: true,
})

api.interceptors.request.use((config) => {
  const csrf = sessionStorage.getItem('serviceClientCsrf')
  if (csrf && !['get', 'head', 'options'].includes((config.method || 'get').toLowerCase())) {
    config.headers['X-CSRF-Token'] = csrf
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ error?: { code?: string; message?: string } }>) => {
    if (error.code === 'ERR_CANCELED') {
      return Promise.reject(new ApiError('request_cancelled', '请求已由更新的操作替代'))
    }
    const body = error.response?.data?.error
    if (error.response?.status === 401) {
      const hadSession = Boolean(sessionStorage.getItem('serviceClientCsrf'))
      sessionStorage.removeItem('serviceClientCsrf')
      if (hadSession) window.dispatchEvent(new CustomEvent(CLIENT_SESSION_EXPIRED_EVENT))
    }
    const message = body?.message || (error.code === 'ECONNABORTED' ? '请求超时，请稍后重试' : '网络连接异常，请检查后重试')
    return Promise.reject(new ApiError(body?.code || 'network_error', message, error.response?.status))
  },
)

export function isRequestCancelled(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'request_cancelled'
}

export function unwrap<T>(response: { data: { success: boolean; data: T } }): T {
  return response.data.data
}

export function idempotencyKey(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`
}
