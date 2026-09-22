type HayaleEdit = { profile: string; editedSeconds: number; generatedSeconds: number; totalFrames: number }

export function hayaleEdit(continuity: Record<string, unknown> | undefined): HayaleEdit | null {
  const value = continuity?.hayaleEdit
  if (!value || typeof value !== 'object') return null
  const edit = value as Record<string, unknown>
  if (typeof edit.profile !== 'string' || typeof edit.editedSeconds !== 'number'
    || typeof edit.generatedSeconds !== 'number' || typeof edit.totalFrames !== 'number') return null
  return { profile: edit.profile, editedSeconds: edit.editedSeconds, generatedSeconds: edit.generatedSeconds, totalFrames: edit.totalFrames }
}
