# mikuRAG

mikuRAG is a private, multi-user knowledge system operated within a self-hosted installation.

## Language

**User**:
An authenticated person who can access only the parts of the installation for which they have permission.
_Avoid_: End user, regular user

**Administrator**:
A User with authority over the entire installation, including its users, knowledge, and configuration.
_Avoid_: Admin user, superuser

**Installation**:
One independently operated instance of mikuRAG and the privacy boundary around its users and knowledge.
_Avoid_: Server, deployment, tenant

**Knowledge Base**:
A named collection of Documents that an Administrator curates and grants Users permission to query.
_Avoid_: Database, library, workspace

**Document**:
A source uploaded to a Knowledge Base and used as evidence for answers.
_Avoid_: File, resource

**Ingestion**:
The all-or-nothing process that turns an uploaded Document into searchable evidence. A Document is available for retrieval only after Ingestion succeeds.
_Avoid_: Indexing, processing, import

**Citation**:
A reference from an answer to the specific part of a Document that supports it, including a small retained evidence excerpt. Its excerpt remains part of Conversation history even if the original Document is later deleted.
_Avoid_: Source link, reference

**Conversation**:
A sequence of questions and answers scoped to exactly one Knowledge Base for its entire lifetime. It is visible to its User and to Administrators.
_Avoid_: Chat session, thread

**Grounded Answer**:
An answer whose factual claims are supported by retrieved Documents and accompanied by Citations. When the available evidence is missing or conflicting, it states that a reliable answer cannot be given and explains the limitation.
_Avoid_: AI answer, generated response
