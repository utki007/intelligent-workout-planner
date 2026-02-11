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

function addMessage(role, content) {
  const bubble = document.createElement("article");
  bubble.className = `msg ${role}`;
  bubble.textContent = content;
  chatLog.appendChild(bubble);
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

    addMessage("assistant", payload.reply || "No response from assistant.");
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

addMessage(
  "assistant",
  "Tell me your goal, available days, and constraints. I can keep your profile in the database and build a rule-safe workout plan."
);
