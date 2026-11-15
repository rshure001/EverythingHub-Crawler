CREATE TABLE IF NOT EXISTS collections (name TEXT PRIMARY KEY, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, collection_name TEXT NOT NULL REFERENCES collections(name) ON DELETE CASCADE, body TEXT NOT NULL CHECK(json_valid(body)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_documents_collection_created ON documents(collection_name, created_at DESC);
