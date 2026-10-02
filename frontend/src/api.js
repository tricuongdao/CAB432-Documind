// Thin API client. The SPA is same-origin with the API in every environment
// (the Lambda serves both). When served behind the API Gateway route prefix
// (/documind/*), API calls keep the prefix so they resolve correctly.
const PREFIX = typeof window !== 'undefined' && window.location.pathname.startsWith('/documind')
  ? '/documind'
  : ''
export const API_PREFIX = PREFIX
const API_BASE = import.meta.env.VITE_API_BASE || PREFIX

export async function apiJson(path, opts = {}) {
  const o = { ...opts }
  if (o.body && !o.headers) o.headers = { 'content-type': 'application/json' }
  const res = await fetch(API_BASE + path, o)
  let data = null
  try {
    data = await res.json()
  } catch (e) {
    data = null
  }
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || `HTTP ${res.status}`
    const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    err.status = res.status
    throw err
  }
  return { data, headers: res.headers }
}

// Direct browser -> S3 upload against a presigned URL, with progress events.
export function uploadWithProgress(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url, true)
    xhr.setRequestHeader('Content-Type', 'application/pdf')
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else reject(new Error(`S3 responded ${xhr.status}`))
    }
    xhr.onerror = () => reject(new Error('network error during S3 upload'))
    xhr.send(file)
  })
}
