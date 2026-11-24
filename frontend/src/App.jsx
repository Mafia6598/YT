import React, { useState } from 'react'

class ErrorBoundary extends React.Component {
  constructor(p) { super(p); this.state = { err: null } }
  static getDerivedStateFromError(err) { return { err } }
  componentDidUpdate(prev) { if (prev.resetKey !== this.props.resetKey && this.state.err) this.setState({ err: null }) }
  render() {
    if (this.state.err) return <div className="warn">This page hit an error: {String(this.state.err.message || this.state.err)}. Pick another video or page.</div>
    return this.props.children
  }
}
import * as P from './pages.jsx'

const SECTIONS = [
  ['dashboard', 'Dashboard', P.Dashboard], ['research', 'Topic Research', P.Research],
  ['script', 'Script Generator', P.Script], ['voice', 'Voice Generator', P.Voice],
  ['editor', 'Video Editor', P.Editor], ['thumbs', 'Thumbnail Studio', P.Thumbnails],
  ['shorts', 'Shorts Generator', P.Shorts], ['upload', 'Upload Manager', P.Upload],
  ['analytics', 'Analytics', P.Analytics], ['revenue', 'Revenue Tracker', P.Revenue],
  ['settings', 'Settings', P.Settings],
]

export default function App() {
  const [tab, setTab] = useState(() => location.hash.slice(1) || 'dashboard')
  const [videoId, setVideoId] = useState(null)
  const [open, setOpen] = useState(false)
  const go = (t, vid) => { setTab(t); if (vid !== undefined) setVideoId(vid); location.hash = t; setOpen(false); window.scrollTo(0, 0) }
  const Page = (SECTIONS.find((s) => s[0] === tab) || SECTIONS[0])[2]
  return (
    <div className="layout">
      <aside className={'side' + (open ? ' open' : '')}>
        <div className="brand">HISTORY<span>STUDIO</span></div>
        <nav className="nav">
          {SECTIONS.map(([id, label]) => (
            <button key={id} className={tab === id ? 'on' : ''} onClick={() => go(id)}>{label}</button>
          ))}
        </nav>
      </aside>
      <main className="main">
        <button className="btn menu-toggle" onClick={() => setOpen(!open)}>☰ Menu</button>
        <ErrorBoundary resetKey={tab + ':' + videoId}>
          <Page go={go} videoId={videoId} setVideoId={setVideoId} />
        </ErrorBoundary>
      </main>
    </div>
  )
}
