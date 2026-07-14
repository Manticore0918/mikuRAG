# Store application data and embeddings in PostgreSQL

mikuRAG will use PostgreSQL with pgvector for accounts, permissions, Knowledge Bases, document metadata and extracted chunks, embeddings, Conversations, and Citations, while original uploaded Documents live in a persistent filesystem volume. A single transactional store is preferred over SQLite plus a separate vector database because the Installation is multi-user, retrieval must enforce Knowledge Base authorization, and the same deployment must scale from one machine to a separate server.
