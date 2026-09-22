import { hayaleEdit } from './hayaleEdit'

export function HayalePlanSummary({ continuity, jobId }: { continuity: Record<string, unknown>; jobId: string }) {
  const edit = hayaleEdit(continuity)
  if (!edit) return null
  return <aside className="mc-card" aria-label="Hayale film contract">
    <h2>Hayale · 16 scenes × 4 seconds</h2>
    <p>{edit.editedSeconds} seconds edited · {edit.totalFrames.toLocaleString()} frames at 24 fps · {edit.profile}</p>
    <p>{edit.generatedSeconds} provider source seconds. Customer photos and both logo moments stay in local composition.</p>
    <p>Preview completion requires a separate decision before a final batch. Technical verification and creative approval are separate.</p>
    <details><summary>Hayale operator commands</summary>
      <p>Use the dedicated launcher for its supervised worker and explicit music arrangement. This page shows the same saved job.</p>
      <pre>{`.\\run-hayale-16x4.ps1 -Action Start -JobId ${jobId}\n.\\run-hayale-16x4.ps1 -Action Status -JobId ${jobId}\n.\\run-hayale-16x4.ps1 -Action Stop -JobId ${jobId}\n.\\run-hayale-16x4.ps1 -Action Export -JobId ${jobId}`}</pre>
      <p>Start requires the approved bounded batch. Stop requests local scheduling to stop; provider cancellation remains unconfirmed.</p>
    </details>
  </aside>
}
