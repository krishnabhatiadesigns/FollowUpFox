const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function request(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const response = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    headers: isForm ? { ...options.headers } : { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

export const api = {
  url: API_URL,
  me: () => request("/auth/me"),
  stats: () => request("/dashboard/stats"),
  contacts: (category = "all") => request(`/contacts?category=${category}`),
  connectContact: (contactId) => request(`/contacts/${contactId}/connect`, { method: "POST" }),
  outreach: () => request("/outreach"),
  suggestDraft: (contactId, previousSubject = "", previousBody = "", followUpOutreachId = null) => request("/drafts/suggest", { method: "POST", body: JSON.stringify({
    contact_id: contactId,
    previous_subject: previousSubject,
    previous_body: previousBody,
    follow_up: followUpOutreachId !== null,
    outreach_id: followUpOutreachId,
  }) }),
  suggestLinkedinDraft: (contactId) => request("/drafts/linkedin", { method: "POST", body: JSON.stringify({ contact_id: contactId }) }),
  createOutreach: (contactId, subject) => request("/outreach", { method: "POST", body: JSON.stringify({ contact_id: contactId, subject }) }),
  sendOutreach: (outreachId, subject, body, attachResume = false, followUp = false) => request("/outreach/send", { method: "POST", body: JSON.stringify({
    outreach_id: outreachId,
    subject,
    body,
    attach_resume: attachResume,
    follow_up: followUp,
  }) }),
  query: (question, history = []) => request("/ai/query", { method: "POST", body: JSON.stringify({ question, history }) }),
  analyzeResume: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/resume/analyze", { method: "POST", body: form, headers: {} });
  },
  employers: () => request("/employers"),
  profile: () => request("/profile"),
  latestResume: () => request("/resume/latest"),
  updateProfile: (profile) => request("/profile", { method: "PUT", body: JSON.stringify(profile) }),
  settings: () => request("/settings"),
  updateSettings: (settings) => request("/settings", { method: "PUT", body: JSON.stringify(settings) }),
  calendarEvents: (start, end) => request(`/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),
};
