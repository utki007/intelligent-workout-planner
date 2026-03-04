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

const DEFAULT_PROFILE = {
  injury_muscles: [],
  intensity: 3,
  onboarding_complete: false,
  days_per_week: 5,
}

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
  for (const key of ['injury_muscles', 'days_per_week', 'intensity']) {
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
    days: 5,
    injuries: [],
    intensity: 3,
  })
  const [daysMenuOpen, setDaysMenuOpen] = useState(false)
  const [injuryMap, setInjuryMap] = useState(DEFAULT_JOINT_MUSCLE_MAP)

  const chatEndRef = useRef(null)
  const chatInputRef = useRef(null)
  const daySelectRef = useRef(null)

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
    if (!profileEdit) setDaysMenuOpen(false)
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
          intensity: normalizedIntensity,
        })
      })
      .catch(() => setProfile(DEFAULT_PROFILE))
  }, [userId])

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

  const statusText = chatLoading ? (chatActivity || '') : ''

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

  const publishPlan = (data, assistantText = null) => {
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

  const requestAssistantChat = async (id, msg, planSummaryOverride = null, krrSummaryOverride = null, selectedDayOverride = null) => {
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
      }),
    })
    const requestId = res.headers.get('X-Request-ID')
    if (requestId) setLastRequestId(requestId)
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    return data.response || 'No response.'
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
          await saveProfile(persistableInferred)
          setOnboardingDraft((prev) => ({
            ...prev,
            days: persistableInferred.days_per_week ?? prev.days,
            injuries: persistableInferred.injury_muscles ?? prev.injuries,
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
          try {
            assistantReply = await requestAssistantChat(id, msg, planSummary, krrSummary, selected)
          } catch {
            // Fall back to deterministic copy while still publishing the generated plan.
          }
          setChatMessages((prev) => [...prev, {
            role: 'assistant',
            text: assistantReply,
          }])
          publishPlan(generated, null)
        }
        return
      }

      if (intent.intent === 'profile_update') {
        const fallbackReply = await requestAssistantChat(id, msg)
        const assistantText = intent.assistant_reply || fallbackReply
        if (hasPersistableUpdates) {
          await saveProfile(persistableInferred)
          setOnboardingDraft((prev) => ({
            ...prev,
            days: persistableInferred.days_per_week ?? prev.days,
            injuries: persistableInferred.injury_muscles ?? prev.injuries,
            intensity: normalizeIntensityValue(persistableInferred.intensity, prev.intensity),
          }))
          pushToast('success', 'Profile updated.')
          setChatMessages((prev) => [...prev, {
            role: 'assistant',
            text: assistantText,
          }])
        } else {
          setChatMessages((prev) => [...prev, {
            role: 'assistant',
            text: assistantText,
          }])
        }
        return
      }

      const assistantReply = await requestAssistantChat(id, msg)
      setChatMessages((prev) => [...prev, { role: 'assistant', text: assistantReply }])
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
      pushToast('error', 'Profile unavailable. Refresh and try again.')
      return
    }
    await saveProfile({
      onboarding_complete: true,
      days_per_week: onboardingDraft.days,
      injury_muscles: onboardingDraft.injuries,
      intensity: normalizeIntensityValue(onboardingDraft.intensity, 3),
    })
    pushToast('success', 'Onboarding complete. You can now generate a validated plan.')
  }

  const safeIntensity = normalizeIntensityValue(profile.intensity, 3)
  const formattedIntensity = String(safeIntensity)
  const injuryList = profile.injury_muscles || []
  const hasInjuries = injuryList.length > 0

  const toggleProfileEdit = () => {
    if (profileEdit) {
      setOnboardingDraft({
        days: profile.days_per_week ?? 5,
        injuries: profile.injury_muscles || [],
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
        <p className="panel-subtitle">Set training days, intensity, and injury filters.</p>

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
                  <span className="training-metric-value">{formattedIntensity} / 5</span>
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

              <button
                type="button"
                className="btn btn-primary"
                onClick={async () => {
                  await completeOnboarding()
                  setProfileEdit(false)
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
          </div>
        </div>

          {Boolean(statusText) && (
            <div className="status-strip">
              <span className="pulse-dot" />
              <span>{statusText}</span>
            </div>
          )}

        <div className="chat-zone">
          {chatMessages.map((m, idx) => (
            <div key={`${idx}-${m.role}`} className={`msg msg-${m.role}`}>
              {(() => {
                const text = String(m.text || '')
                const lineBreaks = (text.match(/\n/g) || []).length
                const isLongAssistantReply = m.role === 'assistant' && (text.length > 900 || lineBreaks > 14)
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
            <p className="muted">Ask in chat: “Give me my plan” to populate the weekly schedule.</p>
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

      {drawerOpen && (
        <div
          className="drawer-backdrop"
          onClick={() => {
            if (drawerOpen) setDrawerOpen(false)
          }}
          aria-hidden
        />
      )}
    </div>
  )
}
