import { useEffect, useState, useCallback, useRef } from 'react'

// Optional API token (STUDIO_TOKEN): open the dashboard once with ?token=... and it is remembered.
function token() {
  try {
    const t = new URLSearchParams(location.search).get('token')
    if (t) localStorage.setItem('studio_token', t)
    return localStorage.getItem('studio_token') || ''
  } catch { return '' }
}

function errText(data, res) {
  const d = data && data.detail
  if (Array.isArray(d)) return d.map((x) => `${(x.loc || []).slice(1).join('.')}: ${x.msg}`).join('; ')
  return (typeof d === 'string' && d) || `${res.status} ${res.statusText}`
}

export async function api(path, opts = {}) {
  const headers = opts.body ? { 'Content-Type': 'application/json' } : {}
  const tok = token()
  if (tok) headers['X-Studio-Token'] = tok
  const res = await fetch('/api' + path, {
    method: opts.method || 'GET', headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  const text = await res.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = { detail: text.slice(0, 200) } }
  if (!res.ok) throw new Error(errText(data, res))
  return data
}

export const post = (p, body = {}) => api(p, { method: 'POST', body })
export const put = (p, body = {}) => api(p, { method: 'PUT', body })

// Poll an endpoint. Returns [data, reload, error]
export function usePoll(path, ms = 4000) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const current = useRef(path)
  const load = useCallback(() => {
    if (!path) return
    const p = path
    api(p).then((d) => { if (current.current === p) { setData(d); setErr(null) } })
      .catch((e) => { if (current.current === p) setErr(e.message) })
  }, [path])
  useEffect(() => {
    current.current = path
    setData(null) // never show the previous path's data
    load()
    if (!ms) return
    const t = setInterval(load, ms)
    return () => clearInterval(t)
  }, [load, ms, path])
  return [data, load, err]
}
