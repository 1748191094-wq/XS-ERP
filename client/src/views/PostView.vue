<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Bookmark, Eye, Flag, Heart, MessageCircle, Send } from '@lucide/vue'
import PageHeader from '@/components/PageHeader.vue'
import InlineNotice from '@/components/InlineNotice.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import { api, idempotencyKey, unwrap } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { ForumAuthor, ForumPost } from '@/types'

interface Comment {id:number;parent_id?:number|null;content:string;like_count:number;liked:boolean;author:ForumAuthor;created_at:string}
const route = useRoute()
const auth = useAuthStore()
const post = ref<ForumPost|null>(null)
const comments = ref<Comment[]>([])
const content = ref('')
const error = ref('')
const loading = ref(false)
const replyBox = ref<HTMLTextAreaElement|null>(null)
const replyingTo = ref<Comment|null>(null)

async function load(){
  const data = unwrap<{post:ForumPost;comments:Comment[]}>(await api.get(`/forum/posts/${route.params.id}`))
  post.value = data.post; comments.value = data.comments
}
async function toggle(action:'like'|'favorite'){
  if(!post.value) return
  const data = unwrap<any>(await api.post(`/forum/posts/${post.value.id}/${action}`))
  if(action==='like'){post.value.liked=data.liked;post.value.like_count=data.like_count}
  else post.value.favorited=data.favorited
}
async function comment(){
  if(!content.value.trim() || loading.value) return
  loading.value=true; error.value=''
  try{
    await api.post(`/forum/posts/${route.params.id}/comments`, {content:content.value,parent_id:replyingTo.value?.id}, {headers:{'Idempotency-Key':idempotencyKey('comment')}})
    content.value=''; replyingTo.value=null; await load()
  }catch(e){error.value=e instanceof Error?e.message:'评论失败'}finally{loading.value=false}
}
async function focusReply(){await nextTick();replyBox.value?.focus();replyBox.value?.scrollIntoView({behavior:'smooth',block:'center'})}
async function startReply(item:Comment){replyingTo.value=item;await focusReply()}
async function toggleCommentLike(item:Comment){
  const data = unwrap<any>(await api.post(`/forum/comments/${item.id}/like`))
  item.liked=data.liked; item.like_count=data.like_count
}
async function report(){
  if(!post.value)return
  const reason=window.prompt('请简要说明举报原因')
  if(reason) await api.post('/forum/reports',{post_id:post.value.id,reason})
}
onMounted(async()=>{await load();if(route.query.reply==='1')await focusReply()})
</script>

<template>
  <div class="x-thread-shell">
    <PageHeader title="帖子" back />
    <article v-if="post" class="x-thread-post">
      <header class="x-thread-author">
        <UserAvatar :src="post.author.avatar_url" :name="post.author.nickname" :size="48" />
        <div><strong>{{post.author.nickname}}</strong><span>{{post.author.identifier || `@${post.author.username}`}}</span></div>
      </header>
      <span class="x-category-label">{{post.category.name}}</span>
      <h1>{{post.title}}</h1>
      <p class="x-thread-content">{{post.content}}</p>
      <div v-if="post.images.length" :class="['x-media-grid', `count-${Math.min(post.images.length,4)}`]">
        <img v-for="image in post.images" :key="image.id" :src="image.url" alt="帖子图片" />
      </div>
      <div class="x-thread-time">{{new Date(post.created_at).toLocaleString('zh-CN')}} · <Eye :size="16"/> {{post.view_count}} 次浏览</div>
      <div class="x-thread-stats"><strong>{{post.like_count}}</strong> 喜欢 <strong>{{post.comment_count}}</strong> 回复</div>
      <div class="x-thread-actions">
        <button title="回复" @click="focusReply"><MessageCircle :size="21"/></button>
        <button :class="{liked:post.liked}" title="喜欢" @click="toggle('like')"><Heart :size="21" :fill="post.liked?'currentColor':'none'"/></button>
        <button :class="{saved:post.favorited}" title="收藏" @click="toggle('favorite')"><Bookmark :size="21" :fill="post.favorited?'currentColor':'none'"/></button>
        <button title="举报" @click="report"><Flag :size="20"/></button>
      </div>
    </article>

    <form class="x-reply-composer" @submit.prevent="comment">
      <UserAvatar :src="auth.account?.avatar_url" :name="auth.account?.nickname" :size="42" />
      <div><div v-if="replyingTo" class="x-replying-banner"><span>回复 @{{replyingTo.author.username}}</span><button type="button" @click="replyingTo=null">取消</button></div><textarea ref="replyBox" v-model="content" maxlength="3000" :placeholder="replyingTo?`回复 @${replyingTo.author.username}`:'发布你的回复'" required/><InlineNotice :message="error" type="error" /></div>
      <button class="button" :disabled="loading || !content.trim()"><Send :size="17"/>{{loading?'发布中':'回复'}}</button>
    </form>

    <section class="x-replies" aria-label="评论列表">
      <article v-for="item in comments" :key="item.id" :class="['x-reply',{'is-child':item.parent_id}]">
        <UserAvatar :src="item.author.avatar_url" :name="item.author.nickname" :size="42" />
        <div class="x-post-body">
          <div class="x-author-line"><strong>{{item.author.nickname}}</strong><span>{{item.author.identifier || `@${item.author.username}`}}</span><span>·</span><span>{{new Date(item.created_at).toLocaleDateString('zh-CN')}}</span></div>
          <p>{{item.content}}</p>
          <div class="x-post-actions"><button title="回复这条评论" @click="startReply(item)"><MessageCircle :size="17"/>回复</button><button :class="{liked:item.liked}" title="喜欢这条评论" @click="toggleCommentLike(item)"><Heart :size="17" :fill="item.liked?'currentColor':'none'"/>{{item.like_count}}</button></div>
        </div>
      </article>
      <div v-if="!comments.length" class="x-feed-empty">还没有回复，来参与讨论吧。</div>
    </section>
  </div>
</template>
