(() => {
  "use strict";
  const DB_NAME = "gatherly-private-cache";
  const DB_VERSION = 3;
  const OPT_IN_KEY = "gatherly.offline-storage.enabled";
  const LAST_USER_KEY = "gatherly.offline-storage.last-user";
  const QUEUE_STATES = new Set(["pending", "sending", "done", "conflict", "blocked"]);
  const COMMAND_FIELDS = {
    "note.create": { required: ["content"], optional: ["visibility"] },
    "note.update": { required: ["note_id", "content", "visibility"], optional: [] },
    "note.delete": { required: ["note_id"], optional: [] },
    "task.create": { required: ["title"], optional: ["description", "due_at", "assignee_participant_id"] },
    "task.update": { required: ["task_id", "title"], optional: ["description", "due_at", "assignee_participant_id"] },
    "task.complete": { required: ["task_id", "completed"], optional: [] },
    "task.delete": { required: ["task_id"], optional: [] },
    "venue.set": { required: ["venue_name", "address"], optional: ["latitude", "longitude"] },
    "venue.delete": { required: [], optional: [] }
  };
  let activeUserId = null;
  let dbPromise = null;

  const uuid = () => {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
    return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) + "-" +
      hex.slice(16, 20) + "-" + hex.slice(20);
  };
  const isUuid = value => typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const openDatabase = () => {
    if (!("indexedDB" in window)) return Promise.reject(new Error("IndexedDB is unavailable"));
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains("records")) {
          const records = db.createObjectStore("records", { keyPath: ["userId", "key"] });
          records.createIndex("by-user", "userId", { unique: false });
        }
        if (!db.objectStoreNames.contains("outbox")) {
          const outbox = db.createObjectStore("outbox", { keyPath: ["userId", "operationId"] });
          outbox.createIndex("by-user", "userId", { unique: false });
          outbox.createIndex("by-user-event-created", ["userId", "eventId", "createdAt"], { unique: false });
          outbox.createIndex("by-user-state", ["userId", "state"], { unique: false });
        }
        if (!db.objectStoreNames.contains("leases")) {
          db.createObjectStore("leases", { keyPath: ["userId", "eventId"] });
        }
      };
      request.onsuccess = () => {
        const db = request.result;
        db.onversionchange = () => { db.close(); dbPromise = null; };
        resolve(db);
      };
      request.onerror = () => { dbPromise = null; reject(request.error || new Error("IndexedDB open failed")); };
      request.onblocked = () => { dbPromise = null; reject(new Error("IndexedDB upgrade is blocked")); };
    });
    return dbPromise;
  };
  const transactionDone = tx => new Promise((resolve, reject) => {
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error || new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted"));
  });
  const clearUser = async userId => {
    if (!userId) return;
    const db = await openDatabase();
    const tx = db.transaction("records", "readwrite");
    const store = tx.objectStore("records");
    const request = store.index("by-user").openKeyCursor(IDBKeyRange.only(userId));
    request.onsuccess = () => {
      const cursor = request.result;
      if (cursor) { store.delete(cursor.primaryKey); cursor.continue(); }
    };
    await transactionDone(tx);
  };
  const readOutbox = async userId => {
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
      const request = db.transaction("outbox", "readonly").objectStore("outbox")
        .index("by-user").getAll(IDBKeyRange.only(userId));
      request.onsuccess = () => resolve(request.result || []);
      request.onerror = () => reject(request.error || new Error("Outbox read failed"));
    });
  };
  const validateCommand = (kind, payload) => {
    const schema = COMMAND_FIELDS[kind];
    if (!schema || !payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("Недопустимая офлайн-команда.");
    }
    const keys = Object.keys(payload);
    if (schema.required.some(key => !Object.prototype.hasOwnProperty.call(payload, key)) ||
        keys.some(key => !schema.required.includes(key) && !schema.optional.includes(key))) {
      throw new Error("Неверный состав данных офлайн-команды.");
    }
    const encoded = JSON.stringify(payload);
    if (encoded.length > 12000) throw new Error("Данные офлайн-команды слишком велики.");
    if (kind.startsWith("note.") && kind !== "note.delete") {
      if (typeof payload.content !== "string" || !payload.content.trim() || payload.content.length > 5000) {
        throw new Error("Некорректное содержимое заметки.");
      }
      if (kind === "note.update" && !["private", "shared"].includes(payload.visibility)) {
        throw new Error("Недопустимая видимость заметки.");
      }
      if (kind === "note.create" && payload.visibility !== undefined &&
          !["private", "shared"].includes(payload.visibility)) {
        throw new Error("Недопустимая видимость заметки.");
      }
    }
    if (kind.startsWith("task.") && ["task.create", "task.update"].includes(kind)) {
      if (typeof payload.title !== "string" || !payload.title.trim() || payload.title.length > 160) {
        throw new Error("Некорректное название задачи.");
      }
      if (payload.description !== undefined &&
          (typeof payload.description !== "string" || payload.description.length > 2000)) {
        throw new Error("Некорректное описание задачи.");
      }
      if (payload.due_at !== undefined && payload.due_at !== null) {
        if (typeof payload.due_at !== "string" || !Number.isFinite(Date.parse(payload.due_at)) ||
            !/(Z|[+-]\\d{2}:?\\d{2})$/i.test(payload.due_at)) {
          throw new Error("Срок задачи должен содержать дату, время и часовой пояс.");
        }
      }
      if (payload.assignee_participant_id !== undefined &&
          payload.assignee_participant_id !== null && !isUuid(payload.assignee_participant_id)) {
        throw new Error("Некорректный исполнитель задачи.");
      }
    }
    if (payload.visibility !== undefined && !["private", "shared"].includes(payload.visibility)) {
      throw new Error("Недопустимая видимость заметки.");
    }
    if (kind === "task.complete" && typeof payload.completed !== "boolean") {
      throw new Error("Статус задачи должен быть логическим значением.");
    }
    if (kind === "venue.set") {
      if (typeof payload.venue_name !== "string" || !payload.venue_name.trim() ||
          payload.venue_name.length > 160 || typeof payload.address !== "string" ||
          !payload.address.trim() || payload.address.length > 500) {
        throw new Error("Некорректные данные места проведения.");
      }
      const hasLatitude = payload.latitude !== undefined && payload.latitude !== null;
      const hasLongitude = payload.longitude !== undefined && payload.longitude !== null;
      if (hasLatitude !== hasLongitude ||
          (hasLatitude && (typeof payload.latitude !== "number" || !Number.isFinite(payload.latitude) ||
            payload.latitude < -90 || payload.latitude > 90 ||
            typeof payload.longitude !== "number" || !Number.isFinite(payload.longitude) ||
            payload.longitude < -180 || payload.longitude > 180))) {
        throw new Error("Некорректные координаты места проведения.");
      }
    }
    return JSON.parse(encoded);
  };
  const requireActive = () => {
    if (!activeUserId || !api.isEnabled()) throw new Error("Локальное хранение не включено для активного аккаунта.");
    return activeUserId;
  };

  const api = {
    isEnabled() {
      try { return window.localStorage.getItem(OPT_IN_KEY) === "true"; }
      catch (_) { return false; }
    },
    async setEnabled(enabled, options = {}) {
      if (!enabled && activeUserId) {
        const pending = (await readOutbox(activeUserId)).filter(item => item.state !== "done");
        if (pending.length && options.discardPending !== true) {
          throw new Error("Есть несинхронизированные изменения. Сначала синхронизируйте их или явно удалите очередь.");
        }
        await this.purgeOutbox();
      }
      try {
        if (enabled) window.localStorage.setItem(OPT_IN_KEY, "true");
        else window.localStorage.removeItem(OPT_IN_KEY);
      } catch (_) {
        throw new Error("Не удалось сохранить настройку локального хранения.");
      }
      if (!enabled && activeUserId) await clearUser(activeUserId);
      return Boolean(enabled);
    },
    async setUser(userId) {
      const nextUserId = typeof userId === "string" && userId.trim() ? userId : null;
      let previousUserId = activeUserId;
      try { previousUserId = previousUserId || window.localStorage.getItem(LAST_USER_KEY); } catch (_) {}
      activeUserId = null;
      if (previousUserId && previousUserId !== nextUserId) await clearUser(previousUserId);
      activeUserId = nextUserId;
      try {
        if (nextUserId) window.localStorage.setItem(LAST_USER_KEY, nextUserId);
        else window.localStorage.removeItem(LAST_USER_KEY);
      } catch (_) {}
      return activeUserId;
    },
    getUser() { return activeUserId; },
    getRememberedUser() {
      if (!this.isEnabled()) return null;
      try {
        const userId = window.localStorage.getItem(LAST_USER_KEY);
        return typeof userId === "string" && userId.trim() ? userId : null;
      } catch (_) { return null; }
    },
    async clearCurrentCache() {
      const userId = activeUserId;
      if (!userId) return;
      const db = await openDatabase();
      const tx = db.transaction("records", "readwrite");
      const store = tx.objectStore("records");
      const request = store.index("by-user").openCursor(IDBKeyRange.only(userId));
      request.onsuccess = () => {
        const cursor = request.result;
        if (cursor) {
          if (typeof cursor.value.key !== "string" || !cursor.value.key.startsWith("sync-revision:")) {
            cursor.delete();
          }
          cursor.continue();
        }
      };
      await transactionDone(tx);
    },
    async clearCurrentUser() {
      const previous = activeUserId;
      activeUserId = null;
      try { window.localStorage.removeItem(LAST_USER_KEY); } catch (_) {}
      if (previous) await clearUser(previous);
    },
    async purgeCurrentUser() {
      const previous = activeUserId;
      activeUserId = null;
      try { window.localStorage.removeItem(LAST_USER_KEY); } catch (_) {}
      if (previous) {
        const db = await openDatabase();
        const tx = db.transaction(["records", "outbox"], "readwrite");
        for (const name of ["records", "outbox"]) {
          const store = tx.objectStore(name);
          const request = store.index("by-user").openKeyCursor(IDBKeyRange.only(previous));
          request.onsuccess = () => {
            const cursor = request.result;
            if (cursor) { store.delete(cursor.primaryKey); cursor.continue(); }
          };
        }
        await transactionDone(tx);
      }
    },
    async put(key, value) {
      const userId = requireActive();
      if (typeof key !== "string" || !key) return false;
      const db = await openDatabase();
      const tx = db.transaction("records", "readwrite");
      tx.objectStore("records").put({ userId, key, value, updatedAt: Date.now() });
      await transactionDone(tx);
      return true;
    },
    async get(key) {
      const userId = requireActive();
      if (typeof key !== "string" || !key) return null;
      const db = await openDatabase();
      return new Promise((resolve, reject) => {
        const request = db.transaction("records", "readonly").objectStore("records").get([userId, key]);
        request.onsuccess = () => resolve(request.result ? request.result.value : null);
        request.onerror = () => reject(request.error || new Error("IndexedDB read failed"));
      });
    },
    async enqueue(command) {
      const userId = requireActive();
      if (!command || !isUuid(command.eventId) ||
          !Number.isSafeInteger(command.baseRevision) || command.baseRevision < 1) {
        throw new Error("Некорректное событие или базовая ревизия.");
      }
      const kind = command.kind;
      const payload = validateCommand(kind, command.payload);
      const operationId = command.operationId || uuid();
      if (!isUuid(operationId)) throw new Error("Некорректный идентификатор операции.");
      const record = {
        userId, operationId, eventId: command.eventId, baseRevision: command.baseRevision,
        kind, payload, state: "pending", attempts: 0, createdAt: Date.now(),
        updatedAt: Date.now(), lastError: null
      };
      const db = await openDatabase();
      const tx = db.transaction("outbox", "readwrite");
      tx.objectStore("outbox").add(record);
      await transactionDone(tx);
      return { ...record };
    },
    async listOutbox() {
      const items = await readOutbox(requireActive());
      return items.sort((a, b) => a.createdAt - b.createdAt);
    },
    async getEventRevision(eventId) {
      if (!isUuid(eventId)) return null;
      const value = await this.get("sync-revision:" + eventId);
      return value && Number.isSafeInteger(value.revision) && value.revision >= 1
        ? value.revision : null;
    },
    async setEventRevision(eventId, revision) {
      if (!isUuid(eventId) || !Number.isSafeInteger(revision) || revision < 1) return false;
      return this.put("sync-revision:" + eventId, { revision, updatedAt: Date.now() });
    },
    async recoverSending() {
      const items = await this.listOutbox();
      for (const item of items) {
        if (item.state === "sending") {
          await this.updateOutboxState(item.operationId, "pending", {
            error: "Отправка была прервана; безопасно повторим ту же операцию."
          });
        }
      }
    },
    async acquireLease(eventId, ownerId, ttlMs = 45000) {
      const userId = requireActive();
      if (!isUuid(eventId) || !isUuid(ownerId)) return false;
      const db = await openDatabase();
      const tx = db.transaction("leases", "readwrite");
      const done = transactionDone(tx);
      const store = tx.objectStore("leases");
      const request = store.get([userId, eventId]);
      let acquired = false;
      request.onsuccess = () => {
        const lease = request.result;
        const now = Date.now();
        if (!lease || lease.expiresAt <= now || lease.ownerId === ownerId) {
          store.put({ userId, eventId, ownerId, expiresAt: now + ttlMs });
          acquired = true;
        }
      };
      await done;
      return acquired;
    },
    async releaseLease(eventId, ownerId) {
      const userId = requireActive();
      if (!isUuid(eventId) || !isUuid(ownerId)) return false;
      const db = await openDatabase();
      const tx = db.transaction("leases", "readwrite");
      const done = transactionDone(tx);
      const store = tx.objectStore("leases");
      const request = store.get([userId, eventId]);
      request.onsuccess = () => {
        if (request.result?.ownerId === ownerId) store.delete([userId, eventId]);
      };
      await done;
      return true;
    },
    async updateOutboxState(operationId, state, details = {}) {
      const userId = requireActive();
      if (!isUuid(operationId) || !QUEUE_STATES.has(state)) throw new Error("Недопустимое состояние очереди.");
      const db = await openDatabase();
      const tx = db.transaction("outbox", "readwrite");
      const store = tx.objectStore("outbox");
      const key = [userId, operationId];
      const request = store.get(key);
      request.onsuccess = () => {
        if (!request.result) return;
        const item = request.result;
        item.state = state;
        item.updatedAt = Date.now();
        if (state === "sending") item.attempts += 1;
        item.lastError = typeof details.error === "string" ? details.error.slice(0, 500) : null;
        if (Number.isFinite(details.retryAt)) item.retryAt = details.retryAt;
        else if (state !== "pending") delete item.retryAt;
        if (Number.isSafeInteger(details.serverRevision) && details.serverRevision >= 1) {
          item.serverRevision = details.serverRevision;
        }
        store.put(item);
      };
      await transactionDone(tx);
      return true;
    },
    async removeOutboxItem(operationId) {
      const userId = requireActive();
      if (!isUuid(operationId)) throw new Error("Некорректный идентификатор операции.");
      const db = await openDatabase();
      const tx = db.transaction("outbox", "readwrite");
      tx.objectStore("outbox").delete([userId, operationId]);
      await transactionDone(tx);
    },
    async purgeOutbox() {
      const userId = requireActive();
      const db = await openDatabase();
      const tx = db.transaction("outbox", "readwrite");
      const store = tx.objectStore("outbox");
      const request = store.index("by-user").openKeyCursor(IDBKeyRange.only(userId));
      request.onsuccess = () => {
        const cursor = request.result;
        if (cursor) { store.delete(cursor.primaryKey); cursor.continue(); }
      };
      await transactionDone(tx);
    }
  };
  window.GatherlyOfflineStore = api;
})();