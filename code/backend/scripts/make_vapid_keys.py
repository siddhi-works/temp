"""Print VAPID keys for Web Push. Paste them into .env (VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY)."""

from app.providers.webpush import generate_vapid_keys

if __name__ == "__main__":
    private, public = generate_vapid_keys()
    print(f"VAPID_PRIVATE_KEY={private}\nVAPID_PUBLIC_KEY={public}")
