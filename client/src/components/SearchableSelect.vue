<script setup lang="ts">
import { computed, ref } from 'vue'

defineOptions({ inheritAttrs:false })

interface SearchableSelectOption {
  value: string | number
  label: string
  disabled?: boolean
}

const props = withDefaults(defineProps<{
  modelValue?: string | number | null
  options: SearchableSelectOption[]
  placeholder?: string
  searchPlaceholder?: string
  searchableThreshold?: number
}>(), {
  placeholder:'',
  searchPlaceholder:'输入关键词检索',
  searchableThreshold:10,
})
const emit = defineEmits<{ 'update:modelValue':[value:string|number] }>()
const query = ref('')
const searchable = computed(() => props.options.length > props.searchableThreshold)
const filteredOptions = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase('zh-CN')
  if(!searchable.value || !keyword) return props.options
  return props.options.filter(item =>
    item.value === props.modelValue || item.label.toLocaleLowerCase('zh-CN').includes(keyword)
  )
})

function change(event:Event){
  const raw = (event.target as HTMLSelectElement).value
  const option = props.options.find(item => String(item.value) === raw)
  emit('update:modelValue', option?.value ?? raw)
}
</script>

<template>
  <div class="searchable-select">
    <input
      v-if="searchable"
      v-model="query"
      class="searchable-select__search"
      type="search"
      :placeholder="searchPlaceholder"
      :aria-label="searchPlaceholder"
      autocomplete="off"
    />
    <select v-bind="$attrs" :value="modelValue == null ? '' : String(modelValue)" @change="change">
      <option v-if="placeholder" value="" disabled>{{placeholder}}</option>
      <option v-for="item in filteredOptions" :key="String(item.value)" :value="String(item.value)" :disabled="item.disabled">{{item.label}}</option>
    </select>
    <small v-if="searchable && query" class="muted">找到 {{filteredOptions.length}} 项</small>
  </div>
</template>
