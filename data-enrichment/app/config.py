"""Application settings (env-overridable)."""
import os

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """NOTE: env names that collide with the shared repo config.env (BQ_DATASET=cymbal, GEMINI_MODEL=…) are read
    from ENRICH_* aliases only, so `source config.env` can never point the enrichment tables at the `cymbal` dataset."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    project_id: str = ""
    bq_dataset: str = Field("cymbal_enrichment", validation_alias=AliasChoices("ENRICH_BQ_DATASET"))
    bq_source_dataset: str = "cymbal"
    bq_source_table_name: str = "products"
    bq_source_table: str = ""
    bq_location: str = "asia-northeast1"

    # Online (live) enrichment default. The selectable Gemini 3.x+ models are listed/probed in app/models.py;
    # the UI sends the chosen one with every request (X-Enrich-Model).
    gemini_model: str = Field("gemini-3.8-flash", validation_alias=AliasChoices("ENRICH_GEMINI_MODEL"))
    gemini_fallback_model: str = "gemini-3.5-flash"   # used when the primary model is busy (429/503)
    vertex_location: str = "global"
    # Gemini 3.x thinking level (MINIMAL/LOW/MEDIUM/HIGH, "" = model default).
    # Extraction is not reasoning-heavy: LOW keeps accuracy while cutting thought tokens.
    thinking_level: str = "LOW"

    # Batch prediction
    batch_model: str = Field("gemini-3.8-flash", validation_alias=AliasChoices("ENRICH_BATCH_MODEL"))
    batch_location: str = "global"
    batch_gcs_prefix: str = "_batch"                 # JSONL in/out under gs://{image_bucket}/{batch_gcs_prefix}/

    # Governance
    auto_approve_threshold: float = 0.90

    # Cost display (CONTRACT v6): official LIST prices (standard online) per model live in app/pricing.py
    # (price retrieval date = pricing.PRICE_RETRIEVED_AT). Costs are returned in USD (`*_usd` fields, shown by the UI);
    # JPY siblings (`*_jpy`) use this USD->JPY rate (env USD_JPY). No batch discount is applied to any estimate.
    usd_jpy: float = 150.0

    # Use in-memory demo data (no GCP) – for tests / offline dev
    offline: bool = False

    # ── Seasonal image generation (Gemini image models on Vertex AI, global) ──
    image_model: str = "gemini-3.1-flash-image"      # Nano Banana 2 (fast, default)
    image_model_pro: str = "gemini-3-pro-image"      # Nano Banana Pro (high quality / text rendering)
    image_model_fallback: str = "gemini-2.5-flash-image"
    image_bucket: str = ""
    docs_prefix: str = "_docs_uploads"               # document uploads live in the same bucket under this prefix
    image_default_size: int = 600                    # output px (square => size x size); 0 = model output as-is
    image_fidelity_threshold: float = 0.7
    # Public base URL used for image URIs written to the source catalog (cymbal.products.images[].uri)
    public_base_url: str = ""
    # Allow updating the source catalog table (bq_source_table) on image save / document apply
    # (the UI checkbox defaults to OFF; a daily CLONE backup is always taken before the first write)
    update_aics: bool = True

    # Cross links (CONTRACT_EN v14): storefront URL shown as a header icon-only link (env STORE_URL)
    store_url: str = ""

    @model_validator(mode="after")
    def _derive_defaults(self) -> "Settings":
        if not self.project_id:
            self.project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or "your-gcp-project-id"
        if not self.bq_source_table:
            self.bq_source_table = f"{self.project_id}.{self.bq_source_dataset}.{self.bq_source_table_name}"
        if self.bq_dataset == self.bq_source_dataset:
            raise ValueError(f"enrichment dataset must differ from the source dataset ({self.bq_dataset})")
        if not self.image_bucket:
            self.image_bucket = f"products_images_{self.project_id}"
        return self

    @property
    def dataset_ref(self) -> str:
        return f"{self.project_id}.{self.bq_dataset}"


settings = Settings()
