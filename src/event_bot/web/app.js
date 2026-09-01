(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
  const researchSessionId = globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
  const DAYS = [
    ["mon", "Пн"], ["tue", "Вт"], ["wed", "Ср"], ["thu", "Чт"],
    ["fri", "Пт"], ["sat", "Сб"], ["sun", "Вс"],
  ];
  const INTERESTS = [
    ["Концерты", "♪"], ["Театр", "◫"], ["Выставки", "◇"],
    ["Кино", "▶"], ["Лекции", "↗"], ["Стендап", "☺"],
    ["Фестивали", "✦"], ["Мастер-классы", "✎"], ["Прогулки", "⌁"],
    ["Спорт", "○"], ["Танцы", "∿"], ["Нетворкинг", "+"],
  ];
  const BUDGETS = [[null, "Любой"], [0, "Бесплатно"], [1000, "до 1 000 ₽"], [3000, "до 3 000 ₽"], [5000, "до 5 000 ₽"]];
  const GROUPS = [
    [null, null, "Неважно"], [2, 2, "2 человека"], [3, 3, "3 человека"],
    [4, 4, "4 человека"], [5, 5, "5 человек"],
  ];
  const MONTHS = ["ЯНВ", "ФЕВ", "МАР", "АПР", "МАЙ", "ИЮН", "ИЮЛ", "АВГ", "СЕН", "ОКТ", "НОЯ", "ДЕК"];
  const LONG_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  const WEEKDAYS = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];
  const GLYPHS = ["♪", "◌", "✦", "◇", "∿"];
  const SOURCE_BRANDS = {
    kudago: { name: "KudaGo", mark: "K" },
    timepad: { name: "timepad", mark: "tp" },
    ticketmaster: { name: "Ticketmaster", mark: "★" },
  };
  const RESEARCH_EVENT_LABELS = {
    "miniapp.open": "открыл Mini App",
    "miniapp.profile_saved": "сохранил профиль",
    "miniapp.event_details": "открыл мероприятие",
    "miniapp.company_prompt_opened": "открыл поиск компании",
    "miniapp.event_company.joined": "запустил поиск компании",
    "miniapp.group_details": "открыл компанию",
    "miniapp.profile_editor_opened": "открыл настройки профиля",
    "miniapp.profile_validation_failed": "не смог сохранить профиль",
    "miniapp.tab.feed": "вернулся в афишу",
    "miniapp.tab.my": "открыл свои события",
    "miniapp.tab.group": "открыл раздел компаний",
    "miniapp.tab.profile": "открыл профиль",
  };
  const EVENT_IMAGES = {
    "Концерты": "https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?w=900&h=520&fit=crop&auto=format",
    "Выставки": "https://images.unsplash.com/photo-1541367777708-7905fe3296c0?w=900&h=520&fit=crop&auto=format",
    "Стендап": "https://images.unsplash.com/photo-1527224857830-43a7acc85260?w=900&h=520&fit=crop&auto=format",
    "Лекции": "https://images.unsplash.com/photo-1486325212027-8081e485255e?w=900&h=520&fit=crop&auto=format",
    "Прогулки": "https://images.unsplash.com/photo-1477959858617-67f85cf4f1df?w=900&h=520&fit=crop&auto=format",
  };
  const LOCAL_PREVIEW = {
    user: { id: 1, first_name: "Дима", username: "preview", photo_url: null },
    is_admin: true,
    profile: {
      interests: ["Концерты", "Выставки", "Стендап"], avoid: ["спорт"],
      days: ["fri", "sat", "sun"], budget_rub: 3000,
      preferred_group_size_min: 2, preferred_group_size_max: 5,
    },
    digest_weekday: 4,
    event_groups: [],
    events: [
      { id: 101, title: "Джаз на крыше: вечерний концерт", description: "Живая музыка, закат над Москвой и камерная атмосфера. В программе — современный джаз и авторские аранжировки.", city: "Москва", address: "Берсеневская набережная, 6", date: "2026-09-04T19:30:00", end_date: null, price: "от 1 800 ₽", tags: ["джаз", "концерт", "на крыше"], venue: "Красный Октябрь", source_url: "https://kudago.com/", source_id: "kudago", source_name: "KudaGo", source_mark: "K", intent: "going", visible: true, company_count: 3, company_group_id: 12, company_status: "active" },
      { id: 102, title: "Новая Третьяковка: искусство XX века", description: "Большая экспозиция русского искусства XX века и специальная кураторская программа выходного дня.", city: "Москва", address: "Крымский Вал, 10", date: "2026-09-05T13:00:00", end_date: null, price: "700 ₽", tags: ["выставка", "искусство", "музей"], venue: "Новая Третьяковка", source_url: "https://timepad.ru/", source_id: "timepad", source_name: "timepad", source_mark: "tp", intent: null, visible: false, company_count: 0, company_group_id: null, company_status: null },
      { id: 103, title: "Открытый микрофон на Китай-городе", description: "Начинающие и опытные комики проверяют новый материал в небольшом клубе.", city: "Москва", address: "Покровка, 17", date: "2026-09-06T20:00:00", end_date: null, price: "Бесплатно", tags: ["стендап", "комедия"], venue: "Клуб 17", source_url: "https://ticketmaster.com/", source_id: "ticketmaster", source_name: "Ticketmaster", source_mark: "★", intent: "interested", visible: false, company_count: 1, company_group_id: null, company_status: null },
    ],
    my_events: [],
  };
  LOCAL_PREVIEW.my_events = LOCAL_PREVIEW.events.filter((event) => event.intent);
  LOCAL_PREVIEW.event_groups = [{
    id: 12, status: "active", event: LOCAL_PREVIEW.events[0],
    member_count: 3, minimum_members: 2, maximum_members: 5,
    can_interact: true, meeting_point: "У главного входа за 15 минут", meeting_point_author: "Аня",
    members: [
      { name: "Аня", is_me: false, member_key: "preview-anya", common_interests: ["Концерты"], group_size: "3–5", rsvp: "going", connection_state: "connected", contact: { name: "Аня", url: "https://t.me/telegram" } },
      { name: "Максим", is_me: false, member_key: "preview-max", common_interests: ["Концерты"], group_size: "3–5", rsvp: "going", connection_state: "available" },
      { name: "Вы", is_me: true, member_key: null, common_interests: ["Концерты"], group_size: "2–5", rsvp: "going", connection_state: "self" },
    ],
    messages: [
      { id: 1, author_name: "Аня", is_me: false, message: "Всем привет! Встречаемся у входа?", created_at: "2026-08-29 12:35:00" },
      { id: 2, author_name: "Вы", is_me: true, message: "Да, буду за 15 минут", created_at: "2026-08-29 12:38:00" },
    ],
  }];
  LOCAL_PREVIEW.company_events = LOCAL_PREVIEW.events.filter((event) => event.company_count > 0);

  function localAdminPreview(days) {
    const today = new Date();
    const daily = Array.from({ length: days }, (_, index) => {
      const date = new Date(today);
      date.setDate(today.getDate() - (days - index - 1));
      const wave = Math.max(0, Math.round(4 + Math.sin(index / 2.2) * 2 + index / Math.max(days / 4, 1)));
      return {
        date: date.toISOString().slice(0, 10),
        active_users: wave,
        visits: wave + (index % 3),
        actions: wave * 5 + (index % 4) * 2,
      };
    });
    const active = Math.max(...daily.map((item) => item.active_users));
    return {
      days,
      period: { from: daily[0].date, to: daily[daily.length - 1].date, generated_at: new Date().toISOString() },
      summary: {
        known_users: 74, active_users: active + 24, previous_active_users: active + 17,
        new_users: 12, engaged_users: active + 17, returning_users: active + 10,
        dormant_users: 34, visits: 96, previous_visits: 79, actions: 428,
        previous_actions: 351, usage_rate: 54.1, engagement_rate: 82.5,
        returning_rate: 67.5, actions_per_active: 10.7, visits_per_active: 2.4,
        active_days_per_user: 3.2,
      },
      daily,
      frequency: { one_day: 13, two_three_days: 15, four_seven_days: 8, eight_plus_days: 4 },
      top_features: [
        { label: "Вкладка «Афиша»", amount: 91, users: 29 },
        { label: "Карточка события", amount: 68, users: 24 },
        { label: "Заполнение профиля", amount: 54, users: 18 },
        { label: "Подбор событий", amount: 41, users: 17 },
        { label: "Отметка «Интересно»", amount: 32, users: 14 },
      ],
      sources: [
        { label: "Mini App", amount: 287, users: 34 },
        { label: "Telegram-бот", amount: 141, users: 27 },
      ],
      research_campaigns: [
        {
          campaign: "ux_unu_sep2026", participants: 5, completed: 2,
          sessions: 7, last_activity: new Date().toISOString(),
        },
      ],
      feedback: { total: 5, new: 1 },
      inactivity_feedback: {
        prompts_sent: 18, responses: 12, response_rate: 66.7,
        reasons: [
          { code: "no_events", label: "Не нашёл подходящего", amount: 5 },
          { code: "not_now", label: "Сейчас неактуально", amount: 4 },
          { code: "confusing", label: "Не понял, как пользоваться", amount: 2 },
          { code: "other", label: "Другое", amount: 1 },
        ],
      },
    };
  }

  function localResearchPreview(campaign) {
    const stages = [
      ["enrolled", "Получили код", 5],
      ["opened_app", "Открыли Mini App", 5],
      ["profiled", "Сохранили профиль", 4],
      ["event_opened", "Открыли мероприятие", 4],
      ["company_prompt", "Открыли поиск компании", 3],
      ["joined_company", "Запустили поиск", 2],
      ["group_opened", "Открыли компанию", 2],
    ];
    return {
      campaign,
      summary: {
        participants: 5, sessions: 7, completed: 2,
        completion_rate: 40, median_session_seconds: 184,
      },
      funnel: stages.map(([stage, label, users]) => ({
        stage, label, users, conversion: users * 20,
      })),
      participants: [
        { participant_code: "UX-7MK9QP", sessions: 2, events: 17, duration_seconds: 412, completed: true, last_event: "miniapp.group_details" },
        { participant_code: "UX-BR4X2N", sessions: 1, events: 11, duration_seconds: 205, completed: true, last_event: "miniapp.group_details" },
        { participant_code: "UX-K8WD3H", sessions: 1, events: 8, duration_seconds: 148, completed: false, last_event: "miniapp.company_prompt_opened" },
        { participant_code: "UX-P2FM6Y", sessions: 2, events: 6, duration_seconds: 126, completed: false, last_event: "miniapp.event_details" },
        { participant_code: "UX-T9CA5R", sessions: 1, events: 2, duration_seconds: 29, completed: false, last_event: "miniapp.open" },
      ],
      top_events: [],
    };
  }

  const state = {
    data: null,
    tab: "feed",
    myFilter: "all",
    selectedInterests: new Set(),
    selectedDays: new Set(),
    budget: null,
    group: [null, null],
    feedCategory: "Все",
    selectedEventGroupId: null,
    groupDetailOpen: false,
    profileEditorOpen: false,
    companyModalEventId: null,
    modalEventId: null,
    adminData: null,
    adminDays: 30,
    adminMetric: "active_users",
    adminLoading: false,
    researchCampaign: null,
    researchData: null,
    researchLoading: false,
  };
  let groupPollTimer = null;
  let researchHeartbeatTimer = null;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function haptic(type = "light") {
    try { tg?.HapticFeedback?.impactOccurred(type); } catch (_) { /* optional */ }
  }

  async function api(path, options = {}) {
    const response = await fetch(`/r/api${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": tg?.initData || "",
        "X-Research-Session": researchSessionId,
        ...(options.headers || {}),
      },
    });
    let body = {};
    try { body = await response.json(); } catch (_) { /* empty response */ }
    if (!response.ok) {
      const detail = Array.isArray(body.detail)
        ? body.detail.map((item) => item.msg).filter(Boolean).join(" · ")
        : body.detail;
      throw new Error(detail || "Не удалось выполнить запрос");
    }
    return body;
  }

  function trackMiniapp(event, metadata = {}) {
    if (!tg?.initData) return;
    void api("/track", {
      method: "POST",
      body: JSON.stringify({ event, metadata }),
    }).catch(() => {});
  }

  function startResearchHeartbeat() {
    clearInterval(researchHeartbeatTimer);
    if (!state.data?.research) return;
    const ping = () => trackMiniapp("session_heartbeat", {
      tab: state.tab || "loading",
      status: document.visibilityState,
    });
    researchHeartbeatTimer = setInterval(ping, 15000);
    document.addEventListener("visibilitychange", ping);
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[char]);
  }

  function variantFor(event) {
    const text = `${event.title} ${(event.tags || []).join(" ")}`;
    let hash = 0;
    for (let i = 0; i < text.length; i += 1) hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
    return Math.abs(hash) % 5;
  }

  function sourceBadge(event) {
    const id = Object.hasOwn(SOURCE_BRANDS, event.source_id) ? event.source_id : "other";
    const fallback = { name: event.source_name || "Источник", mark: event.source_mark || "↗" };
    const brand = SOURCE_BRANDS[id] || fallback;
    return `<span class="source-badge source-${id}"><i>${escapeHtml(brand.mark)}</i><b>${escapeHtml(brand.name)}</b></span>`;
  }

  function formatWhen(raw, long = false) {
    const date = new Date(raw);
    if (long) return `${date.getDate()} ${LONG_MONTHS[date.getMonth()]}, ${WEEKDAYS[date.getDay()]} · ${date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`;
    return { day: date.getDate(), month: MONTHS[date.getMonth()], time: date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) };
  }

  function categoryFor(event) {
    const value = `${event.title} ${(event.tags || []).join(" ")}`.toLocaleLowerCase("ru");
    if (/стендап|комед/.test(value)) return "Стендап";
    if (/лекц|экскурс|образован/.test(value)) return "Лекции";
    if (/выстав|музе|галере|искусств/.test(value)) return "Выставки";
    if (/прогул|маршрут/.test(value)) return "Прогулки";
    return "Концерты";
  }

  function eventImage(event) {
    return event.image_url || EVENT_IMAGES[categoryFor(event)] || EVENT_IMAGES["Концерты"];
  }

  function countdownFor(raw) {
    const target = new Date(raw);
    const now = new Date();
    const days = Math.max(0, Math.ceil((target.getTime() - now.getTime()) / 86400000));
    if (days === 0) return "Сегодня";
    if (days === 1) return "Завтра";
    return `Через ${days} ${days % 10 === 1 && days % 100 !== 11 ? "день" : [2, 3, 4].includes(days % 10) && ![12, 13, 14].includes(days % 100) ? "дня" : "дней"}`;
  }

  function companyAction(event) {
    if (event.company_group_id) {
      return `<button class="company-button joined" data-open-company="${event.company_group_id}" type="button">Открыть компанию <span>→</span></button>`;
    }
    const label = event.company_count > 0 ? "Присоединиться" : "Найти компанию";
    return `<button class="company-button" data-find-company="${event.id}" type="button">${label} <span>→</span></button>`;
  }

  function eventActions(event) {
    return `${companyAction(event)}
      <button class="save-event ${event.intent === "interested" ? "active" : ""}" data-intent="interested" data-id="${event.id}" type="button" aria-label="Сохранить мероприятие">${event.intent === "interested" ? "★" : "☆"}</button>`;
  }

  function eventCard(event, showVisibility = false) {
    const date = formatWhen(event.date);
    const place = [event.venue, event.address].filter(Boolean).join(" · ");
    const category = categoryFor(event);
    const visibility = showVisibility && ["going", "interested"].includes(event.intent)
      ? `<div class="visibility-row"><span>Видно другим участникам</span><button class="switch ${event.visible ? "on" : ""}" data-visible="${!event.visible}" data-id="${event.id}" aria-label="Изменить видимость"></button></div>`
      : "";
    return `
      <article class="event-card" data-event-id="${event.id}">
        <button class="card-visual" data-open="${event.id}" type="button" aria-label="Подробнее о мероприятии" style="background-image:url('${escapeHtml(eventImage(event))}')">
          <span class="image-shade"></span>
          ${sourceBadge(event)}
          ${event.company_count > 0 ? `<span class="waiting-signal"><i>● ●</i><b>${event.company_count > 1 ? `Уже ищут компанию: ${event.company_count}` : "Ищут: 1"}</b></span>` : ""}
          <span class="card-overlay-tags"><i>${escapeHtml(category)}</i><i>${escapeHtml(countdownFor(event.date))}</i></span>
        </button>
        <div class="card-body">
          <button class="card-title" data-open="${event.id}" type="button">${escapeHtml(event.title)}</button>
          <div class="card-meta">
            <span><b>◷</b>${escapeHtml(formatWhen(event.date, true))}</span>
            <span><b>⌖</b>${escapeHtml(place || "Москва")}</span>
          </div>
          <div class="card-footer">
            <div class="card-actions">${eventActions(event)}</div>
            <span class="price-pill">${escapeHtml(event.price)}</span>
          </div>
          ${visibility}
        </div>
      </article>`;
  }

  function myEventCard(event) {
    const status = event.company_status === "active"
      ? ["Группа собрана", "success"]
      : event.company_group_id
        ? ["Ищу компанию", "waiting"]
        : [event.intent === "going" ? "Иду" : "Сохранено", "saved"];
    return `
      <button class="my-event-card" type="button" ${event.company_group_id ? `data-open-company="${event.company_group_id}"` : `data-open="${event.id}"`}>
        <span class="my-event-image" style="background-image:url('${escapeHtml(eventImage(event))}')"><i class="my-status ${status[1]}">${status[0]}</i></span>
        <span class="my-event-copy"><b>${escapeHtml(event.title)}</b><small>${escapeHtml(formatWhen(event.date, true))}</small><small>${escapeHtml(event.price)}</small></span>
        <span class="my-chevron">›</span>
      </button>`;
  }

  function companyDiscoveryCard(event) {
    const count = Number(event.company_count || 0);
    const singular = count % 10 === 1 && count % 100 !== 11;
    const few = [2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100);
    const people = `${count} ${singular ? "человек ищет" : few ? "человека ищут" : "человек ищут"} компанию`;
    return `
      <article class="company-discovery-card">
        <button class="discovery-image" type="button" data-open="${event.id}" style="background-image:url('${escapeHtml(eventImage(event))}')" aria-label="Подробнее о мероприятии">
          <span class="image-shade"></span>${sourceBadge(event)}
          <strong><i>● ●</i>${escapeHtml(people)}</strong>
        </button>
        <div class="discovery-copy">
          <button type="button" data-open="${event.id}">${escapeHtml(event.title)}</button>
          <span>◷ ${escapeHtml(formatWhen(event.date, true))}</span>
          <span>⌖ ${escapeHtml(event.venue || event.address || "Москва")}</span>
          <div>${companyAction(event)}<b>${escapeHtml(event.price)}</b></div>
        </div>
      </article>`;
  }

  function emptyState(kind) {
    if (kind === "feed") return `<div class="empty-state"><span>⌁</span><h3>Пока ничего точного</h3><p>Попробуйте выбрать другие дни или увеличить бюджет в профиле.</p></div>`;
    return `<div class="empty-state"><span>◒</span><h3>Планы ещё впереди</h3><p>Отмечайте интересные события в афише — они появятся здесь.</p></div>`;
  }

  function renderHeader() {
    const user = state.data.user;
    const avatar = $("#avatar");
    avatar.textContent = (user.first_name || "М").slice(0, 1).toUpperCase();
    if (user.photo_url) avatar.innerHTML = `<img src="${escapeHtml(user.photo_url)}" alt="">`;
  }

  function renderFeed() {
    const companyEvents = (state.data.company_events || []).filter((event) => Number(event.company_count || 0) > 0);
    const companyIds = new Set(companyEvents.map((event) => event.id));
    const events = (state.data.events || []).filter((event) => (
      !companyIds.has(event.id)
      && (state.feedCategory === "Все" || categoryFor(event) === state.feedCategory)
    ));
    $("#company-discovery").classList.toggle("hidden", !companyEvents.length);
    $("#company-event-list").innerHTML = companyEvents.map((event) => companyDiscoveryCard(event)).join("");
    $("#company-nav-dot").classList.toggle("hidden", !(state.data.event_groups || []).some((group) => group.status !== "active"));
    $("#event-count").textContent = events.length;
    $("#event-list").innerHTML = events.length ? events.map((event) => eventCard(event)).join("") : emptyState("feed");
  }

  function renderMy() {
    const events = (state.data.my_events || []).filter((event) => state.myFilter === "all" || event.intent === state.myFilter);
    $("#my-event-list").innerHTML = events.length ? events.map((event) => myEventCard(event)).join("") : emptyState("my");
  }

  function choiceButton(label, selected, attrs = "", custom = false) {
    return `<button type="button" class="choice-chip ${selected ? "selected" : ""} ${custom ? "custom" : ""}" ${attrs}>${label}</button>`;
  }

  function hydrateProfileForm() {
    const profile = state.data.profile;
    const canonicalInterests = new Map(INTERESTS.map(([name]) => [name.toLocaleLowerCase("ru"), name]));
    state.selectedInterests = new Set((profile?.interests || []).map((name) => canonicalInterests.get(name.toLocaleLowerCase("ru")) || name));
    state.selectedDays = new Set(profile?.days || []);
    state.budget = profile?.budget_rub ?? null;
    state.group = [profile?.preferred_group_size_min ?? null, profile?.preferred_group_size_max ?? null];
    $("#avoid-input").value = (profile?.avoid || []).join(", ");
    $("#digest-select").value = state.data.digest_weekday ?? "";
    renderProfileChoices();
  }

  function renderProfileChoices() {
    const defaults = new Set(INTERESTS.map(([name]) => name.toLocaleLowerCase("ru")));
    const custom = [...state.selectedInterests].filter((name) => !defaults.has(name.toLocaleLowerCase("ru")));
    $("#interest-chips").innerHTML = [
      ...INTERESTS.map(([name, icon]) => choiceButton(`${icon} ${name}`, state.selectedInterests.has(name), `data-interest="${escapeHtml(name)}"`)),
      ...custom.map((name) => choiceButton(`${escapeHtml(name)} ×`, true, `data-interest="${escapeHtml(name)}"`, true)),
    ].join("");
    $("#day-chips").innerHTML = DAYS.map(([value, label]) => choiceButton(label, state.selectedDays.has(value), `data-day="${value}"`)).join("");
    $("#budget-chips").innerHTML = BUDGETS.map(([value, label]) => choiceButton(label, state.budget === value, `data-budget="${value ?? "any"}"`)).join("");
    const predefined = BUDGETS.some(([value]) => value === state.budget);
    $("#budget-input").value = predefined ? "" : (state.budget ?? "");
    $("#group-chips").innerHTML = GROUPS.map(([min, max, label]) => choiceButton(label, state.group[0] === min && state.group[1] === max, `data-group-min="${min ?? "any"}" data-group-max="${max ?? "any"}"`)).join("");
  }

  function renderProfile() {
    const user = state.data.user || {};
    const initial = (user.first_name || "М").slice(0, 1).toLocaleUpperCase("ru");
    const avatar = $("#profile-avatar");
    avatar.textContent = initial;
    if (user.photo_url) avatar.innerHTML = `<img src="${escapeHtml(user.photo_url)}" alt="">`;
    $("#profile-identity").textContent = `${user.username ? `@${user.username} · ` : ""}Москва`;
    const onboarding = !state.data.profile;
    if (onboarding) state.profileEditorOpen = true;
    $("#profile-summary").classList.toggle("hidden", state.profileEditorOpen);
    $("#profile-menu").classList.toggle("hidden", state.profileEditorOpen);
    $("#profile-editor").classList.toggle("hidden", !state.profileEditorOpen);
    $("#profile-editor-back").classList.toggle("hidden", onboarding);
    $("#onboarding-copy").classList.toggle("hidden", !onboarding);
  }

  function groupConnectionActions(member, groupId) {
    if (member.is_me) return "";
    if (member.connection_state === "connected" && member.contact?.url) {
      return `<a class="member-action connected" href="${escapeHtml(member.contact.url)}" target="_blank" rel="noopener noreferrer">Написать в Telegram ↗</a>`;
    }
    if (member.connection_state === "pending_sent") {
      return `<button class="member-action" type="button" disabled>Запрос отправлен</button>`;
    }
    if (member.connection_state === "rejected") {
      return `<button class="member-action" type="button" disabled>Запрос отклонён</button>`;
    }
    if (member.connection_state === "pending_received" && member.request_id) {
      return `<div class="member-response"><button class="member-action accept" type="button" data-event-connection-action="accept" data-group-id="${groupId}" data-request-id="${member.request_id}">Познакомиться</button><button class="member-action" type="button" data-event-connection-action="reject" data-group-id="${groupId}" data-request-id="${member.request_id}">Не сейчас</button></div>`;
    }
    if (member.connection_state === "available") {
      return `<button class="member-action" type="button" data-event-connect="${escapeHtml(member.member_key)}" data-group-id="${groupId}">Познакомиться</button>`;
    }
    return "";
  }

  function groupSafetyActions(member, groupId) {
    if (member.is_me || !member.member_key) return "";
    return `<div class="member-safety">
      <button type="button" data-event-report="${escapeHtml(member.member_key)}" data-group-id="${groupId}">Пожаловаться</button>
      <button type="button" data-event-block="${escapeHtml(member.member_key)}" data-group-id="${groupId}">Заблокировать</button>
    </div>`;
  }

  function groupChat(group) {
    const messages = (group.messages || []).map((message) => `
      <article class="chat-message ${message.is_me ? "me" : ""}">
        <b>${escapeHtml(message.author_name)}</b>
        <p>${escapeHtml(message.message)}</p>
        <time>${escapeHtml(formatChatTime(message.created_at))}</time>
      </article>`).join("");
    return `
      <section class="group-chat">
        <div class="group-subheading"><div><p class="eyebrow">ОБЩИЙ ЧАТ</p><h3>Договориться о встрече</h3></div></div>
        <div class="chat-list">${messages || '<p class="group-empty-copy">Напишите первым — например, предложите познакомиться перед мероприятием.</p>'}</div>
        <form id="group-chat-form" class="chat-form" data-group-id="${group.id}">
          <textarea id="group-chat-input" maxlength="1000" rows="2" placeholder="Сообщение группе" aria-label="Сообщение группе"></textarea>
          <button type="submit" aria-label="Отправить сообщение">↑</button>
        </form>
      </section>`;
  }

  function renderGroup() {
    const container = $("#group-content");
    const groups = state.data.event_groups || [];
    $("#company-nav-dot").classList.toggle("hidden", !groups.some((group) => group.status !== "active"));
    if (!groups.length) {
      container.innerHTML = `
        <div class="screen-heading"><h1>Компания</h1><p>Здесь появятся группы по выбранным мероприятиям</p></div>
        <div class="group-disabled">
          <div class="empty-people" aria-hidden="true"><i>А</i><i>М</i><i>Вы</i></div>
          <h3>Сначала выберите событие</h3>
          <p>Нажмите «Найти компанию» в карточке мероприятия. Мы соберём 2–5 человек, которые хотят пойти именно туда.</p>
          <button class="primary-button" type="button" data-tab="feed">Открыть афишу</button>
        </div>`;
      return;
    }
    if (!state.groupDetailOpen) {
      container.innerHTML = `
        <div class="screen-heading"><h1>Компания</h1><p>Ваши группы по конкретным мероприятиям</p></div>
        <div class="company-list">${groups.map((item) => {
          const active = item.status === "active";
          const missing = Math.max(0, item.minimum_members - item.member_count);
          const initials = (item.members || []).slice(0, 3).map((member, index) => `<i style="z-index:${4 - index}">${escapeHtml((member.name || "У").slice(0, 1).toLocaleUpperCase("ru"))}</i>`).join("");
          return `<button class="company-list-card" type="button" data-select-company="${item.id}">
            <span class="company-list-image" style="background-image:url('${escapeHtml(eventImage(item.event))}')"></span>
            <span class="company-list-copy">
              <em class="company-state ${active ? "success" : "waiting"}">${active ? "Группа собрана" : "Ищем людей"}</em>
              <b>${escapeHtml(item.event.title)}</b>
              <small>${escapeHtml(formatWhen(item.event.date, true))}</small>
              <span class="company-members">${initials}<strong>${item.member_count}/${item.maximum_members}</strong></span>
              <span class="company-next">${active ? "Открыть чат и подтвердить участие" : missing === 1 ? "Ждём ещё 1 человека" : `Ждём ещё ${missing} человек`} <i>›</i></span>
            </span>
          </button>`;
        }).join("")}</div>`;
      return;
    }
    if (!groups.some((group) => group.id === state.selectedEventGroupId)) state.selectedEventGroupId = groups[0].id;
    const group = groups.find((item) => item.id === state.selectedEventGroupId) || groups[0];
    const event = group.event;
    const ready = group.status === "active";
    const missing = Math.max(0, group.minimum_members - group.member_count);
    const progressTarget = ready ? group.maximum_members : group.minimum_members;
    const progress = Math.min(100, Math.round((group.member_count / progressTarget) * 100));
    const members = (group.members || []).map((member) => {
      const initial = (member.name || "У").slice(0, 1).toLocaleUpperCase("ru");
      const common = (member.common_interests || []).join(", ");
      return `
        <article class="member-card ${member.is_me ? "me" : ""}">
          <span class="member-avatar">${escapeHtml(initial)}</span>
          <div>
            <h4>${escapeHtml(member.name)}</h4>
            <p>${common ? `Общее: ${escapeHtml(common)}` : "Идёт на это же событие"} · ${member.rsvp === "going" ? "идёт" : "не сможет"}</p>
            ${ready ? groupConnectionActions(member, group.id) : ""}
            ${ready ? groupSafetyActions(member, group.id) : ""}
          </div>
        </article>`;
    }).join("");
    const me = (group.members || []).find((member) => member.is_me);
    const waiting = `
      <article class="waiting-card">
        <span class="waiting-orbit">◎</span>
        <p class="eyebrow">ИЩЕМ КОМПАНИЮ</p>
        <h3>${missing === 1 ? "Ищем ещё одного человека" : `Ищем ещё ${missing} человек`}</h3>
        <p>${group.member_count} из ${group.minimum_members}–${group.maximum_members} участников</p>
        <div class="group-progress"><span style="width:${progress}%"></span></div>
        <div class="notify-note">🔔 Сообщим в Telegram, когда кто-то присоединится. Приложение можно закрыть.</div>
        <button class="text-button danger" type="button" data-leave-company="${group.id}">Отменить поиск</button>
      </article>`;
    const active = `
      <div class="rsvp-card"><div><p class="eyebrow">ПОДТВЕРДИТЕ УЧАСТИЕ</p><b>${me?.rsvp === "going" ? "Вы идёте" : me?.rsvp === "declined" ? "Вы не сможете" : "Ответьте группе"}</b></div><div><button class="${me?.rsvp === "going" ? "active" : ""}" data-event-rsvp="going" data-group-id="${group.id}">Иду</button><button class="${me?.rsvp === "declined" ? "active" : ""}" data-event-rsvp="declined" data-group-id="${group.id}">Не смогу</button></div></div>
      <section class="meeting-card"><p class="eyebrow">МЕСТО ВСТРЕЧИ</p>${group.meeting_point ? `<h3>${escapeHtml(group.meeting_point)}</h3><p>Предложил(а): ${escapeHtml(group.meeting_point_author || "участник")}</p>` : `<h3>Пока не договорились</h3><p>Предложите понятный ориентир и время.</p>`}<form id="meeting-form" data-group-id="${group.id}"><input maxlength="240" placeholder="Например: у входа в 19:45"><button type="submit">Предложить</button></form></section>
      <h3 class="group-members-title">Участники · ${group.member_count}/${group.maximum_members}</h3>
      <div class="member-list">${members}</div>
      ${groupChat(group)}`;
    container.innerHTML = `
      <button class="company-back" type="button" data-company-list>‹ <span>Все компании</span></button>
      <article class="group-hero">
        <span class="group-hero-image" style="background-image:url('${escapeHtml(eventImage(event))}')"></span>
        <span class="group-hero-shade"></span>
        ${sourceBadge(event)}
        <span class="group-status ${ready ? "success" : "waiting"}">${ready ? "ГРУППА СОБРАНА" : "ИДЁТ ПОИСК"}</span>
        <h3>${escapeHtml(event.title)}</h3>
        <p>${formatWhen(event.date, true)} · ${escapeHtml(event.venue || event.address || "Москва")}</p>
        <button class="group-event-open" type="button" data-open="${event.id}">Открыть мероприятие ↗</button>
      </article>
      ${ready ? active : waiting}`;
  }

  function formatChatTime(raw) {
    if (!raw) return "";
    const normalized = raw.includes("T") ? raw : `${raw.replace(" ", "T")}Z`;
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }

  function formatNumber(value, digits = 0) {
    return Number(value || 0).toLocaleString("ru-RU", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function shortDate(value) {
    const [, month, day] = String(value).split("-");
    return `${day}.${month}`;
  }

  function comparisonCopy(current, previous) {
    const difference = Number(current || 0) - Number(previous || 0);
    if (!previous) return current ? "новая активность" : "без изменений";
    const percent = Math.round(Math.abs(difference) * 100 / previous);
    if (!difference) return "как в прошлом периоде";
    return `${difference > 0 ? "↑" : "↓"} ${percent}% к прошлому периоду`;
  }

  function configureAdminAccess() {
    const allowed = Boolean(state.data?.is_admin);
    $("#admin-nav-button").classList.add("hidden");
    $("#admin-profile-button").classList.toggle("hidden", !allowed);
    $("#admin-profile-menu-button").classList.toggle("hidden", !allowed);
    $("#bottom-nav").classList.remove("admin-enabled");
    if (!allowed && state.tab === "admin") setTab("feed");
  }

  function metricCard(label, value, note, accent = "") {
    return `
      <article class="admin-kpi ${accent}">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
        <small>${escapeHtml(note)}</small>
      </article>`;
  }

  function renderAdminChart() {
    const data = state.adminData;
    if (!data) return;
    const metric = state.adminMetric;
    const titles = {
      active_users: "Активные пользователи",
      visits: "Входы в бот и приложение",
      actions: "Все действия",
    };
    $("#admin-chart-title").textContent = titles[metric];
    $$('[data-admin-metric]').forEach((button) => button.classList.toggle("active", button.dataset.adminMetric === metric));

    const values = data.daily.map((item) => Number(item[metric] || 0));
    const width = 640;
    const height = 230;
    const left = 38;
    const right = 12;
    const top = 18;
    const bottom = 35;
    const chartWidth = width - left - right;
    const chartHeight = height - top - bottom;
    const maxValue = Math.max(1, ...values);
    const x = (index) => left + (values.length === 1 ? chartWidth / 2 : index * chartWidth / (values.length - 1));
    const y = (value) => top + chartHeight - value * chartHeight / maxValue;
    const points = values.map((value, index) => `${x(index).toFixed(1)},${y(value).toFixed(1)}`).join(" ");
    const areaPoints = values.map((value, index) => `${x(index).toFixed(1)} ${y(value).toFixed(1)}`).join(" L ");
    const area = values.length
      ? `M ${x(0).toFixed(1)} ${top + chartHeight} L ${areaPoints} L ${x(values.length - 1).toFixed(1)} ${top + chartHeight} Z`
      : "";
    const grid = [0, 0.5, 1].map((fraction) => {
      const lineY = top + chartHeight * fraction;
      const label = Math.round(maxValue * (1 - fraction));
      return `<line x1="${left}" y1="${lineY}" x2="${width - right}" y2="${lineY}" class="chart-grid-line"/><text x="${left - 7}" y="${lineY + 4}" class="chart-y-label">${label}</text>`;
    }).join("");
    const labelCount = Math.min(5, data.daily.length);
    const labelIndexes = new Set(Array.from({ length: labelCount }, (_, index) => Math.round(index * (data.daily.length - 1) / Math.max(labelCount - 1, 1))));
    const labels = [...labelIndexes].map((index) => `<text x="${x(index)}" y="${height - 9}" class="chart-x-label">${shortDate(data.daily[index].date)}</text>`).join("");
    const dots = values.length <= 31
      ? values.map((value, index) => `<circle cx="${x(index)}" cy="${y(value)}" r="3.2" class="chart-dot"><title>${shortDate(data.daily[index].date)}: ${value}</title></circle>`).join("")
      : "";
    const total = values.reduce((sum, value) => sum + value, 0);
    const average = values.length ? total / values.length : 0;

    $("#admin-chart").innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(titles[metric])} по дням">
        ${grid}
        <path d="${area}" class="chart-area"></path>
        <polyline points="${points}" class="chart-line"></polyline>
        ${dots}${labels}
      </svg>
      <div class="chart-summary"><span>Всего <b>${formatNumber(total)}</b></span><span>В среднем в день <b>${formatNumber(average, 1)}</b></span></div>`;
  }

  function analyticsBar(label, value, maxValue, detail = "") {
    const width = maxValue ? Math.max(2, Math.round(value * 100 / maxValue)) : 0;
    return `
      <div class="analytics-row">
        <div><span>${escapeHtml(label)}</span><small>${escapeHtml(detail)}</small><b>${formatNumber(value)}</b></div>
        <i><span style="width:${width}%"></span></i>
      </div>`;
  }

  function formatDuration(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    if (value < 60) return `${value} сек`;
    const minutes = Math.floor(value / 60);
    const rest = value % 60;
    return rest ? `${minutes} мин ${rest} сек` : `${minutes} мин`;
  }

  function researchEventLabel(eventName) {
    return RESEARCH_EVENT_LABELS[eventName] || eventName || "не открыл приложение";
  }

  function renderResearchDashboard() {
    const campaigns = state.adminData?.research_campaigns || [];
    const select = $("#admin-research-campaign");
    const controls = $("#admin-research-controls");
    const empty = $("#admin-research-empty");
    if (!campaigns.length) {
      controls.classList.add("hidden");
      empty.classList.remove("hidden");
      $("#admin-research-summary").innerHTML = "";
      $("#admin-research-funnel").innerHTML = "";
      $("#admin-research-participants").innerHTML = "";
      return;
    }
    controls.classList.remove("hidden");
    empty.classList.add("hidden");
    if (!campaigns.some((item) => item.campaign === state.researchCampaign)) {
      state.researchCampaign = campaigns[0].campaign;
      state.researchData = null;
    }
    select.innerHTML = campaigns.map((item) => (
      `<option value="${escapeHtml(item.campaign)}">${escapeHtml(item.campaign)} · ${formatNumber(item.participants)} чел.</option>`
    )).join("");
    select.value = state.researchCampaign;
    const data = state.researchData;
    if (!data || data.campaign !== state.researchCampaign) {
      $("#admin-research-summary").innerHTML = `<div class="analytics-empty">Загружаем прохождения…</div>`;
      $("#admin-research-funnel").innerHTML = "";
      $("#admin-research-participants").innerHTML = "";
      return;
    }
    const summary = data.summary;
    $("#admin-research-summary").innerHTML = [
      metricCard("Участники", formatNumber(summary.participants), `${formatNumber(summary.sessions)} сессий`, "compact"),
      metricCard("Завершили", `${formatNumber(summary.completion_rate, 1)}%`, `${formatNumber(summary.completed)} из ${formatNumber(summary.participants)}`, "compact"),
      metricCard("Медиана сессии", formatDuration(summary.median_session_seconds), "от открытия до последнего действия", "compact"),
    ].join("");
    const funnelMax = Math.max(1, ...data.funnel.map((item) => item.users));
    $("#admin-research-funnel").innerHTML = data.funnel.map((item) => (
      analyticsBar(item.label, item.users, funnelMax, `${formatNumber(item.conversion, 1)}% участников`)
    )).join("");
    $("#admin-research-participants").innerHTML = data.participants.length
      ? data.participants.map((item) => `
          <div class="research-participant ${item.completed ? "completed" : ""}">
            <div><b>${escapeHtml(item.participant_code)}</b><span>${item.completed ? "Сценарий завершён" : "Последнее действие: " + escapeHtml(researchEventLabel(item.last_event))}</span></div>
            <small>${formatNumber(item.sessions)} сесс. · ${formatNumber(item.events)} действий · ${formatDuration(item.duration_seconds)}</small>
          </div>`).join("")
      : `<div class="analytics-empty">Участники ещё не начали прохождение.</div>`;
  }

  async function loadResearchAnalytics(force = false) {
    if (!state.data?.is_admin || !state.researchCampaign || state.researchLoading) return;
    if (!force && state.researchData?.campaign === state.researchCampaign) {
      renderResearchDashboard();
      return;
    }
    state.researchLoading = true;
    renderResearchDashboard();
    try {
      const isPreview = !tg?.initData && ["localhost", "127.0.0.1"].includes(window.location.hostname);
      state.researchData = isPreview
        ? localResearchPreview(state.researchCampaign)
        : await api(`/admin/research?campaign=${encodeURIComponent(state.researchCampaign)}`);
    } catch (error) {
      $("#admin-research-summary").innerHTML = `<div class="analytics-empty">${escapeHtml(error.message)}</div>`;
    } finally {
      state.researchLoading = false;
      renderResearchDashboard();
    }
  }

  async function downloadResearchCsv() {
    if (!state.researchCampaign) return;
    const button = $("#admin-research-export");
    button.disabled = true;
    try {
      const response = await fetch(`/r/api/admin/research/export?campaign=${encodeURIComponent(state.researchCampaign)}`, {
        headers: {
          "X-Telegram-Init-Data": tg?.initData || "",
          "X-Research-Session": researchSessionId,
        },
      });
      if (!response.ok) throw new Error("Не удалось сформировать CSV");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${state.researchCampaign}-research-events.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast("CSV сформирован");
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function renderAdminDashboard() {
    const data = state.adminData;
    if (!data) return;
    const summary = data.summary;
    const generated = new Date(data.period.generated_at);
    $("#admin-updated").textContent = `Обновлено ${generated.toLocaleDateString("ru-RU", { day: "numeric", month: "long" })} в ${generated.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`;
    $("#admin-kpis").innerHTML = [
      metricCard("Всего пользователей", formatNumber(summary.known_users), `${formatNumber(summary.new_users)} новых за период`),
      metricCard("Активные", formatNumber(summary.active_users), `${formatNumber(summary.usage_rate, 1)}% всей базы`, "accent-blue"),
      metricCard("Возвращаются", `${formatNumber(summary.returning_rate, 1)}%`, `${formatNumber(summary.returning_users)} человек`, "accent-coral"),
      metricCard("Действий на человека", formatNumber(summary.actions_per_active, 1), comparisonCopy(summary.actions, summary.previous_actions), "accent-violet"),
    ].join("");

    $("#admin-engagement").innerHTML = [
      metricCard("Реально пользуются", `${formatNumber(summary.engagement_rate, 1)}%`, `${formatNumber(summary.engaged_users)} совершали действия`, "compact"),
      metricCard("Неактивны", formatNumber(summary.dormant_users), `не заходили ${data.days} дней`, "compact"),
      metricCard("Входов на активного", formatNumber(summary.visits_per_active, 1), comparisonCopy(summary.visits, summary.previous_visits), "compact"),
      metricCard("Активных дней", formatNumber(summary.active_days_per_user, 1), "в среднем на человека", "compact"),
    ].join("");

    const funnel = data.funnel || [];
    const funnelMax = Math.max(1, ...funnel.map((item) => item.users));
    $("#admin-funnel").innerHTML = funnel.length
      ? funnel.map((item) => analyticsBar(item.label, item.users, funnelMax, `${formatNumber(item.conversion, 1)}% от пришедших`)).join("")
      : `<div class="analytics-empty">Новых пользователей за период нет.</div>`;

    const campaigns = data.campaigns || [];
    const campaignMax = Math.max(1, ...campaigns.map((item) => item.users));
    $("#admin-campaigns").innerHTML = campaigns.length
      ? campaigns.map((item) => analyticsBar(item.campaign, item.users, campaignMax, `${item.opened_app} открыли · ${item.profiled} профилей · ${item.searched_company} искали компанию`)).join("")
      : `<div class="analytics-empty">Для атрибуции используйте ссылки вида <b>t.me/MskMeetupBot?start=club_singles</b>.</div>`;
    renderResearchDashboard();

    const frequencyItems = [
      ["Один день", data.frequency.one_day],
      ["2–3 дня", data.frequency.two_three_days],
      ["4–7 дней", data.frequency.four_seven_days],
      ["8 дней и чаще", data.frequency.eight_plus_days],
    ];
    const frequencyMax = Math.max(1, ...frequencyItems.map(([, value]) => value));
    $("#admin-frequency").innerHTML = frequencyItems.map(([label, value]) => analyticsBar(label, value, frequencyMax, "пользователей")).join("");

    const inactivity = data.inactivity_feedback || { prompts_sent: 0, responses: 0, response_rate: 0, reasons: [] };
    $("#admin-inactivity-rate").textContent = `${formatNumber(inactivity.response_rate, 1)}%`;
    $("#admin-inactivity-summary").textContent = inactivity.prompts_sent
      ? `${formatNumber(inactivity.responses)} из ${formatNumber(inactivity.prompts_sent)} ответили за период`
      : "За этот период опросы ещё не отправлялись";
    const inactivityMax = Math.max(1, ...inactivity.reasons.map((item) => item.amount));
    $("#admin-inactivity-feedback").innerHTML = inactivity.reasons.length
      ? inactivity.reasons.map((item) => analyticsBar(item.label, item.amount, inactivityMax, "ответов")).join("")
      : `<div class="analytics-empty">Причин пока нет — ответы появятся здесь автоматически.</div>`;

    const featureMax = Math.max(1, ...data.top_features.map((item) => item.amount));
    $("#admin-features").innerHTML = data.top_features.length
      ? data.top_features.map((item) => analyticsBar(item.label, item.amount, featureMax, `${item.users} польз.`)).join("")
      : `<div class="analytics-empty">За этот период действий пока нет.</div>`;

    const sourceMax = Math.max(1, ...data.sources.map((item) => item.amount));
    $("#admin-sources").innerHTML = data.sources.length
      ? data.sources.map((item) => analyticsBar(item.label, item.amount, sourceMax, `${item.users} польз.`)).join("")
      : `<div class="analytics-empty">Данных по каналам пока нет.</div>`;
    const outcomes = data.company_outcomes || [];
    const outcomeMax = Math.max(1, ...outcomes.map((item) => item.amount));
    $("#admin-company-outcomes").innerHTML = outcomes.length
      ? outcomes.map((item) => analyticsBar(item.label, item.amount, outcomeMax, "ответов")).join("")
      : `<div class="analytics-empty">Ответы появятся после завершённых мероприятий.</div>`;
    const reports = data.reports || { total: 0, new: 0 };
    $("#admin-reports").textContent = `${formatNumber(reports.total)} жалоб · ${formatNumber(reports.new)} новых`;
    const sourceHealthLabels = { success: "в норме", warning: "есть ошибки", failed: "сбой", stale: "давно не обновлялся", unknown: "нет данных" };
    const sourceHealth = data.source_health || [];
    $("#admin-source-health").innerHTML = sourceHealth.length
      ? sourceHealth.map((item) => {
          const when = item.finished_at ? new Date(`${item.finished_at.replace(" ", "T")}Z`).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "никогда";
          return `<div class="source-health-row ${escapeHtml(item.health)}"><div><span><b>${escapeHtml(item.source_id)}</b> · ${escapeHtml(sourceHealthLabels[item.health] || item.health)}</span><small>Обновлено: ${escapeHtml(when)} · активных событий: ${formatNumber(item.active_events)} · ошибок: ${formatNumber(item.errors)}</small></div><i></i></div>`;
        }).join("")
      : `<div class="analytics-empty">Импорты ещё не запускались.</div>`;
    renderAdminChart();
  }

  async function loadAdminAnalytics(force = false) {
    if (!state.data?.is_admin || state.adminLoading) return;
    if (state.adminData && !force && state.adminData.days === state.adminDays) {
      renderAdminDashboard();
      void loadResearchAnalytics();
      return;
    }
    state.adminLoading = true;
    $("#admin-refresh").classList.add("loading");
    $("#admin-updated").textContent = "Загружаем актуальные данные…";
    try {
      const isPreview = !tg?.initData && ["localhost", "127.0.0.1"].includes(window.location.hostname);
      state.adminData = isPreview ? localAdminPreview(state.adminDays) : await api(`/admin/analytics?days=${state.adminDays}`);
      renderAdminDashboard();
      void loadResearchAnalytics();
    } catch (error) {
      $("#admin-updated").textContent = error.message || "Не удалось загрузить аналитику";
    } finally {
      state.adminLoading = false;
      $("#admin-refresh").classList.remove("loading");
    }
  }

  function setTab(tab) {
    if (tab === "admin" && !state.data.is_admin) tab = "feed";
    if (!state.data.profile && tab !== "profile" && tab !== "admin") tab = "profile";
    state.tab = tab;
    $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === tab));
    $$(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
    window.scrollTo({ top: 0, behavior: "smooth" });
    renderHeader();
    if (tab === "my") renderMy();
    if (tab === "group") renderGroup();
    if (tab === "profile") renderProfile();
    if (tab === "admin") void loadAdminAnalytics();
    trackMiniapp(`tab.${tab}`, { tab });
    clearInterval(groupPollTimer);
    groupPollTimer = tab === "group" && tg?.initData
      ? setInterval(() => void refreshGroup(true), 15000)
      : null;
  }

  function mergeEvent(updated) {
    state.data.company_events ||= [];
    for (const collection of [state.data.events, state.data.my_events, state.data.company_events]) {
      const index = collection.findIndex((event) => event.id === updated.id);
      if (index >= 0) {
        const existing = collection[index];
        collection[index] = {
          ...existing,
          ...updated,
          company_count: Math.max(Number(existing.company_count || 0), Number(updated.company_count || 0)),
          company_group_id: updated.company_group_id ?? existing.company_group_id,
          company_status: updated.company_status ?? existing.company_status,
        };
      }
    }
    const myIndex = state.data.my_events.findIndex((event) => event.id === updated.id);
    if (myIndex < 0) state.data.my_events.push(updated);
    if (Number(updated.company_count || 0) > 0 && !state.data.company_events.some((event) => event.id === updated.id)) {
      state.data.company_events.unshift(updated);
    }
  }

  let toastTimer;
  function toast(message) {
    const element = $("#toast");
    element.textContent = message;
    element.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => element.classList.add("hidden"), 2200);
  }

  async function setIntent(eventId, intent) {
    haptic(intent === "not_going" ? "medium" : "light");
    try {
      const updated = await api(`/events/${eventId}/intent`, { method: "PUT", body: JSON.stringify({ status: intent }) });
      mergeEvent(updated);
      renderFeed(); renderMy();
      if (state.modalEventId === eventId) openModal(eventId);
      toast(intent === "going" ? "Добавили в ваши планы" : intent === "interested" ? "Сохранили — интересно" : "Больше не будем предлагать");
    } catch (error) { toast(error.message); }
  }

  async function setVisibility(eventId, visible) {
    haptic();
    try {
      const updated = await api(`/events/${eventId}/visibility`, { method: "PUT", body: JSON.stringify({ visible }) });
      mergeEvent(updated); renderFeed(); renderMy();
      toast(visible ? "Теперь вас видят участники" : "Вы скрыты от участников");
    } catch (error) { toast(error.message); }
  }

  function mergeEventGroup(group) {
    const groups = state.data.event_groups || (state.data.event_groups = []);
    const index = groups.findIndex((item) => item.id === group.id);
    if (index >= 0) groups[index] = group; else groups.push(group);
    state.selectedEventGroupId = group.id;
    mergeEvent(group.event);
    renderFeed(); renderMy(); renderGroup();
  }

  async function refreshGroup(silent = false) {
    if (!tg?.initData || state.tab !== "group" || !state.selectedEventGroupId) return;
    try {
      const group = await api(`/event-groups/${state.selectedEventGroupId}`);
      mergeEventGroup(group);
    } catch (error) {
      if (!silent) toast(error.message);
    }
  }

  function openCompanyModal(eventId) {
    const event = findEvent(eventId);
    if (!event) return;
    if (!$("#event-modal").classList.contains("hidden")) closeModal();
    state.companyModalEventId = event.id;
    $("#company-modal-title").textContent = event.company_count > 0 ? "Присоединиться к компании" : "Найти компанию";
    $("#company-modal-event").innerHTML = `<b>${escapeHtml(event.title)}</b><span>${escapeHtml(formatWhen(event.date, true))}</span><span>${escapeHtml(event.venue || event.address || "Москва")}</span>${event.company_count > 0 ? `<em>Уже ищут компанию: ${event.company_count}</em>` : ""}`;
    $("#company-modal-confirm span").textContent = event.company_count > 0 ? "Войти в компанию" : "Начать поиск";
    $("#company-modal").classList.remove("hidden");
    document.body.style.overflow = "hidden";
    tg?.BackButton?.show();
    trackMiniapp("company_prompt_opened", {
      event_id: event.id,
      company_count: Number(event.company_count || 0),
    });
  }

  function closeCompanyModal() {
    state.companyModalEventId = null;
    $("#company-modal").classList.add("hidden");
    document.body.style.overflow = "";
    if (state.modalEventId === null) tg?.BackButton?.hide();
  }

  async function joinEventCompany() {
    const eventId = state.companyModalEventId;
    if (!eventId) return;
    if (!tg?.initData) {
      const preview = state.data.event_groups?.[0];
      closeCompanyModal();
      if (preview) { state.selectedEventGroupId = preview.id; state.groupDetailOpen = true; setTab("group"); }
      else toast("В режиме просмотра поиск не запускается");
      return;
    }
    const button = $("#company-modal-confirm");
    button.disabled = true;
    try {
      const result = await api(`/events/${eventId}/company`, { method: "POST" });
      mergeEventGroup(result.event_group);
      state.groupDetailOpen = true;
      closeCompanyModal();
      haptic("medium");
      setTab("group");
      trackMiniapp("group_details", {
        group_id: result.event_group.id,
        event_id: eventId,
        status: result.event_group.status,
      });
      toast(result.event_group.status === "active" ? "Компания собрана" : "Поиск запущен");
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  }

  function openEventCompany(groupId) {
    state.selectedEventGroupId = Number(groupId);
    state.groupDetailOpen = true;
    setTab("group");
    const group = (state.data.event_groups || []).find((item) => item.id === Number(groupId));
    trackMiniapp("group_details", {
      group_id: Number(groupId),
      event_id: group?.event?.id || 0,
      status: group?.status || "unknown",
    });
  }

  async function leaveEventCompany(groupId) {
    if (!tg?.initData) { toast("В режиме просмотра поиск не отменяется"); return; }
    try {
      await api(`/event-groups/${groupId}`, { method: "DELETE" });
      state.data.event_groups = (state.data.event_groups || []).filter((item) => item.id !== groupId);
      for (const event of [...state.data.events, ...state.data.my_events, ...(state.data.company_events || [])]) {
        if (event.company_group_id === groupId) {
          event.company_group_id = null; event.company_status = null;
          event.company_count = Math.max(0, Number(event.company_count || 1) - 1);
        }
      }
      state.data.company_events = (state.data.company_events || []).filter((event) => Number(event.company_count || 0) > 0);
      state.selectedEventGroupId = state.data.event_groups[0]?.id || null;
      if (!state.data.event_groups.length) state.groupDetailOpen = false;
      renderFeed(); renderMy(); renderGroup();
      toast("Поиск отменён");
    } catch (error) { toast(error.message); }
  }

  async function updateEventRsvp(groupId, status) {
    if (!tg?.initData) { toast("В режиме просмотра ответ не сохраняется"); return; }
    try {
      const result = await api(`/event-groups/${groupId}/rsvp`, { method: "PUT", body: JSON.stringify({ status }) });
      if (result.status === "left") {
        state.data.event_groups = (state.data.event_groups || []).filter((item) => item.id !== groupId);
        state.selectedEventGroupId = state.data.event_groups[0]?.id || null;
        state.groupDetailOpen = false;
        renderFeed(); renderMy(); renderGroup();
        haptic(); toast("Вы вышли из компании, место освобождено");
        return;
      }
      mergeEventGroup(result.event_group); haptic(); toast("Участие подтверждено");
    } catch (error) { toast(error.message); }
  }

  async function submitMemberReport(groupId, memberKey, reason) {
    if (!tg?.initData) { toast("В режиме просмотра жалоба не отправляется"); return; }
    try {
      await api(`/event-groups/${groupId}/members/${encodeURIComponent(memberKey)}/report`, {
        method: "POST", body: JSON.stringify({ reason }),
      });
      haptic("medium"); toast("Жалоба передана администратору");
    } catch (error) { toast(error.message); }
  }

  function reportEventMember(groupId, memberKey) {
    const submit = (reason) => void submitMemberReport(groupId, memberKey, reason);
    if (tg?.showPopup) {
      tg.showPopup({
        title: "Причина жалобы",
        message: "Администратор проверит обращение. Другой участник не увидит, кто подал жалобу.",
        buttons: [
          { id: "harassment", type: "default", text: "Оскорбления" },
          { id: "unsafe", type: "destructive", text: "Небезопасное поведение" },
          { id: "spam", type: "default", text: "Спам" },
        ],
      }, (buttonId) => { if (buttonId) submit(buttonId); });
      return;
    }
    if (window.confirm("Отправить жалобу администратору?")) submit("other");
  }

  function blockEventMember(groupId, memberKey) {
    const proceed = async (confirmed) => {
      if (!confirmed) return;
      if (!tg?.initData) { toast("В режиме просмотра блокировка не сохраняется"); return; }
      try {
        await api(`/event-groups/${groupId}/members/${encodeURIComponent(memberKey)}/block`, { method: "POST" });
        state.data.event_groups = (state.data.event_groups || []).filter((item) => item.id !== groupId);
        state.selectedEventGroupId = state.data.event_groups[0]?.id || null;
        state.groupDetailOpen = false;
        renderFeed(); renderMy(); renderGroup();
        haptic("medium"); toast("Пользователь заблокирован. Вы больше не будете в одной компании.");
      } catch (error) { toast(error.message); }
    };
    const message = "Вы выйдете из этой компании, а этот пользователь больше не будет подбираться вам.";
    if (tg?.showConfirm) tg.showConfirm(message, proceed);
    else void proceed(window.confirm(message));
  }

  function openInfoPage(page) {
    const url = `${window.location.origin}/r/${page}`;
    if (tg?.openLink) tg.openLink(url);
    else window.open(url, "_blank", "noopener,noreferrer");
  }

  function deleteAccount() {
    const proceed = async (confirmed) => {
      if (!confirmed) return;
      if (!tg?.initData) { toast("В режиме просмотра данные не удаляются"); return; }
      try {
        await api("/account", { method: "DELETE" });
        haptic("medium");
        document.querySelector("main").innerHTML = `<section class="deleted-account"><h1>Данные удалены</h1><p>Профиль, история и компании удалены. При следующем запуске можно создать новый профиль.</p></section>`;
        $("#bottom-nav")?.classList.add("hidden");
      } catch (error) { toast(error.message); }
    };
    const message = "Удалить профиль, историю, сообщения и все связи? Это действие нельзя отменить.";
    if (tg?.showConfirm) tg.showConfirm(message, proceed);
    else void proceed(window.confirm(message));
  }

  async function sendMeetingPoint(event) {
    event.preventDefault();
    const form = event.target;
    const input = form.querySelector("input");
    const meetingPoint = input.value.trim();
    if (meetingPoint.length < 2) return;
    if (!tg?.initData) { input.value = ""; toast("В режиме просмотра предложение не отправляется"); return; }
    try {
      const result = await api(`/event-groups/${form.dataset.groupId}/meeting-point`, { method: "PUT", body: JSON.stringify({ meeting_point: meetingPoint }) });
      mergeEventGroup(result.event_group); haptic(); toast("Место встречи предложено");
    } catch (error) { toast(error.message); }
  }

  async function requestEventContact(groupId, memberKey) {
    if (!tg?.initData) { toast("В режиме просмотра запрос не отправляется"); return; }
    try {
      const result = await api(`/event-groups/${groupId}/connections/${encodeURIComponent(memberKey)}`, { method: "POST" });
      mergeEventGroup(result.event_group); haptic("medium");
      toast(result.status === "accepted" ? "Контакты открыты" : "Запрос отправлен");
    } catch (error) { toast(error.message); }
  }

  async function respondEventContact(groupId, requestId, action) {
    if (!tg?.initData) { toast("В режиме просмотра ответ не отправляется"); return; }
    try {
      const result = await api(`/event-groups/${groupId}/connections/${requestId}/${action}`, { method: "POST" });
      mergeEventGroup(result.event_group); haptic("medium");
      toast(action === "accept" ? "Теперь можно написать друг другу" : "Запрос отклонён");
    } catch (error) { toast(error.message); }
  }

  async function sendGroupMessage(event) {
    event.preventDefault();
    const input = $("#group-chat-input");
    const message = input?.value.trim();
    if (!message) return;
    if (!tg?.initData) { input.value = ""; toast("В режиме просмотра сообщение не отправляется"); return; }
    const button = $("#group-chat-form button[type='submit']");
    if (button) button.disabled = true;
    try {
      const groupId = event.target.dataset.groupId;
      const result = await api(`/event-groups/${groupId}/messages`, { method: "POST", body: JSON.stringify({ message }) });
      input.value = "";
      mergeEventGroup(result.event_group);
      haptic();
      requestAnimationFrame(() => {
        const list = $(".chat-list");
        if (list) list.scrollTop = list.scrollHeight;
      });
    } catch (error) { toast(error.message); }
    finally { if (button) button.disabled = false; }
  }

  function findEvent(id) {
    const groupEvents = (state.data.event_groups || []).map((group) => group.event);
    return [...(state.data.events || []), ...(state.data.company_events || []), ...(state.data.my_events || []), ...groupEvents].find((event) => event.id === Number(id));
  }

  function openModal(id) {
    const event = findEvent(id);
    if (!event) return;
    state.modalEventId = event.id;
    const variant = variantFor(event);
    $("#modal-visual").className = `modal-visual variant-${variant}`;
    $("#modal-visual").style.backgroundImage = `url('${eventImage(event)}')`;
    $("#modal-visual span").textContent = "";
    $("#modal-date").textContent = formatWhen(event.date, true).toLocaleUpperCase("ru");
    $("#modal-title").textContent = event.title;
    $("#modal-meta").innerHTML = `<span>⌖ ${escapeHtml([event.venue, event.address].filter(Boolean).join(" · ") || "Москва")}</span><span>Билет: ${escapeHtml(event.price)}</span>`;
    $("#modal-description").textContent = event.description || "Подробности доступны на странице мероприятия.";
    $("#modal-tags").innerHTML = (event.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
    const source = $("#modal-source");
    source.classList.toggle("hidden", !event.source_url);
    source.href = event.source_url || "#";
    source.innerHTML = `${sourceBadge(event)}<span>Открыть оригинал ↗</span>`;
    $("#modal-actions").innerHTML = eventActions(event);
    $("#event-modal").classList.remove("hidden");
    document.body.style.overflow = "hidden";
    tg?.BackButton?.show();
    trackMiniapp("event_details", {
      event_id: event.id,
      source_id: event.source_id || null,
      company_count: Number(event.company_count || 0),
    });
  }

  function closeModal() {
    state.modalEventId = null;
    $("#event-modal").classList.add("hidden");
    document.body.style.overflow = "";
    tg?.BackButton?.hide();
  }

  function addInterest() {
    const input = $("#custom-interest");
    const value = input.value.trim().replace(/\s+/g, " ");
    if (!value) return;
    state.selectedInterests.add(value.slice(0, 60));
    input.value = "";
    haptic(); renderProfileChoices();
  }

  async function saveProfile(event) {
    event.preventDefault();
    const errorBox = $("#form-error");
    errorBox.classList.add("hidden");
    const customBudget = $("#budget-input").value.trim();
    const budget = customBudget ? Number(customBudget) : state.budget;
    if (!state.selectedInterests.size) {
      errorBox.textContent = "Выберите хотя бы один интерес.";
      errorBox.classList.remove("hidden");
      trackMiniapp("profile_validation_failed", { reason: "no_interests" });
      return;
    }
    if (budget !== null && (!Number.isFinite(budget) || budget < 0 || budget > 1000000)) {
      errorBox.textContent = "Проверьте указанную сумму.";
      errorBox.classList.remove("hidden");
      trackMiniapp("profile_validation_failed", { reason: "invalid_budget" });
      return;
    }
    const button = $("#save-profile");
    button.disabled = true;
    button.querySelector("span").textContent = "Подбираем…";
    const avoid = $("#avoid-input").value.split(",").map((item) => item.trim()).filter(Boolean);
    const digest = $("#digest-select").value;
    try {
      state.data = await api("/profile", {
        method: "PUT",
        body: JSON.stringify({
          interests: [...state.selectedInterests], avoid,
          days: [...state.selectedDays], budget_rub: budget,
          preferred_group_size_min: state.group[0], preferred_group_size_max: state.group[1],
          digest_weekday: digest === "" ? null : Number(digest),
        }),
      });
      configureAdminAccess(); hydrateProfileForm(); renderFeed(); renderMy(); renderGroup();
      state.profileEditorOpen = false;
      haptic("medium"); toast("Профиль сохранён"); setTab("feed");
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.classList.remove("hidden");
      trackMiniapp("profile_validation_failed", { reason: "api_error" });
    } finally {
      button.disabled = false;
      button.querySelector("span").textContent = "Сохранить и подобрать";
    }
  }

  async function sendFeedback() {
    const input = $("#feedback-input");
    const statusBox = $("#feedback-status");
    const button = $("#send-feedback");
    const message = input.value.trim();
    statusBox.classList.add("hidden");
    if (message.length < 3) {
      statusBox.textContent = "Напишите хотя бы несколько слов.";
      statusBox.classList.remove("hidden");
      return;
    }
    if (!tg?.initData) {
      input.value = "";
      toast("В режиме просмотра сообщение не отправляется");
      return;
    }
    button.disabled = true;
    button.textContent = "Отправляем…";
    try {
      const result = await api("/feedback", {
        method: "POST",
        body: JSON.stringify({ message }),
      });
      input.value = "";
      statusBox.textContent = `Спасибо! Обращение #${result.feedback_id} передано команде.`;
      statusBox.classList.remove("hidden");
      haptic("medium");
    } catch (error) {
      statusBox.textContent = error.message;
      statusBox.classList.remove("hidden");
    } finally {
      button.disabled = false;
      button.textContent = "Отправить сообщение";
    }
  }

  function bindEvents() {
    $("#bottom-nav").addEventListener("click", (event) => {
      const button = event.target.closest("[data-tab]");
      if (button) {
        if (button.dataset.tab === "group") state.groupDetailOpen = false;
        if (button.dataset.tab === "profile" && state.data.profile) state.profileEditorOpen = false;
        haptic(); setTab(button.dataset.tab);
      }
    });
    document.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-tab]");
      if (tab && !tab.closest("#bottom-nav")) { haptic(); setTab(tab.dataset.tab); }
      const intent = event.target.closest("[data-intent]");
      if (intent) setIntent(Number(intent.dataset.id), intent.dataset.intent);
      const opener = event.target.closest("[data-open]");
      if (opener) openModal(Number(opener.dataset.open));
      const visibility = event.target.closest("[data-visible]");
      if (visibility) setVisibility(Number(visibility.dataset.id), visibility.dataset.visible === "true");
      const findCompany = event.target.closest("[data-find-company]");
      if (findCompany) openCompanyModal(Number(findCompany.dataset.findCompany));
      const openCompany = event.target.closest("[data-open-company]");
      if (openCompany) openEventCompany(Number(openCompany.dataset.openCompany));
      const selectCompany = event.target.closest("[data-select-company]");
      if (selectCompany) {
        state.selectedEventGroupId = Number(selectCompany.dataset.selectCompany);
        state.groupDetailOpen = true;
        haptic();
        renderGroup();
        const group = (state.data.event_groups || []).find((item) => item.id === state.selectedEventGroupId);
        trackMiniapp("group_details", {
          group_id: state.selectedEventGroupId,
          event_id: group?.event?.id || 0,
          status: group?.status || "unknown",
        });
      }
      const companyList = event.target.closest("[data-company-list]");
      if (companyList) { state.groupDetailOpen = false; haptic(); renderGroup(); }
      const leaveCompany = event.target.closest("[data-leave-company]");
      if (leaveCompany) void leaveEventCompany(Number(leaveCompany.dataset.leaveCompany));
      const rsvp = event.target.closest("[data-event-rsvp]");
      if (rsvp) void updateEventRsvp(Number(rsvp.dataset.groupId), rsvp.dataset.eventRsvp);
      const connect = event.target.closest("[data-event-connect]");
      if (connect) void requestEventContact(Number(connect.dataset.groupId), connect.dataset.eventConnect);
      const connectionResponse = event.target.closest("[data-event-connection-action]");
      if (connectionResponse) void respondEventContact(Number(connectionResponse.dataset.groupId), Number(connectionResponse.dataset.requestId), connectionResponse.dataset.eventConnectionAction);
      const reportMember = event.target.closest("[data-event-report]");
      if (reportMember) reportEventMember(Number(reportMember.dataset.groupId), reportMember.dataset.eventReport);
      const blockMember = event.target.closest("[data-event-block]");
      if (blockMember) blockEventMember(Number(blockMember.dataset.groupId), blockMember.dataset.eventBlock);
      const privacy = event.target.closest("[data-open-privacy]");
      if (privacy) openInfoPage("privacy");
      const rules = event.target.closest("[data-open-rules]");
      if (rules) openInfoPage("rules");
      const source = event.target.closest("#modal-source");
      if (source) {
        const openedEvent = findEvent(state.modalEventId);
        trackMiniapp("external_source", {
          event_id: openedEvent?.id || 0,
          source_id: openedEvent?.source_id || null,
        });
      }
      const profileSection = event.target.closest("[data-profile-section]");
      if (profileSection) {
        state.profileEditorOpen = true;
        renderProfile();
        trackMiniapp("profile_editor_opened", {
          reason: profileSection.dataset.profileSection,
        });
        const targets = { notifications: ".digest-section", feedback: ".feedback-section" };
        const target = targets[profileSection.dataset.profileSection];
        if (target) requestAnimationFrame(() => $(target)?.scrollIntoView({ behavior: "smooth", block: "start" }));
      }
      const profileInfo = event.target.closest("[data-profile-info]");
      if (profileInfo) toast(profileInfo.dataset.profileInfo === "privacy" ? "Контакты открываются только по взаимному согласию" : "Мск.Митап подбирает события и людей, с которыми можно пойти");
    });
    document.addEventListener("submit", (event) => {
      if (event.target.matches("#group-chat-form")) void sendGroupMessage(event);
      if (event.target.matches("#meeting-form")) void sendMeetingPoint(event);
    });
    $("#interest-chips").addEventListener("click", (event) => {
      const button = event.target.closest("[data-interest]");
      if (!button) return;
      const value = button.dataset.interest;
      const existing = [...state.selectedInterests].find((item) => item.toLocaleLowerCase("ru") === value.toLocaleLowerCase("ru"));
      if (existing) state.selectedInterests.delete(existing); else state.selectedInterests.add(value);
      haptic(); renderProfileChoices();
    });
    $("#day-chips").addEventListener("click", (event) => {
      const button = event.target.closest("[data-day]");
      if (!button) return;
      state.selectedDays.has(button.dataset.day) ? state.selectedDays.delete(button.dataset.day) : state.selectedDays.add(button.dataset.day);
      haptic(); renderProfileChoices();
    });
    $("#budget-chips").addEventListener("click", (event) => {
      const button = event.target.closest("[data-budget]"); if (!button) return;
      state.budget = button.dataset.budget === "any" ? null : Number(button.dataset.budget);
      haptic(); renderProfileChoices();
    });
    $("#group-chips").addEventListener("click", (event) => {
      const button = event.target.closest("[data-group-min]"); if (!button) return;
      state.group = [button.dataset.groupMin === "any" ? null : Number(button.dataset.groupMin), button.dataset.groupMax === "any" ? null : Number(button.dataset.groupMax)];
      haptic(); renderProfileChoices();
    });
    $("#add-interest").addEventListener("click", addInterest);
    $("#custom-interest").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); addInterest(); } });
    $("#profile-form").addEventListener("submit", saveProfile);
    $("#send-feedback").addEventListener("click", sendFeedback);
    $("#delete-account").addEventListener("click", deleteAccount);
    $("#profile-editor-back").addEventListener("click", () => { state.profileEditorOpen = false; renderProfile(); window.scrollTo({ top: 0, behavior: "smooth" }); });
    $("#feed-filters").addEventListener("click", (event) => {
      const button = event.target.closest("[data-category]"); if (!button) return;
      state.feedCategory = button.dataset.category;
      $$("[data-category]", $("#feed-filters")).forEach((chip) => chip.classList.toggle("active", chip === button));
      haptic(); renderFeed();
    });
    $("#my-filters").addEventListener("click", (event) => {
      const button = event.target.closest("[data-filter]"); if (!button) return;
      state.myFilter = button.dataset.filter;
      $$(".filter-chip", $("#my-filters")).forEach((chip) => chip.classList.toggle("active", chip === button));
      renderMy();
    });
    $("#admin-periods").addEventListener("click", (event) => {
      const button = event.target.closest("[data-admin-days]");
      if (!button) return;
      state.adminDays = Number(button.dataset.adminDays);
      $$('[data-admin-days]').forEach((item) => item.classList.toggle("active", item === button));
      haptic(); void loadAdminAnalytics(true);
    });
    $("#admin-chart-metrics").addEventListener("click", (event) => {
      const button = event.target.closest("[data-admin-metric]");
      if (!button) return;
      state.adminMetric = button.dataset.adminMetric;
      haptic(); renderAdminChart();
    });
    $("#admin-refresh").addEventListener("click", () => {
      haptic(); void loadAdminAnalytics(true);
    });
    $("#admin-research-campaign").addEventListener("change", (event) => {
      state.researchCampaign = event.target.value;
      state.researchData = null;
      haptic(); void loadResearchAnalytics(true);
    });
    $("#admin-research-export").addEventListener("click", () => {
      haptic(); void downloadResearchCsv();
    });
    $("#modal-close").addEventListener("click", closeModal);
    $("#modal-backdrop").addEventListener("click", closeModal);
    $("#company-modal-close").addEventListener("click", closeCompanyModal);
    $("#company-modal-backdrop").addEventListener("click", closeCompanyModal);
    $("#company-modal-cancel").addEventListener("click", closeCompanyModal);
    $("#company-modal-confirm").addEventListener("click", joinEventCompany);
    tg?.BackButton?.onClick(() => {
      if (!$("#company-modal").classList.contains("hidden")) closeCompanyModal();
      else closeModal();
    });
  }

  async function boot() {
    tg?.ready(); tg?.expand();
    try {
      tg?.setHeaderColor?.("secondary_bg_color");
      tg?.setBackgroundColor?.("bg_color");
    } catch (_) { /* old Telegram client */ }
    bindEvents();
    if (!tg?.initData && ["localhost", "127.0.0.1"].includes(window.location.hostname)) {
      state.data = structuredClone(LOCAL_PREVIEW);
      configureAdminAccess(); hydrateProfileForm(); renderFeed(); renderMy(); renderProfile();
      $("#loading").classList.add("hidden");
      $("#app").classList.remove("hidden");
      const requestedTab = new URLSearchParams(window.location.search).get("tab");
      setTab(new Set(["feed", "my", "group", "profile"]).has(requestedTab) ? requestedTab : "feed");
      return;
    }
    if (!tg?.initData) {
      $("#loading").classList.add("hidden");
      $("#launch-screen").classList.remove("hidden");
      return;
    }
    try {
      state.data = await api("/bootstrap");
      startResearchHeartbeat();
      configureAdminAccess(); hydrateProfileForm(); renderFeed(); renderMy(); renderProfile();
      $("#loading").classList.add("hidden");
      $("#app").classList.remove("hidden");
      const requestedTab = new URLSearchParams(window.location.search).get("tab");
      const publicTabs = new Set(["feed", "my", "group", "profile"]);
      setTab(
        state.data.profile && publicTabs.has(requestedTab)
          ? requestedTab
          : (state.data.profile ? "feed" : "profile"),
      );
    } catch (error) {
      $("#loading").classList.add("hidden");
      $("#launch-screen").classList.remove("hidden");
      $(".launch-copy").textContent = error.message || "Не удалось загрузить приложение. Попробуйте открыть его снова.";
    }
  }

  boot();
})();
