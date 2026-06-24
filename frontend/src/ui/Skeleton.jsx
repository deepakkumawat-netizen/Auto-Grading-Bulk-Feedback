/** Skeleton block — single line, table row, or grid block. */
export default function Skeleton({ w = '100%', h = 16, radius = 8, count = 1, style }) {
  if (count > 1) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {Array.from({ length: count }, (_, i) => (
          <Skeleton key={i} w={w} h={h} radius={radius} style={style} />
        ))}
      </div>
    )
  }
  return <div className="skel" style={{ width: w, height: h, borderRadius: radius, ...style }} />
}
