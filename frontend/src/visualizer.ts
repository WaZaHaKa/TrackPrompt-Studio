import type { SpaceJourneyParameters } from './types'
import { isSpaceJourneyPalette } from './types'

export const VISUALIZER_CONFIG_SCHEMA_VERSION = '1.0.0' as const
export const DEFAULT_VISUALIZER_SEED = 84291

export const DEFAULT_SPACE_JOURNEY_PARAMETERS: Readonly<SpaceJourneyParameters> = Object.freeze({
  cameraDistance: 18,
  cameraOrbitSpeed: 0.15,
  ringThickness: 0.06,
  ringOcclusion: 0.2,
  palette: 'andromeda',
  glowStrength: 1.8,
  shardDensity: 0.35,
  fogDepth: 0.5,
  bassResponse: 1.2,
  drumResponse: 0.9,
  vocalResponse: 0.65,
})

type NumericSpaceJourneyParameter = Exclude<keyof SpaceJourneyParameters, 'palette'>

export interface NumericParameterBounds {
  min: number
  max: number
  step: number
}

export const SPACE_JOURNEY_PARAMETER_BOUNDS: Readonly<
  Record<NumericSpaceJourneyParameter, NumericParameterBounds>
> = Object.freeze({
  cameraDistance: { min: 8, max: 40, step: 0.5 },
  cameraOrbitSpeed: { min: 0, max: 0.5, step: 0.01 },
  ringThickness: { min: 0.02, max: 0.2, step: 0.01 },
  ringOcclusion: { min: 0, max: 1, step: 0.05 },
  glowStrength: { min: 0, max: 4, step: 0.1 },
  shardDensity: { min: 0, max: 1, step: 0.05 },
  fogDepth: { min: 0, max: 1, step: 0.05 },
  bassResponse: { min: 0, max: 2, step: 0.05 },
  drumResponse: { min: 0, max: 2, step: 0.05 },
  vocalResponse: { min: 0, max: 2, step: 0.05 },
})

export type SpaceJourneyValidationErrors = Partial<Record<keyof SpaceJourneyParameters, string>>

export function cloneDefaultSpaceJourneyParameters(): SpaceJourneyParameters {
  return { ...DEFAULT_SPACE_JOURNEY_PARAMETERS }
}

export function validateSpaceJourneyParameters(
  parameters: SpaceJourneyParameters,
): SpaceJourneyValidationErrors {
  const errors: SpaceJourneyValidationErrors = {}
  const numericKeys = Object.keys(SPACE_JOURNEY_PARAMETER_BOUNDS) as NumericSpaceJourneyParameter[]
  for (const key of numericKeys) {
    const value = parameters[key]
    const bounds = SPACE_JOURNEY_PARAMETER_BOUNDS[key]
    if (!Number.isFinite(value)) {
      errors[key] = 'Enter a finite number.'
    } else if (value < bounds.min || value > bounds.max) {
      errors[key] = `Use a value from ${bounds.min} to ${bounds.max}.`
    }
  }
  if (!isSpaceJourneyPalette(parameters.palette)) {
    errors.palette = 'Choose a supported palette.'
  }
  return errors
}
