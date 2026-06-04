"""A2: split competitor_products into a stable catalog + scrape observations

competitor_products was a partitioned scrape log; matches referenced its
composite PK (id, scraped_at), so re-scraping created new ids and stranded
matches. Split into:
  - competitor_products: stable catalog (simple PK, UNIQUE per competitor)
  - competitor_price_observations: partitioned scrape log (FK to catalog)
and point product_competitor_matches at the stable catalog id.

Greenfield (test data): drop and recreate the competitor tables.

Revision ID: 0013_competitor_split
Revises: 0012_store_price_temporal
Create Date: 2026-06-04
"""

from alembic import op

revision = "0013_competitor_split"
down_revision = "0012_store_price_temporal"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
DROP TABLE IF EXISTS public.product_competitor_matches;
DROP TABLE IF EXISTS public.competitor_products CASCADE;

-- Stable catalog of competitor products.
CREATE TABLE public.competitor_products (
  id               bigint GENERATED ALWAYS AS IDENTITY,
  competitor_id    bigint NOT NULL,
  external_ref     varchar(120),
  product_name     varchar(180) NOT NULL,
  category         varchar(80),
  size_description varchar(80),
  is_active        boolean NOT NULL DEFAULT true,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_competitor_products PRIMARY KEY (id),
  CONSTRAINT uq_competitor_products UNIQUE (competitor_id, product_name, size_description),
  CONSTRAINT fk_cp_competitor FOREIGN KEY (competitor_id) REFERENCES public.competitors(id) ON DELETE CASCADE
);

-- Partitioned scrape log; one row per observation.
CREATE TABLE public.competitor_price_observations (
  id                    bigint GENERATED ALWAYS AS IDENTITY,
  competitor_product_id bigint NOT NULL,
  price                 price_amount,
  currency_code         char(3) NOT NULL DEFAULT 'COP',
  source_url            text,
  scraped_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_competitor_price_observations PRIMARY KEY (id, scraped_at),
  CONSTRAINT fk_cpo_product  FOREIGN KEY (competitor_product_id) REFERENCES public.competitor_products(id) ON DELETE CASCADE,
  CONSTRAINT fk_cpo_currency FOREIGN KEY (currency_code)         REFERENCES public.currencies(code)        ON UPDATE CASCADE ON DELETE RESTRICT
) PARTITION BY RANGE (scraped_at);
CREATE TABLE public.competitor_price_observations_2025 PARTITION OF public.competitor_price_observations
  FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE public.competitor_price_observations_2026 PARTITION OF public.competitor_price_observations
  FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE public.competitor_price_observations_2027 PARTITION OF public.competitor_price_observations
  FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE TABLE public.product_competitor_matches (
  id                    bigint GENERATED ALWAYS AS IDENTITY,
  our_product_id        bigint NOT NULL,
  our_size_id           bigint NOT NULL,
  competitor_product_id bigint NOT NULL,
  matched_by            varchar(120),
  matched_at            timestamptz NOT NULL DEFAULT now(),
  notes                 text,
  CONSTRAINT pk_product_competitor_matches PRIMARY KEY (id),
  CONSTRAINT uq_product_competitor_matches UNIQUE (our_product_id, our_size_id, competitor_product_id),
  CONSTRAINT fk_pcm_product FOREIGN KEY (our_product_id) REFERENCES public.products(id)      ON DELETE CASCADE,
  CONSTRAINT fk_pcm_size    FOREIGN KEY (our_size_id)    REFERENCES public.product_sizes(id) ON DELETE CASCADE,
  CONSTRAINT fk_pcm_competitor_product FOREIGN KEY (competitor_product_id) REFERENCES public.competitor_products(id) ON DELETE CASCADE
);

CREATE INDEX idx_cp_competitor          ON public.competitor_products (competitor_id);
CREATE INDEX idx_cpo_product_scraped    ON public.competitor_price_observations (competitor_product_id, scraped_at DESC);
CREATE INDEX idx_pcm_competitor_product ON public.product_competitor_matches (competitor_product_id);
CREATE INDEX idx_pcm_size               ON public.product_competitor_matches (our_size_id);

CREATE TRIGGER trg_competitor_products_set_updated_at
  BEFORE UPDATE ON public.competitor_products
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS public.product_competitor_matches;
DROP TABLE IF EXISTS public.competitor_price_observations;
DROP TABLE IF EXISTS public.competitor_products;

CREATE TABLE public.competitor_products (
  id               bigint GENERATED ALWAYS AS IDENTITY,
  competitor_id    bigint NOT NULL,
  product_name     varchar(180),
  category         varchar(80),
  size_description varchar(80),
  price            price_amount,
  source_url       text,
  scraped_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_competitor_products PRIMARY KEY (id, scraped_at),
  CONSTRAINT fk_cp_competitor FOREIGN KEY (competitor_id) REFERENCES public.competitors(id) ON DELETE CASCADE
) PARTITION BY RANGE (scraped_at);
CREATE TABLE public.competitor_products_2025 PARTITION OF public.competitor_products
  FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE public.competitor_products_2026 PARTITION OF public.competitor_products
  FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE public.competitor_products_default PARTITION OF public.competitor_products DEFAULT;

CREATE TABLE public.product_competitor_matches (
  id                            bigint GENERATED ALWAYS AS IDENTITY,
  our_product_id                bigint NOT NULL,
  our_size_id                   bigint NOT NULL,
  competitor_product_id         bigint NOT NULL,
  competitor_product_scraped_at timestamptz NOT NULL,
  matched_by                    varchar(120),
  matched_at                    timestamptz NOT NULL DEFAULT now(),
  notes                         text,
  CONSTRAINT pk_product_competitor_matches PRIMARY KEY (id),
  CONSTRAINT uq_product_competitor_matches UNIQUE (our_product_id, our_size_id, competitor_product_id),
  CONSTRAINT fk_pcm_product FOREIGN KEY (our_product_id) REFERENCES public.products(id)      ON DELETE CASCADE,
  CONSTRAINT fk_pcm_size    FOREIGN KEY (our_size_id)    REFERENCES public.product_sizes(id) ON DELETE CASCADE,
  CONSTRAINT fk_pcm_competitor_product FOREIGN KEY (competitor_product_id, competitor_product_scraped_at)
    REFERENCES public.competitor_products (id, scraped_at) ON DELETE CASCADE
);
CREATE INDEX idx_cp_competitor_scraped  ON public.competitor_products (competitor_id, scraped_at DESC);
CREATE INDEX idx_pcm_competitor_product ON public.product_competitor_matches (competitor_product_id, competitor_product_scraped_at);
CREATE INDEX idx_pcm_size               ON public.product_competitor_matches (our_size_id);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
