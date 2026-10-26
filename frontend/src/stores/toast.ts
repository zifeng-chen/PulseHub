/** 轻量 Toast（03 §5：成功/失败反馈使用统一组件）。 */

import { defineStore } from 'pinia'

export type ToastKind = 'success' | 'error' | 'info' | 'warning'

export interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

let seq = 1

export const useToastStore = defineStore('toast', {
  state: () => ({ items: [] as ToastItem[] }),
  actions: {
    push(kind: ToastKind, message: string, timeout = 3200) {
      const id = seq++
      this.items.push({ id, kind, message })
      if (timeout > 0) {
        setTimeout(() => this.dismiss(id), timeout)
      }
      return id
    },
    success(message: string) {
      return this.push('success', message)
    },
    error(message: string) {
      return this.push('error', message, 4800)
    },
    info(message: string) {
      return this.push('info', message)
    },
    warning(message: string) {
      return this.push('warning', message, 4000)
    },
    dismiss(id: number) {
      this.items = this.items.filter((i) => i.id !== id)
    },
  },
})
