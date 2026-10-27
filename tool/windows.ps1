# Bootstrap pinned, private Python before any Python build tooling runs.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$spec = (Get-Content (Join-Path $root 'sources.lock.json') -Raw | ConvertFrom-Json).windows_tools.python
$cache = Join-Path $root '.tool-cache'
$archive = Join-Path $cache $spec.file
$destination = Join-Path $cache 'python'
New-Item -ItemType Directory -Force $cache | Out-Null
if (!(Test-Path $archive)) {
    $partial = "$archive.part"
    if (Test-Path $partial) { throw "Interrupted download preserved: $partial" }
    Invoke-WebRequest -Uri $spec.url -OutFile $partial -TimeoutSec 300
    if ((Get-FileHash $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $spec.sha256) {
        throw 'Python archive checksum mismatch; download preserved, nothing executed.'
    }
    Move-Item $partial $archive
}
if ((Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $spec.sha256) {
    throw 'Cached Python archive changed; nothing executed.'
}
if (Test-Path $destination) { throw 'Existing Python destination preserved.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
try {
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $total = 0L
    foreach ($entry in $zip.Entries) {
        $name = $entry.FullName
        if ($name -match '(^[/\\]|\\|:|(^|/)\.\.?(/|$)|[\x00-\x1f])' -or !$seen.Add($name)) {
            throw 'Unsafe or duplicate Python archive entry.'
        }
        if (($entry.ExternalAttributes -shr 16 -band 0xF000) -eq 0xA000) { throw 'Linked Python archive entry.' }
        $total += $entry.Length
    }
    if ($total -gt 256MB -or $seen.Count -gt 10000) { throw 'Oversized Python archive.' }
} finally { $zip.Dispose() }
[System.IO.Compression.ZipFile]::ExtractToDirectory($archive, $destination)
& (Join-Path $destination 'python.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'Private Python failed to initialize.' }
