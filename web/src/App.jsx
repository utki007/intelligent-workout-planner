import { useEffect, useMemo, useRef, useState } from 'react'

const API = '/api'
const DEV_MODE = import.meta.env.DEV
const ATHLETE_STORAGE_KEY = 'workout_athlete_profiles'
const ATHLETE_ACTIVE_KEY = 'workout_active_athlete_id'

const MUSCLE_OPTIONS = [
  'chest', 'pectorals', 'back', 'lats', 'upper back', 'traps', 'shoulders', 'delts', 'arms', 'biceps', 'triceps',
  'legs', 'quads', 'hamstrings', 'glutes', 'calves', 'abs', 'forearms', 'adductors', 'abductors',
]

const INJURY_INPUT_OPTIONS = [
  ...MUSCLE_OPTIONS,
  'knee', 'hip', 'ankle', 'shoulder', 'elbow', 'wrist', 'wrists',
  'neck', 'lower back', 'upper back', 'lower_back', 'upper_back',
  'hamstring', 'abdominal', 'heart', 'lungs',
]

const DEFAULT_PROFILE = {
  injury_muscles: [],
  intensity: 3,
  onboarding_complete: false,
  fitness_goals: '',
  days_per_week: 5,
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

function makeAthleteId(name) {
  const base = String(name || 'athlete')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 24) || 'athlete'
  const suffix = Math.random().toString(36).slice(2, 6)
  return `athlete_${base}_${suffix}`
}

function escapeRegex(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function summarizeProfileUpdates(updates) {
  const parts = []
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

export default function App() {
  const [athletes, setAthletes] = useState([])
  const [activeAthleteId, setActiveAthleteId] = useState('')
  const [newAthleteOpen, setNewAthleteOpen] = useState(false)
  const [newAthleteName, setNewAthleteName] = useState('')
  const [renameAthleteOpen, setRenameAthleteOpen] = useState(false)
  const [renameAthleteName, setRenameAthleteName] = useState('')
  const [athleteMenuOpen, setAthleteMenuOpen] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [profileUpdateModal, setProfileUpdateModal] = useState({
    open: false,
    messageId: null,
    updates: null,
    summary: [],
    prompt: '',
  })
  const [userId, setUserId] = useState('')
  const [profile, setProfile] = useState(DEFAULT_PROFILE)
  const [profileEdit, setProfileEdit] = useState(false)

  const [chatMessages, setChatMessages] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [collapsedPlanMsg, setCollapsedPlanMsg] = useState({})

  const hasConversation = useMemo(
    () => chatMessages.some((m) => m.role === 'assistant' || m.role === 'user'),
    [chatMessages],
  )

  const [activePlan, setActivePlan] = useState(null)
  const [selectedDay, setSelectedDay] = useState(null)
  const [showHistory, setShowHistory] = useState(false)
  const [savePreviousWeeks, setSavePreviousWeeks] = useState(true)
  const [planHistory, setPlanHistory] = useState([])
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
  const [safetyPreview, setSafetyPreview] = useState({
    loading: false,
    error: '',
    requested: [],
    forbidden: [],
  })

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
    try {
      const stored = localStorage.getItem('workout_plan_history')
      if (stored) setPlanHistory(JSON.parse(stored))
    } catch {
      setPlanHistory([])
    }
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem('workout_plan_history', JSON.stringify(planHistory.slice(0, 12)))
    } catch {
      // noop
    }
  }, [planHistory])

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
    appendSystem(`Created athlete profile: ${name}`)
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
    appendSystem(`Renamed athlete to: ${name}`)
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
    appendSystem(`Deleted athlete profile: ${target?.name || id}`)
    pushToast('success', `Deleted ${target?.name || 'athlete'} and switched profile.`)
  }

  const dayLabels = useMemo(() => sortedDayLabelsFromPlan(activePlan?.plan || {}), [activePlan])

  const injuryImpact = useMemo(() => {
    const injuries = Array.from(new Set((profile.injury_muscles || [])
      .map((m) => String(m || '').trim().toLowerCase())
      .filter(Boolean)))
    const blocked = Array.from(new Set((safetyPreview.forbidden || [])
      .map((m) => String(m || '').trim().toLowerCase())
      .filter(Boolean)))
    const restDays = Object.values(activePlan?.plan || {}).filter((dayExercises) => (dayExercises || []).length === 0).length
    return {
      injuries,
      blocked,
      restDays,
    }
  }, [profile.injury_muscles, safetyPreview.forbidden, activePlan])

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

  const quickStartSuggestions = useMemo(() => {
    const injuries = (profile.injury_muscles || []).filter(Boolean)
    const athleteName = activeAthlete?.name || 'my athlete'

    if (!profile.onboarding_complete) {
      return [
        { key: 'setup', label: 'Finish profile setup', action: 'setup_profile' },
        { key: 'build3', label: 'Build a 3-day split', action: 'chat', prompt: 'Plan a 3-day split' },
        { key: 'progress', label: 'Review training progress', action: 'chat', prompt: 'Show my progress' },
      ]
    }

    if (!activePlan) {
      return [
        {
          key: 'week-plan',
          label: `Generate ${profile.days_per_week || 5}-day plan`,
          action: 'generate_profile_days',
        },
        injuries.length > 0
          ? {
              key: 'safe-plan',
              label: `Plan around ${injuries.slice(0, 2).join(', ')} injury`,
              action: 'chat',
              prompt: `Generate my plan around these injuries: ${injuries.join(', ')}`,
            }
          : {
              key: 'build3',
              label: 'Build a 3-day split',
              action: 'chat',
              prompt: 'Plan a 3-day split',
            },
        { key: 'athlete-progress', label: `Review ${athleteName} progress`, action: 'chat', prompt: 'Show my progress' },
      ]
    }

    return [
      { key: 'regen-day', label: selectedDay ? `Regenerate ${formatDayLabel(selectedDay)}` : 'Regenerate selected day', action: 'regen_selected_day' },
      { key: 'explain-day', label: selectedDay ? `Explain ${formatDayLabel(selectedDay)}` : 'Explain selected day', action: 'explain_selected_day' },
      { key: 'progress', label: `Review ${athleteName} progress`, action: 'chat', prompt: 'Show my progress' },
    ]
  }, [profile, activeAthlete, activePlan, selectedDay])

  const previewInjuries = useMemo(() => {
    const source = profileEdit ? onboardingDraft.injuries : profile.injury_muscles
    return Array.from(new Set((source || [])
      .map((m) => String(m || '').trim().toLowerCase())
      .filter(Boolean)))
  }, [profileEdit, onboardingDraft.injuries, profile.injury_muscles])

  const refreshSafetyPreview = async (injuryInputs) => {
    const requested = Array.from(new Set((injuryInputs || [])
      .map((m) => String(m || '').trim().toLowerCase())
      .filter(Boolean)))

    if (requested.length === 0) {
      setSafetyPreview({ loading: false, error: '', requested: [], forbidden: [] })
      return
    }

    setSafetyPreview((prev) => ({ ...prev, loading: true, error: '', requested }))
    try {
      const res = await fetch(`${API}/injury/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ injury_muscles: requested }),
      })
      if (!res.ok) throw new Error('Unable to resolve injury mapping')
      const data = await res.json()
      setSafetyPreview({
        loading: false,
        error: '',
        requested,
        forbidden: Array.isArray(data?.forbidden_muscles) ? data.forbidden_muscles : [],
      })
    } catch {
      setSafetyPreview((prev) => ({ ...prev, loading: false, error: 'Preview unavailable right now.' }))
    }
  }

  useEffect(() => {
    refreshSafetyPreview(previewInjuries)
  }, [previewInjuries])

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
      if (!res.ok) return { intent: 'chat', days_per_week: null, assistant_reply: null }
      const data = await res.json()
      return {
        intent: data.intent || 'chat',
        days_per_week: Number.isInteger(data.days_per_week) ? data.days_per_week : null,
        assistant_reply: typeof data.assistant_reply === 'string' ? data.assistant_reply : null,
      }
    } catch {
      return { intent: 'chat', days_per_week: null, assistant_reply: null }
    }
  }

  const inferProfileUpdatesFromMessage = (message) => {
    const text = String(message || '').toLowerCase()
    const updates = {}
    const normalizeToken = (value) => String(value || '').trim().toLowerCase().replace(/\s+/g, ' ')

    const injurySignals = /\b(injured|injury|hurt|pain|strained|strain|pulled|tear|tore|sore)\b/.test(text)
    const fullRecoverySignals = /\b(no injuries|not injured|fully recovered|completely recovered)\b/.test(text)
    const recoverySignals = /\b(recovered|healed)\b/.test(text)
    if (fullRecoverySignals) {
      updates.injury_muscles = []
    } else if (recoverySignals) {
      const healedMentions = INJURY_INPUT_OPTIONS.filter((muscle) => {
        const expr = new RegExp(`\\b${escapeRegex(muscle.toLowerCase())}\\b`)
        return expr.test(text)
      })

      const recoveryPatterns = [
        /(?:healed|recovered)\s+(?:my\s+)?([a-z_ ]{3,30})/g,
        /([a-z_ ]{3,30})\s+(?:is\s+)?(?:healed|recovered)/g,
      ]
      for (const pattern of recoveryPatterns) {
        let m = pattern.exec(text)
        while (m) {
          const token = normalizeToken(m[1])
          if (token) healedMentions.push(token)
          m = pattern.exec(text)
        }
      }

      if (healedMentions.length) {
        const healedSet = new Set(healedMentions.map(normalizeToken))
        const currentInjuries = profile.injury_muscles || []
        updates.injury_muscles = currentInjuries.filter((entry) => !healedSet.has(normalizeToken(entry)))
      }
    } else if (injurySignals) {
      const mentioned = INJURY_INPUT_OPTIONS.filter((muscle) => {
        const expr = new RegExp(`\\b${escapeRegex(muscle.toLowerCase())}\\b`)
        return expr.test(text)
      })

      // Extra capture for patterns like "injured my knee" / "pain in lower back"
      const phrasePatterns = [
        /injur(?:ed|y)?\s+(?:my\s+)?([a-z_ ]{3,30})/g,
        /pain\s+(?:in|on)\s+(?:my\s+)?([a-z_ ]{3,30})/g,
      ]
      for (const pattern of phrasePatterns) {
        let m = pattern.exec(text)
        while (m) {
          const token = String(m[1] || '').trim().replace(/\s+/g, ' ')
          if (token) mentioned.push(token)
          m = pattern.exec(text)
        }
      }

      if (mentioned.length) {
        updates.injury_muscles = Array.from(new Set([...(profile.injury_muscles || []), ...mentioned]))
      }
    }

    const daysMatch = text.match(/\b([2-7])\s*[- ]?day(s)?\b/)
    if (daysMatch) {
      updates.days_per_week = Number(daysMatch[1])
    }

    const intensityMatch = text.match(/\bintensity\b[^\d]*([1-5](?:\.5)?)\b/)
    if (intensityMatch) {
      updates.intensity = Number(intensityMatch[1])
    }

    const goalMatch = text.match(/\b(goals?|goal)\s*(are|is|to)?\s*[:\-]?\s*(.+)$/)
    if (goalMatch && goalMatch[3]) {
      const goals = goalMatch[3].trim()
      if (goals.length >= 4) updates.fitness_goals = goals
    }

    return updates
  }

  const applyDetectedProfileUpdate = async (messageId, updates) => {
    if (!updates || Object.keys(updates).length === 0) return
    await saveProfile(updates)
    setOnboardingDraft((prev) => ({
      ...prev,
      goals: updates.fitness_goals ?? prev.goals,
      days: updates.days_per_week ?? prev.days,
      injuries: updates.injury_muscles ?? prev.injuries,
      intensity: updates.intensity ?? prev.intensity,
    }))
    setChatMessages((prev) => prev.map((m) => (
      m.id === messageId
        ? { ...m, pendingProfileStatus: 'applied' }
        : m
    )))
    appendSystem('Profile updated and saved.')
    pushToast('success', 'Profile card updated from confirmed changes.')
  }

  const discardDetectedProfileUpdate = (messageId) => {
    setChatMessages((prev) => prev.map((m) => (
      m.id === messageId
        ? { ...m, pendingProfileStatus: 'discarded' }
        : m
    )))
    appendSystem('Detected profile update discarded.')
  }

  const closeProfileUpdateModal = () => {
    setProfileUpdateModal({
      open: false,
      messageId: null,
      updates: null,
      summary: [],
      prompt: '',
    })
  }

  const confirmProfileUpdateFromModal = async () => {
    if (!profileUpdateModal.messageId || !profileUpdateModal.updates) {
      closeProfileUpdateModal()
      return
    }
    await applyDetectedProfileUpdate(profileUpdateModal.messageId, profileUpdateModal.updates)
    closeProfileUpdateModal()
  }

  const discardProfileUpdateFromModal = () => {
    if (profileUpdateModal.messageId) {
      discardDetectedProfileUpdate(profileUpdateModal.messageId)
    }
    closeProfileUpdateModal()
  }

  const appendSystem = (text) => {
    const nextText = String(text || '').trim()
    if (!nextText) return
    setChatMessages((prev) => {
      const last = prev[prev.length - 1]
      if (last?.role === 'system' && String(last.text || '').trim().toLowerCase() === nextText.toLowerCase()) {
        return prev
      }
      return [...prev, { role: 'system', text: nextText }]
    })
  }

  const appendAssistant = (text) => {
    const nextText = String(text || '').trim()
    if (!nextText) return
    setChatMessages((prev) => [...prev, { role: 'assistant', text: nextText }])
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

    if (savePreviousWeeks) {
      setPlanHistory((prev) => [
        {
          id: `${Date.now()}`,
          createdAt: planPack.createdAt,
          userId: (userId || 'user').trim(),
          days: labels.length,
          plan: planPack.plan,
        },
        ...prev,
      ].slice(0, 12))
    }
  }

  const generatePlan = async (daysOverride = null, source = 'button', options = {}) => {
    const id = (userId || '').trim()
    const quiet = Boolean(options?.quiet)
    if (!id) {
      if (source === 'chat') appendAssistant('Select or create an athlete profile first, then I can generate your plan.')
      else appendSystem('Select or create an athlete profile first.')
      return
    }

    if (!profile.onboarding_complete) {
      if (source === 'chat') appendAssistant('Complete onboarding first so I can personalize your plan safely.')
      else appendSystem('Complete the onboarding wizard in the left panel first.')
      return
    }

    setChatLoading(true)
    if (!quiet) {
      if (source === 'chat') appendAssistant('On it — generating your plan with injury and recovery constraints.')
      else appendSystem('Analyzing soreness and injury constraints…')
    }

    try {
      const data = await runGeneratePlan(id, daysOverride)
      const days = sortedDayLabelsFromPlan(data.plan || {}).length
      const text = source === 'chat'
        ? `Done — I built a ${days}-day plan that matches your profile and safety rules.`
        : `Generated ${days}-day themed plan. Use day tabs on the right to inspect and control each day.`
      publishPlan(data, text)
    } catch (e) {
      if (source === 'chat') appendAssistant(`I couldn't generate the plan just now: ${e.message || 'Unknown error'}`)
      else appendSystem(`Plan generation failed: ${e.message || 'Unknown error'}`)
    } finally {
      setChatLoading(false)
    }
  }

  const sendChat = async (textOverride = null) => {
    const msg = (textOverride ?? chatInput).trim()
    if (!msg) return
    if (!textOverride) setChatInput('')
    setChatMessages((prev) => [...prev, { role: 'user', text: msg }])

    const id = (userId || '').trim() || 'user'
    setChatLoading(true)
    try {
      const intent = await resolveIntent(id, msg)

      if (intent.intent === 'generate_plan') {
        if (intent.assistant_reply) appendAssistant(intent.assistant_reply)
        await generatePlan(intent.days_per_week, 'chat', { quiet: true })
        return
      }

      if (intent.intent === 'profile_update') {
        const inferred = inferProfileUpdatesFromMessage(msg)
        if (Object.keys(inferred).length > 0) {
          const detectedSummary = summarizeProfileUpdates(inferred)
          const pendingId = `pending-update-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
          const promptText = intent.assistant_reply || 'Detected profile updates. Review and confirm in the modal.'
          setChatMessages((prev) => [...prev, {
            id: pendingId,
            role: 'system',
            text: promptText,
            pendingProfileUpdate: inferred,
            pendingProfileSummary: detectedSummary,
            pendingProfileStatus: 'pending',
          }])
          setProfileUpdateModal({
            open: true,
            messageId: pendingId,
            updates: inferred,
            summary: detectedSummary,
            prompt: promptText,
          })
        } else {
          appendSystem(intent.assistant_reply || 'Profile update detected. Use Edit in the left panel to apply exact fields.')
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

      const res = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: id, message: msg, plan_summary: planSummary, krr_summary: krrSummary }),
      })
      const requestId = res.headers.get('X-Request-ID')
      if (requestId) setLastRequestId(requestId)
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setChatMessages((prev) => [...prev, { role: 'assistant', text: data.response || 'No response.' }])
    } catch (e) {
      appendSystem(`Chat failed: ${e.message || 'Unknown error'}`)
    } finally {
      setChatLoading(false)
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
      appendSystem(payload.message)
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
      appendSystem(`Regenerating ${selectedDay} with current constraints…`)
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
      appendSystem(`Regeneration failed: ${e.message || 'Unknown error'}`)
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
      appendSystem(`Reduce volume failed: ${e.message || 'Unknown error'}`)
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
      appendSystem(`Swap exercise failed: ${e.message || 'Unknown error'}`)
      pushToast('error', `Swap exercise failed: ${e.message || 'Unknown error'}`)
    } finally {
      setChatLoading(false)
    }
  }

  const toggleInjuryDraft = (muscle) => {
    setOnboardingDraft((prev) => {
      const exists = prev.injuries.includes(muscle)
      return { ...prev, injuries: exists ? prev.injuries.filter((m) => m !== muscle) : [...prev.injuries, muscle] }
    })
  }

  const completeOnboarding = async () => {
    const id = (userId || '').trim()
    if (!id) {
      appendSystem('Select or create an athlete profile before saving onboarding.')
      return
    }
    await saveProfile({
      onboarding_complete: true,
      fitness_goals: onboardingDraft.goals,
      days_per_week: onboardingDraft.days,
      injury_muscles: onboardingDraft.injuries,
      intensity: onboardingDraft.intensity,
    })
    appendSystem('Onboarding complete. You can now generate a validated plan.')
  }

  const handleQuickSuggestion = async (item) => {
    if (!item) return
    if (item.action === 'setup_profile') {
      setProfileEdit(true)
      appendSystem('Profile editor opened. Complete setup to personalize suggestions.')
      return
    }
    if (item.action === 'generate_profile_days') {
      await generatePlan(profile.days_per_week || null, 'quick')
      return
    }
    if (item.action === 'regen_selected_day') {
      if (!activePlan || !selectedDay) {
        appendSystem('Generate a plan first, then select a day to regenerate.')
        return
      }
      await regenerateDay()
      return
    }
    if (item.action === 'explain_selected_day') {
      if (!activePlan || !selectedDay) {
        appendSystem('Generate a plan first, then select a day to explain.')
        return
      }
      await openExplanationDrawer(selectedDay)
      return
    }
    if (item.action === 'chat' && item.prompt) {
      await sendChat(item.prompt)
    }
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
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setProfileEdit(true)}>
              Edit
            </button>
          </div>
          <p className="section-hint">These values drive plan generation and safety filters.</p>

          <div className="profile-grid">
            <p><span className="profile-icon">🎯</span><strong> Goals:</strong> {profile.fitness_goals || 'Not set'}</p>
            <p><span className="profile-icon">📅</span><strong> Days/week:</strong> {profile.days_per_week}</p>
            <p><span className="profile-icon">🔥</span><strong> Intensity:</strong> {profile.intensity}</p>
            <div className="mini-meter">
              <div className="mini-meter-fill" style={{ width: `${Math.min(100, ((Number(profile.intensity) || 1) / 5) * 100)}%` }} />
            </div>
            <p><span className="profile-icon">🛡️</span><strong> Injuries:</strong> {(profile.injury_muscles || []).join(', ') || 'None'}</p>
          </div>

        </section>

        <section className="panel-section">
          <h3>Plan History</h3>
          <p className="section-hint">Manage saved weekly plans.</p>

          <label className="toggle-row">
            <input type="checkbox" checked={savePreviousWeeks} onChange={(e) => setSavePreviousWeeks(e.target.checked)} />
            Auto-save weekly plans
          </label>
          <label className="toggle-row">
            <input type="checkbox" checked={showHistory} onChange={(e) => setShowHistory(e.target.checked)} />
            Show saved plans
          </label>

          {showHistory && (
            <div className="history-list">
              {planHistory.length === 0 && <p className="muted small">No saved weeks yet.</p>}
              {planHistory.map((h) => (
                <button
                  key={h.id}
                  type="button"
                  className="history-item"
                  onClick={() => {
                    setActivePlan({
                      plan: h.plan,
                      krr_reasoning: {},
                      day_themes: {},
                      day_theme_muscles: {},
                      evaluation_ok: true,
                      suggestions: [],
                      requestId: null,
                      createdAt: h.createdAt,
                    })
                    const labels = sortedDayLabelsFromPlan(h.plan)
                    setSelectedDay(labels[0] || null)
                    appendSystem(`Loaded previous week from ${new Date(h.createdAt).toLocaleString()}.`)
                  }}
                >
                  {new Date(h.createdAt).toLocaleDateString()} • {h.days} days
                </button>
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
                  const selected = athletes.find((a) => a.id === e.target.value)
                  if (selected) appendSystem(`Loaded athlete: ${selected.name}`)
                }}
                className="user-input"
              >
                {athletes.map((athlete) => (
                  <option key={athlete.id} value={athlete.id}>{athlete.name}</option>
                ))}
              </select>
              <button type="button" className="btn btn-ghost" onClick={() => setNewAthleteOpen((v) => !v)}>
                {newAthleteOpen ? 'Cancel' : 'New Athlete'}
              </button>
            </div>
              <button type="button" className="btn btn-primary btn-primary-action" disabled={chatLoading} onClick={() => generatePlan()}>
              Generate Plan
            </button>
          </div>
        </div>

          <div className="status-strip">
            <span className="pulse-dot" />
            <span>
              {injuryImpact.injuries.length
                ? `Injury filter active (${injuryImpact.injuries.join(', ')}) • plan auto-adjusts to avoid unsafe muscles`
                : 'Ready • Generate a plan or ask for changes'}
            </span>
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
                {quickStartSuggestions.map((item) => (
                  <button key={item.key} type="button" className="quick-chip" onClick={() => handleQuickSuggestion(item)}>
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {chatMessages.map((m, idx) => {
            const roleLabel = m.role === 'user' ? 'You' : m.role === 'assistant' ? 'Coach AI' : 'System'
            const roleIcon = m.role === 'user' ? '👤' : m.role === 'assistant' ? '🤖' : 'ℹ️'
            return (
              <div key={`${idx}-${m.role}`} className={`msg-row msg-row-${m.role}`}>
                <div className={`msg-avatar msg-avatar-${m.role}`} aria-hidden>{roleIcon}</div>
                <div className={`msg msg-${m.role}`}>
                  <div className="msg-head">
                    <p className="msg-role">{roleLabel}</p>
                  </div>
                  <p>{m.text}</p>
                  {m.safetyVerified && <span className="safety-badge">🟢 Safety Verified</span>}
                  {m.validationText && !m.safetyVerified && <p className="validation-ok">✓ {m.validationText}</p>}

                  {m.pendingProfileUpdate && m.pendingProfileStatus === 'pending' && (
                    <p className="detected-update-state muted">Pending profile update — review in modal.</p>
                  )}
                  {m.pendingProfileUpdate && m.pendingProfileStatus === 'applied' && (
                    <p className="detected-update-state success">Saved to profile card.</p>
                  )}
                  {m.pendingProfileUpdate && m.pendingProfileStatus === 'discarded' && (
                    <p className="detected-update-state muted">Discarded. No profile changes saved.</p>
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
                                {(m.plan[d] || []).length === 0 && (
                                  <li className="summary-ex-item muted">Rest Day — no injury-safe exercises matched this day.</li>
                                )}
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
                          View reasoning
                        </button>
                        <button type="button" className="btn btn-ghost btn-sm" onClick={regenerateDay}>Regenerate selected day</button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )
          })}

          {chatLoading && (
            <div className="msg-row msg-row-system">
              <div className="msg-avatar msg-avatar-system" aria-hidden>ℹ️</div>
              <div className="msg msg-system loading">Thinking …</div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        <form
          className="chat-input-row"
          onSubmit={(e) => {
            e.preventDefault()
            sendChat()
          }}
        >
          <input
            aria-label="Chat with workout planner"
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            placeholder="Ask for a change (e.g. Regenerate Day 2 or make it lower impact)"
            disabled={chatLoading}
          />
          <button type="submit" className="btn btn-primary" disabled={chatLoading}>Send</button>
        </form>

        {DEV_MODE && lastRequestId && <p className="muted small">Request ID: {lastRequestId}</p>}
      </main>

      <aside className="right-panel card">
        <h2 className="panel-title">Weekly Plan View</h2>
        <p className="panel-subtitle">Inspect each day, then explain or regenerate if needed.</p>

        {injuryImpact.injuries.length > 0 && (
          <div className="injury-impact-card">
            <p className="injury-impact-title">Injury impact on this plan</p>
            <p className="muted small">Active injuries: {injuryImpact.injuries.join(', ')}</p>
            <p className="muted small">
              Blocked muscles: {injuryImpact.blocked.length ? injuryImpact.blocked.slice(0, 10).join(', ') : 'resolving...'}
              {injuryImpact.blocked.length > 10 ? ` +${injuryImpact.blocked.length - 10} more` : ''}
            </p>
            {activePlan && (
              <p className="muted small">
                {injuryImpact.restDays > 0
                  ? `${injuryImpact.restDays} day(s) set to Rest because no injury-safe exercises matched.`
                  : 'Plan adjusted to avoid blocked muscles while preserving training days.'}
              </p>
            )}
          </div>
        )}

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
              Generate Plan
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
                {selectedDayExercises.length === 0 && (
                  <li className="exercise-item muted">Rest Day — recommended because no injury-safe exercises matched today’s theme.</li>
                )}
                {selectedDayExercises.map((ex, idx) => (
                  <li key={`${ex.name}-${idx}`} className="exercise-item">
                    <p className="exercise-name">{toTitleCase(ex.name)}</p>
                    <p className="exercise-prescription">{ex.sets} × {ex.reps}</p>
                  </li>
                ))}
              </ol>

              <div className="day-actions row gap wrap">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => openExplanationDrawer(selectedDay)}>
                  Explain Day
                </button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={regenerateDay}>
                  Regenerate Day Plan
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
                  <p>✓ Adjusted workload with profile constraints and injury safety rules.</p>
                  <p className="muted small">Profile intensity influences set scaling and day-level load.</p>
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

      {profileEdit && (
        <section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="edit-profile-title">
          <div className="confirm-card">
            <h3 id="edit-profile-title">Edit profile</h3>
            <div className="wizard-grid">
              <p className="wizard-label">Update your training preferences and injury filters.</p>
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

              <label>Injury muscles</label>
              <div className="chips">
                {MUSCLE_OPTIONS.map((m) => (
                  <button
                    key={m}
                    type="button"
                    className={`chip ${onboardingDraft.injuries.includes(m) ? 'chip-active' : ''}`}
                    onClick={() => toggleInjuryDraft(m)}
                  >
                    {m}
                  </button>
                ))}
              </div>

              <div className="row gap confirm-actions">
                <button type="button" className="btn btn-ghost" onClick={() => setProfileEdit(false)}>Cancel</button>
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
            </div>
          </div>
        </section>
      )}

      {profileUpdateModal.open && (
        <section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-update-title">
          <div className="confirm-card">
            <h3 id="confirm-update-title">Confirm profile update</h3>
            <p>{profileUpdateModal.prompt || 'Detected profile updates from your message.'}</p>
            <div className="detected-update-box">
              <p className="detected-update-label">Changes to apply</p>
              <div className="detected-update-chips">
                {(profileUpdateModal.summary || []).map((item) => (
                  <span key={item} className="detected-update-chip">{item}</span>
                ))}
              </div>
            </div>
            <div className="row gap confirm-actions">
              <button type="button" className="btn btn-ghost" onClick={discardProfileUpdateFromModal}>
                Discard
              </button>
              <button type="button" className="btn btn-primary" onClick={confirmProfileUpdateFromModal}>
                Confirm & Save
              </button>
            </div>
          </div>
        </section>
      )}

      {(drawerOpen || deleteConfirmOpen || profileUpdateModal.open || profileEdit) && (
        <div
          className="drawer-backdrop"
          onClick={() => {
            if (drawerOpen) setDrawerOpen(false)
            if (deleteConfirmOpen) setDeleteConfirmOpen(false)
            if (profileUpdateModal.open) closeProfileUpdateModal()
            if (profileEdit) setProfileEdit(false)
          }}
          aria-hidden
        />
      )}
    </div>
  )
}
