import time
import pytest
import yaml
from pathlib import Path

from backend.services.scraping.utils.config_loader import ConfigLoader
from backend.services.scraping.core.exceptions import ConfigurationError
from backend.services.scraping.core.exceptions import ValidationError as ScrapingValidationError
from backend.services.scraping.core.models import ScraperConfig


# ============================================
# HELPERS
# ============================================

_VALID_CONFIG = {
    "scraper_id": "test_001",
    "business_name": "Test Business",
    "business_type": "competitor",
    "scraper_type": "restaurant",
    "base_url": "https://example.com",
    "selectors": {
        "product_name": ".name",
        "product_price": ".price",
    },
    "navigation": {},
    "browser": {},
    "rate_limiting": {},
    "required_fields": ["product_name", "product_price"],
    "enabled": True,
}


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f)


# ============================================
# FIXTURES
# ============================================

@pytest.fixture
def config_dir(tmp_path):
    """Minimal valid config directory with one competitor config."""
    (tmp_path / "competitors").mkdir()
    (tmp_path / "suppliers").mkdir()
    _write_yaml(tmp_path / "competitors" / "test_001.yaml", _VALID_CONFIG)
    return tmp_path


@pytest.fixture
def loader(config_dir):
    return ConfigLoader(str(config_dir))


# ============================================
# Constructor / directory validation
# ============================================

class TestConstructor:
    def test_nonexistent_dir_raises(self, tmp_path):
        with pytest.raises(ConfigurationError) as exc_info:
            ConfigLoader(str(tmp_path / "no_such_dir"))
        assert exc_info.value.code == "CONFIG_DIR_NOT_FOUND"

    def test_valid_dir_creates_loader(self, config_dir):
        loader = ConfigLoader(str(config_dir))
        assert loader.config_dir == config_dir

    def test_cache_disabled_by_default_can_be_overridden(self, config_dir):
        loader = ConfigLoader(str(config_dir), enable_cache=False)
        assert loader.enable_cache is False


# ============================================
# load_config
# ============================================

class TestLoadConfig:
    def test_success_returns_scraper_config(self, loader):
        cfg = loader.load_config("test_001")
        assert isinstance(cfg, ScraperConfig)
        assert cfg.scraper_id == "test_001"
        assert cfg.business_name == "Test Business"
        assert cfg.base_url == "https://example.com"

    def test_base_url_trailing_slash_stripped(self, config_dir):
        data = {**_VALID_CONFIG, "base_url": "https://example.com/"}
        _write_yaml(config_dir / "competitors" / "slash_url.yaml", {**data, "scraper_id": "slash_url"})
        cfg = ConfigLoader(str(config_dir)).load_config("slash_url")
        assert not cfg.base_url.endswith("/")

    def test_not_found_raises_configuration_error(self, loader):
        with pytest.raises(ConfigurationError) as exc_info:
            loader.load_config("nonexistent")
        assert exc_info.value.code == "CONFIG_FILE_NOT_FOUND"

    def test_not_found_error_details_contain_scraper_id(self, loader):
        with pytest.raises(ConfigurationError) as exc_info:
            loader.load_config("nonexistent")
        assert "nonexistent" in str(exc_info.value)

    def test_loads_from_suppliers_subdir(self, config_dir):
        data = {**_VALID_CONFIG, "scraper_id": "supplier_001", "business_type": "supplier"}
        _write_yaml(config_dir / "suppliers" / "supplier_001.yaml", data)
        cfg = ConfigLoader(str(config_dir)).load_config("supplier_001")
        assert cfg.scraper_id == "supplier_001"
        assert cfg.business_type == "supplier"

    def test_validate_false_skips_pydantic_schema(self, config_dir):
        # required_fields referencing a missing selector fails Pydantic's
        # _check_required_selectors but ScraperConfig.__post_init__ doesn't check it.
        bad = {**_VALID_CONFIG, "scraper_id": "no_validate", "required_fields": ["missing_selector"]}
        _write_yaml(config_dir / "competitors" / "no_validate.yaml", bad)
        cfg = ConfigLoader(str(config_dir)).load_config("no_validate", validate=False)
        assert cfg.scraper_id == "no_validate"
        assert "missing_selector" in cfg.required_fields


# ============================================
# Pydantic validation
# ============================================

class TestValidation:
    def test_invalid_business_type_raises_validation_error(self, config_dir):
        bad = {**_VALID_CONFIG, "scraper_id": "bad_type", "business_type": "unknown"}
        _write_yaml(config_dir / "competitors" / "bad_type.yaml", bad)
        with pytest.raises(ScrapingValidationError) as exc_info:
            ConfigLoader(str(config_dir)).load_config("bad_type")
        assert exc_info.value.code == "CONFIG_VALIDATION_FAILED"

    def test_invalid_scraper_type_raises_validation_error(self, config_dir):
        bad = {**_VALID_CONFIG, "scraper_id": "bad_scraper", "scraper_type": "unknown"}
        _write_yaml(config_dir / "competitors" / "bad_scraper.yaml", bad)
        with pytest.raises(ScrapingValidationError):
            ConfigLoader(str(config_dir)).load_config("bad_scraper")

    def test_required_field_without_selector_raises(self, config_dir):
        bad = {**_VALID_CONFIG, "scraper_id": "bad_req", "required_fields": ["nonexistent_selector"]}
        _write_yaml(config_dir / "competitors" / "bad_req.yaml", bad)
        with pytest.raises(ScrapingValidationError):
            ConfigLoader(str(config_dir)).load_config("bad_req")

    def test_empty_base_url_raises(self, config_dir):
        bad = {**_VALID_CONFIG, "scraper_id": "bad_url", "base_url": ""}
        _write_yaml(config_dir / "competitors" / "bad_url.yaml", bad)
        with pytest.raises(ScrapingValidationError):
            ConfigLoader(str(config_dir)).load_config("bad_url")

    def test_validate_config_returns_scraper_config(self, loader):
        cfg = loader.validate_config(dict(_VALID_CONFIG))
        assert isinstance(cfg, ScraperConfig)
        assert cfg.scraper_id == "test_001"

    def test_validate_config_invalid_raises(self, loader):
        bad = {**_VALID_CONFIG, "business_type": "bad"}
        with pytest.raises(ScrapingValidationError):
            loader.validate_config(bad)


# ============================================
# YAML errors
# ============================================

class TestYamlErrors:
    def test_invalid_yaml_syntax_raises_configuration_error(self, config_dir):
        path = config_dir / "competitors" / "broken.yaml"
        path.write_text("key: [unclosed bracket\n")
        with pytest.raises(ConfigurationError) as exc_info:
            ConfigLoader(str(config_dir)).load_config("broken")
        assert exc_info.value.code == "CONFIG_YAML_SYNTAX_ERROR"

    def test_non_mapping_yaml_raises_configuration_error(self, config_dir):
        path = config_dir / "competitors" / "list_root.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(ConfigurationError) as exc_info:
            ConfigLoader(str(config_dir)).load_config("list_root")
        assert exc_info.value.code == "CONFIG_INVALID_STRUCTURE"


# ============================================
# Cache
# ============================================

class TestCache:
    def test_cache_returns_same_instance(self, loader):
        cfg1 = loader.load_config("test_001")
        cfg2 = loader.load_config("test_001")
        assert cfg1 is cfg2

    def test_cache_disabled_returns_new_instances(self, config_dir):
        loader = ConfigLoader(str(config_dir), enable_cache=False)
        cfg1 = loader.load_config("test_001")
        cfg2 = loader.load_config("test_001")
        assert cfg1 is not cfg2

    def test_reload_config_bypasses_cache(self, loader):
        cfg1 = loader.load_config("test_001")
        cfg2 = loader.reload_config("test_001")
        assert cfg1 is not cfg2

    def test_reload_config_returns_fresh_data(self, config_dir):
        loader = ConfigLoader(str(config_dir))
        loader.load_config("test_001")
        # Modify the file on disk
        updated = {**_VALID_CONFIG, "business_name": "Updated Name"}
        _write_yaml(config_dir / "competitors" / "test_001.yaml", updated)
        cfg = loader.reload_config("test_001")
        assert cfg.business_name == "Updated Name"

    def test_cache_ttl_expiry(self, config_dir):
        loader = ConfigLoader(str(config_dir), enable_cache=True, cache_ttl_seconds=0)
        cfg1 = loader.load_config("test_001")
        # TTL=0 means immediately stale
        cfg2 = loader.load_config("test_001")
        assert cfg1 is not cfg2

    def test_is_cache_valid_false_when_empty(self, loader):
        assert loader._is_cache_valid("unknown_id") is False


# ============================================
# load_registry
# ============================================

class TestLoadRegistry:
    def test_returns_empty_dict_when_no_registry(self, loader):
        registry = loader.load_registry()
        assert registry == {}

    def test_loads_registry_yaml(self, config_dir):
        registry_data = {"version": "1.0", "scrapers": {"competitors": [{"id": "test_001"}]}}
        _write_yaml(config_dir / "registry.yaml", registry_data)
        loader = ConfigLoader(str(config_dir))
        registry = loader.load_registry()
        assert registry["version"] == "1.0"
        assert len(registry["scrapers"]["competitors"]) == 1


# ============================================
# list_scraper_ids
# ============================================

class TestListScraperIds:
    def test_returns_enabled_scraper(self, loader):
        ids = loader.list_scraper_ids(enabled_only=True)
        assert "test_001" in ids

    def test_excludes_disabled_scraper(self, config_dir):
        disabled = {**_VALID_CONFIG, "scraper_id": "disabled_001", "enabled": False}
        _write_yaml(config_dir / "competitors" / "disabled_001.yaml", disabled)
        loader = ConfigLoader(str(config_dir))
        ids = loader.list_scraper_ids(enabled_only=True)
        assert "disabled_001" not in ids

    def test_includes_disabled_when_flag_false(self, config_dir):
        disabled = {**_VALID_CONFIG, "scraper_id": "disabled_001", "enabled": False}
        _write_yaml(config_dir / "competitors" / "disabled_001.yaml", disabled)
        loader = ConfigLoader(str(config_dir))
        ids = loader.list_scraper_ids(enabled_only=False)
        assert "disabled_001" in ids

    def test_excludes_template_files(self, config_dir):
        template = {**_VALID_CONFIG, "scraper_id": "_base_template"}
        _write_yaml(config_dir / "competitors" / "_base_template.yaml", template)
        ids = ConfigLoader(str(config_dir)).list_scraper_ids(enabled_only=False)
        assert "_base_template" not in ids

    def test_includes_both_competitors_and_suppliers(self, config_dir):
        supplier = {**_VALID_CONFIG, "scraper_id": "supp_001", "business_type": "supplier"}
        _write_yaml(config_dir / "suppliers" / "supp_001.yaml", supplier)
        ids = ConfigLoader(str(config_dir)).list_scraper_ids(enabled_only=False)
        assert "test_001" in ids
        assert "supp_001" in ids

    def test_skips_invalid_configs_with_warning(self, config_dir):
        bad = {**_VALID_CONFIG, "scraper_id": "bad_config", "business_type": "invalid"}
        _write_yaml(config_dir / "competitors" / "bad_config.yaml", bad)
        # Should not raise; just skips bad_config
        ids = ConfigLoader(str(config_dir)).list_scraper_ids(enabled_only=True)
        assert "bad_config" not in ids


# ============================================
# load_all_configs
# ============================================

class TestLoadAllConfigs:
    def test_returns_mapping_of_valid_configs(self, loader):
        all_cfgs = loader.load_all_configs()
        assert "test_001" in all_cfgs
        assert isinstance(all_cfgs["test_001"], ScraperConfig)

    def test_skips_invalid_config(self, config_dir):
        bad = {**_VALID_CONFIG, "scraper_id": "bad_one", "business_type": "invalid"}
        _write_yaml(config_dir / "competitors" / "bad_one.yaml", bad)
        all_cfgs = ConfigLoader(str(config_dir)).load_all_configs()
        assert "bad_one" not in all_cfgs
        assert "test_001" in all_cfgs


# ============================================
# Template inheritance
# ============================================

class TestTemplateInheritance:
    def _make_template(self, config_dir: Path) -> None:
        template = {
            "scraper_id": "base_restaurant",
            "business_name": "Base",
            "business_type": "competitor",
            "scraper_type": "restaurant",
            "base_url": "https://base.example.com",
            "selectors": {"product_name": ".name", "product_price": ".price"},
            "navigation": {},
            "browser": {},
            "rate_limiting": {},
        }
        (config_dir / "_templates").mkdir(exist_ok=True)
        _write_yaml(config_dir / "_templates" / "base_restaurant.yaml", template)

    def test_child_inherits_base_values(self, config_dir):
        self._make_template(config_dir)
        child = {
            "extends": "base_restaurant",
            "scraper_id": "child_001",
            "business_name": "Child Business",
            "base_url": "https://child.example.com",
        }
        _write_yaml(config_dir / "competitors" / "child_001.yaml", child)
        cfg = ConfigLoader(str(config_dir)).load_config("child_001")
        # Overridden values
        assert cfg.scraper_id == "child_001"
        assert cfg.business_name == "Child Business"
        assert cfg.base_url == "https://child.example.com"
        # Inherited value
        assert cfg.scraper_type == "restaurant"

    def test_child_overrides_selectors(self, config_dir):
        self._make_template(config_dir)
        child = {
            "extends": "base_restaurant",
            "scraper_id": "override_sel",
            "business_name": "Override",
            "base_url": "https://override.example.com",
            "selectors": {"product_name": ".custom-name", "product_price": ".custom-price"},
        }
        _write_yaml(config_dir / "competitors" / "override_sel.yaml", child)
        cfg = ConfigLoader(str(config_dir)).load_config("override_sel")
        assert cfg.selectors["product_name"] == ".custom-name"

    def test_missing_template_raises_configuration_error(self, config_dir):
        child = {**_VALID_CONFIG, "scraper_id": "orphan", "extends": "nonexistent_template"}
        _write_yaml(config_dir / "competitors" / "orphan.yaml", child)
        with pytest.raises(ConfigurationError) as exc_info:
            ConfigLoader(str(config_dir)).load_config("orphan")
        assert exc_info.value.code == "CONFIG_TEMPLATE_NOT_FOUND"

    def test_circular_inheritance_raises_configuration_error(self, config_dir):
        (config_dir / "_templates").mkdir(exist_ok=True)
        # a extends b, b extends a
        tpl_a = {
            "scraper_id": "tpl_a", "extends": "tpl_b",
            "business_name": "A", "business_type": "competitor",
            "scraper_type": "restaurant", "base_url": "https://a.example.com",
        }
        tpl_b = {
            "scraper_id": "tpl_b", "extends": "tpl_a",
            "business_name": "B", "business_type": "competitor",
            "scraper_type": "restaurant", "base_url": "https://b.example.com",
        }
        _write_yaml(config_dir / "_templates" / "tpl_a.yaml", tpl_a)
        _write_yaml(config_dir / "_templates" / "tpl_b.yaml", tpl_b)
        child = {**_VALID_CONFIG, "scraper_id": "circular_child", "extends": "tpl_a"}
        _write_yaml(config_dir / "competitors" / "circular_child.yaml", child)
        with pytest.raises(ConfigurationError) as exc_info:
            ConfigLoader(str(config_dir)).load_config("circular_child")
        assert exc_info.value.code == "CONFIG_CIRCULAR_INHERITANCE"


# ============================================
# _merge_configs (unit)
# ============================================

class TestMergeConfigs:
    @pytest.fixture
    def l(self, loader):
        return loader

    def test_override_wins_on_scalar(self, l):
        result = l._merge_configs({"key": "base"}, {"key": "override"})
        assert result["key"] == "override"

    def test_base_key_kept_when_not_in_override(self, l):
        result = l._merge_configs({"a": 1, "b": 2}, {"a": 99})
        assert result["b"] == 2

    def test_nested_dicts_deep_merged(self, l):
        base = {"nav": {"search": {"path": "/old"}, "category": {"path": "/cat"}}}
        override = {"nav": {"search": {"path": "/new"}}}
        result = l._merge_configs(base, override)
        assert result["nav"]["search"]["path"] == "/new"
        assert result["nav"]["category"]["path"] == "/cat"

    def test_lists_replaced_not_merged(self, l):
        base = {"fields": ["a", "b"]}
        override = {"fields": ["c"]}
        result = l._merge_configs(base, override)
        assert result["fields"] == ["c"]

    def test_inputs_not_mutated(self, l):
        base = {"key": "base_val"}
        override = {"key": "override_val"}
        l._merge_configs(base, override)
        assert base["key"] == "base_val"
        assert override["key"] == "override_val"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
