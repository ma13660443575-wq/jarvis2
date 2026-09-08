const ICONS = {
  Chat: '✉',
  Command: '⚡',
  Knowledge: '▤',
  Skills: '⬡',
  Review: '↻',
  Retro: '⌁',
  Settings: '⚙',
}

export default function Placeholder({ name, desc }) {
  return (
    <div>
      <h2 style={{ margin: '0 0 4px' }}>{name}</h2>
      <p className="muted small" style={{ margin: '0 0 20px' }}>{desc}</p>
      <div className="card">
        <div className="empty">
          <div className="big">{ICONS[name] ?? '◌'}</div>
          模块建设中
          <p className="muted small" style={{ marginTop: 6 }}>
            该功能将在后续阶段接入，当前为框架占位。
          </p>
        </div>
      </div>
    </div>
  )
}
