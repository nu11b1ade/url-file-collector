#Requires -Version 5.1
[CmdletBinding()]
param([switch]$CoreOnly)

$regexOptions = [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor
    [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
$script:UrlCollectorUrlPattern = [regex]::new(
    '(?<![a-z0-9_])(?:https?|ftp|file)://[^\s<>"'']+', $regexOptions)
$domainLabel = '[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
$script:UrlCollectorDomainPattern = [regex]::new(
    ('(?<![a-z0-9_.-])(?:' + $domainLabel + '\.)+[a-z]{2,63}(?![a-z0-9_-]|\.[a-z0-9_-])'),
    $regexOptions)
$script:UrlCollectorIPv4Pattern = [regex]::new(
    '(?<![a-z0-9_.])(?<address>(?:[0-9]{1,3}\.){3}[0-9]{1,3})(?![a-z0-9_]|\.[a-z0-9_])(?::[0-9]{1,5})?',
    $regexOptions)
$script:UrlCollectorBinaryExtensions = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase)
foreach ($extension in ('.exe .dll .png .jpg .jpeg .gif .bmp .ico .zip .7z .rar .pdf ' +
    '.doc .docx .xls .xlsx .ppt .pptx .mp3 .mp4 .avi .mov .woff .woff2 ' +
    '.ttf .otf .class .pyc .so .dylib .bin .iso .dmg .apk').Split(' ')) {
    [void]$script:UrlCollectorBinaryExtensions.Add($extension)
}
$script:UrlCollectorEncodings = @(
    [System.Text.UTF8Encoding]::new($false, $true),
    [System.Text.Encoding]::GetEncoding([System.Text.Encoding]::Default.CodePage,
        [System.Text.EncoderFallback]::ExceptionFallback,
        [System.Text.DecoderFallback]::ExceptionFallback),
    [System.Text.Encoding]::GetEncoding(28591)
)
$script:UrlCollectorPathComparer = [System.StringComparer]::Ordinal
$script:UrlCollectorPathComparison = [System.StringComparison]::Ordinal
if ([System.IO.Path]::DirectorySeparatorChar -eq '\') {
    $script:UrlCollectorPathComparer = [System.StringComparer]::OrdinalIgnoreCase
    $script:UrlCollectorPathComparison = [System.StringComparison]::OrdinalIgnoreCase
}

function Test-UrlCollectorText {
    param([AllowEmptyString()][string]$Text)

    if ($script:UrlCollectorUrlPattern.IsMatch($Text) -or
        $script:UrlCollectorDomainPattern.IsMatch($Text)) {
        return $true
    }
    foreach ($match in $script:UrlCollectorIPv4Pattern.Matches($Text)) {
        $valid = $true
        foreach ($octet in $match.Groups['address'].Value.Split('.')) {
            if ([int]$octet -gt 255) {
                $valid = $false
                break
            }
        }
        if ($valid) { return $true }
    }
    return $false
}

function Get-UrlCollectorFullPath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'A folder path is required.' }
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.Length -gt $root.Length) {
        $full = $full.TrimEnd([System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar)
    }
    return $full
}

function Get-UrlCollectorAttributes {
    param([string]$Path)

    try { return [System.IO.File]::GetAttributes($Path) }
    catch [System.IO.FileNotFoundException] { return $null }
    catch [System.IO.DirectoryNotFoundException] { return $null }
}

function Assert-UrlCollectorDirectory {
    param([string]$Path, [switch]$AllowMissing)

    # Check ancestors too: a junction in an output parent must not redirect writes.
    $current = $Path
    while ($null -ne $current) {
        $attributes = Get-UrlCollectorAttributes $current
        if ($null -eq $attributes) {
            if ($current -eq $Path -and -not $AllowMissing) {
                throw "Folder does not exist: $Path"
            }
        }
        else {
            if ($attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "Symbolic links / reparse points are not supported: $current"
            }
            if (-not ($attributes -band [System.IO.FileAttributes]::Directory)) {
                throw "Not a folder: $current"
            }
        }
        $parent = [System.IO.Directory]::GetParent($current)
        $current = if ($null -eq $parent) { $null } else { $parent.FullName }
    }
}

function Read-UrlCollectorData {
    param([System.IO.FileInfo]$File, [int]$MaxBytes)

    if (($File.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
        $script:UrlCollectorBinaryExtensions.Contains($File.Extension) -or
        $File.Length -gt $MaxBytes) {
        return $null
    }
    $stream = $null
    $memory = [System.IO.MemoryStream]::new()
    try {
        $stream = [System.IO.File]::Open($File.FullName, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete))
        $buffer = [byte[]]::new(8192)
        # Bound the read even if a file grows after its initial size check.
        while ($memory.Length -le $MaxBytes) {
            $limit = [int][Math]::Min($buffer.Length, ($MaxBytes + 1L - $memory.Length))
            $count = $stream.Read($buffer, 0, $limit)
            if ($count -eq 0) { break }
            $memory.Write($buffer, 0, $count)
        }
        if ($memory.Length -gt $MaxBytes) { return $null }
        $data = $memory.ToArray()
        for ($index = 0; $index -lt [Math]::Min(8192, $data.Length); $index++) {
            if ($data[$index] -eq 0) { return $null }
        }
        return ,$data
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
        $memory.Dispose()
    }
}

function ConvertFrom-UrlCollectorData {
    param([byte[]]$Data)

    foreach ($encoding in $script:UrlCollectorEncodings) {
        try { return $encoding.GetString($Data) }
        catch [System.Text.DecoderFallbackException] { }
    }
    throw 'Unable to decode file content.'
}

function Select-UrlCollectorDestination {
    param(
        [string]$Desired,
        [byte[]]$Data,
        [System.Collections.Generic.Dictionary[string, string]]$Reserved
    )

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($sha.ComputeHash($Data)) }
    finally { $sha.Dispose() }
    $signature = [string]$Data.Length + ':' + $digest
    $directory = [System.IO.Path]::GetDirectoryName($Desired)
    $extension = [System.IO.Path]::GetExtension($Desired)
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($Desired)
    if ($stem.Length -eq 0) {
        $stem = [System.IO.Path]::GetFileName($Desired)
        $extension = ''
    }
    for ($index = 0; ; $index++) {
        $candidate = $Desired
        if ($index -gt 0) {
            $candidate = [System.IO.Path]::Combine($directory, ($stem + '__' + $index + $extension))
        }
        $duplicate = $false
        if ($Reserved.ContainsKey($candidate)) {
            if ($Reserved[$candidate] -ne $signature) { continue }
            $duplicate = $true
        }
        else {
            $attributes = Get-UrlCollectorAttributes $candidate
            if ($null -ne $attributes) {
                if (($attributes -band [System.IO.FileAttributes]::Directory) -or
                    ($attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
                    ([System.IO.FileInfo]::new($candidate).Length -ne $Data.Length)) {
                    continue
                }
                $stream = [System.IO.File]::OpenRead($candidate)
                $sha = [System.Security.Cryptography.SHA256]::Create()
                try { $existingDigest = [BitConverter]::ToString($sha.ComputeHash($stream)) }
                finally { $stream.Dispose(); $sha.Dispose() }
                if ($existingDigest -ne $digest) { continue }
                $duplicate = $true
            }
        }
        return [pscustomobject]@{ Path = $candidate; Duplicate = $duplicate; Signature = $signature }
    }
}

function Send-UrlCollectorEvent {
    param(
        [scriptblock]$OnEvent, [hashtable]$State, [string]$Kind = 'Progress',
        [string]$RelativePath = '', [string]$Destination = '',
        [string]$Action = '', [string]$Message = ''
    )

    if ($null -ne $OnEvent) {
        $null = & $OnEvent ([pscustomobject]@{
            Kind = $Kind; Scanned = $State.Scanned; Hits = $State.Hits
            Copied = $State.Copied; Skipped = $State.Skipped; Errors = $State.Errors
            RelativePath = $RelativePath; Destination = $Destination
            Action = $Action; Message = $Message
        })
    }
}

function Invoke-UrlFileCollection {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Output,
        [ValidateRange(0, 2147483646)][int]$MaxBytes = 5000000,
        [switch]$DryRun,
        [scriptblock]$OnEvent
    )

    $ErrorActionPreference = 'Stop'
    $sourceRoot = Get-UrlCollectorFullPath $Source
    $outputRoot = Get-UrlCollectorFullPath $Output
    $separator = [string][System.IO.Path]::DirectorySeparatorChar
    $sourcePrefix = $sourceRoot.TrimEnd([char]$separator) + $separator
    $outputPrefix = $outputRoot.TrimEnd([char]$separator) + $separator
    if ($script:UrlCollectorPathComparer.Equals($sourceRoot, $outputRoot) -or
        $sourceRoot.StartsWith($outputPrefix, $script:UrlCollectorPathComparison)) {
        throw 'Source and output must differ, and source must not be inside output.'
    }
    Assert-UrlCollectorDirectory $sourceRoot
    Assert-UrlCollectorDirectory $outputRoot -AllowMissing

    $state = @{ Scanned = 0; Hits = 0; Copied = 0; Skipped = 0; Errors = 0 }
    $reserved = [System.Collections.Generic.Dictionary[string, string]]::new(
        $script:UrlCollectorPathComparer)
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push($sourceRoot)
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $lastProgress = 0L
    Send-UrlCollectorEvent $OnEvent $state

    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        try {
            foreach ($entry in [System.IO.DirectoryInfo]::new($directory).EnumerateFileSystemInfos()) {
                if ($entry.Attributes -band [System.IO.FileAttributes]::Directory) {
                    if (-not ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -and
                        -not $script:UrlCollectorPathComparer.Equals($entry.FullName, $outputRoot)) {
                        $pending.Push($entry.FullName)
                    }
                    continue
                }
                $state.Scanned++
                $relative = $entry.FullName.Substring($sourcePrefix.Length)
                try {
                    $data = Read-UrlCollectorData $entry $MaxBytes
                    if ($null -eq $data) {
                        $state.Skipped++
                        continue
                    }
                    if (-not (Test-UrlCollectorText (ConvertFrom-UrlCollectorData $data))) { continue }
                    $state.Hits++
                    $desired = [System.IO.Path]::Combine($outputRoot, $relative)
                    $parent = [System.IO.Path]::GetDirectoryName($desired)
                    Assert-UrlCollectorDirectory $parent -AllowMissing
                    $target = Select-UrlCollectorDestination $desired $data $reserved
                    if ($target.Duplicate) {
                        $state.Skipped++
                        $action = 'Duplicate'
                    }
                    elseif ($DryRun) {
                        $reserved[$target.Path] = $target.Signature
                        $action = 'Preview'
                    }
                    else {
                        [void][System.IO.Directory]::CreateDirectory($parent)
                        Assert-UrlCollectorDirectory $parent
                        # Exclusive creation never overwrites a concurrently created file.
                        $stream = [System.IO.File]::Open($target.Path, [System.IO.FileMode]::CreateNew,
                            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
                        try { $stream.Write($data, 0, $data.Length) }
                        catch {
                            $stream.Dispose()
                            [System.IO.File]::Delete($target.Path)
                            throw
                        }
                        finally { $stream.Dispose() }
                        $state.Copied++
                        $action = 'Copied'
                    }
                    $destinationRelative = $target.Path.Substring($outputPrefix.Length)
                    Send-UrlCollectorEvent $OnEvent $state 'Hit' $relative $destinationRelative $action
                }
                catch {
                    $state.Skipped++
                    $state.Errors++
                    Send-UrlCollectorEvent $OnEvent $state 'Error' $relative '' 'Failed' $_.Exception.Message
                }
                finally {
                    if ($state.Scanned % 50 -eq 0 -or $clock.ElapsedMilliseconds - $lastProgress -ge 100) {
                        Send-UrlCollectorEvent $OnEvent $state
                        $lastProgress = $clock.ElapsedMilliseconds
                    }
                }
            }
        }
        catch {
            $state.Skipped++
            $state.Errors++
            Send-UrlCollectorEvent $OnEvent $state 'Error' $directory '' 'Failed' $_.Exception.Message
        }
    }
    $clock.Stop()
    Send-UrlCollectorEvent $OnEvent $state
    return [pscustomobject]@{
        Scanned = $state.Scanned; Hits = $state.Hits; Copied = $state.Copied
        Skipped = $state.Skipped; Errors = $state.Errors; DryRun = [bool]$DryRun
        Source = $sourceRoot; Output = $outputRoot; ElapsedSeconds = $clock.Elapsed.TotalSeconds
    }
}
