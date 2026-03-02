import { useState, useRef, useEffect } from 'react'

const API = '/api'

const MUSCLE_OPTIONS = [
  'chest', 'pectorals', 'back', 'lats', 'upper back', 'traps', 'shoulders', 'delts', 'arms', 'biceps', 'triceps',
  'legs',
  'quads', 'hamstrings', 'glutes', 'calves', 'abs', 'forearms', 'adductors', 'abductors'
]

function dayNumber(label) {
  const m = String(label || '').match(/\d+/)
  return m ? Number(m[0]) : 0
}

function sortedDayLabelsFromPlan(plan) {
  return Object.keys(plan || {}).sort((a, b) => dayNumber(a) - dayNumber(b))
}

const ONBOARDING_MESSAGE =
  "Hi! I'm your workout coach. I'll ask you a few quick questions here in chat to set up your profile before we generate your plan.";

function isGeneratePlanIntent(text) {
  const t = (text || '').toLowerCase().trim()
  return /generate\s*(my\s*)?plan|(my\s*)?plan\s*please|create\s*(my\s*)?plan/.test(t) || t === 'generate plan' || t === 'generate my plan'
}

function isRecoveryOrChangeIntent(text) {
  const t = (text || '').toLowerCase().trim()
  return /recover|healed|better|no longer|changed|update|different|new injury|hurt|pain/.test(t)
}

export default function App() {
  const [userId, setUserId] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [profile, setProfile] = useState({
    injury_muscles: [],
    intensity: 3,
    feedback_history: [],
    onboarding_complete: false,
    fitness_goals: '',
    days_per_week: 5,
  })
  const [onboardingGoals, setOnboardingGoals] = useState('')
  const [onboardingDays, setOnboardingDays] = useState(5)
  const [chatMessages, setChatMessages] = useState([
    { role: 'agent', text: ONBOARDING_MESSAGE },
  ])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [explanationCache, setExplanationCache] = useState({})
  const [expandedThinking, setExpandedThinking] = useState({})
  const [explanationLoading, setExplanationLoading] = useState(null)
  const [collapsedDays, setCollapsedDays] = useState({})
  const [feedbackSoreness, setFeedbackSoreness] = useState(3)
  const [feedbackSent, setFeedbackSent] = useState(false)
  const chatEndRef = useRef(null)
  const chatInputRef = useRef(null)
  const [isChatOnboarding, setIsChatOnboarding] = useState(false)
  const [onboardingStep, setOnboardingStep] = useState(0) // 0 = none, 1=goals, 2=days, 3=injuries, 4=intensity
  const [onboardingDraft, setOnboardingDraft] = useState({
    goals: '',
    days: 5,
    injuries: [],
    intensity: 3,
  })

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages])

  useEffect(() => {
    if (!sidebarOpen && !chatLoading) chatInputRef.current?.focus()
  }, [sidebarOpen, chatLoading])

  const startChatOnboarding = (profFromApi) => {
    if (isChatOnboarding || profile.onboarding_complete) return
    const base = profFromApi || profile
    setIsChatOnboarding(true)
    setOnboardingStep(1)
    setOnboardingDraft({
      goals: base.fitness_goals || '',
      days: base.days_per_week ?? 5,
      injuries: base.injury_muscles || [],
      intensity: base.intensity ?? 3,
    })
    setChatMessages((prev) => [
      ...prev,
      { role: 'agent', text: "Before I build your plan, tell me about your fitness goals. What are you trying to achieve?" },
    ])
  }

  useEffect(() => {
    const id = (userId || '').trim()
    if (!id) {
      setProfile({ injury_muscles: [], intensity: 3, feedback_history: [] })
      setIsChatOnboarding(false)
      setOnboardingStep(0)
      return
    }
    fetch(`${API}/profile?user_id=${encodeURIComponent(id)}`)
      .then((r) => r.ok ? r.json() : {})
      .then((p) => {
        const prof = {
          injury_muscles: p.injury_muscles || [],
          intensity: p.intensity ?? 3,
          feedback_history: p.feedback_history || [],
          onboarding_complete: p.onboarding_complete ?? false,
          fitness_goals: p.fitness_goals || '',
          days_per_week: p.days_per_week ?? 5,
        }
        setProfile(prof)
        if (!prof.onboarding_complete) {
          // Start chat-based onboarding instead of forcing sidebar work
          startChatOnboarding(prof)
        } else {
          setOnboardingGoals(prof.fitness_goals)
          setOnboardingDays(prof.days_per_week)
        }
      })
      .catch(() => setProfile({
        injury_muscles: [],
        intensity: 3,
        feedback_history: [],
        onboarding_complete: false,
        fitness_goals: '',
        days_per_week: 5,
      }))
  }, [userId])

  const saveProfile = async (updates) => {
    const id = (userId || 'user').trim()
    if (!id) return
    try {
      await fetch(`${API}/profile`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: id, ...updates }),
      })
      setProfile((prev) => ({ ...prev, ...updates }))
    } catch (e) {
      console.error(e)
    }
  }

  const toggleInjury = (muscle) => {
    const next = profile.injury_muscles.includes(muscle)
      ? profile.injury_muscles.filter((m) => m !== muscle)
      : [...profile.injury_muscles, muscle]
    saveProfile({ injury_muscles: next })
  }

  const submitFeedback = async () => {
    const id = (userId || 'user').trim()
    if (!id) return
    try {
      const res = await fetch(`${API}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: id, soreness: feedbackSoreness }),
      })
      if (res.ok) {
        setFeedbackSent(true)
        setProfile((prev) => ({
          ...prev,
          feedback_history: [...(prev.feedback_history || []), { soreness: feedbackSoreness }].slice(-20),
        }))
      }
    } catch (e) {
      console.error(e)
    }
  }

  const toggleDayCollapsed = (msgIdx, day) => {
    setCollapsedDays((prev) => ({ ...prev, [`${msgIdx}-${day}`]: !prev[`${msgIdx}-${day}`] }))
  }

  const fetchExplanation = async (msgIdx, day) => {
    const id = (userId || 'user').trim()
    const key = `${msgIdx}-${day}`
    if (explanationCache[key]) {
      setExpandedThinking((prev) => ({ ...prev, [key]: !prev[key] }))
      return
    }
    setExplanationLoading(key)
    try {
      const res = await fetch(`${API}/explanation?day=${encodeURIComponent(day)}&user_id=${encodeURIComponent(id)}`)
      const data = res.ok ? await res.json() : null
      setExplanationCache((prev) => ({
        ...prev,
        [key]: data ? { krr_reasoning: data.krr_reasoning || '', llm_explanation: data.llm_explanation || '' } : { error: res.statusText },
      }))
      setExpandedThinking((prev) => ({ ...prev, [key]: true }))
    } catch (e) {
      setExplanationCache((prev) => ({ ...prev, [key]: { error: e.message } }))
      setExpandedThinking((prev) => ({ ...prev, [key]: true }))
    } finally {
      setExplanationLoading(null)
    }
  }

  const toggleThinking = (msgIdx, day) => {
    const key = `${msgIdx}-${day}`
    if (explanationCache[key]) setExpandedThinking((prev) => ({ ...prev, [key]: !prev[key] }))
    else fetchExplanation(msgIdx, day)
  }

  const handleOnboardingTurn = async (msg) => {
    const text = msg.trim()
    const textLower = text.toLowerCase()
    const numberWords = { three: 3, four: 4, five: 5, six: 6, seven: 7 }

    const parseDays = () => {
      const mNum = textLower.match(/\d+/)
      if (mNum) return Number(mNum[0])
      for (const [w, n] of Object.entries(numberWords)) {
        if (textLower.includes(w)) return n
      }
      return NaN
    }

    const parseIntensity = () => {
      const m = textLower.match(/\d+(\.\d+)?/)
      if (m) return parseFloat(m[0])
      return NaN
    }

    const normalizeInjuryToken = (tok) => {
      const t = (tok || '').trim().toLowerCase()
      if (!t) return null
      const aliases = {
        shoulder: 'shoulders',
        pec: 'chest',
        pectoral: 'chest',
        chest: 'chest',
        shoulders: 'shoulders',
        back: 'back',
        lowback: 'back',
        'lower back': 'back',
        arm: 'arms',
        arms: 'arms',
        elbow: 'arms',
        wrist: 'forearms',
        forearm: 'forearms',
        knee: 'legs',
        knees: 'legs',
        leg: 'legs',
        legs: 'legs',
        core: 'abs',
        abs: 'abs',
      }
      const mapped = aliases[t] || t
      // Accept only known options (so we can enforce backend constraints reliably)
      return MUSCLE_OPTIONS.includes(mapped) ? mapped : null
    }

    const parseInjuries = () => {
      if (/^(none|no injury|no injuries|nothing)$/i.test(textLower)) return []
      const raw = textLower.split(/[,\n]/).map((s) => s.trim()).filter(Boolean)
      const mapped = raw.map(normalizeInjuryToken).filter(Boolean)
      // Deduplicate
      return [...new Set(mapped)]
    }

    // Step 1: goals
    if (onboardingStep === 1) {
      const cleaned = text.replace(/\s+/g, ' ').trim()
      const hasAlpha = /[a-zA-Z]/.test(cleaned)
      if (!cleaned || cleaned.length < 4 || !hasAlpha || /^(idk|dont know|not sure|none)$/i.test(cleaned)) {
        setChatMessages((prev) => [
          ...prev,
          { role: 'agent', text: 'Tell me your goal in one short line (e.g. build muscle, get stronger, lose fat). If you’re unsure, pick one.' },
        ])
        return
      }
      setOnboardingDraft((prev) => ({ ...prev, goals: cleaned }))
      setOnboardingStep(2)
      setChatMessages((prev) => [
        ...prev,
        { role: 'agent', text: 'Great. How many days per week do you want to work out? (3–7)' },
      ])
      return
    }

    // Step 2: days per week
    if (onboardingStep === 2) {
      const days = parseDays()
      if (!days || days < 3 || days > 7) {
        setChatMessages((prev) => [
          ...prev,
          { role: 'agent', text: 'Please reply with a number between 3 and 7 (e.g. 5).' },
        ])
        return
      }
      setOnboardingDraft((prev) => ({ ...prev, days }))
      setOnboardingStep(3)
      setChatMessages((prev) => [
        ...prev,
        { role: 'agent', text: "Any injuries or areas to avoid? Reply with comma-separated items (e.g. shoulders, back, knees) or say 'none'." },
      ])
      return
    }

    // Step 3: injuries
    if (onboardingStep === 3) {
      const injuries = parseInjuries()
      if (!/^(none|no injury|no injuries|nothing)$/i.test(textLower) && injuries.length === 0) {
        setChatMessages((prev) => [
          ...prev,
          { role: 'agent', text: `I didn’t recognize that. Try: chest, shoulders, back, arms, legs, abs, forearms (comma-separated) or 'none'.` },
        ])
        return
      }
      setOnboardingDraft((prev) => ({ ...prev, injuries }))
      setOnboardingStep(4)
      setChatMessages((prev) => [
        ...prev,
        { role: 'agent', text: 'Last one: how intense should training feel from 1 (very light) to 5 (hard but manageable)?' },
      ])
      return
    }

    // Step 4: intensity
    if (onboardingStep === 4) {
      let intensity = parseIntensity()
      if (!intensity || intensity < 1 || intensity > 5) {
        setChatMessages((prev) => [
          ...prev,
          { role: 'agent', text: 'Choose an intensity between 1 and 5 (you can say just the number).' },
        ])
        return
      }
      intensity = Math.max(1, Math.min(5, intensity))
      const finalDraft = {
        ...onboardingDraft,
        intensity,
      }
      setOnboardingDraft(finalDraft)
      // Persist profile
      await saveProfile({
        onboarding_complete: true,
        fitness_goals: finalDraft.goals,
        days_per_week: finalDraft.days,
        injury_muscles: finalDraft.injuries,
        intensity: finalDraft.intensity,
      })
      setProfile((prev) => ({
        ...prev,
        onboarding_complete: true,
        fitness_goals: finalDraft.goals,
        days_per_week: finalDraft.days,
        injury_muscles: finalDraft.injuries,
        intensity: finalDraft.intensity,
      }))
      setIsChatOnboarding(false)
      setOnboardingStep(0)
      setChatMessages((prev) => [
        ...prev,
        {
          role: 'agent',
          text:
            "Perfect, I've saved your profile:\n" +
            `- Goals: ${finalDraft.goals}\n` +
            `- Days/week: ${finalDraft.days}\n` +
            `- Injuries: ${finalDraft.injuries.length ? finalDraft.injuries.join(', ') : 'none'}\n` +
            `- Intensity: ${finalDraft.intensity}\n` +
            'You can now say "Generate my plan" or click the Generate plan button.',
        },
      ])
    }
  }

  const runGeneratePlan = async (id) => {
    const res = await fetch(`${API}/plan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: id,
        name: id,
        experience_level: 'beginner',
        injury_muscles: profile.injury_muscles || [],
      }),
    })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  }

  const completeOnboarding = async () => {
    const id = (userId || 'user').trim()
    if (!id || !onboardingGoals.trim()) return
    const goals = onboardingGoals.trim()
    const days = onboardingDays
    await saveProfile({
      onboarding_complete: true,
      fitness_goals: goals,
      days_per_week: days,
    })
    setProfile((prev) => ({
      ...prev,
      onboarding_complete: true,
      fitness_goals: goals,
      days_per_week: days,
    }))
    setChatMessages((prev) => [
      ...prev,
      { role: 'agent', text: `Great! I've saved your goals (${goals}) and ${days} days per week. Now you can generate your workout plan!` },
    ])
  }

  const sendChat = async (textOverride) => {
    const msg = (textOverride ?? chatInput).trim()
    if (!msg) return
    const id = (userId || 'user').trim()
    setChatMessages((prev) => [...prev, { role: 'user', text: msg }])
    if (!textOverride) setChatInput('')
    setChatLoading(true)
    try {
      // If onboarding isn't complete, run chat-based onboarding flow instead of normal chat.
      if (!profile.onboarding_complete) {
        if (!isChatOnboarding) {
          startChatOnboarding()
          setChatLoading(false)
          return
        }
        await handleOnboardingTurn(msg)
        setChatLoading(false)
        return
      }
      // Detect recovery/change intent and suggest updating profile
      if (isRecoveryOrChangeIntent(msg) && profile.onboarding_complete) {
        setChatMessages((prev) => [
          ...prev,
          { role: 'agent', text: "Sounds like something changed! Open the Profile panel on the left to update your injuries or goals. I'll use the updated info for your next plan." },
        ])
        setSidebarOpen(true)
        setChatLoading(false)
        return
      }
      if (isGeneratePlanIntent(msg)) {
        if (!id) {
          setChatMessages((prev) => [...prev, { role: 'agent', text: 'Enter a User ID first.' }])
          setChatLoading(false)
          return
        }
        const data = await runGeneratePlan(id)
        const plan = data.plan
        const krrReasoning = data.krr_reasoning || {}
        const dayThemes = data.day_themes || {}
        const suggestions = data.suggestions || []
        let replyText = "Here's your plan. Expand a day to see exercises; tap See Thinking for reasoning."
        if (suggestions.length > 0) replyText += ` Note: ${suggestions.join(' ')}`
        setChatMessages((prev) => [...prev, { role: 'agent', text: replyText, plan, day_themes: dayThemes, krr_reasoning: krrReasoning }])
        setChatLoading(false)
        return
      }
      let planSummary = ''
      let krrSummary = ''
      const lastPlanMsg = [...chatMessages].reverse().find((m) => m.role === 'agent' && m.plan)
      if (lastPlanMsg) {
        planSummary = Object.entries(lastPlanMsg.plan).map(([d, exs]) => `${d}: ${(exs || []).map((e) => e.name).join(', ') || 'Rest'}`).join('; ')
        krrSummary = Object.entries(lastPlanMsg.krr_reasoning || {}).slice(0, 2).map(([, v]) => v).join(' ')
      }
      const res = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: id, message: msg, plan_summary: planSummary || null, krr_summary: krrSummary || null }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setChatMessages((prev) => [...prev, { role: 'agent', text: data.response }])
    } catch (e) {
      setChatMessages((prev) => [...prev, { role: 'agent', text: e.message?.includes('fetch') ? 'API not reachable. Run: uvicorn api.app:app --port 8000' : (e.message || 'Something went wrong.') }])
    } finally {
      setChatLoading(false)
    }
  }

  const generatePlan = async () => {
    const id = (userId || 'user').trim()
    if (!id) {
      setChatMessages((prev) => [...prev, { role: 'agent', text: 'Enter a User ID first.' }])
      return
    }
    if (!profile.onboarding_complete) {
      // Kick off chat-based onboarding instead of forcing sidebar work
      startChatOnboarding()
      return
    }
    setChatLoading(true)
    try {
      const data = await runGeneratePlan(id)
      const plan = data.plan
      const krrReasoning = data.krr_reasoning || {}
      const dayThemes = data.day_themes || {}
      const suggestions = data.suggestions || []
      let replyText = "Here's your plan. Expand a day to see exercises; tap See Thinking for reasoning."
      if (suggestions.length > 0) replyText += ` Note: ${suggestions.join(' ')}`
      setChatMessages((prev) => [...prev, { role: 'agent', text: replyText, plan, day_themes: dayThemes, krr_reasoning: krrReasoning }])
    } catch (e) {
      setChatMessages((prev) => [...prev, { role: 'agent', text: 'Failed: ' + (e.message || 'Is the API running on port 8000?') }])
    } finally {
      setChatLoading(false)
    }
  }

  return (
    <div className="app">
      {/* Left sidebar - user state */}
      <aside className={`sidebar ${sidebarOpen ? 'sidebar--open' : ''}`}>
        <div className="sidebar-header">
          <h2 className="sidebar-title">Your profile</h2>
          <button type="button" className="sidebar-close" onClick={() => setSidebarOpen(false)} aria-label="Close">×</button>
        </div>
        <div className="sidebar-body">
          {!profile.onboarding_complete ? (
            <section className="sidebar-section onboarding-section">
              <h3 className="onboarding-title">Get started</h3>
              <label className="sidebar-label">Fitness goals</label>
              <textarea
                value={onboardingGoals}
                onChange={(e) => setOnboardingGoals(e.target.value)}
                placeholder="e.g. Build muscle, lose weight, improve strength..."
                className="onboarding-textarea"
                rows={3}
              />
              <label className="sidebar-label">Days per week</label>
              <select
                value={onboardingDays}
                onChange={(e) => setOnboardingDays(Number(e.target.value))}
                className="onboarding-select"
              >
                <option value={3}>3 days</option>
                <option value={4}>4 days</option>
                <option value={5}>5 days</option>
                <option value={6}>6 days</option>
              </select>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={completeOnboarding}
                disabled={!onboardingGoals.trim()}
              >
                Save profile
              </button>
            </section>
          ) : (
            <>
              <section className="sidebar-section">
                <label className="sidebar-label">Fitness goals</label>
                <p className="profile-info">{profile.fitness_goals || 'Not set'}</p>
                <label className="sidebar-label">Days per week</label>
                <p className="profile-info">{profile.days_per_week} days</p>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    setProfile((prev) => ({ ...prev, onboarding_complete: false }))
                    setOnboardingGoals(profile.fitness_goals || '')
                    setOnboardingDays(profile.days_per_week || 5)
                  }}
                >
                  Edit
                </button>
              </section>
              <section className="sidebar-section">
                <label className="sidebar-label">Intensity (1–5)</label>
            <div className="intensity-row">
              <input
                type="range"
                min={1}
                max={5}
                step={0.5}
                value={profile.intensity ?? 3}
                onChange={(e) => {
                  const v = parseFloat(e.target.value)
                  setProfile((prev) => ({ ...prev, intensity: v }))
                  saveProfile({ intensity: v })
                }}
                className="intensity-slider"
              />
              <span className="intensity-value">{profile.intensity ?? 3}</span>
            </div>
          </section>
          <section className="sidebar-section">
            <label className="sidebar-label">Injuries (tap to toggle)</label>
            <div className="injury-chips">
              {MUSCLE_OPTIONS.map((m) => (
                <button
                  key={m}
                  type="button"
                  className={`injury-chip ${(profile.injury_muscles || []).includes(m) ? 'injury-chip--active' : ''}`}
                  onClick={() => toggleInjury(m)}
                >
                  {m}
                </button>
              ))}
            </div>
          </section>
          <section className="sidebar-section">
            <label className="sidebar-label">Soreness feedback</label>
            <div className="feedback-row">
              <input
                type="range"
                min={1}
                max={5}
                step={1}
                value={feedbackSoreness}
                onChange={(e) => setFeedbackSoreness(Number(e.target.value))}
                className="intensity-slider"
              />
              <span>{feedbackSoreness}</span>
            </div>
            <button type="button" className="btn btn-primary btn-sm" onClick={submitFeedback}>
              Submit feedback
            </button>
            {feedbackSent && <span className="feedback-ok">Saved</span>}
            {(profile.feedback_history || []).length > 0 && (
              <div className="feedback-history">
                <p className="feedback-history-title">Recent:</p>
                {(profile.feedback_history || []).slice(-5).reverse().map((f, i) => (
                  <p key={i} className="feedback-history-item">Soreness: {f.soreness}</p>
                ))}
              </div>
            )}
          </section>
            </>
          )}
        </div>
      </aside>

      {/* Main: header + chat (centered) */}
      <div className="main-wrap">
        <header className="header">
          <button type="button" className="btn btn-ghost sidebar-toggle" onClick={() => setSidebarOpen(true)} aria-label="Open profile">
            Profile
          </button>
          <h1 className="header-title">Workout Coach</h1>
          <div className="header-actions">
            <input
              type="text"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="User ID"
              className="header-input"
              aria-label="User ID"
            />
            <button type="button" onClick={generatePlan} disabled={chatLoading} className="btn btn-primary">
              Generate plan
            </button>
          </div>
        </header>

        <main className="main">
          <div className="chat">
            {chatMessages.map((m, i) => (
              <div key={i} className={`bubble bubble-${m.role}`}>
                <div className="bubble-text">{m.text}</div>
                {m.role === 'agent' && m.plan && (
                  <div className="plan">
                    {sortedDayLabelsFromPlan(m.plan).map((day) => {
                      const exs = m.plan[day] || []
                      const theme = (m.day_themes && m.day_themes[day]) || day
                      const targets = (m.day_theme_muscles && m.day_theme_muscles[day]) || []
                      const key = `${i}-${day}`
                      const dayCollapsed = collapsedDays[key]
                      const thinkingOpen = expandedThinking[key]
                      const cached = explanationCache[key]
                      const loading = explanationLoading === key
                      const isRest = !exs.length
                      return (
                        <div key={day} className={`day-card ${isRest ? 'day-card--rest' : ''}`}>
                          <button type="button" className="day-card-head" onClick={() => toggleDayCollapsed(i, day)}>
                            <div className="day-card-title-wrap">
                              <span className="day-card-title">{day} — {theme}</span>
                              {!!targets.length && (
                                <span className="day-card-subtitle">Targets: {targets.join(', ')}</span>
                              )}
                            </div>
                            <span className="day-card-toggle">{dayCollapsed ? '▼' : '▲'}</span>
                          </button>
                          {!dayCollapsed && (
                            <div className="day-card-body">
                              {exs.length ? (
                                <div className="day-exercises-table">
                                  <div className="day-ex-header">
                                    <span>Exercise</span>
                                    <span>Sets</span>
                                    <span>Reps</span>
                                    <span>Target</span>
                                  </div>
                                  {exs.map((ex, j) => (
                                    <div key={j} className="day-ex-row">
                                      <span className="day-ex-name">{ex.name}</span>
                                      <span className="day-ex-sets">{ex.sets}</span>
                                      <span className="day-ex-reps">{ex.reps}</span>
                                      <span className="day-ex-target">{ex.target || '—'}</span>
                                    </div>
                                  ))}
                                </div>
                              ) : (
                                <p className="day-rest">Rest day — no exercises for this day.</p>
                              )}
                              <button
                                type="button"
                                className="btn btn-ghost btn-thinking"
                                onClick={(e) => { e.stopPropagation(); toggleThinking(i, day) }}
                                disabled={loading}
                              >
                                {loading ? 'Loading…' : thinkingOpen ? 'Hide thinking' : 'See Thinking'}
                              </button>
                              {thinkingOpen && (cached || loading) && (
                                <div className="thinking-panel">
                                  {loading && !cached && <p className="thinking-loading">Loading…</p>}
                                  {cached?.error && <p className="thinking-error">{cached.error}</p>}
                                  {cached && !cached.error && (
                                    <>
                                      <p className="thinking-label">KRR reasoning</p>
                                      <pre className="thinking-text">{cached.krr_reasoning || '—'}</pre>
                                      <p className="thinking-label">LLM explanation</p>
                                      <pre className="thinking-text thinking-llm">{cached.llm_explanation || '—'}</pre>
                                    </>
                                  )}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            ))}
            {chatLoading && <div className="bubble bubble-agent bubble-loading">Thinking…</div>}
            <div ref={chatEndRef} />
          </div>

          <form className="chat-form" onSubmit={(e) => { e.preventDefault(); sendChat() }}>
            <input
              type="text"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder='Type "Generate my plan" or ask anything…'
              className="chat-input"
              disabled={chatLoading}
              aria-label="Message"
              ref={chatInputRef}
            />
            <button
              type="button"
              className="btn btn-ghost"
              onClick={generatePlan}
              disabled={chatLoading}
            >
              Generate plan
            </button>
            <button type="submit" className="btn btn-primary btn-send" disabled={chatLoading}>Send</button>
          </form>
        </main>
      </div>

      {sidebarOpen && <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} aria-hidden />}
    </div>
  )
}
