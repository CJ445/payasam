import { describe, expect, it } from 'vitest'
import { appendEvent, buildEvent, MAX_EVENTS } from './activityLog.js'

describe('buildEvent', () => {
  it('captures the message, kind, and injected time', () => {
    const fixedTime = new Date('2026-01-01T00:00:00.000Z')
    const event = buildEvent('Fault injected', 'warning', () => fixedTime)
    expect(event.message).toBe('Fault injected')
    expect(event.kind).toBe('warning')
    expect(event.time).toBe(fixedTime)
  })

  it('defaults to kind "info" when not specified', () => {
    const event = buildEvent('System healthy', undefined, () => new Date())
    expect(event.kind).toBe('info')
  })

  it('generates distinct ids for two events built at the same instant', () => {
    const sameTime = () => new Date('2026-01-01T00:00:00.000Z')
    const a = buildEvent('A', 'info', sameTime)
    const b = buildEvent('B', 'info', sameTime)
    expect(a.id).not.toBe(b.id)
  })
})

describe('appendEvent', () => {
  it('prepends the new event so the feed reads newest-first', () => {
    const existing = [buildEvent('older', 'info', () => new Date())]
    const updated = appendEvent(existing, buildEvent('newer', 'info', () => new Date()))
    expect(updated[0].message).toBe('newer')
    expect(updated[1].message).toBe('older')
  })

  it('caps the list length so a long demo session cannot grow it unbounded', () => {
    let events = []
    for (let i = 0; i < MAX_EVENTS + 10; i++) {
      events = appendEvent(events, buildEvent(`event-${i}`, 'info', () => new Date()))
    }
    expect(events.length).toBe(MAX_EVENTS)
    // newest (last pushed) must survive the cap; oldest must be dropped
    expect(events[0].message).toBe(`event-${MAX_EVENTS + 9}`)
  })

  it('respects a custom maxLen', () => {
    let events = []
    for (let i = 0; i < 5; i++) {
      events = appendEvent(events, buildEvent(`event-${i}`, 'info', () => new Date()), 3)
    }
    expect(events.length).toBe(3)
  })
})
