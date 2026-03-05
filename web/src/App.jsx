import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const API = '/api'
const DEV_MODE = import.meta.env.DEV
const GENERIC_USER_ID = 'default'

const DEFAULT_JOINT_MUSCLE_MAP = {
  shoulder: ['delts', 'traps', 'upper back', 'levator scapulae'],
  elbow: ['biceps', 'triceps', 'forearms'],
  wrist: ['forearms'],
  hip: ['glutes', 'hamstrings', 'adductors', 'abductors'],
  knee: ['quads', 'hamstrings', 'calves'],
  ankle: ['calves'],
  spine: ['upper back', 'lats', 'abs'],
}

const DEFAULT_EQUIPMENT_OPTIONS = [
  'body weight',
  'dumbbell',
  'barbell',
  'kettlebell',
  'cable',
  'band',
  'resistance band',
  'smith machine',
  'leverage machine',
  'stability ball',
  'medicine ball',
  'ez barbell',
]

const DEFAULT_PROFILE = {
  injury_muscles: [],
  equipment_preferences: [],
  intensity: 3,
  onboarding_complete: false,
  days_per_week: 5,
}

function dayNumber(label) {
  const m = String(label || '').match(/\d+/)
  return m ? Number(m[0]) : 0
}

function normalizeDayToken(label) {
  return String(label || '')
    .toLowerCase()
    .replace(/\s+/g, '')
}

function formatDayLabel(label) {
  return String(label || '').replace(/^Day(\d+)$/i, 'Day $1')
}

function sortedDayLabelsFromPlan(plan) {
  return Object.keys(plan || {}).sort((a, b) => dayNumber(a) - dayNumber(b))
}

function resolveDayLabel(labels, preferred) {
  const requested = normalizeDayToken(preferred)
  if (!requested) return labels[0] || null
  const exact = labels.find((label) => normalizeDayToken(label) === requested)
  return exact || labels[0] || null
}

function normalizedExerciseNamesByDay(plan) {
  const out = {}
  for (const day of sortedDayLabelsFromPlan(plan || {})) {
    const names = (plan?.[day] || [])
      .map((ex) => String(ex?.name || '').trim().toLowerCase())
      .filter(Boolean)
    out[day] = Array.from(new Set(names))
  }
  return out
}

function summarizePlanExerciseChanges(previousPlan, nextPlan) {
  if (!previousPlan || !nextPlan) return 'No plan diff available.'
  const prevByDay = normalizedExerciseNamesByDay(previousPlan)
  const nextByDay = normalizedExerciseNamesByDay(nextPlan)
  const dayLabels = Array.from(new Set([...Object.keys(prevByDay), ...Object.keys(nextByDay)]))
    .sort((a, b) => dayNumber(a) - dayNumber(b))

  const lines = []
  for (const day of dayLabels) {
    const prev = prevByDay[day] || []
    const next = nextByDay[day] || []
    const added = next.filter((name) => !prev.includes(name))
    const removed = prev.filter((name) => !next.includes(name))
    if (!added.length && !removed.length) continue
    lines.push(
      `${day}: added [${added.slice(0, 8).join(', ') || 'none'}]; removed [${removed.slice(0, 8).join(', ') || 'none'}]`,
    )
    if (lines.length >= 6) break
  }
  if (!lines.length) return 'No exercise-name changes detected.'
  return lines.join('\n')
}

function summarizeUpdateReasons(updates) {
  const reasons = []
  if (Object.prototype.hasOwnProperty.call(updates || {}, 'days_per_week')) reasons.push(`days/week=${updates.days_per_week}`)
  if (Object.prototype.hasOwnProperty.call(updates || {}, 'injury_muscles')) reasons.push(`injuries=${(updates.injury_muscles || []).join(', ') || 'none'}`)
  if (Object.prototype.hasOwnProperty.call(updates || {}, 'equipment_preferences')) reasons.push(`equipment=${(updates.equipment_preferences || []).join(', ') || 'none'}`)
  if (Object.prototype.hasOwnProperty.call(updates || {}, 'intensity')) reasons.push(`intensity=${updates.intensity}`)
  return reasons.join('; ') || 'profile constraints updated'
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

function formatKrrLogicFromStructured(krrLogic) {
  if (!krrLogic || typeof krrLogic !== 'object') return null
  const inj = krrLogic.injuries || []
  const eq = krrLogic.equipment || []
  const pipeline = krrLogic.constraint_pipeline || []
  const forbiddenCount = krrLogic.forbidden_count ?? 0
  const allowedCount = krrLogic.allowed_exercises_count ?? 0
  const how = krrLogic.how_avoidance || ''
  const themeAdjusted = krrLogic.theme_adjusted_due_to_injury
  const parts = []
  parts.push(`Injuries (avoided): ${inj.length ? inj.map(toTitleCase).join(', ') : 'none'}`)
  parts.push(`Equipment (used): ${eq.length ? eq.map(toTitleCase).join(', ') : 'any'}`)
  if (pipeline.length) parts.push(`Constraint order: ${pipeline.join(' → ')}`)
  if (forbiddenCount > 0) parts.push(`${forbiddenCount} exercise(s) excluded (forbidden)`)
  parts.push(`${allowedCount} exercise(s) in pool after constraints`)
  if (themeAdjusted) parts.push('Theme adjusted due to conflict with injury')
  parts.push(how)
  return parts.join('\n')
}

function sorenessClass(value) {
  if (value >= 4) return 'heat-high'
  if (value >= 3) return 'heat-mid'
  if (value >= 2) return 'heat-low'
  return 'heat-none'
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
  if (Object.prototype.hasOwnProperty.call(updates, 'equipment_preferences')) {
    const equipment = updates.equipment_preferences || []
    parts.push(equipment.length ? `Equipment: ${equipment.join(', ')}` : 'Equipment: none')
  }
  if (Object.prototype.hasOwnProperty.call(updates, 'days_per_week')) {
    parts.push(`Days/week: ${updates.days_per_week}`)
  }
  if (Object.prototype.hasOwnProperty.call(updates, 'intensity')) {
    parts.push(`Intensity: ${updates.intensity}`)
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
  if (Array.isArray(raw.equipment_preferences)) {
    out.equipment_preferences = Array.from(
      new Set(
        raw.equipment_preferences
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
  for (const key of ['injury_muscles', 'equipment_preferences', 'days_per_week', 'intensity']) {
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

function normalizeIntensityValue(value, fallback = 3) {
  const raw = Number(value)
  if (!Number.isFinite(raw)) return fallback
  return Math.min(5, Math.max(1, Math.round(raw)))
}

function IconCalendar() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="4.5" width="18" height="16" rx="4" stroke="currentColor" strokeWidth="1.8" />
      <path d="M3 9.5H21" stroke="currentColor" strokeWidth="1.8" />
      <path d="M8 3V7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M16 3V7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  )
}

function IconFlame() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12.2 3.8c.6 2.1-.4 3.6-1.9 5.4-1.4 1.7-2.8 3.4-2.8 5.8 0 2.8 2.2 5 5 5s5-2.2 5-5c0-3.9-2.8-5.7-5.3-8.2-.2-.2-.3-.5-.4-.8-.9.9-1.3 2-1.3 3.4"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function IconDumbbell() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M5.5 9.5v5M7.5 8v8M16.5 8v8M18.5 9.5v5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M7.5 12h9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  )
}

function IconShieldCheck() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 3.5 5.5 6v5.8c0 3.9 2.6 7.5 6.5 8.7 3.9-1.2 6.5-4.8 6.5-8.7V6L12 3.5Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="m9.2 12.2 2 2 3.8-3.8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function IconAlertTriangle() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M10.7 4.7 3.1 18a1.6 1.6 0 0 0 1.4 2.4h15a1.6 1.6 0 0 0 1.4-2.4L13.3 4.7a1.6 1.6 0 0 0-2.6 0Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M12 9.2v4.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="12" cy="16.3" r="1" fill="currentColor" />
    </svg>
  )
}

function IconEdit() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="m4 20 4.2-1 9.7-9.7a1.6 1.6 0 0 0 0-2.3l-.9-.9a1.6 1.6 0 0 0-2.3 0L5 15.8 4 20Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M13.8 6.2 17.8 10.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  )
}

function IconSearch() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="11" cy="11" r="6" stroke="currentColor" strokeWidth="1.8" />
      <path d="m16 16 4 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  )
}

function IconCode() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="m8 18-6-6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="m16 6 6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function IconBrain() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function App() {
  const userId = GENERIC_USER_ID
  const [profile, setProfile] = useState(DEFAULT_PROFILE)
  const [profileEdit, setProfileEdit] = useState(false)

  const [chatMessages, setChatMessages] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [chatActivity, setChatActivity] = useState('')
  const [expandedMsg, setExpandedMsg] = useState({})

  const hasConversation = useMemo(
    () => chatMessages.some((m) => m.role === 'assistant' || m.role === 'user'),
    [chatMessages],
  )

  const [activePlan, setActivePlan] = useState(null)
  const [selectedDay, setSelectedDay] = useState(null)
  const [lastRequestId, setLastRequestId] = useState(null)

  const [dayReasoningModalOpen, setDayReasoningModalOpen] = useState(false)
  const [dayReasoningModalDay, setDayReasoningModalDay] = useState(null)
  const [dayReasoningModalData, setDayReasoningModalData] = useState(null)
  const [dayReasoningModalLoading, setDayReasoningModalLoading] = useState(false)
  const [dayReasoningModalError, setDayReasoningModalError] = useState('')
  const [dayReasoningModalTab, setDayReasoningModalTab] = useState('krr') // 'krr' | 'symbolic' | 'llm'
  const [defaultExplanationView, setDefaultExplanationView] = useState('none') // 'none' | 'krr' | 'llm'
  const [messageExplanationView, setMessageExplanationView] = useState({}) // idx -> 'krr' | 'llm'
  const [reasoningModalOpen, setReasoningModalOpen] = useState(false)
  const [reasoningModalTab, setReasoningModalTab] = useState('overview') // 'overview' | 'krr' | 'llm'
  const [globalReasoning, setGlobalReasoning] = useState(null)
  const [globalReasoningLoading, setGlobalReasoningLoading] = useState(false)
  const [globalReasoningError, setGlobalReasoningError] = useState('')
  const [toasts, setToasts] = useState([])
  const toastTimersRef = useRef({})

  const [onboardingDraft, setOnboardingDraft] = useState({
    days: 5,
    injuries: [],
    equipment: [],
    intensity: 3,
  })
  const [daysMenuOpen, setDaysMenuOpen] = useState(false)
  const [equipmentMenuOpen, setEquipmentMenuOpen] = useState(false)
  const [equipmentSearch, setEquipmentSearch] = useState('')
  const [injuryMap, setInjuryMap] = useState(DEFAULT_JOINT_MUSCLE_MAP)
  const [equipmentOptions, setEquipmentOptions] = useState(DEFAULT_EQUIPMENT_OPTIONS)

  const chatEndRef = useRef(null)
  const chatInputRef = useRef(null)
  const daySelectRef = useRef(null)
  const equipmentSelectRef = useRef(null)
  const dayTabRefs = useRef({})

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

  useEffect(() => {
    let cancelled = false
    const loadEquipmentOptions = async () => {
      try {
        const res = await fetch(`${API}/equipment-options`)
        if (!res.ok) return
        const data = await res.json()
        if (cancelled) return
        const next = Array.isArray(data?.equipment)
          ? Array.from(new Set(data.equipment.map((x) => String(x || '').trim().toLowerCase()).filter(Boolean)))
          : []
        if (next.length) setEquipmentOptions(next)
      } catch {
        // Keep fallback list.
      }
    }
    loadEquipmentOptions()
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

  const openReasoningModal = async () => {
    setReasoningModalOpen(true)
    setReasoningModalTab('overview')
    setGlobalReasoningError('')
    try {
      setGlobalReasoningLoading(true)
      const res = await fetch(`${API}/global-reasoning?user_id=${encodeURIComponent(userId)}`)
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || 'Failed to load global reasoning')
      }
      const data = await res.json()
      setGlobalReasoning(data)
    } catch (e) {
      setGlobalReasoning(null)
      setGlobalReasoningError(e?.message || 'Unable to load global reasoning. Generate a plan or try again later.')
    } finally {
      setGlobalReasoningLoading(false)
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
    const timer = window.setTimeout(() => {
      if (!chatLoading) chatInputRef.current?.focus()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [chatLoading])

  useEffect(() => {
    if (!daysMenuOpen) return undefined
    const onDocPointerDown = (event) => {
      if (!daySelectRef.current?.contains(event.target)) {
        setDaysMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocPointerDown)
    return () => document.removeEventListener('mousedown', onDocPointerDown)
  }, [daysMenuOpen])

  useEffect(() => {
    if (!equipmentMenuOpen) return undefined
    const onDocPointerDown = (event) => {
      if (!equipmentSelectRef.current?.contains(event.target)) {
        setEquipmentMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocPointerDown)
    return () => document.removeEventListener('mousedown', onDocPointerDown)
  }, [equipmentMenuOpen])

  useEffect(() => {
    if (!profileEdit) {
      setDaysMenuOpen(false)
      setEquipmentMenuOpen(false)
      setEquipmentSearch('')
    }
  }, [profileEdit])

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
        }
        const normalizedIntensity = normalizeIntensityValue(next.intensity, 3)
        const normalizedProfile = {
          ...next,
          intensity: normalizedIntensity,
        }
        setProfile(normalizedProfile)
        setOnboardingDraft({
          days: next.days_per_week ?? 5,
          injuries: next.injury_muscles || [],
          equipment: next.equipment_preferences || [],
          intensity: normalizedIntensity,
        })
      })
      .catch(() => setProfile(DEFAULT_PROFILE))
  }, [userId])

  const dayLabels = useMemo(() => sortedDayLabelsFromPlan(activePlan?.plan || {}), [activePlan])

  const handleDayTabKeyDown = (event, day) => {
    if (!dayLabels.length) return
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()

    const currentIndex = dayLabels.indexOf(day)
    if (currentIndex < 0) return

    const step = event.key === 'ArrowRight' ? 1 : -1
    const nextIndex = (currentIndex + step + dayLabels.length) % dayLabels.length
    const nextDay = dayLabels[nextIndex]

    setSelectedDay(nextDay)
    dayTabRefs.current[nextDay]?.focus()
  }

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

  const normalizedDraftInjuries = useMemo(
    () => Array.from(new Set((onboardingDraft.injuries || []).map((m) => String(m || '').trim().toLowerCase()).filter(Boolean))),
    [onboardingDraft.injuries],
  )
  const normalizedDraftEquipment = useMemo(
    () => Array.from(new Set((onboardingDraft.equipment || []).map((m) => String(m || '').trim().toLowerCase()).filter(Boolean))),
    [onboardingDraft.equipment],
  )

  const selectedInjurySet = useMemo(
    () => new Set(normalizedDraftInjuries),
    [normalizedDraftInjuries],
  )
  const selectedEquipmentSet = useMemo(
    () => new Set(normalizedDraftEquipment),
    [normalizedDraftEquipment],
  )

  const jointOptions = useMemo(
    () => Object.keys(injuryMap || {}),
    [injuryMap],
  )

  const muscleOptions = useMemo(
    () => uniqueMusclesFromJointMap(injuryMap, normalizedDraftInjuries),
    [injuryMap, normalizedDraftInjuries],
  )
  const equipmentChipOptions = useMemo(
    () => Array.from(new Set([...equipmentOptions, ...normalizedDraftEquipment])),
    [equipmentOptions, normalizedDraftEquipment],
  )
  const filteredEquipmentOptions = useMemo(() => {
    const query = String(equipmentSearch || '').trim().toLowerCase()
    if (!query) return equipmentChipOptions
    return equipmentChipOptions.filter((item) => item.includes(query))
  }, [equipmentChipOptions, equipmentSearch])

  const applyServerPlanPayload = (data, preferredDay = null) => {
    if (!data?.plan || typeof data.plan !== 'object') return null
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
    setSelectedDay(resolveDayLabel(labels, preferredDay ?? selectedDay))
    return planPack
  }

  const saveProfile = async (updates, options = {}) => {
    const id = (userId || '').trim()
    if (!id) return null
    const res = await fetch(`${API}/profile`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: id,
        ...updates,
        selected_day: options.selectedDay ?? selectedDay ?? null,
        auto_update_plan: options.autoUpdatePlan ?? true,
      }),
    })
    const requestId = res.headers.get('X-Request-ID')
    if (requestId) setLastRequestId(requestId)
    if (!res.ok) throw new Error(await res.text())
    const payload = await res.json()
    const normalizedIntensity = normalizeIntensityValue(payload?.profile?.intensity, normalizeIntensityValue(updates.intensity, profile.intensity))
    if (payload?.profile && typeof payload.profile === 'object') {
      setProfile({
        ...DEFAULT_PROFILE,
        ...payload.profile,
        intensity: normalizedIntensity,
      })
    } else {
      setProfile((prev) => ({
        ...prev,
        ...updates,
        intensity: normalizeIntensityValue(updates.intensity, prev.intensity),
      }))
    }
    if (payload?.plan_updated && payload?.plan) {
      applyServerPlanPayload(payload, options.selectedDay ?? selectedDay ?? null)
    }
    return payload
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

  const publishPlan = (data, assistantText = null, preferredDay = null) => {
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
    setSelectedDay(resolveDayLabel(labels, preferredDay))

    if (assistantText) {
      setChatMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: assistantText,
        },
      ])
    }

    return planPack
  }

  const generatePlan = async (daysOverride = null, source = 'button', activityHint = '') => {
    const id = (userId || '').trim()
    if (!id) {
      pushToast('error', 'Profile unavailable. Refresh and try again.')
      return
    }

    setChatLoading(true)
    setChatActivity(activityHint || '')

    try {
      const data = await runGeneratePlan(id, daysOverride)
      if (source === 'chat') {
        publishPlan(data, null)
      } else {
        const days = sortedDayLabelsFromPlan(data.plan || {}).length
        publishPlan(data, `Generated ${days}-day themed plan. Use day tabs on the right to inspect and control each day.`)
      }
      return data
    } catch (e) {
      pushToast('error', `Plan generation failed: ${e.message || 'Unknown error'}`)
      return null
    } finally {
      setChatLoading(false)
      setChatActivity('')
    }
  }

  const requestAssistantChat = async (id, msg, planSummaryOverride = null, krrSummaryOverride = null, selectedDayOverride = null, options = {}) => {
    const planSummary = planSummaryOverride ?? (
      activePlan
        ? sortedDayLabelsFromPlan(activePlan.plan)
          .map((d) => `${d}: ${(activePlan.plan[d] || []).map((e) => e.name).join(', ') || 'Rest'}`)
          .join('; ')
        : null
    )

    const krrSummary = krrSummaryOverride ?? (
      activePlan
        ? Object.entries(activePlan.krr_reasoning || {}).slice(0, 2).map(([, v]) => clip(v, 240)).join(' ')
        : null
    )

    const res = await fetch(`${API}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: id,
        message: msg,
        plan_summary: planSummary,
        krr_summary: krrSummary,
        selected_day: selectedDayOverride ?? selectedDay ?? null,
        include_explanation: options.include_explanation ?? false,
        plan_only_response: options.plan_only_response ?? false,
      }),
    })
    const requestId = res.headers.get('X-Request-ID')
    if (requestId) setLastRequestId(requestId)
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    return data
  }

  const sendChat = async (textOverride = null) => {
    const msg = (textOverride ?? chatInput).trim()
    if (!msg) return
    if (!textOverride) setChatInput('')
    setChatMessages((prev) => [...prev, { role: 'user', text: msg }])

    const id = (userId || '').trim() || 'user'
    setChatLoading(true)
    setChatActivity('')
    try {
      const intent = await resolveIntent(id, msg)
      setChatActivity(intent.assistant_reply || '')
      const inferred = intent.profile_updates || {}
      const persistableInferred = toPersistedProfileUpdates(inferred)
      const hasPersistableUpdates = Object.keys(persistableInferred).length > 0

      if (intent.intent === 'generate_plan') {
        if (hasPersistableUpdates) {
          await saveProfile(persistableInferred, { autoUpdatePlan: false })
          setOnboardingDraft((prev) => ({
            ...prev,
            days: persistableInferred.days_per_week ?? prev.days,
            injuries: persistableInferred.injury_muscles ?? prev.injuries,
            equipment: persistableInferred.equipment_preferences ?? prev.equipment,
            intensity: normalizeIntensityValue(persistableInferred.intensity, prev.intensity),
          }))
          pushToast('success', 'Applied detected profile updates before plan generation.')
        }
        setChatActivity(intent.assistant_reply || 'Building your personalized plan...')
        const generated = await runGeneratePlan(id, intent.days_per_week)
        if (generated?.plan) {
          const labels = sortedDayLabelsFromPlan(generated.plan || {})
          const selected = labels[0] || selectedDay || null
          const planSummary = labels
            .map((d) => `${d}: ${(generated.plan[d] || []).map((e) => e.name).join(', ') || 'Rest'}`)
            .join('; ')
          const krrSummary = Object.entries(generated.krr_reasoning || {}).slice(0, 2).map(([, v]) => clip(v, 240)).join(' ')
          setChatActivity('Finalizing your response...')
          let assistantReply = intent.assistant_reply || 'Your plan is ready. I have applied it to your weekly view.'
          let chatPayload = null
          try {
            chatPayload = await requestAssistantChat(
              id,
              msg,
              planSummary,
              krrSummary,
              selected,
              { include_explanation: true, plan_only_response: true },
            )
            assistantReply = chatPayload?.response ?? assistantReply
          } catch {
            // Fall back to deterministic copy while still publishing the generated plan.
          }
          setChatMessages((prev) => [...prev, {
            role: 'assistant',
            text: assistantReply,
            ...(chatPayload?.krr_logic && { krr_logic: chatPayload.krr_logic }),
            ...(chatPayload?.krr_reasoning && { krr_reasoning: chatPayload.krr_reasoning }),
            ...(chatPayload?.llm_explanation && { llm_explanation: chatPayload.llm_explanation }),
            ...(chatPayload?.llm_grounding_confidence && { llm_grounding_confidence: chatPayload.llm_grounding_confidence }),
            ...((chatPayload?.krr_logic || chatPayload?.krr_reasoning || chatPayload?.llm_explanation) && { explanationView: defaultExplanationView }),
          }])
          publishPlan(generated, null)
        }
        return
      }

      if (intent.intent === 'profile_update') {
        const previousPlanSnapshot = activePlan?.plan ? JSON.parse(JSON.stringify(activePlan.plan)) : null
        if (hasPersistableUpdates) {
          setChatActivity('Applying dynamic profile update to your plan...')
          const savePayload = await saveProfile(persistableInferred, {
            autoUpdatePlan: true,
            selectedDay,
          })
          setOnboardingDraft((prev) => ({
            ...prev,
            days: persistableInferred.days_per_week ?? prev.days,
            injuries: persistableInferred.injury_muscles ?? prev.injuries,
            equipment: persistableInferred.equipment_preferences ?? prev.equipment,
            intensity: normalizeIntensityValue(persistableInferred.intensity, prev.intensity),
          }))
          if (savePayload?.plan_updated && savePayload?.update_mode === 'remaining') {
            pushToast('success', 'Profile updated. Remaining days were refreshed automatically.')
          } else if (savePayload?.plan_updated && savePayload?.update_mode === 'full') {
            pushToast('success', 'Profile updated. The weekly plan was regenerated automatically.')
          } else {
            pushToast('success', 'Profile updated.')
          }
          const planSummary = savePayload?.plan
            ? sortedDayLabelsFromPlan(savePayload.plan)
              .map((d) => `${d}: ${(savePayload.plan[d] || []).map((e) => e.name).join(', ') || 'Rest'}`)
              .join('; ')
            : null
          const krrSummary = savePayload?.krr_reasoning
            ? Object.entries(savePayload.krr_reasoning).slice(0, 2).map(([, v]) => clip(v, 240)).join(' ')
            : null
          const changeSummary = savePayload?.plan_updated && previousPlanSnapshot && savePayload?.plan
            ? summarizePlanExerciseChanges(previousPlanSnapshot, savePayload.plan)
            : 'No automatic plan update occurred.'
          const reasonsSummary = summarizeUpdateReasons(persistableInferred)
          const followUpPrompt = [
            msg,
            '',
            'Dynamic update context:',
            `Profile changes applied: ${reasonsSummary}`,
            `Exercise changes:\n${changeSummary}`,
            'Please explain what changed and why, referencing injury/equipment/intensity/day-count constraints when relevant.',
          ].join('\n')
          setChatActivity(savePayload?.plan_updated
            ? 'Dynamic update applied. Explaining what changed...'
            : 'Profile saved. Preparing response...')
          const chatPayload = await requestAssistantChat(
            id,
            followUpPrompt,
            planSummary,
            krrSummary,
            savePayload?.selected_day || selectedDay,
          )
          const replyText = chatPayload?.response ?? ''
          const assistantText = savePayload?.plan_updated ? replyText : (intent.assistant_reply || replyText)
          setChatMessages((prev) => [...prev, {
            role: 'assistant',
            text: assistantText,
            ...(chatPayload?.krr_logic && { krr_logic: chatPayload.krr_logic }),
            ...(chatPayload?.krr_reasoning && { krr_reasoning: chatPayload.krr_reasoning }),
            ...(chatPayload?.llm_explanation && { llm_explanation: chatPayload.llm_explanation }),
            ...(chatPayload?.llm_grounding_confidence && { llm_grounding_confidence: chatPayload.llm_grounding_confidence }),
            ...((chatPayload?.krr_logic || chatPayload?.krr_reasoning || chatPayload?.llm_explanation) && { explanationView: defaultExplanationView }),
          }])
        } else {
          const chatPayload = await requestAssistantChat(id, msg)
          const replyText = chatPayload?.response ?? ''
          const assistantText = intent.assistant_reply || replyText
          setChatMessages((prev) => [...prev, {
            role: 'assistant',
            text: assistantText,
            ...(chatPayload?.krr_logic && { krr_logic: chatPayload.krr_logic }),
            ...(chatPayload?.krr_reasoning && { krr_reasoning: chatPayload.krr_reasoning }),
            ...(chatPayload?.llm_explanation && { llm_explanation: chatPayload.llm_explanation }),
            ...(chatPayload?.llm_grounding_confidence && { llm_grounding_confidence: chatPayload.llm_grounding_confidence }),
            ...((chatPayload?.krr_logic || chatPayload?.krr_reasoning || chatPayload?.llm_explanation) && { explanationView: defaultExplanationView }),
          }])
        }
        return
      }

      const chatPayload = await requestAssistantChat(id, msg)
      const assistantReply = chatPayload?.response ?? 'No response.'
      setChatMessages((prev) => [...prev, {
        role: 'assistant',
        text: assistantReply,
        ...(chatPayload?.krr_logic && { krr_logic: chatPayload.krr_logic }),
        ...(chatPayload?.krr_reasoning && { krr_reasoning: chatPayload.krr_reasoning }),
        ...(chatPayload?.llm_explanation && { llm_explanation: chatPayload.llm_explanation }),
        ...(chatPayload?.llm_grounding_confidence && { llm_grounding_confidence: chatPayload.llm_grounding_confidence }),
        ...((chatPayload?.krr_logic || chatPayload?.krr_reasoning || chatPayload?.llm_explanation) && { explanationView: defaultExplanationView }),
      }])
    } catch (e) {
      pushToast('error', `Chat failed: ${e.message || 'Unknown error'}`)
    } finally {
      setChatLoading(false)
      setChatActivity('')
    }
  }

  const openDayReasoningModal = async (day) => {
    const id = (userId || '').trim()
    if (!id || !day) return

    setDayReasoningModalDay(day)
    setDayReasoningModalOpen(true)
    setDayReasoningModalLoading(true)
    setDayReasoningModalData(null)
    setDayReasoningModalError('')
    setDayReasoningModalTab('krr')

    try {
      const res = await fetch(`${API}/explanation?day=${encodeURIComponent(day)}&user_id=${encodeURIComponent(id)}`)
      if (!res.ok) {
        setDayReasoningModalError('No reasoning is available for this day yet.')
        setDayReasoningModalData(null)
      } else {
        const data = await res.json()
        setDayReasoningModalData(data)
        setDayReasoningModalError('')
      }
    } catch (e) {
      setDayReasoningModalError('No reasoning is available for this day yet.')
      setDayReasoningModalData(null)
    } finally {
      setDayReasoningModalLoading(false)
    }
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

  const toggleEquipmentPreference = (equipment) => {
    const eq = String(equipment || '').trim().toLowerCase()
    if (!eq) return
    setOnboardingDraft((prev) => {
      const current = Array.from(new Set((prev.equipment || []).map((x) => String(x || '').trim().toLowerCase()).filter(Boolean)))
      const exists = current.includes(eq)
      const next = exists ? current.filter((x) => x !== eq) : [...current, eq]
      return { ...prev, equipment: next }
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
      pushToast('error', 'Profile unavailable. Refresh and try again.')
      return false
    }
    setChatLoading(true)
    setChatActivity('Applying dynamic profile update to your plan...')
    const updates = {
      onboarding_complete: true,
      days_per_week: onboardingDraft.days,
      injury_muscles: onboardingDraft.injuries,
      equipment_preferences: onboardingDraft.equipment,
      intensity: normalizeIntensityValue(onboardingDraft.intensity, 3),
    }
    const previousPlanSnapshot = activePlan?.plan ? JSON.parse(JSON.stringify(activePlan.plan)) : null
    try {
      const payload = await saveProfile(updates, { autoUpdatePlan: true, selectedDay })
      pushToast('success', 'Onboarding complete. You can now generate a validated plan.')
      if (payload?.plan_updated && payload?.plan) {
        setChatActivity('Dynamic update applied. Explaining what changed...')
        const planSummary = sortedDayLabelsFromPlan(payload.plan)
          .map((d) => `${d}: ${(payload.plan[d] || []).map((e) => e.name).join(', ') || 'Rest'}`)
          .join('; ')
        const krrSummary = Object.entries(payload.krr_reasoning || {}).slice(0, 2).map(([, v]) => clip(v, 240)).join(' ')
        const changeSummary = previousPlanSnapshot
          ? summarizePlanExerciseChanges(previousPlanSnapshot, payload.plan)
          : 'A new plan was created using the latest profile constraints.'
        const reasonsSummary = summarizeUpdateReasons(updates)
        const prompt = [
          'I updated my training profile and the plan auto-refreshed.',
          `Profile changes applied: ${reasonsSummary}`,
          `Exercise changes:\n${changeSummary}`,
          'Explain what changed and why with a focus on safety constraints.',
        ].join('\n')
        try {
          const chatPayload = await requestAssistantChat(
            id,
            prompt,
            planSummary,
            krrSummary,
            payload?.selected_day || selectedDay,
          )
          if (chatPayload?.response) {
            setChatMessages((prev) => [...prev, {
              role: 'assistant',
              text: chatPayload.response,
              ...(chatPayload?.krr_logic && { krr_logic: chatPayload.krr_logic }),
              ...(chatPayload?.krr_reasoning && { krr_reasoning: chatPayload.krr_reasoning }),
              ...(chatPayload?.llm_explanation && { llm_explanation: chatPayload.llm_explanation }),
              ...((chatPayload?.krr_logic || chatPayload?.krr_reasoning || chatPayload?.llm_explanation) && { explanationView: defaultExplanationView }),
            }])
          }
        } catch {
          // Keep profile save successful even if explanation call fails.
        }
      }
      return true
    } catch (e) {
      pushToast('error', `Save profile failed: ${e.message || 'Unknown error'}`)
      return false
    } finally {
      setChatLoading(false)
      setChatActivity('')
    }
  }

  const safeIntensity = normalizeIntensityValue(profile.intensity, 3)
  const formattedIntensity = String(safeIntensity)
  const injuryList = profile.injury_muscles || []
  const equipmentList = profile.equipment_preferences || []
  const hasInjuries = injuryList.length > 0
  const hasEquipmentPreferences = equipmentList.length > 0

  const toggleProfileEdit = () => {
    if (profileEdit) {
      setOnboardingDraft({
        days: profile.days_per_week ?? 5,
        injuries: profile.injury_muscles || [],
        equipment: profile.equipment_preferences || [],
        intensity: normalizeIntensityValue(profile.intensity, 3),
      })
      setProfileEdit(false)
      return
    }
    setProfileEdit(true)
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
        <div className="section-head training-profile-head">
          <h2 className="panel-title">Training Profile</h2>
        </div>
        <p className="panel-subtitle">Set training days, intensity, injury filters, and equipment preferences.</p>

        <section className="panel-section training-profile-card">

          {!profileEdit ? (
            <div className="training-profile-body">
              <div className="training-profile-metrics">
                <div className="training-metric-item">
                  <div className="training-metric-meta">
                    <span className="training-metric-icon calendar" aria-hidden="true"><IconCalendar /></span>
                    <span className="training-metric-label">Days per week</span>
                  </div>
                  <span className="training-metric-value">{profile.days_per_week}</span>
                </div>
                <div className="training-metric-item">
                  <div className="training-metric-meta">
                    <span className="training-metric-icon flame" aria-hidden="true"><IconFlame /></span>
                    <span className="training-metric-label">Intensity level</span>
                  </div>
                  <span className="training-metric-value">{formattedIntensity}</span>
                </div>

                <div className={`training-status ${hasEquipmentPreferences ? 'info' : 'safe'}`}>
                  <p className="training-status-main">
                    <span className="training-status-meta">
                      <span className="training-metric-icon training-status-icon info" aria-hidden="true">
                        <IconDumbbell />
                      </span>
                      <span className="training-status-title">Equipment preferences</span>
                    </span>
                    <span className="training-status-value safe">
                      {hasEquipmentPreferences ? `${equipmentList.length}` : 'Any'}
                    </span>
                  </p>
                  {hasEquipmentPreferences && (
                    <p className="training-status-detail">
                      Selected: {equipmentList.map((item) => toTitleCase(item)).join(', ')}
                    </p>
                  )}
                </div>

                <div className={`training-status ${hasInjuries ? 'warning' : 'safe'}`}>
                  <p className="training-status-main">
                    <span className="training-status-meta">
                      <span className={`training-metric-icon training-status-icon ${hasInjuries ? 'warning' : 'safe'}`} aria-hidden="true">
                        {hasInjuries ? <IconAlertTriangle /> : <IconShieldCheck />}
                      </span>
                      <span className="training-status-title">Injury filters</span>
                    </span>
                    <span className={`training-status-value ${hasInjuries ? 'warning' : 'safe'}`}>
                      {hasInjuries ? `${injuryList.length}` : 'None'}
                    </span>
                  </p>
                  {hasInjuries && (
                    <p className="training-status-detail training-status-detail-warning">
                      Avoid loading: {injuryList.map((injury) => toTitleCase(injury)).join(', ')}
                    </p>
                  )}
                </div>
              </div>
              
              <div className="training-card-actions">
                <button
                  type="button"
                  className="btn training-edit-btn"
                  onClick={toggleProfileEdit}
                >
                  <span className="training-edit-btn-icon" aria-hidden="true"><IconEdit /></span>
                  Edit
                </button>
              </div>
            </div>
          ) : (
            <div className="wizard-grid">
              <label>Days / Week</label>
              <div className="day-select" ref={daySelectRef}>
                <button
                  type="button"
                  className="day-select-trigger"
                  aria-haspopup="listbox"
                  aria-expanded={daysMenuOpen}
                  onClick={() => setDaysMenuOpen((v) => !v)}
                >
                  <span>{onboardingDraft.days}</span>
                  <span className="day-select-chevron" aria-hidden="true">▾</span>
                </button>
                {daysMenuOpen && (
                  <div className="day-select-menu" role="listbox" aria-label="Days per week">
                    {[2, 3, 4, 5, 6, 7].map((d) => (
                      <button
                        key={d}
                        type="button"
                        role="option"
                        aria-selected={onboardingDraft.days === d}
                        className={`day-select-option ${onboardingDraft.days === d ? 'selected' : ''}`}
                        onClick={() => {
                          setOnboardingDraft((p) => ({ ...p, days: d }))
                          setDaysMenuOpen(false)
                        }}
                      >
                        <span className="day-select-option-check" aria-hidden="true">{onboardingDraft.days === d ? '✓' : ''}</span>
                        <span>{d}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <label>Intensity ({onboardingDraft.intensity})</label>
              <input
                type="range"
                min={1}
                max={5}
                step={1}
                value={onboardingDraft.intensity}
                onChange={(e) => setOnboardingDraft((p) => ({ ...p, intensity: normalizeIntensityValue(e.target.value, p.intensity) }))}
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

              <label>Equipment preferences</label>
              <div className="injury-filter-grid">
                <div className="injury-filter-group">
                  <p className="injury-filter-title">Choose available equipment</p>
                  <div className="equipment-select" ref={equipmentSelectRef}>
                    <button
                      type="button"
                      className="equipment-select-trigger"
                      aria-haspopup="listbox"
                      aria-expanded={equipmentMenuOpen}
                      onClick={() => setEquipmentMenuOpen((open) => !open)}
                    >
                      <span>
                        {normalizedDraftEquipment.length
                          ? `${normalizedDraftEquipment.length} selected`
                          : 'Select equipment'}
                      </span>
                      <span className="day-select-chevron" aria-hidden="true">▾</span>
                    </button>

                    {equipmentMenuOpen && (
                      <div className="equipment-select-menu" role="listbox" aria-label="Equipment preferences">
                        <input
                          type="text"
                          className="equipment-search-input"
                          value={equipmentSearch}
                          placeholder="Search equipment..."
                          onChange={(e) => setEquipmentSearch(e.target.value)}
                        />
                        <div className="equipment-option-list">
                          {filteredEquipmentOptions.length === 0 ? (
                            <p className="equipment-empty">No equipment found.</p>
                          ) : (
                            filteredEquipmentOptions.map((equipment) => (
                              <button
                                key={equipment}
                                type="button"
                                role="option"
                                aria-selected={selectedEquipmentSet.has(equipment)}
                                className={`equipment-option ${selectedEquipmentSet.has(equipment) ? 'selected' : ''}`}
                                onClick={() => toggleEquipmentPreference(equipment)}
                              >
                                <span className="day-select-option-check" aria-hidden="true">
                                  {selectedEquipmentSet.has(equipment) ? '✓' : ''}
                                </span>
                                <span>{toTitleCase(equipment)}</span>
                              </button>
                            ))
                          )}
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="equipment-bubbles">
                    {normalizedDraftEquipment.length === 0 ? (
                      <p className="muted small">No equipment selected (any allowed).</p>
                    ) : (
                      normalizedDraftEquipment.map((equipment) => (
                        <span key={equipment} className="equipment-bubble">
                          <span>{toTitleCase(equipment)}</span>
                          <button
                            type="button"
                            className="equipment-bubble-clear"
                            aria-label={`Remove ${equipment}`}
                            onClick={() => toggleEquipmentPreference(equipment)}
                          >
                            ×
                          </button>
                        </span>
                      ))
                    )}
                  </div>
                  <p className="muted small">Leave empty to allow any equipment. You can also update this by chat.</p>
                </div>
              </div>

              <button
                type="button"
                className="btn btn-primary"
                onClick={async () => {
                  setProfileEdit(false)
                  const ok = await completeOnboarding()
                  if (!ok) setProfileEdit(true)
                }}
              >
                Save profile
              </button>
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
            <button
              type="button"
              className="btn training-edit-btn"
              onClick={openReasoningModal}
            >
              <span className="training-edit-btn-icon" aria-hidden="true">
                <IconBrain />
              </span>
              <span>Show reasoning</span>
            </button>
          </div>
        </div>

        <div className="chat-zone">
          {chatMessages.map((m, idx) => (
            <div key={`${idx}-${m.role}`} className={`msg msg-${m.role}`}>
              {(() => {
                const text = String(m.text || '')
                const hasExplanation = Boolean(m.krr_reasoning || m.llm_explanation || m.krr_logic)
                const explView = messageExplanationView[idx] ?? m.explanationView ?? defaultExplanationView
                const lineBreaks = (text.match(/\n/g) || []).length
                const isLongAssistantReply = m.role === 'assistant' && !hasExplanation && (text.length > 900 || lineBreaks > 14)
                const isExpanded = Boolean(expandedMsg[idx])
                return (
                  <>
                    {m.role !== 'system' && <p className="msg-role">{m.role === 'user' ? '🧑 You' : '🤖 Coach AI'}</p>}
                    {hasExplanation && m.role === 'assistant' ? (
                      <div className="msg-explanation-wrap">
                        <div className={`msg-body markdown-body msg-llm-view ${isLongAssistantReply && !isExpanded ? 'msg-body-clamped' : ''}`}>
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
                        {(m.llm_explanation || m.krr_reasoning || m.krr_logic) && (
                          <>
                            <div className="msg-explanation-toggle-row" role="group" aria-label="Reasoning view">
                              {m.llm_explanation && (
                                <button
                                  type="button"
                                  className={`msg-explanation-btn msg-explanation-btn-llm ${explView === 'llm' ? 'active' : ''}`}
                                  title="View generative AI reasoning (natural language summary)"
                                  aria-expanded={explView === 'llm'}
                                  onClick={() =>
                                    setMessageExplanationView((prev) => ({
                                      ...prev,
                                      [idx]: explView === 'llm' ? 'none' : 'llm',
                                    }))
                                  }
                                >
                                  <span className="msg-explanation-btn-icon" aria-hidden="true"><IconBrain /></span>
                                  {explView === 'llm' ? 'Hide' : 'View'} Generative Reasoning
                                </button>
                              )}
                              {m.krr_reasoning && (
                                <button
                                  type="button"
                                  className={`msg-explanation-btn msg-explanation-btn-krr ${explView === 'krr' ? 'active' : ''}`}
                                  title="View symbolic logic (facts, rules, inference)"
                                  aria-expanded={explView === 'krr'}
                                  onClick={() =>
                                    setMessageExplanationView((prev) => ({
                                      ...prev,
                                      [idx]: explView === 'krr' ? 'none' : 'krr',
                                    }))
                                  }
                                >
                                  <span className="msg-explanation-btn-icon" aria-hidden="true"><IconCode /></span>
                                  {explView === 'krr' ? 'Hide' : 'View'} Symbolic Logic
                                </button>
                              )}
                            </div>
                            {explView === 'llm' && m.llm_explanation && (
                              <div className="msg-body markdown-body msg-llm-view">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                  {m.llm_explanation}
                                </ReactMarkdown>
                              </div>
                            )}
                            {explView === 'krr' && m.krr_reasoning && (
                              <div className="msg-body msg-krr-view">
                                <pre className="krr-structured-block">
                                  {m.krr_reasoning}
                                </pre>
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    ) : (
                      <>
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
                    )}
                  </>
                )
              })()}
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
          <form
            className="chat-input-row"
            onSubmit={(e) => {
              e.preventDefault()
              sendChat()
            }}
          >
            <input
              ref={chatInputRef}
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              className="chat-composer-input"
              placeholder="Ask anything ..."
              disabled={chatLoading}
              autoFocus
              aria-label="Message coach"
            />
            <button type="submit" className="btn btn-primary chat-send-btn" disabled={chatLoading}>Send</button>
          </form>
        </div>

        {DEV_MODE && lastRequestId && <p className="muted small">Request ID: {lastRequestId}</p>}
      </main>

      <aside className="right-panel card weekly-plan-panel">
        {!activePlan ? (
          <>
          <div className="weekly-plan-header">
            <h2 className="panel-title">Weekly Plan View</h2>
            <p className="panel-subtitle">Select a day tab to inspect or explain.</p>
          </div>
          <div className="empty-plan">
            <div className="empty-plan-graphic" aria-hidden>
              <span />
              <span />
              <span />
            </div>
            <p className="empty-plan-title">No plan generated yet</p>
            <p className="muted">Ask in chat: “Give me my plan” to populate the weekly schedule.</p>
          </div>
          </>
        ) : (
          <>
            <div className="weekly-plan-header weekly-plan-header-attached">
              <h2 className="panel-title">Weekly Plan View</h2>
              <p className="panel-subtitle">Select a day tab to inspect or explain.</p>
            <div className="tabs-shell">
              <div className="tabs" role="tablist" aria-label="Weekly day tabs">
              {dayLabels.map((d) => (
                <button
                  key={d}
                  type="button"
                  role="tab"
                  aria-selected={selectedDay === d}
                  tabIndex={selectedDay === d ? 0 : -1}
                  className={`tab ${selectedDay === d ? 'tab-active' : ''}`}
                  onClick={() => setSelectedDay(d)}
                  onKeyDown={(event) => handleDayTabKeyDown(event, d)}
                  ref={(node) => {
                    if (node) dayTabRefs.current[d] = node
                    else delete dayTabRefs.current[d]
                  }}
                >
                  <span className="tab-day-label">{formatDayLabel(d)}</span>
                </button>
              ))}
              </div>
            </div>
            </div>

            <div className="day-view day-view-attached animate-in">
              <div className="day-title-row">
                <h3>{(activePlan.day_themes && activePlan.day_themes[selectedDay]) || formatDayLabel(selectedDay)}</h3>
              </div>
              <div className="day-insights">
                <div className="day-focus-box" role="group" aria-label="Day focus">
                  <span className="day-focus-label">Focus</span>
                  <span className="day-focus-value">
                    {selectedDayInsights.focusMuscles.length === 0
                      ? 'Full Body'
                      : selectedDayInsights.focusMuscles.map((muscle) => toTitleCase(muscle)).join(' • ')}
                  </span>
                </div>
                <div className="day-metrics-grid" role="group" aria-label="Day summary metrics">
                  <div className="day-metric-card">
                    <span className="day-metric-label">Volume</span>
                    <span className="day-metric-value">{selectedDayInsights.totalSets} sets</span>
                  </div>
                  <div className="day-metric-card">
                    <span className="day-metric-label">Recovery</span>
                    <span className="day-metric-value">{selectedDayInsights.recoveryLoad}</span>
                  </div>
                  <div className="day-metric-card">
                    <span className="day-metric-label">Time</span>
                    <span className="day-metric-value">{selectedDayInsights.estMinutes} min</span>
                  </div>
                </div>
              </div>

              <div className="exercise-table" role="table" aria-label="Day exercise prescriptions">
                <div className="exercise-table-head" role="row">
                  <span className="exercise-head-cell">Exercise</span>
                  <span className="exercise-head-cell exercise-head-cell-rx">Prescription</span>
                </div>
                <ol className="exercise-list">
                {selectedDayExercises.length === 0 && (
                  <li className="exercise-item">
                    <p className="exercise-name muted">Rest Day</p>
                    <p className="exercise-prescription muted">—</p>
                  </li>
                )}
                {selectedDayExercises.map((ex, idx) => (
                  <li key={`${ex.name}-${idx}`} className="exercise-item">
                    <p className="exercise-name">{toTitleCase(ex.name)}</p>
                    <p className="exercise-prescription">{ex.sets} × {ex.reps}</p>
                  </li>
                ))}
                </ol>
              </div>

              <div className="training-card-actions day-actions">
                <button type="button" className="btn training-edit-btn" onClick={() => openDayReasoningModal(selectedDay)}>
                  <span className="training-edit-btn-icon" aria-hidden="true"><IconSearch /></span>
                  <span>Explain Day</span>
                </button>
              </div>
            </div>
          </>
        )}
      </aside>

      {dayReasoningModalOpen && (
        <>
          <div
            className="drawer-backdrop"
            aria-hidden
            onClick={() => setDayReasoningModalOpen(false)}
          />
          <div
            className="reasoning-modal card"
            role="dialog"
            aria-modal="true"
            aria-labelledby="day-reasoning-modal-title"
          >
            <header className="reasoning-modal-header">
              <h3 id="day-reasoning-modal-title">Reasoning for {formatDayLabel(dayReasoningModalDay || '')}</h3>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setDayReasoningModalOpen(false)}
              >
                Close
              </button>
            </header>

            <div className="reasoning-modal-tabs" role="tablist" aria-label="Day reasoning sections">
              {[
                ['krr', 'KRR Logic'],
                ['symbolic', 'Symbolic logic'],
                ['llm', 'LLM Logic'],
              ].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={dayReasoningModalTab === key}
                  className={`reasoning-tab ${dayReasoningModalTab === key ? 'reasoning-tab-active' : ''}`}
                  onClick={() => setDayReasoningModalTab(key)}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="reasoning-modal-body">
              {dayReasoningModalLoading && <p className="muted small">Loading reasoning…</p>}
              {!dayReasoningModalLoading && dayReasoningModalError && (
                <p className="error-text">{dayReasoningModalError}</p>
              )}

              {!dayReasoningModalLoading && !dayReasoningModalError && dayReasoningModalData && (
                <>
                  {dayReasoningModalTab === 'krr' && (
                    <div className="reasoning-section">
                      <h4>KRR Logic (this day)</h4>
                      {dayReasoningModalData.krr_day_text ? (
                        <pre className="krr-structured-block">
                          {dayReasoningModalData.krr_day_text}
                        </pre>
                      ) : (
                        <p className="muted small">
                          No KRR logic is available for this day yet. This may be a rest day or no reasoning has been computed.
                        </p>
                      )}
                    </div>
                  )}

                  {dayReasoningModalTab === 'symbolic' && (
                    <div className="reasoning-section">
                      <h4>Symbolic logic summary</h4>
                      {Array.isArray(dayReasoningModalData.symbolic_summary) &&
                      dayReasoningModalData.symbolic_summary.length > 0 ? (
                        <ul className="reasoning-list">
                          {dayReasoningModalData.symbolic_summary.map((line) => (
                            <li key={line}>{line}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="muted small">
                          No symbolic summary is available for this day yet.
                        </p>
                      )}
                    </div>
                  )}

                  {dayReasoningModalTab === 'llm' && (
                    <div className="reasoning-section">
                      <h4>LLM Logic (this day)</h4>
                      {dayReasoningModalData.llm_reasoning || dayReasoningModalData.llm_explanation ? (
                        <div className="reason-list-wrap">
                          <ul className="reasoning-list">
                            {String(dayReasoningModalData.llm_reasoning || dayReasoningModalData.llm_explanation || '')
                              .replace(/\r\n/g, '\n')
                              .split(/\n+/)
                              .map((chunk) => chunk.replace(/^-\s*/, '').trim())
                              .filter(Boolean)
                              .slice(0, 6)
                              .map((text, idx) => (
                                <li key={idx}>{text}</li>
                              ))}
                          </ul>
                        </div>
                      ) : (
                        <p className="muted small">
                          No LLM reasoning is available for this day yet.
                        </p>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </>
      )}

      {reasoningModalOpen && (
        <>
          <div
            className="drawer-backdrop"
            aria-hidden
            onClick={() => setReasoningModalOpen(false)}
          />
          <div
            className="reasoning-modal card"
            role="dialog"
            aria-modal="true"
            aria-labelledby="reasoning-modal-title"
          >
            <header className="reasoning-modal-header">
              <h3 id="reasoning-modal-title">How this planner reasons</h3>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setReasoningModalOpen(false)}
              >
                Close
              </button>
            </header>

            <div className="reasoning-modal-tabs" role="tablist" aria-label="Reasoning sections">
              {[
                ['overview', 'Overview'],
                ['krr', 'KRR Logic'],
                ['llm', 'LLM Logic'],
              ].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={reasoningModalTab === key}
                  className={`reasoning-tab ${reasoningModalTab === key ? 'reasoning-tab-active' : ''}`}
                  onClick={() => setReasoningModalTab(key)}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="reasoning-modal-body">
              {globalReasoningLoading && <p className="muted small">Loading reasoning…</p>}
              {!globalReasoningLoading && globalReasoningError && (
                <p className="error-text">{globalReasoningError}</p>
              )}

              {!globalReasoningLoading && !globalReasoningError && globalReasoning && (
                <>
                  {reasoningModalTab === 'overview' && (
                    <div className="reasoning-section">
                      <p className="muted small">
                        This summarizes how your <strong>current</strong> profile and stored plan are interpreted.
                      </p>
                      <ul className="reasoning-list">
                        <li>
                          <strong>Profile</strong>:{' '}
                          {globalReasoning.profile
                            ? `${globalReasoning.profile.days_per_week} days/week, intensity ${globalReasoning.profile.intensity}, `
                              + `injuries ${(globalReasoning.profile.injury_muscles || []).length}, `
                              + `equipment ${(globalReasoning.profile.equipment_preferences || []).length}.`
                            : 'No profile information available.'}
                        </li>
                        <li>
                          <strong>Plan</strong>:{' '}
                          {globalReasoning.has_plan
                            ? `${(globalReasoning.plan_days || []).length} day(s) currently stored.`
                            : 'No stored plan yet. Generate a plan to see global reasoning.'}
                        </li>
                      </ul>
                    </div>
                  )}

                  {reasoningModalTab === 'krr' && (
                    <div className="reasoning-section">
                      <h4>Global KRR Logic</h4>
                      {globalReasoning.krr_global_text ? (
                        <pre className="krr-structured-block">
                          {globalReasoning.krr_global_text}
                        </pre>
                      ) : (
                        <p className="muted small">
                          No KRR logic is available yet. Generate a plan to compute the knowledge-base view.
                        </p>
                      )}
                    </div>
                  )}

                  {reasoningModalTab === 'llm' && (
                    <div className="reasoning-section">
                      <h4>LLM Logic (global)</h4>
                      {globalReasoning.llm_reasoning ? (
                        <div className="reason-list-wrap">
                          <ul className="reasoning-list">
                            {String(globalReasoning.llm_reasoning || '')
                              .replace(/\r\n/g, '\n')
                              .split(/\n+/)
                              .map((chunk) => chunk.replace(/^-\s*/, '').trim())
                              .filter(Boolean)
                              .slice(0, 6)
                              .map((text, idx) => (
                                <li key={idx}>{text}</li>
                              ))}
                          </ul>
                        </div>
                      ) : (
                        <p className="muted small">
                          No LLM reasoning has been computed yet for the current constraints.
                        </p>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
