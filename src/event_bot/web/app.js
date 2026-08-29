(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
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
    [null, null, "Неважно"], [1, 1, "Самостоятельно"], [2, 2, "Вдвоём"],
    [3, 5, "3–5 человек"], [6, 10, "6–10 человек"],
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
  const LOCAL_PREVIEW = {
    user: { id: 1, first_name: "Дима", username: "preview", photo_url: null },
    is_admin: true,
    profile: {
      interests: ["Концерты", "Выставки", "Стендап"], avoid: ["спорт"],
      days: ["fri", "sat", "sun"], budget_rub: 3000,
      preferred_group_size_min: 2, preferred_group_size_max: 5,
    },
    digest_weekday: 4,
    group_matching_enabled: true,
    group: {
      id: 12, title: "Концерты · Выставки", status: "active",
      topics: ["Концерты", "Выставки"], member_count: 3,
      minimum_members: 3, maximum_members: 5,
      members: [
        { name: "Вы", is_me: true, common_interests: ["Концерты", "Выставки"], group_size: "2–5 человек" },
        { name: "Аня", is_me: false, common_interests: ["Концерты"], group_size: "3–5 человек" },
        { name: "Максим", is_me: false, common_interests: ["Выставки"], group_size: "3–5 человек" },
      ],
    },
    events: [
      { id: 101, title: "Джаз на крыше: вечерний концерт", description: "Живая музыка, закат над Москвой и камерная атмосфера. В программе — современный джаз и авторские аранжировки.", city: "Москва", address: "Берсеневская набережная, 6", date: "2026-09-04T19:30:00", end_date: null, price: "от 1 800 ₽", tags: ["джаз", "концерт", "на крыше"], venue: "Красный Октябрь", source_url: "https://kudago.com/", source_id: "kudago", source_name: "KudaGo", source_mark: "K", intent: "interested", visible: false },
      { id: 102, title: "Новая Третьяковка: искусство XX века", description: "Большая экспозиция русского искусства XX века и специальная кураторская программа выходного дня.", city: "Москва", address: "Крымский Вал, 10", date: "2026-09-05T13:00:00", end_date: null, price: "700 ₽", tags: ["выставка", "искусство", "музей"], venue: "Новая Третьяковка", source_url: "https://timepad.ru/", source_id: "timepad", source_name: "timepad", source_mark: "tp", intent: null, visible: false },
      { id: 103, title: "Открытый микрофон на Китай-городе", description: "Начинающие и опытные комики проверяют новый материал в небольшом клубе.", city: "Москва", address: "Покровка, 17", date: "2026-09-06T20:00:00", end_date: null, price: "Бесплатно", tags: ["стендап", "комедия"], venue: "Клуб 17", source_url: "https://ticketmaster.com/", source_id: "ticketmaster", source_name: "Ticketmaster", source_mark: "★", intent: "going", visible: true },
    ],
    my_events: [],
  };
  LOCAL_PREVIEW.my_events = LOCAL_PREVIEW.events.filter((event) => event.intent);

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

  const state = {
    data: null,
    tab: "feed",
    myFilter: "all",
    selectedInterests: new Set(),
    selectedDays: new Set(),
    budget: null,
    group: [null, null],
    modalEventId: null,
    adminData: null,
    adminDays: 30,
    adminMetric: "active_users",
    adminLoading: false,
  };

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

  function trackMiniapp(event) {
    if (!tg?.initData) return;
    void api("/track", {
      method: "POST",
      body: JSON.stringify({ event }),
    }).catch(() => {});
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

  function eventActions(event) {
    return `
      <button class="intent-button ${event.intent === "interested" ? "active" : ""}" data-intent="interested" data-id="${event.id}">Интересно</button>
      <button class="intent-button ${event.intent === "going" ? "active" : ""}" data-intent="going" data-id="${event.id}">Пойду</button>
      <button class="intent-button nope ${event.intent === "not_going" ? "active" : ""}" data-intent="not_going" data-id="${event.id}" aria-label="Не предлагать">×</button>`;
  }

  function eventCard(event, showVisibility = false) {
    const date = formatWhen(event.date);
    const variant = variantFor(event);
    const place = [event.venue, event.address].filter(Boolean).join(" · ");
    const tags = (event.tags || []).slice(0, 3).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
    const visibility = showVisibility && ["going", "interested"].includes(event.intent)
      ? `<div class="visibility-row"><span>Видно другим участникам</span><button class="switch ${event.visible ? "on" : ""}" data-visible="${!event.visible}" data-id="${event.id}" aria-label="Изменить видимость"></button></div>`
      : "";
    return `
      <article class="event-card" data-event-id="${event.id}">
        <div class="card-visual variant-${variant}">
          ${sourceBadge(event)}
          <span class="event-glyph">${GLYPHS[variant]}</span>
          <div class="date-tile"><b>${date.day}</b><span>${date.month}</span></div>
          <span class="visual-price">${escapeHtml(event.price)}</span>
        </div>
        <div class="card-body">
          <div class="card-title-row"><h3 class="card-title">${escapeHtml(event.title)}</h3><button class="open-card" data-open="${event.id}" aria-label="Подробнее">↗</button></div>
          <div class="card-meta">
            <span><b>◷</b>${date.time}</span>
            <span><b>⌖</b>${escapeHtml(place || "Москва")}</span>
          </div>
          <div class="tag-row">${tags}</div>
          <div class="card-actions">${eventActions(event)}</div>
          ${visibility}
        </div>
      </article>`;
  }

  function emptyState(kind) {
    if (kind === "feed") return `<div class="empty-state"><span>⌁</span><h3>Пока ничего точного</h3><p>Попробуйте выбрать другие дни или увеличить бюджет в профиле.</p></div>`;
    return `<div class="empty-state"><span>◒</span><h3>Планы ещё впереди</h3><p>Отмечайте интересные события в афише — они появятся здесь.</p></div>`;
  }

  function renderHeader() {
    const user = state.data.user;
    const titles = { profile: "Ваш профиль", my: "Ваши планы", group: "Ваша компания", admin: "Работа бота" };
    const kickers = { admin: "АНАЛИТИКА · АКТУАЛЬНЫЕ ДАННЫЕ" };
    $("#header-title").textContent = titles[state.tab] || `Привет, ${user.first_name}!`;
    $("#header-kicker").textContent = kickers[state.tab] || "ВАША АФИША";
    const avatar = $("#avatar");
    avatar.textContent = (user.first_name || "М").slice(0, 1).toUpperCase();
    if (user.photo_url) avatar.innerHTML = `<img src="${escapeHtml(user.photo_url)}" alt="">`;
  }

  function renderFeed() {
    const events = state.data.events || [];
    $("#event-count").textContent = events.length;
    $("#event-list").innerHTML = events.length ? events.map((event) => eventCard(event)).join("") : emptyState("feed");
  }

  function renderMy() {
    const events = (state.data.my_events || []).filter((event) => state.myFilter === "all" || event.intent === state.myFilter);
    $("#my-event-list").innerHTML = events.length ? events.map((event) => eventCard(event, true)).join("") : emptyState("my");
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
    $("#group-matching-input").checked = Boolean(state.data.group_matching_enabled);
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
    $("#onboarding-copy").classList.toggle("hidden", Boolean(state.data.profile));
  }

  function renderGroup() {
    const container = $("#group-content");
    if (!state.data.group_matching_enabled) {
      container.innerHTML = `
        <div class="group-disabled">
          <div class="group-disabled-art" aria-hidden="true"><i></i><i></i><i></i></div>
          <h3>Компания — только по желанию</h3>
          <p>Включите подбор в профиле, и мы найдём 3–5 человек с пересекающимися интересами. Выйти можно в любой момент.</p>
          <button class="primary-button" type="button" data-go-profile>Настроить подбор</button>
        </div>`;
      return;
    }

    const group = state.data.group;
    if (!group) {
      container.innerHTML = `<div class="group-disabled"><h3>Начинаем поиск</h3><p>Сохраняем ваши настройки и подбираем совместимую компанию.</p></div>`;
      return;
    }

    const ready = group.status === "active";
    const missing = Math.max(0, group.minimum_members - group.member_count);
    const progressTarget = ready ? group.maximum_members : group.minimum_members;
    const progress = Math.min(100, Math.round((group.member_count / progressTarget) * 100));
    const topics = (group.topics || []).map((topic) => `<span class="tag">${escapeHtml(topic)}</span>`).join("");
    const members = (group.members || []).map((member) => {
      const initial = (member.name || "У").slice(0, 1).toLocaleUpperCase("ru");
      const common = (member.common_interests || []).join(", ");
      return `
        <article class="member-card ${member.is_me ? "me" : ""}">
          <span class="member-avatar">${escapeHtml(initial)}</span>
          <div>
            <h4>${escapeHtml(member.name)}</h4>
            <p>${common ? `Общее: ${escapeHtml(common)}` : "Профиль совместим с группой"} · ${escapeHtml(member.group_size)}</p>
          </div>
        </article>`;
    }).join("");
    const statusCopy = ready
      ? "Группа собрана. Она останется вашей, пока вы не выключите подбор в профиле."
      : `Уже ${group.member_count} из ${group.minimum_members}. ${missing === 1 ? "Ищем ещё одного участника." : `Ищем ещё ${missing} участников.`}`;

    container.innerHTML = `
      <article class="group-hero">
        <span class="group-status">${ready ? "ГРУППА СОБРАНА" : "ИДЁТ ПОДБОР"}</span>
        <h3>${escapeHtml(group.title)}</h3>
        <p>${statusCopy}</p>
        <div class="tag-row">${topics}</div>
        <div class="group-progress"><span style="width:${progress}%"></span></div>
      </article>
      <h3 class="group-members-title">Участники · ${group.member_count}/${group.maximum_members}</h3>
      <div class="member-list">${members}</div>`;
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
    $("#admin-nav-button").classList.toggle("hidden", !allowed);
    $("#bottom-nav").classList.toggle("admin-enabled", allowed);
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
    renderAdminChart();
  }

  async function loadAdminAnalytics(force = false) {
    if (!state.data?.is_admin || state.adminLoading) return;
    if (state.adminData && !force && state.adminData.days === state.adminDays) {
      renderAdminDashboard();
      return;
    }
    state.adminLoading = true;
    $("#admin-refresh").classList.add("loading");
    $("#admin-updated").textContent = "Загружаем актуальные данные…";
    try {
      const isPreview = !tg?.initData && ["localhost", "127.0.0.1"].includes(window.location.hostname);
      state.adminData = isPreview ? localAdminPreview(state.adminDays) : await api(`/admin/analytics?days=${state.adminDays}`);
      renderAdminDashboard();
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
    trackMiniapp(`tab.${tab}`);
  }

  function mergeEvent(updated) {
    for (const collection of [state.data.events, state.data.my_events]) {
      const index = collection.findIndex((event) => event.id === updated.id);
      if (index >= 0) collection[index] = { ...collection[index], ...updated };
    }
    const myIndex = state.data.my_events.findIndex((event) => event.id === updated.id);
    if (myIndex < 0) state.data.my_events.push(updated);
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

  function findEvent(id) {
    return [...(state.data.events || []), ...(state.data.my_events || [])].find((event) => event.id === Number(id));
  }

  function openModal(id) {
    const event = findEvent(id);
    if (!event) return;
    state.modalEventId = event.id;
    const variant = variantFor(event);
    $("#modal-visual").className = `modal-visual variant-${variant}`;
    $("#modal-visual span").textContent = GLYPHS[variant];
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
    trackMiniapp("event_details");
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
    const groupMatchingEnabled = $("#group-matching-input").checked;
    if (!state.selectedInterests.size) {
      errorBox.textContent = "Выберите хотя бы один интерес.";
      errorBox.classList.remove("hidden");
      return;
    }
    if (budget !== null && (!Number.isFinite(budget) || budget < 0 || budget > 1000000)) {
      errorBox.textContent = "Проверьте указанную сумму.";
      errorBox.classList.remove("hidden");
      return;
    }
    if (groupMatchingEnabled && ((state.group[1] !== null && state.group[1] < 3) || (state.group[0] !== null && state.group[0] > 5))) {
      errorBox.textContent = "Для подбора компании выберите «Неважно» или размер, совместимый с группой 3–5 человек.";
      errorBox.classList.remove("hidden");
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
          group_matching_enabled: groupMatchingEnabled,
        }),
      });
      configureAdminAccess(); hydrateProfileForm(); renderFeed(); renderMy(); renderGroup();
      haptic("medium"); toast("Профиль сохранён"); setTab("feed");
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.classList.remove("hidden");
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
      if (button) { haptic(); setTab(button.dataset.tab); }
    });
    document.addEventListener("click", (event) => {
      const intent = event.target.closest("[data-intent]");
      if (intent) setIntent(Number(intent.dataset.id), intent.dataset.intent);
      const opener = event.target.closest("[data-open]");
      if (opener) openModal(Number(opener.dataset.open));
      const visibility = event.target.closest("[data-visible]");
      if (visibility) setVisibility(Number(visibility.dataset.id), visibility.dataset.visible === "true");
      const groupSettings = event.target.closest("[data-go-profile]");
      if (groupSettings) setTab("profile");
      const source = event.target.closest("#modal-source");
      if (source) trackMiniapp("external_source");
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
    $("#my-filters").addEventListener("click", (event) => {
      const button = event.target.closest("[data-filter]"); if (!button) return;
      state.myFilter = button.dataset.filter;
      $$(".filter-chip").forEach((chip) => chip.classList.toggle("active", chip === button));
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
    $("#modal-close").addEventListener("click", closeModal);
    $("#modal-backdrop").addEventListener("click", closeModal);
    tg?.BackButton?.onClick(closeModal);
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
      setTab("feed");
      return;
    }
    if (!tg?.initData) {
      $("#loading").classList.add("hidden");
      $("#launch-screen").classList.remove("hidden");
      return;
    }
    try {
      state.data = await api("/bootstrap");
      configureAdminAccess(); hydrateProfileForm(); renderFeed(); renderMy(); renderProfile();
      $("#loading").classList.add("hidden");
      $("#app").classList.remove("hidden");
      setTab(state.data.profile ? "feed" : "profile");
    } catch (error) {
      $("#loading").classList.add("hidden");
      $("#launch-screen").classList.remove("hidden");
      $(".launch-copy").textContent = error.message || "Не удалось загрузить приложение. Попробуйте открыть его снова.";
    }
  }

  boot();
})();
