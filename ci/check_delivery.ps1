$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
docker compose -f compose.yml -f compose.registry.yml up -d --wait orders-db notifications-db registry
$repository = '127.0.0.1:15000/food-notifications'
$ordersImage = '127.0.0.1:15000/food-orders:pr9'
docker tag ma-food-910-orders:lab $ordersImage
docker push $ordersImage
docker tag ma-food-910-notifications:lab "${repository}:pr9"
docker push "${repository}:pr9"
$headers = (Invoke-WebRequest -Uri 'http://127.0.0.1:15000/v2/food-notifications/manifests/pr9' -Headers @{
    Accept = 'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json'
}).Headers
$digest = [string]($headers['Docker-Content-Digest'] | Select-Object -First 1)
if ($digest -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Registry digest was not returned' }
$pinnedImage = "$repository@$digest"
docker pull $pinnedImage
$argsForRun = @('--network','ma-food-910_default','-p','127.0.0.1:18082:8080','-e','PORT=8080',
    '-e','DATABASE_URL=postgresql://notifications:notifications@notifications-db:5432/notifications_db')
$container = docker run -d --rm @argsForRun $pinnedImage
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:18082/health'
            $ready = $health.service -eq 'NotificationService' -and $health.status -eq 'ok'
            if ($ready) { break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) { throw 'NotificationService health check failed' }
    $result = Invoke-WebRequest 'http://127.0.0.1:18082/api/v1/notifications?limit=1'
    if ($result.StatusCode -ne 200) { throw 'NotificationService API check failed' }
    New-Item -ItemType Directory -Force evidence | Out-Null
    [pscustomobject]@{
        environment = 'local registry and Docker'
        orders_image = $ordersImage
        deployed_image = $pinnedImage
        container_port = 8080
        health = $health
        notifications_status = $result.StatusCode
    } | ConvertTo-Json -Depth 4 | Tee-Object -FilePath evidence/local-delivery.json
} finally {
    docker stop $container | Out-Null
}
