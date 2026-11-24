import React, { useEffect, useState } from 'react'
import { api, post, put, usePoll } from './api.js'

const Badge = ({ s }) => <span className={'badge b-' + s}>{String(s || '').replaceAll('_', ' ')}</span>
const fmtS = (s) => (s == null ? '-' : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`)
const STAGES = ['research', 'script', 'voice', 'render', 'seo', 'thumbnails', 'final']

function useAction() {
  const [msg, setMsg] = useState('')
  const run = async (fn, ok = 'Done') => {
    setMsg('Working…')
    try { const r = await fn(); setMsg(ok); return r } catch (e) { setMsg('Error: ' + e.message) }
  }
  return [msg, run]
}

function VideoPicker({ videoId, setVideoId, format }) {
  const [vids] = usePoll('/videos' + (format ? '?format=' + format : ''), 8000)
  useEffect(() => {
    if (!vids || !vids.length) return
    const ok = vids.some((v) => v.id === videoId)
    if (videoId === undefined || videoId === null || (videoId && !ok)) setVideoId(vids[0].id)
  }, [vids, videoId])
  return (
    <div className="row" style={{ marginBottom: 12 }}>
      <select value={videoId || ''} onChange={(e) => setVideoId(Number(e.target.value) || 0)}>
        <option value="">Select a video…</option>
        {(vids || []).map((v) => <option key={v.id} value={v.id}>#{v.id} [{v.format}] {v.title} ({v.status})</option>)}
      </select>
    </div>
  )
}

function StageBar({ v }) {
  return (
    <div className="row" style={{ marginBottom: 10 }}>
      {STAGES.map((s) => <span key={s} className="muted">{s}: <Badge s={v.stages?.[s]?.status || '—'} /></span>)}
    </div>
  )
}

function StageControls({ v, stage, reload, extra }) {
  const [notes, setNotes] = useState('')
  const [msg, run] = useAction()
  const st = v.stages?.[stage]?.status
  const waiting = st === 'awaiting_approval'
  return (
    <div className="card">
      <div className="row">
        <b>{stage}</b> <Badge s={st || 'not started'} />
        <button className="btn ok" disabled={!waiting} onClick={() => run(() => post(`/videos/${v.id}/stages/${stage}/approve`)).then(reload)}>Approve</button>
        <button className="btn danger" disabled={!waiting} onClick={() => run(() => post(`/videos/${v.id}/stages/${stage}/reject`, { notes })).then(reload)}>Reject</button>
        <button className="btn" onClick={() => run(() => post(`/videos/${v.id}/stages/${stage}/regenerate`, { notes, payload: extra || {} }), 'Queued').then(reload)}>Regenerate</button>
        <span className="muted">{msg}</span>
      </div>
      <input style={{ width: '100%', marginTop: 8 }} placeholder="Notes for rejection / regeneration (e.g. stronger hook, focus on the search)" value={notes} onChange={(e) => setNotes(e.target.value)} />
      {v.stages?.[stage]?.error && <p className="err">{v.stages[stage].error}</p>}
    </div>
  )
}

function useVideo(videoId) {
  return usePoll(videoId ? `/videos/${videoId}` : null, 5000)
}

// ------------------------------------------------------------------ Dashboard
const alertErr = (e) => console.warn(e.message)

export function Dashboard({ go }) {
  const [d, reload] = usePoll('/dashboard', 4000)
  const [st] = usePoll('/status', 0)
  if (!d) return <p className="muted">Loading…</p>
  const warn = []
  if (st && !st.llm.configured) warn.push('No LLM key: scripts use the offline extractive template. Add ANTHROPIC_API_KEY for real documentary scripts.')
  if (st && !st.youtube_oauth.token) warn.push('YouTube not connected: uploads and analytics are disabled until you run the OAuth setup.')
  return (
    <>
      <h1>Dashboard</h1>
      {warn.map((w) => <div key={w} className="warn">{w}</div>)}
      <div className="grid">
        {Object.entries(d.counts).map(([k, n]) => <div key={k} className="stat"><b>{n}</b><small><Badge s={k} /></small></div>)}
      </div>
      <h2>Awaiting your approval</h2>
      <div className="card scroll">
        {d.awaiting.length ? <table><tbody>{d.awaiting.map((v) => (
          <tr key={v.id}><td>#{v.id}</td><td>{v.title}</td><td>{v.format}</td>
            <td>{Object.entries(v.stages).filter(([, s]) => s.status === 'awaiting_approval').map(([k]) => k).join(', ')}</td>
            <td><button className="btn" onClick={() => {
              const st = Object.entries(v.stages).find(([, s]) => s.status === 'awaiting_approval')?.[0]
              go({ research: 'script', script: 'script', voice: 'voice', thumbnails: 'thumbs', seo: 'thumbs' }[st] || 'editor', v.id)
            }}>Review</button></td></tr>
        ))}</tbody></table> : <p className="muted">Nothing waiting.</p>}
      </div>
      <h2>Productions</h2>
      <div className="card scroll"><table>
        <thead><tr><th>#</th><th>Title</th><th>Format</th><th>Status</th><th>Updated</th></tr></thead>
        <tbody>{d.videos.map((v) => (
          <tr key={v.id} onClick={() => go('editor', v.id)} style={{ cursor: 'pointer' }}>
            <td>{v.id}</td><td>{v.title}</td><td>{v.format}</td><td><Badge s={v.status} /></td><td className="muted">{v.updated_at?.slice(0, 16)}</td></tr>
        ))}</tbody></table></div>
      <h2>Recent jobs</h2>
      <div className="card scroll"><table><tbody>{d.jobs.map((j) => (
        <tr key={j.id}><td>{j.id}</td><td>{j.kind}</td><td>{j.video_id ? '#' + j.video_id : ''}</td><td><Badge s={j.status} /></td>
          <td className="muted">attempt {j.attempts}/{j.max_attempts}</td><td className="err">{j.error?.slice(0, 140)}</td>
          <td>{j.status === 'failed' && <button className="btn" onClick={() => post(`/jobs/${j.id}/retry`).then(reload).catch((e) => alertErr(e))}>Retry</button>}</td></tr>
      ))}</tbody></table></div>
    </>
  )
}

// ------------------------------------------------------------------ Research
export function Research({ go }) {
  const [topics, reload] = usePoll('/topics', 6000)
  const [cats] = usePoll('/categories', 0)
  const [sel, setSel] = useState([])
  const [per, setPer] = useState(2)
  const [deep, setDeep] = useState(true)
  const [q, setQ] = useState(''), [qc, setQc] = useState('Untold History')
  const [open, setOpen] = useState(null)
  const [msg, run] = useAction()
  return (
    <>
      <h1>Topic Research</h1>
      <div className="card">
        <div className="row">
          <select multiple value={sel} onChange={(e) => setSel([...e.target.selectedOptions].map((o) => o.value))} style={{ minWidth: 260, height: 120 }}>
            {(cats || []).map((c) => <option key={c}>{c}</option>)}
          </select>
          <div>
            <label className="muted">Per category <input type="number" min="1" max="5" value={per} onChange={(e) => setPer(+e.target.value)} style={{ width: 60 }} /></label><br />
            <label className="muted"><input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)} /> YouTube competition check (uses API quota)</label><br />
            <button className="btn primary" onClick={() => run(() => post('/topics/discover', { categories: sel.length ? sel : null, per_category: per, deep }), 'Research job queued; results appear below')}>Find & score topics</button>
          </div>
        </div>
        <p className="muted">Topics are kept only if a substantial Wikipedia article exists. Scores show what was measured vs. unavailable.</p>
        <div className="row"><input placeholder="Add a specific topic, e.g. Operation Mincemeat" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} />
          <select value={qc} onChange={(e) => setQc(e.target.value)}>{(cats || []).map((c) => <option key={c}>{c}</option>)}</select>
          <button className="btn" onClick={() => run(() => post('/topics', { query: q, category: qc })).then(reload)}>Score topic</button></div>
        <p className="muted">{msg}</p>
      </div>
      <div className="card scroll"><table>
        <thead><tr><th>Score</th><th>Topic</th><th>Category</th><th>Status</th><th></th></tr></thead>
        <tbody>{(topics || []).map((t) => (<React.Fragment key={t.id}>
          <tr><td><b>{t.total_score}</b><div className="muted">{t.scores?.confidence}</div></td>
            <td><a href="#" onClick={(e) => { e.preventDefault(); setOpen(open === t.id ? null : t.id) }}>{t.title}</a><div className="muted">{t.summary?.slice(0, 160)}…</div></td>
            <td>{t.category}</td><td><Badge s={t.status} /></td>
            <td className="row">
              {['new', 'approved'].includes(t.status) && <button className="btn primary" onClick={() => run(() => post(`/topics/${t.id}/produce`, { format: 'long' }), 'Production started').then((r) => r && go('dashboard'))}>Produce video</button>}
              <button className="btn ok" onClick={() => run(() => api(`/topics/${t.id}`, { method: 'PATCH', body: { status: 'approved' } }), 'Approved').then(reload)}>Approve</button>
              <button className="btn danger" onClick={() => run(() => api(`/topics/${t.id}`, { method: 'PATCH', body: { status: 'rejected' } }), 'Rejected').then(reload)}>Reject</button>
            </td></tr>
          {open === t.id && <tr><td colSpan="5"><table><tbody>
            {Object.entries(t.scores).filter(([k]) => k !== 'confidence').map(([k, s]) => <tr key={k}><td>{k}</td><td><b>{s.value}</b></td><td className="muted">{s.basis}</td></tr>)}
            <tr><td>sources</td><td colSpan="2">{t.sources.map((s) => <div key={s.url}><a href={s.url} target="_blank">{s.title}</a></div>)}</td></tr>
          </tbody></table></td></tr>}
        </React.Fragment>))}</tbody></table></div>
    </>
  )
}

// ------------------------------------------------------------------ Script
export function Script({ videoId, setVideoId }) {
  const [v, reload] = useVideo(videoId)
  const [draft, setDraft] = useState(null)
  const [msg, run] = useAction()
  useEffect(() => { setDraft(null) }, [videoId])
  const sc = draft || v?.script
  const edit = (si, ci, val) => {
    const d = structuredClone(sc); d.sections[si].scenes[ci].narration = val; setDraft(d)
  }
  return (
    <>
      <h1>Script Generator</h1>
      <VideoPicker videoId={videoId} setVideoId={setVideoId} />
      {v && <StageBar v={v} />}
      {v && v.stages?.research?.status === 'awaiting_approval' && <StageControls v={v} stage="research" reload={reload} />}
      {v && <StageControls v={v} stage="script" reload={reload} />}
      {sc && sc.sections ? (<div className="card">
        <div className="row"><b>{sc.title}</b><span className="muted">{sc.word_count} words · ~{fmtS(sc.duration_s)} · generator: {sc.generator}</span></div>
        {(sc.warnings || []).map((w) => <div key={w} className="warn">{w}</div>)}
        {draft && <div className="row" style={{ margin: '8px 0' }}><button className="btn primary" onClick={() => run(() => put(`/videos/${v.id}/script`, { script: draft, rerun: true }), 'Saved; narration and render will be rebuilt').then(() => { setDraft(null); reload() })}>Save edits & rebuild</button><button className="btn" onClick={() => setDraft(null)}>Discard</button><span className="muted">{msg}</span></div>}
        {sc.sections.map((sec, si) => (
          <div key={si}><h2>{sec.heading} <span className="badge">{sec.purpose}</span></h2>
            {sec.scenes.map((s, ci) => (
              <div className="scene" key={ci}>
                <div className="meta">{s.timecode} · visual: {s.visual} {s.on_screen_text && `· text: "${s.on_screen_text}"`} {s.sfx && `· sfx: ${s.sfx}`} · music: {s.music}</div>
                <textarea value={s.narration} onChange={(e) => edit(si, ci, e.target.value)} />
              </div>))}
          </div>))}
        <h2>Fact check ({sc.claims?.filter((c) => c.status !== 'supported').length || 0} need review)</h2>
        <div className="scroll"><table><tbody>{(sc.claims || []).map((c, i) => (
          <tr key={i}><td><Badge s={c.status} /></td><td>{c.claim}</td><td className="muted">{c.support || c.evidence}</td></tr>))}</tbody></table></div>
        {v.research && <><h2>Sources</h2><p><a href={v.research.url} target="_blank">{v.research.title} (Wikipedia)</a></p>
          {(v.research.strong_references || []).map((r, i) => <div className="muted" key={i}>{[r.author, r.title, r.publisher, r.date].filter(Boolean).join(', ')} {r.url && <a href={r.url} target="_blank">link</a>}</div>)}</>}
      </div>) : v && <p className="muted">No script yet.</p>}
    </>
  )
}

// ------------------------------------------------------------------ Voice
export function Voice({ videoId, setVideoId }) {
  const [st] = usePoll('/status', 0)
  const [text, setText] = useState('In the winter of 1959, nine experienced hikers walked into the Ural Mountains. None of them came back.')
  const [prov, setProv] = useState('')
  const [prev, setPrev] = useState(null)
  const [msg, run] = useAction()
  const [v, reload] = useVideo(videoId)
  return (
    <>
      <h1>Voice Generator</h1>
      <div className="card">
        <p className="muted">Provider chain: {st?.tts.chain.join(' → ')} · Edge voice: {st?.tts.edge_voice}</p>
        <textarea value={text} onChange={(e) => setText(e.target.value)} />
        <div className="row" style={{ marginTop: 8 }}>
          <select value={prov} onChange={(e) => setProv(e.target.value)}><option value="">default</option>{['edge', 'elevenlabs', 'openai', 'espeak'].map((p) => <option key={p}>{p}</option>)}</select>
          <button className="btn primary" onClick={() => run(() => post('/voice/preview', { text, provider: prov || null }).then(setPrev), 'Ready')}>Preview voice</button>
          <span className="muted">{msg}</span>
        </div>
        {prev && <><p className="muted">Used: {prev.provider} {prev.fallback_errors?.length ? '(fallbacks: ' + prev.fallback_errors.join('; ') + ')' : ''}</p><audio controls src={prev.url} /></>}
      </div>
      <VideoPicker videoId={videoId} setVideoId={setVideoId} />
      {v && <><StageControls v={v} stage="voice" reload={reload} extra={prov ? { provider: prov } : {}} />
        {v.audio?.url && <div className="card"><p className="muted">Narration {fmtS(v.audio.duration)} · {(v.audio.segments || []).length} segments · providers {(v.audio.providers || []).join(', ')}</p>
          {v.audio.fallback_errors?.length > 0 && <div className="warn">Fallback used: {v.audio.fallback_errors.join(' | ')}</div>}
          <audio controls src={v.audio.url} style={{ width: '100%' }} /></div>}</>}
    </>
  )
}

// ------------------------------------------------------------------ Editor
export function Editor({ videoId, setVideoId }) {
  const [v, reload] = useVideo(videoId)
  const [msg, run] = useAction()
  return (
    <>
      <h1>Video Editor</h1>
      <VideoPicker videoId={videoId} setVideoId={setVideoId} />
      {v && <>
        <StageBar v={v} />
        {v.error && <p className="err">{v.error}</p>}
        {v.render?.url && <div className="card"><video controls src={v.render.url + '?t=' + v.updated_at} />
          <p className="muted">{v.render.size} · {fmtS(v.render.duration)} · music: {v.render.music} ({v.render.license}) · <a href={v.render.subtitles_srt_url}>subtitles.srt</a></p></div>}
        <StageControls v={v} stage="render" reload={reload} extra={{ refresh_visuals: true }} />
        <div className="card"><div className="row"><b>Final review</b><Badge s={v.stages?.final?.status || 'not ready'} />
          <button className="btn ok" disabled={v.stages?.final?.status !== 'awaiting_approval' || !['awaiting_approval'].includes(v.status)} onClick={() => run(() => post(`/videos/${v.id}/stages/final/approve`)).then(reload)}>Approve for upload</button>
          <button className="btn danger" disabled={v.stages?.final?.status !== 'awaiting_approval'} onClick={() => run(() => post(`/videos/${v.id}/stages/final/reject`)).then(reload)}>Reject</button><span className="muted">{msg}</span></div></div>
        <h2>Visual assets & licenses</h2>
        <div className="card scroll"><table><thead><tr><th></th><th>Scene</th><th>Provider</th><th>License</th><th>Author / source</th></tr></thead>
          <tbody>{(v.assets || []).map((a) => <tr key={a.scene_id}><td><img src={a.url} style={{ width: 120, borderRadius: 6 }} /></td><td>{a.scene_id}</td><td>{a.provider}</td>
            <td><Badge s={a.license_verified ? 'supported' : 'needs_review'} /> {a.license}</td><td className="muted">{a.author} {a.source_url && <a href={a.source_url} target="_blank">source</a>}<div>{(a.tried || []).join('; ').slice(0, 200)}</div></td></tr>)}</tbody></table></div>
      </>}
    </>
  )
}

// ------------------------------------------------------------------ Thumbnails
export function Thumbnails({ videoId, setVideoId }) {
  const [v, reload] = useVideo(videoId)
  const [msg, run] = useAction()
  const th = v?.thumbnails
  return (
    <>
      <h1>Thumbnail Studio</h1>
      <VideoPicker videoId={videoId} setVideoId={setVideoId} />
      {v && <StageControls v={v} stage="seo" reload={reload} />}
      {v && <StageControls v={v} stage="thumbnails" reload={reload} />}
      {th?.concepts && <div className="card">
        <p className="muted">Scoring: {th.scoring}. AI pick: concept {th.recommended}. Click to choose manually. {msg}</p>
        <div className="thumbs">{th.concepts.map((c) => (
          <div key={c.index}><img className={th.selected === c.index ? 'sel' : ''} src={c.url + '?t=' + v.updated_at}
            onClick={() => run(() => put(`/videos/${v.id}/thumbnail`, { selected: c.index }), 'Selected').then(reload)} />
            <div className="muted">{c.concept} · score <b>{c.scores?.final}</b> (contrast {c.scores?.contrast}, focal {c.scores?.focal_detail}, brevity {c.scores?.text_brevity}{c.scores?.vision != null && `, vision ${c.scores.vision}`})</div>
            {c.scores?.vision_reason && <div className="muted">{c.scores.vision_reason}</div>}</div>))}</div>
      </div>}
      {v?.seo?.title_options && <div className="card"><h2>Title options</h2>
        {v.seo.title_options.map((t) => <div className="row" key={t}><input readOnly value={t} style={{ flex: 1 }} />
          <button className="btn" onClick={() => run(() => put(`/videos/${v.id}/seo`, { seo: { title: t } }), 'Title set').then(reload)}>{v.seo.title === t ? '✓ Selected' : 'Use'}</button></div>)}
        <h2>SEO</h2>
        <SeoEditor key={v.id} v={v} reload={reload} />
      </div>}
    </>
  )
}

function SeoEditor({ v, reload }) {
  const [desc, setDesc] = useState(v.seo.description || '')
  const [tags, setTags] = useState((v.seo.tags || []).join(', '))
  const [pin, setPin] = useState(v.seo.pinned_comment || '')
  const [msg, run] = useAction()
  return (<>
    <label className="muted">Description</label><textarea style={{ minHeight: 220 }} value={desc} onChange={(e) => setDesc(e.target.value)} />
    <label className="muted">Tags</label><textarea value={tags} onChange={(e) => setTags(e.target.value)} />
    <label className="muted">Pinned comment</label><input style={{ width: '100%' }} value={pin} onChange={(e) => setPin(e.target.value)} />
    <p className="muted">Hashtags: {(v.seo.hashtags || []).join(' ')} · Keywords: {(v.seo.keywords || []).join(', ')} · Thumbnail text: {v.seo.thumbnail_text}</p>
    <button className="btn primary" onClick={() => run(() => put(`/videos/${v.id}/seo`, { seo: { description: desc, tags: tags.split(',').map((t) => t.trim()).filter(Boolean), pinned_comment: pin } }), 'Saved').then(reload)}>Save SEO</button> <span className="muted">{msg}</span>
  </>)
}

// ------------------------------------------------------------------ Shorts
export function Shorts({ videoId, setVideoId, go }) {
  const [v, reload] = useVideo(videoId)
  const [n, setN] = useState(3)
  const [msg, run] = useAction()
  return (
    <>
      <h1>Shorts Generator</h1>
      <VideoPicker videoId={videoId} setVideoId={setVideoId} format="long" />
      {v && v.format === 'long' && <div className="card"><div className="row">
        <label className="muted">Shorts to create <input type="number" min="2" max="4" value={n} onChange={(e) => setN(+e.target.value)} style={{ width: 60 }} /></label>
        <button className="btn primary" disabled={!v.script?.sections} onClick={() => run(() => post(`/videos/${v.id}/stages/shorts/regenerate`, { payload: { count: n } }), 'Queued').then(reload)}>Find moments & create Shorts</button>
        <span className="muted">{msg}</span></div>
        <p className="muted">Each Short is re-written as its own story with a new hook and CTA, then narrated and rendered at 1080x1920.</p>
        {(v.stages?.shorts?.moments || []).map((m, i) => <div className="muted" key={i}>• {m.focus} — {m.why}</div>)}
      </div>}
      <div className="grid">{(v?.format === 'long' ? v.children || [] : []).map((c) => (
        <div className="card" key={c.id}><b>#{c.id}</b> <Badge s={c.status} /><div className="muted">{c.title}</div>
          {c.video_url && <video controls src={c.video_url} style={{ maxHeight: 420 }} />}
          <button className="btn" onClick={() => go('editor', c.id)}>Open</button></div>))}</div>
    </>
  )
}

// ------------------------------------------------------------------ Upload
export function Upload({ videoId, setVideoId }) {
  const [yt] = usePoll('/youtube/status', 0)
  const [vids, reload] = usePoll('/videos', 6000)
  const [pls, setPls] = useState([])
  const [pl, setPl] = useState('')
  const [msg, run] = useAction()
  const [conf, setConf] = useState({})
  useEffect(() => { if (yt?.connected) api('/youtube/playlists').then(setPls).catch(() => {}) }, [yt])
  const ready = (vids || []).filter((v) => ['approved', 'uploaded', 'published', 'awaiting_approval'].includes(v.status))
  return (
    <>
      <h1>Upload Manager</h1>
      <div className="card">{yt ? (yt.connected ? <p>Connected to <b>{yt.channel_title}</b> ({yt.subscribers} subscribers)</p>
        : <div className="warn">YouTube not connected: {yt.error}. See docs/API_INTEGRATIONS.md → YouTube OAuth.</div>) : 'Checking…'}
        <p className="muted">Uploads are always <b>private</b> or <b>unlisted</b>. Going public requires typing PUBLISH below. {msg}</p>
        {pls.length > 0 && <select value={pl} onChange={(e) => setPl(e.target.value)}><option value="">No playlist</option>{pls.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</select>}
      </div>
      <div className="card scroll"><table><thead><tr><th>#</th><th>Video</th><th>Status</th><th>YouTube</th><th>Actions</th></tr></thead>
        <tbody>{ready.map((v) => (
          <tr key={v.id}><td>{v.id}</td><td>{v.title}<div className="muted">{v.format}</div></td><td><Badge s={v.status} /></td>
            <td>{v.youtube?.url ? <a href={v.youtube.url} target="_blank">{v.youtube.privacy}</a> : '-'}<div className="muted">{v.youtube?.steps && JSON.stringify(v.youtube.steps)}</div></td>
            <td>{v.status === 'approved' && <div className="row">
              <button className="btn primary" disabled={!yt?.connected} onClick={() => run(() => post(`/videos/${v.id}/upload`, { privacy: 'private', playlist_id: pl || null }), 'Upload queued').then(reload)}>Upload private</button>
              <button className="btn" disabled={!yt?.connected} onClick={() => run(() => post(`/videos/${v.id}/upload`, { privacy: 'unlisted', playlist_id: pl || null }), 'Upload queued').then(reload)}>Upload unlisted</button></div>}
              {v.status === 'awaiting_approval' && <span className="muted">Approve final review in Video Editor first</span>}
              {v.status === 'uploaded' && <div className="row">
                <input placeholder="Type PUBLISH" value={conf[v.id]?.c || ''} onChange={(e) => setConf({ ...conf, [v.id]: { ...conf[v.id], c: e.target.value } })} style={{ width: 110 }} />
                <input type="datetime-local" min={new Date(Date.now() + 3600e3 - new Date().getTimezoneOffset() * 60e3).toISOString().slice(0, 16)} value={conf[v.id]?.at || ''} onChange={(e) => setConf({ ...conf, [v.id]: { ...conf[v.id], at: e.target.value } })} />
                <button className="btn danger" onClick={() => run(() => post(`/videos/${v.id}/publish`, { confirm: conf[v.id]?.c || '', publish_at: conf[v.id]?.at ? new Date(conf[v.id].at).toISOString() : null }), 'Publish queued').then(reload)}>{conf[v.id]?.at ? 'Schedule public' : 'Publish now'}</button></div>}
            </td></tr>))}</tbody></table></div>
    </>
  )
}

// ------------------------------------------------------------------ Analytics
export function Analytics() {
  const [r, reload] = usePoll('/analytics/report', 0)
  const [msg, run] = useAction()
  const [m, setM] = useState({ youtube_id: '' })
  return (
    <>
      <h1>Analytics</h1>
      <div className="card row"><button className="btn primary" onClick={() => run(() => post('/analytics/sync'), 'Sync queued (refresh in a minute)')}>Sync from YouTube Analytics</button>
        <button className="btn" onClick={reload}>Refresh report</button><span className="muted">{msg}</span></div>
      {r && !r.has_data && <div className="warn">{r.summary}</div>}
      {r?.has_data && <>
        <div className="grid">{Object.entries(r.totals).map(([k, n]) => <div className="stat" key={k}><b>{n == null ? 'n/a' : n.toLocaleString()}</b><small>{k.replaceAll('_', ' ')}</small></div>)}</div>
        <h2>Recommendations</h2><div className="card">{r.recommendations.map((x) => <p key={x}>• {x}</p>)}
          {r.ai_analysis && <pre>{r.ai_analysis}</pre>}</div>
        <h2>By category</h2><div className="card scroll"><table><thead><tr><th>Category</th><th>Videos</th><th>Avg views</th><th>Avg % viewed</th><th>Avg CTR</th></tr></thead>
          <tbody>{r.categories.map((c) => <tr key={c.category}><td>{c.category}</td><td>{c.videos}</td><td>{c.avg_views ?? 'n/a'}</td><td>{c.avg_view_pct ?? 'n/a'}</td><td>{c.avg_ctr ?? 'n/a'}</td></tr>)}</tbody></table></div>
        <h2>Videos</h2><div className="card scroll"><table><thead><tr><th>Video</th><th>Views</th><th>Watch h</th><th>AVD</th><th>CTR</th><th>Subs</th><th>Revenue</th><th>RPM</th><th>Traffic</th><th>Source</th></tr></thead>
          <tbody>{r.videos.map((x) => <tr key={x.id}><td>{x.title}</td><td>{x.views ?? 'n/a'}</td><td>{x.watch_minutes != null ? (x.watch_minutes / 60).toFixed(1) : 'n/a'}</td><td>{fmtS(x.avg_view_duration_s)}</td><td>{x.ctr ?? 'n/a'}</td><td>{x.subscribers_gained ?? 'n/a'}</td><td>{x.revenue_usd ?? 'n/a'}</td><td>{x.rpm_usd ?? 'n/a'}</td>
            <td className="muted">{Object.entries(x.traffic_sources || {}).map(([k, n]) => `${k}:${n}`).join(' ')}</td><td>{x.source}</td></tr>)}</tbody></table></div>
      </>}
      <h2>Manual entry (from YouTube Studio)</h2>
      <div className="card row">{['youtube_id', 'views', 'watch_minutes', 'avg_view_duration_s', 'avg_view_pct', 'ctr', 'subscribers_gained', 'revenue_usd'].map((k) =>
        <input key={k} placeholder={k} value={m[k] || ''} onChange={(e) => setM({ ...m, [k]: e.target.value })} style={{ width: 140 }} />)}
        <button className="btn" onClick={() => run(async () => {
          const body = {}
          for (const [k, v] of Object.entries(m)) {
            if (v === '' || v == null) continue
            if (k === 'youtube_id') { body[k] = v.trim(); continue }
            const n = Number(String(v).replace(/[,%$\s]/g, ''))
            if (!Number.isFinite(n) || n < 0) throw new Error(`${k}: "${v}" is not a number`)
            body[k] = ['views', 'subscribers_gained'].includes(k) ? Math.round(n) : n
          }
          if (!body.youtube_id) throw new Error('youtube_id is required')
          if (Object.keys(body).length < 2) throw new Error('enter at least one metric')
          return post('/analytics/manual', body)
        }, 'Saved').then(reload)}>Save</button></div>
    </>
  )
}

// ------------------------------------------------------------------ Revenue
export function Revenue() {
  const [sum, reload] = usePoll('/revenue', 0)
  const [aff, reloadAff] = usePoll('/affiliates', 0)
  const [sc, setSc] = useState(null)
  const [f, setF] = useState({ monthly_views: '', conservative: 3, typical: 6, optimistic: 10, target: 1000, other: 0 })
  const [e, setE] = useState({ month: new Date().toISOString().slice(0, 7), stream: 'ads', amount_usd: '', note: '' })
  const [a, setA] = useState({ name: '', url: '' })
  const [msg, run] = useAction()
  const calc = () => run(() => post('/revenue/scenarios', { monthly_views: f.monthly_views === '' ? null : +f.monthly_views, rpms: { conservative: +f.conservative, typical: +f.typical, optimistic: +f.optimistic }, target: +f.target, other_monthly: +f.other }).then(setSc), '')
  useEffect(() => { calc() }, [])
  return (
    <>
      <h1>Revenue Tracker</h1>
      <div className="warn">Scenarios are arithmetic on views × the RPM you enter. They are not forecasts or guarantees.</div>
      <div className="card"><h2>Scenarios</h2><div className="row">
        <label className="muted">Monthly views (blank = your actual last 28 days) <input value={f.monthly_views} onChange={(x) => setF({ ...f, monthly_views: x.target.value })} style={{ width: 110 }} /></label>
        {['conservative', 'typical', 'optimistic'].map((k) => <label className="muted" key={k}>{k} RPM $<input value={f[k]} onChange={(x) => setF({ ...f, [k]: x.target.value })} style={{ width: 60 }} /></label>)}
        <label className="muted">Target $<input value={f.target} onChange={(x) => setF({ ...f, target: x.target.value })} style={{ width: 80 }} /></label>
        <label className="muted">Other income/mo $<input value={f.other} onChange={(x) => setF({ ...f, other: x.target.value })} style={{ width: 80 }} /></label>
        <button className="btn primary" onClick={calc}>Calculate</button></div>
        {sc && <><p className="muted">Views basis: {sc.views_basis} (actual 28d: {sc.actual_28d_views.toLocaleString()})</p>
          <div className="scroll"><table><thead><tr><th>Scenario</th><th>RPM</th><th>Views</th><th>Ad revenue</th><th>Total</th><th>Views needed for ${sc.target_usd}</th></tr></thead>
            <tbody>{sc.scenarios.map((s) => <tr key={s.scenario}><td>{s.scenario}</td><td>${s.rpm_usd}</td><td>{s.monthly_views.toLocaleString()}</td><td>${s.ad_revenue_usd}</td><td>${s.total_with_other_usd}</td><td>{s.views_needed_for_target?.toLocaleString()}</td></tr>)}</tbody></table></div>
          {sc.notes.map((n) => <p className="muted" key={n}>{n}</p>)}</>}
      </div>
      <div className="card"><h2>Actual revenue</h2>
        <p className="muted">YouTube-reported (last 28d): {sum?.youtube_reported_28d_revenue ?? 'not available'}</p>
        <div className="scroll"><table><thead><tr><th>Month</th><th>Ads</th><th>Affiliate</th><th>Sponsorship</th><th>Digital</th><th>Other</th><th>Total</th></tr></thead>
          <tbody>{(sum?.months || []).map((m) => <tr key={m.month}><td>{m.month}</td><td>{m.ads}</td><td>{m.affiliate}</td><td>{m.sponsorship}</td><td>{m.digital_products}</td><td>{m.other}</td><td><b>{m.total}</b></td></tr>)}</tbody></table></div>
        <div className="row" style={{ marginTop: 10 }}><input type="month" value={e.month} onChange={(x) => setE({ ...e, month: x.target.value })} />
          <select value={e.stream} onChange={(x) => setE({ ...e, stream: x.target.value })}>{['ads', 'affiliate', 'sponsorship', 'digital_products', 'other'].map((s) => <option key={s}>{s}</option>)}</select>
          <input placeholder="Amount USD" value={e.amount_usd} onChange={(x) => setE({ ...e, amount_usd: x.target.value })} style={{ width: 110 }} />
          <input placeholder="Note" value={e.note} onChange={(x) => setE({ ...e, note: x.target.value })} />
          <button className="btn" onClick={() => run(() => {
            const n = Number(String(e.amount_usd).replace(/[,$\s]/g, ''))
            if (!e.amount_usd || !Number.isFinite(n)) throw new Error('enter an amount')
            return post('/revenue/entries', { ...e, amount_usd: n })
          }, 'Added').then(reload)}>Add</button><span className="muted">{msg}</span></div>
      </div>
      <div className="card"><h2>Affiliate links (need your approval)</h2>
        <p className="muted">Approved links are only added to a description when you regenerate SEO with affiliates enabled; a disclosure line is always added.</p>
        <div className="scroll"><table><tbody>{(aff || []).map((x) => <tr key={x.id}><td>{x.name}</td><td className="muted">{x.url}</td><td><Badge s={x.approved ? 'approved' : 'awaiting_approval'} /></td>
          <td className="row"><button className="btn ok" onClick={() => run(() => post(`/affiliates/${x.id}/approve`)).then(reloadAff)}>Approve</button><button className="btn" onClick={() => run(() => post(`/affiliates/${x.id}/revoke`)).then(reloadAff)}>Revoke</button><button className="btn danger" onClick={() => run(() => post(`/affiliates/${x.id}/delete`)).then(reloadAff)}>Delete</button></td></tr>)}</tbody></table></div>
        <div className="row"><input placeholder="Name" value={a.name} onChange={(x) => setA({ ...a, name: x.target.value })} /><input placeholder="URL" value={a.url} onChange={(x) => setA({ ...a, url: x.target.value })} style={{ flex: 1 }} />
          <button className="btn" onClick={() => run(() => {
            if (!a.name.trim() || !/^https?:\/\//.test(a.url)) throw new Error('name and a full https:// URL are required')
            return post('/affiliates', a)
          }, 'Added (needs approval)').then(reloadAff)}>Add</button></div>
      </div>
    </>
  )
}

// ------------------------------------------------------------------ Settings
export function Settings() {
  const [s, reload] = usePoll('/settings', 0)
  const [p, setP] = useState(null), [sch, setSch] = useState(null)
  const [msg, run] = useAction()
  useEffect(() => { if (s) { setP(s.pipeline); setSch(JSON.stringify(s.schedule, null, 2)) } }, [s])
  if (!s || !p) return <p className="muted">Loading…</p>
  const st = s.status
  const gate = (g) => setP({ ...p, gates: p.gates.includes(g) ? p.gates.filter((x) => x !== g) : [...p.gates, g] })
  return (
    <>
      <h1>Settings</h1>
      <div className="card"><h2>Integrations</h2><table><tbody>
        <tr><td>LLM</td><td>{st.llm.configured ? `${st.llm.provider} (${st.llm.model})` : 'not configured: offline template'}</td></tr>
        <tr><td>TTS</td><td>chain {st.tts.chain.join(' → ')} · ElevenLabs {st.tts.elevenlabs ? '✓' : '✗'} · OpenAI {st.tts.openai ? '✓' : '✗'}</td></tr>
        <tr><td>Images</td><td>{st.images.order} · Pexels {st.images.pexels ? '✓' : '✗'} · OpenAI images {st.images.openai ? '✓' : '✗'}</td></tr>
        <tr><td>Research</td><td>YouTube Data API key {st.research.youtube_data_api_key ? '✓' : '✗'} · autocomplete {st.research.autocomplete ? 'on' : 'off'}</td></tr>
        <tr><td>YouTube OAuth</td><td>client secrets {st.youtube_oauth.client_secrets ? '✓' : '✗'} · token {st.youtube_oauth.token ? '✓' : '✗'}</td></tr>
        <tr><td>Music</td><td>{st.music_tracks} licensed tracks in data/music (else procedural ambient)</td></tr>
      </tbody></table><p className="muted">API keys live in the .env file on the server, never in the browser. See docs/API_INTEGRATIONS.md.</p></div>
      <div className="card"><h2>Pipeline</h2>
        <p className="muted">Approval gates (pipeline pauses after these stages):</p>
        <div className="row">{['research', 'script', 'voice', 'render', 'seo', 'thumbnails'].map((g) => <label key={g}><input type="checkbox" checked={p.gates.includes(g)} onChange={() => gate(g)} /> {g}</label>)}</div>
        <div className="row" style={{ marginTop: 10 }}>
          <label><input type="checkbox" checked={p.auto_shorts} onChange={(e) => setP({ ...p, auto_shorts: e.target.checked })} /> Auto-create Shorts</label>
          <label className="muted">Shorts per video <input type="number" min="2" max="4" value={p.shorts_per_video} onChange={(e) => setP({ ...p, shorts_per_video: +e.target.value })} style={{ width: 55 }} /></label>
          <label className="muted">Default minutes <input type="number" min="8" max="15" value={p.default_minutes} onChange={(e) => setP({ ...p, default_minutes: +e.target.value })} style={{ width: 55 }} /></label>
          <label><input type="checkbox" checked={p.fast_render} onChange={(e) => setP({ ...p, fast_render: e.target.checked })} /> Fast render (drafts)</label>
          <label className="muted">TTS <select value={p.tts_provider || ''} onChange={(e) => setP({ ...p, tts_provider: e.target.value || null })}><option value="">from .env</option>{['edge', 'elevenlabs', 'openai', 'espeak'].map((x) => <option key={x}>{x}</option>)}</select></label>
        </div>
        <button className="btn primary" style={{ marginTop: 10 }} onClick={() => run(() => put('/settings/pipeline', p), 'Saved').then(reload)}>Save pipeline</button> <span className="muted">{msg}</span>
      </div>
      <div className="card"><h2>Production calendar</h2>
        <p className="muted">When enabled, research runs daily and long videos are produced on production days up to the weekly quota. Everything stops at “awaiting approval”.</p>
        <textarea style={{ minHeight: 280, fontFamily: 'monospace' }} value={sch} onChange={(e) => setSch(e.target.value)} />
        <button className="btn primary" onClick={() => run(() => put('/settings/schedule', JSON.parse(sch)), 'Saved').then(reload)}>Save schedule</button>
      </div>
    </>
  )
}
