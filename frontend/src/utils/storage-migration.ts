/**
 * 旧版本地存储迁移：`fam.*` → `pulsehub.*`。
 *
 * 项目改名为 PulseHub 后，存储键前缀同步更换。若不做迁移，用户会被踢下线、
 * 主题/锁屏/AI 偏好全部回到默认值——这些键没有服务端副本，丢了就找不回来。
 *
 * 只在启动时执行一次：逐个把 `fam.` 开头的键搬到 `pulsehub.`（新键已存在则不覆盖），
 * 然后删掉旧键。隐私模式下 localStorage 可能抛异常，直接忽略。
 */
const LEGACY_PREFIX = 'fam.'
const NEW_PREFIX = 'pulsehub.'

export function migrateLegacyStorage(): number {
  let migrated = 0
  try {
    const legacyKeys: string[] = []
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i)
      if (key && key.startsWith(LEGACY_PREFIX)) legacyKeys.push(key)
    }
    for (const key of legacyKeys) {
      const nextKey = NEW_PREFIX + key.slice(LEGACY_PREFIX.length)
      const value = localStorage.getItem(key)
      if (value !== null && localStorage.getItem(nextKey) === null) {
        localStorage.setItem(nextKey, value)
        migrated += 1
      }
      localStorage.removeItem(key)
    }
  } catch {
    /* 隐私模式 / 存储被禁用：不影响启动 */
  }
  return migrated
}
