<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Bookmark, Eye, Heart, ImagePlus, MessageCircle, PenLine, RefreshCw, Search, Send, ThumbsDown } from '@lucide/vue'
import InlineNotice from '@/components/InlineNotice.vue'
import SearchableSelect from '@/components/SearchableSelect.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import { api, idempotencyKey, isRequestCancelled, unwrap } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { ForumPost } from '@/types'

const auth = useAuthStore()
const router = useRouter()
const posts = ref<ForumPost[]>([])
const categories = ref<Array<{id:number;name:string;slug:string}>>([])
const sort = ref('recommended')
const category = ref('')
const query = ref('')
const appliedQuery = ref('')
const loading = ref(false)
const loadingFeed = ref(false)
const hasLoaded = ref(false)
const error = ref('')
const communityRoot = ref<HTMLElement|null>(null)
const composerTitle = ref<HTMLInputElement|null>(null)
const searchInput = ref<HTMLInputElement|null>(null)
const actionBusy = reactive<Record<string, boolean>>({})
const files = ref<File[]>([])
const previews = ref<string[]>([])
const form = reactive({ category_id: 0, title: '', content: '' })
const feedMeta = reactive({ personalized:false, description:'按内容新鲜度、互动质量与作者多样性排序' })
const feedbackUndo = ref<{post:ForumPost;index:number}|null>(null)
let feedbackTimer:number|undefined
let signalTimer:number|undefined
let cardObserver:IntersectionObserver|null = null
let feedRequest:AbortController|null = null
let feedRequestSequence = 0
const visibleStarted = new Map<number,number>()
const impressed = new Set<number>()
const pendingSignals = new Map<number,{post_id:number;impression?:boolean;dwell_time_ms?:number}>()

const canPublish = computed(() => Boolean(form.category_id && form.title.trim() && form.content.trim()))
const categoryOptions = computed(() => categories.value.map(item=>({value:item.id,label:item.name})))

async function quickPost(){
  if(!auth.isAuthenticated){await router.push('/auth?redirect=/community');return}
  await nextTick()
  composerTitle.value?.scrollIntoView({behavior:'smooth',block:'center'})
  composerTitle.value?.focus()
}

async function focusSearch(){
  await nextTick()
  searchInput.value?.scrollIntoView({behavior:'smooth',block:'center'})
  searchInput.value?.focus({preventScroll:true})
}

function shortTime(value:string){
  const date = new Date(value)
  const diff = Date.now() - date.getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时`
  return date.toLocaleDateString('zh-CN', { month:'numeric', day:'numeric' })
}

async function loadFeed(searchTerm = appliedQuery.value){
  const requestedQuery = searchTerm.trim()
  const requestSequence = ++feedRequestSequence
  feedRequest?.abort()
  const controller = new AbortController()
  feedRequest = controller
  captureAllDwell()
  cardObserver?.disconnect()
  loadingFeed.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ sort:sort.value, page_size:'30' })
    if(category.value) params.set('category', category.value)
    if(requestedQuery) params.set('q', requestedQuery)
    const data = unwrap<{items:ForumPost[];feed?:{personalized:boolean;description:string}}>(await api.get(`/forum/posts?${params}`, { signal:controller.signal }))
    if(requestSequence !== feedRequestSequence) return
    posts.value = data.items
    appliedQuery.value = requestedQuery
    hasLoaded.value = true
    if(data.feed) Object.assign(feedMeta, data.feed)
    await nextTick()
    observeCards()
  } catch(e) {
    if(isRequestCancelled(e)) return
    error.value = e instanceof Error ? e.message : '帖子加载失败，请重试'
  } finally {
    if(requestSequence === feedRequestSequence){
      loadingFeed.value = false
      feedRequest = null
    }
  }
}

function submitSearch(){ void loadFeed(query.value) }
function clearSearch(){ query.value=''; void loadFeed('') }
function refreshFeed(){ void loadFeed(appliedQuery.value) }

function queueSignal(postId:number, patch:{impression?:boolean;dwell_time_ms?:number}){
  if(!auth.isAuthenticated) return
  const current = pendingSignals.get(postId) || {post_id:postId}
  current.impression = Boolean(current.impression || patch.impression)
  current.dwell_time_ms = Math.min(300_000, (current.dwell_time_ms || 0) + (patch.dwell_time_ms || 0))
  pendingSignals.set(postId, current)
  window.clearTimeout(signalTimer)
  signalTimer = window.setTimeout(flushSignals, 650)
}

async function flushSignals(){
  if(!auth.isAuthenticated || !pendingSignals.size) return
  const items = [...pendingSignals.values()].filter(item=>item.impression || item.dwell_time_ms)
  pendingSignals.clear()
  if(!items.length) return
  try{await api.post('/forum/signals',{items})}catch{/* 推荐信号失败不阻断浏览 */}
}

function captureDwell(postId:number){
  const started = visibleStarted.get(postId)
  if(started === undefined) return
  visibleStarted.delete(postId)
  const elapsed = Math.round(performance.now() - started)
  if(elapsed >= 1_200) queueSignal(postId,{dwell_time_ms:elapsed})
}

function captureAllDwell(){
  for(const postId of [...visibleStarted.keys()]) captureDwell(postId)
}

function observeCards(){
  if(!cardObserver || !communityRoot.value) return
  communityRoot.value.querySelectorAll<HTMLElement>('.x-post-card[data-post-id]').forEach(card=>cardObserver?.observe(card))
}

async function reduceRecommendation(post:ForumPost){
  if(!auth.isAuthenticated){
    await router.push(`/auth?redirect=${encodeURIComponent('/community')}`)
    return
  }
  const index = posts.value.findIndex(item=>item.id===post.id)
  if(index < 0) return
  posts.value.splice(index,1)
  feedbackUndo.value = {post,index}
  window.clearTimeout(feedbackTimer)
  feedbackTimer = window.setTimeout(()=>{feedbackUndo.value=null},6_000)
  try{
    await api.post('/forum/signals',{items:[{post_id:post.id,not_interested:true}]})
  }catch(e){
    posts.value.splice(index,0,post)
    feedbackUndo.value = null
    error.value = e instanceof Error ? e.message : '反馈失败，请重试'
  }
}

async function undoRecommendation(){
  const feedback = feedbackUndo.value
  if(!feedback) return
  try{
    await api.post('/forum/signals',{items:[{post_id:feedback.post.id,not_interested:false}]})
    posts.value.splice(Math.min(feedback.index,posts.value.length),0,feedback.post)
    feedbackUndo.value = null
    await nextTick(); observeCards()
  }catch(e){error.value=e instanceof Error?e.message:'撤销失败，请重试'}
}

function chooseImages(event:Event){
  const selected = Array.from((event.target as HTMLInputElement).files || []).slice(0, 4)
  previews.value.forEach(URL.revokeObjectURL)
  files.value = selected
  previews.value = selected.map(URL.createObjectURL)
}

async function publish(){
  if(!auth.isAuthenticated) return router.push('/auth?redirect=/community')
  if(loading.value || !canPublish.value) return
  loading.value = true
  error.value = ''
  try {
    const post = unwrap<ForumPost>(await api.post('/forum/posts', form, { headers:{'Idempotency-Key':idempotencyKey('post')} }))
    for(const file of files.value){
      const body = new FormData(); body.append('file', file)
      await api.post(`/forum/posts/${post.id}/images`, body)
    }
    Object.assign(form, { category_id:categories.value[0]?.id || 0, title:'', content:'' })
    previews.value.forEach(URL.revokeObjectURL); previews.value=[]; files.value=[]
    await loadFeed(appliedQuery.value)
  } catch(e) { error.value = e instanceof Error ? e.message : '发布失败' }
  finally { loading.value = false }
}

function replyFromPreview(post:ForumPost){
  const target = `/community/${post.id}?reply=1`
  if(!auth.isAuthenticated) return router.push(`/auth?redirect=${encodeURIComponent(target)}`)
  return router.push(target)
}

async function previewAction(post:ForumPost, action:'like'|'favorite'){
  const key = `${post.id}:${action}`
  if(actionBusy[key]) return
  if(!auth.isAuthenticated){
    await router.push(`/auth?redirect=${encodeURIComponent(`/community/${post.id}`)}`)
    return
  }
  actionBusy[key] = true
  try{
    const data = unwrap<any>(await api.post(`/forum/posts/${post.id}/${action}`))
    if(action==='like'){post.liked=data.liked;post.like_count=data.like_count}
    else post.favorited=data.favorited
  }catch(e){error.value=e instanceof Error?e.message:'操作失败'}
  finally{actionBusy[key]=false}
}

watch([sort, category], refreshFeed)
onMounted(async()=>{
  cardObserver = new IntersectionObserver(entries=>{
    for(const entry of entries){
      const postId = Number((entry.target as HTMLElement).dataset.postId)
      if(!postId) continue
      if(entry.isIntersecting && entry.intersectionRatio >= .55){
        if(!visibleStarted.has(postId)) visibleStarted.set(postId,performance.now())
        if(!impressed.has(postId)){impressed.add(postId);queueSignal(postId,{impression:true})}
      }else captureDwell(postId)
    }
  },{threshold:[0,.55]})
  categories.value = unwrap(await api.get('/forum/categories'))
  form.category_id = categories.value[0]?.id || 0
  await loadFeed()
})
onBeforeUnmount(() => {
  feedRequest?.abort()
  previews.value.forEach(URL.revokeObjectURL)
  captureAllDwell(); void flushSignals()
  cardObserver?.disconnect()
  window.clearTimeout(signalTimer);window.clearTimeout(feedbackTimer)
})
</script>

<template>
  <div ref="communityRoot" class="x-community-shell">
    <main class="x-feed" :aria-busy="loadingFeed">
      <header class="x-feed-header">
        <div><h1>社区</h1><p>维修、飞行与设备经验</p></div>
        <button type="button" class="x-header-search" aria-label="搜索社区帖子" title="搜索社区帖子" @click="focusSearch"><Search :size="20"/></button>
      </header>
      <div class="x-feed-tabs" role="tablist" aria-label="帖子排序">
        <button v-for="item in [{v:'recommended',l:'推荐'},{v:'latest',l:'最新'},{v:'hot',l:'热门'}]" :key="item.v" :class="{active:sort===item.v}" @click="sort=item.v">{{item.l}}</button>
      </div>

      <form class="x-composer" @submit.prevent="publish">
        <UserAvatar :src="auth.account?.avatar_url" :name="auth.account?.nickname || '访客'" :size="46" />
        <div class="x-composer-body">
          <template v-if="auth.isAuthenticated">
            <input ref="composerTitle" v-model="form.title" class="x-title-input" maxlength="180" placeholder="一句话概括你的分享" required />
            <textarea v-model="form.content" maxlength="20000" placeholder="分享维修、飞行或设备经验…" required />
            <div v-if="previews.length" class="x-media-grid">
              <img v-for="preview in previews" :key="preview" :src="preview" alt="待上传图片预览" />
            </div>
            <InlineNotice :message="error" type="error" />
            <div class="x-composer-actions">
              <label class="x-media-button" title="添加图片"><ImagePlus :size="21"/><input type="file" accept="image/jpeg,image/png,image/webp" multiple @change="chooseImages" /></label>
              <SearchableSelect v-model="form.category_id" :options="categoryOptions" aria-label="帖子分类" search-placeholder="检索帖子分类" />
              <button class="button x-post-button" :disabled="loading || !canPublish"><Send :size="17"/>{{ loading ? '发布中' : '发布' }}</button>
            </div>
          </template>
          <button v-else type="button" class="x-login-prompt" @click="router.push('/auth?redirect=/community')">登录后分享你的经验</button>
        </div>
      </form>

      <form class="x-search-row" role="search" @submit.prevent="submitSearch">
        <Search :size="17"/><input ref="searchInput" v-model="query" type="search" placeholder="搜索帖子" aria-label="搜索帖子" autocomplete="off"/>
        <button v-if="query" type="button" class="x-search-clear" aria-label="清除搜索" @click="clearSearch">清除</button>
        <button type="submit" :aria-busy="loadingFeed">{{loadingFeed?'搜索中…':'搜索'}}</button>
      </form>
      <div v-if="appliedQuery" class="x-search-status" role="status"><span>“{{appliedQuery}}”</span><strong>找到 {{posts.length}} 条帖子</strong></div>
      <div class="x-category-strip">
        <button :class="{active:category===''}" @click="category=''">全部</button>
        <button v-for="item in categories" :key="item.id" :class="{active:category===item.slug}" @click="category=item.slug">{{item.name}}</button>
      </div>

      <div v-if="sort==='recommended'" class="x-feed-context" role="status">
        <div><strong>{{feedMeta.personalized ? '为你推荐' : '推荐内容'}}</strong><span>{{feedMeta.description}}</span></div>
        <button type="button" :disabled="loadingFeed" aria-label="刷新推荐" title="刷新推荐" @click="refreshFeed"><RefreshCw :size="17" :class="{spin:loadingFeed}"/></button>
      </div>
      <InlineNotice :message="error" type="error" />

      <div v-if="loadingFeed && hasLoaded" class="x-feed-refreshing" role="status">正在更新帖子…</div>
      <div v-if="loadingFeed && !hasLoaded" class="x-feed-skeleton" aria-label="正在加载帖子">
        <div v-for="item in 3" :key="item" class="x-skeleton-card"><i/><div><b/><span/><span/></div></div>
      </div>
      <template v-else-if="posts.length">
        <article v-for="post in posts" :key="post.id" class="x-post-card" :data-post-id="post.id">
          <UserAvatar :src="post.author.avatar_url" :name="post.author.nickname" :size="46" />
          <div class="x-post-body">
            <RouterLink :to="`/community/${post.id}`" class="x-post-link">
              <div class="x-author-line"><strong>{{post.author.nickname}}</strong><span>{{post.author.identifier || `@${post.author.username}`}}</span><span>·</span><span>{{shortTime(post.created_at)}}</span><span v-if="post.is_pinned" class="x-pin">置顶</span></div>
              <span v-if="sort==='recommended' && post.recommendation_reason" class="x-recommendation-reason">{{post.recommendation_reason}}</span>
              <span class="x-category-label">{{post.category.name}}</span>
              <h2>{{post.title}}</h2>
              <p>{{post.content}}</p>
              <div v-if="post.images.length" :class="['x-media-grid', `count-${Math.min(post.images.length,4)}`]">
                <img v-for="image in post.images.slice(0,4)" :key="image.id" :src="image.url" alt="帖子图片" loading="lazy" />
              </div>
            </RouterLink>
            <div class="x-post-actions" aria-label="帖子快捷操作">
              <button title="回复" :aria-label="`回复 ${post.title}`" @click="replyFromPreview(post)"><MessageCircle :size="18"/>{{post.comment_count}}</button>
              <button :class="{liked:post.liked}" title="喜欢" :aria-label="`${post.liked?'取消喜欢':'喜欢'} ${post.title}`" :disabled="actionBusy[`${post.id}:like`]" @click="previewAction(post,'like')"><Heart :size="18" :fill="post.liked?'currentColor':'none'"/>{{post.like_count}}</button>
              <button :class="{saved:post.favorited}" title="收藏" :aria-label="`${post.favorited?'取消收藏':'收藏'} ${post.title}`" :disabled="actionBusy[`${post.id}:favorite`]" @click="previewAction(post,'favorite')"><Bookmark :size="18" :fill="post.favorited?'currentColor':'none'"/></button>
              <span><Eye :size="18"/>{{post.view_count}}</span>
              <button title="不感兴趣" :aria-label="`减少推荐 ${post.title}`" @click="reduceRecommendation(post)"><ThumbsDown :size="17"/></button>
            </div>
          </div>
        </article>
      </template>
      <div v-else class="x-feed-empty">这个分类还没有帖子，来发布第一条吧。</div>
    </main>

    <aside class="x-community-aside">
      <section><h2>社区指南</h2><p>分享真实经历，保护个人隐私；涉及维修结论时注明设备型号与现场条件。</p></section>
      <section><h2>热门话题</h2><button v-for="item in categories.slice(0,5)" :key="item.id" @click="category=item.slug"><small>设备社区</small><strong># {{item.name}}</strong></button></section>
    </aside>
    <button type="button" class="x-compose-fab" aria-label="快捷发帖" title="快捷发帖" @click="quickPost"><PenLine :size="21"/><span>发帖</span></button>
    <div v-if="feedbackUndo" class="x-feedback-toast" role="status"><span>已减少此类内容的推荐</span><button type="button" @click="undoRecommendation">撤销</button></div>
  </div>
</template>
