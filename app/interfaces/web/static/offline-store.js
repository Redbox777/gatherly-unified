(() => {
  "use strict";
  const DB_NAME = "gatherly-private-cache";
  const DB_VERSION = 1;
  const OPT_IN_KEY = "gatherly.offline-storage.enabled";
  let activeUserId = null;
  let dbPromise = null;

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

  const clearUser = async (userId) => {
    if (!userId) return;
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("records", "readwrite");
      const objectStore = tx.objectStore("records");
      const request = objectStore.index("by-user").openKeyCursor(IDBKeyRange.only(userId));
      request.onsuccess = () => {
        const cursor = request.result;
        if (cursor) { objectStore.delete(cursor.primaryKey); cursor.continue(); }
      };
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error || new Error("IndexedDB cleanup failed"));
      tx.onabort = () => reject(tx.error || new Error("IndexedDB cleanup aborted"));
    });
  };

  const api = {
    isEnabled() {
      try { return window.localStorage.getItem(OPT_IN_KEY) === "true"; }
      catch (_) { return false; }
    },
    async setEnabled(enabled) {
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
      if (activeUserId && activeUserId !== nextUserId) await clearUser(activeUserId);
      activeUserId = nextUserId;
      return activeUserId;
    },
    getUser() { return activeUserId; },
    async clearCurrentUser() {
      const previous = activeUserId;
      activeUserId = null;
      if (previous) await clearUser(previous);
    },
    async put(key, value) {
      if (!this.isEnabled() || !activeUserId || typeof key !== "string" || !key) return false;
      const db = await openDatabase();
      await new Promise((resolve, reject) => {
        const tx = db.transaction("records", "readwrite");
        tx.objectStore("records").put({ userId: activeUserId, key, value, updatedAt: Date.now() });
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error || new Error("IndexedDB write failed"));
        tx.onabort = () => reject(tx.error || new Error("IndexedDB write aborted"));
      });
      return true;
    },
    async get(key) {
      if (!this.isEnabled() || !activeUserId || typeof key !== "string" || !key) return null;
      const db = await openDatabase();
      return new Promise((resolve, reject) => {
        const request = db.transaction("records", "readonly").objectStore("records").get([activeUserId, key]);
        request.onsuccess = () => resolve(request.result ? request.result.value : null);
        request.onerror = () => reject(request.error || new Error("IndexedDB read failed"));
      });
    }
  };
  window.GatherlyOfflineStore = api;
})();