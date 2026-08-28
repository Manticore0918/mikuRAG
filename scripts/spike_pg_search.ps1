[CmdletBinding()]
param(
    [string]$ImageTag = "paradedb/paradedb:0.25.5-pg16"
)

$ErrorActionPreference = "Stop"
$containerName = "mikurag-pg-search-spike-$PID"
$postgresUser = "mikurag"
$postgresPassword = "spike"
$postgresDatabase = "mikurag"
$containerCreated = $false

function Invoke-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($args -join ' ') failed with exit code $LASTEXITCODE"
    }
}

$dialectSql = @'
SELECT current_setting('server_version_num') AS pg_version,
       EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_search') AS pg_search_available,
       EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') AS vector_available;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;
CREATE TABLE spike_docs (id bigserial PRIMARY KEY, text text NOT NULL);
CREATE INDEX spike_docs_bm25 ON spike_docs USING bm25 (id, text) WITH (key_field = 'id');
INSERT INTO spike_docs (text) VALUES
  ('The quick brown fox jumps over the lazy dog.'),
  ('PostgreSQL full text search ranks documents by ts_rank.'),
  ('ParadeDB provides BM25 ranking over PostgreSQL tables.');
SELECT id,
       pdb.score(id) AS bm25_score,
       left(text, 40) AS snippet
FROM spike_docs
WHERE text @@@ 'bm25 search ranking'
ORDER BY pdb.score(id) DESC
LIMIT 3;
SELECT id,
       pdb.score(id) AS bm25_score
FROM spike_docs
WHERE text @@@ pdb.match('What does PostgreSQL''s BM25 ranking provide?')
ORDER BY pdb.score(id) DESC
LIMIT 3;
'@

try {
    Write-Host "==> Starting $ImageTag"
    Invoke-Docker run -d --name $containerName `
        -e "POSTGRES_USER=$postgresUser" `
        -e "POSTGRES_PASSWORD=$postgresPassword" `
        -e "POSTGRES_DB=$postgresDatabase" `
        $ImageTag | Out-Null
    $containerCreated = $true

    Write-Host "==> Waiting for PostgreSQL initialization"
    $ready = $false
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        $databaseReady = $null
        try {
            $databaseReady = & docker exec $containerName psql `
                -U $postgresUser -d postgres -Atqc `
                "SELECT 1 FROM pg_database WHERE datname = '$postgresDatabase'" 2>$null
        } catch {
            # The image briefly starts and stops an initialization server.
            $databaseReady = $null
        }
        if ($LASTEXITCODE -eq 0 -and $databaseReady -eq "1") {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        throw "PostgreSQL did not finish initializing inside $containerName"
    }

    Write-Host "==> Confirming extensions, index syntax, and BM25 query dialect"
    $dialectSql | & docker exec -i $containerName `
        psql -U $postgresUser -d $postgresDatabase -v ON_ERROR_STOP=1 -f -
    if ($LASTEXITCODE -ne 0) {
        throw "The BM25 dialect check failed against $ImageTag"
    }

    Write-Host "==> SPIKE PASSED on $ImageTag"
} finally {
    if ($containerCreated) {
        & docker rm -f $containerName *> $null
    }
}
