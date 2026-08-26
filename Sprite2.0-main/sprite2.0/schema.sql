-- Schema per SpriteBot 2.0
-- Eseguire in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS collezione (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    username TEXT,
    spiritello TEXT NOT NULL,
    variante TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, spiritello, variante)
);

-- Indici per performance
CREATE INDEX IF NOT EXISTS idx_collezione_user ON collezione (user_id);
CREATE INDEX IF NOT EXISTS idx_collezione_username ON collezione (username);
CREATE INDEX IF NOT EXISTS idx_collezione_spiritello ON collezione (spiritello);
