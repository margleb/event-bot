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
  const LOCAL_PREVIEW = {
    user: { id: 1, first_name: "Дима", username: "preview", photo_url: null },
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
      { id: 101, title: "Джаз на крыше: вечерний концерт", description: "Живая музыка, закат над Москвой и камерная атмосфера. В программе — современный джаз и авторские аранжировки.", city: "Москва", address: "Берсеневская набережная, 6", date: "2026-09-04T19:30:00", end_date: null, price: "от 1 800 ₽", tags: ["джаз", "концерт", "на крыше"], venue: "Красный Октябрь", source_url: "https://kudago.com/", intent: "interested", visible: false },
      { id: 102, title: "Новая Третьяковка: искусство XX века", description: "Большая экспозиция русского искусства XX века и специальная кураторская программа выходного дня.", city: "Москва", address: "Крымский Вал, 10", date: "2026-09-05T13:00:00", end_date: null, price: "700 ₽", tags: ["выставка", "искусство", "музей"], venue: "Новая Третьяковка", source_url: "https://kudago.com/", intent: null, visible: false },
      { id: 103, title: "Открытый микрофон на Китай-городе", description: "Начинающие и опытные комики проверяют новый материал в небольшом клубе.", city: "Москва", address: "Покровка, 17", date: "2026-09-06T20:00:00", end_date: null, price: "Бесплатно", tags: ["стендап", "комедия"], venue: "Клуб 17", source_url: "https://kudago.com/", intent: "going", visible: true },
    ],
    my_events: [],
  };
  LOCAL_PREVIEW.my_events = LOCAL_PREVIEW.events.filter((event) => event.intent);

  const state = {
    data: null,
    tab: "feed",
    myFilter: "all",
    selectedInterests: new Set(),
    selectedDays: new Set(),
    budget: null,
    group: [null, null],
    modalEventId: null,
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
    const titles = { profile: "Ваш профиль", my: "Ваши планы", group: "Ваша компания" };
    $("#header-title").textContent = titles[state.tab] || `Привет, ${user.first_name}!`;
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

  function setTab(tab) {
    if (!state.data.profile && tab !== "profile") tab = "profile";
    state.tab = tab;
    $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === tab));
    $$(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
    window.scrollTo({ top: 0, behavior: "smooth" });
    renderHeader();
    if (tab === "my") renderMy();
    if (tab === "group") renderGroup();
    if (tab === "profile") renderProfile();
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
    $("#modal-actions").innerHTML = eventActions(event);
    $("#event-modal").classList.remove("hidden");
    document.body.style.overflow = "hidden";
    tg?.BackButton?.show();
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
      hydrateProfileForm(); renderFeed(); renderMy(); renderGroup();
      haptic("medium"); toast("Профиль сохранён"); setTab("feed");
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.classList.remove("hidden");
    } finally {
      button.disabled = false;
      button.querySelector("span").textContent = "Сохранить и подобрать";
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
    $("#my-filters").addEventListener("click", (event) => {
      const button = event.target.closest("[data-filter]"); if (!button) return;
      state.myFilter = button.dataset.filter;
      $$(".filter-chip").forEach((chip) => chip.classList.toggle("active", chip === button));
      renderMy();
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
      hydrateProfileForm(); renderFeed(); renderMy(); renderProfile();
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
      hydrateProfileForm(); renderFeed(); renderMy(); renderProfile();
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
