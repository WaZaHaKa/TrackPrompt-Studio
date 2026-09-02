import { describe, expect, it } from 'vitest'

import type { SpaceJourneyParameters } from './types'
import {
  cloneDefaultSpaceJourneyParameters,
  DEFAULT_SPACE_JOURNEY_PARAMETERS,
  validateSpaceJourneyParameters,
} from './visualizer'

describe('Space Journey visualizer parameters', () => {
  it('exposes the reviewed defaults as a fresh editable object', () => {
    const parameters = cloneDefaultSpaceJourneyParameters()

    expect(parameters).toEqual({
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
    parameters.cameraDistance = 24
    expect(DEFAULT_SPACE_JOURNEY_PARAMETERS.cameraDistance).toBe(18)
  })

  it('rejects non-finite, out-of-range, and unsupported values', () => {
    const parameters = {
      ...cloneDefaultSpaceJourneyParameters(),
      cameraDistance: 41,
      cameraOrbitSpeed: Number.NaN,
      ringThickness: -1,
      ringOcclusion: 1.1,
      palette: 'rainbow',
      glowStrength: 4.1,
      shardDensity: -0.1,
      fogDepth: 1.1,
      bassResponse: 2.1,
      drumResponse: -0.1,
      vocalResponse: Number.POSITIVE_INFINITY,
    } as unknown as SpaceJourneyParameters

    expect(validateSpaceJourneyParameters(parameters)).toEqual({
      cameraDistance: 'Use a value from 8 to 40.',
      cameraOrbitSpeed: 'Enter a finite number.',
      ringThickness: 'Use a value from 0.02 to 0.2.',
      ringOcclusion: 'Use a value from 0 to 1.',
      glowStrength: 'Use a value from 0 to 4.',
      shardDensity: 'Use a value from 0 to 1.',
      fogDepth: 'Use a value from 0 to 1.',
      bassResponse: 'Use a value from 0 to 2.',
      drumResponse: 'Use a value from 0 to 2.',
      vocalResponse: 'Enter a finite number.',
      palette: 'Choose a supported palette.',
    })
  })
})
