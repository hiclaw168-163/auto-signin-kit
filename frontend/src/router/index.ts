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
        { path: 'dashboard', name: 'dashboard', component: () => import('../views/DashboardView.vue'), meta: { title: '签到总览' } },
        { path: 'accounts', name: 'accounts', component: () => import('../views/AccountsView.vue'), meta: { title: '账户管理' } },
        { path: 'credits', name: 'credits', component: () => import('../views/CreditsView.vue'), meta: { title: '积分统计' } },
        { path: 'apps', name: 'apps', component: () => import('../views/AppsView.vue'), meta: { title: '应用管理' } },
        { path: 'history', name: 'history', component: () => import('../views/HistoryView.vue'), meta: { title: '签到台账' } },
        { path: 'schedule', name: 'schedule', component: () => import('../views/ScheduleView.vue'), meta: { title: '定时任务' } },
        { path: 'config', name: 'config', component: () => import('../views/ConfigView.vue'), meta: { title: '域配置' } },
        { path: 'doctor', name: 'doctor', component: () => import('../views/DoctorView.vue'), meta: { title: '体检' } },
        { path: 'settings', name: 'settings', component: () => import('../views/SettingsView.vue'), meta: { title: '设置' } },
      ],
    },
  ],
})

export default router
