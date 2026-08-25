export interface Account {
  id: number
  customer_id: number
  username: string
  identifier: string
  nickname: string
  phone: string
  email?: string | null
  avatar_url?: string | null
  csrf_token?: string
  expires_at?: string
}

export interface ProductSku {
  id: number
  sku: string
  name: string
  attributes: Record<string, string>
  price: string
  original_price?: string | null
  stock: number
  enabled: boolean
}

export interface Product {
  id: number
  name: string
  slug: string
  summary?: string | null
  description?: string | null
  after_sales?: string | null
  status: string
  featured: boolean
  price_from?: string | null
  category?: { id: number; name: string; slug: string } | null
  images: Array<{ id: number; url: string; alt: string }>
  skus?: ProductSku[]
}

export interface Address {
  id: number
  recipient_name: string
  phone: string
  province: string
  city: string
  district?: string | null
  detail: string
  postal_code?: string | null
  is_default: boolean
}

export interface RepairOrder {
  id: number
  order_no: string
  status: string
  status_label: string
  fault_description: string
  device: { id: number; brand: string; model: string; serial_number: string }
  current_quote?: Record<string, unknown> | null
  created_at: string
}

export interface RecycleRequest {
  id: number
  request_no: string
  status: string
  status_label: string
  reference_min: string
  reference_max: string
  maximum_price: string
  contact_name?: string | null
  contact_phone?: string | null
  contact_wechat?: string | null
  device_condition?: string | null
  notes?: string | null
  staff_quote?: string | null
  created_at: string
}

export interface ClientWorkItem {
  key: string
  type: 'repair' | 'retail' | 'recycle' | 'replacement'
  type_label: string
  id: number
  number: string
  title: string
  summary: string
  status: string
  status_label: string
  amount?: string | null
  created_at: string
  updated_at: string
}

export interface ForumAuthor {
  id: number
  username: string
  identifier: string
  nickname: string
  avatar_url?: string | null
}

export interface ForumPost {
  id: number
  title: string
  content: string
  author: ForumAuthor
  category: { id: number; name: string; slug: string }
  view_count: number
  like_count: number
  comment_count: number
  liked: boolean
  favorited: boolean
  is_pinned: boolean
  is_featured: boolean
  recommendation_reason?: string | null
  images: Array<{ id: number; url: string }>
  created_at: string
}
