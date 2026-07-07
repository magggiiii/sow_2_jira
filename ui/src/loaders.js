// ui/src/loaders.js — motion loaders ported from beUI (https://beui.dev/components/motion/loader)
//
// beUI ships these as React + motion/react (Framer Motion) + Tailwind. This app is
// vanilla-JS + Vite, so we drive them with `motion` — the SAME framework-agnostic
// animation engine beUI is built on — via its vanilla `animate()` API:
//   • helix / newton  → motion `animate()` (real eased/spring interpolation → smooth)
//   • scramble        → a timer that cycles glyphs (discrete text; beUI's scramble is too)
//
// Every variant respects `prefers-reduced-motion` (calm opacity pulse via CSS).
// The factory returns a uniform handle: { el, start(), stop(), destroy() }.
import { animate } from 'motion'

export const LOADER_TYPES = ['scramble', 'helix', 'newton']

const SCRAMBLE_GLYPHS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&@*+=<>/\\'.split('')
const HELIX_ROWS = 7
const NEWTON_BALLS = 5

// jsdom (the vitest environment) does not implement matchMedia, so guard for it.
function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

function el(tag, className, attrs) {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (attrs) {
    for (const key of Object.keys(attrs)) node.setAttribute(key, attrs[key])
  }
  return node
}

function randomGlyph() {
  return SCRAMBLE_GLYPHS[Math.floor(Math.random() * SCRAMBLE_GLYPHS.length)]
}

// --- scramble: reveal `label` left-to-right, unrevealed slots flicker glyphs ---
function buildScramble(opts) {
  const label = String(opts.label || 'LOADING').toUpperCase()
  const root = el('div', 'loader loader-scramble', { role: 'status', 'aria-label': opts.label || 'Loading' })
  const text = el('span', 'loader-scramble-text', { 'aria-hidden': 'true' })
  root.appendChild(text)

  const reduced = prefersReducedMotion()
  let revealed = 0
  let timer = null

  function render() {
    if (reduced) {
      text.textContent = label
      return
    }
    let out = ''
    for (let i = 0; i < label.length; i++) {
      if (label[i] === ' ') out += ' '
      else out += i < revealed ? label[i] : randomGlyph()
    }
    text.textContent = out
    // reveal one more slot per tick, then hold the finished word briefly and loop
    revealed = revealed > label.length + 6 ? 0 : revealed + 1
  }

  return {
    el: root,
    start() {
      if (timer) return
      render()
      if (reduced) return
      timer = setInterval(render, 55)
    },
    stop() {
      if (timer) {
        clearInterval(timer)
        timer = null
      }
    },
    destroy() {
      this.stop()
      root.remove()
    },
  }
}

// --- helix: paired dots oscillating out of phase per row → travelling spiral wave ---
function buildHelix(opts) {
  const root = el('div', 'loader loader-helix', { role: 'status', 'aria-label': opts.label || 'Loading' })
  const dots = []
  for (let r = 0; r < HELIX_ROWS; r++) {
    const row = el('div', 'loader-helix-row')
    const a = el('span', 'loader-helix-dot')
    const b = el('span', 'loader-helix-dot loader-helix-dot--b')
    row.appendChild(a)
    row.appendChild(b)
    root.appendChild(row)
    // `a` and `b` swing in opposition; each row is phase-shifted to make the wave travel.
    dots.push({ node: a, dir: -1, row: r })
    dots.push({ node: b, dir: 1, row: r })
  }

  const reduced = prefersReducedMotion()
  const controls = []

  return {
    el: root,
    start() {
      if (reduced || controls.length) return
      for (const { node, dir, row } of dots) {
        controls.push(
          animate(
            node,
            { x: [dir * -6, dir * 6], opacity: dir < 0 ? [0.35, 1] : [1, 0.35] },
            {
              duration: 1.3,
              ease: 'easeInOut',
              repeat: Infinity,
              repeatType: 'mirror',
              delay: -0.09 * row, // negative delay = phase offset → travelling wave
            }
          )
        )
      }
    },
    stop() {
      for (const c of controls) c.stop()
      controls.length = 0
    },
    destroy() {
      this.stop()
      root.remove()
    },
  }
}

// --- newton: 5-ball cradle; the two end balls swing (pendulum ease), middle three rest ---
function buildNewton(opts) {
  const root = el('div', 'loader loader-newton', { role: 'status', 'aria-label': opts.label || 'Loading' })
  const balls = []
  for (let b = 0; b < NEWTON_BALLS; b++) {
    const ball = el('span', 'loader-newton-ball')
    root.appendChild(ball)
    balls.push(ball)
  }

  const reduced = prefersReducedMotion()
  const controls = []
  const DURATION = 1.5

  return {
    el: root,
    start() {
      if (reduced || controls.length) return
      const first = balls[0]
      const last = balls[balls.length - 1]
      // Left swings out+back over the first half of the cycle, then rests; right mirrors it
      // over the second half. Pendulum feel: decelerate to the apex, accelerate on the way down.
      controls.push(
        animate(
          first,
          { rotate: [0, -40, 0, 0] },
          { duration: DURATION, times: [0, 0.25, 0.5, 1], ease: ['easeOut', 'easeIn', 'linear'], repeat: Infinity }
        )
      )
      controls.push(
        animate(
          last,
          { rotate: [0, 0, 40, 0] },
          { duration: DURATION, times: [0, 0.5, 0.75, 1], ease: ['linear', 'easeOut', 'easeIn'], repeat: Infinity }
        )
      )
    },
    stop() {
      for (const c of controls) c.stop()
      controls.length = 0
    },
    destroy() {
      this.stop()
      root.remove()
    },
  }
}

/**
 * Create a loader. Returns { el, start(), stop(), destroy() }.
 * `start`/`stop` drive (helix, newton) or gate (scramble) the animation; all are
 * safe to call repeatedly. In reduced-motion mode the motion loaders stay static
 * and CSS applies a calm opacity pulse instead.
 * @param {'scramble'|'helix'|'newton'} type
 * @param {{ label?: string }} [opts]
 */
export function createLoader(type = 'scramble', opts = {}) {
  switch (type) {
    case 'scramble':
      return buildScramble(opts)
    case 'helix':
      return buildHelix(opts)
    case 'newton':
      return buildNewton(opts)
    default:
      throw new Error(`Unknown loader type: ${type}`)
  }
}

/**
 * Convenience: create a loader, mount it into `container`, start it, return the handle.
 */
export function mountLoader(container, type = 'scramble', opts = {}) {
  const loader = createLoader(type, opts)
  if (container) container.appendChild(loader.el)
  loader.start()
  return loader
}
