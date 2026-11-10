# Überträgt Zugangsdaten aus deinen Windows-Benutzervariablen in die GitHub-Secrets
# des Repos. Die Werte werden nie angezeigt. Bitte selbst ausführen:
#   powershell -ExecutionPolicy Bypass -File scripts\set_github_secrets.ps1

$gh = 'C:\Program Files\GitHub CLI\gh.exe'
$repo = 'Lucifrrrr/facelessreels'
$names = 'HF_KEY', 'ELEVENLABS_API_KEY', 'IG_ACCESS_TOKEN', 'IG_USER_ID', 'GH_SECRETS_TOKEN'

foreach ($n in $names) {
    $v = [Environment]::GetEnvironmentVariable($n, 'User')
    if ([string]::IsNullOrWhiteSpace($v)) {
        Write-Host "– $n nicht gesetzt, übersprungen"
        continue
    }
    $v | & $gh secret set $n --repo $repo
    if ($LASTEXITCODE -eq 0) { Write-Host "✓ $n gespeichert" } else { Write-Host "✗ $n fehlgeschlagen" }
}
& $gh secret list --repo $repo
