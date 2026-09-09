import { describe, expect, it } from 'vitest'
import { appendHistoryPoint, MAX_HISTORY_POINTS } from './impactHistory.js'

describe('appendHistoryPoint', () => {
  it('appends to the end, oldest first', () => {
    const history = appendHistoryPoint([1, 2, 3], 4)
    expect(history).toEqual([1, 2, 3, 4])
  })

  it('caps the length so a long demo session cannot grow it unbounded', () => {
    let history = []
    for (let i = 0; i < MAX_HISTORY_POINTS + 10; i++) {
      history = appendHistoryPoint(history, i)
    }
    expect(history.length).toBe(MAX_HISTORY_POINTS)
    // oldest points must be dropped, newest must survive
    expect(history[history.length - 1]).toBe(MAX_HISTORY_POINTS + 9)
  })

  it('respects a custom maxLen', () => {
    let history = []
    for (let i = 0; i < 5; i++) {
      history = appendHistoryPoint(history, i, 3)
    }
    expect(history).toEqual([2, 3, 4])
  })

  it('does not mutate the input array', () => {
    const original = [1, 2]
    const updated = appendHistoryPoint(original, 3)
    expect(original).toEqual([1, 2])
    expect(updated).toEqual([1, 2, 3])
  })
})
