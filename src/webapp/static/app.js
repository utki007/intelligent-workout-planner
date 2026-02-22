const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const statusText = document.getElementById("status-text");
const llmBadge = document.getElementById("llm-badge");

const profileGoal = document.getElementById("profile-goal");
const profileLevel = document.getElementById("profile-level");
const profileDays = document.getElementById("profile-days");
const profileMinutes = document.getElementById("profile-minutes");
const profileInjuries = document.getElementById("profile-injuries");
const profileEquipment = document.getElementById("profile-equipment");
const profileFocus = document.getElementById("profile-focus");

const SESSION_KEY = "workout_planner_session_id";
let sessionId = window.localStorage.getItem(SESSION_KEY) || "";

function addMessage(role, content, options = {}) {
  const wrap = document.createElement("div");
  wrap.className = `msg-wrap msg-wrap-${role}`;

  const bubble = document.createElement("article");
  bubble.className = `msg ${role}`;
  bubble.textContent = content;
  wrap.appendChild(bubble);

  if (options.rulesApplied) {
    const badge = document.createElement("span");
    badge.className = "rules-badge";
    badge.textContent = "PyDatalog rules applied";
    wrap.appendChild(badge);
  }

  if (options.recommendations?.length) {
    const list = document.createElement("ul");
    list.className = "response-list recommendations";
    options.recommendations.slice(0, 12).forEach((ex) => {
      const li = document.createElement("li");
      li.textContent = `${ex.name} — ${ex.target || ex.bodyPart || ""} (${ex.difficulty || ""})`;
      list.appendChild(li);
    });
    wrap.appendChild(list);
  }

  if (options.plan?.length) {
    const planEl = document.createElement("div");
    planEl.className = "response-plan";
    options.plan.forEach((day) => {
      const dayBlock = document.createElement("div");
      dayBlock.className = "plan-day";
      dayBlock.innerHTML = `<strong>Day ${day.day} — ${day.name}</strong> (${day.focus || ""})`;
      const ul = document.createElement("ul");
      (day.exercises || []).forEach((ex) => {
        const li = document.createElement("li");
        li.textContent = ex.name || "";
        ul.appendChild(li);
      });
      dayBlock.appendChild(ul);
      planEl.appendChild(dayBlock);
    });
    wrap.appendChild(planEl);
  }

  chatLog.appendChild(wrap);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function setBusy(isBusy) {
  sendBtn.disabled = isBusy;
  chatInput.disabled = isBusy;
  statusText.textContent = isBusy ? "Processing..." : "Connected to planner backend";
}

function setProfile(profile) {
  profileGoal.textContent = profile.goal || "general fitness";
  profileLevel.textContent = profile.experience_level || "beginner";
  profileDays.textContent = String(profile.days_per_week || 3);
  profileMinutes.textContent = String(profile.session_minutes || 45);
  profileInjuries.textContent = profile.injuries?.length ? profile.injuries.join(", ") : "none";
  profileEquipment.textContent = profile.excluded_equipment?.length ? profile.excluded_equipment.join(", ") : "none";
  profileFocus.textContent = profile.focus_muscles?.length ? profile.focus_muscles.join(", ") : "none";
}

async function sendMessage(message) {
  const trimmed = message.trim();
  if (!trimmed) {
    return;
  }

  addMessage("user", trimmed);
  setBusy(true);

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: trimmed,
        session_id: sessionId || null,
      }),
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || `Request failed with ${response.status}`);
    }

    const payload = await response.json();
    sessionId = payload.session_id;
    window.localStorage.setItem(SESSION_KEY, sessionId);

    setProfile(payload.profile || {});
    llmBadge.textContent = payload.llm_used ? "LLM: active" : "LLM: fallback";

    const rulesApplied = (payload.recommendations?.length > 0 || payload.plan?.length > 0);
    addMessage("assistant", payload.reply || "No response from assistant.", {
      rulesApplied,
      recommendations: payload.recommendations,
      plan: payload.plan,
    });
  } catch (error) {
    addMessage("assistant", `Request error: ${error.message}`);
    llmBadge.textContent = "LLM: unavailable";
  } finally {
    setBusy(false);
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = chatInput.value;
  chatInput.value = "";
  await sendMessage(text);
});

chatInput.addEventListener("keydown", async (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    const text = chatInput.value;
    chatInput.value = "";
    await sendMessage(text);
  }
});

for (const chip of document.querySelectorAll(".chip")) {
  chip.addEventListener("click", async () => {
    const prompt = chip.getAttribute("data-prompt");
    if (!prompt) {
      return;
    }
    await sendMessage(prompt);
  });
}

addMessage("assistant", "Enter your prompt above. I use the PyDatalog knowledge base (exercises, your profile, injury-safety rules) to update your profile, recommend safe exercises, or generate a weekly plan.");
