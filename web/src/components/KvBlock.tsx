import { stringifyCell } from '../utils/display'

export default function KvBlock({ title, data }: { title: string; data: Record<string, unknown> }) {
  return (
    <>
      <h3 className="section-heading">{title}</h3>
      <table className="kv-table">
        <tbody>
          {Object.entries(data).map(([k, v]) => (
            <tr key={k}>
              <th scope="row" className="mono">{k}</th>
              <td>
                <pre className="kv-cell">{stringifyCell(v)}</pre>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}
