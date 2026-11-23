import { useEffect, useMemo, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import logo from "./assets/follow-up-fox-logo.png";
import "./style-overrides.css";

function FormattedAnswer({ text }) {
  const lines = text.split(/\r?\n/);
  const blocks = [];
  let list = [];
  let ordered = false;
  const flushList = () => {
    if (!list.length) return;
    const List = ordered ? "ol" : "ul";
    blocks.push(<List key={`list-${blocks.length}`}>{list}</List>);
    list = [];
  };
  const inline = (value) => value.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith("**") && part.endsWith("**")
      ? <strong key={index}>{part.slice(2, -2)}</strong>
      : part
  );
  lines.forEach((line, index) => {
    const trimmed = line.trim();
    const bullet = trimmed.match(/^[-*]\s+(.+)/);
    const number = trimmed.match(/^\d+[.)]\s+(.+)/);
    if (bullet || number) {
      const isOrdered = Boolean(number);
      if (list.length && ordered !== isOrdered) flushList();
      ordered = isOrdered;
      list.push(<li key={index}>{inline((bullet || number)[1])}</li>);
    } else {
      flushList();
      if (!trimmed) return;
      const heading = trimmed.match(/^#{1,3}\s+(.+)/);
      blocks.push(heading
        ? <h4 key={index}>{inline(heading[1])}</h4>
        : <p key={index}>{inline(trimmed)}</p>);
    }
  });
  flushList();
  return <div className="formatted-answer">{blocks}</div>;
}

const initials = (contact) => `${contact.first[0]}${contact.last[0]}`;
const statusText = (status) => status === "replied" ? "Replied" : status === "draft" ? "Draft" : "Awaiting reply";
const formatTaskDue = (due) => localDateKey(due) === localDateKey(new Date())
  ? "Follow-up due today"
  : new Date(due).toLocaleDateString();
const localDateKey = (value) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
};

function Avatar({ contact, small = false }) {
  return <div className={`avatar ${small ? "avatar-small" : ""}`}>{contact ? initials(contact) : "KB"}</div>;
}

function Sidebar({ view, setView, count, stats }) {
  const [workspaceMenuOpen, setWorkspaceMenuOpen] = useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const nav = [["overview", "⌂", "Overview"], ["contacts", "♙", "Contacts"], ["outreach", "↗", "Outreach"], ["calendar", "□", "Calendar"], ["resume", "▤", "Resume profile"], ["ai", "✦", "AI assistant"], ["settings", "⚙", "Settings"]];
  const navigate = (nextView) => {
    setView(nextView);
    setWorkspaceMenuOpen(false);
    setProfileMenuOpen(false);
  };
  return <aside className="sidebar">
    <div className="brand"><img className="brand-logo" src={logo} alt="Follow Up Fox" /></div>
    <div className="menu-anchor"><button className="workspace-switcher" onClick={() => { setWorkspaceMenuOpen((open) => !open); setProfileMenuOpen(false); }} aria-expanded={workspaceMenuOpen} aria-haspopup="menu"><Avatar small /><span className="workspace-copy"><strong>Krishna's workspace</strong><span>Personal workspace</span></span></button>{workspaceMenuOpen && <div className="sidebar-menu workspace-menu" role="menu"><button onClick={() => navigate("overview")} role="menuitem">Overview</button><button onClick={() => navigate("settings")} role="menuitem">Workspace settings</button></div>}</div>
    <nav className="main-nav"><p className="nav-label">Workspace</p>{nav.slice(0, 4).map(([id, icon, label]) => <button key={id} className={`nav-item ${view === id ? "active" : ""}`} aria-current={view === id ? "page" : undefined} onClick={() => navigate(id)}><span className="nav-icon">{icon}</span>{label}{id === "contacts" && <span className="nav-count">{count}</span>}{id === "calendar" && <span className="nav-dot" />}</button>)}<p className="nav-label">Tools</p>{nav.slice(4).map(([id, icon, label]) => <button key={id} className={`nav-item ${view === id ? "active" : ""}`} aria-current={view === id ? "page" : undefined} onClick={() => navigate(id)}><span className="nav-icon">{icon}</span>{label}</button>)}</nav>
    <div className="sidebar-bottom"><div className="usage-card"><div className="usage-top"><span>Daily sending</span><strong>{stats?.daily_sent ?? 0} / {stats?.daily_limit ?? 20}</strong></div><div className="progress"><span style={{ width: `${Math.min(100, ((stats?.daily_sent ?? 0) / (stats?.daily_limit || 20)) * 100)}%` }} /></div><p>Based on messages sent today</p></div><div className="menu-anchor profile-menu-anchor"><button className="profile-row" onClick={() => { setProfileMenuOpen((open) => !open); setWorkspaceMenuOpen(false); }} aria-expanded={profileMenuOpen} aria-haspopup="menu"><Avatar /><span className="profile-copy"><strong>Krishna Bhatia</strong><span>Free plan</span></span></button>{profileMenuOpen && <div className="sidebar-menu profile-menu" role="menu"><button onClick={() => navigate("settings")} role="menuitem">Settings</button><button onClick={() => setProfileMenuOpen(false)} role="menuitem">Close menu</button></div>}</div></div>
  </aside>;
}

function Header({ view, toast }) {
  const pageTitle = view === "ai" ? "AI assistant" : view === "resume" ? "Resume profile" : view[0].toUpperCase() + view.slice(1);
  return <header className="topbar"><div className="mobile-brand"><img className="brand-logo" src={logo} alt="Follow Up Fox" /></div><div className="breadcrumb"><span>Workspace</span><b>/</b><strong>{pageTitle}</strong></div><div className="topbar-actions"><button className="icon-button notification-button" onClick={() => toast("You have no new notifications")} aria-label="View notifications">♢<span /></button><Avatar /></div></header>;
}

function Heading({ eyebrow, title, subtitle, action }) {
  return <div className="page-heading"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p className="subtitle">{subtitle}</p></div>{action}</div>;
}

function Status({ value }) { return <span className={`status ${value}`}>{statusText(value)}</span>; }

function MessageList({ messages, contacts }) {
  if (!messages.length) return <div className="empty-state">No outreach conversations yet.</div>;
  return <div className="outreach-list">{messages.map((message, index) => { const contact = contacts.find((item) => item.id === message.contactId); return contact && <div className="outreach-row" key={`${message.contactId}-${index}`}><div className="contact-initials">{initials(contact)}</div><div className="outreach-details"><strong>{contact.first} {contact.last}<span className="company-note"> · {contact.company}</span></strong><span>{message.preview}</span></div><Status value={message.status} /><span className="row-time">{message.time}</span></div>; })}</div>;
}

function Overview({ contacts, messages, stats, setView, openDraft }) {
  const [upcomingFollowUps, setUpcomingFollowUps] = useState(null);
  const [followUpError, setFollowUpError] = useState("");
  useEffect(() => {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const end = new Date(start);
    end.setDate(end.getDate() + 30);
    api.calendarEvents(start.toISOString(), end.toISOString()).then((events) => {
      const today = localDateKey(new Date());
      const dueToday = events.filter((event) => localDateKey(event.start) === today);
      const later = events.filter((event) => localDateKey(event.start) !== today).slice(0, 3);
      setUpcomingFollowUps([...dueToday, ...later]);
      setFollowUpError("");
    }).catch((error) => {
      setUpcomingFollowUps([]);
      setFollowUpError(error.message);
    });
  }, []);
  const todayFollowUps = (upcomingFollowUps || []).filter(
    (event) => localDateKey(event.start) === localDateKey(new Date()),
  );
  const activity = stats?.activity || [];
  const maxActivity = Math.max(1, ...activity.map((day) => day.count));
  const breakdown = stats?.breakdown || { replied: 0, awaiting: 0, drafts: 0, no_response: 0 };
  const breakdownItems = [
    { label: "Replied", count: breakdown.replied, color: "var(--purple)" },
    { label: "Awaiting reply", count: breakdown.awaiting, color: "#63bd96" },
    { label: "No response", count: breakdown.no_response, color: "#dcd8e1" },
    { label: "Drafts", count: breakdown.drafts, color: "#a89be5" },
  ];
  const breakdownTotal = breakdownItems.reduce((total, item) => total + item.count, 0);
  let breakdownAngle = 0;
  const donutStops = breakdownItems.map((item) => {
    const start = breakdownAngle;
    breakdownAngle += breakdownTotal ? item.count / breakdownTotal * 360 : 0;
    return `${item.color} ${start}deg ${breakdownAngle}deg`;
  });
  const donutBackground = breakdownTotal
    ? `conic-gradient(${donutStops.join(", ")})`
    : "conic-gradient(#dcd8e1 0deg 360deg)";
  const graphPath = activity.length
    ? activity.map((day, index) => `${index ? "L" : "M"} ${(index / Math.max(1, activity.length - 1)) * 640} ${200 - (day.count / maxActivity) * 180}`).join(" ")
    : "M0 200 L640 200";
  const graphArea = `${graphPath} V220 H0Z`;
  return <section className="view active-view"><Heading title={<>Hello, Krishna <span className="wave">✦</span></>} subtitle="Keep your momentum going. Try and fail, but never fail to try!" action={<div className="heading-actions"><button className="secondary-button" onClick={() => setView("resume")}>Find by resume</button><button className="secondary-button" onClick={() => setView("contacts")}>Find by contact</button><button className="primary-button" onClick={() => openDraft(contacts[0]?.id)}><span>+</span> New outreach</button></div>} />
    {!followUpError && upcomingFollowUps !== null && <div className={`calendar-notification ${todayFollowUps.length ? "" : "no-follow-up-notification"}`} role="status" aria-live="polite">
      <div>
        <strong>{todayFollowUps.length ? "Follow-up due today" : "No follow-up today"}</strong>
        {todayFollowUps.length
          ? todayFollowUps.map((event) => <span key={event.id}>{event.title}</span>)
          : <span>You’re all caught up.</span>}
      </div>
      {todayFollowUps.filter((event) => event.contact_id && event.outreach_id).map((event) => (
        <button className="primary-button" key={event.id} onClick={() => openDraft(event.contact_id, null, event.outreach_id)}>
          Follow up →
        </button>
      ))}
    </div>}
    <article className="home-hero"><div className="hero-copy"><p className="hero-eyebrow">A BETTER KIND OF FOLLOW-THROUGH</p><h2>Good outreach<br /><em>sounds like you.</em></h2><p className="hero-description">Keep the right conversations moving, without losing your voice or your judgment along the way.</p><div className="hero-actions"><button className="primary-button" onClick={() => openDraft(contacts[0]?.id)}>Start a thoughtful draft <span>→</span></button><button className="hero-link" onClick={() => setView("outreach")}>See your conversations <span>↗</span></button></div></div><div className="hero-preview"><div className="preview-glow" /><div className="preview-top"><span className="preview-label">FOLLOW-UP FOX</span><span className="preview-status"><i /> Ready for your review</span></div><div className="preview-message"><div className="preview-avatar">AS</div><div><strong>Ananya Sharma · Product Designer</strong><span>Re: A thought on product craft</span></div></div><div className="preview-body">“I’d love to hear how your team is thinking about the next chapter of product craft.”</div><div className="preview-footer"><span>AI-assisted draft</span><button onClick={() => openDraft(contacts[0]?.id)}>Review draft <span>→</span></button></div></div></article>
    <section className="discovery-section"><div className="discovery-heading"><div><p className="eyebrow">GET STARTED IN SECONDS</p><h2>Find your next opportunity</h2><p>Choose how you want Follow Up Fox to help you today.</p></div></div><div className="discovery-grid"><button className="discovery-card discovery-resume" onClick={() => setView("resume")}><span className="discovery-icon">▤</span><span className="discovery-copy"><strong>Find by resume</strong><span>Upload your resume and discover employers that match your experience.</span></span><span className="discovery-arrow">→</span></button><button className="discovery-card discovery-contact" onClick={() => setView("contacts")}><span className="discovery-icon">♙</span><span className="discovery-copy"><strong>Find by contact</strong><span>Search your network, save the right people, and start a thoughtful conversation.</span></span><span className="discovery-arrow">→</span></button></div></section>
    <div className="promise-grid"><article className="promise-card"><div className="promise-icon purple">✦</div><div><strong>AI helps you find the first words</strong><span>Start with a thoughtful draft, shaped around the person you're reaching out to.</span></div></article><article className="promise-card"><div className="promise-icon orange">✓</div><div><strong>You approve every message before it sends</strong><span>Your voice and judgment stay in the driver's seat.</span></div></article><article className="promise-card"><div className="promise-icon green">↗</div><div><strong>Replies and next steps stay in view</strong><span>Know who to follow up with and what to do next.</span></div></article></div>
    <div className="stat-grid">{[["↗", "purple", "Total outreach", stats?.companies_applied ?? 0, "Live data"], ["✉", "green", "Response rate", `${stats?.response_rate ?? 0}%`, "Live data"], ["◷", "orange", "Awaiting reply", stats?.awaiting_reply ?? 0, "Live data"], ["♧", "blue", "Positive replies", stats?.companies_responded ?? 0, "Live data"]].map(([icon, color, label, value, change]) => <article className="stat-card" key={label}><div className={`stat-icon ${color}`}>{icon}</div><div className="stat-label">{label}</div><strong>{value}</strong><div className="stat-change neutral">{change}</div></article>)}</div>
    <div className="content-grid"><article className="panel activity-panel"><div className="panel-heading"><div><h2>Outreach activity</h2><p>Your sent messages over the last 30 days</p></div><select><option>Last 30 days</option></select></div><div className="chart-wrap"><div className="chart-y"><span>{maxActivity}</span><span>{Math.round(maxActivity * .75)}</span><span>{Math.round(maxActivity * .5)}</span><span>{Math.round(maxActivity * .25)}</span><span>0</span></div><div className="chart"><div className="grid-lines"><i /><i /><i /><i /><i /></div><svg viewBox="0 0 640 220" preserveAspectRatio="none"><defs><linearGradient id="activity-area" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--chart-line)" stopOpacity=".3" /><stop offset="100%" stopColor="var(--chart-line)" stopOpacity="0" /></linearGradient></defs><path className="area" style={{ fill: "url(#activity-area)" }} d={graphArea} /><path className="line" style={{ stroke: "var(--chart-line)", fill: "none" }} d={graphPath} /></svg><div className="chart-x"><span>{activity[0]?.date || "—"}</span><span>{activity[Math.floor(activity.length * .25)]?.date || "—"}</span><span>{activity[Math.floor(activity.length * .5)]?.date || "—"}</span><span>{activity[Math.floor(activity.length * .75)]?.date || "—"}</span><span>{activity[activity.length - 1]?.date || "—"}</span></div></div></div></article><article className="panel tasks-panel"><div className="panel-heading"><div><h2>Upcoming follow-ups</h2><p>Automatically scheduled after you send outreach</p></div><button className="text-button" onClick={() => setView("calendar")}>View calendar →</button></div><div className="task-list">{followUpError ? <div className="empty-state error-state">{followUpError}</div> : upcomingFollowUps === null ? <div className="empty-state">Loading follow-ups...</div> : upcomingFollowUps.length ? upcomingFollowUps.map((item) => <div className="task-item" key={item.id}><div className="task-check empty">•</div><div><strong>{item.title}</strong><span>{formatTaskDue(item.start)}</span></div>{item.outreach_id && item.contact_id ? <button className="text-button" onClick={() => openDraft(item.contact_id, null, item.outreach_id)}>Follow up</button> : <span className="task-time">Scheduled</span>}</div>) : <div className="empty-state">No upcoming follow-ups scheduled.</div>}</div></article></div>
    <div className="content-grid lower-grid"><article className="panel recent-panel"><div className="panel-heading"><div><h2>Recent outreach</h2><p>Stay close to every conversation</p></div><button className="text-button" onClick={() => setView("outreach")}>View all →</button></div><MessageList messages={messages} contacts={contacts} /></article><article className="panel response-panel"><div className="panel-heading"><div><h2>Response breakdown</h2><p>How people are engaging</p></div><span className="period">Live data</span></div><div className="donut-row"><div className="donut" role="img" aria-label={`Outreach breakdown: ${breakdownItems.map((item) => `${item.label} ${item.count}`).join(", ")}`} style={{ background: donutBackground }}><div><strong>{stats?.response_rate ?? 0}%</strong><span>response</span></div></div><div className="legend">{breakdownItems.map((item) => <span key={item.label}><i className="legend-dot" style={{ backgroundColor: item.color }} />{item.label}<b>{item.count}</b></span>)}</div></div></article></div>
    <div className="visitor-summary"><strong>{stats?.visitors ?? 0}</strong><span>workspace visitors in the last 30 days</span></div>
  </section>;
}

function Contacts({ contacts, connectEmployer, goToResume }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [companyQuery, setCompanyQuery] = useState("");
  const [companies, setCompanies] = useState([]);
  const [companyError, setCompanyError] = useState("");
  const [companyLoading, setCompanyLoading] = useState(false);
  const [connectingId, setConnectingId] = useState(null);
  const categoryContacts = useMemo(() => contacts.filter((contact) => contact.category === "employer"), [contacts]);
  const filtered = useMemo(() => categoryContacts.filter((contact) => {
    const matchesQuery = `${contact.first} ${contact.last} ${contact.company} ${contact.role}`.toLowerCase().includes(query.toLowerCase());
    const matchesFilter = filter === "all" || contact.status === filter;
    return matchesQuery && matchesFilter;
  }), [categoryContacts, query, filter]);
  const searchCompanies = async (event) => {
    event.preventDefault();
    setCompanyLoading(true);
    setCompanyError("");
    try {
      const payload = await api.employers();
      setCompanies(payload.employers || []);
    } catch (error) {
      setCompanies([]);
      setCompanyError(error.message);
    } finally {
      setCompanyLoading(false);
    }
  };
  const companyResults = companies.filter((company) => company.company.toLowerCase().includes(companyQuery.toLowerCase()));
  const connect = async (employer) => {
    const contactId = employer.contact_id;
    setConnectingId(contactId);
    try {
      await connectEmployer(employer);
    } finally {
      setConnectingId(null);
    }
  };
  return (
    <section className="view active-view">
      <Heading
        eyebrow="Your network"
        title="Contacts"
        subtitle="Find and connect with employer contacts."
        action={
          <button className="secondary-button" onClick={goToResume}>Find via resume</button>
        }
      />
      <article className="panel company-finder-panel">
          <div className="company-finder-heading">
            <div><h2>Find company contacts</h2><p>Connect with hiring contacts to start a conversation.</p></div>
          </div>
          <form className="company-finder-form" onSubmit={searchCompanies}>
            <input value={companyQuery} onChange={(event) => setCompanyQuery(event.target.value)} placeholder="Search by company name..." />
            <button className="primary-button" type="submit" disabled={companyLoading}>{companyLoading ? "Searching..." : "Find contacts"}</button>
          </form>
          {companyError && <p className="empty-state error-state" role="alert">Unable to find company contacts: {companyError}</p>}
          {companies.length > 0 && (
            <div className="company-search-results">
              {companyResults.map((company) => (
                <div className="company-search-row" key={company.contact_id}>
                  <div className="company-search-mark">{company.company.charAt(0)}</div>
                  <div>
                    <strong>{company.company}</strong>
                    <span>{company.contact_name || "Hiring team"} · {company.contact_email || "No public email listed"}{company.contact_linkedin ? " · LinkedIn available" : ""}</span>
                  </div>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => connect(company)}
                    disabled={contacts.some((contact) => contact.id === company.contact_id && contact.connected) || connectingId === company.contact_id}
                  >
                    {connectingId === company.contact_id ? "Please wait..." : contacts.some((contact) => contact.id === company.contact_id && contact.connected) ? "Connected" : "Connect"}
                  </button>
                </div>
              ))}
              {companyResults.length === 0 && <p className="company-empty">No companies match “{companyQuery}”.</p>}
            </div>
          )}
      </article>
      <article className="panel contacts-panel">
        <div className="toolbar">
          <div className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search employer contacts..." /></div>
          <button className={`filter-button ${filter === "all" ? "active" : ""}`} onClick={() => setFilter("all")}>All contacts <span>{categoryContacts.length}</span></button>
          <button className={`filter-button ${filter === "awaiting" ? "active" : ""}`} onClick={() => setFilter("awaiting")}>Needs follow-up</button>
          <button className={`filter-button ${filter === "replied" ? "active" : ""}`} onClick={() => setFilter("replied")}>Replied</button>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>CONTACT</th><th>COMPANY</th><th>ROLE</th><th>STATUS</th><th>LAST CONTACT</th><th /></tr></thead>
            <tbody>
              {filtered.map((contact) => (
                <tr key={contact.id}>
                  <td><div className="person-cell"><div className="contact-initials">{initials(contact)}</div><span>{contact.first} {contact.last}{!contact.email && contact.linkedin_url ? " · LinkedIn" : ""}</span></div></td>
                  <td className="company-cell">{contact.company}{contact.location && <small className="contact-subdetail">{contact.location}</small>}</td>
                  <td>{contact.role}{contact.hiring_roles?.length > 0 && <small className="contact-subdetail">Hiring: {contact.hiring_roles.join(", ")}</small>}</td>
                  <td><Status value={contact.status} /></td>
                  <td>{contact.lastContact}</td>
                  <td>
                    <button className="secondary-button" disabled={contact.connected || connectingId === contact.id} onClick={() => connect(contact)}>
                      {connectingId === contact.id ? "Please wait..." : contact.connected ? "Connected" : "Connect"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!filtered.length && <div className="empty-state">No employer contacts yet.</div>}
      </article>
    </section>
  );
}

function Outreach({ contacts, messages, openDraft }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const filtered = messages.filter((message) => {
    const contact = contacts.find((item) => item.id === message.contactId);
    return contact && (filter === "all" || message.status === filter) && `${contact.first} ${contact.last} ${contact.company} ${message.preview}`.toLowerCase().includes(query.toLowerCase());
  });
  return <section className="view active-view"><Heading eyebrow="Your conversations" title="Outreach" subtitle="Every message, draft, and reply in one calm place." action={<button className="primary-button" onClick={() => openDraft(contacts[0]?.id)}><span>+</span> New outreach</button>} /><article className="panel"><div className="toolbar"><div className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search outreach..." /></div><button className={`filter-button ${filter === "all" ? "active" : ""}`} onClick={() => setFilter("all")}>All conversations</button><button className={`filter-button ${filter === "awaiting" ? "active" : ""}`} onClick={() => setFilter("awaiting")}>Awaiting reply</button><button className={`filter-button ${filter === "replied" ? "active" : ""}`} onClick={() => setFilter("replied")}>Replied</button></div><MessageList messages={filtered} contacts={contacts} /></article></section>;
}

function Calendar({ openDraft }) {
  const [monthOffset, setMonthOffset] = useState(0);
  const [events, setEvents] = useState([]);
  const [busy, setBusy] = useState(true);
  const [loadError, setLoadError] = useState("");
  const baseMonth = new Date(new Date().getFullYear(), new Date().getMonth() + monthOffset, 1);
  const month = baseMonth.toLocaleString("en-US", { month: "long", year: "numeric" });
  useEffect(() => {
    const start = new Date(baseMonth.getFullYear(), baseMonth.getMonth(), 1);
    const end = new Date(baseMonth.getFullYear(), baseMonth.getMonth() + 1, 1);
    setBusy(true);
    setLoadError("");
    api.calendarEvents(start.toISOString(), end.toISOString()).then(setEvents).catch((error) => {
      setEvents([]);
      setLoadError(error.message);
    }).finally(() => setBusy(false));
  }, [monthOffset]);
  const daysInMonth = new Date(baseMonth.getFullYear(), baseMonth.getMonth() + 1, 0).getDate();
  const firstDay = (new Date(baseMonth.getFullYear(), baseMonth.getMonth(), 1).getDay() + 6) % 7;
  const days = [...Array.from({ length: firstDay }, (_, index) => ({ day: "", muted: true, key: `before-${index}` })), ...Array.from({ length: daysInMonth }, (_, index) => ({ day: String(index + 1), key: `day-${index}` }))];
  const eventsByDay = events.reduce((groups, event) => {
    const day = localDateKey(event.start);
    if (day) groups[day] = [...(groups[day] || []), event];
    return groups;
  }, {});
  return <section className="view active-view">
    <Heading eyebrow="Plan your next move" title="Calendar" subtitle="Your Follow Up Fox follow-up reminders." />
    <article className="panel calendar-panel">
      <div className="calendar-header"><button className="icon-button" onClick={() => setMonthOffset((value) => value - 1)} aria-label="Previous month">‹</button><h2>{month}</h2><button className="icon-button" onClick={() => setMonthOffset((value) => value + 1)} aria-label="Next month">›</button><span className="calendar-sync">{busy ? "● Loading..." : `● ${events.length} event${events.length === 1 ? "" : "s"}`}</span></div>
      {loadError ? <div className="empty-state error-state">Unable to load your calendar: {loadError}</div> : <><div className="weekdays">{["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].map((day) => <span key={day}>{day}</span>)}</div><div className="calendar-grid">{days.map((item) => { const dateKey = item.day ? `${baseMonth.getFullYear()}-${String(baseMonth.getMonth() + 1).padStart(2, "0")}-${item.day.padStart(2, "0")}` : ""; return <div className={`calendar-day ${item.muted ? "muted" : ""}`} key={item.key}><span>{item.day}</span>{(eventsByDay[dateKey] || []).map((event) => <div className="calendar-event" key={event.id}>{event.title}</div>)}</div>; })}</div>{!busy && !events.length && <div className="empty-state">No follow-ups scheduled for this month. Follow-ups are added automatically after sending outreach.</div>}</>}
    </article>
  </section>;
}

function Profile({ toast, connectEmployer }) {
  const [file, setFile] = useState(null);
  const [result, setResult] = useState({ skills: [], employers: [], level_fit: {} });
  const [profile, setProfile] = useState({ professional_headline: "", about: "" });
  const [targetLevel, setTargetLevel] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const [busy, setBusy] = useState(false);
  const [addingEmployer, setAddingEmployer] = useState(null);
  const score = result.score ?? 0;
  const levelFit = result.level_fit || {};
  const levelFitCounts = result.level_fit_counts || {};

  useEffect(() => {
    api.profile().then(setProfile).catch(() => {});
    api.latestResume().then((saved) => {
      if (saved.analysis_id) setResult(saved);
    }).catch(() => {});
  }, []);

  const updateProfile = (field, value) => setProfile((current) => ({ ...current, [field]: value }));
  const saveProfile = async () => {
    try { await api.updateProfile(profile); toast("Resume details saved"); } catch (error) { toast(error.message); }
  };
  const connectWithEmployer = async (employer) => {
    setAddingEmployer(employer.id);
    try {
      await connectEmployer(employer);
    } finally {
      setAddingEmployer(null);
    }
  };
  const analyze = async (event) => {
    event.preventDefault();
    if (!file) return toast("Choose a PDF, TXT, or Markdown resume first");
    await saveProfile();
    setBusy(true);
    try { setResult(await api.analyzeResume(file)); toast("Resume analyzed and employers matched"); } catch (error) { toast(error.message); } finally { setBusy(false); }
  };
  const matchedEmployers = result.employers.filter((employer) => employer.match_score > 0).length;
  return <section className="view active-view"><Heading eyebrow="AI career matching" title="Resume profile" subtitle="Upload your resume and discover employers that match your skills." /><div className="resume-profile-layout"><article className="panel resume-panel"><div className="resume-intro"><div className="resume-icon">✦</div><div><h2>Resume details</h2><p>Your headline and background are saved with your profile.</p></div></div><form className="resume-details-upload" onSubmit={analyze}><input className="analysis-file-input" type="file" accept=".pdf,.txt,.md" onChange={(event) => setFile(event.target.files?.[0] || null)} /><button className="primary-button" disabled={busy}>{busy ? "Analyzing resume..." : "Analyze my resume"}</button></form><label>Professional headline<input value={profile.professional_headline} onChange={(event) => updateProfile("professional_headline", event.target.value)} placeholder="e.g. Product designer focused on clear workflows" /></label><label>About you<textarea value={profile.about} onChange={(event) => updateProfile("about", event.target.value)} placeholder="Describe your experience, strengths, and goals..." /></label><button type="button" className="secondary-button save-profile-button" onClick={saveProfile}>Save resume details</button>{result.analysis_id && <><div className="skills-heading"><h3>Skills found</h3><span>{result.skills.length} extracted</span></div><div className="skill-chips">{result.skills.map((skill) => <span key={skill}>{skill}</span>)}</div></>}</article><aside className="panel resume-analysis-card">{!result.analysis_id ? <><div className="analysis-card-icon">✦</div><p className="eyebrow">Resume analysis</p><h2>Your analysis will appear here</h2><p className="analysis-description">Upload your resume and click Analyze my resume to see your summary, score, and career-level matching.</p></> : <><div className="analysis-card-icon">✓</div><p className="eyebrow">Resume analysis complete</p><h2>Your resume results</h2><p className="analysis-description">{result.summary || "Analysis completed successfully."}</p><div className="analysis-score-line"><span>Overall match score</span><strong>{score}%</strong></div><div className="analysis-progress"><span style={{ width: `${score}%` }} /></div><div className="level-fit level-fit-four">{[["intern", "Intern"], ["entry", "Entry"], ["mid", "Mid"], ["senior", "Senior"]].map(([key, label]) => <div key={key}><span>{label} · {levelFitCounts[key] || 0} jobs</span><strong>{levelFit[key] == null ? "N/A" : `${levelFit[key]}%`}</strong></div>)}</div></>}</aside></div>{result.analysis_id && <section className="panel improvement-panel"><div className="skills-heading"><h3>Choose your target level</h3><span>Personalize your next steps</span></div><select value={targetLevel} onChange={(event) => setTargetLevel(event.target.value)}><option value="">Select a target level</option><option value="intern">Intern</option><option value="entry">Entry level</option><option value="mid">Mid level</option><option value="senior">Senior level</option></select>{targetLevel && <>{levelFit[targetLevel] == null ? <p className="target-note">Not enough matching jobs to score this level yet.</p> : <p className="target-note">Your {targetLevel} target score is <strong>{levelFit[targetLevel]}%</strong> from {levelFitCounts[targetLevel]} matching jobs.</p>}<h3>Ways to improve</h3><ul className="improvement-list">{(result.improvement_actions || []).map((action) => <li key={action}>{action}</li>)}</ul><button type="button" className="secondary-button" onClick={() => setShowDetails((value) => !value)}>{showDetails ? "Hide detailed guidance" : "How can I improve in detail?"}</button>{showDetails && <ul className="improvement-list detailed">{(result.detailed_improvements || []).map((action) => <li key={action}>{action}</li>)}</ul>}</>}</section>}{result.analysis_id && result.employers.length > 0 && <section className="employer-results"><div className="skills-heading"><h3>All companies</h3><span>{matchedEmployers} matched · {result.employers.length} total</span></div>  <div className="employer-grid">{result.employers.map((employer) => <article className={`employer-card ${employer.match_score ? "" : "employer-card-unmatched"}`} key={employer.id}><div className="employer-card-mark" aria-hidden="true">{employer.company.charAt(0)}</div><div className="employer-card-content"><div className="employer-card-heading"><div><h3>{employer.hiring_roles[0] || "Open role"}</h3><span className="employer-company">{employer.company}</span></div><div className="employer-match">{employer.match_score ? `${employer.match_score}%` : "—"}<small>{employer.match_score ? "skill match" : "not matched"}</small></div></div><div className="employer-skill-chips">{employer.skills.map((skill) => <span className={result.skills.includes(skill.toLowerCase()) ? "skill-hit" : ""} key={skill}>{skill}</span>)}</div><p className="employer-detail">{employer.industry}<br /><a href={employer.website} target="_blank" rel="noreferrer">{employer.website}</a></p><div className="employer-contact">{employer.location && <span>{employer.location}</span>}<span>Hiring roles: {employer.hiring_roles.join(", ")}</span><span>{employer.contact_name || "Hiring team"}</span><code>{employer.contact_email || "No public email listed"}</code></div><div className="employer-actions"><a href={employer.contact_linkedin} target="_blank" rel="noreferrer">LinkedIn</a><button type="button" className="primary-button" onClick={() => connectWithEmployer(employer)} disabled={addingEmployer === employer.id || !employer.contact_eligible}>  {addingEmployer === employer.id ? "Please wait..." : "Connect"}</button></div></div></article>)}</div></section>}</section>;
}

function Settings({ toast }) {
  const [values, setValues] = useState({ human_approval_required: true, followup_delay_days: 5, daily_send_limit: 20 });
  const [status, setStatus] = useState("loading");
  const [saving, setSaving] = useState(false);
  useEffect(() => { api.settings().then((data) => { setValues(data); setStatus("ready"); }).catch(() => setStatus("error")); }, []);
  const save = async (next) => {
    setValues(next);
    setSaving(true);
    try { setValues(await api.updateSettings(next)); toast("Settings saved"); } catch (error) { toast(error.message); } finally { setSaving(false); }
  };
  if (status === "loading") return <section className="view active-view"><Heading eyebrow="Workspace preferences" title="Settings" subtitle="Loading your preferences..." /><article className="panel empty-state">Loading settings...</article></section>;
  if (status === "error") return <section className="view active-view"><Heading eyebrow="Workspace preferences" title="Settings" subtitle="Make Follow Up Fox work the way you do." /><article className="panel empty-state error-state">Unable to load settings. Refresh and try again.</article></section>;
  return <section className="view active-view"><Heading eyebrow="Workspace preferences" title="Settings" subtitle="Make Follow Up Fox work the way you do." /><article className="panel settings-panel"><div className="setting-row"><div><h3>Human approval required</h3><p>Never send an AI-generated message without your review.</p></div><label className="toggle"><input type="checkbox" checked={values.human_approval_required} disabled={saving} onChange={(event) => save({ ...values, human_approval_required: event.target.checked })} /><span /></label></div><div className="setting-row"><div><h3>Default follow-up delay</h3><p>Set when a follow-up reminder should appear.</p></div><select value={values.followup_delay_days} disabled={saving} onChange={(event) => save({ ...values, followup_delay_days: Number(event.target.value) })}><option value={3}>3 days</option><option value={5}>5 days</option><option value={7}>7 days</option></select></div><div className="setting-row"><div><h3>Daily send limit</h3><p>Keep your outreach thoughtful and safe.</p></div><select value={values.daily_send_limit} disabled={saving} onChange={(event) => save({ ...values, daily_send_limit: Number(event.target.value) })}><option value={10}>10 emails</option><option value={20}>20 emails</option><option value={30}>30 emails</option></select></div></article></section>;
}

function AiAssistant({ toast }) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([
    { role: "assistant", text: "Hi! I can help with outreach strategy, contacts, follow-ups, email wording, resumes, career planning, and workspace metrics." },
  ]);
  const [busy, setBusy] = useState(false);
  const ask = async (event) => {
    event.preventDefault();
    const text = question.trim();
    if (!text || busy) return;
    setQuestion("");
    setMessages((current) => [...current, { role: "user", text }]);
    setBusy(true);
    try {
      const history = messages.slice(-10).map((message) => ({
        role: message.role,
        content: message.text,
      }));
      const result = await api.query(text, history);
      setMessages((current) => [...current, { role: "assistant", text: result.answer }]);
    } catch (error) {
      toast(error.message);
    } finally {
      setBusy(false);
    }
  };
  return <section className="view active-view"><Heading eyebrow="AI workspace assistant" title="Ask Follow Up Fox" subtitle="Get grounded answers from your contacts and outreach workspace." /><article className="panel ai-chat-panel"><div className="ai-chat-messages">{messages.map((message, index) => <div className={`ai-message ${message.role}`} key={`${message.role}-${index}`}><span>{message.role === "assistant" ? "✦" : "You"}</span><div className="ai-message-content">{message.role === "assistant" ? <FormattedAnswer text={message.text} /> : <p>{message.text}</p>}</div></div>)}{busy && <div className="ai-message assistant"><span>✦</span><p>Thinking...</p></div>}</div><form className="ai-chat-form" onSubmit={ask}><input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask about your outreach, contacts, or next step..." /><button className="primary-button" type="submit" disabled={busy || !question.trim()}>Ask AI</button></form></article></section>;
}

function AppContent() {
  const navigate = useNavigate();
  const location = useLocation();
  const routeViews = { "/": "overview", "/contacts": "contacts", "/outreach": "outreach", "/calendar": "calendar", "/resume": "resume", "/ai": "ai", "/settings": "settings" };
  const view = routeViews[location.pathname] || "overview";
  const setView = (nextView) => navigate(nextView === "overview" ? "/" : `/${nextView}`);
  const [contacts, setContacts] = useState([]);
  const [messages, setMessages] = useState([]);
  const [stats, setStats] = useState(null);
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [modal, setModal] = useState(null);
  const [draftContact, setDraftContact] = useState(null);
  const [draftSuggestion, setDraftSuggestion] = useState(null);
  const [draftError, setDraftError] = useState("");
  const [draftFollowUpOutreachId, setDraftFollowUpOutreachId] = useState(null);
  const [linkedinLoading, setLinkedinLoading] = useState(false);
  const [linkedinError, setLinkedinError] = useState("");
  const [toastMessage, setToastMessage] = useState("");
  const toast = (message) => { setToastMessage(message); window.setTimeout(() => setToastMessage(""), 2600); };
  const reloadData = async () => {
    const [nextContacts, nextMessages, nextStats] = await Promise.all([api.contacts(), api.outreach(), api.stats()]);
    const contactStatus = new Map(nextMessages.map((message) => [message.contact_id, message.status === "sent" ? "awaiting" : message.status]));
    const refreshedContacts = nextContacts.map((contact) => ({ id: contact.id, first: contact.first_name, last: contact.last_name, company: contact.company, role: contact.role, email: contact.email, email_eligible: contact.email_eligible, email_eligibility_reason: contact.email_eligibility_reason, linkedin_url: contact.linkedin_url, notes: contact.notes, category: contact.category || "my", location: contact.location, hiring_roles: contact.hiring_roles || [], connected: contact.connected || false, status: contactStatus.get(contact.id) || "draft", lastContact: "Not contacted" }));
    setContacts(refreshedContacts);
    setMessages(nextMessages.map((message) => ({ outreachId: message.id, contactId: message.contact_id, preview: message.subject, time: message.sent_at ? new Date(message.sent_at).toLocaleDateString() : "Draft", outreachStatus: message.status, status: message.status === "sent" ? "awaiting" : message.status })));
    setStats(nextStats);
    return refreshedContacts;
  };
  useEffect(() => {
    api.me().then(async (nextUser) => { setUser(nextUser); await reloadData(); }).catch((error) => setLoadError(error.message)).finally(() => setLoading(false));
  }, []);
  const openDraft = async (id, contactOverride = null, followUpOutreachId = null) => { const contact = contactOverride || contacts.find((candidate) => candidate.id === id) || contacts[0]; if (!contact) return toast("Add a contact before starting outreach"); setDraftContact(contact); setDraftSuggestion(null); setDraftError(""); setDraftFollowUpOutreachId(followUpOutreachId); setModal("draft"); try { setDraftSuggestion(await api.suggestDraft(contact.id, "", "", followUpOutreachId)); } catch (error) { setDraftError(error.message); } };
  const connectWithLinkedIn = async (contact) => {
    setDraftContact(contact);
    setDraftSuggestion(null);
    setLinkedinError("");
    setLinkedinLoading(true);
    setModal("linkedin");
    try {
      await api.connectContact(contact.id);
      setDraftSuggestion(await api.suggestLinkedinDraft(contact.id));
      try {
        await reloadData();
      } catch (error) {
        toast(`Message generated, but contacts could not be refreshed: ${error.message}`);
      }
    } catch (error) {
      setLinkedinError(error.message);
    } finally {
      setLinkedinLoading(false);
    }
  };
  const connectEmployer = async (employer) => {
    const contactId = employer.contact_id ?? employer.id;
    if (!contactId) {
      toast("Employer contact is unavailable");
      return;
    }
    const existingContact = contacts.find((contact) => contact.id === contactId);
    const email = employer.contact_email ?? employer.email ?? existingContact?.email;
    const linkedinUrl = employer.contact_linkedin ?? employer.linkedin_url ?? existingContact?.linkedin_url;
    const emailEligible = employer.email_eligible ?? existingContact?.email_eligible ?? Boolean(email);
    if (!emailEligible) {
      if (!linkedinUrl) {
        toast("No email or LinkedIn profile is available for this contact.");
        return;
      }
      const name = (employer.contact_name || "").trim().split(/\s+/);
      await connectWithLinkedIn({
        ...existingContact,
        id: contactId,
        first: existingContact?.first || name[0] || "there",
        last: existingContact?.last || name.slice(1).join(" "),
        company: employer.company || existingContact?.company || "",
        role: employer.role || existingContact?.role || employer.hiring_roles?.[0] || "hiring team",
        email,
        linkedin_url: linkedinUrl,
      });
      return;
    }
    try {
      await api.connectContact(contactId);
      const refreshedContacts = await reloadData();
      const connectedContact = refreshedContacts.find((contact) => contact.id === contactId);
      if (!connectedContact) {
        toast("Contact connected, but it could not be loaded. Refresh and try again.");
        return;
      }
      toast("Contact connected. Please wait while your AI draft opens.");
      await openDraft(contactId, connectedContact);
    } catch (error) {
      toast(error.message);
    }
  };
  const approve = async (subject, body, attachResume = false) => { try { const followUp = Boolean(draftSuggestion?.follow_up); const outreach = followUp ? { id: draftSuggestion.outreach_id } : await api.createOutreach(draftContact.id, subject); await api.sendOutreach(outreach.id, subject, body, attachResume, followUp); await reloadData(); setModal(null); toast(followUp ? `Follow-up sent to ${draftContact.first}` : `Message approved and sent to ${draftContact.first}`); } catch (error) { toast(error.message); } };
  if (loading) return <div className="auth-screen"><img className="auth-logo" src={logo} alt="Follow Up Fox" /><h1>Loading your workspace</h1><p>Connecting to Follow Up Fox...</p></div>;
  if (!user) return <LandingPage apiUrl={api.url} loadError={loadError} />;
  const body = view === "overview" ? <Overview contacts={contacts} messages={messages} stats={stats} setView={setView} openDraft={openDraft} /> : view === "contacts" ? <Contacts contacts={contacts} connectEmployer={connectEmployer} goToResume={() => setView("resume")} /> : view === "outreach" ? <Outreach contacts={contacts} messages={messages} openDraft={openDraft} /> : view === "calendar" ? <Calendar openDraft={openDraft} /> : view === "resume" ? <Profile toast={toast} connectEmployer={connectEmployer} /> : view === "ai" ? <AiAssistant toast={toast} /> : <Settings toast={toast} />;
  return <div className="app-shell"><Sidebar view={view} setView={setView} count={contacts.length} stats={stats} /><main className="main-content"><Header view={view} toast={toast} />{body}</main>{modal === "draft" && <DraftModal contact={draftContact} suggestion={draftSuggestion} error={draftError} retry={() => openDraft(draftContact.id, draftContact, draftFollowUpOutreachId)} close={() => setModal(null)} approve={approve} toast={toast} />}{modal === "linkedin" && <LinkedInModal contact={draftContact} suggestion={draftSuggestion} loading={linkedinLoading} error={linkedinError} retry={() => connectWithLinkedIn(draftContact)} close={() => setModal(null)} toast={toast} />}{toastMessage && <div className="toast show"><span className="toast-icon">✓</span>{toastMessage}</div>}</div>;
}

function LandingPage({ apiUrl, loadError }) {
  const steps = [
    ["01", "Add the right people", "Keep your contacts, context, and goals together in one calm workspace."],
    ["02", "Write with your voice", "Get a thoughtful starting point that feels personal, never robotic."],
    ["03", "Follow through well", "Track replies, reminders, and next steps so good conversations keep moving."],
  ];
  return <main className="landing-page"><nav className="landing-nav"><img src={logo} alt="Follow Up Fox" /><div className="landing-nav-right"><span>Human-approved outreach</span><a className="landing-nav-link" href={`${apiUrl}/auth/google/login`}>Sign in <span>→</span></a></div></nav><section className="landing-hero"><div className="landing-hero-copy"><p className="hero-eyebrow">A BETTER KIND OF FOLLOW-THROUGH</p><h1>Good outreach<br /><em>sounds like you.</em></h1><p>Keep the right conversations moving, without losing your voice or your judgment along the way.</p><a className="landing-cta" href={`${apiUrl}/auth/google/login`}>Get started with Google <span>→</span></a></div><div className="landing-art"><div className="landing-card"><div className="landing-card-top"><span>FOLLOW UP FOX</span><i>● YOUR WORKSPACE</i></div><div className="landing-card-title">Stay close to<br /><em>every conversation.</em></div><div className="landing-card-progress"><span /><span /><span /></div><div className="landing-conversation"><div className="preview-avatar">AS</div><div><strong>Ananya Sharma · Product Designer</strong><span>Ready for a thoughtful follow-up</span></div><b>↗</b></div><div className="landing-line wide" /><div className="landing-line" /><div className="landing-card-footer"><span>AI-assisted draft</span><strong>Review before sending →</strong></div></div></div></section><section className="landing-intro"><p className="hero-eyebrow">MADE FOR MEANINGFUL CONNECTIONS</p><h2>Everything you need to<br /><em>stay in the conversation.</em></h2><p>Follow Up Fox gives you the structure to be consistent and the space to be human.</p></section><section className="landing-steps">{steps.map(([number, title, description]) => <article key={number}><span>{number}</span><div><h3>{title}</h3><p>{description}</p></div></article>)}</section><footer className="landing-footer"><img src={logo} alt="Follow Up Fox" /><span>Follow up. Get replies.</span><a className="footer-cta" href={`${apiUrl}/auth/google/login`}>Start following through <span>→</span></a></footer></main>;
}

function LinkedInModal({ contact, suggestion, loading, error, retry, close, toast }) {
  const [message, setMessage] = useState("");
  useEffect(() => { if (suggestion?.body) setMessage(suggestion.body); }, [suggestion]);
  const copy = async () => { await navigator.clipboard.writeText(message); toast("LinkedIn message copied"); };
  return <div className="modal-backdrop open" onClick={(event) => event.target === event.currentTarget && close()}><div className="modal draft-modal"><button className="modal-close" onClick={close}>×</button><p className="eyebrow">LinkedIn connection</p><h2>Email unavailable</h2><p className="modal-subtitle">No email is available for {contact.first} at {contact.company}. Open their LinkedIn profile to connect.</p>{loading ? <div className="empty-state" role="status" aria-live="polite">Please wait while AI prepares your LinkedIn message...</div> : error ? <div className="empty-state error-state" role="alert">{error}</div> : <><p className="modal-subtitle">Your AI-generated message is ready. Review or edit it before copying.</p><textarea value={message} onChange={(event) => setMessage(event.target.value)} /></>}<div className="draft-footer">{contact.linkedin_url && <a className="secondary-button" href={contact.linkedin_url} target="_blank" rel="noreferrer">Open LinkedIn profile</a>}{error ? <button className="primary-button" onClick={retry}>Try again</button> : <button className="primary-button" onClick={copy} disabled={loading || !suggestion?.body}>Copy message</button>}</div></div></div>;
}

function DraftModal({ contact, suggestion, error, retry, close, approve, toast }) {
  const [message, setMessage] = useState(`Hi ${contact.first},\n\nI've been following ${contact.company}'s work and was especially drawn to the way your team makes complex products feel effortless. I’m a product designer focused on thoughtful systems and would love to learn how you're approaching the next chapter.\n\nWould you be open to a short conversation sometime next week?\n\nBest,\nKrishna`);
  const [subject, setSubject] = useState(`A thought on ${contact.company}'s approach to product craft`);
  const [regenerating, setRegenerating] = useState(false);
  const [resumeAvailable, setResumeAvailable] = useState(false);
  const [resumeFilename, setResumeFilename] = useState("");
  const [attachResume, setAttachResume] = useState(false);
  useEffect(() => {
    setResumeAvailable(false);
    setResumeFilename("");
    setAttachResume(false);
  }, [contact.id]);
  useEffect(() => {
    if (suggestion) {
      setSubject(suggestion.subject);
      setMessage(suggestion.body);
      setResumeAvailable(Boolean(suggestion.resume_available));
      setResumeFilename(suggestion.resume_filename || "");
    }
  }, [suggestion]);
  const regenerate = async () => {
    setRegenerating(true);
    try {
      const generated = await api.suggestDraft(contact.id, subject, message);
      setSubject(generated.subject);
      setMessage(generated.body);
      toast("A new draft is ready to review");
    } catch (error) {
      toast(error.message);
    } finally {
      setRegenerating(false);
    }
  };
  const emailEligible = contact.email_eligible === true;
  const unavailableReason = contact.email_eligibility_reason || "No usable email address is available.";
  return (
    <div className="modal-backdrop open" onClick={(event) => event.target === event.currentTarget && close()}>
      <div className="modal draft-modal">
        <button className="modal-close" onClick={close}>×</button>
        <div className="draft-header">
          <div><p className="eyebrow">AI draft assistant</p><h2>{suggestion?.follow_up ? "Draft a follow-up" : "Draft a thoughtful introduction"}</h2></div>
          <div className="sparkle">✦</div>
        </div>
        <div className="draft-context">
          <Avatar contact={contact} small />
          <div><strong>{contact.first} {contact.last}</strong><span>{contact.role} at {contact.company}</span></div>
          <span className="context-chip">Personalized</span>
        </div>
        {!suggestion && (error
          ? <div className="empty-state error-state" role="alert">Unable to generate your AI draft: {error} <button className="text-button" onClick={retry}>Try again</button></div>
          : <div className="empty-state" role="status" aria-live="polite">Please wait while AI generates your message...</div>)}
        <label>Subject<input value={subject} onChange={(event) => setSubject(event.target.value)} disabled={!suggestion} /></label>
        <label>Message<textarea value={message} onChange={(event) => setMessage(event.target.value)} disabled={!suggestion} /></label>
        {resumeAvailable && (
          <>
            <label className="resume-attachment-option" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <input
                type="checkbox"
                style={{ width: "auto", margin: 0 }}
                checked={attachResume}
                onChange={(event) => setAttachResume(event.target.checked)}
              />
              <span>Attach my resume: {resumeFilename}</span>
            </label>
            <a href={`${api.url}/resume/latest/file`} target="_blank" rel="noreferrer">Review saved resume</a>
          </>
        )}
        <div className="draft-footer">
          <span className="approval-note">
            {emailEligible ? "✓ You'll review before anything is sent" : unavailableReason}
          </span>
          <div>
            <button className="secondary-button" onClick={regenerate} disabled={!suggestion || regenerating}>
              {regenerating ? "Generating..." : "↻ Regenerate"}
            </button>
            <button
              className="primary-button"
              onClick={() => approve(subject, message, attachResume)}
              disabled={!suggestion || !emailEligible || regenerating}
              title={emailEligible ? "" : unavailableReason}
            >
              {emailEligible ? suggestion?.follow_up ? "Send follow-up" : "Approve & send" : "Email unavailable"}
            </button>
          </div>
        </div>
        {!emailEligible && contact.linkedin_url && (
          <p className="modal-subtitle">
            You can still contact them through{" "}
            <a href={contact.linkedin_url} target="_blank" rel="noreferrer">LinkedIn</a>.
          </p>
        )}
      </div>
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppContent />} />
        <Route path="/contacts" element={<AppContent />} />
        <Route path="/outreach" element={<AppContent />} />
        <Route path="/calendar" element={<AppContent />} />
        <Route path="/resume" element={<AppContent />} />
        <Route path="/ai" element={<AppContent />} />
        <Route path="/settings" element={<AppContent />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
