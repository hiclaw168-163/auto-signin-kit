import { createApp } from 'vue'
import naive from 'naive-ui'
import App from './App.vue'

// 全量注册 naive-ui: 管理台体量小,不做按需引入的额外配置。
createApp(App).use(naive).mount('#app')
