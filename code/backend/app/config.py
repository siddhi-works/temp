"""Application settings, loaded from environment variables (or a .env file).

Every external service is optional at boot: the app starts without keys and
reports what is missing on /ready, so the team can develop the frontend before
keys are shared. Calls to an unconfigured provider fail fast with a clear error
and the pipeline falls back to the next provider.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core -----------------------------------------------------------
    env: str = Field("dev", description="dev | prod. prod enforces webhook signatures and secrets.")
    app_name: str = "Saathi Campus"
    public_base_url: str = "http://localhost:8000"  # used in links sent to students
    frontend_base_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    timezone: str = "Asia/Kolkata"

    database_url: str = "sqlite+aiosqlite:///./suchana.db"
    storage_dir: Path = Path("./storage")

    # Public-deploy guards. If set, first-time setup needs this token, and joining needs this code. Empty = open (local use).
    # Tried in order when the main Gemini model is out of quota (429) or overloaded (503). Each model has its own free quota.
    gemini_fallback_models: str = "gemini-3.1-flash-lite"
    # Extra Google AI Studio keys (ideally from different Google accounts/projects: quota is per project). Tried when one is out of quota.
    gemini_api_key_2: str = ""
    gemini_api_key_3: str = ""
    setup_token: str = ""
    demo_access_code: str = ""

    # Secret used to sign student links and hash phone numbers. Must be set in prod.
    secret_key: str = "dev-only-change-me"
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt phone numbers at rest.
    # Generate with: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
    phone_encryption_key: str | None = None

    # --- Limits (protect budget and the server) --------------------------
    max_upload_mb: int = 15
    max_pdf_pages: int = 10  # scanned pages: each one is paid OCR and slow
    max_text_pdf_pages: int = 40  # PDFs that already contain text are read for free, so they can be longer
    # Read a PDF's own text instead of paying for OCR when it has a clean text layer.
    pdf_text_layer: bool = True
    snap_max_side_px: int = 1600  # board photos are read at this size; larger costs time, not accuracy
    max_image_side_px: int = 2500
    student_daily_forward_limit: int = 25
    public_check_per_minute: int = 10

    # --- Worker -----------------------------------------------------------
    worker_enabled: bool = True
    worker_concurrency: int = 3
    worker_poll_seconds: float = 1.0
    job_lease_seconds: int = 180
    job_max_attempts: int = 5

    # --- Sarvam (OCR, TTS, STT) ---------------------------------------------
    sarvam_api_key: str | None = None
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_tts_speaker: str | None = None  # None = model default voice
    sarvam_stt_model: str | None = None  # None = API default
    # Hard spending cap in paise (₹55 by default, leaving headroom from ₹70 credit).
    sarvam_budget_paise: int = 5500
    sarvam_ocr_paise_per_page: int = 50  # ₹0.50 / page
    sarvam_tts_paise_per_1k_chars: int = 300  # bulbul:v3 ₹3 / 1K chars (v4-flash is ₹2)
    sarvam_stt_paise_per_min: int = 49

    # --- Google -------------------------------------------------------------
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    # Cheaper model for tiny jobs (labels, short answers). Falls back to gemini_model if unset.
    gemini_light_model: str | None = None
    google_tts_api_key: str | None = None  # Cloud TTS REST key (free tier fallback)

    # --- WhatsApp Cloud API -------------------------------------------------
    whatsapp_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_verify_token: str = "suchana-verify"
    whatsapp_app_secret: str | None = None
    whatsapp_graph_version: str = "v23.0"
    # Approved utility template used outside the 24-hour customer service window.
    whatsapp_notice_template: str | None = None
    whatsapp_template_lang: str = "en"

    # --- Web Push (Buzz Codes). Generate with: python -m scripts.make_vapid_keys ---
    vapid_private_key: str | None = None
    vapid_public_key: str | None = None
    vapid_subject: str = "mailto:admin@example.org"

    # Night-before exam briefing: what students are told to carry (comma separated).
    exam_eve_checklist: str = "hall ticket,college ID card,pen"
    exam_eve_hour_local: int = 20

    # Languages always voiced at publish (students' own languages are added automatically).
    publish_langs: str = "en"  # plus every language a student of the college uses; each language costs TTS credit

    # --- Reminders ------------------------------------------------------------
    reminder_offsets_days: str = "3,1,0"  # days before a deadline
    reminder_hour_local: int = 9

    @property
    def is_prod(self) -> bool:
        return self.env.lower() == "prod"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def reminder_offsets(self) -> list[int]:
        return sorted({int(x) for x in self.reminder_offsets_days.split(",") if x.strip()}, reverse=True)

    def missing_for_prod(self) -> list[str]:
        problems = []
        if self.secret_key == "dev-only-change-me":
            problems.append("SECRET_KEY")
        if not self.phone_encryption_key:
            problems.append("PHONE_ENCRYPTION_KEY")
        if self.whatsapp_token and not self.whatsapp_app_secret:
            problems.append("WHATSAPP_APP_SECRET")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
