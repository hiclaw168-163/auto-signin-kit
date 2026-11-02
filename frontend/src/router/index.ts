import { createRouter, createWebHashHistory } from 'vue-router'
import AdminLayout from '../layouts/AdminLayout.vue'

// hash 路由: 前端由 FastAPI StaticFiles 托管,history 模式的深链接会 404,
// hash 模式对静态托管零配置成立(单机工具,不需要 SEO 友好 URL)。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      component: AdminLayout,
      children: [
        { path: '', redirect: '/dashboard' },
        { path: 'dashboard', name: 'dashboard', component: () => import('../views/DashboardView.vue'), meta: { title: '看板' } },
        { path: 'history', name: 'history', component: () => import('../views/HistoryView.vue'), meta: { title: '历史' } },
        { path: 'schedule', name: 'schedule', component: () => import('../views/ScheduleView.vue'), meta: { title: '调度' } },
        { path: 'config', name: 'config', component: () => import('../views/ConfigView.vue'), meta: { title: '配置' } },
        { path: 'doctor', name: 'doctor', component: () => import('../views/DoctorView.vue'), meta: { title: '诊断' } },
      ],
    },
  ],
})

export default router
