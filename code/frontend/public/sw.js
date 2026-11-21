/* Samyukta service worker: shows notifications with the student's Buzz Code vibration.
   Vibration patterns only take effect on Android (Chrome); other platforms show a normal notification. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

// Share target: "Share -> Samyukta" from WhatsApp, Gallery or Files. The file is parked in a cache and the page picks it up.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "POST" || url.pathname !== "/share-target") return;
  event.respondWith(
    (async () => {
      try {
        const form = await event.request.formData();
        const file = form.get("notice");
        if (file && typeof file !== "string") {
          const cache = await caches.open("saathi-share");
          await cache.put("/shared-file", new Response(file, { headers: { "Content-Type": file.type || "application/octet-stream" } }));
        }
      } catch (e) {
        /* fall through: the page shows the normal upload button */
      }
      return Response.redirect("/me?shared=1", 303);
    })(),
  );
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: "Samyukta", body: event.data && event.data.text() }; }
  const title = data.title || "Samyukta";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "",
      tag: data.tag || data.type || "saathi",
      renotify: true,
      vibrate: Array.isArray(data.vibrate) ? data.vibrate : [400],
      icon: "/icon.svg",
      badge: "/icon.svg",
      data: { url: data.url || "/me" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/me";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) if ("focus" in c && c.url.includes(url)) return c.focus();
      return self.clients.openWindow(url);
    }),
  );
});
