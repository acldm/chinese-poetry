/* =========================================================
 * 0) 基础类型 + 通用 updated_at 触发器（可选但强烈推荐）
 * ========================================================= */

-- 季节是稳定集合，用 ENUM 没问题
DO $$ BEGIN
  CREATE TYPE season_type AS ENUM ('spring','summer','autumn','winter','unknown');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- 自动维护 updated_at
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;


/* =========================================================
 * 1) 作者
 * ========================================================= */

CREATE TABLE IF NOT EXISTS authors (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT NOT NULL,          -- 作者名（建议存你展示用的主写法）
  name_simplified TEXT,
  name_traditional TEXT,
  dynasty         TEXT,                   -- 可选：唐/宋/元...
  bio             TEXT,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- 同名作者跨朝代可能存在；这里用 (name, dynasty) 约束更安全
  UNIQUE (name, dynasty)
);

DROP TRIGGER IF EXISTS trg_authors_updated_at ON authors;
CREATE TRIGGER trg_authors_updated_at
BEFORE UPDATE ON authors
FOR EACH ROW EXECUTE FUNCTION set_updated_at();


/* =========================================================
 * 2) 站点导航分类树（可扩充：唐诗/宋诗/宋词/元曲/明诗...）
 * ========================================================= */

CREATE TABLE IF NOT EXISTS collections (
  id            BIGSERIAL PRIMARY KEY,
  code          TEXT NOT NULL UNIQUE,      -- 稳定标识：tang_poem/song_ci/yuan_qu...
  name          TEXT NOT NULL,             -- 显示名：唐诗/宋词/元曲...
  description   TEXT,

  parent_id     BIGINT REFERENCES collections(id) ON DELETE SET NULL,
  sort_order    INT NOT NULL DEFAULT 0,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,

  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_collections_parent ON collections(parent_id);
CREATE INDEX IF NOT EXISTS idx_collections_sort ON collections(sort_order);

DROP TRIGGER IF EXISTS trg_collections_updated_at ON collections;
CREATE TRIGGER trg_collections_updated_at
BEFORE UPDATE ON collections
FOR EACH ROW EXECUTE FUNCTION set_updated_at();


/* =========================================================
 * 3) 作品主表
 * ========================================================= */

CREATE TABLE IF NOT EXISTS works (
  id              BIGSERIAL PRIMARY KEY,

  title           TEXT NOT NULL,
  author_id       BIGINT NOT NULL REFERENCES authors(id) ON DELETE RESTRICT,

  -- ✅ 站点主大类（唐诗/宋诗/宋词/元曲...）来自 collections，可无限扩充
  primary_collection_id BIGINT NOT NULL REFERENCES collections(id) ON DELETE RESTRICT,

  dynasty         TEXT,                   -- 可选：作品朝代（可与作者朝代不同或更精确）
  score           NUMERIC(3,1) CHECK (score IS NULL OR (score >= 0 AND score <= 5)),

  translation     TEXT,                   -- 译文
  analysis        TEXT,                   -- 总赏析
  structural_logic TEXT,                  -- technique_analysis.structural_logic

  -- 可选：保留原始 JSON（方便回溯/重算/数据迁移）
  source_json     JSONB,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_works_author ON works(author_id);
CREATE INDEX IF NOT EXISTS idx_works_primary_collection ON works(primary_collection_id);
CREATE INDEX IF NOT EXISTS idx_works_dynasty ON works(dynasty);

DROP TRIGGER IF EXISTS trg_works_updated_at ON works;
CREATE TRIGGER trg_works_updated_at
BEFORE UPDATE ON works
FOR EACH ROW EXECUTE FUNCTION set_updated_at();


/* =========================================================
 * 4) 作品可挂多个分类（专题/子栏目/合集），可选但推荐
 * ========================================================= */

CREATE TABLE IF NOT EXISTS work_collections (
  work_id        BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  collection_id  BIGINT NOT NULL REFERENCES collections(id) ON DELETE RESTRICT,
  is_primary     BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (work_id, collection_id)
);

CREATE INDEX IF NOT EXISTS idx_work_collections_collection ON work_collections(collection_id);


/* =========================================================
 * 5) 正文逐句（支持繁/简；并存 sentence_type）
 * ========================================================= */

CREATE TABLE IF NOT EXISTS work_lines (
  id               BIGSERIAL PRIMARY KEY,
  work_id          BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  line_no          INT NOT NULL,                 -- 从 1 开始
  text_traditional TEXT NOT NULL,
  text_simplified  TEXT,
  sentence_type    SMALLINT,                     -- 对应你的 sentence_types

  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (work_id, line_no)
);

CREATE INDEX IF NOT EXISTS idx_work_lines_work ON work_lines(work_id);


/* =========================================================
 * 6) 最佳名句（指向某一行）
 * ========================================================= */

CREATE TABLE IF NOT EXISTS best_quotes (
  work_id     BIGINT PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
  line_no     INT NOT NULL,
  reason      TEXT,

  FOREIGN KEY (work_id, line_no)
    REFERENCES work_lines(work_id, line_no)
    ON DELETE RESTRICT
);


/* =========================================================
 * 7) 时间季节（time_season）
 * ========================================================= */

CREATE TABLE IF NOT EXISTS work_time_season (
  work_id        BIGINT PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
  season         season_type NOT NULL DEFAULT 'unknown',
  specific_time  TEXT
);


/* =========================================================
 * 8) 用典（allusions）
 * ========================================================= */

CREATE TABLE IF NOT EXISTS allusions (
  id           BIGSERIAL PRIMARY KEY,
  work_id      BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,

  phrase       TEXT NOT NULL,
  explanation  TEXT NOT NULL,

  line_no      INT,  -- 可选：对应到某一句
  FOREIGN KEY (work_id, line_no)
    REFERENCES work_lines(work_id, line_no)
    ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_allusions_work ON allusions(work_id);


/* =========================================================
 * 9) 诗歌风格（poetry_styles）
 *    你要求 tag 独立表：style 也拆成 style_tags
 * ========================================================= */

CREATE TABLE IF NOT EXISTS style_tags (
  id    BIGSERIAL PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS poetry_styles (
  id              BIGSERIAL PRIMARY KEY,
  work_id          BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,

  style_tag_id     BIGINT NOT NULL REFERENCES style_tags(id) ON DELETE RESTRICT, -- 典雅等
  imagery_analysis TEXT,
  realm            TEXT,
  reason           TEXT
);

CREATE INDEX IF NOT EXISTS idx_poetry_styles_work ON poetry_styles(work_id);
CREATE INDEX IF NOT EXISTS idx_poetry_styles_style ON poetry_styles(style_tag_id);


/* =========================================================
 * 10) 技法证据（technique_analysis.evidence）
 *     你要求 tag 独立表：evidence 的 tag 也独立成 evidence_tags
 * ========================================================= */

CREATE TABLE IF NOT EXISTS evidence_tags (
  id    BIGSERIAL PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE     -- 如：用典/比喻/对比...
);

CREATE TABLE IF NOT EXISTS technique_evidence (
  id             BIGSERIAL PRIMARY KEY,
  work_id         BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  evidence_tag_id BIGINT NOT NULL REFERENCES evidence_tags(id) ON DELETE RESTRICT,
  explanation     TEXT NOT NULL,

  -- 可选：落到具体句子
  line_no         INT,
  FOREIGN KEY (work_id, line_no)
    REFERENCES work_lines(work_id, line_no)
    ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_tech_ev_work ON technique_evidence(work_id);
CREATE INDEX IF NOT EXISTS idx_tech_ev_tag ON technique_evidence(evidence_tag_id);


/* =========================================================
 * 11) 你 JSON 里的各类 tags —— 全部独立表 + 各自关联表
 *     subject / emotion / imagery / expression / rhetoric / presentation / structure
 * ========================================================= */

-- 11.1 题材 subject
CREATE TABLE IF NOT EXISTS subject_tags (
  id    BIGSERIAL PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS work_subject_tags (
  work_id        BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  subject_tag_id BIGINT NOT NULL REFERENCES subject_tags(id) ON DELETE RESTRICT,
  PRIMARY KEY (work_id, subject_tag_id)
);

CREATE INDEX IF NOT EXISTS idx_work_subject_tags_tag ON work_subject_tags(subject_tag_id);


-- 11.2 情绪 emotion
CREATE TABLE IF NOT EXISTS emotion_tags (
  id    BIGSERIAL PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS work_emotion_tags (
  work_id        BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  emotion_tag_id BIGINT NOT NULL REFERENCES emotion_tags(id) ON DELETE RESTRICT,
  PRIMARY KEY (work_id, emotion_tag_id)
);

CREATE INDEX IF NOT EXISTS idx_work_emotion_tags_tag ON work_emotion_tags(emotion_tag_id);


-- 11.3 意象 imagery
CREATE TABLE IF NOT EXISTS imagery_tags (
  id    BIGSERIAL PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS work_imagery_tags (
  work_id        BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  imagery_tag_id BIGINT NOT NULL REFERENCES imagery_tags(id) ON DELETE RESTRICT,
  PRIMARY KEY (work_id, imagery_tag_id)
);

CREATE INDEX IF NOT EXISTS idx_work_imagery_tags_tag ON work_imagery_tags(imagery_tag_id);


-- 11.4 表达 expression（借景抒情/议论...）
CREATE TABLE IF NOT EXISTS expression_tags (
  id    BIGSERIAL PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS work_expression_tags (
  work_id           BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  expression_tag_id BIGINT NOT NULL REFERENCES expression_tags(id) ON DELETE RESTRICT,
  PRIMARY KEY (work_id, expression_tag_id)
);

CREATE INDEX IF NOT EXISTS idx_work_expression_tags_tag ON work_expression_tags(expression_tag_id);


-- 11.5 修辞 rhetoric（比喻/用典...）
CREATE TABLE IF NOT EXISTS rhetoric_tags (
  id    BIGSERIAL PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS work_rhetoric_tags (
  work_id         BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  rhetoric_tag_id BIGINT NOT NULL REFERENCES rhetoric_tags(id) ON DELETE RESTRICT,
  PRIMARY KEY (work_id, rhetoric_tag_id)
);

CREATE INDEX IF NOT EXISTS idx_work_rhetoric_tags_tag ON work_rhetoric_tags(rhetoric_tag_id);


-- 11.6 表现 presentation（虚实结合/侧面烘托...）
CREATE TABLE IF NOT EXISTS presentation_tags (
  id    BIGSERIAL PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS work_presentation_tags (
  work_id             BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  presentation_tag_id BIGINT NOT NULL REFERENCES presentation_tags(id) ON DELETE RESTRICT,
  PRIMARY KEY (work_id, presentation_tag_id)
);

CREATE INDEX IF NOT EXISTS idx_work_presentation_tags_tag ON work_presentation_tags(presentation_tag_id);


-- 11.7 结构 structure（承上启下/卒章显志...）
CREATE TABLE IF NOT EXISTS structure_tags (
  id    BIGSERIAL PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS work_structure_tags (
  work_id          BIGINT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  structure_tag_id BIGINT NOT NULL REFERENCES structure_tags(id) ON DELETE RESTRICT,
  PRIMARY KEY (work_id, structure_tag_id)
);

CREATE INDEX IF NOT EXISTS idx_work_structure_tags_tag ON work_structure_tags(structure_tag_id);
