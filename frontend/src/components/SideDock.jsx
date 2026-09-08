import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/', icon: '◈', tip: '指挥台 Dashboard', exact: true },
  { to: '/chat', icon: '✉', tip: '对话 Chat' },
  { to: '/command', icon: '⚡', tip: '命令台 Command' },
  { to: '/knowledge', icon: '▤', tip: '知识库 Knowledge' },
  { to: '/skills', icon: '⬡', tip: '技能库 Skills' },
  { to: '/review', icon: '↻', tip: '复习 Review' },
  { to: '/retro', icon: '⌁', tip: '复盘 Retro' },
]

export default function SideDock() {
  return (
    <nav className="dock">
      <div className="dock-logo" title="JARVIS2">J</div>
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.exact}
          className={({ isActive }) => 'dock-btn' + (isActive ? ' active' : '')}
        >
          <span aria-hidden>{item.icon}</span>
          <span className="tip">{item.tip}</span>
        </NavLink>
      ))}
      <div className="dock-spacer" />
      <NavLink
        to="/settings"
        className={({ isActive }) => 'dock-btn' + (isActive ? ' active' : '')}
      >
        <span aria-hidden>⚙</span>
        <span className="tip">设置 Settings</span>
      </NavLink>
    </nav>
  )
}
