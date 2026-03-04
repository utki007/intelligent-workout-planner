import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const API = '/api'
const DEV_MODE = import.meta.env.DEV
const ATHLETE_STORAGE_KEY = 'workout_athlete_profiles'
const ATHLETE_ACTIVE_KEY = 'workout_active_athlete_id'

const DEFAULT_JOINT_MUSCLE_MAP = {
  shoulder: ['delts', 'traps', 'upper back', 'levator scapulae'],
  elbow: ['biceps', 'triceps', 'forearms'],
  wrist: ['forearms'],
  hip: ['glutes', 'hamstrings', 'adductors', 'abductors'],
  knee: ['quads', 'hamstrings', 'calves'],
  ankle: ['calves'],
  spine: ['upper back', 'lats', 'abs'],
}

const DEFAULT_PROFILE = {
  injury_muscles: [],
  intensity: 3,
  onboarding_complete: false,
  fitness_goals: '',
  days_per_week: 5,
  soreness_per_muscle: {},
  weekly_volume_per_muscle: {},
}

const ONBOARDING_STEPS = ['Goals', 'Days / Week', 'Injuries', 'Intensity']

function dayNumber(label) {
  const m = String(label || '').match(/\d+/)
  return m ? Number(m[0]) : 0
}

function formatDayLabel(label) {
  return String(label || '').replace(/^Day(\d+)$/i, 'Day $1')
}

function sortedDayLabelsFromPlan(plan) {
  return Object.keys(plan || {}).sort((a, b) => dayNumber(a) - dayNumber(b))
}

function clip(text, n = 120) {
  if (!text) return ''
  return text.length > n ? `${text.slice(0, n)}...` : text
}

function toTitleCase(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/\b[a-z]/g, (char) => char.toUpperCase())
}

function extractQuotedValues(text) {
  const values = []
  const re = /"([^"]+)"|'([^']+)'/g
  let m = re.exec(text)
  while (m) {
    const v = (m[1] || m[2] || '').trim()
    if (v) values.push(v)
    m = re.exec(text)
  }
  return values
}

function parseSymbolicSummary(reasoningText) {
  const raw = String(reasoningText || '')
  const lines = raw.split('\n').map((x) => x.trim()).filter(Boolean)
  const themeLine = lines.find((line) => /^Inference\s*\(/i.test(line)) || null
  const resultLine = lines.find((line) => /^Result\s*:/i.test(line)) || null
  const recommendedLines = lines.filter((line) => /recommended\(/i.test(line))
  const quoted = recommendedLines.flatMap((line) => extractQuotedValues(line))
  const cleaned = Array.from(new Set(quoted.filter((x) => x.toLowerCase() !== 'athlete')))
  return {
    themeLine,
    resultLine,
    recommendations: cleaned,
  }
}

function sorenessClass(value) {
  if (value >= 4) return 'heat-high'
  if (value >= 3) return 'heat-mid'
  if (value >= 2) return 'heat-low'
  return 'heat-none'
}

function makeAthleteId(name) {
  const base = String(name || 'athlete')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 24) || 'athlete'
  const suffix = Math.random().toString(36).slice(2, 6)
  return `athlete_${base}_${suffix}`
}

function summarizeProfileUpdates(updates) {
  const parts = []
  if (updates?.injury_update) {
    const status = String(updates.injury_update.status || '').trim()
    const muscles = Array.isArray(updates.injury_update.muscles) ? updates.injury_update.muscles : []
    if (status) {
      parts.push(`Injury status: ${status}${muscles.length ? ` (${muscles.join(', ')})` : ''}`)
    }
  }
  if (Object.prototype.hasOwnProperty.call(updates, 'injury_muscles')) {
    const injuries = updates.injury_muscles || []
    parts.push(injuries.length ? `Injuries: ${injuries.join(', ')}` : 'Injuries: none')
  }
  if (Object.prototype.hasOwnProperty.call(updates, 'days_per_week')) {
    parts.push(`Days/week: ${updates.days_per_week}`)
  }
  if (Object.prototype.hasOwnProperty.call(updates, 'intensity')) {
    parts.push(`Intensity: ${updates.intensity}`)
  }
  if (Object.prototype.hasOwnProperty.call(updates, 'fitness_goals')) {
    parts.push(`Goals: ${updates.fitness_goals}`)
  }
  return parts
}

function normalizeIntentProfileUpdates(raw) {
  if (!raw || typeof raw !== 'object') return {}
  const out = {}
  if (Array.isArray(raw.injury_muscles)) {
    out.injury_muscles = Array.from(
      new Set(
        raw.injury_muscles
          .map((m) => String(m || '').trim().toLowerCase())
          .filter(Boolean),
      ),
    )
  }
  if (Number.isInteger(raw.days_per_week) && raw.days_per_week >= 2 && raw.days_per_week <= 7) {
    out.days_per_week = raw.days_per_week
  }
  if (typeof raw.intensity === 'number' && raw.intensity >= 1 && raw.intensity <= 5) {
    out.intensity = raw.intensity
  }
  if (typeof raw.fitness_goals === 'string' && raw.fitness_goals.trim()) {
    out.fitness_goals = raw.fitness_goals.trim()
  }
  if (raw.injury_update && typeof raw.injury_update === 'object') {
    const injuryUpdate = {}
    if (typeof raw.injury_update.status === 'string' && raw.injury_update.status.trim()) {
      injuryUpdate.status = raw.injury_update.status.trim().toLowerCase()
    }
    if (typeof raw.injury_update.operation === 'string' && raw.injury_update.operation.trim()) {
      injuryUpdate.operation = raw.injury_update.operation.trim().toLowerCase()
    }
    if (Array.isArray(raw.injury_update.muscles)) {
      injuryUpdate.muscles = Array.from(
        new Set(
          raw.injury_update.muscles
            .map((m) => String(m || '').trim().toLowerCase())
            .filter(Boolean),
        ),
      )
    }
    if (Object.keys(injuryUpdate).length > 0) {
      out.injury_update = injuryUpdate
    }
  }
  return out
}

function toPersistedProfileUpdates(updates) {
  const out = {}
  for (const key of ['injury_muscles', 'days_per_week', 'intensity', 'fitness_goals']) {
    if (Object.prototype.hasOwnProperty.call(updates || {}, key)) {
      out[key] = updates[key]
    }
  }
  return out
}

function normalizeJointMuscleMap(rawMap) {
  const source = rawMap && typeof rawMap === 'object' ? rawMap : DEFAULT_JOINT_MUSCLE_MAP
  const out = {}
  for (const [jointRaw, musclesRaw] of Object.entries(source)) {
    const joint = String(jointRaw || '').trim().toLowerCase()
    if (!joint || !Array.isArray(musclesRaw)) continue
    const muscles = Array.from(
      new Set(
        musclesRaw
          .map((m) => String(m || '').trim().toLowerCase())
          .filter(Boolean),
      ),
    )
    if (!muscles.length) continue
    out[joint] = muscles
  }
  return Object.keys(out).length ? out : DEFAULT_JOINT_MUSCLE_MAP
}

function uniqueMusclesFromJointMap(jointMap, selectedMuscles = []) {
  const out = []
  const seen = new Set()
  Object.values(jointMap || {}).forEach((muscles) => {
    ;(muscles || []).forEach((muscle) => {
      const m = String(muscle || '').trim().toLowerCase()
      if (!m || seen.has(m)) return
      seen.add(m)
      out.push(m)
    })
  })
  ;(selectedMuscles || []).forEach((muscle) => {
    const m = String(muscle || '').trim().toLowerCase()
    if (!m || seen.has(m)) return
    seen.add(m)
    out.push(m)
  })
  return out
}

export default function App() {
  const [athletes, setAthletes] = useState([])
  const [activeAthleteId, setActiveAthleteId] = useState('')
  const [newAthleteOpen, setNewAthleteOpen] = useState(false)
  const [newAthleteName, setNewAthleteName] = useState('')
  const [renameAthleteOpen, setRenameAthleteOpen] = useState(false)
  const [renameAthleteName, setRenameAthleteName] = useState('')
  const [athleteMenuOpen, setAthleteMenuOpen] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [userId, setUserId] = useState('')
  const [profile, setProfile] = useState(DEFAULT_PROFILE)
  const [profileEdit, setProfileEdit] = useState(false)

  const [chatMessages, setChatMessages] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [chatActivity, setChatActivity] = useState('')
  const [expandedMsg, setExpandedMsg] = useState({})
  const [collapsedPlanMsg, setCollapsedPlanMsg] = useState({})

  const hasConversation = useMemo(
    () => chatMessages.some((m) => m.role === 'assistant' || m.role === 'user'),
    [chatMessages],
  )

  const [activePlan, setActivePlan] = useState(null)
  const [selectedDay, setSelectedDay] = useState(null)
  const [lastRequestId, setLastRequestId] = useState(null)

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerLoading, setDrawerLoading] = useState(false)
  const [drawerDay, setDrawerDay] = useState(null)
  const [drawerData, setDrawerData] = useState(null)
  const [explainSectionsOpen, setExplainSectionsOpen] = useState({
    symbolic: true,
    policy: true,
    llm: true,
  })
  const [toasts, setToasts] = useState([])
  const toastTimersRef = useRef({})

  const [onboardingDraft, setOnboardingDraft] = useState({
    goals: '',
    days: 5,
    injuries: [],
    intensity: 3,
  })
  const [injuryMap, setInjuryMap] = useState(DEFAULT_JOINT_MUSCLE_MAP)

  const chatEndRef = useRef(null)

  const activeAthlete = useMemo(
    () => athletes.find((a) => a.id === activeAthleteId) || null,
    [athletes, activeAthleteId],
  )

  useEffect(() => {
    let stored = []
    try {
      const raw = localStorage.getItem(ATHLETE_STORAGE_KEY)
      if (raw) {
        const parsed = JSON.parse(raw)
        if (Array.isArray(parsed)) stored = parsed.filter((x) => x && x.id && x.name)
      }
    } catch {
      stored = []
    }

    if (!stored.length) {
      stored = [{ id: 'athlete_default', name: 'My Athlete' }]
    }

    setAthletes(stored)
    const lastActive = localStorage.getItem(ATHLETE_ACTIVE_KEY)
    const fallbackId = stored[0]?.id || ''
    setActiveAthleteId(stored.some((a) => a.id === lastActive) ? lastActive : fallbackId)
  }, [])

  useEffect(() => {
    if (!activeAthleteId) return
    const match = athletes.find((a) => a.id === activeAthleteId)
    if (!match) return
    setUserId(match.id)
    setRenameAthleteName(match.name)
    localStorage.setItem(ATHLETE_ACTIVE_KEY, match.id)
  }, [activeAthleteId, athletes])

  useEffect(() => {
    if (!athletes.length) return
    localStorage.setItem(ATHLETE_STORAGE_KEY, JSON.stringify(athletes))
  }, [athletes])

  useEffect(() => {
    let cancelled = false
    const loadInjuryMap = async () => {
      try {
        const res = await fetch(`${API}/injury-map`)
        if (!res.ok) return
        const data = await res.json()
        if (cancelled) return
        setInjuryMap(normalizeJointMuscleMap(data?.joints))
      } catch {
        // Keep fallback mapping from KB defaults.
      }
    }
    loadInjuryMap()
    return () => {
      cancelled = true
    }
  }, [])

  const removeToast = (id) => {
    const meta = toastTimersRef.current[id]
    if (meta?.timer) window.clearTimeout(meta.timer)
    delete toastTimersRef.current[id]
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }

  const scheduleToastDismiss = (id, delayMs) => {
    const prev = toastTimersRef.current[id]
    if (prev?.timer) window.clearTimeout(prev.timer)
    const delay = Math.max(0, delayMs)
    const timer = window.setTimeout(() => {
      removeToast(id)
    }, delay)
    toastTimersRef.current[id] = {
      timer,
      startedAt: Date.now(),
      remainingMs: delay,
    }
  }

  const pauseToastDismiss = (id) => {
    const meta = toastTimersRef.current[id]
    if (!meta || !meta.timer) return
    window.clearTimeout(meta.timer)
    const elapsed = Date.now() - meta.startedAt
    const remaining = Math.max(0, meta.remainingMs - elapsed)
    toastTimersRef.current[id] = {
      timer: null,
      startedAt: Date.now(),
      remainingMs: remaining,
    }
  }

  const resumeToastDismiss = (id) => {
    const meta = toastTimersRef.current[id]
    if (!meta) return
    if (meta.remainingMs <= 0) {
      removeToast(id)
      return
    }
    scheduleToastDismiss(id, meta.remainingMs)
  }

  const pushToast = (type, text) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    setToasts((prev) => [...prev, { id, type, text }])
    scheduleToastDismiss(id, 2400)
  }

  useEffect(() => {
    return () => {
      Object.values(toastTimersRef.current).forEach((meta) => {
        if (meta?.timer) window.clearTimeout(meta.timer)
      })
      toastTimersRef.current = {}
    }
  }, [])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages])

  useEffect(() => {
    const id = (userId || '').trim()
    if (!id) {
      setProfile(DEFAULT_PROFILE)
      setActivePlan(null)
      setSelectedDay(null)
      return
    }

    fetch(`${API}/profile?user_id=${encodeURIComponent(id)}`)
      .then((r) => (r.ok ? r.json() : {}))
      .then((p) => {
        const next = {
          ...DEFAULT_PROFILE,
          ...p,
          soreness_per_muscle: p.soreness_per_muscle || {},
          weekly_volume_per_muscle: p.weekly_volume_per_muscle || {},
        }
        setProfile(next)
        setOnboardingDraft({
          goals: next.fitness_goals || '',
          days: next.days_per_week ?? 5,
          injuries: next.injury_muscles || [],
          intensity: next.intensity ?? 3,
        })
      })
      .catch(() => setProfile(DEFAULT_PROFILE))
  }, [userId])

  const createAthlete = () => {
    const name = newAthleteName.trim()
    if (!name) {
      pushToast('error', 'Enter an athlete name first.')
      return
    }
    const id = makeAthleteId(name)
    const next = { id, name }
    setAthletes((prev) => [next, ...prev])
    setActiveAthleteId(id)
    setNewAthleteName('')
    setNewAthleteOpen(false)
    pushToast('success', `Switched to ${name}`)
  }

  const renameAthlete = () => {
    const id = activeAthleteId
    const name = renameAthleteName.trim()
    if (!id) {
      pushToast('error', 'No athlete selected.')
      return
    }
    if (!name) {
      pushToast('error', 'Enter a new athlete name.')
      return
    }
    setAthletes((prev) => prev.map((a) => (a.id === id ? { ...a, name } : a)))
    setRenameAthleteOpen(false)
    setAthleteMenuOpen(false)
    pushToast('success', `Renamed to ${name}`)
  }

  const requestDeleteAthlete = () => {
    const id = activeAthleteId
    if (!id) {
      pushToast('error', 'No athlete selected.')
      return
    }
    if (athletes.length <= 1) {
      pushToast('error', 'You must keep at least one athlete profile.')
      return
    }
    setDeleteConfirmOpen(true)
  }

  const deleteAthlete = () => {
    const id = activeAthleteId
    if (!id) return
    const target = athletes.find((a) => a.id === id)
    const remaining = athletes.filter((a) => a.id !== id)
    const nextActive = remaining[0]?.id || ''
    setAthletes(remaining)
    setActiveAthleteId(nextActive)
    setActivePlan(null)
    setSelectedDay(null)
    setProfile(DEFAULT_PROFILE)
    setDeleteConfirmOpen(false)
    setAthleteMenuOpen(false)
    pushToast('success', `Deleted ${target?.name || 'athlete'} and switched profile.`)
  }

  const stateRows = useMemo(() => {
    const soreness = profile.soreness_per_muscle || {}
    const volume = profile.weekly_volume_per_muscle || {}
    const muscles = Array.from(new Set([...Object.keys(soreness), ...Object.keys(volume)])).sort()
    const cap = Math.max(12, Math.min(28, Math.round(4 * (profile.intensity ?? 3) + 8)))
    return {
      cap,
      rows: muscles.map((muscle) => ({
        muscle,
        soreness: Number(soreness[muscle] ?? 0),
        volume: Number(volume[muscle] ?? 0),
      })),
    }
  }, [profile])

  const dayLabels = useMemo(() => sortedDayLabelsFromPlan(activePlan?.plan || {}), [activePlan])

  useEffect(() => {
    if (!selectedDay && dayLabels.length) setSelectedDay(dayLabels[0])
    if (selectedDay && dayLabels.length && !dayLabels.includes(selectedDay)) setSelectedDay(dayLabels[0])
  }, [dayLabels, selectedDay])

  const selectedDayExercises = useMemo(() => {
    if (!activePlan || !selectedDay) return []
    return activePlan.plan?.[selectedDay] || []
  }, [activePlan, selectedDay])

  const selectedDayInsights = useMemo(() => {
    if (!selectedDay) {
      return {
        totalSets: 0,
        recoveryLoad: 'Light',
        estMinutes: 0,
        focusMuscles: [],
      }
    }

    const totalSets = selectedDayExercises.reduce((acc, ex) => acc + Number(ex.sets || 0), 0)
    const estMinutes = Math.max(20, Math.round((selectedDayExercises.length * 7 + totalSets * 1.5) / 5) * 5)
    const recoveryLoad = totalSets >= 20 ? 'High' : totalSets >= 14 ? 'Moderate' : 'Light'
    const fromTheme = (activePlan?.day_theme_muscles?.[selectedDay] || []).map((m) => String(m).trim()).filter(Boolean)
    const fromExercises = Array.from(new Set(selectedDayExercises.map((ex) => String(ex.target || '').trim()).filter(Boolean)))

    return {
      totalSets,
      recoveryLoad,
      estMinutes,
      focusMuscles: (fromTheme.length ? fromTheme : fromExercises).slice(0, 4),
    }
  }, [activePlan, selectedDay, selectedDayExercises])

  const topSorenessSignals = useMemo(() => {
    return Object.entries(profile.soreness_per_muscle || {})
      .map(([muscle, score]) => ({ muscle, score: Number(score || 0) }))
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 2)
  }, [profile.soreness_per_muscle])

  const projectedStateRows = useMemo(() => {
    if (stateRows.rows.length > 0 || !activePlan?.plan) return []
    const totals = {}
    Object.values(activePlan.plan || {}).forEach((dayExercises) => {
      ;(dayExercises || []).forEach((ex) => {
        const target = String(ex.target || '').trim().toLowerCase()
        if (!target) return
        totals[target] = (totals[target] || 0) + Number(ex.sets || 0)
      })
    })
    return Object.entries(totals)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([muscle, volume]) => ({
        muscle,
        soreness: 0,
        volume: Number(volume),
      }))
  }, [activePlan, stateRows.rows.length])

  const normalizedDraftInjuries = useMemo(
    () => Array.from(new Set((onboardingDraft.injuries || []).map((m) => String(m || '').trim().toLowerCase()).filter(Boolean))),
    [onboardingDraft.injuries],
  )

  const selectedInjurySet = useMemo(
    () => new Set(normalizedDraftInjuries),
    [normalizedDraftInjuries],
  )

  const jointOptions = useMemo(
    () => Object.keys(injuryMap || {}),
    [injuryMap],
  )

  const muscleOptions = useMemo(
    () => uniqueMusclesFromJointMap(injuryMap, normalizedDraftInjuries),
    [injuryMap, normalizedDraftInjuries],
  )

  const statusText = chatLoading
    ? (chatActivity || 'Working on your request…')
    : 'Ready • Generate a plan or ask for changes'

  const lastUserMessage = useMemo(() => {
    for (let i = chatMessages.length - 1; i >= 0; i -= 1) {
      const m = chatMessages[i]
      if (m?.role === 'user' && m?.text) return String(m.text).toLowerCase()
    }
    return ''
  }, [chatMessages])

  const guidedPrompts = useMemo(() => {
    const hasPlan = Boolean(activePlan?.plan && Object.keys(activePlan.plan || {}).length)
    if (!hasPlan) {
      return [
        { icon: '⚡', text: 'Generate my plan for this week.' },
        { icon: '🏗️', text: 'Plan a 3-day split for me.' },
        { icon: '🩹', text: 'Update my injuries before generating.' },
        { icon: '🎯', text: 'Set my goals to build strength safely.' },
      ]
    }

    const prompts = []
    const selectedLabel = selectedDay ? formatDayLabel(selectedDay) : 'today'

    if (selectedDay) {
      prompts.push({ icon: '🔎', text: `Explain ${selectedLabel} for me.` })
    }

    if (lastUserMessage.includes('today')) {
      prompts.push({ icon: '➡️', text: 'Tell me about tomorrow.' })
      prompts.push({ icon: '🛡️', text: `Make ${selectedLabel} lower impact.` })
      prompts.push({ icon: '♻️', text: `Regenerate ${selectedLabel}.` })
    } else if (lastUserMessage.includes('tomorrow') || lastUserMessage.includes('next workout')) {
      prompts.push({ icon: '🗓️', text: 'What am I doing today?' })
      prompts.push({ icon: '🛠️', text: 'Swap my first two exercises for this day.' })
      prompts.push({ icon: '📉', text: 'Reduce volume for this day.' })
    } else if (lastUserMessage.includes('progress') || lastUserMessage.includes('status')) {
      prompts.push({ icon: '📈', text: 'Show my progress summary.' })
      prompts.push({ icon: '🧠', text: 'Explain how workload adaptation works.' })
      prompts.push({ icon: '⚖️', text: 'How can I avoid overtraining this week?' })
    } else {
      prompts.push({ icon: '🗓️', text: 'What am I doing today?' })
      prompts.push({ icon: '➡️', text: 'What is my next workout?' })
      prompts.push({ icon: '🛡️', text: 'Explain this day with injury safety in mind.' })
    }

    const deduped = []
    const seen = new Set()
    prompts.forEach((p) => {
      if (!p?.text || seen.has(p.text)) return
      seen.add(p.text)
      deduped.push(p)
    })
    return deduped.slice(0, 4)
  }, [activePlan, selectedDay, lastUserMessage])

  const saveProfile = async (updates) => {
    const id = (userId || '').trim()
    if (!id) return
    await fetch(`${API}/profile`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: id, ...updates }),
    })
    setProfile((prev) => ({ ...prev, ...updates }))
  }

  const resolveIntent = async (id, message) => {
    try {
      const res = await fetch(`${API}/intent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: id, message }),
      })
      if (!res.ok) return { intent: 'chat', days_per_week: null, assistant_reply: null, profile_updates: {} }
      const data = await res.json()
      return {
        intent: data.intent || 'chat',
        days_per_week: Number.isInteger(data.days_per_week) ? data.days_per_week : null,
        assistant_reply: typeof data.assistant_reply === 'string' ? data.assistant_reply : null,
        profile_updates: normalizeIntentProfileUpdates(data.profile_updates),
      }
    } catch {
      return { intent: 'chat', days_per_week: null, assistant_reply: null, profile_updates: {} }
    }
  }

  const applyDetectedProfileUpdate = async (messageIndex, updates) => {
    const persisted = toPersistedProfileUpdates(updates || {})
    if (Object.keys(persisted).length === 0) return
    await saveProfile(persisted)
    setOnboardingDraft((prev) => ({
      ...prev,
      goals: persisted.fitness_goals ?? prev.goals,
      days: persisted.days_per_week ?? prev.days,
      injuries: persisted.injury_muscles ?? prev.injuries,
      intensity: persisted.intensity ?? prev.intensity,
    }))
    setChatMessages((prev) => prev.map((m, idx) => (
      idx === messageIndex
        ? { ...m, pendingProfileStatus: 'applied' }
        : m
    )))
    pushToast('success', 'Profile card updated from confirmed changes.')
  }

  const discardDetectedProfileUpdate = (messageIndex) => {
    setChatMessages((prev) => prev.map((m, idx) => (
      idx === messageIndex
        ? { ...m, pendingProfileStatus: 'discarded' }
        : m
    )))
    pushToast('success', 'Detected profile update discarded.')
  }

  const runGeneratePlan = async (id, daysOverride = null) => {
    const res = await fetch(`${API}/plan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: id,
        name: id,
        experience_level: 'beginner',
        injury_muscles: profile.injury_muscles || [],
        days_per_week: Number.isInteger(daysOverride) ? daysOverride : null,
      }),
    })
    const requestId = res.headers.get('X-Request-ID')
    if (requestId) setLastRequestId(requestId)
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    return { ...data, requestId }
  }

  const publishPlan = (data, assistantText) => {
    const planPack = {
      plan: data.plan || {},
      krr_reasoning: data.krr_reasoning || {},
      day_themes: data.day_themes || {},
      day_theme_muscles: data.day_theme_muscles || {},
      evaluation_ok: data.evaluation_ok,
      suggestions: data.suggestions || [],
      requestId: data.requestId || null,
      createdAt: new Date().toISOString(),
    }

    setActivePlan(planPack)
    const labels = sortedDayLabelsFromPlan(planPack.plan)
    setSelectedDay(labels[0] || null)

    setChatMessages((prev) => [
      ...prev,
      {
        role: 'assistant',
        text: assistantText,
        plan: planPack.plan,
        day_themes: planPack.day_themes,
        krr_reasoning: planPack.krr_reasoning,
        safetyVerified: Boolean(planPack.evaluation_ok),
        validationText: planPack.evaluation_ok
          ? 'Plan validated against safety constraints.'
          : `Plan generated with suggestions: ${(planPack.suggestions || []).join(' ') || 'See policy notes.'}`,
      },
    ])

  }

  const generatePlan = async (daysOverride = null, source = 'button') => {
    const id = (userId || '').trim()
    if (!id) {
      pushToast('error', 'Select or create an athlete profile first.')
      return
    }

    if (!profile.onboarding_complete) {
      pushToast('error', 'Complete the onboarding wizard in the left panel first.')
      return
    }

    setChatLoading(true)
    setChatActivity('Building your plan with injury and safety rules…')

    try {
      const data = await runGeneratePlan(id, daysOverride)
      setChatActivity('Finalizing your plan summary…')
      const days = sortedDayLabelsFromPlan(data.plan || {}).length
      const text = source === 'chat'
        ? `Plan ready. Built ${days}-day themed schedule with rule validation and adaptation.`
        : `Generated ${days}-day themed plan. Use day tabs on the right to inspect and control each day.`
      publishPlan(data, text)
    } catch (e) {
      pushToast('error', `Plan generation failed: ${e.message || 'Unknown error'}`)
    } finally {
      setChatLoading(false)
      setChatActivity('')
    }
  }

  const sendChat = async (textOverride = null) => {
    const msg = (textOverride ?? chatInput).trim()
    if (!msg) return
    if (!textOverride) setChatInput('')
    setChatMessages((prev) => [...prev, { role: 'user', text: msg }])

    const id = (userId || '').trim() || 'user'
    setChatLoading(true)
    setChatActivity('Understanding your request…')
    try {
      const intent = await resolveIntent(id, msg)
      setChatActivity('Routing to the best action…')
      const inferred = intent.profile_updates || {}
      const persistableInferred = toPersistedProfileUpdates(inferred)
      const hasPersistableUpdates = Object.keys(persistableInferred).length > 0

      if (intent.intent === 'generate_plan') {
        if (hasPersistableUpdates) {
          setChatActivity('Applying profile updates…')
          await saveProfile(persistableInferred)
          setOnboardingDraft((prev) => ({
            ...prev,
            goals: persistableInferred.fitness_goals ?? prev.goals,
            days: persistableInferred.days_per_week ?? prev.days,
            injuries: persistableInferred.injury_muscles ?? prev.injuries,
            intensity: persistableInferred.intensity ?? prev.intensity,
          }))
          pushToast('success', 'Applied detected profile updates before plan generation.')
        }
        setChatActivity('Generating your weekly plan…')
        await generatePlan(intent.days_per_week, 'chat')
        return
      }

      if (intent.intent === 'profile_update') {
        setChatActivity('Preparing profile update confirmation…')
        if (hasPersistableUpdates) {
          const detectedSummary = summarizeProfileUpdates(inferred)
          setChatMessages((prev) => [...prev, {
            role: 'assistant',
            text: intent.assistant_reply || 'Detected profile updates. Confirm before saving.',
            pendingProfileUpdate: inferred,
            pendingProfileSummary: detectedSummary,
            pendingProfileStatus: 'pending',
          }])
        } else {
          setChatMessages((prev) => [...prev, {
            role: 'assistant',
            text: intent.assistant_reply || 'Profile update detected but no concrete fields were extracted.',
          }])
        }
        return
      }

      const planSummary = activePlan
        ? sortedDayLabelsFromPlan(activePlan.plan)
          .map((d) => `${d}: ${(activePlan.plan[d] || []).map((e) => e.name).join(', ') || 'Rest'}`)
          .join('; ')
        : null

      const krrSummary = activePlan
        ? Object.entries(activePlan.krr_reasoning || {}).slice(0, 2).map(([, v]) => clip(v, 240)).join(' ')
        : null

      setChatActivity('Composing response from your plan and profile…')
      const res = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: id,
          message: msg,
          plan_summary: planSummary,
          krr_summary: krrSummary,
          selected_day: selectedDay || null,
        }),
      })
      const requestId = res.headers.get('X-Request-ID')
      if (requestId) setLastRequestId(requestId)
      if (!res.ok) throw new Error(await res.text())
      setChatActivity('Formatting response…')
      const data = await res.json()
      setChatMessages((prev) => [...prev, { role: 'assistant', text: data.response || 'No response.' }])
    } catch (e) {
      pushToast('error', `Chat failed: ${e.message || 'Unknown error'}`)
    } finally {
      setChatLoading(false)
      setChatActivity('')
    }
  }

  const openExplanationDrawer = async (day) => {
    const id = (userId || '').trim()
    if (!id || !day) return

    setDrawerDay(day)
    setDrawerOpen(true)
    setDrawerLoading(true)
    setDrawerData(null)
    setExplainSectionsOpen({ symbolic: true, policy: true, llm: true })

    try {
      const res = await fetch(`${API}/explanation?day=${encodeURIComponent(day)}&user_id=${encodeURIComponent(id)}`)
      const data = res.ok ? await res.json() : null
      setDrawerData(data || { error: await res.text() })
    } catch (e) {
      setDrawerData({ error: e.message || 'Failed to fetch explanation' })
    } finally {
      setDrawerLoading(false)
    }
  }

  const toggleExplainSection = (key) => {
    setExplainSectionsOpen((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const applyDayActionResult = (payload) => {
    if (!payload?.plan) return
    setActivePlan((prev) => {
      const base = prev || {}
      const nextThemes = { ...(base.day_themes || {}) }
      const nextThemeMuscles = { ...(base.day_theme_muscles || {}) }
      if (payload.day && payload.day_theme) nextThemes[payload.day] = payload.day_theme
      if (payload.day && Array.isArray(payload.day_theme_muscles)) nextThemeMuscles[payload.day] = payload.day_theme_muscles
      return {
        ...base,
        plan: payload.plan,
        krr_reasoning: payload.krr_reasoning || base.krr_reasoning || {},
        day_themes: nextThemes,
        day_theme_muscles: nextThemeMuscles,
      }
    })
    if (payload?.message) {
      pushToast('success', payload.message)
    } else {
      pushToast('success', 'Action completed successfully.')
    }
  }

  const regenerateDay = async () => {
    if (!selectedDay) return
    const id = (userId || '').trim()
    if (!id) return
    setChatLoading(true)
    try {
      const res = await fetch(`${API}/plan/day/regenerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: id, day: selectedDay }),
      })
      const requestId = res.headers.get('X-Request-ID')
      if (requestId) setLastRequestId(requestId)
      if (!res.ok) throw new Error(await res.text())
      const payload = await res.json()
      applyDayActionResult(payload)
    } catch (e) {
      pushToast('error', `Regeneration failed: ${e.message || 'Unknown error'}`)
    } finally {
      setChatLoading(false)
    }
  }

  const reduceVolumeForDay = async () => {
    if (!selectedDay) return
    const id = (userId || '').trim()
    if (!id) return
    setChatLoading(true)
    try {
      const res = await fetch(`${API}/plan/day/reduce`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: id, day: selectedDay, delta_sets: 1 }),
      })
      const requestId = res.headers.get('X-Request-ID')
      if (requestId) setLastRequestId(requestId)
      if (!res.ok) throw new Error(await res.text())
      const payload = await res.json()
      applyDayActionResult(payload)
    } catch (e) {
      pushToast('error', `Reduce volume failed: ${e.message || 'Unknown error'}`)
    } finally {
      setChatLoading(false)
    }
  }

  const swapExerciseForDay = async () => {
    if (!selectedDay) return
    const id = (userId || '').trim()
    if (!id) return
    setChatLoading(true)
    try {
      const res = await fetch(`${API}/plan/day/swap`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: id, day: selectedDay, index_a: 0, index_b: 1 }),
      })
      const requestId = res.headers.get('X-Request-ID')
      if (requestId) setLastRequestId(requestId)
      if (!res.ok) throw new Error(await res.text())
      const payload = await res.json()
      applyDayActionResult(payload)
    } catch (e) {
      pushToast('error', `Swap exercise failed: ${e.message || 'Unknown error'}`)
    } finally {
      setChatLoading(false)
    }
  }

  const toggleInjuryMuscle = (muscle) => {
    const m = String(muscle || '').trim().toLowerCase()
    if (!m) return
    setOnboardingDraft((prev) => {
      const current = Array.from(new Set((prev.injuries || []).map((x) => String(x || '').trim().toLowerCase()).filter(Boolean)))
      const exists = current.includes(m)
      const next = exists ? current.filter((x) => x !== m) : [...current, m]
      return { ...prev, injuries: next }
    })
  }

  const toggleInjuryJoint = (joint) => {
    const j = String(joint || '').trim().toLowerCase()
    const mapped = (injuryMap?.[j] || []).map((m) => String(m || '').trim().toLowerCase()).filter(Boolean)
    if (!mapped.length) return
    setOnboardingDraft((prev) => {
      const current = Array.from(new Set((prev.injuries || []).map((x) => String(x || '').trim().toLowerCase()).filter(Boolean)))
      const currentSet = new Set(current)
      const allSelected = mapped.every((m) => currentSet.has(m))
      let next
      if (allSelected) {
        next = current.filter((m) => !mapped.includes(m))
      } else {
        next = Array.from(new Set([...current, ...mapped]))
      }
      return { ...prev, injuries: next }
    })
  }

  const isJointHighlighted = (joint) => {
    const mapped = injuryMap?.[joint] || []
    if (!mapped.length) return false
    return mapped.every((muscle) => selectedInjurySet.has(String(muscle).toLowerCase()))
  }

  const completeOnboarding = async () => {
    const id = (userId || '').trim()
    if (!id) {
      pushToast('error', 'Select or create an athlete profile before saving onboarding.')
      return
    }
    await saveProfile({
      onboarding_complete: true,
      fitness_goals: onboardingDraft.goals,
      days_per_week: onboardingDraft.days,
      injury_muscles: onboardingDraft.injuries,
      intensity: onboardingDraft.intensity,
    })
    pushToast('success', 'Onboarding complete. You can now generate a validated plan.')
  }

  return (
    <div className="control-center">
      <div className="toast-stack" aria-live="polite" aria-atomic="true">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`toast toast-${toast.type}`}
            onMouseEnter={() => pauseToastDismiss(toast.id)}
            onMouseLeave={() => resumeToastDismiss(toast.id)}
          >
            <span className="toast-text">{toast.text}</span>
            <button
              type="button"
              className="toast-close"
              aria-label="Dismiss notification"
              onClick={() => removeToast(toast.id)}
            >
              ×
            </button>
          </div>
        ))}
      </div>

      <aside className="left-panel card">
        <h2 className="panel-title">Athlete Profile & State</h2>
        <p className="panel-subtitle">Set profile, review readiness, and track recovery.</p>

        <section className="panel-section">
          <div className="section-head">
            <h3>Current Athlete</h3>
            <div className="athlete-settings-wrap">
              <button
                type="button"
                className="btn btn-ghost btn-sm settings-btn"
                aria-label="Athlete settings"
                onClick={() => setAthleteMenuOpen((v) => !v)}
              >
                ⚙
              </button>
              {athleteMenuOpen && (
                <div className="settings-menu">
                  <button
                    type="button"
                    className="settings-item"
                    onClick={() => {
                      setRenameAthleteOpen((v) => !v)
                      setAthleteMenuOpen(false)
                    }}
                  >
                    Rename athlete
                  </button>
                  <button
                    type="button"
                    className="settings-item danger"
                    onClick={() => {
                      requestDeleteAthlete()
                      setAthleteMenuOpen(false)
                    }}
                  >
                    Delete athlete
                  </button>
                </div>
              )}
            </div>
          </div>
          <p className="section-hint">Switch or manage the active athlete profile.</p>
          <p className="profile-active-name">🧍 {activeAthlete?.name || 'No athlete selected'}</p>
          {DEV_MODE && <p className="muted small">ID: {activeAthlete?.id || '-'}</p>}
          {renameAthleteOpen && (
            <div className="new-athlete-row">
              <input
                value={renameAthleteName}
                onChange={(e) => setRenameAthleteName(e.target.value)}
                placeholder="New athlete name"
                className="user-input"
              />
              <button type="button" className="btn btn-primary btn-sm" onClick={renameAthlete}>Save</button>
            </div>
          )}
        </section>

        <section className="panel-section">
          <div className="section-head">
            <h3>Profile Card</h3>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setProfileEdit((v) => !v)}>
              {profileEdit ? 'Done' : 'Edit'}
            </button>
          </div>
          <p className="section-hint">These values drive plan generation and safety filters.</p>

          {!profileEdit ? (
            <div className="profile-grid">
              <p><span className="profile-icon">🎯</span><strong> Goals:</strong> {profile.fitness_goals || 'Not set'}</p>
              <p><span className="profile-icon">📅</span><strong> Days/week:</strong> {profile.days_per_week}</p>
              <p><span className="profile-icon">🔥</span><strong> Intensity:</strong> {profile.intensity}</p>
              <div className="mini-meter">
                <div className="mini-meter-fill" style={{ width: `${Math.min(100, ((Number(profile.intensity) || 1) / 5) * 100)}%` }} />
              </div>
              <p><span className="profile-icon">🛡️</span><strong> Injuries:</strong> {(profile.injury_muscles || []).join(', ') || 'None'}</p>
            </div>
          ) : (
            <div className="wizard-grid">
              <p className="wizard-label">Complete setup to generate reliable plans</p>
              <div className="wizard-steps">
                {ONBOARDING_STEPS.map((step, idx) => (
                  <div key={step} className="wizard-step">
                    <span className="wizard-dot">{idx + 1}</span> {step}
                  </div>
                ))}
              </div>

              <label>Goals</label>
              <input
                value={onboardingDraft.goals}
                onChange={(e) => setOnboardingDraft((p) => ({ ...p, goals: e.target.value }))}
                placeholder="e.g. build strength and reduce injury risk"
              />

              <label>Days / Week</label>
              <select
                value={onboardingDraft.days}
                onChange={(e) => setOnboardingDraft((p) => ({ ...p, days: Number(e.target.value) }))}
              >
                {[2, 3, 4, 5, 6, 7].map((d) => <option key={d} value={d}>{d}</option>)}
              </select>

              <label>Intensity ({onboardingDraft.intensity})</label>
              <input
                type="range"
                min={1}
                max={5}
                step={0.5}
                value={onboardingDraft.intensity}
                onChange={(e) => setOnboardingDraft((p) => ({ ...p, intensity: Number(e.target.value) }))}
              />

              <label>Injury filters</label>
              <div className="injury-filter-grid">
                <div className="injury-filter-group">
                  <p className="injury-filter-title">Joints</p>
                  <div className="chips">
                    {jointOptions.map((joint) => (
                      <button
                        key={joint}
                        type="button"
                        className={`chip ${isJointHighlighted(joint) ? 'chip-active' : ''}`}
                        onClick={() => toggleInjuryJoint(joint)}
                      >
                        {joint}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="injury-filter-group">
                  <p className="injury-filter-title">Muscles</p>
                  <div className="chips">
                    {muscleOptions.map((muscle) => (
                      <button
                        key={muscle}
                        type="button"
                        className={`chip ${selectedInjurySet.has(muscle) ? 'chip-active' : ''}`}
                        onClick={() => toggleInjuryMuscle(muscle)}
                      >
                        {muscle}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <button type="button" className="btn btn-primary" onClick={completeOnboarding}>Save profile</button>
            </div>
          )}
        </section>

        <section className="panel-section">
          <h3>Current Training State</h3>
          <p className="section-hint">Live or projected load by muscle group.</p>
          {stateRows.rows.length === 0 && projectedStateRows.length === 0 ? (
            <p className="muted">No tracked muscle state yet. Generate a plan to start tracking.</p>
          ) : (
            <div className="state-list">
              {stateRows.rows.length === 0 && projectedStateRows.length > 0 && (
                <p className="muted small">Projected muscle load from current plan</p>
              )}
              {(stateRows.rows.length ? stateRows.rows : projectedStateRows).map((row) => (
                <div key={row.muscle} className="state-row">
                  <div className="state-row-top">
                    <span>{row.muscle}</span>
                    <span className={`heat-pill ${sorenessClass(row.soreness)}`}>soreness {row.soreness || 0}</span>
                  </div>
                  <div className="volume-track">
                    <div className="volume-fill" style={{ width: `${Math.min(100, (row.volume / stateRows.cap) * 100)}%` }} />
                  </div>
                  <span className="muted small">{row.volume} / {stateRows.cap}</span>
                </div>
              ))}
            </div>
          )}
        </section>

      </aside>

      <main className="center-panel card">
        <div className="top-row">
          <div className="center-headline">
            <h1>Workout Planner</h1>
            <p className="top-subtitle">Build safe, personalized weekly workouts</p>
          </div>
          <div className="top-actions">
            <div className="athlete-picker">
              <select
                value={activeAthleteId}
                onChange={(e) => {
                  setActiveAthleteId(e.target.value)
                }}
                className="user-input"
              >
                {athletes.map((athlete) => (
                  <option key={athlete.id} value={athlete.id}>{athlete.name}</option>
                ))}
              </select>
              <button type="button" className="btn btn-ghost" onClick={() => setNewAthleteOpen((v) => !v)}>
                {newAthleteOpen ? 'Cancel' : '➕ New Athlete'}
              </button>
            </div>
              <button type="button" className="btn btn-primary btn-primary-action" disabled={chatLoading} onClick={() => generatePlan()}>
              ⚡ Generate Plan
            </button>
          </div>
        </div>

          <div className="status-strip">
            <span className="pulse-dot" />
            <span>{statusText}</span>
          </div>

        {newAthleteOpen && (
          <div className="new-athlete-row">
            <input
              value={newAthleteName}
              onChange={(e) => setNewAthleteName(e.target.value)}
              placeholder="Athlete name (e.g. Alex, Rehab Phase 1)"
              className="user-input"
            />
            <button type="button" className="btn btn-primary btn-sm" onClick={createAthlete}>Create</button>
          </div>
        )}

        <div className="chat-zone">
          {!hasConversation && !chatLoading && (
            <div className="quick-start">
              <p className="muted">Start with one of these:</p>
              <div className="quick-chips">
                <button type="button" className="quick-chip" onClick={() => sendChat('Plan a 3-day split')}>
                  🏗️ Build a 3-day split
                </button>
                <button type="button" className="quick-chip" onClick={() => sendChat('Show my progress')}>
                  📈 Review training progress
                </button>
                <button type="button" className="quick-chip" onClick={() => sendChat('Generate my plan for this week')}>
                  🗓️ Generate this week’s plan
                </button>
              </div>
            </div>
          )}

          {chatMessages.map((m, idx) => (
            <div key={`${idx}-${m.role}`} className={`msg msg-${m.role}`}>
              {(() => {
                const text = String(m.text || '')
                const lineBreaks = (text.match(/\n/g) || []).length
                const isLongAssistantReply = m.role === 'assistant' && (text.length > 520 || lineBreaks > 8)
                const isExpanded = Boolean(expandedMsg[idx])
                return (
                  <>
                    {m.role !== 'system' && <p className="msg-role">{m.role === 'user' ? '🧑 You' : '🤖 Coach AI'}</p>}
                    <div className={`msg-body markdown-body ${isLongAssistantReply && !isExpanded ? 'msg-body-clamped' : ''}`}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {text}
                      </ReactMarkdown>
                    </div>
                    {isLongAssistantReply && (
                      <button
                        type="button"
                        className="msg-expand"
                        onClick={() => setExpandedMsg((prev) => ({ ...prev, [idx]: !prev[idx] }))}
                      >
                        {isExpanded ? 'Show less' : 'Show more'}
                      </button>
                    )}
                  </>
                )
              })()}
              {m.safetyVerified && <span className="safety-badge">🟢 Safety Verified</span>}
              {m.validationText && !m.safetyVerified && <p className="validation-ok">✓ {m.validationText}</p>}

              {m.pendingProfileUpdate && (
                <div className="detected-update-box">
                  <p className="detected-update-label">Detected update</p>
                  <div className="detected-update-chips">
                    {(m.pendingProfileSummary || []).map((item) => (
                      <span key={item} className="detected-update-chip">{item}</span>
                    ))}
                  </div>

                  {m.pendingProfileStatus === 'pending' && (
                    <div className="detected-update-actions">
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        onClick={() => applyDetectedProfileUpdate(idx, m.pendingProfileUpdate)}
                      >
                        Confirm
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => discardDetectedProfileUpdate(idx)}
                      >
                        Discard
                      </button>
                    </div>
                  )}

                  {m.pendingProfileStatus === 'applied' && (
                    <p className="detected-update-state success">Saved to profile card.</p>
                  )}
                  {m.pendingProfileStatus === 'discarded' && (
                    <p className="detected-update-state muted">Discarded. No profile changes saved.</p>
                  )}
                </div>
              )}

              {m.plan && (
                <div className="plan-summary-card">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setCollapsedPlanMsg((prev) => ({ ...prev, [idx]: !prev[idx] }))}
                  >
                    {collapsedPlanMsg[idx] ? 'Show summary' : 'Hide summary'}
                  </button>

                  {!collapsedPlanMsg[idx] && (
                    <div className="plan-summary-body">
                      {sortedDayLabelsFromPlan(m.plan).map((d) => (
                        <div key={d} className="summary-day-card">
                          <p className="summary-day-head">
                            <strong>{formatDayLabel(d)}</strong>
                            <span className="summary-day-theme">{m.day_themes?.[d] || d}</span>
                          </p>
                          <ul className="summary-ex-list">
                            {(m.plan[d] || []).length === 0 && <li className="summary-ex-item muted">Rest Day</li>}
                            {(m.plan[d] || []).map((e, exIdx) => (
                              <li key={`${d}-${e.name}-${exIdx}`} className="summary-ex-item">
                                {toTitleCase(e.name)}
                              </li>
                            ))}
                          </ul>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="row gap">
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => openExplanationDrawer(selectedDay || sortedDayLabelsFromPlan(m.plan)[0])}
                    >
                      🔍 View reasoning
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={regenerateDay}>♻️ Regenerate selected day</button>
                  </div>
                </div>
              )}
            </div>
          ))}

          {chatLoading && (
            <div className="msg msg-assistant msg-activity" aria-live="polite" aria-atomic="true">
              <p className="msg-role">🤖 Coach AI</p>
              <div className="activity-line">
                <span className="activity-pulse" aria-hidden />
                <span>{chatActivity || 'Thinking…'}</span>
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        <div className="assistant-bar" role="region" aria-label="Assistant actions">
          <div className="system-hints" role="note" aria-label="Chat guidance prompts">
            <p className="system-hints-title">🧭 System Tips</p>
            <div className="system-hints-chips">
              {guidedPrompts.map((prompt) => (
                <button
                  key={prompt.text}
                  type="button"
                  className="system-hint-chip"
                  disabled={chatLoading}
                  onClick={() => sendChat(prompt.text)}
                >
                  <span aria-hidden>{prompt.icon}</span> {prompt.text}
                </button>
              ))}
            </div>
          </div>

          <form
            className="chat-input-row"
            onSubmit={(e) => {
              e.preventDefault()
              sendChat()
            }}
          >
            <input
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="Ask anything (e.g. What am I doing today? / Explain Day 2 / Make next workout lower impact)"
              disabled={chatLoading}
            />
            <button type="submit" className="btn btn-primary" disabled={chatLoading}>➤ Send</button>
          </form>
        </div>

        {DEV_MODE && lastRequestId && <p className="muted small">Request ID: {lastRequestId}</p>}
      </main>

      <aside className="right-panel card">
        <h2 className="panel-title">Weekly Plan View</h2>
        <p className="panel-subtitle">Inspect each day, then explain or regenerate if needed.</p>

        {!activePlan ? (
          <div className="empty-plan">
            <div className="empty-plan-graphic" aria-hidden>
              <span />
              <span />
              <span />
            </div>
            <p className="empty-plan-title">No plan generated yet</p>
            <p className="muted">Generate a plan to populate the weekly schedule.</p>
            <button type="button" className="btn btn-primary btn-primary-action" disabled={chatLoading} onClick={() => generatePlan()}>
              ⚡ Generate Plan
            </button>
          </div>
        ) : (
          <>
            <div className="tabs">
              {dayLabels.map((d) => (
                <button
                  key={d}
                  type="button"
                  className={`tab ${selectedDay === d ? 'tab-active' : ''}`}
                  onClick={() => setSelectedDay(d)}
                >
                  <span>{formatDayLabel(d)}</span>
                  <small>{activePlan?.day_themes?.[d] || 'Theme'}</small>
                </button>
              ))}
            </div>

            <div className="day-view animate-in">
              <h3>{(activePlan.day_themes && activePlan.day_themes[selectedDay]) || formatDayLabel(selectedDay)}</h3>
              <div className="day-insights">
                <div className="focus-badges">
                  {selectedDayInsights.focusMuscles.map((muscle) => (
                    <span key={muscle} className="focus-badge">{muscle}</span>
                  ))}
                </div>
                <div className="day-metrics-inline">
                  <span><strong>Volume:</strong> {selectedDayInsights.totalSets} sets</span>
                  <span><strong>Recovery:</strong> {selectedDayInsights.recoveryLoad}</span>
                  <span><strong>Time:</strong> {selectedDayInsights.estMinutes} min</span>
                </div>
              </div>

              <ol className="exercise-list">
                {selectedDayExercises.length === 0 && <li className="exercise-item muted">Rest Day</li>}
                {selectedDayExercises.map((ex, idx) => (
                  <li key={`${ex.name}-${idx}`} className="exercise-item">
                    <p className="exercise-name">{toTitleCase(ex.name)}</p>
                    <p className="exercise-prescription">{ex.sets} × {ex.reps}</p>
                  </li>
                ))}
              </ol>

              <div className="day-actions row gap wrap">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => openExplanationDrawer(selectedDay)}>
                  🔍 Explain Day
                </button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={regenerateDay}>
                  ♻️ Regenerate Day Plan
                </button>
              </div>
            </div>
          </>
        )}
      </aside>

      <section className={`explain-drawer ${drawerOpen ? 'open' : ''}`}>
        <div className="drawer-head">
          <h3>Reasoning Breakdown{drawerDay ? ` — ${drawerDay}` : ''}</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDrawerOpen(false)}>Close</button>
        </div>

        {drawerLoading && <p className="muted">Loading reasoning…</p>}
        {!drawerLoading && drawerData?.error && <p className="error-text">{drawerData.error}</p>}

        {!drawerLoading && drawerData && !drawerData.error && (
          <div className="drawer-body">
            <div className="reason-block safe">
              <div className="reason-head">
                <div className="reason-head-title">
                  <h4>Symbolic Rules</h4>
                  <span className="confidence-badge confidence-rule">Rule-grounded</span>
                </div>
                <button type="button" className="collapse-btn" onClick={() => toggleExplainSection('symbolic')}>
                  {explainSectionsOpen.symbolic ? 'Hide' : 'Show'}
                </button>
              </div>
              {explainSectionsOpen.symbolic && (
                <div className="reason-content">
                  {(() => {
                    const parsed = parseSymbolicSummary(drawerData.krr_reasoning)
                    return (
                      <>
                        <p>✓ Excluded movements violating injury constraints.</p>
                        {parsed.themeLine && <p><strong>Focus Scope:</strong> {parsed.themeLine}</p>}
                        {parsed.resultLine && <p><strong>Coverage:</strong> {parsed.resultLine}</p>}
                        {parsed.recommendations.length > 0 && (
                          <div className="reason-list-wrap">
                            <p className="muted small">Recommended set</p>
                            <ul className="reason-list">
                              {parsed.recommendations.slice(0, 8).map((name) => (
                                <li key={name}>{toTitleCase(name)}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        <details className="reason-raw">
                          <summary>View raw symbolic trace</summary>
                          <pre>{clip(drawerData.krr_reasoning || 'No symbolic reasoning available.', 1600)}</pre>
                        </details>
                      </>
                    )
                  })()}
                </div>
              )}
            </div>

            <div className="reason-block warning">
              <div className="reason-head">
                <div className="reason-head-title">
                  <h4>Policy Adaptation</h4>
                  <span className="confidence-badge confidence-policy">State-aware</span>
                </div>
                <button type="button" className="collapse-btn" onClick={() => toggleExplainSection('policy')}>
                  {explainSectionsOpen.policy ? 'Hide' : 'Show'}
                </button>
              </div>
              {explainSectionsOpen.policy && (
                <div className="reason-content">
                  <p>✓ Adjusted workload using soreness and weekly volume state.</p>
                  <p className="muted small">Profile intensity drives weekly caps and set scaling.</p>
                  <p className="muted small">Current intensity: {profile.intensity} • Days/week: {profile.days_per_week}</p>
                  {topSorenessSignals.length > 0 && (
                    <p className="muted small">
                      Top soreness signals: {topSorenessSignals.map((s) => `${s.muscle} (${s.score.toFixed(1)})`).join(', ')}
                    </p>
                  )}
                </div>
              )}
            </div>

            <div className="reason-block">
              <div className="reason-head">
                <div className="reason-head-title">
                  <h4>LLM Synthesis</h4>
                  <span className="confidence-badge confidence-llm">LLM rationale</span>
                </div>
                <button type="button" className="collapse-btn" onClick={() => toggleExplainSection('llm')}>
                  {explainSectionsOpen.llm ? 'Hide' : 'Show'}
                </button>
              </div>
              {explainSectionsOpen.llm && (
                <div className="reason-content">
                  <p>✓ Balanced split with safety and progression constraints.</p>
                  <p>{drawerData.llm_explanation || 'No LLM explanation available.'}</p>
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      {deleteConfirmOpen && (
        <section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-delete-title">
          <div className="confirm-card">
            <h3 id="confirm-delete-title">Delete athlete profile?</h3>
            <p>
              You are deleting <strong>{activeAthlete?.name || 'this athlete'}</strong>.
              This removes it from your profile list and clears the current view.
            </p>
            <p className="muted small">A different athlete profile will be selected automatically.</p>
            <div className="row gap confirm-actions">
              <button type="button" className="btn btn-ghost" onClick={() => setDeleteConfirmOpen(false)}>
                Cancel
              </button>
              <button type="button" className="btn btn-danger-solid" onClick={deleteAthlete}>
                Yes, Delete
              </button>
            </div>
          </div>
        </section>
      )}

      {(drawerOpen || deleteConfirmOpen) && (
        <div
          className="drawer-backdrop"
          onClick={() => {
            if (drawerOpen) setDrawerOpen(false)
            if (deleteConfirmOpen) setDeleteConfirmOpen(false)
          }}
          aria-hidden
        />
      )}
    </div>
  )
}
