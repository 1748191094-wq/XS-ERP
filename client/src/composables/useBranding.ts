import { computed, readonly, ref } from 'vue'

const DEFAULT_BRAND_NAME = '服务品牌'
const brandName = ref(DEFAULT_BRAND_NAME)
let loading: Promise<void> | null = null

function markFor(value: string): string {
  const characters = [...value.replace(/\s+/g, '')]
  return characters.slice(0, Math.min(2, characters.length)).join('').toUpperCase() || '服'
}

async function loadBranding(): Promise<void> {
  if (loading) return loading
  loading = fetch('/api/branding', { cache: 'no-store' })
    .then(async response => {
      if (!response.ok) return
      const body = await response.json() as { data?: { brand_name?: string } }
      brandName.value = body.data?.brand_name?.trim() || DEFAULT_BRAND_NAME
      document.title = `${brandName.value} · 客户端`
    })
    .catch(() => undefined)
  return loading
}

export function useBranding() {
  void loadBranding()
  return {
    brandName: readonly(brandName),
    brandMark: computed(() => markFor(brandName.value)),
    reloadBranding: loadBranding,
  }
}
