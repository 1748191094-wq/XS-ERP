import { computed, ref } from 'vue'

export type ClientTheme = 'light' | 'dark'

const savedTheme = localStorage.getItem('serviceClientTheme')
const theme = ref<ClientTheme>(
  savedTheme === 'light' || savedTheme === 'dark'
    ? savedTheme
    : window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
)

function applyTheme(value: ClientTheme) {
  document.documentElement.dataset.theme = value
  document.documentElement.style.colorScheme = value
  document.querySelector('meta[name="theme-color"]')?.setAttribute(
    'content',
    value === 'dark' ? '#000000' : '#f5f5f7',
  )
}

applyTheme(theme.value)

export function useClientTheme() {
  const darkMode = computed(() => theme.value === 'dark')
  function setTheme(value: ClientTheme) {
    theme.value = value
    localStorage.setItem('serviceClientTheme', value)
    applyTheme(value)
  }
  return { theme, darkMode, setTheme }
}
