from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from backend.database import Base


class Competitor(Base):
    """Cadena o negocio competidor monitoreado.

    Representa a un competidor cuyo menú y precios se rastrean periódicamente
    mediante scraping. is_active permite desactivar competidores sin borrar
    su historial de precios.

    Ejemplo:
        name="Juan Valdez", website_url="https://juanvaldezcafe.com"
        name="Starbucks",   website_url="https://starbucks.com.co"
    """

    __tablename__ = "competitors"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String(200), nullable=False)
    website_url: str | None = Column(Text)
    is_active: bool = Column(Boolean, default=True)


class CompetitorProduct(Base):
    """Producto scrapeado del menú de un competidor.

    Cada fila es un snapshot de un producto tal como aparece publicado en
    el sitio del competidor en el momento del scraping. No se normaliza ni
    interpreta: product_name y size_description se guardan exactamente como
    vienen de la fuente para preservar fidelidad al dato original.

    scraped_at permite construir series de tiempo de precios por competidor
    y detectar cambios de precio entre scrapes sucesivos.

    source_url apunta a la página o endpoint exacto donde se encontró el dato,
    facilitando la verificación manual y el debugging del scraper.

    Ejemplo:
        competitor_id=1 (Juan Valdez), product_name="Cappuccino",
        size_description="12oz", price=12900.00,
        source_url="https://juanvaldezcafe.com/menu/bebidas-calientes"
    """

    __tablename__ = "competitor_products"

    id: int = Column(Integer, primary_key=True, index=True)
    competitor_id: int = Column(
        Integer, ForeignKey("competitors.id"), nullable=False, index=True
    )
    product_name: str | None = Column(String(200))
    category: str | None = Column(String(100))
    size_description: str | None = Column(String(100))
    price: float | None = Column(Numeric(10, 2))
    scraped_at: object = Column(DateTime(timezone=True), server_default=func.now())
    source_url: str | None = Column(Text)


class ProductCompetitorMatch(Base):
    """Correspondencia manual entre un producto propio y uno de la competencia.

    Este match es SIEMPRE una decisión humana: ningún proceso automático
    inserta filas aquí. El usuario evalúa si dos productos son comparables
    (tamaño, preparación, mercado objetivo) y registra el match con su nombre
    y una justificación en notes.

    El motor de análisis competitivo usa esta tabla para calcular brechas de
    precio entre productos propios y sus equivalentes en la competencia.

    La constraint única en (our_product_id, our_size_id, competitor_product_id)
    impide duplicar el mismo par, pero un producto propio puede tener múltiples
    matches contra distintos competidores.

    Ejemplo:
        our_product_id=5 (Cappuccino mediano 12oz) ↔
        competitor_product_id=42 (Juan Valdez Cappuccino 12oz)
        matched_by="carlos", notes="Mismo tamaño y preparación estándar"
    """

    __tablename__ = "product_competitor_matches"

    __table_args__ = (
        UniqueConstraint(
            "our_product_id",
            "our_size_id",
            "competitor_product_id",
            name="uq_product_competitor_match",
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    our_product_id: int = Column(
        Integer, ForeignKey("products.id"), nullable=False
    )
    our_size_id: int = Column(
        Integer, ForeignKey("product_sizes.id"), nullable=False
    )
    competitor_product_id: int = Column(
        Integer, ForeignKey("competitor_products.id"), nullable=False
    )
    matched_by: str | None = Column(String(100))
    matched_at: object = Column(DateTime(timezone=True), server_default=func.now())
    notes: str | None = Column(Text)
