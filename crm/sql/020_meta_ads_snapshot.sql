-- Lecturas del MCP de Meta Ads. Aplicar antes de publicar el panel nuevo.
CREATE TABLE IF NOT EXISTS crm_meta_ads_daily (
  id_tenant BIGINT NOT NULL,
  account_id VARCHAR(32) NOT NULL,
  ad_id VARCHAR(32) NOT NULL,
  report_date DATE NOT NULL,
  ad_name VARCHAR(255) NOT NULL,
  adset_id VARCHAR(32) NOT NULL DEFAULT '',
  adset_name VARCHAR(255) NOT NULL DEFAULT '',
  campaign_id VARCHAR(32) NOT NULL DEFAULT '',
  campaign_name VARCHAR(255) NOT NULL DEFAULT '',
  spend DECIMAL(14, 2) NOT NULL DEFAULT 0,
  impressions BIGINT NOT NULL DEFAULT 0,
  clicks BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id_tenant, account_id, ad_id, report_date),
  KEY idx_meta_campaign_date (id_tenant, campaign_id, report_date),
  KEY idx_meta_date (id_tenant, report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS crm_meta_ads_sync (
  id_tenant BIGINT NOT NULL,
  account_id VARCHAR(32) NOT NULL,
  date_from DATE NOT NULL,
  date_to DATE NOT NULL,
  synced_at DATETIME NOT NULL,
  row_count INT NOT NULL,
  PRIMARY KEY (id_tenant, account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
