import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { MissionControlApp } from './mission-control'
import type { MissionSection } from './mission-control'

const AnalysisApp = lazy(async () => {
  await import('./styles.css')
  return import('./App')
})

const root = document.getElementById('root')
if (!root) throw new Error('TrackPrompt Studio could not find its application root.')

const workspace = new URLSearchParams(window.location.search).get('workspace')
const showAnalysisWorkspace = workspace === 'analysis'
const requestedSection = new URLSearchParams(window.location.search).get('section')
const missionSections: MissionSection[] = ['home', 'render', 'profiles', 'calibration', 'jobs', 'director', 'encode', 'video', 'cloud', 'settings']
const initialMissionSection = missionSections.includes(requestedSection as MissionSection)
  ? requestedSection as MissionSection
  : 'home'
document.title = showAnalysisWorkspace ? 'TrackPrompt Studio' : 'WZHK Media Mission Control'
document.documentElement.dataset.workspace = showAnalysisWorkspace ? 'analysis' : 'mission-control'

createRoot(root).render(
  <StrictMode>
    {showAnalysisWorkspace ? (
      <Suspense fallback={<main aria-busy="true">Loading TrackPrompt Studio…</main>}>
        <AnalysisApp />
      </Suspense>
    ) : <MissionControlApp initialSection={initialMissionSection} />}
  </StrictMode>,
)
