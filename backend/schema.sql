-- mir-176-h5 PostgreSQL schema (design doc MVP-DESIGN.md section 8.2).
-- Idempotent: every CREATE TABLE / CREATE INDEX uses IF NOT EXISTS,
-- so this file can be applied repeatedly.

CREATE TABLE IF NOT EXISTS account (
  id            BIGSERIAL PRIMARY KEY,
  username      VARCHAR(32) UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,                -- bcrypt
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS character (
  id          BIGSERIAL PRIMARY KEY,
  account_id  BIGINT NOT NULL REFERENCES account(id),
  name        VARCHAR(16) UNIQUE NOT NULL,
  class       VARCHAR(8) NOT NULL CHECK (class IN ('warrior','mage','taoist')),
  level       SMALLINT NOT NULL DEFAULT 1,
  exp         BIGINT NOT NULL DEFAULT 0,
  hp          INT NOT NULL,  mp INT NOT NULL,
  gold        BIGINT NOT NULL DEFAULT 0,
  bound_city  VARCHAR(8) NOT NULL DEFAULT 'bichon',      -- bound city
  map_id      VARCHAR(16) NOT NULL DEFAULT 'm1',         -- offline (last saved) position
  x           SMALLINT NOT NULL, y SMALLINT NOT NULL,
  quest_flags JSONB NOT NULL DEFAULT '{}',               -- {qid: {status, progress}}
  skill_ranks JSONB NOT NULL DEFAULT '{}',               -- {skill_code: {rank, exp}}
  autobattle_cfg JSONB NOT NULL DEFAULT '{}',            -- auto-battle config (section 6.7)
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_save   TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at  TIMESTAMPTZ                                -- soft delete
);
CREATE INDEX IF NOT EXISTS idx_character_account ON character(account_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS item_instance (
  id          BIGSERIAL PRIMARY KEY,
  character_id BIGINT NOT NULL REFERENCES character(id),
  def_id      VARCHAR(32) NOT NULL,            -- maps to item_def.code
  slot        SMALLINT NOT NULL,               -- 0..39 inventory; 100=weapon 101=armor 102=helmet 103=necklace 104/105=bangle 106/107=ring
  quantity    INT NOT NULL DEFAULT 1,          -- potions/scrolls stack; equipment always 1
  durability  SMALLINT,                        -- weapon/armor only
  bonus       JSONB,                           -- premium bonus attrs [{attr:'atk_max', value:2}, ...] or NULL
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_item_char ON item_instance(character_id, slot);

CREATE TABLE IF NOT EXISTS quest_progress (  -- optional: MVP uses character.quest_flags, kept for extension
  character_id BIGINT NOT NULL REFERENCES character(id),
  quest_id     VARCHAR(16) NOT NULL,
  status       VARCHAR(8) NOT NULL,            -- done / none
  progress     INT NOT NULL DEFAULT 0,
  PRIMARY KEY (character_id, quest_id)
);

-- ===== static content def tables (written by the seed script, read by the server at startup) =====
CREATE TABLE IF NOT EXISTS item_def (
  code VARCHAR(32) PRIMARY KEY, name VARCHAR(32) NOT NULL, kind VARCHAR(16) NOT NULL, -- weapon/armor/helmet/necklace/bangle/ring/potion/scroll
  class_req VARCHAR(8), level_req SMALLINT,
  atk_min INT DEFAULT 0, atk_max INT DEFAULT 0, mag_min INT DEFAULT 0, mag_max INT DEFAULT 0,
  dao_min INT DEFAULT 0, dao_max INT DEFAULT 0, def INT DEFAULT 0, mdef INT DEFAULT 0,
  hp_bonus INT DEFAULT 0, mp_bonus INT DEFAULT 0,
  buy_price INT DEFAULT 0, sell_price INT DEFAULT 0,
  max_durability SMALLINT, stackable BOOLEAN DEFAULT false
);
CREATE TABLE IF NOT EXISTS map_def ( code VARCHAR(16) PRIMARY KEY, name VARCHAR(32), width SMALLINT, height SMALLINT, tiles BYTEA );
CREATE TABLE IF NOT EXISTS monster_def ( code VARCHAR(32) PRIMARY KEY, name VARCHAR(32), level SMALLINT, hp INT,
  atk_min INT, atk_max INT, def INT, mdef INT, exp INT, gold_min INT, gold_max INT,
  aggro BOOLEAN, move_ms SMALLINT, is_boss BOOLEAN DEFAULT false, respawn_s INT DEFAULT 60 );
CREATE TABLE IF NOT EXISTS spawn_point ( id SERIAL PRIMARY KEY, map_code VARCHAR(16) REFERENCES map_def(code),
  monster_code VARCHAR(32) REFERENCES monster_def(code), max_count SMALLINT, respawn_s INT );
CREATE TABLE IF NOT EXISTS drop_def ( id SERIAL PRIMARY KEY, monster_code VARCHAR(32) REFERENCES monster_def(code),
  item_code VARCHAR(32) REFERENCES item_def(code), chance NUMERIC(6,4), min_qty INT DEFAULT 1, max_qty INT DEFAULT 1, boss_table BOOLEAN DEFAULT false );
CREATE TABLE IF NOT EXISTS npc_def ( id SERIAL PRIMARY KEY, code VARCHAR(32), name VARCHAR(32), map_code VARCHAR(16),
  x SMALLINT, y SMALLINT, kind VARCHAR(16) NOT NULL,      -- merchant/smith/teleporter/guide
  shop_items JSONB, teleport_routes JSONB );              -- shop/teleport tables duplicated in DB for ops tuning
CREATE TABLE IF NOT EXISTS quest_def ( id VARCHAR(16) PRIMARY KEY, name VARCHAR(64), level_req SMALLINT,
  kind VARCHAR(16) NOT NULL,                              -- dialog/kill/reach
  target_monster VARCHAR(32), target_map VARCHAR(16), target_count INT DEFAULT 1,
  reward_gold INT DEFAULT 0, reward_items JSONB, unlock_teleport JSONB );
CREATE TABLE IF NOT EXISTS account_session ( ws_token_hash TEXT PRIMARY KEY, account_id BIGINT, expires_at TIMESTAMPTZ );
