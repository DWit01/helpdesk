# Runs only on the explicitly enabled, trusted VM runner. This is a private demo,
# not a general-purpose deployment tool or an automatic migration mechanism.
$ErrorActionPreference = 'Stop'
$project = 'helpdeskazure84e9'
$requiredContainers = @("$project-keycloak-1", "$project-api-1", "$project-frontend-1")
$dataVolume = "${project}_helpdesk-data"
$authFile = Join-Path ([IO.Path]::GetTempPath()) ('helpdesk-registry-' + [guid]::NewGuid().ToString('N') + '.json')

function Assert-NativeSuccess([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

function Read-NormalizedText([string]$Path) {
    return [IO.File]::ReadAllText($Path).Replace("`r`n", "`n")
}

function Assert-CurrentMaster {
    $headers = @{ Authorization = "Bearer $env:REGISTRY_TOKEN"; 'X-GitHub-Api-Version' = '2022-11-28' }
    $ref = Invoke-RestMethod -Uri "$env:GITHUB_API_URL/repos/$env:GITHUB_REPOSITORY/git/ref/heads/master" -Headers $headers
    if ($ref.object.sha -ne $env:GITHUB_SHA) {
        throw 'This deployment is no longer the current master commit; refusing a stale update.'
    }
}

if (-not $env:HELPDESK_DEPLOY_DIR -or -not $env:HELPDESK_COMPOSE_BIN -or -not $env:REGISTRY_TOKEN) {
    throw 'Runner deployment directory, Compose binary, and registry token are required.'
}
if ($env:GITHUB_SHA -notmatch '^[0-9a-f]{40}$') { throw 'Invalid commit identifier.' }
if (-not (Test-Path -LiteralPath $env:HELPDESK_COMPOSE_BIN -PathType Leaf)) {
    throw 'The configured Compose binary was not found.'
}
$runnerUid = (& id -u).Trim()
Assert-NativeSuccess 'Could not determine the runner user ID.'
if ($runnerUid -notmatch '^[1-9][0-9]*$') { throw 'The deployment runner must use a non-root account.' }
$env:DOCKER_HOST = "unix:///run/user/$runnerUid/podman/podman.sock"
$isRootless = & podman info --format '{{.Host.Security.Rootless}}'
Assert-NativeSuccess 'Could not connect to rootless Podman.'
if ($isRootless -ne 'true') { throw 'The deployment runner must use rootless Podman.' }
$composeFile = Join-Path $env:HELPDESK_DEPLOY_DIR 'compose.azure-demo.yml'
$envFile = Join-Path $env:HELPDESK_DEPLOY_DIR 'deploy/demo.env.example'
foreach ($relativePath in @('compose.azure-demo.yml', 'deploy/frontend-config.js', 'deploy/demo.env.example')) {
    $checkedOut = Join-Path $env:GITHUB_WORKSPACE $relativePath
    $installed = Join-Path $env:HELPDESK_DEPLOY_DIR $relativePath
    if (-not (Test-Path -LiteralPath $installed -PathType Leaf) -or
        -not (Test-Path -LiteralPath $checkedOut -PathType Leaf)) {
        throw "Required demo configuration is missing: $relativePath"
    }
    if ((Read-NormalizedText $checkedOut) -cne (Read-NormalizedText $installed)) {
        throw "Installed configuration differs from this commit: $relativePath. Review and update it manually."
    }
}
foreach ($container in $requiredContainers) {
    $running = & podman inspect --format '{{.State.Running}}' $container
    Assert-NativeSuccess "Cannot inspect $container."
    if ($running -ne 'true') { throw "$container is not running; start and verify the demo first." }
}
& podman volume exists $dataVolume
Assert-NativeSuccess 'The expected demo database volume is missing.'
Assert-CurrentMaster

$prefix = 'ghcr.io/' + $env:GITHUB_REPOSITORY.ToLowerInvariant()
$apiImage = "${prefix}-api:$env:GITHUB_SHA"
$frontendImage = "${prefix}-frontend:$env:GITHUB_SHA"
$apiLocal = 'localhost/helpdesk-api:azure-demo'
$frontendLocal = 'localhost/helpdesk-frontend:azure-demo'

try {
    # Isolate registry credentials to a temporary file, then remove it on exit.
    $env:REGISTRY_TOKEN | & podman login --authfile $authFile --username $env:GITHUB_ACTOR --password-stdin ghcr.io
    Assert-NativeSuccess 'Registry login failed.'
    & podman pull --authfile $authFile $apiImage
    Assert-NativeSuccess 'API image pull failed.'
    & podman pull --authfile $authFile $frontendImage
    Assert-NativeSuccess 'Frontend image pull failed.'

    # Never migrate automatically. Stop if the image expects a different schema.
    & podman run --rm --network none --volume "${dataVolume}:/data:ro" `
        --env 'DATABASE_URL=sqlite:////data/helpdesk.sqlite3' $apiImage alembic current --check-heads
    Assert-NativeSuccess 'Database schema does not match the image or could not be checked. No update was started.'
    Assert-CurrentMaster

    # Preserve old image IDs for a manual recovery if an update fails.
    $oldApi = & podman image inspect --format '{{.Id}}' $apiLocal
    Assert-NativeSuccess 'Could not inspect the existing API image.'
    $oldFrontend = & podman image inspect --format '{{.Id}}' $frontendLocal
    Assert-NativeSuccess 'Could not inspect the existing frontend image.'
    Write-Host "Previous API image: $oldApi"
    Write-Host "Previous frontend image: $oldFrontend"

    & podman tag $apiImage $apiLocal
    Assert-NativeSuccess 'Could not tag API image.'
    & podman tag $frontendImage $frontendLocal
    Assert-NativeSuccess 'Could not tag frontend image.'

    # The existing Keycloak is deliberately not recreated. These values satisfy
    # Compose interpolation only and are not the real bootstrap credentials.
    $env:KC_BOOTSTRAP_ADMIN_USERNAME = 'validation-only'
    $env:KC_BOOTSTRAP_ADMIN_PASSWORD = 'validation-only'
    & $env:HELPDESK_COMPOSE_BIN --env-file $envFile -p $project -f $composeFile `
        up --detach --no-build --pull never --no-deps --force-recreate api frontend
    Assert-NativeSuccess 'Container update failed; inspect the demo before retrying.'

    foreach ($url in @('http://127.0.0.1:18183/ready', 'http://127.0.0.1:18181/')) {
        $healthy = $false
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            try {
                $response = Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing
                if ($response.StatusCode -eq 200) { $healthy = $true; break }
            } catch { Start-Sleep -Seconds 2 }
        }
        if (-not $healthy) { throw "Post-deployment check failed for $url. Manual recovery may be required." }
    }
    Write-Host "Demo containers updated to commit $env:GITHUB_SHA."
} finally {
    Remove-Item Env:KC_BOOTSTRAP_ADMIN_USERNAME, Env:KC_BOOTSTRAP_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    if ([IO.File]::Exists($authFile)) { [IO.File]::Delete($authFile) }
}
