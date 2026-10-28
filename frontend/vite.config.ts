import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // 缓存放到项目内：某些受管环境（沙箱 / 只读 TEMP）会拒绝写系统临时目录，
  // vitest 启动时会因此偶发 EPERM，落到 node_modules 下就稳定了
  cacheDir: 'node_modules/.vite-cache',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    // 组件测试需要 DOM：用 jsdom（与生产浏览器行为最接近）
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.ts'],
    restoreMocks: true,
    // 组件里会用到 matchMedia / ResizeObserver 等浏览器 API，统一在 setup 里兜底
    clearMocks: true,
  },
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ['vue', 'vue-router', 'pinia'],
          echarts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
          icons: ['lucide-vue-next'],
        },
      },
    },
  },
})
