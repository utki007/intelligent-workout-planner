const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const injuriesList = document.getElementById("injuries-list");
const pydatalogOutput = document.getElementById("pydatalog-output");

const SESSION_KEY = "workout_planner_session_id";
let sessionId = window.localStorage.getItem(SESSION_KEY) || "";

function addMessage(role, content) {
  const wrap = document.createElement("div");
  wrap.className = `msg-wrap msg-wrap-${role}`;
  const bubble = document.createElement("article");
  bubble.className = `msg ${role}`;
  bubble.textContent = content;
  wrap.appendChild(bubble);
  chatLog.appendChild(wrap);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function setBusy(isBusy) {
  sendBtn.disabled = isBusy;
  chatInput.disabled = isBusy;
}

function setInjuries(profile) {
  const injuries = profile?.injuries;
  if (injuries && injuries.length > 0) {
    injuriesList.textContent = injuries.join(", ");
  } else {
    injuriesList.textContent = "None";
  }
}

function setPydatalog(text) {
  pydatalogOutput.textContent = text || "No query yet. Send a message to see rule-engine output.";
}

async function sendMessage(message) {
  const trimmed = message.trim();
  if (!trimmed) return;

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

    setInjuries(payload.profile || {});
    setPydatalog(payload.pydatalog_output || "");

    addMessage("assistant", payload.reply || "No response.");
  } catch (error) {
    addMessage("assistant", `Error: ${error.message}`);
    setPydatalog("");
  } finally {
    setBusy(false);
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = chatInput.value;
  chatInput.value = "";
  sendMessage(text);
});

chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    const text = chatInput.value;
    chatInput.value = "";
    sendMessage(text);
  }
});

addMessage("assistant", "Ask for exercise recommendations, set or clear injuries, or generate a weekly plan. Responses use the PyDatalog rule engine for safety.");
setPydatalog("No query yet. Send a message to see rule-engine output.");
