param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $JavaPacks
)

$ErrorActionPreference = 'Stop'
$builderRoot = $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $builderRoot 'converter/convert_java_author_pack.py'))) {
    $builderRoot = Split-Path -Parent $PSScriptRoot
}

try {
    if (-not $JavaPacks -or $JavaPacks.Count -eq 0) {
        throw 'Drag one or more Java pack ZIP files onto Convert-Java-Pack.cmd. Put the base pack first, followed by addon packs in increasing priority. You can add your own Bedrock .mcpack too: its Vibrant Visuals lighting, fog and water are used.'
    }
    $resolvedPacks = @()
    $scenePack = $null
    foreach ($item in $JavaPacks) {
        $sourcePath = (Resolve-Path -LiteralPath $item).Path
        $extension = [IO.Path]::GetExtension($sourcePath).ToLowerInvariant()
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf) -or ($extension -ne '.zip' -and $extension -ne '.mcpack')) {
            throw "Expected a Java pack ZIP file or a Bedrock .mcpack: $sourcePath"
        }
        if ($extension -eq '.mcpack') {
            if ($scenePack) { throw 'Add at most one Bedrock .mcpack.' }
            $scenePack = $sourcePath
        } else {
            $resolvedPacks += $sourcePath
        }
    }
    if ($resolvedPacks.Count -eq 0) { throw 'Add at least one Java pack ZIP file.' }

    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        throw 'Install Node.js 18 or later from https://nodejs.org, then reopen this converter. Node runs the local tile selector.'
    }
    & node --eval "process.exit(Number(process.versions.node.split('.')[0]) >= 18 ? 0 : 1)"
    if ($LASTEXITCODE -ne 0) { throw 'This converter requires Node.js 18 or later. Install it from https://nodejs.org and reopen the converter.' }

    $environmentPath = Join-Path $builderRoot '.author-python'
    $runtimePath = Join-Path $environmentPath 'Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $runtimePath)) {
        Write-Host 'First run: creating a private Python environment in this builder folder.'
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3 -m venv $environmentPath
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            & python -m venv $environmentPath
        } else {
            throw 'Install Python 3.10 or later from python.org, then run this converter again.'
        }
        if ($LASTEXITCODE -ne 0) { throw 'Python could not create the private environment.' }
    }
    & $runtimePath -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'This converter requires Python 3.10 or later.' }
    & $runtimePath -c "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(name) for name in ('PIL','numpy')) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'First run: installing Pillow and NumPy into the private environment.'
        & $runtimePath -m pip install -r (Join-Path $builderRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the connection and run again.' }
    }

    # Mojang's Bedrock samples (block, texture and entity definitions the converter maps to), downloaded once.
    $samplesPath = if ($env:BEDROCK_SAMPLES) { $env:BEDROCK_SAMPLES } else { Join-Path $builderRoot 'bedrock-samples' }
    if (-not (Test-Path -LiteralPath (Join-Path $samplesPath 'resource_pack/blocks.json'))) {
        Write-Host "First run: downloading Mojang's Bedrock samples from GitHub (several hundred MB). This happens once."
        $download = Join-Path ([IO.Path]::GetTempPath()) ('bedrock-samples-' + [guid]::NewGuid().ToString('N') + '.zip')
        $staging = $samplesPath + '.download'
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $ProgressPreference = 'SilentlyContinue'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/Mojang/bedrock-samples/archive/refs/heads/main.zip' -OutFile $download
            if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $archive = [IO.Compression.ZipFile]::OpenRead($download)
            try {
                foreach ($entry in $archive.Entries) {
                    $slash = $entry.FullName.IndexOf('/')
                    if ($slash -lt 0 -or $entry.Name -eq '') { continue }
                    $relative = $entry.FullName.Substring($slash + 1)
                    if (-not ($relative.StartsWith('metadata/') -or $relative.StartsWith('resource_pack/') -or $relative.StartsWith('behavior_pack/'))) { continue }
                    $target = Join-Path $staging $relative
                    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
                    [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
                }
            } finally { $archive.Dispose() }
            if (Test-Path -LiteralPath $samplesPath) { Remove-Item -LiteralPath $samplesPath -Recurse -Force }
            Move-Item -LiteralPath $staging -Destination $samplesPath
        } catch {
            throw "Downloading Mojang's Bedrock samples failed: $($_.Exception.Message). Check the connection and run again."
        } finally {
            if (Test-Path -LiteralPath $download) { Remove-Item -LiteralPath $download -Force }
        }
    }

    $packTitle = [IO.Path]::GetFileNameWithoutExtension($resolvedPacks[0])
    $packKey = ($packTitle.ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
    if (-not $packKey) { $packKey = 'author-pack' }
    if ($packKey -notmatch '^[a-z]') { $packKey = 'pack-' + $packKey }
    if ($packKey.Length -gt 48) { $packKey = $packKey.Substring(0, 48).TrimEnd('-') }
    $outputName = '{0}-{1}-{2}' -f $packKey, (Get-Date -Format 'yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 6))
    $outputPath = Join-Path (Join-Path $builderRoot 'converted') $outputName
    Write-Host 'Source priority (last file wins):'
    $resolvedPacks | ForEach-Object { Write-Host "  $_" }
    Write-Host "Output: $outputPath"
    if ($scenePack) { Write-Host "Vibrant Visuals lighting, fog and water from: $scenePack" }
    Write-Host 'Converting locally. No account, AI service or API key is used.'
    $arguments = @('--java') + $resolvedPacks + @('--output', $outputPath, '--key', $packKey, '--title', $packTitle, '--samples', $samplesPath)
    if ($scenePack) { $arguments += @('--vv-scene', $scenePack) }
    & $runtimePath (Join-Path $builderRoot 'converter/convert_java_author_pack.py') @arguments
    if ($LASTEXITCODE -ne 0) { throw "Conversion stopped. Its diagnostics and completed material receipts are in $outputPath" }
    Write-Host "Conversion finished: $outputPath"
    Write-Host "Install Bedrock Connected Textures once, then import $packKey.mcaddon and turn the pack on in the world."
    Write-Host 'For RTX, pick RTX in the pack settings. Read conversion.json for coverage and limits.'
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
