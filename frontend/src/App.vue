<script setup lang="ts">
import { ref } from 'vue'
import AppSidebar from './components/AppSidebar.vue'
import ChatComposer from './components/ChatComposer.vue'
import ChatView from './components/ChatView.vue'
import ConversationNav from './components/ConversationNav.vue'
import KnowledgeView from './components/KnowledgeView.vue'
import StyleDrawer from './components/StyleDrawer.vue'
import { useBackend } from './composables/useBackend'
import { useStyle } from './composables/useStyle'
import { useWorkspace } from './composables/useWorkspace'

const { apply } = useStyle()
apply()

const { view, activeThread } = useWorkspace()
const scrollRoot = ref<HTMLElement | null>(null)
useBackend()
</script>

<template>
  <div class="ambient" aria-hidden="true">
    <span class="blob blob-a" />
    <span class="blob blob-b" />
    <span class="grid-lines" />
    <span class="grain" />
  </div>

  <div class="app">
    <AppSidebar />

    <main class="main">
      <header class="topbar">
        <div class="crumbs">
          <span class="crumb-muted">工作台</span>
          <span class="crumb-sep">/</span>
          <strong>{{ view === 'chat' ? activeThread.title : '知识库' }}</strong>
        </div>
      </header>

      <div class="chat-scroll-region">
        <div ref="scrollRoot" class="scroll">
          <ChatView v-show="view === 'chat'" />
          <KeepAlive>
            <KnowledgeView v-if="view === 'kb'" />
          </KeepAlive>
        </div>
        <ConversationNav v-if="view === 'chat'" :scroll-root="scrollRoot" />
      </div>

      <ChatComposer v-show="view === 'chat'" />
    </main>
  </div>

  <StyleDrawer />
</template>
